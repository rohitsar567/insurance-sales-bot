"""Admin endpoints — IP+password gated. NEVER exposed to ordinary users.

The four endpoints:
  GET  /api/admin/health        — full LLM health snapshot (every model)
  POST /api/admin/probe         — force a fresh probe of all models now
  GET  /api/admin/chain         — current brain/fast/judge chain order
  POST /api/admin/chain         — reorder a chain (promote a model to primary)

Access gates (BOTH must pass):
  1. Client IP must match ADMIN_IP_ALLOWLIST env (comma-separated)
  2. Request must include `X-Admin-Password` header matching ADMIN_PASSWORD env

If either gate fails, returns 404 (not 401) — the endpoints DO NOT exist
for unauthorized callers. Even the existence of /api/admin/* is hidden.

Setup:
  Add to Space secrets (Settings → Variables and secrets):
    ADMIN_PASSWORD     = your-strong-password-here
    ADMIN_IP_ALLOWLIST = your.public.ip.v4,your.other.ip
  Find your IP at https://api.ipify.org/
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

router = APIRouter()


# Cap how many tail lines of 40-data/llm_usage.jsonl we hold in memory while
# computing per-role stats. 1000 lines @ ~150B each = ~150 KB peak — bounded.
USAGE_TAIL_LINES = 1000


def _ip_allowed(client_ip: str) -> bool:
    """Allowlist check. Empty/unset allowlist => deny everyone."""
    allowed = os.environ.get("ADMIN_IP_ALLOWLIST", "").strip()
    if not allowed:
        return False
    return client_ip in {ip.strip() for ip in allowed.split(",") if ip.strip()}


def _password_ok(supplied: Optional[str]) -> bool:
    expected = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not expected:
        return False  # no password configured => deny
    return supplied is not None and supplied == expected


def _check_admin(request: Request, password: Optional[str]) -> None:
    """Both gates must pass. Returns 404 (not 401) on failure to hide existence."""
    # Honor X-Forwarded-For for HF Space proxy headers (HF Spaces sets this)
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if not client_ip:
        client_ip = request.client.host if request.client else ""
    if not _ip_allowed(client_ip) or not _password_ok(password):
        raise HTTPException(status_code=404, detail="Not Found")


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
    from backend.providers.nvidia_nim_llm import (
        BRAIN_CHAIN, FAST_BRAIN_CHAIN, JUDGE_CHAIN,
    )
    return {
        "brain":      BRAIN_CHAIN,
        "fast_brain": FAST_BRAIN_CHAIN,
        "judge":      JUDGE_CHAIN,
    }


class ChainReorderRequest(BaseModel):
    role: str   # 'brain' | 'fast_brain' | 'judge'
    order: list[str]   # new ordering of model ids


@router.post("/api/admin/chain")
async def admin_chain_set(
    body: ChainReorderRequest,
    request: Request,
    x_admin_password: Optional[str] = Header(default=None, alias="X-Admin-Password"),
):
    _check_admin(request, x_admin_password)
    if body.role not in ("brain", "fast_brain", "judge"):
        raise HTTPException(status_code=400, detail="role must be brain | fast_brain | judge")

    # Mutate the in-memory chain
    from backend.providers import nvidia_nim_llm as nim
    name = {"brain": "BRAIN_CHAIN", "fast_brain": "FAST_BRAIN_CHAIN", "judge": "JUDGE_CHAIN"}[body.role]
    setattr(nim, name, list(body.order))

    # Persist for next process restart — write to 40-data/admin_overrides.json
    override_path = Path(__file__).resolve().parent.parent / "40-data" / "admin_overrides.json"
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

    # Live (post-override) chains — read attributes off the module so any
    # admin reorder applied in the current process is reflected immediately.
    from backend.providers import nvidia_nim_llm as nim
    chains = {
        "brain":      list(getattr(nim, "BRAIN_CHAIN", [])),
        "fast_brain": list(getattr(nim, "FAST_BRAIN_CHAIN", [])),
        "judge":      list(getattr(nim, "JUDGE_CHAIN", [])),
    }

    usage_path = Path(__file__).resolve().parent.parent / "40-data" / "llm_usage.jsonl"
    rows = _tail_jsonl(usage_path, USAGE_TAIL_LINES)
    health_state = llm_health.load()  # {model: ModelHealth} dict

    return {
        role: _stat_block_for_role(rows, role, chains[role], health_state)
        for role in ("brain", "fast_brain", "judge")
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
    """
    usage_path = _REPO_ROOT / "40-data" / "llm_usage.jsonl"
    rows = _tail_jsonl(usage_path, USAGE_TAIL_LINES)
    if not rows:
        return None

    agg: dict[str, dict] = {}
    for r in rows:
        role = r.get("role")
        if not role:
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
