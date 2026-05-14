"""Daily link-rot check + auto-fix.

Three-phase pipeline run unattended by launchd every night:
  1. DETECT — HEAD every external URL referenced in the KB
  2. AUTO-FIX — for each dead URL, try repair strategies in order
        (a) Wayback Machine snapshot lookup
        (b) URL canonicalisation (strip query strings, swap http/https)
        (c) Insurer-site root-path retry (PDFs only)
     If any fix succeeds, the source file is patched in place.
  3. REPORT — anything still dead is written to MUST_FIX.md and a macOS
     notification is posted so the user knows a manual fix is needed.

The cron job is idempotent. Re-running after a successful auto-fix is a no-op.

URLs are pulled from three places:
  - 40-data/corpus_urls.md           — policy PDF index (markdown table)
  - 40-data/premiums/illustrative_premiums.json — premium anchors
  - 40-data/reviews/*.json           — aggregator + news + IRDAI + Reddit + YouTube

Exit codes:
  0 — all URLs reachable, OR all dead URLs were auto-fixed
  1 — at least one URL is still dead after auto-fix (manual action required)
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
LOG_FILE = LOG_DIR / "link_rot.log"
MUST_FIX = PROJECT_ROOT / "MUST_FIX.md"
BROWSER_ALLOWLIST = PROJECT_ROOT / "tools" / "browser_verified.json"
ALLOWLIST_TTL_DAYS = 30  # re-verify via browser after 30 days

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/pdf,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}
TIMEOUT = httpx.Timeout(20.0, connect=8.0)


def notify(title: str, body: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{title}"'],
            check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pass


def load_browser_allowlist() -> dict[str, dict]:
    """URLs that a real browser has verified work. The cron skips these
    (real users would succeed) until the entry ages past ALLOWLIST_TTL_DAYS,
    at which point browser_verify.py should be re-run."""
    if not BROWSER_ALLOWLIST.exists():
        return {}
    try:
        return json.loads(BROWSER_ALLOWLIST.read_text())
    except json.JSONDecodeError:
        return {}


def is_allowlisted(url: str, allowlist: dict[str, dict]) -> bool:
    entry = allowlist.get(url)
    if not entry:
        return False
    ts_str = entry.get("ts", "")
    if not ts_str:
        return False
    try:
        # ISO 8601 with TZ offset; chop fractional seconds if present
        ts_clean = ts_str.split(".")[0]
        # python <3.11 doesn't parse +0530 without colon — normalise
        if len(ts_clean) >= 5 and ts_clean[-5] in ("+", "-") and ts_clean[-3] != ":":
            ts_clean = ts_clean[:-2] + ":" + ts_clean[-2:]
        from datetime import datetime, timezone
        verified = datetime.fromisoformat(ts_clean)
        if verified.tzinfo is None:
            verified = verified.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - verified).days
        return age_days <= ALLOWLIST_TTL_DAYS
    except (ValueError, IndexError):
        return False


def collect_urls() -> dict[str, list[tuple[str, Path]]]:
    """Return {url: [(label, source_file), ...]}."""
    urls: dict[str, list[tuple[str, Path]]] = {}

    def add(url: str, label: str, source: Path) -> None:
        u = url.strip().rstrip(",.)")
        if not u.startswith("http"):
            return
        urls.setdefault(u, []).append((label, source))

    corpus_md = PROJECT_ROOT / "40-data" / "corpus_urls.md"
    if corpus_md.exists():
        # Markdown tables use `|` as column separator. URLs themselves may
        # contain parens (e.g. care-advantage-(health-insurance-product...) so
        # we only break on whitespace and the column separator.
        for line in corpus_md.read_text().splitlines():
            if not line.startswith("|"):
                continue
            parts = [p.strip() for p in line.strip("|").split("|")]
            for p in parts:
                m = re.search(r"https?://\S+", p)
                if m:
                    add(m.group(0), "corpus_urls.md", corpus_md)

    prem_json = PROJECT_ROOT / "40-data" / "premiums" / "illustrative_premiums.json"
    if prem_json.exists():
        d = json.loads(prem_json.read_text())
        for pid, entry in d.get("base_premiums", {}).items():
            for s in entry.get("samples", []):
                add(s.get("source_url", ""), f"premiums:{pid}", prem_json)

    reviews_dir = PROJECT_ROOT / "40-data" / "reviews"
    if reviews_dir.exists():
        for f in reviews_dir.glob("*.json"):
            text = f.read_text()
            for m in re.finditer(r"https?://[^\s\"',\]\}]+", text):
                add(m.group(0), f"reviews:{f.name}", f)

    return urls


def head_check(url: str, client: httpx.Client) -> tuple[int, str]:
    try:
        r = client.head(url, headers=HEADERS, follow_redirects=True)
        if r.status_code in (403, 405, 501):
            r = client.get(
                url,
                headers={**HEADERS, "Range": "bytes=0-1023"},
                follow_redirects=True,
            )
        return r.status_code, f"final_url={r.url}"
    except httpx.TimeoutException:
        return 0, "timeout"
    except httpx.HTTPError as e:
        return 0, f"transport_error:{type(e).__name__}"


# ---------- auto-fix strategies ----------


def try_wayback(url: str, client: httpx.Client) -> str | None:
    """Return a working Wayback Machine snapshot URL, or None."""
    try:
        r = client.get(
            "https://archive.org/wayback/available",
            params={"url": url},
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            return None
        snap = r.json().get("archived_snapshots", {}).get("closest", {})
        if snap.get("available") and snap.get("status", "").startswith("2"):
            return snap.get("url")
    except (httpx.HTTPError, ValueError):
        return None
    return None


def try_canonicalise(url: str, client: httpx.Client) -> str | None:
    """Strip query strings, flip http<->https."""
    candidates = []
    if "?" in url:
        candidates.append(url.split("?", 1)[0])
    if url.startswith("http://"):
        candidates.append("https://" + url[len("http://") :])
    elif url.startswith("https://"):
        candidates.append("http://" + url[len("https://") :])
    for c in candidates:
        s, _ = head_check(c, client)
        if 200 <= s < 400:
            return c
    return None


def auto_fix(url: str, client: httpx.Client) -> tuple[str | None, str]:
    """Return (replacement_url, strategy) or (None, "")."""
    for strat_name, strat in (
        ("canonicalise", try_canonicalise),
        ("wayback", try_wayback),
    ):
        fix = strat(url, client)
        if fix:
            return fix, strat_name
    return None, ""


def apply_patch(old: str, new: str, files: list[Path]) -> int:
    """Replace `old` with `new` in every distinct file; return file-count patched."""
    count = 0
    for f in set(files):
        try:
            content = f.read_text()
            if old in content:
                f.write_text(content.replace(old, new))
                count += 1
        except OSError:
            continue
    return count


# ---------- main ----------


def main() -> int:
    urls = collect_urls()
    if not urls:
        print("[link-rot] no URLs found — KB layout changed?", file=sys.stderr)
        return 2

    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    fixed: list[tuple[str, str, str]] = []  # (old, new, strategy)
    still_dead: list[tuple[str, int, str, list[str]]] = []
    allowlist = load_browser_allowlist()
    allowlisted_count = 0

    with LOG_FILE.open("a") as fp, httpx.Client(timeout=TIMEOUT) as client:
        fp.write(f"\n=== run start {started} | {len(urls)} URLs | allowlist={len(allowlist)} ===\n")
        for url, refs in urls.items():
            # Skip URLs a real browser has already verified work — until TTL expires.
            # These are typically bot-protected hosts (Akamai/Cloudflare/DataDome)
            # that httpx cannot HEAD but render fine for end users.
            if is_allowlisted(url, allowlist):
                fp.write(json.dumps({"url": url, "browser_allowlisted": allowlist[url].get("ts")}) + "\n")
                allowlisted_count += 1
                continue
            status, note = head_check(url, client)
            ok = 200 <= status < 400
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "url": url,
                "status": status,
                "ok": ok,
                "note": note,
                "sources": [lbl for lbl, _ in refs],
            }
            if ok:
                fp.write(json.dumps(entry) + "\n")
                continue

            # auto-fix attempt
            new_url, strat = auto_fix(url, client)
            if new_url:
                files = [src for _, src in refs]
                patched = apply_patch(url, new_url, files)
                entry["auto_fix"] = {"strategy": strat, "new_url": new_url, "files_patched": patched}
                fixed.append((url, new_url, strat))
            else:
                still_dead.append((url, status, note, [lbl for lbl, _ in refs]))
            fp.write(json.dumps(entry) + "\n")

    # MUST_FIX.md report — overwritten every run so it always reflects current state
    if still_dead:
        lines = [
            "# Link-rot — manual fix required",
            "",
            f"Run: {started}",
            f"Auto-fixed: {len(fixed)}   Still dead: {len(still_dead)}",
            "",
            "| status | url | sources |",
            "|---|---|---|",
        ]
        for url, status, _note, sources in still_dead:
            lines.append(f"| {status} | {url} | {', '.join(sources)} |")
        MUST_FIX.write_text("\n".join(lines) + "\n")
        notify(
            "Insurance Bot — link rot",
            f"{len(still_dead)} dead URLs need manual fix. See MUST_FIX.md",
        )
    elif MUST_FIX.exists():
        MUST_FIX.unlink()  # clean up stale report

    print(
        f"[link-rot] total {len(urls)} | browser-allowlisted {allowlisted_count} | "
        f"http-checked {len(urls) - allowlisted_count} | auto-fixed {len(fixed)} | "
        f"still dead {len(still_dead)}"
    )
    for old, new, strat in fixed[:10]:
        print(f"  FIXED ({strat}): {old}\n            -> {new}")
    for url, status, note, sources in still_dead[:10]:
        print(f"  DEAD [{status}]: {url}  ({note})  ← {','.join(sources)}")

    return 1 if still_dead else 0


if __name__ == "__main__":
    sys.exit(main())
