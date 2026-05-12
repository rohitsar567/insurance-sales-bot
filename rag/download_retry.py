"""Retry failed downloads from _manifest.json with browser-grade headers + longer timeouts.

Targets the 19 failures from the first pass (mostly Star Health timeouts + ICICI Lombard 403s).

Run:
  cd "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot"
  python rag/download_retry.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "rag" / "corpus"
MANIFEST_FILE = CORPUS_DIR / "_manifest.json"
RETRY_TIMEOUT = 60  # longer than first pass; Star CDN is slow

# Browser-grade headers tuned to bypass dumb WAF rules (mostly Akamai / Cloudfront defaults).
def headers_for(url: str) -> dict:
    host = urlparse(url).netloc
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/pdf,application/x-pdf,application/octet-stream,text/html;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"https://{host}/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def retry_one(entry: dict, session: requests.Session) -> dict:
    out_path = ROOT / entry["local_path"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    status = {**entry, "ok": False, "error": None, "size_bytes": 0}

    # First do a quick GET on the insurer homepage to seed cookies
    home = f"https://{urlparse(entry['url']).netloc}/"
    try:
        session.get(home, headers=headers_for(home), timeout=15, allow_redirects=True)
    except Exception:
        pass  # not fatal

    try:
        with session.get(entry["url"], headers=headers_for(entry["url"]), stream=True,
                         timeout=RETRY_TIMEOUT, allow_redirects=True) as resp:
            if resp.status_code != 200:
                status["error"] = f"http_{resp.status_code}"
                return status
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype.startswith("text/html"):
                status["error"] = "html_response"
                return status

            with open(out_path, "wb") as f:
                bytes_written = 0
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)

            with open(out_path, "rb") as f:
                first4 = f.read(4)
            if not first4.startswith(b"%PDF"):
                out_path.unlink()
                status["error"] = "not_pdf_magic"
                return status

            if bytes_written < 50 * 1024:
                out_path.unlink()
                status["error"] = f"too_small_{bytes_written}b"
                return status

            status["ok"] = True
            status["size_bytes"] = bytes_written
            return status

    except requests.exceptions.RequestException as e:
        status["error"] = f"req_{type(e).__name__}"
    except Exception as e:
        status["error"] = f"err_{type(e).__name__}"
    return status


def main():
    manifest = json.loads(MANIFEST_FILE.read_text())
    failed = [r for r in manifest["results"] if not r["ok"]]
    print(f"Retrying {len(failed)} failed entries with browser-grade headers + 60s timeout", flush=True)

    session = requests.Session()
    new_results_by_url = {}

    fixed = 0
    for i, entry in enumerate(failed, 1):
        # Pace the requests so we don't hammer
        time.sleep(1.0)
        result = retry_one(entry, session)
        new_results_by_url[entry["url"]] = result
        if result["ok"]:
            fixed += 1
            tag = f"OK {result['size_bytes']//1024}KB"
        else:
            tag = f"STILL_FAIL {result['error']}"
        print(f"[{i:>2}/{len(failed)}] {entry['insurer_slug']:>15s} | {entry['policy_name'][:40]:<40} | {tag}", flush=True)

    # Patch the manifest in-place: replace failed entries with retry results
    by_insurer = {}
    new_results = []
    ok_count = 0
    fail_count = 0
    for r in manifest["results"]:
        if r["url"] in new_results_by_url:
            r = new_results_by_url[r["url"]]
        new_results.append(r)
        slug = r["insurer_slug"]
        by_insurer.setdefault(slug, {"ok": 0, "fail": 0})
        if r["ok"]:
            ok_count += 1
            by_insurer[slug]["ok"] += 1
        else:
            fail_count += 1
            by_insurer[slug]["fail"] += 1

    manifest["results"] = new_results
    manifest["ok"] = ok_count
    manifest["fail"] = fail_count
    manifest["by_insurer"] = by_insurer
    manifest["retried_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2))

    print(f"\nRetry rescued {fixed}/{len(failed)}. Manifest totals: {ok_count} ok / {fail_count} fail.", flush=True)
    print("Per-insurer:")
    for slug, c in sorted(by_insurer.items()):
        print(f"  {slug:>22s}: {c['ok']:>3d} ok, {c['fail']:>3d} fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
