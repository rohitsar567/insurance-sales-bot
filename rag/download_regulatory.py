"""Download regulatory PDFs from 40-data/regulatory_urls.md.

Two tricky cases handled:
  1. IRDAI URLs are behind Akamai bot-check — solved with a session that GETs
     the homepage first to collect cookies, then sends a browser-shaped UA +
     Referer when fetching the PDF.
  2. Some entries are document-detail LANDING pages (HTML), not direct PDFs —
     we parse them to find the embedded /documents/... PDF anchor.

Run from project root:
  python -m rag.download_regulatory
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "rag" / "corpus" / "regulatory"
URL_FILE = ROOT / "40-data" / "regulatory_urls.md"
MANIFEST_FILE = CORPUS_DIR / "_manifest.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def headers_for(url: str) -> dict:
    host = urlparse(url).netloc
    return {
        "User-Agent": UA,
        "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"https://{host}/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def parse_table(md: str):
    """Yield dicts of {doc_slug, doc_name, category, issuing_body, doc_type, url}."""
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 7:
            continue
        if cells[0] == "doc_slug" or set(cells[0]) <= {"-", ":"}:
            continue
        slug, name, category, issuing, year, doc_type, url, *notes = cells
        if not url.startswith("http"):
            continue
        yield {
            "doc_slug": slug,
            "doc_name": name,
            "category": category,
            "issuing_body": issuing,
            "year": year,
            "doc_type": doc_type,
            "url": url,
            "notes": notes[0] if notes else "",
        }


def warmup_session(session: requests.Session, url: str):
    """Hit the host root to collect Akamai cookies before requesting the PDF."""
    host = urlparse(url).netloc
    try:
        session.get(f"https://{host}/", headers=headers_for(f"https://{host}/"), timeout=15, allow_redirects=True)
    except Exception:
        pass


def resolve_landing_to_pdf(session: requests.Session, landing_url: str) -> str | None:
    """If URL is an HTML landing page, fetch it and find the embedded PDF link."""
    try:
        resp = session.get(landing_url, headers=headers_for(landing_url), timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            return None
        if "html" not in (resp.headers.get("Content-Type") or "").lower():
            # Already a PDF
            return None
        html = resp.text
    except Exception:
        return None

    # Look for /documents/... or .pdf hrefs
    candidates = re.findall(r'href="([^"]*\.pdf[^"]*)"', html, re.IGNORECASE)
    if not candidates:
        # Try IRDAI's documents path with no .pdf in URL but ?download=true
        candidates = re.findall(r'href="(/documents/[^"]+)"', html)
    if not candidates:
        return None
    # Prefer absolute URLs with 'documents' in the path
    abs_url = None
    for c in candidates:
        if c.startswith("http"):
            abs_url = c
            break
        if c.startswith("/"):
            host = urlparse(landing_url).netloc
            abs_url = f"https://{host}{c}"
            break
    return abs_url


def download_one(entry: dict, session: requests.Session) -> dict:
    out_path = CORPUS_DIR / f"{entry['doc_slug']}.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    status = {**entry, "local_path": str(out_path.relative_to(ROOT)), "ok": False, "error": None, "size_bytes": 0}

    if out_path.exists() and out_path.stat().st_size >= 50 * 1024:
        status["ok"] = True
        status["size_bytes"] = out_path.stat().st_size
        status["error"] = "already_downloaded"
        return status

    url = entry["url"]
    host = urlparse(url).netloc

    if "irdai.gov.in" in host:
        warmup_session(session, url)

    # If the URL is a landing page, resolve to PDF first
    if "document-detail" in url or "/web/guest/document-detail" in url:
        pdf_url = resolve_landing_to_pdf(session, url)
        if not pdf_url:
            status["error"] = "landing_page_no_pdf_link"
            return status
        url = pdf_url
        warmup_session(session, url)

    try:
        with session.get(url, headers=headers_for(url), stream=True, timeout=90, allow_redirects=True) as resp:
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
                out_path.unlink(missing_ok=True)
                status["error"] = "not_pdf_magic"
                return status

            if bytes_written < 50 * 1024:
                out_path.unlink(missing_ok=True)
                status["error"] = f"too_small_{bytes_written}b"
                return status

            status["ok"] = True
            status["size_bytes"] = bytes_written
    except requests.exceptions.RequestException as e:
        status["error"] = f"req_{type(e).__name__}"
    return status


def main():
    md = URL_FILE.read_text()
    entries = list(parse_table(md))
    print(f"Parsed {len(entries)} regulatory entries", flush=True)

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    results = []
    ok = 0
    fail = 0
    t0 = time.time()
    for i, entry in enumerate(entries, 1):
        time.sleep(1.0)  # politeness
        r = download_one(entry, session)
        results.append(r)
        if r["ok"]:
            ok += 1
            tag = f"OK {r['size_bytes']//1024}KB"
        else:
            fail += 1
            tag = f"FAIL {r['error']}"
        print(f"[{i:>2}/{len(entries)}] {entry['doc_slug']:<48s} | {tag}", flush=True)

    elapsed = time.time() - t0
    MANIFEST_FILE.write_text(json.dumps({
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": ok,
        "fail": fail,
        "elapsed_seconds": round(elapsed, 1),
        "results": results,
    }, indent=2))

    print(f"\nDone in {elapsed:.1f}s. {ok} ok / {fail} fail.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
