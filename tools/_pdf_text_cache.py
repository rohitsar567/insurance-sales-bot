#!/usr/bin/env python3
"""Extract + cache full text of every source PDF referenced by policy_facts.

Writes plaintext to tools/.pdf_text_cache/<sha-ish>.txt keyed by pdf path.
Idempotent. This is internal-data mining (the insurer's own IRDAI-filed
wording), NOT web fabrication.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "tools" / ".pdf_text_cache"
CACHE.mkdir(exist_ok=True)

# Known path fixes (typo'd source_pdf_path in policy_facts).
PATH_FIX = {
    "rag/corpus/bajaj-allianz/group-health-guard-gold__wordings.pdf":
        "rag/corpus/bajaj-allianz/group-health-guard-silver__wordings.pdf",
}


def cache_path(pdf_rel: str) -> Path:
    h = hashlib.sha1(pdf_rel.encode()).hexdigest()[:16]
    return CACHE / f"{h}.txt"


def text_for(pdf_rel: str) -> str | None:
    pdf_rel = PATH_FIX.get(pdf_rel, pdf_rel)
    p = ROOT / pdf_rel
    if not p.exists():
        return None
    cp = cache_path(pdf_rel)
    if cp.exists() and cp.stat().st_size > 0:
        return cp.read_text(errors="replace")
    parts = []
    try:
        with pdfplumber.open(p) as pdf:
            for pg in pdf.pages:
                parts.append(pg.extract_text() or "")
                for tbl in (pg.extract_tables() or []):
                    for row in tbl:
                        parts.append(
                            " | ".join(c or "" for c in row)
                        )
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"!! {pdf_rel}: {e}\n")
        return None
    txt = "\n".join(parts)
    cp.write_text(txt, errors="replace")
    return txt


def resolve_source_pdf(facts: dict) -> str | None:
    sp = facts.get("source_pdf_path")
    if isinstance(sp, str):
        return sp
    meta = facts.get("_meta", {}) or {}
    if isinstance(meta.get("primary_source_pdf"), str):
        return meta["primary_source_pdf"]
    for v in facts.values():
        if isinstance(v, dict) and isinstance(v.get("source_pdf_path"), str):
            return v["source_pdf_path"]
    return None


if __name__ == "__main__":
    import glob
    n_ok = n_fail = 0
    for f in sorted(glob.glob(str(ROOT / "40-data/policy_facts/*.json"))):
        if os.path.basename(f).startswith("_"):
            continue
        facts = json.loads(Path(f).read_text())
        sp = resolve_source_pdf(facts)
        if not sp:
            n_fail += 1
            continue
        t = text_for(sp)
        if t:
            n_ok += 1
        else:
            n_fail += 1
            print("FAIL", os.path.basename(f), sp)
    print(f"cached text for {n_ok} policies, {n_fail} failed")
