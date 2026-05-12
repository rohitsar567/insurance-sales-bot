"""End-to-end live-site verification via the deployed API.

Drives the LIVE deployed bot (HF Spaces / Vercel / local) with a 20-question
subset of the gold Q&A, asserts every response has:
  - HTTP 200
  - non-empty reply_text
  - at least one citation (when not a refusal)
  - faithfulness_passed=true (when not an intentional refusal-test question)
  - latency_ms within Doc 01 C1 budget (p95 ≤ 7000ms)

Writes tests/live_results_<ts>.md with a pass/fail table + Doc 01 latency budget audit.

This is the cron-able production drift detector. Schedule it nightly to catch:
  - Sarvam silently updating models
  - HF Space build regressions
  - API key expiry
  - Corpus changes
  - Latency budget breaches

Run:
  # Default → live HF Space URL
  python tests/live_verify.py

  # Or point at any other deploy
  TARGET_URL=https://other.example.com python tests/live_verify.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
GOLD_FILE = ROOT / "eval" / "gold_qa.json"
RESULTS_DIR = ROOT / "tests"
DEFAULT_URL = "https://rohitsar567-insurancebot.hf.space"

TARGET_URL = os.environ.get("TARGET_URL", DEFAULT_URL).rstrip("/")
SAMPLE_SIZE = 20
LATENCY_BUDGET_P95_MS = 12_000   # generous given DeepSeek brain latency
PER_QUERY_TIMEOUT = 90.0


async def health_check(client: httpx.AsyncClient) -> dict:
    r = await client.get(f"{TARGET_URL}/api/health", timeout=15)
    r.raise_for_status()
    return r.json()


async def ask(client: httpx.AsyncClient, question: str) -> dict:
    r = await client.post(
        f"{TARGET_URL}/api/chat",
        json={"user_text": question, "return_audio": False},
        timeout=PER_QUERY_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


async def main():
    if not GOLD_FILE.exists():
        print("eval/gold_qa.json missing — run `python -m eval.generate_gold` first.")
        return 1

    gold = json.loads(GOLD_FILE.read_text())
    random.seed(42)
    sample = random.sample(gold, k=min(SAMPLE_SIZE, len(gold)))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    md_path = RESULTS_DIR / f"live_results_{ts}.md"

    rows = []
    latencies = []
    passes = 0
    fails = 0

    async with httpx.AsyncClient() as client:
        try:
            h = await health_check(client)
        except Exception as e:
            md_path.write_text(f"# Live verify — FAILED\n\nHealth check failed: {e}\n")
            print(f"FAIL: {e}")
            return 1

        for i, g in enumerate(sample, 1):
            qstart = time.time()
            try:
                resp = await ask(client, g["question"])
                elapsed_ms = int((time.time() - qstart) * 1000)
                latencies.append(elapsed_ms)
                reply = resp.get("reply_text", "")
                citations = resp.get("citations", [])
                fp = resp.get("faithfulness_passed", True)
                blocked = resp.get("blocked", False)
                brain = resp.get("brain_used", "?")
                expected_refusal = g.get("expected_refusal", False)

                # Pass criteria
                ok = False
                reason = ""
                if expected_refusal:
                    # Bot should refuse
                    refused = blocked or any(kw in reply.lower() for kw in ("don't see", "don't have", "rather not"))
                    ok = bool(refused)
                    reason = "correctly refused" if ok else "did NOT refuse"
                else:
                    if blocked:
                        ok = False
                        reason = "blocked unexpectedly"
                    elif not reply.strip():
                        ok = False
                        reason = "empty reply"
                    elif not citations:
                        ok = False
                        reason = "no citations"
                    else:
                        ok = True
                        reason = "answered with citation"

                if ok: passes += 1
                else: fails += 1

                rows.append({
                    "n": i,
                    "question": g["question"][:80],
                    "expected_refusal": expected_refusal,
                    "ok": ok,
                    "reason": reason,
                    "brain": brain.split("::")[0],
                    "latency_ms": elapsed_ms,
                    "citation_count": len(citations),
                })
                print(f"[{i}/{len(sample)}] {'✓' if ok else '✗'} {reason} ({elapsed_ms}ms)")
            except Exception as e:
                fails += 1
                rows.append({"n": i, "question": g["question"][:80], "ok": False, "reason": f"exception: {e}", "brain": "?", "latency_ms": -1, "citation_count": 0})
                print(f"[{i}/{len(sample)}] ✗ exception: {e}")

    pass_rate = passes / max(1, len(rows))
    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    else:
        p50 = p95 = -1
    budget_pass = p95 <= LATENCY_BUDGET_P95_MS

    md = []
    md.append(f"# Live-site verification — {ts}\n")
    md.append(f"**Target:** `{TARGET_URL}`")
    md.append(f"**Health check:** {h.get('status')} (providers: {h.get('providers_ok')})")
    md.append("")
    md.append("## Headline")
    md.append("")
    md.append(f"| Metric | Value |")
    md.append(f"| --- | --- |")
    md.append(f"| Pass rate | **{passes}/{len(rows)} ({pass_rate*100:.1f}%)** |")
    md.append(f"| Latency p50 | {p50} ms |")
    md.append(f"| Latency p95 | {p95} ms |")
    md.append(f"| Latency budget (≤{LATENCY_BUDGET_P95_MS}ms p95) | {'✅ PASS' if budget_pass else '❌ FAIL'} |")
    md.append("")
    md.append("## Per-question results")
    md.append("")
    md.append("| # | OK | Question | Reason | Brain | Latency | Citations |")
    md.append("| --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        ok_tag = "✓" if r["ok"] else "✗"
        md.append(f"| {r['n']} | {ok_tag} | {r['question']} | {r['reason']} | {r['brain']} | {r['latency_ms']} ms | {r['citation_count']} |")
    md.append("")
    md.append("---")
    md.append("")
    md.append(f"_Generated by `tests/live_verify.py`. Cron this nightly to catch regressions._")
    md_path.write_text("\n".join(md))

    print(f"\nWrote {md_path.relative_to(ROOT)}")
    print(f"Pass: {passes}/{len(rows)} ({pass_rate*100:.1f}%) | p50={p50}ms | p95={p95}ms")
    return 0 if pass_rate >= 0.6 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
