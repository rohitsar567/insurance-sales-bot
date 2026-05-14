"""Hardened hallucination guard for newly-added corpus entries.

Runs AFTER the corpus-expansion agents finish. For every manifest entry
that didn't exist on the previous git HEAD (i.e. added in this session),
applies a strict 4-check verification. Any failure deletes the local PDF
+ the manifest entry + appends a rejection log line — there is no "yellow"
state. Either the entry survives all 4 checks or it's gone.

The 4 checks per new entry:
  1. LOCAL FILE  — the PDF exists locally, file(1) reports "PDF document",
     size > 50 KB.
  2. URL ALIVE   — HEAD request on the manifest URL returns 200/301/302
     within 15s.
  3. URL → PDF   — GET on the URL returns either content-type:application/pdf
                   OR an HTML page whose body mentions the manifest
                   policy_name (case-insensitive).
  4. PDF → NAME  — first 8 KB of pdfplumber-extracted text from the local
                   PDF contains either the manifest policy_name or the
                   manifest UIN code. Catches mis-mapped PDFs.

Run:
  .venv/bin/python tools/verify_new_corpus.py

Outputs:
  - 40-data/new_corpus_verification.json   (machine-readable audit)
  - logs/new_corpus_verification.log    (human-readable rejection trail)
  - rejected entries DELETED from _manifest.json + .pdf files removed
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "rag" / "corpus" / "_manifest.json"
LOG = ROOT / "logs" / "new_corpus_verification.log"
AUDIT = ROOT / "40-data" / "new_corpus_verification.json"
LOG.parent.mkdir(parents=True, exist_ok=True)
AUDIT.parent.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _git_baseline_manifest() -> dict:
    """Snapshot manifest from the last committed HEAD — everything not in
    this set is considered 'new in this session' for the purpose of this
    verification."""
    try:
        out = subprocess.run(
            ["git", "show", "HEAD:rag/corpus/_manifest.json"],
            cwd=str(ROOT), capture_output=True, text=True, check=True,
        )
        return json.loads(out.stdout)
    except Exception:
        return {}


def check_local_file(rel_path: str) -> tuple[bool, str]:
    p = ROOT / rel_path
    if not p.exists():
        return False, f"file_missing: {rel_path}"
    sz = p.stat().st_size
    if sz < 50 * 1024:
        return False, f"file_too_small: {sz} bytes"
    out = subprocess.run(["file", "--brief", str(p)], capture_output=True, text=True)
    if "PDF document" not in out.stdout:
        return False, f"not_a_pdf: file(1) says {out.stdout.strip()!r}"
    return True, f"OK {sz} bytes, file(1)=PDF document"


def check_url_alive(url: str) -> tuple[bool, str]:
    if not url or not url.startswith(("http://", "https://")):
        return False, f"bad_scheme: {url!r}"
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as c:
            r = c.head(url)
            if r.status_code in (405, 403):  # some servers reject HEAD; try GET
                r = c.get(url, headers={"Range": "bytes=0-1023"})
            if r.status_code not in (200, 301, 302):
                return False, f"http_{r.status_code}"
            return True, f"http_{r.status_code}"
    except Exception as e:
        return False, f"net_error: {type(e).__name__}: {str(e)[:80]}"


def check_url_returns_pdf_or_page(url: str, policy_name: str) -> tuple[bool, str]:
    """Either the URL is a direct PDF, or it's an HTML page that mentions
    the policy_name. Catches sketch entries where the URL points at a
    different policy or at a 404 page."""
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as c:
            r = c.get(url, headers={"Range": "bytes=0-65536"})  # 64K is enough
            ctype = r.headers.get("content-type", "")
            if "application/pdf" in ctype.lower() or url.lower().endswith(".pdf"):
                # PDF — first bytes should start with %PDF-
                if r.content[:5] == b"%PDF-":
                    return True, "direct_pdf"
                return False, f"pdf_magic_missing: starts {r.content[:20]!r}"
            # HTML — body must mention the policy name
            body = r.text.lower()
            words = [w for w in re.split(r"\s+", policy_name.lower()) if len(w) >= 4]
            hits = sum(1 for w in words if w in body)
            if hits >= max(1, len(words) // 2):
                return True, f"html_mentions_{hits}/{len(words)}_name_tokens"
            return False, f"html_does_not_mention_policy: only {hits}/{len(words)} name tokens"
    except Exception as e:
        return False, f"net_error: {type(e).__name__}: {str(e)[:80]}"


def check_pdf_mentions_policy(rel_path: str, policy_name: str, uin_code: str = "") -> tuple[bool, str]:
    """First 8 KB of PDF text should contain the policy name OR the UIN."""
    p = ROOT / rel_path
    try:
        with pdfplumber.open(str(p)) as pdf:
            text = ""
            for page in pdf.pages[:3]:
                text += (page.extract_text() or "") + "\n"
                if len(text) > 8000:
                    break
        text_lower = text.lower()
        if uin_code and uin_code.lower() in text_lower:
            return True, f"uin_match: {uin_code}"
        words = [w for w in re.split(r"\s+", policy_name.lower()) if len(w) >= 4]
        hits = sum(1 for w in words if w in text_lower)
        if hits >= max(1, len(words) // 2):
            return True, f"pdf_mentions_{hits}/{len(words)}_name_tokens"
        return False, f"pdf_does_not_mention_policy: {hits}/{len(words)} name tokens in first 8KB"
    except Exception as e:
        return False, f"pdf_parse_error: {type(e).__name__}: {str(e)[:80]}"


def main():
    if not MANIFEST.exists():
        print(f"FATAL: {MANIFEST} not found")
        return 1
    current = json.loads(MANIFEST.read_text())
    baseline = _git_baseline_manifest()
    new_keys = sorted(set(current.keys()) - set(baseline.keys()))
    print(f"baseline manifest size: {len(baseline)}")
    print(f"current  manifest size: {len(current)}")
    print(f"NEW entries (added this session): {len(new_keys)}")
    print()

    audit: dict = {"verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "baseline_count": len(baseline), "current_count": len(current),
                   "results": []}
    rejected_keys = []

    log_f = LOG.open("a")
    log_f.write(f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} verify_new_corpus.py run ===\n")

    for rel_path in new_keys:
        entry = current[rel_path]
        url = entry.get("url", "")
        policy_name = entry.get("policy_name", "")
        uin = entry.get("uin_code", "")
        print(f"--- {rel_path} ---")
        print(f"  url: {url}")
        print(f"  policy_name: {policy_name}")
        checks = {}
        ok_local, msg_local = check_local_file(rel_path)
        checks["local_file"] = {"ok": ok_local, "msg": msg_local}
        print(f"  [1/4] local_file: {msg_local}")
        ok_url, msg_url = check_url_alive(url) if ok_local else (False, "skipped:no_local")
        checks["url_alive"] = {"ok": ok_url, "msg": msg_url}
        print(f"  [2/4] url_alive:  {msg_url}")
        ok_content, msg_content = check_url_returns_pdf_or_page(url, policy_name) if ok_url else (False, "skipped:url_dead")
        checks["url_content"] = {"ok": ok_content, "msg": msg_content}
        print(f"  [3/4] url_content:{msg_content}")
        ok_pdf, msg_pdf = check_pdf_mentions_policy(rel_path, policy_name, uin) if ok_local else (False, "skipped:no_local")
        checks["pdf_mentions"] = {"ok": ok_pdf, "msg": msg_pdf}
        print(f"  [4/4] pdf_mentions:{msg_pdf}")
        passed = all(c["ok"] for c in checks.values())
        audit["results"].append({"path": rel_path, "url": url, "policy_name": policy_name,
                                 "checks": checks, "passed": passed})
        log_f.write(f"{rel_path}: {'PASS' if passed else 'REJECT'} | "
                    f"local={msg_local[:60]} | url={msg_url[:60]} | "
                    f"content={msg_content[:60]} | pdf={msg_pdf[:60]}\n")
        if not passed:
            rejected_keys.append(rel_path)
            print(f"  ✗ REJECTED — deleting file + manifest entry")
            try:
                (ROOT / rel_path).unlink(missing_ok=True)
            except Exception:
                pass
        else:
            print(f"  ✓ VERIFIED")
        print()

    for k in rejected_keys:
        current.pop(k, None)
    MANIFEST.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n")
    log_f.write(f"summary: {len(new_keys) - len(rejected_keys)} verified / {len(rejected_keys)} rejected\n")
    log_f.close()

    print(f"\n=== SUMMARY ===")
    print(f"  new entries:  {len(new_keys)}")
    print(f"  VERIFIED:     {len(new_keys) - len(rejected_keys)}")
    print(f"  REJECTED:     {len(rejected_keys)}")
    print(f"  audit file:   {AUDIT.relative_to(ROOT)}")
    print(f"  log file:     {LOG.relative_to(ROOT)}")
    return 0 if not rejected_keys else 0  # informational only — never crash CI


if __name__ == "__main__":
    sys.exit(main())
