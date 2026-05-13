"""Weekly PDF freshness check + auto-fix.

For each policy PDF URL in data/corpus_urls.md:
  1. Fetch HTTP ETag + Last-Modified
  2. Compare against tools/.pdf_etag_state.json
  3. If changed: download the new PDF, re-run rag/ingest for that policy_id,
     save the new ETag, and notify the user that the corpus changed.

The state file is the single source of truth for "last seen version of every
policy PDF". It lives in tools/ so it's checked into git — that means we can
diff the state file across commits to audit "what changed in the corpus
between launch and today".

A fresh ingest after a PDF change is the auto-fix. If ingest fails, the URL
is appended to MUST_FIX.md.

Exit codes:
  0 — nothing changed, OR all changed PDFs were re-ingested successfully
  1 — at least one PDF changed but re-ingest failed
  2 — script-level error
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = Path.home() / "Library" / "Logs" / "insurance-bot"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "pdf_etags.log"
STATE_FILE = PROJECT_ROOT / "tools" / ".pdf_etag_state.json"
MUST_FIX = PROJECT_ROOT / "MUST_FIX.md"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def notify(title: str, body: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{title}"'],
            check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pass


def parse_corpus_urls() -> list[dict]:
    """Return [{insurer_slug, policy_name, url}, ...] from the markdown table."""
    md = (PROJECT_ROOT / "data" / "corpus_urls.md").read_text()
    out: list[dict] = []
    for line in md.splitlines():
        if not line.startswith("|") or line.startswith("| insurer"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 5 or not parts[4].startswith("http"):
            continue
        out.append(
            {
                "insurer_slug": parts[0],
                "policy_name": parts[2],
                "url": parts[4],
            }
        )
    return out


def load_state() -> dict[str, dict]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict[str, dict]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def head_meta(url: str, client: httpx.Client) -> dict:
    try:
        r = client.head(url, headers=HEADERS, follow_redirects=True)
        if r.status_code in (403, 405, 501):
            r = client.get(
                url,
                headers={**HEADERS, "Range": "bytes=0-1023"},
                follow_redirects=True,
            )
        return {
            "status": r.status_code,
            "etag": r.headers.get("ETag", ""),
            "last_modified": r.headers.get("Last-Modified", ""),
            "content_length": r.headers.get("Content-Length", ""),
        }
    except httpx.HTTPError as e:
        return {"status": 0, "error": type(e).__name__}


def reingest_policy(insurer_slug: str, policy_name: str, url: str) -> bool:
    """Download the new PDF, delete its chunks from Chroma, re-run full ingest
    (which is incremental — skips already-indexed policy_ids).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", policy_name.lower()).strip("-")
    policy_id = f"{insurer_slug}__{slug}"
    out_dir = PROJECT_ROOT / "data" / "policies" / insurer_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}.pdf"

    # 1. Download new PDF
    try:
        with httpx.stream("GET", url, headers=HEADERS, timeout=60, follow_redirects=True) as r:
            if r.status_code != 200:
                return False
            with out_path.open("wb") as fp:
                for chunk in r.iter_bytes():
                    fp.write(chunk)
    except httpx.HTTPError:
        return False

    if out_path.read_bytes()[:5] != b"%PDF-":
        out_path.unlink(missing_ok=True)
        return False

    # 2. Delete this policy's chunks from Chroma so the next ingest re-creates them
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from rag.ingest import get_chroma_collection  # local import
        coll = get_chroma_collection()
        coll.delete(where={"policy_id": policy_id})
    except Exception:  # noqa: BLE001
        return False

    # 3. Run the (incremental) ingest — it will only process the deleted policy
    try:
        result = subprocess.run(
            [sys.executable, "-m", "rag.ingest"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=600,
            text=True,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def append_must_fix(entries: list[str]) -> None:
    header = "\n## PDF re-ingest failures (auto-detected)\n\n"
    if not MUST_FIX.exists():
        MUST_FIX.write_text("# Must Fix\n")
    with MUST_FIX.open("a") as fp:
        fp.write(header)
        for e in entries:
            fp.write(f"- {e}\n")


def main() -> int:
    entries = parse_corpus_urls()
    if not entries:
        print("[pdf-etags] corpus_urls.md not parsed", file=sys.stderr)
        return 2

    state = load_state()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    changed: list[dict] = []
    reingest_failures: list[str] = []

    with LOG_FILE.open("a") as fp, httpx.Client(timeout=TIMEOUT) as client:
        fp.write(f"\n=== run start {started} | {len(entries)} PDFs ===\n")
        for e in entries:
            url = e["url"]
            meta = head_meta(url, client)
            prev = state.get(url, {})
            sig = (meta.get("etag", ""), meta.get("last_modified", ""), meta.get("content_length", ""))
            prev_sig = (prev.get("etag", ""), prev.get("last_modified", ""), prev.get("content_length", ""))
            row = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "url": url,
                "policy": e["policy_name"],
                "meta": meta,
                "changed": False,
            }
            if not prev:
                # first time we see it — seed state, no action
                state[url] = meta
                row["seeded"] = True
            elif sig != prev_sig and meta.get("status") == 200:
                # genuine change → auto-fix via re-ingest
                row["changed"] = True
                ok = reingest_policy(e["insurer_slug"], e["policy_name"], url)
                row["reingest_ok"] = ok
                if ok:
                    state[url] = meta
                    changed.append(row)
                else:
                    reingest_failures.append(f"{e['policy_name']} ({url})")
            fp.write(json.dumps(row) + "\n")

    save_state(state)

    if changed:
        notify(
            "Insurance Bot — corpus refreshed",
            f"{len(changed)} policy PDFs updated and re-ingested",
        )
    if reingest_failures:
        append_must_fix(reingest_failures)
        notify(
            "Insurance Bot — re-ingest failure",
            f"{len(reingest_failures)} PDFs need manual re-ingest. See MUST_FIX.md",
        )

    print(
        f"[pdf-etags] checked {len(entries)} | changed {len(changed)} | "
        f"reingest-failures {len(reingest_failures)}"
    )
    return 1 if reingest_failures else 0


if __name__ == "__main__":
    sys.exit(main())
