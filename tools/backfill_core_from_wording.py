#!/usr/bin/env python3
"""Backfill ONLY the deterministically-safe core fields from the policy's
own already-curated source PDF (insurer's IRDAI-filed wording).

ZERO fabrication. Each field is filled only when an unambiguous,
IRDAI-standardised clause is present verbatim. The verbatim sentence is
stored as source_quote and the existing wrapped-object schema is preserved:
    {"value":…, "unit":…, "source_quote":…, "source_pdf_path":…,
     "extraction_method":"tools/backfill_core_from_wording.py", "_confidence":…}

Fields handled (and ONLY these — the rest need official-brochure web sourcing):
  - cashless_treatment_supported  (IRDAI std "Cashless facility means …")
  - ayush_coverage               (AYUSH treatment/hospital coverage clause)
  - maternity_coverage           (explicit cover clause vs Excl18 exclusion)
  - initial_waiting_period_days  (IRDAI Excl03 standardised 30 days)
  - pre_existing_disease_waiting_months (IRDAI Excl01 "expiry of N months")

Run:
  python tools/backfill_core_from_wording.py --dry     # report only
  python tools/backfill_core_from_wording.py --write    # apply edits
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pdf_text_cache import resolve_source_pdf, text_for  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FACTS_DIR = ROOT / "40-data" / "policy_facts"


def _clip(t: str, m: re.Match, before=90, after=240) -> str:
    s = max(0, m.start() - before)
    e = min(len(t), m.end() + after)
    return re.sub(r"\s+", " ", t[s:e]).strip()


def _empty(v) -> bool:
    if isinstance(v, dict):
        v = v.get("value") if "value" in v else (v.get("covered") if "covered" in v else v)
    return v is None or v == "" or v == [] or (isinstance(v, dict) and not v)


# ---- per-field deterministic extractors -----------------------------------
def x_cashless(t: str):
    m = re.search(r"Cashless facility means[^.]{0,400}\.", t, re.I)
    if m:
        return True, _clip(t, m)
    return None, None


_AYUSH_RE = re.compile(
    r"(AYUSH (?:Hospital|Treatment|Day Care)[^.]{0,300}\.)"
    r"|(Ayurveda, Yoga (?:and|&) Naturopathy, Unani, Siddha (?:and|&) Hom"
    r"o?eopathy[^.]{0,260}\.)",
    re.I,
)


# Product types where an AYUSH *definition* in the boilerplate does NOT imply
# the product covers AYUSH in-patient treatment (CI/PA/benefit/top-up-only).
_AYUSH_SKIP_NAME = re.compile(
    r"criti|critical[ -]?illness|personal[ -]?accident|\bpa\b|cancer|"
    r"hospi[- ]?cash|benefit(?:\s|$)|surakshit|term|janata personal",
    re.I,
)
# A genuine AYUSH benefit clause — coverage verb attached to the AYUSH benefit,
# NOT merely the IRDAI standard definition ("AYUSH Treatment refers to ...").
_AYUSH_COVER_RE = re.compile(
    r"AYUSH (?:Treatment|Hospital|Day Care|Cover|Benefit|Hospitali[sz]ation)"
    r"[^.]{0,300}?(?:we will (?:cover|indemnif|pay|reimburse)|"
    r"is covered|shall be (?:covered|payable|indemnified)|covered up to|"
    r"will be (?:covered|payable)|expenses (?:incurred|towards)[^.]{0,90}AYUSH|"
    r"in[- ]?patient (?:care|treatment)[^.]{0,80}AYUSH|"
    r"sum insured[^.]{0,40}AYUSH|AYUSH[^.]{0,80}up to the sum insured)"
    r"[^.]{0,200}\.",
    re.I,
)
# Also accept a "Coverage/Benefits" section header that lists AYUSH as a covered
# item (e.g. "4. AYUSH Treatment Section B-1.4" inside a benefits index).
_AYUSH_BENEFIT_INDEX_RE = re.compile(
    r"(?:Cover|Benefit|In[- ]?Patient)[^\n]{0,40}\n?[^\n]{0,40}"
    r"AYUSH (?:Treatment|Hospitali[sz]ation)",
    re.I,
)


def x_ayush(t: str, policy_name: str = ""):
    if policy_name and _AYUSH_SKIP_NAME.search(policy_name):
        return None, None
    m = _AYUSH_COVER_RE.search(t)
    if m:
        return True, _clip(t, m)
    return None, None


def x_maternity(t: str):
    # Covered?  explicit benefit clause: "We will indemnify ... Maternity
    # Expenses" / "Maternity Expenses ... is opted/covered". This is a
    # BENEFIT clause, never the IRDAI Excl18 definition.
    mcov = re.search(
        r"(?:[Ww]e will (?:cover|indemnif\w+|pay)|[Cc]ompany will "
        r"(?:cover|indemnif\w+|pay)|shall (?:cover|indemnif\w+))"
        r"[^.]{0,60}Maternity Expenses[^.]{0,260}\.", t
    ) or re.search(
        r"Maternity Expenses[^.]{0,30}(?:is|are)\s+(?:opted|covered)"
        r"[^.]{0,220}\.", t, re.I
    )
    if mcov:
        # Guard: not the exclusion sentence itself.
        if not re.search(r"Excl18|shall be excluded", mcov.group(0), re.I):
            return True, _clip(t, mcov)
    # Excluded in base?  IRDAI standard exclusion code Excl18 against the
    # Maternity head. This is the same signal the existing curation used
    # (e.g. acko-health-ii: maternity_coverage=false w/ the Excl18 quote).
    mex = re.search(
        r"Maternity(?: Expenses)?\s*[^.\n]{0,30}\(?(?:Code[- ]?)?Excl18\)?"
        r"[^.]{0,260}\.", t, re.I
    )
    if mex:
        return False, _clip(t, mex)
    return None, None


def x_initial_wait(t: str):
    m = re.search(
        r"(?:30|thirty)[ -]days?\b[^.]{0,140}?(?:Code[- ]?Excl03|Excl03|"
        r"from the (?:date of |)first policy commencement|within 30 days from "
        r"the first policy)[^.]{0,200}\.", t, re.I,
    ) or re.search(
        r"Excl03[^.]{0,40}(?:30|thirty)[ -]days?[^.]{0,200}\.", t, re.I
    )
    if m:
        return 30, _clip(t, m)
    return None, None


def x_ped_months(t: str):
    pats = [
        r"[Pp]re-?[Ee]xisting [Dd]iseases?[^.]{0,300}?expiry of\s+(\d{1,2})\s*"
        r"(?:\([^)]{0,18}\)\s*)?months",
        r"expiry of\s+(\d{1,2})\s*(?:\([^)]{0,18}\)\s*)?months[^.]{0,160}?"
        r"pre-?existing",
        r"[Pp]re-?[Ee]xisting [Dd]iseases?[^.]{0,260}?(\d{1,2})\s+months of "
        r"continuous coverage",
    ]
    for p in pats:
        m = re.search(p, t, re.I | re.S)
        if m:
            v = int(m.group(1))
            if 12 <= v <= 60:  # sanity: IRDAI PED is 12–48mo in practice
                return v, _clip(t, m)
    return None, None


EXTRACTORS = {
    "cashless_treatment_supported": (x_cashless, None, "high"),
    "ayush_coverage": (x_ayush, None, "high"),
    "maternity_coverage": (x_maternity, None, "high"),
    "initial_waiting_period_days": (x_initial_wait, "days", "high"),
    "pre_existing_disease_waiting_months": (x_ped_months, "months", "high"),
}


def main() -> int:
    write = "--write" in sys.argv
    changed_files = 0
    changed_fields = 0
    per_field = {}
    samples = []

    for f in sorted(FACTS_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        data = json.loads(f.read_text())
        sp = resolve_source_pdf(data)
        if not sp:
            continue
        t = text_for(sp)
        if not t:
            continue
        pname = data.get("policy_name", "") or ""
        file_changed = False
        for field, (fn, unit, conf) in EXTRACTORS.items():
            cur = data.get(field)
            if cur is not None and not _empty(cur):
                continue
            if field == "ayush_coverage":
                val, quote = fn(t, pname)
            else:
                val, quote = fn(t)
            if val is None:
                continue
            entry = {
                "value": val,
                "source_pdf_path": sp,
                "source_quote": quote[:600],
                "extraction_method": "tools/backfill_core_from_wording.py "
                "(deterministic IRDAI-standard clause match over pdfplumber text)",
                "_confidence": conf,
            }
            if unit:
                entry["unit"] = unit
            data[field] = entry
            file_changed = True
            changed_fields += 1
            per_field[field] = per_field.get(field, 0) + 1
            if len(samples) < 12:
                samples.append((f.name, field, val, quote[:140]))
        if file_changed:
            changed_files += 1
            if write:
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    print(("WROTE" if write else "DRY-RUN") +
          f": {changed_fields} fields across {changed_files} files")
    print("per field:", json.dumps(per_field, indent=1))
    print("\nsamples:")
    for fn, fld, v, q in samples:
        print(f"  [{fn}] {fld} = {v}")
        print(f"     “{q}”")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
