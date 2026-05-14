"""Run the audit — walks each persona's 30-turn flow against the live API.

Design notes:
  - Resumable: completed personas land in audit_results/<run_id>/transcripts/
    as soon as they finish; if re-run, already-saved IDs are skipped.
  - Rate-limited: NIM's 40 req/min cap is the bottleneck. Each /api/chat
    triggers ~1-3 NIM calls (brain + sometimes judge + sometimes cross-check).
    We use a serial loop with adaptive sleep, plus a soft retry on 5xx.
  - Concurrent: WORKERS workers pull personas off a shared queue. The global
    NIM cap is enforced via the per-request sleep so concurrency × dispatch
    rate stays under the cap.
  - Each turn captures: reply_text, brain_used, intent, citations,
    profile_updates, faithfulness_passed, blocked, latency_ms.

Usage:
  python tools/audit/run_audit.py [--max-personas N] [--workers W] [--base URL]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE = "https://rohitsar567-insurancebot.hf.space"
ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = ROOT.parent.parent / "audit_results"


async def post_chat(
    client: httpx.AsyncClient,
    base: str,
    user_text: str,
    session_id: str,
    chat_history: list[dict],
    sema: asyncio.Semaphore,
    delay_s: float,
) -> dict[str, Any]:
    """One /api/chat call. Returns raw response dict + latency_ms.

    On HTTP 5xx, retries up to 3x with exponential backoff (3s, 8s, 18s).
    """
    payload = {
        "user_text": user_text,
        "session_id": session_id,
        "chat_history": chat_history,
        "profile": {},
        "return_audio": False,
        "tts_language_code": "en-IN",
    }
    backoff = [3.0, 8.0, 18.0]
    for attempt in range(len(backoff) + 1):
        async with sema:
            await asyncio.sleep(delay_s)  # global rate-limit token
            t0 = time.monotonic()
            try:
                resp = await client.post(f"{base}/api/chat", json=payload, timeout=60.0)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as e:
                if attempt < len(backoff):
                    await asyncio.sleep(backoff[attempt])
                    continue
                return {"error": f"{type(e).__name__}: {e}", "latency_ms": int((time.monotonic() - t0) * 1000)}
            elapsed = int((time.monotonic() - t0) * 1000)
            if resp.status_code >= 500 and attempt < len(backoff):
                await asyncio.sleep(backoff[attempt])
                continue
            if resp.status_code != 200:
                return {
                    "error": f"http_{resp.status_code}",
                    "body": resp.text[:500],
                    "latency_ms": elapsed,
                }
            data = resp.json()
            data["_latency_ms"] = elapsed
            return data
    return {"error": "retries_exhausted"}


async def run_persona(
    client: httpx.AsyncClient,
    base: str,
    persona: dict[str, Any],
    flow: list[str],
    out_dir: Path,
    sema: asyncio.Semaphore,
    delay_s: float,
    log_lock: asyncio.Lock,
) -> None:
    out_file = out_dir / f"{persona['persona_id']}.json"
    if out_file.exists():
        async with log_lock:
            print(f"  skip {persona['persona_id']} (already done)")
        return

    session_id = f"audit_{persona['persona_id']}_{uuid.uuid4().hex[:6]}"
    history: list[dict] = []
    transcript: list[dict] = []

    for turn_idx, user_text in enumerate(flow, 1):
        resp = await post_chat(client, base, user_text, session_id, history, sema, delay_s)
        if "error" in resp:
            transcript.append({
                "turn": turn_idx,
                "user_text": user_text,
                "error": resp.get("error"),
                "body": resp.get("body"),
                "latency_ms": resp.get("latency_ms") or resp.get("_latency_ms"),
            })
            # Don't break — keep going so we get partial transcript for analysis.
            continue
        reply_text = resp.get("reply_text", "")
        transcript.append({
            "turn": turn_idx,
            "user_text": user_text,
            "reply_text": reply_text,
            "brain_used": resp.get("brain_used"),
            "intent": resp.get("intent"),
            "language": resp.get("language"),
            "citations": resp.get("citations", []),
            "profile_updates": resp.get("profile_updates", {}),
            "faithfulness_passed": resp.get("faithfulness_passed"),
            "faithfulness_reasons": resp.get("faithfulness_reasons", []),
            "blocked": resp.get("blocked"),
            "latency_ms": resp.get("_latency_ms"),
            "session_id": resp.get("session_id"),
        })
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply_text})

    payload = {
        "persona": persona,
        "session_id": session_id,
        "turns": transcript,
        "completed_turns": len(transcript),
        "errors": sum(1 for t in transcript if t.get("error")),
    }
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    async with log_lock:
        ok = sum(1 for t in transcript if not t.get("error"))
        print(f"  done {persona['persona_id']:<5} | {ok}/{len(transcript)} ok | "
              f"refusals={sum(1 for t in transcript if t.get('blocked'))} | "
              f"out={out_file.name}")


async def worker(
    worker_id: int,
    queue: asyncio.Queue,
    base: str,
    out_dir: Path,
    sema: asyncio.Semaphore,
    delay_s: float,
    log_lock: asyncio.Lock,
) -> None:
    async with httpx.AsyncClient() as client:
        while True:
            item = await queue.get()
            if item is None:
                queue.task_done()
                break
            persona, flow = item
            try:
                await run_persona(client, base, persona, flow, out_dir, sema, delay_s, log_lock)
            except Exception as e:
                async with log_lock:
                    print(f"  ERR  {persona['persona_id']}: {type(e).__name__}: {e}")
            queue.task_done()


async def main_async(args: argparse.Namespace) -> None:
    personas = json.loads((ROOT / "personas.json").read_text())
    flows = json.loads((ROOT / "flows.json").read_text())

    if args.max_personas:
        personas = personas[: args.max_personas]

    run_id = args.run_id or time.strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS_ROOT / run_id / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_file = RESULTS_ROOT / run_id / "summary.json"
    print(f"=== audit run_id={run_id} ===")
    print(f"  base:       {args.base}")
    print(f"  personas:   {len(personas)}")
    print(f"  total turns: {sum(len(flows[p['persona_id']]) for p in personas)}")
    print(f"  workers:    {args.workers}")
    print(f"  delay_s:    {args.delay}  (per dispatch)")
    print(f"  out:        {out_dir}")

    sema = asyncio.Semaphore(args.workers)
    queue: asyncio.Queue = asyncio.Queue()
    for p in personas:
        await queue.put((p, flows[p["persona_id"]]))
    # Sentinel to stop workers
    for _ in range(args.workers):
        await queue.put(None)

    log_lock = asyncio.Lock()
    started = time.time()
    workers = [
        asyncio.create_task(worker(i, queue, args.base, out_dir, sema, args.delay, log_lock))
        for i in range(args.workers)
    ]
    await queue.join()
    await asyncio.gather(*workers)
    elapsed = time.time() - started

    completed = sorted(out_dir.glob("*.json"))
    summary_file.write_text(json.dumps({
        "run_id": run_id,
        "base": args.base,
        "personas_requested": len(personas),
        "personas_completed": len(completed),
        "elapsed_seconds": int(elapsed),
        "workers": args.workers,
        "delay_s": args.delay,
        "transcripts_dir": str(out_dir),
    }, indent=2))
    print(f"\n=== done in {elapsed:.0f}s ===")
    print(f"  completed: {len(completed)} / {len(personas)} personas")
    print(f"  summary:   {summary_file}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--max-personas", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4, help="concurrent personas in flight")
    parser.add_argument("--delay", type=float, default=1.8, help="seconds between dispatches per worker")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
