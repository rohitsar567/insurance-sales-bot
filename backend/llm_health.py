"""Live NIM model health monitor — OpenRouter-style availability filter.

Probes every model in BRAIN_CHAIN + FAST_BRAIN_CHAIN + JUDGE_CHAIN periodically
with a tiny ping ("reply 'ok'"). Records:
  - status:           healthy / degraded / down
  - last_success_at:  timestamp of last 2xx response
  - last_failure_at:  timestamp of last failure (timeout / 5xx / parse)
  - latency_ms:       last successful response latency
  - consecutive_fail: counter (3+ => marked down)
  - tested_at:        when this row was last refreshed

Persistence: data/llm_health.json (atomic write via temp+rename).

Consumers:
  - NimChainLLM.chat() filters the chain to status != 'down' before iterating.
  - GET /api/health/llms returns the current state for the frontend.

Operating schedule:
  - background asyncio task in main.py startup, ticks every PROBE_INTERVAL_SEC.
  - on-demand refresh via probe_all() (e.g. when NimChainLLM exhausts the chain).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent
HEALTH_FILE = ROOT / "data" / "llm_health.json"
HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)

PROBE_INTERVAL_SEC = 300          # ping each model every 5 min
PROBE_TIMEOUT_SEC = 12            # per-probe HTTP timeout
DOWN_AFTER_CONSECUTIVE_FAILS = 3  # 3 fails in a row = mark down

NIM_BASE = "https://integrate.api.nvidia.com/v1/chat/completions"


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


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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


def load() -> dict[str, ModelHealth]:
    if not HEALTH_FILE.exists():
        return {}
    try:
        raw = json.loads(HEALTH_FILE.read_text())
        return {k: ModelHealth(**v) for k, v in raw.get("models", {}).items()}
    except Exception:
        return {}


def save(state: dict[str, ModelHealth]) -> None:
    """Atomic write — temp file then rename so concurrent readers never see partial."""
    out = {
        "updated_at": _now_iso(),
        "models": {k: asdict(v) for k, v in state.items()},
    }
    tmp = HEALTH_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2) + "\n")
    tmp.replace(HEALTH_FILE)


async def probe_one(client: httpx.AsyncClient, model: str, api_key: str) -> tuple[bool, str, Optional[int]]:
    """Returns (ok, error_msg, latency_ms)."""
    t0 = time.time()
    try:
        r = await client.post(
            NIM_BASE,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
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


async def probe_all() -> dict[str, ModelHealth]:
    """One-shot probe of every known model. Updates persisted state + returns it."""
    state = load()
    models = _all_known_models()
    api_key = os.environ.get("NVIDIA_NIM_API_KEY", "")
    if not api_key:
        return state

    async with httpx.AsyncClient() as client:
        # Probe all in parallel — they hit different NIM pools so concurrency is fine
        results = await asyncio.gather(
            *[probe_one(client, m, api_key) for m in models],
            return_exceptions=True,
        )

    for model, result in zip(models, results):
        if isinstance(result, Exception):
            ok, err, latency = False, f"exc: {type(result).__name__}", None
        else:
            ok, err, latency = result

        h = state.get(model, ModelHealth(model=model))
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
        state[model] = h

    save(state)
    return state


def filter_chain(chain: list[str]) -> list[str]:
    """Return the chain with 'down' models removed. Always preserves order +
    keeps at least one model so callers never get an empty chain."""
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
