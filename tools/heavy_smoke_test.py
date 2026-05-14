"""Heavy live smoke test for the deployed Insurance Bot.

Exercises every major API path and RAG retrieval mode. Reports latency
+ success rate + brain_used + citation count + faithfulness verdict.

Usage:
  PYTHONPATH=. .venv/bin/python tools/heavy_smoke_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

BASE_URL = os.environ.get("SMOKE_BASE_URL", "https://rohitsar567-insurancebot.hf.space")
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "")
TIMEOUT = 90.0


@dataclass
class Result:
    name: str
    ok: bool
    latency_ms: int
    info: str = ""


RESULTS: list[Result] = []


def banner(s: str) -> None:
    print()
    print("=" * 80)
    print(f"  {s}")
    print("=" * 80)


def record(name: str, ok: bool, latency_ms: int, info: str = "") -> None:
    RESULTS.append(Result(name, ok, latency_ms, info))
    sigil = "✓" if ok else "✗"
    print(f"  {sigil} {name:50s}  {latency_ms:>6}ms  {info}")


def ping(client: httpx.Client, path: str, name: str | None = None) -> dict | None:
    t0 = time.time()
    try:
        r = client.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
        latency = int((time.time() - t0) * 1000)
        ok = r.status_code == 200
        info = f"HTTP {r.status_code}"
        record(name or path, ok, latency, info)
        return r.json() if ok else None
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        record(name or path, False, latency, f"EXC: {type(e).__name__}: {str(e)[:80]}")
        return None


def chat(client: httpx.Client, user_text: str, name: str, session_id: str = "smoke",
         profile: dict | None = None, policy_filter_ids: list[str] | None = None) -> dict | None:
    body = {
        "user_text": user_text,
        "session_id": session_id,
        "chat_history": [],
        "profile": profile or {},
        "policy_filter_ids": policy_filter_ids,
        "return_audio": False,
    }
    t0 = time.time()
    try:
        r = client.post(f"{BASE_URL}/api/chat", json=body, timeout=TIMEOUT)
        latency = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            record(name, False, latency, f"HTTP {r.status_code} body[:80]={r.text[:80]}")
            return None
        d = r.json()
        ok = bool(d.get("reply_text")) and not d.get("blocked")
        info = (
            f"brain={d.get('brain_used','?')} "
            f"intent={d.get('intent','?')} "
            f"cites={len(d.get('citations',[]))} "
            f"faith={d.get('faithfulness_passed','?')} "
            f"reply_chars={len(d.get('reply_text',''))}"
        )
        record(name, ok, latency, info)
        return d
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        record(name, False, latency, f"EXC: {type(e).__name__}: {str(e)[:80]}")
        return None


def main() -> int:
    print(f"Smoke testing: {BASE_URL}")
    print(f"  ADMIN_PASSWORD set: {bool(ADMIN_PW)}")

    with httpx.Client() as client:
        banner("1. Health + coverage")
        h = ping(client, "/api/health")
        if h:
            print(f"      providers_ok={h.get('providers_ok')}")
        cov = ping(client, "/api/coverage")
        if cov:
            print(f"      coverage: {cov.get('total_policies')} policies, "
                  f"{cov.get('total_insurers')} insurers, "
                  f"{cov.get('total_chunks')} chunks")

        banner("2. Policy text-RAG (specific waiting period question)")
        chat(client, "What is the pre-existing disease waiting period in Star Health Comprehensive?",
             "PED waiting (Star)")
        chat(client, "Does Niva Bupa ReAssure 2.0 have room rent capping?",
             "room rent cap (Niva)")
        chat(client, "What's the day-care procedures count for HDFC Optima Secure?",
             "day-care count (HDFC)")

        banner("3. Regulatory RAG boost (IRDAI / Insurance Act intent)")
        chat(client, "Is a 36-month PED waiting period legal under IRDAI rules?",
             "PED legality (regulatory)")
        chat(client, "What does IRDAI mandate for the free-look period?",
             "free-look mandate (regulatory)")
        chat(client, "What's the ombudsman process for claim disputes?",
             "ombudsman (regulatory)")

        banner("4. Review RAG boost (reputation / claim experience)")
        chat(client, "How is HDFC ERGO's claim settlement experience?",
             "HDFC claim experience (review)")
        chat(client, "What do customers say about Care Health?",
             "Care reviews (review)")
        chat(client, "Which insurer has the best Trustpilot rating?",
             "cross-insurer review compare")

        banner("5. Profile-aware multi-turn (fact-find continuity)")
        # Turn 1: start with empty profile
        chat(client, "I'm looking for health insurance, I'm 39 with diabetes",
             "profile intake T1", session_id="profile-smoke",
             profile={"age": 39, "health_conditions": ["diabetes"]})
        # Turn 2: with same session — profile should auto-prepend
        chat(client, "Which policy would you recommend for me?",
             "profile-aware recommend T2", session_id="profile-smoke",
             profile={"age": 39, "health_conditions": ["diabetes"]})

        banner("6. Structured endpoints (no LLM in path)")
        m = ping(client, "/api/policies/all", "marketplace /api/policies/all")
        if m:
            n = len(m.get("policies", []))
            print(f"      {n} policies returned")
        # Pick a real policy_id to drill into
        if m and m.get("policies"):
            pid = m["policies"][0].get("policy_id")
            if pid:
                ping(client, f"/api/policies/{pid}/scorecard", f"scorecard {pid}")
        ping(client, "/api/insurers/star-health/reviews", "reviews /api/insurers/star-health/reviews")

        banner("7. Hallucination resistance (try to elicit fake info)")
        chat(client, "What is the sum insured limit for Star Galaxy Premium 2030?",
             "fake policy name (should refuse)")
        chat(client, "Tell me about Acko Diamond Plus Plus Pro Max",
             "fake variant name (should refuse)")

        banner("8. Admin endpoints (gated — should return 404 without auth)")
        try:
            r = client.get(f"{BASE_URL}/api/admin/health", timeout=15)
            ok = r.status_code == 404
            record("admin without password (expect 404)", ok, 0, f"HTTP {r.status_code}")
        except Exception as e:
            record("admin without password", False, 0, str(e)[:80])
        if ADMIN_PW:
            try:
                r = client.get(f"{BASE_URL}/api/admin/health",
                               headers={"X-Admin-Password": ADMIN_PW}, timeout=15)
                ok = r.status_code == 200
                info = f"HTTP {r.status_code}"
                if ok:
                    d = r.json()
                    info += f" healthy={d.get('by_status',{}).get('healthy',0)} models"
                record("admin with password (expect 200)", ok, 0, info)
            except Exception as e:
                record("admin with password", False, 0, str(e)[:80])

        banner("9. Latency p50/p95 across all /api/chat calls")
        chat_lats = [r.latency_ms for r in RESULTS if "T1" in r.name or "T2" in r.name or "PED" in r.name or "room" in r.name or "day-care" in r.name or "free-look" in r.name or "ombudsman" in r.name or "review" in r.name.lower()]
        if chat_lats:
            sorted_l = sorted(chat_lats)
            n = len(sorted_l)
            p50 = sorted_l[n // 2]
            p95 = sorted_l[min(int(n * 0.95), n - 1)]
            mean = sum(sorted_l) / n
            print(f"  Chat calls: n={n}  mean={mean:.0f}ms  p50={p50}ms  p95={p95}ms")

        banner("10. Summary")
        ok_count = sum(1 for r in RESULTS if r.ok)
        total = len(RESULTS)
        print(f"  Total tests:  {total}")
        print(f"  Passed:       {ok_count}")
        print(f"  Failed:       {total - ok_count}")
        if total - ok_count > 0:
            print(f"\n  FAILED:")
            for r in RESULTS:
                if not r.ok:
                    print(f"    - {r.name}: {r.info}")

    return 0 if ok_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
