"""HEAD-check every URL we surface in 40-data/reviews/*.json.

We commit hard to the 'no fake / no broken URLs' invariant. This script:
  1. Walks every reviews JSON and harvests every URL field
  2. HEAD-checks each (with GET-Range fallback for sites that block HEAD)
  3. Annotates each URL in the source JSON with {verified: bool, status: int,
     last_checked: ts}
  4. Writes a global summary to eval/reviews_url_verification.json
  5. Prints a leaderboard of broken URLs per insurer

Run:
  python tools/verify_review_urls.py [--annotate]

  --annotate writes verification flags back into 40-data/reviews/*.json.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REVIEWS_DIR = ROOT / "40-data" / "reviews"
OUTPUT = ROOT / "eval" / "reviews_url_verification.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
    "Accept-Language": "en-IN,en;q=0.9",
}


def harvest_urls_from(obj, ctx: list, out: list[tuple[str, str]]):
    """Recursively walk a dict/list, emitting (json_path, url) pairs for every
    string field whose value looks like an http(s) URL."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            harvest_urls_from(v, ctx + [str(k)], out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            harvest_urls_from(v, ctx + [f"[{i}]"], out)
    elif isinstance(obj, str):
        s = obj.strip()
        if s.startswith("http://") or s.startswith("https://"):
            out.append((".".join(ctx), s))


def check(url: str) -> dict:
    """HEAD → fallback GET Range. Many CDNs reject HEAD. Trust 2xx/3xx as OK."""
    try:
        r = requests.head(url, headers=HEADERS, timeout=12, allow_redirects=True)
        if r.status_code in (200, 301, 302, 303, 304):
            return {"url": url, "ok": True, "status": r.status_code, "method": "HEAD", "final_url": r.url}
        # Fallback to a tiny ranged GET
        r2 = requests.get(
            url,
            headers={**HEADERS, "Range": "bytes=0-2047"},
            timeout=12, allow_redirects=True, stream=True,
        )
        ok = r2.status_code in (200, 206, 301, 302, 303, 304)
        return {"url": url, "ok": ok, "status": r2.status_code, "method": "GET-range", "final_url": r2.url}
    except requests.exceptions.RequestException as e:
        return {"url": url, "ok": False, "error": f"{type(e).__name__}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotate", action="store_true", help="Write verified flags back into source JSON")
    args = parser.parse_args()

    files = sorted(REVIEWS_DIR.glob("*.json"))
    print(f"Walking {len(files)} reviews files...")
    per_insurer = {}
    all_url_checks = []

    for f in files:
        if f.name == "INDEX.md":
            continue
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            print(f"  skip {f.name}: {e}")
            continue
        slug = data.get("insurer_slug", f.stem)
        urls: list[tuple[str, str]] = []
        harvest_urls_from(data, [], urls)
        # Dedupe by URL preserving first path
        seen = set()
        uniq = []
        for p, u in urls:
            if u in seen: continue
            seen.add(u); uniq.append((p, u))
        per_insurer[slug] = {"file": str(f.relative_to(ROOT)), "count": len(uniq), "checks": [], "_raw": data, "_uniq_urls": uniq}

    # Parallel check
    all_urls = [(slug, p, u) for slug, v in per_insurer.items() for (p, u) in v["_uniq_urls"]]
    print(f"Checking {len(all_urls)} unique URLs in parallel...")
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(check, url): url for (_, _, url) in all_urls}
        for fut in as_completed(futures):
            r = fut.result()
            results[r["url"]] = r

    # Aggregate per insurer
    summary = {"verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "per_insurer": {}, "total_urls": len(results),
               "broken_urls": [], "ok_urls": 0}
    for slug, info in per_insurer.items():
        ok = 0; bad = []
        for p, u in info["_uniq_urls"]:
            r = results.get(u, {"ok": False, "error": "no_result"})
            info["checks"].append({"path": p, "url": u, **r})
            if r.get("ok"): ok += 1
            else: bad.append({"path": p, "url": u, "status": r.get("status"), "error": r.get("error")})
        summary["per_insurer"][slug] = {"total": info["count"], "ok": ok, "broken": len(info["count"] - ok if False else info["checks"]) - ok, "broken_list": bad}
        summary["ok_urls"] += ok
        for b in bad:
            summary["broken_urls"].append({"insurer": slug, **b})

        if args.annotate:
            # Walk the raw JSON, set _verified flag on URL fields
            # Simpler approach: just write a `_url_verification` block alongside the data
            info["_raw"]["_url_verification"] = {
                "verified_at": summary["verified_at"],
                "total_urls": info["count"],
                "ok": ok,
                "broken_count": len(bad),
                "broken_urls": [b["url"] for b in bad],
            }
            f = ROOT / info["file"]
            f.write_text(json.dumps(info["_raw"], indent=2))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, indent=2))

    print(f"\n=== URL verification summary ===")
    print(f"Total URLs:  {summary['total_urls']}")
    print(f"OK:          {summary['ok_urls']}")
    print(f"Broken:      {len(summary['broken_urls'])}")
    print()
    print("Per insurer:")
    for slug, s in summary["per_insurer"].items():
        bad = s["total"] - s["ok"]
        tag = "✓" if bad == 0 else "✗"
        print(f"  {tag} {slug:>15s}: {s['ok']}/{s['total']} OK ({bad} broken)")
    if summary["broken_urls"]:
        print(f"\nBroken URLs:")
        for b in summary["broken_urls"]:
            err = b.get("error") or f"HTTP {b.get('status')}"
            print(f"  [{b['insurer']}] {b['path']:<40} {err:<25} {b['url'][:80]}")
    if args.annotate:
        print(f"\nWrote verification flags to 40-data/reviews/*.json")
    print(f"Summary: {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
