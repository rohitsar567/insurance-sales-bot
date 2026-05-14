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
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

from backend import llm_health

router = APIRouter()


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
