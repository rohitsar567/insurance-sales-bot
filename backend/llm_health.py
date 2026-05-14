"""Live NIM model health monitor — OpenRouter-style availability filter
+ KI-080 sticky primary/backup election.

Architectural shift (KI-080, 2026-05-15):
==========================================
Pre-KI-080 the chain was iterated EVERY chat turn — primary, fallback 1,
fallback 2, ... until one succeeded. Under NIM per-key concurrency throttling,
the first 5 NIM-hosted candidates queued together inside a single turn,
burning the 22s budget before the cross-provider fallback links (Groq /
OpenRouter) were ever reached. The 10-turn live probe at commit 078ff45
showed 7/10 fact-find turns timing out at exactly 26.6s.

KI-080 inverts the model: the background probe loop ELECTS a primary +
backup per chain based on real probe latencies; chat() calls the elected
primary ONCE per turn, with at most ONE real-time fallback to the elected
backup. Worst case per turn drops from 5-6 LLM calls to 1-2.

Probes every PROBE_INTERVAL_SEC tick with a tiny ping ("Reply with exactly: ok").
Records per model:
  - status:           healthy / degraded / down / unknown
  - last_success_at:  timestamp of last 2xx response
  - last_failure_at:  timestamp of last failure (timeout / 5xx / parse)
  - latency_ms:       last successful response latency
  - consecutive_fail: counter (3+ => marked down)
  - tested_at:        when this row was last refreshed
  - probe_history:    last PROBE_HISTORY_LEN (ok, latency_ms) tuples; powers
                      the success_rate signal in the election score.

Election (KI-080):
  - For each chain (brain / fast_brain / judge), compute a score per healthy
    candidate: score = (1 / max(50, latency_ms)) * success_rate_last_5.
  - CURRENT_PRIMARY = highest scorer.
  - CURRENT_BACKUP  = highest scorer among candidates with a DIFFERENT
    provider (NIM vs Groq vs OpenRouter) than primary; falls back to next-
    best same-provider candidate when no cross-provider option qualifies.
  - DEGRADED window: when chat() calls report_failure(model), that model is
    sidelined for DEGRADED_WINDOW_SEC so the same turn's failure doesn't
    recycle to the same broken primary on the next turn. The next probe
    tick reconsiders the model normally.

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

import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent
HEALTH_FILE = ROOT / "40-data" / "llm_health.json"
HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)

# KI-080 — probe every 60s so election reflects real-time pool health
# fast enough that a NIM pool brownout is rotated out within a minute.
# The 5-min cadence pre-KI-080 was fine for a "filter the dead" use case
# but too slow when probe results drive primary election.
PROBE_INTERVAL_SEC = 60
PROBE_TIMEOUT_SEC = 8             # per-probe HTTP timeout
DOWN_AFTER_CONSECUTIVE_FAILS = 3  # 3 fails in a row = mark down
PROBE_HISTORY_LEN = 5             # rolling window for success_rate signal
HEALTHY_PROBE_AGE_SEC = 90        # election candidates need a probe within
                                  # the last 90s — stale data excluded
DEGRADED_WINDOW_SEC = 30          # report_failure sidelines a model this long

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
    status: str = "unknown"               # 'healthy' | 'degraded' | 'down' | 'unknown'
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_error: Optional[str] = None
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
    """Pull the union of every model name from every chain at module import."""
    from backend.providers.nvidia_nim_llm import (
        BRAIN_CHAIN, FAST_BRAIN_CHAIN, JUDGE_CHAIN,
    )
    seen: list[str] = []
    for chain in (BRAIN_CHAIN, FAST_BRAIN_CHAIN, JUDGE_CHAIN):
        for m in chain:
            if m not in seen:
                seen.append(m)
    return seen


def _chain_for(chain_name: str) -> list[str]:
    """Resolve a chain name → live chain list. Reads off the module so
    runtime admin reorderings are respected by the elector."""
    from backend.providers import nvidia_nim_llm as nim
    if chain_name == "brain":
        return list(getattr(nim, "BRAIN_CHAIN", []))
    if chain_name == "fast_brain":
        return list(getattr(nim, "FAST_BRAIN_CHAIN", []))
    if chain_name == "judge":
        return list(getattr(nim, "JUDGE_CHAIN", []))
    return []


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
                    # Tolerate older schema (pre-KI-080 records missing
                    # probe_history / degraded_until_monotonic).
                    v.setdefault("probe_history", [])
                    v.setdefault("degraded_until_monotonic", 0.0)
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
    kept for backward compatibility with the pre-KI-080 call sites in admin.py."""
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
# KI-080 — public API: primary/backup election + failure/success reporting
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


def _is_election_eligible(h: ModelHealth, now_mono: float) -> bool:
    """A candidate is electable when:
      - status is healthy (or degraded with a recent success)
      - last probe was within HEALTHY_PROBE_AGE_SEC
      - it is NOT currently in the degraded-window sin-bin
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
    return True


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


def get_primary(chain_name: str) -> Optional[str]:
    """Top-scoring election-eligible candidate, or None when no probe data
    is fresh enough. Callers cold-start by falling back to chain[0]."""
    ranked = _ranked_candidates(chain_name)
    return ranked[0].model if ranked else None


def get_backup(chain_name: str) -> Optional[str]:
    """Second-best election candidate. Prefers a DIFFERENT provider from
    primary (NIM vs Groq vs OpenRouter) so a single provider's regional
    outage can't take out both. Falls back to the next-best same-provider
    candidate when no cross-provider option qualifies — better an in-
    family backup than none."""
    ranked = _ranked_candidates(chain_name)
    if len(ranked) < 2:
        # No usable backup. Either zero candidates or only one (in which
        # case the cold-start path in NimChainLLM falls back to chain[1]).
        return None
    primary_provider = provider_of(ranked[0].model)
    # Prefer cross-provider
    for h in ranked[1:]:
        if provider_of(h.model) != primary_provider:
            return h.model
    # No cross-provider candidate — accept same-provider next-best.
    return ranked[1].model


def report_failure(chain_name: str, model: str, error_class: str) -> None:
    """Called by NimChainLLM.chat() when primary OR backup throws.

    Effects:
      - Sidelines `model` from election for DEGRADED_WINDOW_SEC so the
        same turn's failure doesn't immediately re-elect the same model.
      - Appends a synthetic 'failed' entry to probe_history so the next
        election's success_rate reflects the live failure even before
        the next probe tick.
      - Schedules an async re-probe (best-effort) so the next turn gets
        fresh data instead of waiting up to 60s for the next tick.
    """
    _load_into_memory()
    with _STATE_LOCK:
        h = _STATE.get(model) or ModelHealth(model=model)
        h.degraded_until_monotonic = time.monotonic() + DEGRADED_WINDOW_SEC
        h.last_failure_at = _now_iso()
        h.last_error = f"chat_failure: {error_class}"
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
# Probing (mostly unchanged from pre-KI-080 — extended to record
# probe_history + skip degraded-window models on the regular tick).
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
                "max_tokens": 5,
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
            h.latency_ms = latency
            h.consecutive_failures = 0
            h.status = "healthy" if (latency or 0) < 5000 else "degraded"
        else:
            h.last_failure_at = _now_iso()
            h.last_error = err
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
    # Per-provider keys are resolved inside probe_one(); we no longer
    # gate the whole loop on the NIM key (KI-080) — cross-provider
    # candidates must keep getting probed even when NVIDIA_NIM_API_KEY
    # is missing.

    async with httpx.AsyncClient() as client:
        # Probe all in parallel — they hit different NIM pools so concurrency is fine
        results = await asyncio.gather(
            *[probe_one(client, m, "") for m in models],
            return_exceptions=True,
        )

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
    summary = {"updated_at": None, "by_status": {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0}, "models": []}
    for m, h in state.items():
        summary["by_status"][h.status] = summary["by_status"].get(h.status, 0) + 1
        summary["models"].append({
            "model": m,
            "status": h.status,
            "latency_ms": h.latency_ms,
            "last_success_at": h.last_success_at,
            "last_failure_at": h.last_failure_at,
            "last_error": h.last_error,
            "tested_at": h.tested_at,
        })
    summary["models"].sort(key=lambda x: (x["status"] != "healthy", x["model"]))
    if summary["models"]:
        summary["updated_at"] = max((m.get("tested_at") or "") for m in summary["models"])
    # KI-080 — surface currently-elected primary/backup per chain for the
    # admin UI. Cheap (a couple of dict lookups + sort over <=10 entries).
    summary["elections"] = {
        role: {"primary": get_primary(role), "backup": get_backup(role)}
        for role in ("brain", "fast_brain", "judge")
    }
    return summary


async def background_probe_loop() -> None:
    """Long-running task — probes every PROBE_INTERVAL_SEC. Started from main.py."""
    while True:
        try:
            await probe_all()
        except Exception:
            pass  # never let one bad probe kill the loop
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
    for role in ("brain", "fast_brain", "judge"):
        print(f"  {role:10s}  primary={get_primary(role)}  backup={get_backup(role)}")
