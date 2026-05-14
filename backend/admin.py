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
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

from backend import llm_health

router = APIRouter()


# Cap how many tail lines of data/llm_usage.jsonl we hold in memory while
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

    # Persist for next process restart — write to data/admin_overrides.json
    override_path = Path(__file__).resolve().parent.parent / "data" / "admin_overrides.json"
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

    Backward-compatible: if data/llm_usage.jsonl doesn't exist yet, returns
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

    usage_path = Path(__file__).resolve().parent.parent / "data" / "llm_usage.jsonl"
    rows = _tail_jsonl(usage_path, USAGE_TAIL_LINES)
    health_state = llm_health.load()  # {model: ModelHealth} dict

    return {
        role: _stat_block_for_role(rows, role, chains[role], health_state)
        for role in ("brain", "fast_brain", "judge")
    }
