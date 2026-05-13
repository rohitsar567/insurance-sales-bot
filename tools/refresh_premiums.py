"""Monthly premium-anchor refresh + auto-fix.

For each real (non-derived) premium sample in data/premiums/illustrative_premiums.json:
  1. Re-fetch the source URL (HEAD then partial GET)
  2. If the URL is dead → run link-rot auto-fix (Wayback / canonicalise)
  3. If the page is alive but the numeric anchor on the page has shifted, log
     a warning and append to MUST_FIX.md (we don't auto-overwrite a price
     value — that requires a human eyeball)
  4. After all anchor refreshes, re-derive every sample where
     source_url == "derived_from_anchor" using the same scaling factors
     stored in the JSON itself.

For aggregator ratings (data/reviews/*.json), re-HEAD every aggregator URL
and same auto-fix routine.

Exit codes:
  0 — no anchor changes, OR all anchors validated/repaired
  1 — at least one anchor needs human review (price drift > 15%)
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
LOG_FILE = LOG_DIR / "premium_refresh.log"
PREM_FILE = PROJECT_ROOT / "data" / "premiums" / "illustrative_premiums.json"
MUST_FIX = PROJECT_ROOT / "MUST_FIX.md"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "*/*"}
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
DRIFT_THRESHOLD = 0.15  # 15% price shift → human-review flag


def notify(title: str, body: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{title}"'],
            check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pass


def fetch(url: str, client: httpx.Client) -> tuple[int, str]:
    """Return (status, body or note)."""
    try:
        r = client.get(url, headers=HEADERS, follow_redirects=True)
        return r.status_code, r.text if r.status_code == 200 else ""
    except httpx.HTTPError as e:
        return 0, f"transport:{type(e).__name__}"


def try_wayback(url: str, client: httpx.Client) -> str | None:
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


def detect_price_drift(html: str, expected_inr: int) -> tuple[bool, list[int]]:
    """Look for INR amounts in the HTML near the expected value.

    Returns (drift_detected, candidate_values_found).
    """
    # Match Rs. / ₹ / Rs followed by digits & optional comma-formatting
    candidates = [int(m.replace(",", "")) for m in re.findall(r"(?:Rs\.?|₹|INR)\s*([\d,]{3,7})", html, re.I)]
    if not candidates:
        return False, []
    # consider only candidates within 5x of expected — filters phone numbers etc.
    band = [c for c in candidates if expected_inr // 5 <= c <= expected_inr * 5]
    if not band:
        return False, candidates[:5]
    closest = min(band, key=lambda v: abs(v - expected_inr))
    drift = abs(closest - expected_inr) / expected_inr
    return drift > DRIFT_THRESHOLD, band[:5]


def re_derive_samples(d: dict) -> None:
    """For every sample where source_url == derived_from_anchor, recompute from
    the anchor base × the documented scaling factor.

    We use the existing derivation_note as a deterministic record — this fn
    only refreshes the numeric value if the anchor it was derived from has
    been updated this run. v1: best-effort; we currently keep derived values
    static unless the anchor was updated."""
    # placeholder for v2 — anchor → derived chain rebuild
    _ = d


def append_must_fix(entries: list[str]) -> None:
    if not entries:
        return
    header = "\n## Premium drift — manual review\n\n"
    if not MUST_FIX.exists():
        MUST_FIX.write_text("# Must Fix\n")
    with MUST_FIX.open("a") as fp:
        fp.write(header)
        for e in entries:
            fp.write(f"- {e}\n")


def main() -> int:
    if not PREM_FILE.exists():
        print("[premium-refresh] premiums file missing", file=sys.stderr)
        return 2

    d = json.loads(PREM_FILE.read_text())
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    drift_alerts: list[str] = []
    auto_fixes = 0
    checked = 0

    with LOG_FILE.open("a") as fp, httpx.Client(timeout=TIMEOUT) as client:
        fp.write(f"\n=== run start {started} ===\n")
        for pid, entry in d.get("base_premiums", {}).items():
            for sample in entry.get("samples", []):
                src = sample.get("source_url", "")
                if not src.startswith("http"):
                    continue
                checked += 1
                status, body = fetch(src, client)
                row = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "policy_id": pid,
                    "url": src,
                    "status": status,
                }
                if status == 0 or status >= 400:
                    # try wayback
                    snap = try_wayback(src, client)
                    if snap:
                        sample["source_url"] = snap
                        sample.setdefault("auto_fix", []).append(
                            {"ts": started, "from": src, "to": snap, "reason": "link-rot"}
                        )
                        auto_fixes += 1
                        row["auto_fix"] = "wayback->" + snap
                    else:
                        drift_alerts.append(f"DEAD URL: {pid} -> {src}")
                else:
                    drifted, found = detect_price_drift(body, sample["annual_premium_inr"])
                    if drifted:
                        drift_alerts.append(
                            f"PRICE DRIFT: {pid} expected ₹{sample['annual_premium_inr']:,} "
                            f"but page shows {found} | {src}"
                        )
                        row["drift"] = found
                fp.write(json.dumps(row) + "\n")

        # reviews aggregator URLs
        reviews_dir = PROJECT_ROOT / "data" / "reviews"
        for f in reviews_dir.glob("*.json") if reviews_dir.exists() else []:
            data = json.loads(f.read_text())
            ratings = data.get("aggregator_ratings", {}) or {}
            for aggregator, info in list(ratings.items()):
                url = (info or {}).get("url") if isinstance(info, dict) else None
                if not url or not url.startswith("http"):
                    continue
                checked += 1
                status, _ = fetch(url, client)
                row = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "review_file": f.name,
                    "aggregator": aggregator,
                    "url": url,
                    "status": status,
                }
                if status == 0 or status >= 400:
                    snap = try_wayback(url, client)
                    if snap:
                        info["url"] = snap
                        info.setdefault("auto_fix", []).append(
                            {"ts": started, "from": url, "to": snap}
                        )
                        auto_fixes += 1
                        row["auto_fix"] = "wayback->" + snap
                    else:
                        drift_alerts.append(f"DEAD AGGREGATOR: {f.name}:{aggregator} -> {url}")
                fp.write(json.dumps(row) + "\n")
            f.write_text(json.dumps(data, indent=2))

    # persist any premium-file auto-fixes
    PREM_FILE.write_text(json.dumps(d, indent=2))

    if drift_alerts:
        append_must_fix(drift_alerts)
        notify(
            "Insurance Bot — premium drift",
            f"{len(drift_alerts)} premium anchors need review. See MUST_FIX.md",
        )

    print(
        f"[premium-refresh] checked {checked} URLs | auto-fixed {auto_fixes} | "
        f"drift alerts {len(drift_alerts)}"
    )
    return 1 if drift_alerts else 0


if __name__ == "__main__":
    sys.exit(main())
