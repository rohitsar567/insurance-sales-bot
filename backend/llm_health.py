"""Live NIM model health monitor — availability filter + sticky
primary/backup election.

The background probe loop ELECTS a primary + backup per chain; chat()
calls the elected primary ONCE per turn, with at most ONE real-time
fallback to the elected backup (worst case 1-2 LLM calls per turn).
Electing out-of-band, rather than iterating the whole chain on every
turn, keeps a single turn from queuing many NIM-hosted candidates under
per-key concurrency throttling and burning the turn budget.

Probes every PROBE_INTERVAL_SEC tick (300s) with a tiny ping ("Reply with
exactly: ok"), `max_tokens=1` so probe-driven token spend is negligible.
Records per model:
  - status:           healthy / degraded / down / unknown
  - last_success_at:  timestamp of last 2xx response
  - last_failure_at:  timestamp of last failure (timeout / 5xx / parse)
  - latency_ms:       last successful response latency
  - consecutive_fail: counter (3+ => marked down)
  - tested_at:        when this row was last refreshed
  - probe_history:    last PROBE_HISTORY_LEN (ok, latency_ms) tuples; powers
                      the success_rate signal in the election score.

Election:
  - Walk the chain definition (BRAIN_CHAIN — the only chain) in priority
    order.
    CURRENT_PRIMARY = first election-eligible model.
    CURRENT_BACKUP  = next election-eligible model after primary.
  - Chain hierarchy IS the truth — nemotron-49b is LAST in BRAIN_CHAIN so
    it only serves when qwen + mistral + maverick are all unavailable.
  - Latency / success_rate gate ELIGIBILITY (probe-fresh, not in
    sin-bin, credits above water) but do not drive ORDERING, so a faster
    lower-priority model never beats a higher-priority one.
  - DEGRADED window: when chat() calls report_failure(model), that model is
    sidelined for either DEGRADED_WINDOW_SEC (transient, 30s) or
    DEGRADE_DURATION_LONG_S (rate-limit / HTTP 429, 1h) so the same turn's
    failure doesn't recycle to the same broken primary on the next turn.
    The next probe tick reconsiders the model normally for the short
    window; rate-limit demotions persist past several probe ticks so the
    elector doesn't keep bouncing back to a quota-exhausted model.

Persistence: 40-data/llm_health.json (atomic write via temp+rename).

Public API (the surface NimChainLLM.chat consumes):
  get_primary(chain_name)  -> Optional[str]
  get_backup(chain_name)   -> Optional[str]
  report_failure(chain, model, error_class)
  report_success(chain, model, latency_ms)

  Legacy:
  filter_chain(chain)      -> chain with 'down' models removed (still used
                              by admin / probe-refresh paths)
  status_summary()         -> compact dict for GET /api/health/llms

Operating schedule:
  - background asyncio task in main.py startup, ticks every PROBE_INTERVAL_SEC.
  - on-demand refresh via probe_all() (e.g. when NimChainLLM exhausts both
    primary + backup in a single turn).
"""
from __future__ import annotations

from backend.config import settings

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
HEALTH_FILE = settings.DATA_DIR / "llm_health.json"
HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)

# Probe cadence 300s. With ~25 candidates per tick × a 4-token round-trip
# each, a tighter cadence would burn enough tokens/day on Groq to push the
# brain to HTTP 429 on Groq's free-tier TPD cap. 300s keeps election
# responsive within 5 min of a pool degradation (fast enough at our chat
# volume) and keeps probe-driven Groq spend well inside quota.
PROBE_INTERVAL_SEC = 300
# 300s for steady-state probes. The post-failure tighter cadence is
# implemented OUT-OF-BAND in `report_failure()`, which schedules a
# `_reprobe_one()` immediately after every chat failure (effective 0s
# after-failure cadence on the loop). The 300s cadence then resumes for
# the long-run probe stream so the steady state stays cheap.
PROBE_INTERVAL_SEC_FAILING = 30   # post-failure cadence ceiling
# The probe only needs a non-empty 200 response to mark a candidate
# healthy; the body content is never parsed. max_tokens=1 keeps the same
# response shape with minimal token spend per probe.
PROBE_MAX_TOKENS = 1
PROBE_TIMEOUT_SEC = 8             # per-probe HTTP timeout
DOWN_AFTER_CONSECUTIVE_FAILS = 3  # 3 fails in a row = mark down
PROBE_HISTORY_LEN = 5             # rolling window for success_rate signal
HEALTHY_PROBE_AGE_SEC = 600       # election candidates need a probe within
                                  # the last 600s (tracks 300s cadence plus
                                  # headroom for one missed tick).
# Explicit STALE window. If a candidate hasn't been probed in
# >STALE_AGE_SEC, its on-record status is rewritten to "stale" so the
# router treats it as untested rather than trusting a last-known
# "healthy"/"unhealthy" verdict from minutes/hours ago.
STALE_AGE_SEC = HEALTHY_PROBE_AGE_SEC  # alias for clarity; same threshold.
DEGRADED_WINDOW_SEC = 30          # report_failure sidelines a model this long
                                  # for transient failures (timeout / 5xx).

# "brain" is the sole election role; consumers iterate ROLES so a future
# role only needs to be added in one place.
ROLES: tuple[str, ...] = ("brain",)
# Rate-limit failures (HTTP 429 + provider 'RateLimit' bodies) are
# almost always the daily quota on free tiers (Groq TPD, etc.) — they do
# NOT reset in 30s. Demote the model from election for an hour so the
# elector falls through to a non-rate-limited provider instead of
# bouncing back to the dead candidate on every chat turn.
DEGRADE_DURATION_LONG_S = 3600.0

# Proactive credit tracking: election excludes quota-exhausted candidates
# BEFORE the user gets stuck behind a 429 (rather than only demoting a
# candidate for 1h after a 429 hits).
#
# Three signal sources:
#   1) GROQ — response headers (x-ratelimit-remaining-tokens-day etc.) on
#      every successful Groq call. Low-water 5000 tokens (>= one fact-find
#      ~2K-input + ~400-output round-trip with margin).
#   2) OPENROUTER — dedicated GET /api/v1/credits endpoint, polled every
#      10min from the probe loop. Low-water 0.05 USD (5¢ safety margin —
#      OpenRouter free models charge $0 but the account-level signal still
#      tells us if the user's prepaid credits are gone).
#   3) NIM — no clean header. Local rate-meter: count successful calls in
#      the last 60s. Free tier is 40 req/min; gate at >=35 to stay clear.
GROQ_TOKENS_LOW_WATER = 5000.0          # tokens-per-day remaining
OPENROUTER_USD_LOW_WATER = 0.05         # USD balance remaining
NIM_REQ_PER_MIN_CAP = 40                # free-tier hard cap
NIM_REQ_PER_MIN_HEADROOM = 5            # gate at cap - headroom = 35
NIM_REQ_PER_MIN_LOW_WATER = 5.0         # below this remaining-in-window, gate

# OpenRouter credits poll cadence — every ~10 min, piggybacked on the
# probe loop tick counter. With PROBE_INTERVAL_SEC=300 that's every 2 ticks.
OPENROUTER_CREDITS_POLL_EVERY_N_TICKS = 2

# Per-provider endpoints + env-var names. The chain entries embed the
# provider via a prefix ('openrouter:<id>' / 'groq:<id>'); unprefixed entries
# fall through to NIM. Keep these dicts in sync with the providers in
# backend/providers/{openrouter_llm,groq_llm,nvidia_nim_llm}.py.
NIM_BASE = "https://integrate.api.nvidia.com/v1/chat/completions"
OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
GROQ_BASE = "https://api.groq.com/openai/v1/chat/completions"


def _base_url_for(model_id: str) -> str:
    if model_id.startswith("openrouter:"):
        return OPENROUTER_BASE
    if model_id.startswith("groq:"):
        return GROQ_BASE
    return NIM_BASE


def _api_key_for(model_id: str) -> str:
    if model_id.startswith("openrouter:"):
        return os.environ.get("OPENROUTER_API_KEY", "")
    if model_id.startswith("groq:"):
        return os.environ.get("GROQ_API_KEY", "")
    return os.environ.get("NVIDIA_NIM_API_KEY", "")


def _model_id_for(model_id: str) -> str:
    """Strip the provider prefix before sending to the upstream API."""
    if model_id.startswith("openrouter:"):
        return model_id[len("openrouter:"):]
    if model_id.startswith("groq:"):
        return model_id[len("groq:"):]
    return model_id


def _headers_for(model_id: str, api_key: str) -> dict[str, str]:
    """OpenRouter needs HTTP-Referer + X-Title to avoid being treated as
    anonymous traffic. NIM + Groq just need Bearer + Content-Type."""
    h = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if model_id.startswith("openrouter:"):
        h["HTTP-Referer"] = "https://huggingface.co/spaces/rohitsar567/InsuranceBot"
        h["X-Title"] = "Insurance Bot"
    return h


# Categorize a `last_error` string + optional HTTP status code into the
# short operator-facing reason rendered by the admin
# Health columns. Centralised here so backend and any other consumer agree
# on the same vocabulary ("network issue" / "rate limit (429)" / etc.).
# Use lookbehind/lookahead on non-digit so we still match e.g. "Status429",
# "http_429", "HTTPStatusError:503" — \b word-boundary alone treats "_" as
# a word char so "http_429" wouldn't match.
_RATE_LIMIT_RE = re.compile(r"(?<!\d)429(?!\d)|rate[_\s-]?limit", re.IGNORECASE)
_AUTH_RE       = re.compile(r"(?<!\d)40[13](?!\d)|unauthor|forbidden",  re.IGNORECASE)
_QUOTA_RE      = re.compile(r"(?<!\d)402(?!\d)|quota|out[\s_-]?of[\s_-]?credit|insufficient[_\s]?funds|payment[_\s]?required", re.IGNORECASE)
_TIMEOUT_RE    = re.compile(r"timeout|timed[_\s-]?out|connect(ion)?[_\s-]?(refused|reset|error)|network|dns|name[_\s-]?or[_\s-]?service", re.IGNORECASE)
_5XX_RE        = re.compile(r"(?<!\d)5\d{2}(?!\d)|service[_\s-]?unavailable|bad[_\s-]?gateway|gateway[_\s-]?timeout|internal[_\s-]?server", re.IGNORECASE)


def _classify_error_reason(last_error: Optional[str],
                            last_status_code: Optional[int] = None) -> Optional[str]:
    """Map a stored `last_error` string (+ optional HTTP code) into a short
    operator-facing reason for the admin Health column. Returns None when
    there is no error signal at all (caller renders just "Live" or "Off —
    unknown" depending on status).

    Categories (matches the display contract in the admin spec):
      - "network issue"            — timeout / connection refused / DNS
      - "out of credits"           — HTTP 402 / quota / insufficient funds
      - "rate limit (429)"         — HTTP 429 / rate-limit keywords
      - "service unavailable (5xx)"— HTTP 5xx
      - "auth error (4xx)"         — HTTP 401 / 403
      - "stale"                    — explicitly surfaced by effective_status()
      - first-40-chars fallback    — anything else, truncated so the cell
                                     doesn't blow up the table width.
    """
    if not last_error:
        return None

    # Prefer the explicit status code when present — it's unambiguous.
    if last_status_code is not None:
        code = int(last_status_code)
        if code == 429:
            return "rate limit (429)"
        if code == 402:
            return "out of credits"
        if code in (401, 403):
            return f"auth error ({code})"
        if 500 <= code < 600:
            return f"service unavailable ({code})"

    text = str(last_error)

    if _RATE_LIMIT_RE.search(text):
        return "rate limit (429)"
    if _QUOTA_RE.search(text):
        return "out of credits"
    if _AUTH_RE.search(text):
        # Try to surface the code if it's embedded in the string.
        m = re.search(r"(?<!\d)(40[13])(?!\d)", text)
        return f"auth error ({m.group(1)})" if m else "auth error"
    if _TIMEOUT_RE.search(text):
        return "network issue"
    if _5XX_RE.search(text):
        m = re.search(r"(?<!\d)(5\d{2})(?!\d)", text)
        return f"service unavailable ({m.group(1)})" if m else "service unavailable"

    # Final fallback — truncate so the table cell stays readable.
    snippet = text.strip().splitlines()[0] if text.strip() else ""
    return snippet[:40] if snippet else None


def _extract_status_code(err: str) -> Optional[int]:
    """Best-effort scrape of an HTTP status code out of an error string —
    used by the probe path to backfill `last_status_code` from the existing
    `http_{code}` / `HTTPStatusError:{code}` tags without changing every
    probe-result producer. Returns None when no 3-digit HTTP code is found."""
    if not err:
        return None
    # Surround-by-non-digit boundary so we still catch "http_429" / "Status429"
    # where \b would fail because '_' is a word char.
    m = re.search(r"(?<!\d)(\d{3})(?!\d)", err)
    if not m:
        return None
    try:
        code = int(m.group(1))
    except ValueError:
        return None
    if 100 <= code < 600:
        return code
    return None


def provider_of(model_id: str) -> str:
    """Coarse provider bucket — used by the election routine to prefer a
    cross-provider backup so a NIM regional outage can't take out both
    primary + backup simultaneously. Returns one of: 'nim' | 'groq' |
    'openrouter'. (NIM is the implicit default for unprefixed model ids
    even though NIM hosts many model families — all of those share the
    same NIM ingress + per-key rate quota, which is what we need to
    diversify against.)"""
    if model_id.startswith("openrouter:"):
        return "openrouter"
    if model_id.startswith("groq:"):
        return "groq"
    return "nim"


@dataclass
class ModelHealth:
    model: str
    # 'stale' is set by `effective_status()` when a row hasn't been pinged
    # in > STALE_AGE_SEC; the router then treats it
    # as untested instead of trusting a last-known healthy/unhealthy verdict
    # from minutes/hours ago. Persisted records may still carry the older
    # status — the elector calls effective_status() at decision time.
    status: str = "unknown"               # 'healthy' | 'degraded' | 'down' | 'stale' | 'unknown'
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error: Optional[str] = None
    # Explicit HTTP status code from the most recent failed probe / chat
    # call. `last_error` is a free-form string ("timeout",
    # "http_429", "net: TimeoutException: ..."); `last_status_code` is the
    # parsed integer (or None when the failure wasn't HTTP-shaped). Surfaced
    # in admin Health columns so the operator can see "Off — rate limit (429)"
    # vs "Off — service unavailable (503)" at a glance.
    last_status_code: Optional[int] = None
    latency_ms: Optional[int] = None
    consecutive_failures: int = 0
    tested_at: Optional[str] = None
    # KI-080 — rolling probe history powers the success_rate signal in
    # the election score. Each entry: {"ok": bool, "latency_ms": int|None,
    # "ts": iso8601}. Capped at PROBE_HISTORY_LEN.
    probe_history: list[dict] = field(default_factory=list)
    # KI-080 — set by report_failure(); model is excluded from election
    # while monotonic time < degraded_until_monotonic.
    degraded_until_monotonic: float = 0.0
    # Proactive credit tracking. Stamped by
    # update_credits_from_groq / update_credits_from_openrouter (response
    # headers + account endpoint) and by the NIM local rate-meter. The
    # elector gates on `credits_remaining is None OR > credits_low_water`
    # so None (no signal yet) is permissive (cold-start = electable).
    credits_remaining: Optional[float] = None    # tokens / USD / req-slots
    credits_unit: Optional[str] = None           # "tokens_day" / "tokens_min" /
                                                 # "usd_balance" / "requests_min"
    credits_reset_at: Optional[float] = None     # monotonic time when quota resets
    credits_observed_at: Optional[float] = None  # monotonic time of snapshot
    credits_low_water: float = 0.0               # below this, gated out


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_age_seconds(iso_ts: Optional[str]) -> Optional[float]:
    """Seconds since an ISO timestamp; None if missing/unparseable."""
    if not iso_ts:
        return None
    try:
        t = time.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")
        return time.time() - time.mktime(t) + time.timezone
    except Exception:
        return None


def _all_known_models() -> list[str]:
    """Pull every model name from BRAIN_CHAIN (the only chain)."""
    from backend.providers.nvidia_nim_llm import BRAIN_CHAIN
    seen: list[str] = []
    for m in BRAIN_CHAIN:
        if m not in seen:
            seen.append(m)
    return seen


def _chain_for(chain_name: str) -> list[str]:
    """Resolve a chain name → live chain list. Reads off the module so
    runtime admin reorderings are respected by the elector.

    "brain" is the only valid role. Any other input is coerced to "brain"
    for back-compat with callers that pass another role name.
    """
    from backend.providers import nvidia_nim_llm as nim
    return list(getattr(nim, "BRAIN_CHAIN", []))


# KI-080 — in-process state. Probe history + degraded windows are
# performance-critical hot paths (read on every chat turn), so we keep
# them in memory and only persist the long-lived signal to disk on the
# probe tick. Concurrent NimChainLLM.chat workers + the probe loop both
# mutate this; a coarse lock is fine — the held region is microseconds.
_STATE_LOCK = threading.Lock()
_STATE: dict[str, ModelHealth] = {}
_STATE_LOADED = False


def _load_into_memory() -> None:
    """Hydrate _STATE from disk on first access. Idempotent."""
    global _STATE, _STATE_LOADED
    if _STATE_LOADED:
        return
    with _STATE_LOCK:
        if _STATE_LOADED:
            return
        if HEALTH_FILE.exists():
            try:
                raw = json.loads(HEALTH_FILE.read_text())
                for k, v in raw.get("models", {}).items():
                    # Tolerate older on-disk schema: records may be missing
                    # probe_history / degraded_until_monotonic or the five
                    # credits_* fields.
                    v.setdefault("probe_history", [])
                    v.setdefault("degraded_until_monotonic", 0.0)
                    v.setdefault("last_status_code", None)
                    v.setdefault("credits_remaining", None)
                    v.setdefault("credits_unit", None)
                    v.setdefault("credits_reset_at", None)
                    v.setdefault("credits_observed_at", None)
                    v.setdefault("credits_low_water", 0.0)
                    _STATE[k] = ModelHealth(**v)
            except Exception:
                _STATE = {}
        _STATE_LOADED = True


def load() -> dict[str, ModelHealth]:
    """Legacy/back-compat: snapshot of the in-memory state. Returned dict
    is a shallow copy so callers can't mutate _STATE directly."""
    _load_into_memory()
    with _STATE_LOCK:
        return dict(_STATE)


def save(state: Optional[dict[str, ModelHealth]] = None) -> None:
    """Atomic write — temp file then rename so concurrent readers never see partial.

    If `state` is None, persists the in-memory _STATE. The optional arg is
    kept for backward compatibility with call sites in admin.py."""
    if state is None:
        _load_into_memory()
        with _STATE_LOCK:
            state = dict(_STATE)
    out = {
        "updated_at": _now_iso(),
        "models": {k: asdict(v) for k, v in state.items()},
    }
    tmp = HEALTH_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2) + "\n")
    tmp.replace(HEALTH_FILE)


# ---------------------------------------------------------------------------
# Public API: primary/backup election + failure/success reporting
# ---------------------------------------------------------------------------

def _success_rate(h: ModelHealth) -> float:
    """Fraction of the last PROBE_HISTORY_LEN probes that succeeded.
    Returns 1.0 when there's no history yet (cold-start — give every
    healthy candidate the benefit of the doubt)."""
    if not h.probe_history:
        return 1.0
    hits = sum(1 for r in h.probe_history if r.get("ok"))
    return hits / len(h.probe_history)


def _score(h: ModelHealth) -> float:
    """Election score — higher is better.

    score = (1 / max(50, latency_ms)) * success_rate
    The 50ms floor stops a sub-millisecond outlier from dominating
    election; success_rate is the rolling-window stability signal.
    """
    if h.latency_ms is None:
        return 0.0
    return (1.0 / max(50, h.latency_ms)) * _success_rate(h)


def effective_status(h: ModelHealth) -> str:
    """Return the routing-relevant status, applying the STALE_AGE_SEC
    override at read time.

    A stored `status` of "healthy" can mean "the last probe N hours ago
    said this was healthy" — which the router must NOT trust. When
    `tested_at` is older than `STALE_AGE_SEC` (or missing entirely), we
    return "stale" so the elector treats the candidate as untested.

    The stored status is not mutated here — that's the probe's job. Only
    the live decision surface (election eligibility, status_summary) calls
    this so historical inspection (logs, on-disk JSON) is preserved.
    """
    age = _iso_age_seconds(h.tested_at)
    if age is None or age > STALE_AGE_SEC:
        return "stale"
    return h.status


def _is_election_eligible(h: ModelHealth, now_mono: float) -> bool:
    """A candidate is electable when:
      - status is healthy (or degraded with a recent success)
      - last probe was within HEALTHY_PROBE_AGE_SEC
      - it is NOT currently in the degraded-window sin-bin
      - it has credits remaining above its low-water mark, OR no credit
        signal yet (cold-start = permissive).
      - effective_status != "stale" (catches the case where the status
        field is 'healthy' but the probe is older than STALE_AGE_SEC).
    """
    if h.degraded_until_monotonic > now_mono:
        return False
    if h.status == "down":
        return False
    age = _iso_age_seconds(h.tested_at)
    if age is None or age > HEALTHY_PROBE_AGE_SEC:
        return False
    if h.latency_ms is None:
        return False
    if effective_status(h) == "stale":
        return False
    if not _has_credits(h, now_mono):
        logger.info(
            "election: skipping %s — credits %s/%s below water %s",
            h.model, h.credits_remaining, h.credits_unit, h.credits_low_water,
        )
        return False
    return True


def _has_credits(h: ModelHealth, now_mono: float) -> bool:
    """Credit-gate predicate for election eligibility.

    Rules:
      - If `credits_reset_at` has elapsed, treat the signal as stale and
        permissive (next call will refresh). We don't auto-zero the
        snapshot here so other readers (admin UI / status_summary) still
        see the LAST observed value with its observed_at timestamp.
      - If `credits_remaining is None` (no signal yet), return True —
        cold-start must not penalize a fresh candidate.
      - Otherwise gate on `credits_remaining > credits_low_water`.
    """
    if h.credits_reset_at is not None and now_mono >= h.credits_reset_at:
        return True
    if h.credits_remaining is None:
        return True
    return h.credits_remaining > h.credits_low_water


def _ranked_candidates(chain_name: str) -> list[ModelHealth]:
    """Return the chain's election-eligible candidates, best-score first."""
    _load_into_memory()
    chain = _chain_for(chain_name)
    now_mono = time.monotonic()
    with _STATE_LOCK:
        snapshot = {m: _STATE.get(m) for m in chain}
    eligible: list[tuple[float, ModelHealth]] = []
    for m in chain:
        h = snapshot.get(m)
        if h is None:
            continue
        if not _is_election_eligible(h, now_mono):
            continue
        eligible.append((_score(h), h))
    eligible.sort(key=lambda t: t[0], reverse=True)
    return [h for _, h in eligible]


# Election event log. Tracks the last elected primary/
# backup per chain so we can emit a structured promotion/demotion log line
# when the elected model changes. Stored in-memory only (cheap; resets on
# process restart, which is fine — first post-restart election re-emits).
_LAST_ELECTION_LOCK = threading.Lock()
_LAST_ELECTION: dict[str, dict[str, Optional[str]]] = {}


def _emit_election_event(chain_name: str, role: str, from_m: Optional[str],
                          to_m: Optional[str], reason: str) -> None:
    """Structured log line for every primary/backup promotion or
    demotion. Format:
        {event, chain, role, from, to, reason, ts}
    The router / admin UI / log-shipper can grep on `event=election` to
    rebuild the timeline of who served what when.
    """
    event = {
        "event": "election",
        "chain": chain_name,
        "role": role,
        "from": from_m,
        "to": to_m,
        "reason": reason,
        "ts": _now_iso(),
    }
    try:
        logger.info("llm_health.election %s", json.dumps(event, ensure_ascii=False))
    except Exception:
        # Logging must never block the election path.
        pass


def _record_election(chain_name: str, role: str, new_model: Optional[str],
                      reason: str = "elect") -> None:
    """Compare against last-recorded election for this (chain, role) and
    emit a structured log line on change. No-op if the value is unchanged.
    """
    with _LAST_ELECTION_LOCK:
        per_chain = _LAST_ELECTION.setdefault(chain_name, {})
        prev = per_chain.get(role, "__UNSET__")  # sentinel — None is a real value
        if prev == new_model:
            return
        per_chain[role] = new_model
    # Logging outside the lock — never block other elections on logger I/O.
    _emit_election_event(
        chain_name, role,
        None if prev == "__UNSET__" else prev,
        new_model, reason,
    )


def get_primary(chain_name: str) -> Optional[str]:
    """Elect by CHAIN ORDER, not latency score.

    Walk the chain definition in priority order. Return the first model
    that's election-eligible. Chain hierarchy IS the truth — e.g. in
    BRAIN_CHAIN, nemotron-49b is LAST so it only serves when every
    higher-priority model is unavailable (last-resort rule).

    Latency is not used for primary/backup selection — eligibility
    filtering uses the rolling probe state, but ordering is purely
    chain-positional, so the hierarchy is always honoured when models
    are live.

    Emits a structured `event=election` log line via `_record_election`
    whenever the elected primary changes for this chain, so the
    operator/admin/log-shipper can rebuild the promotion/demotion
    timeline. No log emit when the value is unchanged.
    """
    chain = _chain_for(chain_name)
    if not chain:
        _record_election(chain_name, "primary", None, reason="empty_chain")
        return None
    # _ranked_candidates returns the eligibility-filtered set (probe-fresh,
    # not in sin-bin, credit-not-exhausted, healthy). Reduce to a set for
    # O(1) lookup; we ignore its score-based ordering.
    eligible_models = {h.model for h in _ranked_candidates(chain_name)}
    for model in chain:
        if model in eligible_models:
            _record_election(chain_name, "primary", model, reason="chain_walk")
            return model
    _record_election(chain_name, "primary", None, reason="no_eligible")
    return None  # nothing eligible


def get_backup(chain_name: str) -> Optional[str]:
    """Backup is the SECOND eligible model in chain order, skipping the
    elected primary.

    Walks the chain definition in priority order, skips the elected
    primary, and returns the next eligible model. Same chain-as-truth
    philosophy as get_primary: the chains in nvidia_nim_llm.py are
    designed with family/provider diversity (Qwen → Mistral → Meta →
    NVIDIA), so walking past the primary in chain order naturally keeps
    the backup on a different family from the primary.

    Emits a structured `event=election` log line via `_record_election`
    whenever the elected backup changes.
    """
    chain = _chain_for(chain_name)
    if not chain:
        _record_election(chain_name, "backup", None, reason="empty_chain")
        return None
    eligible_models = {h.model for h in _ranked_candidates(chain_name)}
    primary = get_primary(chain_name)
    for model in chain:
        if model == primary:
            continue
        if model in eligible_models:
            _record_election(chain_name, "backup", model, reason="chain_walk")
            return model
    _record_election(chain_name, "backup", None, reason="no_eligible")
    return None


def _is_rate_limit_error(error_class: str) -> bool:
    """KI-084 — true when the failure looks like a provider rate-limit
    rather than a transient network/server error.

    The hot-path producer is NimChainLLM._classify_error, which inspects
    the underlying HTTPStatusError's response.status_code and mints
    `"Status429"` for 429s explicitly (vs `"HTTPStatusError:503"` for
    server errors). We match:
      - `"Status429"` / any string containing `"429"`  — explicit 429 tag.
      - `"RateLimit"` / `"rate_limit"`                 — defensive upstream
                                                         text tag (some
                                                         providers embed
                                                         this in the body).
    Crucially we DO NOT match bare `"HTTPStatusError"` here, because
    `_classify_error` only emits that string for non-429 HTTP failures
    (e.g. 503), which deserve the SHORT sin-bin, not the 1h quota window.
    """
    if not error_class:
        return False
    needle = error_class.lower()
    return (
        "429" in needle
        or "ratelimit" in needle
        or "rate_limit" in needle
    )


def report_failure(chain_name: str, model: str, error_class: str) -> None:
    """Called by NimChainLLM.chat() when primary OR backup throws.

    Effects:
      - Sidelines `model` from election. For rate-limit failures
        (HTTP 429 / "RateLimit" body — KI-084) the sin-bin is
        DEGRADE_DURATION_LONG_S (1 hour) because free-tier daily token
        quotas don't reset for hours; for all other transient failures
        (timeout / 5xx / parse) it's the short DEGRADED_WINDOW_SEC (30s).
      - Appends a synthetic 'failed' entry to probe_history so the next
        election's success_rate reflects the live failure even before
        the next probe tick.
      - Schedules an async re-probe (best-effort) so the next turn gets
        fresh data instead of waiting up to PROBE_INTERVAL_SEC for the
        next tick. (For 429s the reprobe is cheap and informative — if
        Groq's quota happens to have reset early we'll find out
        immediately rather than waiting an hour.)
    """
    _load_into_memory()
    # KI-084 — 429-class failures get a long sin-bin, everything else
    # the existing 30s window.
    if _is_rate_limit_error(error_class):
        degrade_for_s = DEGRADE_DURATION_LONG_S
    else:
        degrade_for_s = DEGRADED_WINDOW_SEC
    with _STATE_LOCK:
        h = _STATE.get(model) or ModelHealth(model=model)
        h.degraded_until_monotonic = time.monotonic() + degrade_for_s
        h.last_failure_at = _now_iso()
        h.last_error = f"chat_failure: {error_class}"
        # KI-202 — `error_class` from NimChainLLM._classify_error is
        # `Status429`, `HTTPStatusError:503`, `TimeoutException`, etc.
        # Pull the trailing 3-digit code out so the admin Health column
        # can render a code-specific label.
        h.last_status_code = _extract_status_code(error_class)
        h.probe_history.append({
            "ok": False,
            "latency_ms": None,
            "ts": _now_iso(),
            "src": "chat",
        })
        if len(h.probe_history) > PROBE_HISTORY_LEN:
            h.probe_history = h.probe_history[-PROBE_HISTORY_LEN:]
        # Don't flip status to 'down' here — that's the probe's job and
        # we don't want a single transient turn-failure to evict the
        # candidate permanently. The degraded sin-bin is sufficient.
        _STATE[model] = h

    # Best-effort re-probe — fire and forget. We can't await inside this
    # sync API (callers are in the hot path) so we schedule on the loop
    # if one is running.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_reprobe_one(model))
    except RuntimeError:
        pass  # no loop (unit tests etc.) — skip silently


def report_success(chain_name: str, model: str, latency_ms: int) -> None:
    """Called by NimChainLLM.chat() on a successful response. Updates
    the rolling success/latency window — important because chat traffic
    is dramatically richer signal than 1-token probes (real prompts,
    real concurrency)."""
    _load_into_memory()
    with _STATE_LOCK:
        h = _STATE.get(model) or ModelHealth(model=model)
        h.last_success_at = _now_iso()
        h.last_error = None
        h.last_status_code = None
        h.latency_ms = int(latency_ms)
        h.consecutive_failures = 0
        h.tested_at = _now_iso()
        h.status = "healthy" if latency_ms < 5000 else "degraded"
        h.probe_history.append({
            "ok": True,
            "latency_ms": int(latency_ms),
            "ts": _now_iso(),
            "src": "chat",
        })
        if len(h.probe_history) > PROBE_HISTORY_LEN:
            h.probe_history = h.probe_history[-PROBE_HISTORY_LEN:]
        _STATE[model] = h


async def _reprobe_one(model: str) -> None:
    """Async re-probe of a single model after report_failure(). Updates
    state in place; failures here are silently swallowed so the chat
    hot path never raises through this back-channel."""
    try:
        async with httpx.AsyncClient() as client:
            ok, err, latency = await probe_one(client, model, "")
            _absorb_probe_result(model, ok, err, latency)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# KI-085 — proactive credit tracking. Three signal sources:
#   (1) Groq response headers (per-call, real-time)
#   (2) OpenRouter dedicated /credits endpoint (10-min poll)
#   (3) NIM local rate-meter (no clean header; count successes in last 60s)
# ---------------------------------------------------------------------------

# Match formats observed in the wild for Groq reset headers. Three shapes
# coexist on the Groq API:
#   - duration string: "1h2m"  /  "30m"  /  "45s"  /  "1h2m30s"
#   - bare seconds-from-now (float-ish): "60.5"  /  "3600"
#   - epoch unix seconds (only when value is large enough): "1747326123"
_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$")


def _parse_reset_seconds(raw: Optional[str], now_mono: float) -> Optional[float]:
    """Parse a Groq-style reset header into a monotonic deadline.

    Returns the monotonic timestamp at which the quota resets (or None
    when the value is missing/malformed). Accepts three shapes:
      - "1h2m30s" / "30m" / "60s"   → seconds offset from now
      - "60.5"                       → seconds offset (bare numeric)
      - "1747326123"                 → unix epoch (treated as absolute
                                       wall-clock; converted to monotonic
                                       relative to current time.time()).
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    # Duration string ("1h2m" / "30m45s" / "45s")
    m = _DURATION_RE.match(s)
    if m and any(m.groups()):
        h = int(m.group(1) or 0)
        mn = int(m.group(2) or 0)
        sec = float(m.group(3) or 0)
        offset = h * 3600 + mn * 60 + sec
        if offset > 0:
            return now_mono + offset
        return None
    # Bare numeric — seconds-from-now or unix epoch
    try:
        v = float(s)
    except ValueError:
        return None
    # Heuristic: > 1e9 means it's almost certainly a unix epoch (after 2001).
    # Convert to seconds-from-now first, then to monotonic.
    if v > 1e9:
        offset = v - time.time()
        if offset <= 0:
            return now_mono  # already reset
        return now_mono + offset
    if v <= 0:
        return None
    return now_mono + v


def update_credits_from_groq(chain_name: str, model: str, headers: dict) -> None:
    """Stamp credits_remaining from a Groq response's x-ratelimit-* headers.

    Called from GroqLLM.chat() after a successful HTTP response. The
    `headers` dict is the response's headers (case-insensitive via httpx).
    We prefer the DAILY tokens signal (`x-ratelimit-remaining-tokens-day`)
    because Groq's free-tier daily TPD cap is the binding limit.

    Missing header → no-op. Malformed value → log warning + no-op.
    """
    if not headers:
        return
    # httpx headers are case-insensitive; index defensively for plain dicts.
    def _h(k: str) -> Optional[str]:
        try:
            v = headers.get(k)
        except AttributeError:
            return None
        if v is not None:
            return v
        # Plain dict fallback — case-fold lookup.
        for hk, hv in headers.items():
            if hk.lower() == k.lower():
                return hv
        return None

    remaining_raw = _h("x-ratelimit-remaining-tokens-day")
    reset_raw = _h("x-ratelimit-reset-tokens-day")

    if remaining_raw is None:
        # No daily-tokens header — Groq sometimes only sends the minute
        # window; that's not the signal we care about for KI-085 (KI-084's
        # 1h sin-bin already covers minute-window blips).
        return

    try:
        remaining = float(remaining_raw)
    except (TypeError, ValueError):
        logger.warning(
            "update_credits_from_groq: malformed remaining value %r for %s",
            remaining_raw, model,
        )
        return

    now_mono = time.monotonic()
    reset_at = _parse_reset_seconds(reset_raw, now_mono)

    _load_into_memory()
    with _STATE_LOCK:
        h = _STATE.get(model) or ModelHealth(model=model)
        h.credits_remaining = remaining
        h.credits_unit = "tokens_day"
        h.credits_reset_at = reset_at
        h.credits_observed_at = now_mono
        h.credits_low_water = GROQ_TOKENS_LOW_WATER
        _STATE[model] = h


def update_credits_from_openrouter_headers(chain_name: str, model: str, headers: dict) -> None:
    """OpenRouter sometimes surfaces per-call remaining credits on response
    headers (`x-ratelimit-remaining` etc.). Lower fidelity than the
    dedicated /credits endpoint but useful as a between-poll signal so the
    elector reacts inside the 10-min poll window.

    Header shape varies by model — we accept `x-ratelimit-remaining` (raw
    count, no unit semantics) and treat it as request-slots so the gate
    catches a near-empty bucket. Missing header → no-op.
    """
    if not headers:
        return

    def _h(k: str) -> Optional[str]:
        try:
            v = headers.get(k)
        except AttributeError:
            return None
        if v is not None:
            return v
        for hk, hv in headers.items():
            if hk.lower() == k.lower():
                return hv
        return None

    remaining_raw = _h("x-ratelimit-remaining")
    if remaining_raw is None:
        return
    try:
        remaining = float(remaining_raw)
    except (TypeError, ValueError):
        logger.warning(
            "update_credits_from_openrouter_headers: malformed remaining value %r for %s",
            remaining_raw, model,
        )
        return

    now_mono = time.monotonic()
    _load_into_memory()
    with _STATE_LOCK:
        h = _STATE.get(model) or ModelHealth(model=model)
        # Only stamp from headers if we DON'T already have a fresher
        # account-level signal from the dedicated endpoint. usd_balance is
        # the authoritative truth for OpenRouter; per-call requests_min is
        # a between-poll approximation.
        if h.credits_unit != "usd_balance":
            h.credits_remaining = remaining
            h.credits_unit = "requests_min"
            h.credits_observed_at = now_mono
            # Low-water: stay 5 slots above zero so a near-empty bucket
            # gates out the candidate.
            h.credits_low_water = float(NIM_REQ_PER_MIN_LOW_WATER)
            _STATE[model] = h


# NIM local rate-meter (no clean header). Per-chain-entry deque of monotonic
# success timestamps; we trim to last 60s on each read.
_NIM_CALL_TIMES_LOCK = threading.Lock()
_NIM_CALL_TIMES: dict[str, list[float]] = {}


def record_nim_call(chain_name: str, model: str) -> None:
    """Bump the local NIM rate-meter on a successful call. Also stamps
    credits_remaining on the ModelHealth so the elector can gate.

    NIM free tier = 40 req/min per API key. We gate at >= 35 in-window
    calls (`NIM_REQ_PER_MIN_CAP - NIM_REQ_PER_MIN_HEADROOM`) so the
    elector sidelines the candidate before we burn the cap.
    """
    if not model:
        return
    now_mono = time.monotonic()
    cutoff = now_mono - 60.0
    with _NIM_CALL_TIMES_LOCK:
        times = _NIM_CALL_TIMES.get(model, [])
        times = [t for t in times if t > cutoff]
        times.append(now_mono)
        _NIM_CALL_TIMES[model] = times
        in_window = len(times)

    remaining = max(0.0, float(NIM_REQ_PER_MIN_CAP - in_window))
    # Window resets 60s after the OLDEST in-window call.
    reset_at = (times[0] + 60.0) if times else (now_mono + 60.0)

    _load_into_memory()
    with _STATE_LOCK:
        h = _STATE.get(model) or ModelHealth(model=model)
        h.credits_remaining = remaining
        h.credits_unit = "requests_min"
        h.credits_reset_at = reset_at
        h.credits_observed_at = now_mono
        h.credits_low_water = float(NIM_REQ_PER_MIN_LOW_WATER)
        _STATE[model] = h


async def poll_openrouter_credits() -> Optional[dict]:
    """Hit GET https://openrouter.ai/api/v1/credits and stamp every
    OpenRouter-prefixed candidate with the account-level USD balance.

    Returns the parsed `{total_credits, total_usage}` dict on success, or
    None on any failure (missing key / HTTP error / parse fail). Best-
    effort: never raises. Called from background_probe_loop on a counter
    every OPENROUTER_CREDITS_POLL_EVERY_N_TICKS ticks.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    url = "https://openrouter.ai/api/v1/credits"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                logger.info(
                    "poll_openrouter_credits: HTTP %d — skipping update",
                    r.status_code,
                )
                return None
            payload = r.json()
    except Exception as e:
        logger.info("poll_openrouter_credits: exception %s — skipping update", type(e).__name__)
        return None

    data = payload.get("data") or payload
    try:
        total_credits = float(data.get("total_credits", 0.0))
        total_usage = float(data.get("total_usage", 0.0))
    except (TypeError, ValueError):
        logger.warning("poll_openrouter_credits: malformed payload %r", payload)
        return None

    remaining = max(0.0, total_credits - total_usage)
    now_mono = time.monotonic()

    _load_into_memory()
    # Stamp every OpenRouter-prefixed candidate in every known chain.
    with _STATE_LOCK:
        for model in list(_STATE.keys()):
            if not model.startswith("openrouter:"):
                continue
            h = _STATE[model]
            h.credits_remaining = remaining
            h.credits_unit = "usd_balance"
            # OpenRouter credits don't auto-reset on a clock — they're a
            # prepaid wallet. Use None to mean "no scheduled reset"; the
            # elector treats None reset_at as a static gate (recheck on
            # every election; refreshed by next poll).
            h.credits_reset_at = None
            h.credits_observed_at = now_mono
            h.credits_low_water = OPENROUTER_USD_LOW_WATER
            _STATE[model] = h
        # Also seed entries that haven't been probed yet (chain entries
        # discovered at import time but no probe completed).
        for chain_model in _all_known_models():
            if not chain_model.startswith("openrouter:"):
                continue
            if chain_model in _STATE:
                continue
            h = ModelHealth(model=chain_model)
            h.credits_remaining = remaining
            h.credits_unit = "usd_balance"
            h.credits_observed_at = now_mono
            h.credits_low_water = OPENROUTER_USD_LOW_WATER
            _STATE[chain_model] = h

    return {"total_credits": total_credits, "total_usage": total_usage,
            "remaining": remaining}


# ---------------------------------------------------------------------------
# Probing — records probe_history + skips degraded-window models on the
# regular tick.
# ---------------------------------------------------------------------------

async def probe_one(client: httpx.AsyncClient, model: str, api_key: str) -> tuple[bool, str, Optional[int]]:
    """Probe a single chain entry (NIM or cross-provider).

    `model` is the chain entry exactly as it appears in BRAIN_CHAIN etc., so
    it may carry a provider prefix (`openrouter:` / `groq:`). The resolvers
    pick the right base URL, headers, and stripped model id. `api_key` here
    is intentionally ignored — we always pick the right key for the model's
    provider via `_api_key_for()`, so a missing prefix-specific key skips
    that probe with a clear error rather than spuriously using the NIM key.
    Returns (ok, error_msg, latency_ms).
    """
    url = _base_url_for(model)
    upstream_model = _model_id_for(model)
    provider_key = _api_key_for(model)
    headers = _headers_for(model, provider_key)

    if not provider_key:
        # No key configured for this provider — treat as benign-skip. The
        # background loop will keep probing; once the user sets the key the
        # next tick will succeed.
        return False, "no_api_key", None

    t0 = time.time()
    try:
        r = await client.post(
            url,
            headers=headers,
            json={
                "model": upstream_model,
                "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                # KI-084 — max_tokens cut 5 → 1. Same 200 envelope, ~50×
                # less token spend; probe never inspects the body content
                # beyond `choices[0].message.content` existing.
                "max_tokens": PROBE_MAX_TOKENS,
                "temperature": 0.0,
            },
            timeout=PROBE_TIMEOUT_SEC,
        )
        elapsed = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            return False, f"http_{r.status_code}", elapsed
        # 404 sometimes returned in 200 envelope by NIM for unavailable models —
        # check the actual content
        try:
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                return False, "empty_content", elapsed
            return True, "ok", elapsed
        except Exception as e:
            return False, f"parse_fail: {type(e).__name__}", elapsed
    except httpx.TimeoutException:
        return False, "timeout", int((time.time() - t0) * 1000)
    except Exception as e:
        return False, f"net: {type(e).__name__}: {str(e)[:60]}", int((time.time() - t0) * 1000)


def _absorb_probe_result(model: str, ok: bool, err: str, latency: Optional[int]) -> None:
    """Update _STATE with one probe result. Used by both probe_all (every
    tick) and _reprobe_one (after-failure reprobe)."""
    with _STATE_LOCK:
        h = _STATE.get(model) or ModelHealth(model=model)
        h.tested_at = _now_iso()
        if ok:
            h.last_success_at = _now_iso()
            h.last_error = None
            h.last_status_code = None
            h.latency_ms = latency
            h.consecutive_failures = 0
            h.status = "healthy" if (latency or 0) < 5000 else "degraded"
        else:
            h.last_failure_at = _now_iso()
            h.last_error = err
            # KI-202 — backfill last_status_code from the err tag so the
            # admin Health column can render code-specific reasons (429 /
            # 503 / etc.) without changing every producer.
            h.last_status_code = _extract_status_code(err)
            h.consecutive_failures += 1
            if h.consecutive_failures >= DOWN_AFTER_CONSECUTIVE_FAILS:
                h.status = "down"
            else:
                h.status = "degraded"
        h.probe_history.append({
            "ok": ok,
            "latency_ms": latency,
            "ts": _now_iso(),
            "src": "probe",
        })
        if len(h.probe_history) > PROBE_HISTORY_LEN:
            h.probe_history = h.probe_history[-PROBE_HISTORY_LEN:]
        _STATE[model] = h


async def probe_all() -> dict[str, ModelHealth]:
    """One-shot probe of every known model. Updates persisted state + returns it."""
    _load_into_memory()
    models = _all_known_models()
    # Per-provider keys are resolved inside probe_one(); the loop is not
    # gated on the NIM key so cross-provider candidates keep getting
    # probed even when NVIDIA_NIM_API_KEY is missing.

    # Probe candidates serially, not in parallel. Candidates share the
    # same per-API-key concurrency quota (~3-5 slots free-tier), so a
    # parallel burst every 300s would queue inside NIM and steal slots
    # from in-flight user turns. A serial loop is simple to reason about
    # and naturally yields control to user traffic between candidates.
    #
    # Cost: ~6 NIM candidates × ~2s healthy probe = ~12s, well under the
    # 300s probe cadence. No sleep between candidates — the outbound
    # semaphore handles pacing.
    async with httpx.AsyncClient() as client:
        results = []
        for m in models:
            try:
                result = await probe_one(client, m, "")
            except Exception as exc:
                result = exc
            results.append(result)

    for model, result in zip(models, results):
        if isinstance(result, Exception):
            ok, err, latency = False, f"exc: {type(result).__name__}", None
        else:
            ok, err, latency = result
        _absorb_probe_result(model, ok, err, latency)

    save()  # persist in-memory state
    return load()


def filter_chain(chain: list[str]) -> list[str]:
    """Return the chain with 'down' models removed. Always preserves order +
    keeps at least one model so callers never get an empty chain.

    Kept for backward compatibility — admin endpoints + the final
    probe-refresh fallback in NimChainLLM still call this. Primary
    election (get_primary / get_backup) is the new hot-path entry."""
    state = load()
    keep = [m for m in chain if state.get(m, ModelHealth(model=m)).status != "down"]
    if not keep:
        return chain  # all known-down → still try them, infrastructure may have recovered
    return keep


def status_summary() -> dict:
    """Compact summary for /api/health/llms endpoint."""
    state = load()
    summary = {"updated_at": None, "by_status": {"healthy": 0, "degraded": 0, "down": 0, "stale": 0, "unknown": 0}, "models": []}
    for m, h in state.items():
        # `effective_status` applies the STALE_AGE_SEC override at read
        # time so the admin UI / router see "stale" for
        # rows whose stored 'healthy' verdict is older than STALE_AGE_SEC.
        eff = effective_status(h)
        summary["by_status"][eff] = summary["by_status"].get(eff, 0) + 1
        # KI-202 — operator-facing reason string for the admin Health
        # column. None when there is no error signal (caller renders just
        # "Live" or "Off — stale"/"Off — unknown" based on `effective_status`).
        if eff == "stale":
            health_reason = "stale"
        elif eff in ("healthy",):
            health_reason = None
        else:
            health_reason = _classify_error_reason(h.last_error, h.last_status_code)
        summary["models"].append({
            "model": m,
            "status": eff,
            "effective_status": eff,
            "stored_status": h.status,  # preserved for debug / drift detection
            "latency_ms": h.latency_ms,
            "last_success_at": h.last_success_at,
            "last_failure_at": h.last_failure_at,
            "last_error": h.last_error,
            "last_status_code": h.last_status_code,
            "health_reason": health_reason,
            "tested_at": h.tested_at,
            # KI-085 — surface credits state for the admin UI.
            "credits_remaining": h.credits_remaining,
            "credits_unit": h.credits_unit,
            "credits_low_water": h.credits_low_water,
        })
    summary["models"].sort(key=lambda x: (x["status"] != "healthy", x["model"]))
    if summary["models"]:
        summary["updated_at"] = max((m.get("tested_at") or "") for m in summary["models"])
    # Surface currently-elected primary/backup per chain for the admin UI.
    # Cheap (a couple of dict lookups + sort over <=10 entries). "brain"
    # is the only role.
    summary["elections"] = {
        role: {"primary": get_primary(role), "backup": get_backup(role)}
        for role in ROLES
    }
    return summary


async def background_probe_loop() -> None:
    """Long-running task — probes every PROBE_INTERVAL_SEC (300s).
    Started from main.py.

    Also polls OpenRouter's account-level credits endpoint every
    OPENROUTER_CREDITS_POLL_EVERY_N_TICKS ticks (10 min by default at the
    300s probe cadence). Groq + NIM signals come from the chat hot path
    (response headers + local rate-meter respectively) so only OpenRouter
    needs an out-of-band poll.
    """
    tick = 0
    # Initial credits poll on startup so the elector has a non-None
    # account-level signal before the first chat call.
    try:
        await poll_openrouter_credits()
    except Exception:
        pass
    while True:
        try:
            await probe_all()
        except Exception:
            pass  # never let one bad probe kill the loop
        tick += 1
        if tick % OPENROUTER_CREDITS_POLL_EVERY_N_TICKS == 0:
            try:
                await poll_openrouter_credits()
            except Exception:
                pass
        await asyncio.sleep(PROBE_INTERVAL_SEC)


if __name__ == "__main__":
    # CLI: one-shot probe + print summary
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    state = asyncio.run(probe_all())
    print(f"Probed {len(state)} models.")
    for m, h in sorted(state.items(), key=lambda kv: (kv[1].status != "healthy", kv[0])):
        latency = f"{h.latency_ms}ms" if h.latency_ms else "—"
        print(f"  {h.status:8s}  {latency:>8s}  {m:50s}  {h.last_error or 'ok'}")
    print()
    print("Elections:")
    for role in ROLES:
        print(f"  {role:10s}  primary={get_primary(role)}  backup={get_backup(role)}")
