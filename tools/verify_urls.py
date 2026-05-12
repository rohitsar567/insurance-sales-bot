"""Verify every URL we expose to users is real (200) and reachable.

Two sets of URLs:
  1. Insurer home URLs (10) — curated from corpus discovery agent's report
  2. Policy PDF URLs — from rag/corpus/_manifest.json (already verified at
     download time, but URLs can rot, so we re-check)

Outputs:
  - eval/verified_urls.json — per-URL status, last_checked timestamp
  - prints summary table

Run:
  python tools/verify_urls.py
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "rag" / "corpus" / "_manifest.json"
OUTPUT = ROOT / "eval" / "verified_urls.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# Insurer home URLs — curated, public, primary domains.
INSURER_HOME_URLS = {
    "star-health": ("Star Health & Allied Insurance", "https://www.starhealth.in/"),
    "hdfc-ergo": ("HDFC ERGO General Insurance", "https://www.hdfcergo.com/"),
    "niva-bupa": ("Niva Bupa Health Insurance", "https://www.nivabupa.com/"),
    "care-health": ("Care Health Insurance", "https://www.careinsurance.com/"),
    "icici-lombard": ("ICICI Lombard General Insurance", "https://www.icicilombard.com/"),
    "bajaj-allianz": ("Bajaj Allianz General Insurance", "https://www.bajajallianz.com/"),
    "new-india": ("New India Assurance", "https://www.newindia.co.in/"),
    "aditya-birla": ("Aditya Birla Health Insurance", "https://www.adityabirlacapital.com/healthinsurance"),
    "tata-aig": ("Tata AIG General Insurance", "https://www.tataaig.com/"),
    "manipalcigna": ("ManipalCigna Health Insurance", "https://www.manipalcigna.com/"),
}


def check_url(url: str, timeout: float = 12.0) -> dict:
    """Do a HEAD then fall back to GET (some sites reject HEAD)."""
    try:
        r = requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code in (200, 301, 302, 303):
            return {
                "url": url, "ok": True, "status": r.status_code,
                "method": "HEAD", "final_url": r.url,
                "content_type": r.headers.get("Content-Type", ""),
            }
        # Try GET on a Range to avoid downloading full content
        r2 = requests.get(
            url, headers={**HEADERS, "Range": "bytes=0-2047"},
            timeout=timeout, allow_redirects=True, stream=True,
        )
        ok = r2.status_code in (200, 206, 301, 302, 303)
        return {
            "url": url, "ok": ok, "status": r2.status_code,
            "method": "GET-range", "final_url": r2.url,
            "content_type": r2.headers.get("Content-Type", ""),
        }
    except Exception as e:
        return {"url": url, "ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # 1) Insurer home URLs
    print("=== Verifying insurer home URLs ===")
    insurer_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            ex.submit(check_url, url): slug
            for slug, (_, url) in INSURER_HOME_URLS.items()
        }
        for f in as_completed(futures):
            slug = futures[f]
            result = f.result()
            name, url = INSURER_HOME_URLS[slug]
            insurer_results[slug] = {**result, "name": name}
            ok_tag = "OK" if result["ok"] else f"FAIL {result.get('error') or result.get('status')}"
            print(f"  {slug:>15s}: {ok_tag:<25s} {url}")

    # 2) Policy PDF URLs
    print("\n=== Verifying policy PDF URLs (sample) ===")
    policy_results: list[dict] = []
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text())
        ok_results = [r for r in manifest.get("results", []) if r.get("ok")]
        # Verify up to 30 policy URLs (avoid hammering CDNs)
        sample = ok_results[:30]
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(check_url, r["url"]): r for r in sample}
            for f in as_completed(futures):
                src = futures[f]
                result = f.result()
                result.update({
                    "policy_name": src.get("policy_name"),
                    "insurer_slug": src.get("insurer_slug"),
                    "doc_type": src.get("doc_type"),
                })
                policy_results.append(result)
                ok_tag = "OK" if result["ok"] else f"FAIL {result.get('error') or result.get('status')}"
                print(f"  {src['insurer_slug']:>15s} | {src['policy_name'][:40]:<40s} | {ok_tag}")

    insurer_ok = sum(1 for r in insurer_results.values() if r["ok"])
    policy_ok = sum(1 for r in policy_results if r["ok"])

    payload = {
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "insurer_summary": {"total": len(insurer_results), "ok": insurer_ok},
        "policy_summary": {"total": len(policy_results), "ok": policy_ok},
        "insurers": insurer_results,
        "policy_urls": policy_results,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {OUTPUT.relative_to(ROOT)}")
    print(f"Insurer URLs: {insurer_ok}/{len(insurer_results)} OK")
    print(f"Policy URLs:  {policy_ok}/{len(policy_results)} OK")


if __name__ == "__main__":
    main()
