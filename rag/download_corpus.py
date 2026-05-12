"""Bulk-download every PDF URL from data/corpus_urls.md.

Per-URL flow:
  1. HEAD-check (or fall back to Range GET) — confirm Content-Type is PDF-ish
  2. Stream-download to rag/corpus/<insurer_slug>/<safe_filename>.pdf
  3. Skip if file already exists (idempotent re-runs)
  4. Verify file size > 50 KB (PDF stubs / error pages are usually smaller)

Writes a manifest at rag/corpus/_manifest.json with every successful download.
Prints a per-row status line so progress is visible.

Run:
  cd "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot"
  python rag/download_corpus.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Iterator

import requests

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "rag" / "corpus"
URL_FILE = ROOT / "data" / "corpus_urls.md"
MANIFEST_FILE = CORPUS_DIR / "_manifest.json"

# Generous headers — some insurer CDNs reject default Python UA
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "application/pdf,*/*",
}

MIN_FILE_SIZE_BYTES = 50 * 1024  # 50 KB — anything smaller is suspect
HEAD_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 90


def safe_filename(policy_name: str, doc_type: str) -> str:
    """Slugify a policy name into a safe filename."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", policy_name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return f"{slug}__{doc_type}.pdf"


def parse_corpus_table(md_text: str) -> Iterator[dict]:
    """Yield dicts of {insurer_slug, insurer_name, policy_name, doc_type, url, notes}."""
    seen = set()
    for line in md_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue
        if cells[0] == "insurer_slug" or set(cells[0]) <= {"-", ":"}:
            continue  # header / separator row
        slug, name, policy, doc_type, url, notes = cells[:6]
        if not url.startswith("http"):
            continue
        key = (slug, policy, doc_type, url)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "insurer_slug": slug,
            "insurer_name": name,
            "policy_name": policy,
            "doc_type": doc_type,
            "url": url,
            "notes": notes,
        }


def is_pdf_response(resp: requests.Response) -> bool:
    """Heuristic: trust Content-Type when it's clear; fall back to magic bytes."""
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "pdf" in ctype:
        return True
    if ctype.startswith("text/html"):
        return False
    # Some servers send octet-stream — check first 4 bytes
    try:
        first = resp.raw.read(4, decode_content=False)
        return first.startswith(b"%PDF")
    except Exception:
        return False


def download_one(entry: dict) -> dict:
    """Returns a status dict per entry."""
    insurer_dir = CORPUS_DIR / entry["insurer_slug"]
    insurer_dir.mkdir(parents=True, exist_ok=True)

    fname = safe_filename(entry["policy_name"], entry["doc_type"])
    out_path = insurer_dir / fname

    status = {**entry, "local_path": str(out_path.relative_to(ROOT)), "size_bytes": 0, "ok": False, "error": None}

    if out_path.exists() and out_path.stat().st_size >= MIN_FILE_SIZE_BYTES:
        status["ok"] = True
        status["size_bytes"] = out_path.stat().st_size
        status["error"] = "already_downloaded"
        return status

    try:
        with requests.get(
            entry["url"],
            headers=HEADERS,
            stream=True,
            timeout=DOWNLOAD_TIMEOUT,
            allow_redirects=True,
        ) as resp:
            if resp.status_code != 200:
                status["error"] = f"http_{resp.status_code}"
                return status

            # Peek at first chunk to confirm PDF
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if content_type.startswith("text/html"):
                status["error"] = "html_response"
                return status

            with open(out_path, "wb") as f:
                bytes_written = 0
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    bytes_written += len(chunk)

            # Post-download checks
            with open(out_path, "rb") as f:
                first4 = f.read(4)
            if not first4.startswith(b"%PDF"):
                out_path.unlink()
                status["error"] = "not_pdf_magic"
                return status

            if bytes_written < MIN_FILE_SIZE_BYTES:
                out_path.unlink()
                status["error"] = f"too_small_{bytes_written}b"
                return status

            status["ok"] = True
            status["size_bytes"] = bytes_written

    except requests.exceptions.RequestException as e:
        status["error"] = f"req_{type(e).__name__}"
    except Exception as e:
        status["error"] = f"err_{type(e).__name__}_{e}"

    return status


def main():
    md = URL_FILE.read_text()
    entries = list(parse_corpus_table(md))
    print(f"Parsed {len(entries)} unique URLs from corpus_urls.md", flush=True)

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    ok_count = 0
    fail_count = 0
    by_insurer = {}

    t0 = time.time()
    for i, entry in enumerate(entries, 1):
        status = download_one(entry)
        results.append(status)
        slug = entry["insurer_slug"]
        by_insurer.setdefault(slug, {"ok": 0, "fail": 0})
        if status["ok"]:
            ok_count += 1
            by_insurer[slug]["ok"] += 1
            tag = f"OK {status['size_bytes']//1024}KB"
        else:
            fail_count += 1
            by_insurer[slug]["fail"] += 1
            tag = f"FAIL {status['error']}"
        print(f"[{i:>2}/{len(entries)}] {slug:>15s} | {entry['policy_name'][:40]:<40} | {tag}", flush=True)

    elapsed = time.time() - t0
    MANIFEST_FILE.write_text(json.dumps({
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_entries": len(entries),
        "ok": ok_count,
        "fail": fail_count,
        "elapsed_seconds": round(elapsed, 1),
        "by_insurer": by_insurer,
        "results": results,
    }, indent=2))

    print(f"\nDone in {elapsed:.1f}s. {ok_count} ok / {fail_count} fail.", flush=True)
    print(f"Manifest: {MANIFEST_FILE.relative_to(ROOT)}", flush=True)
    print("Per-insurer:")
    for slug, c in sorted(by_insurer.items()):
        print(f"  {slug:>20s}: {c['ok']:>3d} ok, {c['fail']:>3d} fail")

    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
