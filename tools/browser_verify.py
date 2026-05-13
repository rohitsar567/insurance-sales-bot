"""Browser-based URL verifier — fallback for hosts that block scripted HTTP.

Many insurer / aggregator sites (Akamai, Cloudflare) return 403/503 to httpx
but load fine in a real browser. This tool uses headless Chromium (via
Playwright) to verify those URLs the way a human would.

Two modes:
  1. Standalone: `python tools/browser_verify.py [urls...]`
     - Reads URLs from argv or MUST_FIX.md
     - Renders each in headless Chromium
     - Writes results to tools/browser_verified.json

  2. Library: `from tools.browser_verify import verify_one`
     - check_link_rot.py imports this to retry after httpx failure

Verdict rules:
  ALIVE  — page loaded with status 200-399 AND title doesn't say "404/not found/error"
  DEAD   — main-resource HTTP 4xx/5xx OR title says 404
  TIMEOUT — page didn't finish loading within 25s

The allowlist (tools/browser_verified.json) is checked-in so the daily cron
trusts past verifications for 30 days before re-running browser checks.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = PROJECT_ROOT / "tools" / "browser_verified.json"
MUST_FIX = PROJECT_ROOT / "MUST_FIX.md"

DEAD_TITLE_PATTERNS = re.compile(
    r"(?i)404|not[\s-]found|page[\s-]not[\s-]available|error[\s-]occurred|access[\s-]denied"
)


def verify_one(url: str, page, context) -> dict:
    """Render `url` and return a verdict dict.

    PDFs use the browser's request stack (request.head) — chromium would try
    to download them which fails the page.goto contract. HTML uses full
    navigation so the JS challenge from Akamai/DataDome resolves.
    """
    is_pdf = url.lower().endswith(".pdf") or "/pdf/" in url.lower()
    last_err = ""

    if is_pdf:
        # Many insurer CDNs (Star Health on Akamai, ICICI) require a challenge
        # cookie from the parent host before serving PDFs. Warm up if 403.
        warmed = False
        for attempt in range(3):
            try:
                resp = context.request.get(url, timeout=60000, max_redirects=10)
                status = resp.status
                content_type = resp.headers.get("content-type", "")
                body_head = b""
                try:
                    body_head = resp.body()[:5] if status == 200 else b""
                except Exception:  # noqa: BLE001
                    body_head = b""
                is_real_pdf = body_head == b"%PDF-" or "pdf" in content_type.lower()
                if 200 <= status < 400 and is_real_pdf:
                    return {"verdict": "ALIVE", "status": status, "title": "[pdf]",
                            "reason": f"ct={content_type}", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
                # 403 from Akamai-style bot defence — try cookie warmup on parent host
                if status == 403 and not warmed:
                    try:
                        host_root = re.sub(r"(https?://[^/]+).*", r"\1", url)
                        page.goto(host_root, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2500)  # let JS challenge resolve
                        warmed = True
                        continue
                    except Exception:  # noqa: BLE001
                        pass
                if status >= 400:
                    return {"verdict": "DEAD", "status": status, "title": "[pdf]",
                            "reason": f"main_status={status}", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
                return {"verdict": "DEAD", "status": status, "title": "[pdf]",
                        "reason": f"not_pdf ct={content_type} head={body_head!r}",
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            except Exception as e:  # noqa: BLE001
                last_err = str(e).splitlines()[0][:160]
                if attempt < 2:
                    time.sleep(2)
                    continue
                return {"verdict": "ERROR", "status": 0, "title": "[pdf]",
                        "reason": last_err, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    # HTML path
    for attempt in range(2):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = response.status if response else 0
            title = page.title() or ""
            if status >= 400:
                return {"verdict": "DEAD", "status": status, "title": title[:120],
                        "reason": f"main_status={status}", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            if DEAD_TITLE_PATTERNS.search(title):
                return {"verdict": "DEAD", "status": status, "title": title[:120],
                        "reason": f"title={title!r}", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            return {"verdict": "ALIVE", "status": status, "title": title[:120],
                    "reason": "", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        except Exception as e:  # noqa: BLE001
            last_err = str(e).splitlines()[0][:160]
            if attempt == 0:
                time.sleep(2)
                continue
            verdict = "TIMEOUT" if "Timeout" in last_err or "timeout" in last_err else "ERROR"
            return {"verdict": verdict, "status": 0, "title": "", "reason": last_err,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    return {"verdict": "ERROR", "status": 0, "title": "", "reason": last_err,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def parse_must_fix() -> list[str]:
    """Extract URLs from MUST_FIX.md table."""
    if not MUST_FIX.exists():
        return []
    urls: list[str] = []
    for line in MUST_FIX.read_text().splitlines():
        if not line.startswith("|") or "url" in line.lower() and "status" in line.lower():
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) >= 2:
            m = re.search(r"https?://\S+", parts[1])
            if m:
                urls.append(m.group(0))
    return urls


def load_allowlist() -> dict:
    if ALLOWLIST.exists():
        try:
            return json.loads(ALLOWLIST.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_allowlist(d: dict) -> None:
    ALLOWLIST.write_text(json.dumps(d, indent=2, sort_keys=True))


def main(argv: list[str]) -> int:
    from playwright.sync_api import sync_playwright

    urls = argv[1:] if len(argv) > 1 else parse_must_fix()
    if not urls:
        print("[browser-verify] no URLs to check — pass on argv or populate MUST_FIX.md")
        return 0

    allowlist = load_allowlist()
    counts = {"ALIVE": 0, "DEAD": 0, "TIMEOUT": 0, "ERROR": 0}

    from playwright_stealth import Stealth

    with sync_playwright() as p:
        # Launch with anti-bot-detection flags. Akamai/Cloudflare detect
        # chrome-headless-shell easily; we use full chromium + stealth tricks.
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 800},
            locale="en-IN",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
            },
        )
        Stealth().apply_stealth_sync(context)
        page = context.new_page()

        for i, url in enumerate(urls, 1):
            print(f"  [{i}/{len(urls)}] {url[:90]}", flush=True)
            v = verify_one(url, page, context)
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
            if v["verdict"] == "ALIVE":
                allowlist[url] = v
            print(f"      -> {v['verdict']} | {v.get('status', '')} | {v.get('title', '')[:80]}")

        browser.close()

    save_allowlist(allowlist)
    print(
        f"\n[browser-verify] {sum(counts.values())} URLs checked  | "
        f"ALIVE={counts['ALIVE']}  DEAD={counts['DEAD']}  "
        f"TIMEOUT={counts['TIMEOUT']}  ERROR={counts['ERROR']}"
    )
    print(f"[browser-verify] allowlist saved -> {ALLOWLIST.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
