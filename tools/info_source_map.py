"""
info_source_map.py — 100% link-integrity + claim-to-source two-part audit.

Walks every claim with provenance triple {value, source_pdf_path|source_url,
source_quote} across:

  1. 40-data/policy_facts/*.json   (per-policy curated facts; ~102 files)
  2. 40-data/reviews/*.json        (per-insurer claim metrics + aggregator URLs)
  3. 40-data/premiums/illustrative_premiums.json  (premium samples with source_url)

For every (policy_id / insurer_slug, field, value, source) triple it runs:

  PART 1 — URL or local path resolves
    * source_pdf_path → file exists on disk
    * source_url      → in tools/browser_verified.json allowlist
                        OR httpx HEAD returns 2xx/3xx

  PART 2 — Source content backs the claim
    * source_pdf_path → open the PDF with pdfplumber, search for
                        source_quote (case-insensitive, whitespace-normalised,
                        first ~100 chars used as needle)
    * source_url      → fetch first 50KB and grep for the same needle

Verdicts (per claim):
  ✅ verified               — Part 1 + Part 2 both pass
  ⚠️ url-ok-quote-missing   — Part 1 passes, Part 2 fails
  ❌ url-broken             — Part 1 fails (file missing / URL broken)
  ⏳ no-source-data         — Field has a value but no source at all

Output:
  - eval/info_source_map.json   (machine-readable; ~one row per claim)
  - 40-data/information_source_map.md  (human-readable audit report)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx
import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
POLICY_FACTS_DIR = ROOT / "40-data" / "policy_facts"
REVIEWS_DIR = ROOT / "40-data" / "reviews"
PREMIUMS_FILE = ROOT / "40-data" / "premiums" / "illustrative_premiums.json"
BROWSER_VERIFIED = ROOT / "tools" / "browser_verified.json"
JSON_OUT = ROOT / "eval" / "info_source_map.json"
MD_OUT = ROOT / "40-data" / "information_source_map.md"

PDF_TEXT_CACHE: dict[str, str] = {}
URL_TEXT_CACHE: dict[str, str] = {}
URL_STATUS_CACHE: dict[str, int | None] = {}

NEEDLE_LEN = 60          # length of substring used as a search needle
MIN_NEEDLE_LEN = 12      # minimum useful needle
URL_FETCH_MAX = 50 * 1024
URL_TIMEOUT = 8.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# A small set of phrases that signal "no claim was made" — we treat these as
# administrative notes, not real claims to verify.
SENTINEL_PHRASES = {
    "not extracted",
    "not found",
    "not specified",
    "not enumerated",
    "not explicitly stated",
    "not extracted in this curation pass",
    "not extracted in this pass",
    "insurer-level metric",
    "presumed excluded",
    "presumed",
    "needs re-curation",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    """Lower-case, collapse whitespace, strip punctuation noise for matching."""
    if text is None:
        return ""
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    # Replace common smart-quotes / dashes that PDF extraction may differ on
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace(" ", " ")
    return text.strip()


def best_needle(quote: str) -> str:
    """Pick the longest contiguous alphanumeric-rich substring of the quote
    so that we don't anchor on a single common word."""
    q = normalise(quote)
    if len(q) <= NEEDLE_LEN:
        return q
    # Try to pick a window that contains digits / capitalised tokens
    best = q[:NEEDLE_LEN]
    for i in range(0, len(q) - NEEDLE_LEN, 20):
        window = q[i:i + NEEDLE_LEN]
        if any(c.isdigit() for c in window) and any(c.isalpha() for c in window):
            best = window
            break
    return best


def is_sentinel_quote(quote: str | None) -> bool:
    if not quote:
        return False
    n = normalise(quote)
    return any(p in n for p in SENTINEL_PHRASES)


def load_pdf_text(rel_path: str) -> str | None:
    """Return cached extracted text of a PDF (path is relative to project root)."""
    if rel_path in PDF_TEXT_CACHE:
        return PDF_TEXT_CACHE[rel_path]
    abs_path = (ROOT / rel_path).resolve()
    if not abs_path.exists():
        PDF_TEXT_CACHE[rel_path] = ""
        return None
    try:
        with pdfplumber.open(abs_path) as pdf:
            parts = []
            for page in pdf.pages:
                t = page.extract_text() or ""
                parts.append(t)
        full = normalise("\n".join(parts))
        PDF_TEXT_CACHE[rel_path] = full
        return full
    except Exception as exc:  # noqa: BLE001
        print(f"  [pdf-error] {rel_path}: {exc}", file=sys.stderr)
        PDF_TEXT_CACHE[rel_path] = ""
        return ""


def fetch_url_head(url: str) -> int | None:
    """HEAD request — returns status code or None on error."""
    if url in URL_STATUS_CACHE:
        return URL_STATUS_CACHE[url]
    try:
        with httpx.Client(follow_redirects=True, timeout=URL_TIMEOUT,
                          headers=HEADERS) as client:
            r = client.head(url)
            URL_STATUS_CACHE[url] = r.status_code
            if r.status_code in (405, 403):  # some servers refuse HEAD
                r2 = client.get(url, headers={**HEADERS, "Range": "bytes=0-1024"})
                URL_STATUS_CACHE[url] = r2.status_code
            return URL_STATUS_CACHE[url]
    except Exception:  # noqa: BLE001
        URL_STATUS_CACHE[url] = None
        return None


def fetch_url_text(url: str) -> str:
    """Return first 50KB of URL body, normalised — cached. Empty on failure."""
    if url in URL_TEXT_CACHE:
        return URL_TEXT_CACHE[url]
    try:
        with httpx.Client(follow_redirects=True, timeout=URL_TIMEOUT,
                          headers=HEADERS) as client:
            r = client.get(url, headers={**HEADERS,
                                         "Range": f"bytes=0-{URL_FETCH_MAX}"})
            if r.status_code >= 400:
                URL_TEXT_CACHE[url] = ""
                return ""
            text = r.text[:URL_FETCH_MAX]
            URL_TEXT_CACHE[url] = normalise(text)
            return URL_TEXT_CACHE[url]
    except Exception:  # noqa: BLE001
        URL_TEXT_CACHE[url] = ""
        return ""


def quote_found_in(haystack: str, quote: str) -> bool:
    """Try increasingly forgiving substring matches; return True if quote
    can be located in haystack."""
    if not haystack or not quote:
        return False
    needle = best_needle(quote)
    if len(needle) < MIN_NEEDLE_LEN:
        return False
    if needle in haystack:
        return True
    # Try first 30 chars
    short = needle[:30]
    if len(short) >= MIN_NEEDLE_LEN and short in haystack:
        return True
    # Try a digit/code anchor: any token of >=10 chars that contains a digit
    tokens = re.findall(r"[a-z0-9][a-z0-9\-./]{8,}", needle)
    for tok in tokens:
        if any(c.isdigit() for c in tok) and tok in haystack:
            return True
    return False


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

def load_allowlist() -> set[str]:
    if not BROWSER_VERIFIED.exists():
        return set()
    data = json.loads(BROWSER_VERIFIED.read_text())
    return set(data.keys())


# ---------------------------------------------------------------------------
# Auditors
# ---------------------------------------------------------------------------

def audit_provenance_triple(
    *,
    record_id: str,
    field: str,
    value: Any,
    source_pdf_path: str | None,
    source_url: str | None,
    source_quote: str | None,
    allowlist: set[str],
    check_url_live: bool,
) -> dict:
    """Return audit row for a single claim."""
    row = {
        "record_id": record_id,
        "field": field,
        "value": value,
        "source_pdf_path": source_pdf_path,
        "source_url": source_url,
        "source_quote": source_quote,
        "part1_resolves": False,
        "part2_quote_found": False,
        "verdict": "❌ url-broken",
        "notes": "",
    }

    # If value is None or sentinel quote, there's no claim being made — skip.
    if value is None or value == "" or value == [] or value == {}:
        row["verdict"] = "⏳ no-claim"
        row["notes"] = "value is null / empty — no claim to verify"
        return row

    if is_sentinel_quote(source_quote):
        row["verdict"] = "⏳ no-claim"
        row["notes"] = "source_quote is a 'not extracted' sentinel — administrative note, not a claim"
        return row

    # Need at least one source to audit.
    if not source_pdf_path and not source_url:
        row["verdict"] = "⏳ no-source-data"
        row["notes"] = "value populated but no source_pdf_path or source_url provided"
        return row

    # PART 1 + PART 2 via PDF path
    if source_pdf_path:
        abs_pdf = (ROOT / source_pdf_path).resolve()
        if abs_pdf.exists():
            row["part1_resolves"] = True
            text = load_pdf_text(source_pdf_path) or ""
            if source_quote and quote_found_in(text, source_quote):
                row["part2_quote_found"] = True
                row["verdict"] = "✅ verified"
            else:
                row["verdict"] = "⚠️ url-ok-quote-missing"
                row["notes"] = "PDF exists but source_quote not found in extracted text"
        else:
            row["verdict"] = "❌ url-broken"
            row["notes"] = f"PDF not found at {source_pdf_path}"
        return row

    # PART 1 + PART 2 via URL
    if source_url:
        if source_url in allowlist:
            row["part1_resolves"] = True
            row["notes"] = "URL in browser_verified allowlist"
        elif check_url_live:
            status = fetch_url_head(source_url)
            if status is not None and 200 <= status < 400:
                row["part1_resolves"] = True
            else:
                row["verdict"] = "❌ url-broken"
                row["notes"] = f"HEAD returned status={status}"
                return row
        else:
            # Defer URL liveness check, mark as ok (allowlist-only mode)
            row["part1_resolves"] = True
            row["notes"] = "URL liveness skipped (--allowlist-only)"

        # Part 2 — fetch content
        if source_quote and check_url_live:
            text = fetch_url_text(source_url)
            if text and quote_found_in(text, source_quote):
                row["part2_quote_found"] = True
                row["verdict"] = "✅ verified"
            else:
                row["verdict"] = "⚠️ url-ok-quote-missing"
                if not row["notes"]:
                    row["notes"] = "URL reachable but quote not found in fetched body"
                else:
                    row["notes"] += " | quote not found in fetched body"
        elif not source_quote:
            row["verdict"] = "⚠️ url-ok-quote-missing"
            row["notes"] = "URL reachable but no source_quote provided"
        else:
            # check_url_live False but we got past Part 1
            row["verdict"] = "⚠️ url-ok-quote-missing"
            row["notes"] = "quote not verified (allowlist-only mode)"
        return row

    return row


# ---------------------------------------------------------------------------
# Walkers
# ---------------------------------------------------------------------------

def walk_policy_facts(allowlist: set[str], check_url_live: bool) -> list[dict]:
    rows = []
    files = sorted(POLICY_FACTS_DIR.glob("*.json"))
    for i, f in enumerate(files, 1):
        if f.name.startswith("_"):
            continue
        data = json.loads(f.read_text())
        policy_id = data.get("policy_id", f.stem)
        print(f"  [{i:>3}/{len(files)}] policy_facts: {policy_id}", file=sys.stderr)
        for field, obj in data.items():
            if field in ("policy_id", "policy_name", "insurer_slug", "_meta"):
                continue
            if not isinstance(obj, dict):
                continue
            row = audit_provenance_triple(
                record_id=policy_id,
                field=field,
                value=obj.get("value"),
                source_pdf_path=obj.get("source_pdf_path"),
                source_url=obj.get("source_url"),
                source_quote=obj.get("source_quote"),
                allowlist=allowlist,
                check_url_live=check_url_live,
            )
            row["category"] = "policy_facts"
            row["source_file"] = str(f.relative_to(ROOT))
            rows.append(row)
    return rows


def walk_reviews(allowlist: set[str], check_url_live: bool) -> list[dict]:
    """Audit every URL inside per-insurer reviews JSONs."""
    rows = []
    files = sorted(REVIEWS_DIR.glob("*.json"))
    for i, f in enumerate(files, 1):
        if f.name.startswith("_") or f.name.lower() == "index.md":
            continue
        data = json.loads(f.read_text())
        slug = data.get("insurer_slug", f.stem)
        print(f"  [{i:>3}/{len(files)}] reviews: {slug}", file=sys.stderr)

        # claim_metrics block — three URLs
        cm = data.get("claim_metrics", {}) or {}
        for url_field in ("source_irdai_url", "source_secondary_url", "source_company_url"):
            url = cm.get(url_field)
            if not url:
                continue
            # Source quote for these = the numeric values they support
            csr = cm.get("claim_settlement_ratio_pct")
            quote = f"{csr}" if csr is not None else None
            row = audit_provenance_triple(
                record_id=slug,
                field=f"claim_metrics.{url_field}",
                value=csr,
                source_pdf_path=None,
                source_url=url,
                source_quote=quote,
                allowlist=allowlist,
                check_url_live=check_url_live,
            )
            row["category"] = "reviews"
            row["source_file"] = str(f.relative_to(ROOT))
            rows.append(row)

        # aggregator_ratings — policybazaar / insuredekho / joinditto each have url
        for agg_name, agg in (data.get("aggregator_ratings") or {}).items():
            if not isinstance(agg, dict):
                continue
            url = agg.get("url")
            star = agg.get("avg_star")
            if not url:
                continue
            row = audit_provenance_triple(
                record_id=slug,
                field=f"aggregator_ratings.{agg_name}",
                value=star,
                source_pdf_path=None,
                source_url=url,
                source_quote=None,  # rating pages rarely surface text-quotable evidence
                allowlist=allowlist,
                check_url_live=check_url_live,
            )
            row["category"] = "reviews"
            row["source_file"] = str(f.relative_to(ROOT))
            rows.append(row)

        # trustpilot.url
        tp = data.get("trustpilot") or {}
        if tp.get("url"):
            row = audit_provenance_triple(
                record_id=slug,
                field="trustpilot.url",
                value=tp.get("score"),
                source_pdf_path=None,
                source_url=tp.get("url"),
                source_quote=None,
                allowlist=allowlist,
                check_url_live=check_url_live,
            )
            row["category"] = "reviews"
            row["source_file"] = str(f.relative_to(ROOT))
            rows.append(row)

        # reddit_sentiment.sample_post_urls
        rs = data.get("reddit_sentiment") or {}
        for j, url in enumerate(rs.get("sample_post_urls") or []):
            row = audit_provenance_triple(
                record_id=slug,
                field=f"reddit.sample_post_urls[{j}]",
                value=url,
                source_pdf_path=None,
                source_url=url,
                source_quote=None,
                allowlist=allowlist,
                check_url_live=check_url_live,
            )
            row["category"] = "reviews"
            row["source_file"] = str(f.relative_to(ROOT))
            rows.append(row)

        # youtube_coverage.top_creators_who_reviewed[].video_url
        yc = data.get("youtube_coverage") or {}
        for j, vid in enumerate(yc.get("top_creators_who_reviewed") or []):
            url = vid.get("video_url")
            if not url:
                continue
            row = audit_provenance_triple(
                record_id=slug,
                field=f"youtube[{j}].{vid.get('creator', '?')}",
                value=vid.get("video_title"),
                source_pdf_path=None,
                source_url=url,
                source_quote=None,
                allowlist=allowlist,
                check_url_live=check_url_live,
            )
            row["category"] = "reviews"
            row["source_file"] = str(f.relative_to(ROOT))
            rows.append(row)

        # in_news[].url
        for j, news in enumerate(data.get("in_news") or []):
            url = news.get("url")
            if not url:
                continue
            row = audit_provenance_triple(
                record_id=slug,
                field=f"in_news[{j}]",
                value=news.get("headline"),
                source_pdf_path=None,
                source_url=url,
                source_quote=None,
                allowlist=allowlist,
                check_url_live=check_url_live,
            )
            row["category"] = "reviews"
            row["source_file"] = str(f.relative_to(ROOT))
            rows.append(row)
    return rows


def walk_premiums(allowlist: set[str], check_url_live: bool) -> list[dict]:
    rows = []
    if not PREMIUMS_FILE.exists():
        return rows
    data = json.loads(PREMIUMS_FILE.read_text())
    print(f"  premiums: {PREMIUMS_FILE.name}", file=sys.stderr)

    # sources_consulted at the top level
    for j, url in enumerate(data.get("sources_consulted") or []):
        row = audit_provenance_triple(
            record_id="premiums_meta",
            field=f"sources_consulted[{j}]",
            value=url,
            source_pdf_path=None,
            source_url=url,
            source_quote=None,
            allowlist=allowlist,
            check_url_live=check_url_live,
        )
        row["category"] = "premiums"
        row["source_file"] = str(PREMIUMS_FILE.relative_to(ROOT))
        rows.append(row)

    # per-policy base_premiums
    for policy_id, blk in (data.get("base_premiums") or {}).items():
        for j, sample in enumerate(blk.get("samples") or []):
            url = sample.get("source_url")
            if not url or url == "derived_from_anchor":
                # derived samples are explicitly labelled — not a claim against an external source
                continue
            row = audit_provenance_triple(
                record_id=policy_id,
                field=f"samples[{j}].age={sample.get('age')}_si={sample.get('sum_insured_inr')}",
                value=sample.get("annual_premium_inr"),
                source_pdf_path=None,
                source_url=url,
                source_quote=None,
                allowlist=allowlist,
                check_url_live=check_url_live,
            )
            row["category"] = "premiums"
            row["source_file"] = str(PREMIUMS_FILE.relative_to(ROOT))
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def render_markdown(rows: list[dict], summary_meta: dict) -> str:
    counts_overall = Counter(r["verdict"] for r in rows)
    counts_by_cat: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        counts_by_cat[r["category"]][r["verdict"]] += 1

    lines = []
    lines.append("# Insurance Sales Bot — Information Source Map")
    lines.append("")
    lines.append(f"Generated: {summary_meta['generated_at']}")
    lines.append(f"Total claims audited: **{len(rows)}**")
    lines.append("")
    lines.append("## Verdict Summary")
    lines.append("")
    lines.append("| Category | ✅ verified | ⚠️ url-ok-quote-missing | ❌ url-broken | ⏳ no-claim / no-source |")
    lines.append("|---|---:|---:|---:|---:|")
    cats = sorted(counts_by_cat.keys())
    for cat in cats:
        c = counts_by_cat[cat]
        no_claim = c.get("⏳ no-claim", 0) + c.get("⏳ no-source-data", 0)
        lines.append(
            f"| {cat} | {c.get('✅ verified', 0)} | "
            f"{c.get('⚠️ url-ok-quote-missing', 0)} | "
            f"{c.get('❌ url-broken', 0)} | "
            f"{no_claim} |"
        )
    total_no_claim = counts_overall.get("⏳ no-claim", 0) + counts_overall.get("⏳ no-source-data", 0)
    lines.append(
        f"| **TOTAL** | **{counts_overall.get('✅ verified', 0)}** | "
        f"**{counts_overall.get('⚠️ url-ok-quote-missing', 0)}** | "
        f"**{counts_overall.get('❌ url-broken', 0)}** | "
        f"**{total_no_claim}** |"
    )
    lines.append("")

    # Must-Fix section
    broken = [r for r in rows if r["verdict"] == "❌ url-broken"]
    lines.append(f"## Must Fix — {len(broken)} broken source(s)")
    lines.append("")
    if not broken:
        lines.append("_None — all sources resolve._")
    else:
        lines.append("| Record | Field | Value | Source | Notes |")
        lines.append("|---|---|---|---|---|")
        for r in broken:
            src = r["source_pdf_path"] or r["source_url"] or "—"
            val = str(r["value"])[:60]
            lines.append(
                f"| `{r['record_id']}` | `{r['field']}` | {val} | "
                f"`{src}` | {r['notes']} |"
            )
    lines.append("")

    # Per-category tables (compressed: only ⚠️ + ❌ shown)
    for cat in cats:
        lines.append(f"## {cat}")
        lines.append("")
        cat_rows = [r for r in rows if r["category"] == cat]
        flagged = [r for r in cat_rows if r["verdict"] in ("⚠️ url-ok-quote-missing", "❌ url-broken")]
        verified = sum(1 for r in cat_rows if r["verdict"] == "✅ verified")
        lines.append(f"Audited {len(cat_rows)} claims — ✅ {verified} verified, "
                     f"⚠️ {sum(1 for r in cat_rows if r['verdict']=='⚠️ url-ok-quote-missing')} "
                     f"quote-missing, ❌ {sum(1 for r in cat_rows if r['verdict']=='❌ url-broken')} broken.")
        lines.append("")
        if flagged:
            lines.append("### Flagged claims")
            lines.append("")
            lines.append("| Record | Field | Verdict | Source | Notes |")
            lines.append("|---|---|---|---|---|")
            for r in flagged[:200]:  # cap to keep MD manageable
                src = r["source_pdf_path"] or r["source_url"] or "—"
                lines.append(
                    f"| `{r['record_id']}` | `{r['field']}` | {r['verdict']} | "
                    f"`{src}` | {r['notes']} |"
                )
            if len(flagged) > 200:
                lines.append(f"\n_... and {len(flagged) - 200} more rows truncated; see eval/info_source_map.json for full data._")
        lines.append("")

    # 100% verified insurers
    lines.append("## Insurers / Policies with 100% verified claims")
    lines.append("")
    per_record_counts: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        per_record_counts[r["record_id"]][r["verdict"]] += 1
    clean = []
    not_clean = []
    for record_id, c in sorted(per_record_counts.items()):
        verified = c.get("✅ verified", 0)
        broken = c.get("❌ url-broken", 0)
        quote_missing = c.get("⚠️ url-ok-quote-missing", 0)
        total_real = verified + broken + quote_missing
        if total_real == 0:
            continue  # only no-claim rows
        if broken == 0 and quote_missing == 0:
            clean.append(record_id)
        else:
            not_clean.append((record_id, verified, quote_missing, broken))
    for r in clean:
        lines.append(f"- {r}")
    if not clean:
        lines.append("_None._")
    lines.append("")

    lines.append("## Records with remaining ⚠️ url-ok-quote-missing")
    lines.append("")
    if not_clean:
        lines.append("| Record | ✅ | ⚠️ | ❌ |")
        lines.append("|---|---:|---:|---:|")
        for record_id, v, q, b in not_clean:
            lines.append(f"| {record_id} | {v} | {q} | {b} |")
    else:
        lines.append("_None._")
    lines.append("")

    # Final summary line
    lines.append("---")
    lines.append("")
    lines.append(f"**Audit complete: ✅ {counts_overall.get('✅ verified', 0)} / "
                 f"⚠️ {counts_overall.get('⚠️ url-ok-quote-missing', 0)} / "
                 f"❌ {counts_overall.get('❌ url-broken', 0)}**")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allowlist-only", action="store_true",
                   help="Skip live HTTP for URLs (rely only on browser_verified.json)")
    p.add_argument("--skip-urls", action="store_true",
                   help="Skip URL audits entirely; only audit PDF-backed claims")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    allowlist = load_allowlist()
    print(f"Loaded {len(allowlist)} URLs in browser_verified allowlist.", file=sys.stderr)
    check_url_live = not args.allowlist_only

    t0 = time.time()
    rows: list[dict] = []
    rows.extend(walk_policy_facts(allowlist, check_url_live))
    if not args.skip_urls:
        rows.extend(walk_reviews(allowlist, check_url_live))
        rows.extend(walk_premiums(allowlist, check_url_live))
    elapsed = time.time() - t0

    summary_meta = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "elapsed_sec": round(elapsed, 1),
        "rows": len(rows),
    }

    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(
        {"meta": summary_meta, "rows": rows}, indent=2, ensure_ascii=False))
    md = render_markdown(rows, summary_meta)
    MD_OUT.parent.mkdir(parents=True, exist_ok=True)
    MD_OUT.write_text(md)

    print(f"\nWrote {JSON_OUT.relative_to(ROOT)} ({len(rows)} rows)")
    print(f"Wrote {MD_OUT.relative_to(ROOT)}")
    print(f"Elapsed: {elapsed:.1f}s")

    # one-line verdict
    counts = Counter(r["verdict"] for r in rows)
    print(f"\nVerdicts: ✅ {counts.get('✅ verified',0)} | "
          f"⚠️ {counts.get('⚠️ url-ok-quote-missing',0)} | "
          f"❌ {counts.get('❌ url-broken',0)} | "
          f"⏳ {counts.get('⏳ no-claim',0) + counts.get('⏳ no-source-data',0)}")


if __name__ == "__main__":
    main()
