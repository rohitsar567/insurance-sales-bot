"""Admin endpoints — password gated. NEVER exposed to ordinary users.

The four endpoints:
  GET  /api/admin/health        — full LLM health snapshot (every model)
  POST /api/admin/probe         — force a fresh probe of all models now
  GET  /api/admin/chain         — current brain chain order
  POST /api/admin/chain         — reorder the brain chain (promote a model to primary)

Access gate (KI-097 — single check):
  Request must include `X-Admin-Password` header matching ADMIN_PASSWORD env.

Setup:
  Add to Space secrets (Settings → Variables and secrets):
    ADMIN_PASSWORD = your-strong-password-here

Returns 401 Unauthorized on bad/missing password. Access is gated by a
strong password only; there is no IP allowlist (it would add operational
complexity — changing networks would lock the operator out — without
meaningful additional security).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

from backend import llm_health
from backend.config import settings

router = APIRouter()


# Cap how many tail lines of 40-data/llm_usage.jsonl we hold in memory while
# computing per-role stats. 1000 lines @ ~150B each = ~150 KB peak — bounded.
USAGE_TAIL_LINES = 1000


# A5 — Audit fix #7: emit a console warning at import time when the admin
# password is unset. We don't break access (the password check below already
# returns False which 401s every request), but ops needs a loud signal that
# the gate is effectively unconfigured so deployments don't silently sit
# behind an unreachable admin surface.
if not os.environ.get("ADMIN_PASSWORD", "").strip():
    import sys as _sys
    print(
        "[admin] WARNING: ADMIN_UNGATED — ADMIN_PASSWORD env var is empty. "
        "All /api/admin/* requests will return 401. Set ADMIN_PASSWORD in "
        "the deployment env to enable access.",
        file=_sys.stderr,
        flush=True,
    )


def _password_ok(supplied: Optional[str]) -> bool:
    expected = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not expected:
        return False  # no password configured => deny
    return supplied is not None and supplied == expected


def _check_admin(request: Request, password: Optional[str]) -> None:
    """Password-only gate. Raises 401 on bad/missing password.

    `request` is kept in the signature so callsites don't churn; the
    function does not inspect the client IP.
    """
    # TODO: enforce an IP allowlist as a second factor for hardening (with
    # a documented break-glass procedure so a network change doesn't lock
    # ops out). The gate is password-only, acceptable for the current
    # threat model.
    if not _password_ok(password):
        raise HTTPException(status_code=401, detail="Unauthorized")


# --- Endpoints --------------------------------------------------------------

@router.get("/api/admin/health")
async def admin_health(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    _check_admin(request, x_admin_password)
    return llm_health.status_summary()


@router.post("/api/admin/probe")
async def admin_probe(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    _check_admin(request, x_admin_password)
    state = await llm_health.probe_all()
    return {"probed": len(state), "summary": llm_health.status_summary()}


@router.get("/api/admin/chain")
async def admin_chain_get(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    _check_admin(request, x_admin_password)
    from backend.providers.nvidia_nim_llm import BRAIN_CHAIN
    # Three-chain collapse (2026-05-15) — only the brain role remains.
    return {
        "brain": BRAIN_CHAIN,
    }


class ChainReorderRequest(BaseModel):
    role: str   # 'brain'
    order: list[str]   # new ordering of model ids


@router.post("/api/admin/chain")
async def admin_chain_set(
    body: ChainReorderRequest,
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    _check_admin(request, x_admin_password)
    if body.role != "brain":
        raise HTTPException(status_code=400, detail="role must be 'brain'")

    # Mutate the in-memory chain
    from backend.providers import nvidia_nim_llm as nim
    setattr(nim, "BRAIN_CHAIN", list(body.order))

    # Persist for next process restart — write to 40-data/admin_overrides.json
    override_path = settings.DATA_DIR / "admin_overrides.json"
    override_path.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if override_path.exists():
        try:
            state = json.loads(override_path.read_text())
        except Exception:
            state = {}
    state[body.role] = body.order
    override_path.write_text(json.dumps(state, indent=2) + "\n")

    return {"ok": True, "role": body.role, "new_chain": body.order}


# ---------------------------------------------------------------------------
# /api/admin/usage — per-role usage stats + next-best-recommended
# ---------------------------------------------------------------------------

def _tail_jsonl(path: Path, n: int) -> list[dict]:
    """Read the last `n` lines of a JSONL file, returning parsed dicts.

    Bad lines are skipped silently. Returns [] when the file doesn't exist —
    backward-compatible behavior so the endpoint stays alive before any usage
    has been logged.
    """
    if not path.exists():
        return []
    try:
        # File is bounded by the 1 MB rotation cap in nvidia_nim_llm._append_usage,
        # so reading the whole thing and slicing tail is fine; deque keeps memory
        # bounded even when no rotation has happened yet.
        with path.open("r", encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=n)
        out: list[dict] = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue  # skip malformed line, keep going
        return out
    except Exception:
        return []


def _stat_block_for_role(rows: list[dict], role: str, chain: list[str],
                         health_state: dict) -> dict:
    """Compute the per-role JSON payload (see endpoint docstring for shape)."""
    role_rows = [r for r in rows if r.get("role") == role]
    total = len(role_rows)
    success_count = sum(1 for r in role_rows if r.get("success") is True)
    success_rate = (success_count / total) if total else 0.0

    # Group by served_model (skip rows with no served_model — those are total
    # failures already captured in the role-level success_rate).
    by_model: dict[str, dict] = {}
    for r in role_rows:
        m = r.get("served_model")
        if not m:
            continue
        bucket = by_model.setdefault(m, {"calls": 0, "latency_sum": 0,
                                         "success_count": 0})
        bucket["calls"] += 1
        try:
            bucket["latency_sum"] += int(r.get("latency_ms") or 0)
        except Exception:
            pass
        if r.get("success") is True:
            bucket["success_count"] += 1

    by_model_list = []
    total_served = sum(b["calls"] for b in by_model.values()) or 1
    for m, b in by_model.items():
        avg_lat = int(b["latency_sum"] / b["calls"]) if b["calls"] else 0
        m_success = (b["success_count"] / b["calls"]) if b["calls"] else 0.0
        by_model_list.append({
            "model": m,
            "calls": b["calls"],
            "share": round(b["calls"] / total_served, 4) if total_served else 0.0,
            "avg_latency_ms": avg_lat,
            "success_rate": round(m_success, 4),
        })
    by_model_list.sort(key=lambda x: -x["calls"])

    primary_model = chain[0] if chain else None

    # next_best_recommended: highest-position model in the chain (after the
    # primary) whose llm_health status is 'healthy'. If none are healthy,
    # fall back to chain[1] with next_best_is_unverified=true so the UI can
    # flag the recommendation as unproven.
    next_best = None
    next_best_unverified = False
    for m in chain[1:]:
        h = health_state.get(m)
        status = getattr(h, "status", None) if h else None
        if status == "healthy":
            next_best = m
            break
    if next_best is None:
        next_best = chain[1] if len(chain) > 1 else None
        next_best_unverified = next_best is not None

    return {
        "total_calls_24h": total,
        "success_rate": round(success_rate, 4),
        "by_model": by_model_list,
        "primary_model": primary_model,
        "next_best_recommended": next_best,
        "next_best_is_unverified": next_best_unverified,
    }


@router.get("/api/admin/usage")
async def admin_usage(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    """Per-role usage stats over the last USAGE_TAIL_LINES log entries.

    Backward-compatible: if 40-data/llm_usage.jsonl doesn't exist yet, returns
    zero-stat blocks with primary_model = current chain[0]. The frontend can
    render an empty-state without any extra branching.
    """
    _check_admin(request, x_admin_password)

    # Live (post-override) chain — read attribute off the module so any
    # admin reorder applied in the current process is reflected immediately.
    # Three-chain collapse (2026-05-15) — only the brain role remains.
    from backend.providers import nvidia_nim_llm as nim
    chains = {
        "brain": list(getattr(nim, "BRAIN_CHAIN", [])),
    }

    usage_path = settings.DATA_DIR / "llm_usage.jsonl"
    rows = _tail_jsonl(usage_path, USAGE_TAIL_LINES)
    health_state = llm_health.load()  # {model: ModelHealth} dict

    return {
        role: _stat_block_for_role(rows, role, chains[role], health_state)
        for role in chains
    }


# ---------------------------------------------------------------------------
# /api/admin/profiles — list every named profile + summary
# ---------------------------------------------------------------------------

@router.get("/api/admin/profiles")
async def admin_profiles(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    """List every named profile in the JSON store + lightweight summary.

    Returned shape:
      {
        "profiles": [ {name_display, name_slug, first_seen, last_seen,
                       session_count, profile_complete_fields}, ... ],
        "total": N,
        "snapshot_ts": "2026-05-14T..."
      }
    """
    _check_admin(request, x_admin_password)

    from backend import profile_store
    profiles = profile_store.list_profiles()
    return {
        "profiles": profiles,
        "total": len(profiles),
        "snapshot_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# KI-063 (2026-05-15) — user-facing profile-event endpoints.
#
# These are NOT admin-gated — they're invoked by the frontend when a logged-
# in user (one with a stored profile.name) clicks the select/reject buttons
# on a policy card. Both look up the session, validate that the session has
# a named profile, then append the event through `profile_store.record_policy_event`.
#
# Anonymous sessions (no profile.name) get 400 — there's no key to persist
# against. The frontend should hide the buttons in that case.
# ---------------------------------------------------------------------------


class _PolicyEventBody(BaseModel):
    session_id: str
    policy_slug: str
    insurer: str
    reason: Optional[str] = None


def _do_record_policy_event(body: _PolicyEventBody, event_type: str) -> dict:
    """Shared handler for /api/profile/select + /api/profile/reject."""
    if not body.session_id or not body.policy_slug or not body.insurer:
        raise HTTPException(
            status_code=400,
            detail="session_id, policy_slug, and insurer are required",
        )
    from backend.session_state import get_session
    from backend.profile_store import record_policy_event

    session = get_session(body.session_id)
    if not session.profile.name:
        raise HTTPException(
            status_code=400,
            detail="No named profile on this session — cannot persist event.",
        )
    ok = record_policy_event(
        persona_id_or_name=session.profile.name,
        profile=session.profile,
        event_type=event_type,  # type: ignore[arg-type]
        policy_slug=body.policy_slug,
        insurer=body.insurer,
        session_id=body.session_id,
        reason=body.reason,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="profile save failed")
    # Also persist via the session flush so an in-memory consumer (e.g. the
    # welcome-back greeter) reads the same state without a full disk reload.
    session._flush()
    field_name = {
        "shown": "shown_policies",
        "selected": "selected_policies",
        "rejected": "rejected_policies",
    }[event_type]
    return {
        "ok": True,
        "event_type": event_type,
        "policy_slug": body.policy_slug,
        "count": len(getattr(session.profile, field_name, []) or []),
    }


@router.post("/api/profile/select")
async def profile_select(body: _PolicyEventBody):
    """Record a user clicking "shortlist / save" on a policy card."""
    return _do_record_policy_event(body, "selected")


@router.post("/api/profile/reject")
async def profile_reject(body: _PolicyEventBody):
    """Record a user clicking "not for me / reject" on a policy card."""
    return _do_record_policy_event(body, "rejected")


# ---------------------------------------------------------------------------
# /api/admin/performance — aggregated performance/quality metrics
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_eval_summary() -> Optional[dict]:
    """Return the `summary` block from eval/results.json — or None if missing."""
    p = _REPO_ROOT / "eval" / "results.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    summary = raw.get("summary") if isinstance(raw, dict) else None
    if not isinstance(summary, dict):
        return None
    # Surface exactly the fields the admin panel cares about. Use .get so any
    # missing field becomes None rather than raising.
    return {
        "ran_at":             summary.get("ran_at"),
        "elapsed_seconds":    summary.get("elapsed_seconds"),
        "n_questions":        summary.get("n_questions"),
        "factual_accuracy":   summary.get("factual_accuracy"),
        "citation_accuracy":  summary.get("citation_accuracy"),
        "refusal_precision":  summary.get("refusal_precision"),
        "by_brain":           summary.get("by_brain") or {},
        "by_type":            summary.get("by_type") or {},
    }


def _latest_audit_dir() -> Optional[Path]:
    """Return the most recently modified `80-audit/full_*` directory containing
    a summary.json. Returns None if no such directory exists."""
    audit_root = _REPO_ROOT / "80-audit"
    if not audit_root.exists():
        return None
    candidates = [d for d in audit_root.glob("full_*") if (d / "summary.json").exists()]
    if not candidates:
        return None
    candidates.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return candidates[0]


# Regex helpers for parsing report.md (analyze.py output). Anchored to the
# specific table rows so they're robust against table reordering.
_RE_REPORT_PERSONAS = re.compile(r"Personas completed \| \*\*(\d+)\*\* of (\d+)")
_RE_REPORT_TURNS    = re.compile(r"Total turns executed \| \*\*(\d+)\*\*")
_RE_REPORT_ERRORS   = re.compile(r"Errors \(HTTP / timeout / network\) \| (\d+)")
_RE_REPORT_REFUSALS = re.compile(r"Refusals \(blocked=true\) \| (\d+)")
_RE_REPORT_P50      = re.compile(r"Latency p50 \| (\d+)\s*ms")
_RE_REPORT_P95      = re.compile(r"Latency p95 \| (\d+)\s*ms")
_RE_REPORT_P99      = re.compile(r"Latency p99 \| (\d+)\s*ms")
_RE_BRAIN_ROW       = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*$")


def _parse_brain_routing(report_text: str) -> dict[str, int]:
    """Extract the `## 2. Brain routing` table → {brain: turn_count}."""
    out: dict[str, int] = {}
    section_start = report_text.find("## 2. Brain routing")
    if section_start < 0:
        return out
    section_end = report_text.find("## 3.", section_start)
    if section_end < 0:
        section_end = len(report_text)
    for line in report_text[section_start:section_end].splitlines():
        m = _RE_BRAIN_ROW.match(line)
        if m:
            try:
                out[m.group(1)] = int(m.group(2))
            except ValueError:
                continue
    return out


def _read_audit_summary() -> Optional[dict]:
    """Return aggregate metrics for the latest persona-audit run.

    The on-disk summary.json is intentionally sparse (it's just the launcher's
    config). The real metrics live in `report.md` (produced by
    tools/audit/analyze.py). We parse it via regex — much cheaper than
    re-walking 100+ transcript JSONs on every admin request.

    Returns None if there is no audit run yet OR if the report.md hasn't been
    generated (the launcher writes summary.json before analyze.py runs).
    """
    run_dir = _latest_audit_dir()
    if run_dir is None:
        return None

    summary_path = run_dir / "summary.json"
    report_path  = run_dir / "report.md"
    try:
        launcher_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        launcher_summary = {}

    out: dict = {
        "run_id":            launcher_summary.get("run_id") or run_dir.name,
        "personas_requested": launcher_summary.get("personas_requested"),
        "personas_completed": launcher_summary.get("personas_completed"),
        "elapsed_seconds":   launcher_summary.get("elapsed_seconds"),
        "turns_total":       None,
        "errors":            None,
        "refusals":          None,
        "p50_ms":            None,
        "p95_ms":            None,
        "p99_ms":            None,
        "brain_routing":     {},
    }

    if not report_path.exists():
        return out  # launcher ran but analyze.py hasn't; surface what we have

    try:
        report_text = report_path.read_text(encoding="utf-8")
    except Exception:
        return out

    def _int_match(rx: re.Pattern[str]) -> Optional[int]:
        m = rx.search(report_text)
        if not m:
            return None
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            return None

    # Pull values from report.md — overrides None defaults set above. Personas
    # completed lives in BOTH summary.json and report.md; report.md wins because
    # it reflects the actually-analyzed transcripts (in case the launcher
    # claimed N but only M wrote transcripts).
    p_match = _RE_REPORT_PERSONAS.search(report_text)
    if p_match:
        try:
            out["personas_completed"] = int(p_match.group(1))
        except ValueError:
            pass

    out["turns_total"] = _int_match(_RE_REPORT_TURNS)
    out["errors"]      = _int_match(_RE_REPORT_ERRORS)
    out["refusals"]    = _int_match(_RE_REPORT_REFUSALS)
    out["p50_ms"]      = _int_match(_RE_REPORT_P50)
    out["p95_ms"]      = _int_match(_RE_REPORT_P95)
    out["p99_ms"]      = _int_match(_RE_REPORT_P99)
    out["brain_routing"] = _parse_brain_routing(report_text)
    return out


def _read_usage_24h() -> Optional[dict]:
    """Compute {role: {count, success_rate, avg_latency_ms}} from the last
    USAGE_TAIL_LINES entries of 40-data/llm_usage.jsonl. Returns None if the
    file is missing OR empty so the frontend can render an empty-state.

    Note: "24h" in the field name is conventional — the actual window is the
    last USAGE_TAIL_LINES rows (typically covers ≈24h of activity at current
    traffic). Keeping the name aligns with the admin UI label.

    The live stack emits the `brain` role (see
    providers.nvidia_nim_llm.get_brain_llm + llm_health.ROLES). The
    append-only llm_usage.jsonl can contain rows tagged with non-canonical
    role values, so we filter to the canonical role set — the same way
    /api/admin/usage does via its `chains` dict — instead of surfacing
    every role value present in the file. llm_health.ROLES is the single
    source of truth (also used as _LLM_HEALTH_CHAIN_ROLES below).
    """
    usage_path = settings.DATA_DIR / "llm_usage.jsonl"
    rows = _tail_jsonl(usage_path, USAGE_TAIL_LINES)
    if not rows:
        return None

    canonical_roles = set(llm_health.ROLES)
    agg: dict[str, dict] = {}
    for r in rows:
        role = r.get("role")
        if not role or role not in canonical_roles:
            continue
        bucket = agg.setdefault(role, {"count": 0, "success_count": 0, "latency_sum": 0,
                                       "latency_n": 0})
        bucket["count"] += 1
        if r.get("success") is True:
            bucket["success_count"] += 1
        lat = r.get("latency_ms")
        if isinstance(lat, (int, float)):
            bucket["latency_sum"] += int(lat)
            bucket["latency_n"] += 1

    out: dict[str, dict] = {}
    for role, b in agg.items():
        out[role] = {
            "count": b["count"],
            "success_rate": round(b["success_count"] / b["count"], 4) if b["count"] else 0.0,
            "avg_latency_ms": int(b["latency_sum"] / b["latency_n"]) if b["latency_n"] else 0,
        }
    return out


@router.get("/api/admin/performance")
async def admin_performance(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    """Aggregated performance/quality metrics for the admin Performance section.

    All four sub-blocks are independently nullable — a missing eval/results.json
    or absent audit run shouldn't 500 the endpoint.
    """
    _check_admin(request, x_admin_password)
    return {
        "eval":       _read_eval_summary(),
        "audit":      _read_audit_summary(),
        "usage_24h":  _read_usage_24h(),
        "snapshot_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# /api/admin/llm-health — KI-086 LLM Health & Credits snapshot
#
# Surfaces KI-080..KI-085 telemetry on the existing admin "LLM Chain" tab so
# the operator can see at a glance:
#   - per-chain elected PRIMARY + BACKUP (KI-080)
#   - each candidate's latest probe latency + success rate (KI-080)
#   - credits remaining + unit + reset deadline (KI-085)
#   - degraded-until window when a candidate was demoted on 429 (KI-084)
#   - per-turn served-model distribution from the last N llm_usage.jsonl rows
#
# Response shape — three top-level keys (chains / candidates / recent_turns) +
# a snapshot_ts. All durations on the wire are seconds-from-now (relative,
# never absolute monotonic) so the frontend doesn't need to know the server's
# monotonic clock origin.
# ---------------------------------------------------------------------------


# Map of the on-wire chain role name → human-friendly label. The roles
# themselves match llm_health.get_primary() input strings exactly.
# Three-chain collapse (2026-05-15) — only the brain role remains; we
# defer to llm_health.ROLES so a future role only needs to be added once.
_LLM_HEALTH_CHAIN_ROLES = llm_health.ROLES


def _chain_names_map() -> dict[str, list[str]]:
    """Live (post-admin-override) chain config — read off the module so
    admin reorders applied earlier in the same process are reflected
    immediately. Only the brain role is present."""
    from backend.providers import nvidia_nim_llm as nim
    return {
        "brain": list(getattr(nim, "BRAIN_CHAIN", [])),
    }


def _seconds_until_monotonic(deadline: Optional[float]) -> Optional[float]:
    """Convert a monotonic-time deadline (as stamped in ModelHealth) into a
    seconds-from-now value the frontend can render as an ETA. Returns None
    when the deadline is missing OR already in the past; the caller decides
    how to render `None` vs `0`."""
    if deadline is None:
        return None
    import time as _time
    rem = deadline - _time.monotonic()
    if rem <= 0:
        return 0.0
    return round(rem, 1)


def _probe_age_seconds(iso_ts: Optional[str]) -> Optional[float]:
    """Wall-clock seconds since a probe iso8601 timestamp. Cheap wrapper
    around llm_health._iso_age_seconds for the wire payload."""
    age = llm_health._iso_age_seconds(iso_ts)
    if age is None:
        return None
    return max(0.0, round(age, 1))


def _success_rate_for(h) -> Optional[float]:
    """Last-N probes success rate as a float fraction. Returns None when no
    probe history yet."""
    hist = getattr(h, "probe_history", None) or []
    if not hist:
        return None
    hits = sum(1 for r in hist if r.get("ok"))
    return round(hits / len(hist), 4)


def _candidate_snapshot(model: str, health, chain_membership: list[str],
                        now_mono: float) -> dict:
    """Per-candidate row for Section B. Always returns a dict — even when
    the model has never been probed yet (status='unknown', everything else
    None) — so the frontend table doesn't have to handle missing rows."""
    if health is None:
        return {
            "model":              model,
            "provider":           llm_health.provider_of(model),
            "chain_membership":   chain_membership,
            "status":             "unknown",
            "effective_status":   "unknown",
            "latency_ms":         None,
            "success_rate":       None,
            "probe_age_seconds":  None,
            "last_error":         None,
            "last_status_code":   None,
            "health_reason":      None,
            "credits_remaining":  None,
            "credits_unit":       None,
            "credits_low_water":  None,
            "credits_reset_in_seconds": None,
            "degraded_for_seconds":    None,
        }
    deg_until = getattr(health, "degraded_until_monotonic", 0.0) or 0.0
    deg_for = None
    if deg_until and deg_until > now_mono:
        deg_for = round(deg_until - now_mono, 1)
    # KI-202 — operator-facing reason for the admin Health column. None when
    # the row is healthy (renders "Live" only) or has no error signal yet.
    eff = llm_health.effective_status(health)
    if eff == "stale":
        health_reason = "stale"
    elif eff == "healthy":
        health_reason = None
    else:
        health_reason = llm_health._classify_error_reason(
            health.last_error, health.last_status_code,
        )
    return {
        "model":             model,
        "provider":          llm_health.provider_of(model),
        "chain_membership":  chain_membership,
        "status":            health.status,
        "effective_status":  eff,
        "latency_ms":        health.latency_ms,
        "success_rate":      _success_rate_for(health),
        "probe_age_seconds": _probe_age_seconds(health.tested_at),
        "last_error":        health.last_error,
        "last_status_code":  health.last_status_code,
        "health_reason":     health_reason,
        "credits_remaining": health.credits_remaining,
        "credits_unit":      health.credits_unit,
        "credits_low_water": health.credits_low_water,
        "credits_reset_in_seconds": _seconds_until_monotonic(health.credits_reset_at),
        "degraded_for_seconds":     deg_for,
    }


def _candidate_available_for_calls(h, now_mono: float) -> bool:
    """KI-122 — Is this candidate AVAILABLE RIGHT NOW for real chat traffic?

    This is the operator-facing definition of "live and usable":
      - probe says healthy or degraded (NOT 'down' / 'unknown')
      - has not been sin-binned by report_failure() in the last
        DEGRADED_WINDOW_SEC / DEGRADE_DURATION_LONG_S window
      - credits gate (`_has_credits`) passes — either no signal, OR
        credits_reset_at has elapsed (stale snapshot), OR credits_remaining
        is above the candidate's low_water.

    Returns False for None / unknown candidates."""
    if h is None:
        return False
    if h.status in ("down", "unknown"):
        return False
    if h.degraded_until_monotonic and h.degraded_until_monotonic > now_mono:
        return False
    if not llm_health._has_credits(h, now_mono):
        return False
    return True


def _candidate_credit_exhausted_strict(h, now_mono: float) -> bool:
    """STRICT credit-exhausted rule for a single candidate.

    True ONLY when ALL three hold:
      (1) credits_remaining is NOT None (we have a real signal)
      (2) credits_remaining <= credits_low_water
      (3) credits_reset_at is set AND in the FUTURE (we're inside an
          active gating window — stale or absent reset means the
          snapshot is not authoritative).

    This is intentionally stricter than `not _has_credits()` because the
    banner is louder than the elector. The elector falls through cheaply
    on a single bad candidate; this strictness avoids falsely scaring the
    operator when one quota-exhausted backup co-exists with a perfectly
    healthy primary."""
    if h is None:
        return False
    if h.credits_remaining is None:
        return False
    if h.credits_remaining > (h.credits_low_water or 0.0):
        return False
    if h.credits_reset_at is None:
        # No scheduled reset (e.g. OpenRouter usd_balance — prepaid wallet).
        # Could still be a real "wallet empty" signal, BUT we won't flag the
        # chain banner on it alone because OpenRouter free-tier accounts
        # report $0 even when calls succeed. The probe / chat success
        # signal is the authoritative truth — fall through to the probe
        # status check in `_candidate_available_for_calls` instead.
        return False
    if now_mono >= h.credits_reset_at:
        # Reset window has elapsed — snapshot is stale, treat as permissive.
        return False
    return True


def _chain_summary(role: str, chains: dict[str, list[str]],
                   state: dict, now_mono: float) -> dict:
    """Per-chain block for Section A. Includes elected primary/backup +
    `chain_credit_exhausted` so the frontend can render a banner when every
    candidate in the chain is genuinely unusable."""
    chain = chains.get(role) or []
    primary = llm_health.get_primary(role)
    backup = llm_health.get_backup(role)

    # KI-122 (2026-05-15) — STRICT chain_credit_exhausted rule.
    #
    # Earlier rules (KI-085, KI-116) tried to skip cold-start candidates
    # and elapsed-reset snapshots but still left a hole: when one chain
    # member had a None reset_at + low credits (typical for OpenRouter's
    # usd_balance) while sibling NIM candidates had `credits_remaining=None`
    # (no signal yet), the loop registered `any_signal=True` on the bad
    # OpenRouter row but never broke out via a fresh NIM peer — so the
    # banner fired despite the elected NIM primary being HEALTHY · 100%.
    #
    # New rule: banner fires ONLY when EVERY chain member is BOTH
    #   (a) not available_for_calls (down / sin-binned / credit-gated), AND
    #   (b) at least one of those failures is a credit-exhaustion signal
    #       (otherwise it's a "chain entirely down" situation, which
    #        deserves a different banner — handled by the elected_primary
    #        == None path in the frontend).
    members_info: list[dict] = []
    any_available = False
    any_credit_exhausted = False
    last_probe_at_iso: Optional[str] = None
    for m in chain:
        h = state.get(m)
        available = _candidate_available_for_calls(h, now_mono)
        credit_exhausted = _candidate_credit_exhausted_strict(h, now_mono)
        if available:
            any_available = True
        if credit_exhausted:
            any_credit_exhausted = True
        # Track the most-recent probe timestamp across the chain.
        ts = getattr(h, "tested_at", None) if h is not None else None
        if ts and (last_probe_at_iso is None or ts > last_probe_at_iso):
            last_probe_at_iso = ts
        members_info.append({
            "model":               m,
            "available_for_calls": available,
            "credit_exhausted":    credit_exhausted,
            "is_current_primary":  bool(primary and m == primary),
            "is_current_backup":   bool(backup and m == backup),
        })
    # Banner fires only when: zero available members AND at least one
    # member is strictly credit-exhausted. If everyone is just 'down'
    # (probe failures) the banner is the wrong message — the operator
    # needs the "no eligible candidate" state, which the frontend already
    # renders via `elected_primary == None`.
    chain_credit_exhausted = bool((not any_available) and any_credit_exhausted)

    return {
        "role":            role,
        "chain":           chain,
        "chain_members":   members_info,
        "elected_primary": primary,
        "elected_backup":  backup,
        # KI-122 — explicit "currently in use" name + a boolean the
        # frontend uses to render the "● IN USE" pill without re-deriving.
        "current_primary": primary,
        "current_primary_available": _candidate_available_for_calls(
            state.get(primary) if primary else None, now_mono,
        ),
        "primary_snapshot": _candidate_snapshot(
            primary, state.get(primary), [role], now_mono,
        ) if primary else None,
        "backup_snapshot": _candidate_snapshot(
            backup, state.get(backup), [role], now_mono,
        ) if backup else None,
        "chain_credit_exhausted": chain_credit_exhausted,
        # KI-122 — operator-facing staleness signal. ISO string + a
        # convenience seconds-ago value so the UI can render "Probed Ns ago".
        "last_probe_at":         last_probe_at_iso,
        "last_probe_age_seconds": _probe_age_seconds(last_probe_at_iso),
    }


def _recent_turns(n: int = 20) -> list[dict]:
    """Section C — last N completed turns from 40-data/llm_usage.jsonl,
    most-recent-first. We keep the row shape close to the raw log entries
    so the frontend can render new fields if the producer adds them.

    Fields surfaced (all optional — older rows pre-KI-080 won't carry
    elected_primary/backup):
      ts / role / chain_primary / elected_primary / elected_backup /
      served_model / latency_ms / success / fallback_reason
    """
    usage_path = settings.DATA_DIR / "llm_usage.jsonl"
    # Re-use the existing tail-reader; cap at N so we don't pay for the
    # full 1000-row tail when the panel only renders 20.
    rows = _tail_jsonl(usage_path, max(n, 20))
    if not rows:
        return []
    out: list[dict] = []
    # tail_jsonl returns oldest-first; reverse to newest-first.
    for r in reversed(rows[-n:]):
        out.append({
            "ts":              r.get("ts"),
            "role":            r.get("role"),
            "chain_primary":   r.get("chain_primary"),
            "elected_primary": r.get("elected_primary"),
            "elected_backup":  r.get("elected_backup"),
            "served_model":    r.get("served_model"),
            "latency_ms":      r.get("latency_ms"),
            "success":         r.get("success"),
            "fallback_reason": r.get("fallback_reason"),
        })
    return out


@router.get("/api/admin/llm-health")
async def admin_llm_health(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    """KI-086 — composite LLM health + credits snapshot for the LLM Chain tab.

    Returns three keys:
      chains       — Section A: one entry per chain (brain — the only
                     remaining role after the three-chain collapse) with
                     elected primary + backup + their snapshots +
                     chain_credit_exhausted banner flag.
      candidates   — Section B: one row per known candidate across all chains,
                     with chain_membership listing every chain it appears in,
                     latency / success / credits / degraded-window state.
      recent_turns — Section C: last 20 served turns from llm_usage.jsonl.
    Plus snapshot_ts so the UI can show "updated <wallclock>".
    """
    _check_admin(request, x_admin_password)

    # KI-088: admin endpoint must never trigger probes — read cached state only.
    # Live probing from the admin tab (polled every 30s by the frontend) would
    # stack 6+ NIM candidates onto the same per-key concurrency budget and
    # starve user chat traffic. All data below comes from llm_health.load()
    # (in-memory snapshot persisted by the background_probe_loop) and the
    # llm_usage.jsonl append-only log — both are read-only and trigger zero
    # outbound LLM calls.
    import time as _time
    now_mono = _time.monotonic()

    chains = _chain_names_map()
    state = llm_health.load()  # {model -> ModelHealth}  (cached snapshot only)

    # Section A: per-chain election + credit banner.
    chains_block = [
        _chain_summary(role, chains, state, now_mono)
        for role in _LLM_HEALTH_CHAIN_ROLES
    ]

    # Section B: every known candidate × chain membership (deduped).
    # Membership is the list of chain roles a model belongs to.
    membership: dict[str, list[str]] = {}
    for role, models in chains.items():
        for m in models:
            membership.setdefault(m, []).append(role)
    # Also include any candidate present in state but not currently in any
    # chain (admin reorder may have just removed it) so the operator can
    # still see its last probe + credits.
    for m in state.keys():
        membership.setdefault(m, [])

    candidates_block = [
        _candidate_snapshot(m, state.get(m), membership[m], now_mono)
        for m in membership.keys()
    ]
    # Sort: degraded first (red), then non-healthy (amber), healthy last,
    # then by model name. Helps the operator see problems at the top.
    _status_rank = {"down": 0, "degraded": 1, "unknown": 2, "healthy": 3}
    candidates_block.sort(
        key=lambda c: (_status_rank.get(c["status"], 9), c["model"])
    )

    # Section C: last 20 turns.
    recent = _recent_turns(20)

    return {
        "chains":       chains_block,
        "candidates":   candidates_block,
        "recent_turns": recent,
        "snapshot_ts":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# A5 — Audit fix #4: /api/admin/persona-drift — slot-capture completeness
# for the last 20 personas. Seven canonical required slots: name, age,
# dependents, location_tier, income_band, primary_goal, health_conditions.
# <50% capture is flagged red on the frontend.
# ---------------------------------------------------------------------------

# The seven canonical "ready-to-recommend" fact-find slots. Mirrors
# brain_tools._REQUIRED_FOR_READY (the recommendation-ready required set)
# and needs_finder.Profile's persisted fields. Adding/removing a slot must
# stay in sync with that required set. Health is captured as a list (empty
# list counts as "asked but no conditions" → still a valid captured signal
# once the `asked` array contains the field name).
_PERSONA_DRIFT_SLOTS = ("name", "age", "dependents", "location_tier",
                        "income_band", "primary_goal", "health_conditions")


def _slot_captured(profile: dict, slot: str, asked: list[str]) -> bool:
    """A slot is 'captured' when the field has a non-empty value OR (for
    health_conditions) when the user was asked and confirmed no conditions
    (asked-list contains the slot but the list is empty → still a positive
    answer the bot heard, not a missing signal)."""
    v = profile.get(slot)
    if slot == "health_conditions":
        if isinstance(v, list) and len(v) > 0:
            return True
        if "health_conditions" in (asked or []):
            return True
        return False
    return v not in (None, "", [], 0)


@router.get("/api/admin/persona-drift")
async def admin_persona_drift(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    """Return slot-capture completeness for the last 20 personas, newest first.

    Each row: { persona_id, name_display, last_seen, captured_slots,
                completeness_pct, missing_slots }
    The frontend highlights any row with completeness_pct < 50.
    """
    _check_admin(request, x_admin_password)
    if not _PROFILES_DIR_FOR_DRIFT.exists():
        return {"personas": [], "total": 0,
                "snapshot_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    rows: list[dict] = []
    for p in _PROFILES_DIR_FOR_DRIFT.glob("*.json"):
        try:
            raw = json.loads(p.read_text())
        except Exception:
            continue
        profile = raw.get("profile") or {}
        asked = profile.get("asked") or []
        captured = [s for s in _PERSONA_DRIFT_SLOTS if _slot_captured(profile, s, asked)]
        missing = [s for s in _PERSONA_DRIFT_SLOTS if s not in captured]
        rows.append({
            "persona_id":       raw.get("persona_id") or raw.get("name_slug") or p.stem,
            "name_display":     raw.get("name_display") or "—",
            "last_seen":        raw.get("last_seen"),
            "captured_slots":   captured,
            "missing_slots":    missing,
            "completeness_pct": round(100.0 * len(captured) / len(_PERSONA_DRIFT_SLOTS), 1),
            "session_count":    len(raw.get("sessions") or []),
        })
    # Newest first by last_seen (None sorts last)
    rows.sort(key=lambda r: (r["last_seen"] or ""), reverse=True)
    rows = rows[:20]
    return {
        "personas":    rows,
        "total":       len(rows),
        "slots":       list(_PERSONA_DRIFT_SLOTS),
        "snapshot_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# Cached path resolver — re-uses profile_store's _PROFILES_DIR but we import
# lazily to avoid a circular import at module top.
def _resolve_profiles_dir() -> Path:
    from backend import profile_store
    return profile_store._PROFILES_DIR


# Lazy-evaluated singleton — instantiate on first call. We can't reference
# profile_store at module top because admin.py imports llm_health which
# may not yet have its config wired during tests.
class _LazyProfilesDir:
    def __init__(self) -> None:
        self._p: Optional[Path] = None
    def __getattr__(self, name: str):
        if self._p is None:
            self._p = _resolve_profiles_dir()
        return getattr(self._p, name)
    def exists(self) -> bool:
        if self._p is None:
            self._p = _resolve_profiles_dir()
        return self._p.exists()
    def glob(self, pat: str):
        if self._p is None:
            self._p = _resolve_profiles_dir()
        return self._p.glob(pat)


_PROFILES_DIR_FOR_DRIFT = _LazyProfilesDir()


# ---------------------------------------------------------------------------
# A5 — Audit fix #5: /api/admin/recommendation-history — last 10 policy
# recommendation events across all profiles, newest first.
# ---------------------------------------------------------------------------

@router.get("/api/admin/recommendation-history")
async def admin_recommendation_history(
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    """Return the last 10 policy-event entries across every profile,
    newest first.

    Each row: { persona_id, name_display, event_type, policy_slug, insurer,
                event_at, session_id, outcome }
    outcome: 'selected' / 'rejected' / 'shown' (passthrough from event_type;
             callers may map 'shown' → 'abandoned' if no follow-up exists,
             but we leave the raw label so the operator can decide).
    """
    _check_admin(request, x_admin_password)
    events: list[dict] = []
    if not _PROFILES_DIR_FOR_DRIFT.exists():
        return {"events": [], "total": 0,
                "snapshot_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    for p in _PROFILES_DIR_FOR_DRIFT.glob("*.json"):
        try:
            raw = json.loads(p.read_text())
        except Exception:
            continue
        profile = raw.get("profile") or {}
        persona_id = raw.get("persona_id") or raw.get("name_slug") or p.stem
        name_display = raw.get("name_display") or "—"
        for evt_type, field_name in (("shown", "shown_policies"),
                                     ("selected", "selected_policies"),
                                     ("rejected", "rejected_policies")):
            for entry in (profile.get(field_name) or []):
                events.append({
                    "persona_id":   persona_id,
                    "name_display": name_display,
                    "event_type":   evt_type,
                    "policy_slug":  entry.get("policy_slug"),
                    "insurer":      entry.get("insurer"),
                    "event_at":     entry.get("event_at"),
                    "session_id":   entry.get("session_id"),
                    "reason":       entry.get("reason"),
                    # Outcome label is the raw event_type — operator decides
                    # what 'shown without follow-up' means in their context.
                    "outcome":      evt_type,
                })
    events.sort(key=lambda e: (e["event_at"] or ""), reverse=True)
    events = events[:10]
    return {
        "events":      events,
        "total":       len(events),
        "snapshot_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
