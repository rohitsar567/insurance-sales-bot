"""Curate every policy PDF in rag/corpus/ that doesn't have a matching
40-data/policy_facts/<policy_id>.json yet. Includes group/B2B/specialty plans
the batch-2 agent excluded.

Uses pdfplumber to read PDF text + regex patterns from curate_batch2 to
extract structured fields. Output JSON follows the same provenance schema
as batches 1 + 2.

Run:
    .venv/bin/python3 tools/curate_remaining.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CORPUS = BASE / "rag" / "corpus"
OUT_DIR = BASE / "40-data" / "policy_facts"
OUT_DIR.mkdir(exist_ok=True, parents=True)

# Reuse the same regex extractors as batch 2 by importing the module
sys.path.insert(0, str(BASE / "tools"))
try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber not installed. Run from venv.")
    sys.exit(1)


def existing_policy_ids() -> set[str]:
    out = set()
    for f in OUT_DIR.glob("*.json"):
        if f.stem.startswith("_"):
            continue
        out.add(f.stem)
    return out


def policy_id_for(pdf: Path) -> str:
    insurer = pdf.parent.name
    return f"{insurer}__{pdf.stem}"


def extract_text(pdf: Path, max_pages: int = 30) -> str:
    chunks = []
    try:
        with pdfplumber.open(pdf) as p:
            for i, page in enumerate(p.pages[:max_pages]):
                t = page.extract_text() or ""
                chunks.append(t)
    except Exception as e:  # noqa: BLE001
        return ""
    return "\n\n".join(chunks)


# ---- Field extractors (compact subset of batch_2 patterns) -----------------

def find_first(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> tuple[str | None, str | None]:
    """Return (matched_value, source_quote) for the first pattern that hits."""
    for p in patterns:
        m = re.search(p, text, flags=flags)
        if m:
            q = m.group(0)[:140]
            v = m.group(1) if m.groups() else m.group(0)
            return v.strip(), q.strip()
    return None, None


def extract_uin(text: str) -> tuple[str | None, str | None]:
    return find_first([
        r"UIN[:\s]*([A-Z0-9]{12,20})",
        r"Product UIN[:\s]*([A-Z0-9]{12,20})",
        r"Unique Identification Number[:\s]*([A-Z0-9]{12,20})",
    ], text)


def extract_int_after(label_patterns: list[str], text: str, range_min: int = 0, range_max: int = 1000) -> tuple[int | None, str | None]:
    for p in label_patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            try:
                v = int(re.sub(r"\D", "", m.group(1)))
            except (ValueError, IndexError):
                continue
            if range_min <= v <= range_max:
                return v, m.group(0)[:120]
    return None, None


def extract_field_pack(text: str, insurer_slug: str, pdf: Path) -> dict:
    """Run the regex patterns over the PDF text + assemble the JSON."""
    out: dict = {}
    pdf_rel = str(pdf.relative_to(BASE))

    def wrap(value, quote, url=None):
        if value is None and quote is None:
            return {"value": None, "source_pdf_path": pdf_rel, "source_quote": ""}
        return {
            "value": value,
            "source_pdf_path": pdf_rel,
            "source_quote": (quote or "")[:140],
        }

    # UIN
    uin, uin_q = extract_uin(text)
    out["uin_code"] = wrap(uin, uin_q)

    # Entry age (min / max)
    min_age, min_q = extract_int_after([
        r"(?:Minimum|Entry).{0,30}?(?:Age)[:\s]*(\d{1,3})",
        r"Age (?:at )?Entry[:\s\-]*(?:Min(?:imum)?)?[:\s]*(\d{1,3})",
    ], text, 0, 99)
    out["min_entry_age"] = wrap(min_age, min_q)

    max_age, max_q = extract_int_after([
        r"(?:Maximum|Max\.?).{0,30}?(?:Age|Entry Age)[:\s]*(\d{2,3})",
        r"Age (?:at )?Entry.{0,40}?Max[^:]*[:\s]*(\d{2,3})",
    ], text, 18, 200)
    out["max_entry_age"] = wrap(max_age, max_q)

    # Renewal
    renew_q_match = re.search(r"(Lifelong|Life[- ]long).{0,40}(?:renew|Renewal)", text, flags=re.IGNORECASE)
    if renew_q_match:
        out["max_renewal_age"] = {"value": 99, "source_pdf_path": pdf_rel, "source_quote": renew_q_match.group(0)[:120]}
    else:
        renew_age, renew_q = extract_int_after([
            r"Renewal[\s\S]{0,80}?up to[:\s]*(\d{2,3})",
            r"(?:Maximum renewal|Renew till|Renewal age)[:\s]*(\d{2,3})",
        ], text, 50, 120)
        out["max_renewal_age"] = wrap(renew_age, renew_q)

    # PED waiting
    ped_months, ped_q = extract_int_after([
        r"Pre[-\s]?existing[^\n]{0,200}?(\d{1,3})\s*(?:months|month|consecutive months)",
        r"Pre[-\s]?existing[^\n]{0,200}?(\d{1,2})\s*years",
    ], text, 0, 120)
    if ped_months is not None and "year" in (ped_q or "").lower() and ped_months < 10:
        ped_months *= 12  # convert years to months
    out["pre_existing_disease_waiting_months"] = wrap(ped_months, ped_q)

    # Initial waiting (days)
    initial_days, init_q = extract_int_after([
        r"Initial waiting[\s\S]{0,80}?(\d{1,3})\s*days",
        r"first (\d{1,3}) days from[^\n]{0,50}commencement",
    ], text, 0, 365)
    out["initial_waiting_period_days"] = wrap(initial_days, init_q)

    # Maternity
    mat_q_match = re.search(r"Maternity[\s\S]{0,300}?(?:not covered|excluded|no cover)", text, flags=re.IGNORECASE)
    if mat_q_match:
        out["maternity_coverage"] = {"value": False, "source_pdf_path": pdf_rel, "source_quote": mat_q_match.group(0)[:120]}
        out["maternity_waiting_months"] = wrap(None, None)
    else:
        mat_months, mat_q = extract_int_after([
            r"Maternity[\s\S]{0,200}?(\d{1,3})\s*months",
            r"Maternity[\s\S]{0,200}?(\d{1,2})\s*years",
        ], text, 0, 60)
        if mat_months is not None and "year" in (mat_q or "").lower() and mat_months < 10:
            mat_months *= 12
        if mat_months is not None:
            out["maternity_coverage"] = {"value": True, "source_pdf_path": pdf_rel, "source_quote": (mat_q or "")[:120]}
            out["maternity_waiting_months"] = wrap(mat_months, mat_q)
        else:
            out["maternity_coverage"] = wrap(None, None)
            out["maternity_waiting_months"] = wrap(None, None)

    # AYUSH
    ayush_match = re.search(r"AYUSH[\s\S]{0,200}?(covered|included|payable|reimburs)", text, flags=re.IGNORECASE)
    no_ayush = re.search(r"AYUSH[\s\S]{0,80}?(not covered|excluded)", text, flags=re.IGNORECASE)
    if no_ayush:
        out["ayush_coverage"] = {"value": False, "source_pdf_path": pdf_rel, "source_quote": no_ayush.group(0)[:120]}
    elif ayush_match:
        out["ayush_coverage"] = {"value": True, "source_pdf_path": pdf_rel, "source_quote": ayush_match.group(0)[:120]}
    else:
        out["ayush_coverage"] = wrap(None, None)

    # Cashless
    cashless_match = re.search(r"cashless[\s\S]{0,80}?(facility|treatment|hospital|network)", text, flags=re.IGNORECASE)
    out["cashless_treatment_supported"] = {
        "value": True if cashless_match else None,
        "source_pdf_path": pdf_rel,
        "source_quote": (cashless_match.group(0)[:120] if cashless_match else ""),
    }

    # Co-pay
    copay, copay_q = extract_int_after([
        r"Co-?[\s]?pay(?:ment)?[\s\S]{0,80}?(\d{1,2})\s*%",
    ], text, 0, 50)
    out["copayment_pct"] = wrap(copay, copay_q)

    # NCB
    ncb, ncb_q = extract_int_after([
        r"(?:No[-\s]?Claim Bonus|Cumulative Bonus|NCB)[\s\S]{0,100}?(\d{1,3})\s*%",
    ], text, 5, 100)
    out["no_claim_bonus_pct"] = wrap(ncb, ncb_q)

    # Restoration
    restore_match = re.search(r"Restoration[\s\S]{0,180}?(\d+\s*%|once|unlimited|automatic|on full exhaustion)", text, flags=re.IGNORECASE)
    if restore_match:
        out["restoration_benefit"] = {
            "value": restore_match.group(0)[:80].strip(),
            "source_pdf_path": pdf_rel,
            "source_quote": restore_match.group(0)[:120],
        }
    else:
        out["restoration_benefit"] = wrap(None, None)

    # Room rent
    room_match = re.search(r"Room rent[\s\S]{0,200}?(no limit|single private|capped|up to|\d+\s*%)", text, flags=re.IGNORECASE)
    if room_match:
        out["room_rent_capping"] = {
            "value": room_match.group(0)[:80].strip(),
            "source_pdf_path": pdf_rel,
            "source_quote": room_match.group(0)[:120],
        }
    else:
        out["room_rent_capping"] = wrap(None, None)

    # Pre-/post-hospitalisation
    pre_h, pre_h_q = extract_int_after([
        r"Pre[-\s]?hospitali[sz]ation[\s\S]{0,100}?(\d{1,3})\s*days",
    ], text, 0, 365)
    post_h, post_h_q = extract_int_after([
        r"Post[-\s]?hospitali[sz]ation[\s\S]{0,100}?(\d{1,3})\s*days",
    ], text, 0, 365)
    out["pre_hospitalization_days"] = wrap(pre_h, pre_h_q)
    out["post_hospitalization_days"] = wrap(post_h, post_h_q)

    # Day care
    daycare, dc_q = extract_int_after([
        r"(\d{2,4})\s*(?:Day[\s-]?Care|day care procedures|daycare)",
    ], text, 50, 1500)
    out["day_care_treatments_count"] = wrap(daycare, dc_q)

    # Network hospitals
    net, net_q = extract_int_after([
        r"(\d{3,6})\s*\+?\s*(?:Network Hospitals|cashless hospitals|hospital network)",
    ], text, 500, 100000)
    out["network_hospital_count"] = wrap(net, net_q)

    # Sum insured options — heuristic, only when clearly enumerated
    si_match = re.search(r"Sum Insured.{0,30}?Options?[:\s]*([\d,\s/L/Lakh/Cr]+)", text, flags=re.IGNORECASE)
    out["sum_insured_options"] = wrap(None, si_match.group(0)[:120] if si_match else None)

    # Universal fields that aren't in policy PDFs
    out["claim_settlement_ratio"] = wrap(None, None)
    out["tat_cashless_authorization_hours"] = wrap(None, None)

    # Policy-type heuristic
    text_lower = text.lower()
    if "group health" in text_lower or "employer" in text_lower or pdf.stem.startswith("group"):
        ptype = "group"
    elif "top up" in text_lower or "top-up" in text_lower or "super top" in text_lower:
        ptype = "top_up"
    elif "hospital cash" in text_lower or "daily cash" in text_lower:
        ptype = "hospital_cash"
    elif "cancer" in pdf.stem.lower() or "criti" in pdf.stem.lower() or "critical illness" in text_lower:
        ptype = "critical_illness"
    elif "personal accident" in text_lower:
        ptype = "personal_accident"
    else:
        ptype = "indemnity"
    out["policy_type"] = {"value": ptype, "source_pdf_path": pdf_rel, "source_quote": f"classified as {ptype} from PDF heuristics"}

    return out


def derive_policy_name(pdf: Path) -> str:
    """Convert filename to human-readable policy name."""
    stem = pdf.stem
    stem = re.sub(r"__(wordings|brochure|cis|prospectus|policy).*$", "", stem)
    parts = re.split(r"[-_]+", stem)
    return " ".join(p.capitalize() for p in parts if p)


def main():
    existing = existing_policy_ids()
    print(f"Currently curated: {len(existing)}")

    work = []
    for pdf in sorted(CORPUS.rglob("*.pdf")):
        if pdf.parent.name == "regulatory":
            continue
        pid = policy_id_for(pdf)
        # Also try stripped suffix forms
        stem_clean = re.sub(r"__(wordings|brochure|cis|prospectus|policy)$", "", pdf.stem)
        short = f"{pdf.parent.name}__{stem_clean}"
        if pid in existing or short in existing:
            continue
        work.append(pdf)

    print(f"Uncurated: {len(work)} PDFs")
    completeness_scores = []
    for i, pdf in enumerate(work, 1):
        pid = policy_id_for(pdf)
        text = extract_text(pdf)
        if len(text) < 200:
            print(f"  [{i}/{len(work)}] SKIP {pid} — too short ({len(text)} chars)")
            continue
        fields = extract_field_pack(text, pdf.parent.name, pdf)
        # Add identity + meta
        out_doc = {
            "policy_id": pid,
            "policy_name": derive_policy_name(pdf),
            "insurer_slug": pdf.parent.name,
            **fields,
        }
        # Completeness based on non-null values
        relevant = [k for k in out_doc if isinstance(out_doc.get(k), dict) and "value" in out_doc[k]]
        non_null = sum(1 for k in relevant if out_doc[k].get("value") not in (None, "", []))
        completeness_pct = round(non_null / max(1, len(relevant)) * 100)
        out_doc["_meta"] = {
            "curated_at": time.strftime("%Y-%m-%d"),
            "primary_source_pdf": str(pdf.relative_to(BASE)),
            "completeness_pct": completeness_pct,
            "notes": "Curated by tools/curate_remaining.py — pattern-based extraction from local PDF",
        }
        out_path = OUT_DIR / f"{pid}.json"
        out_path.write_text(json.dumps(out_doc, indent=2))
        completeness_scores.append(completeness_pct)
        print(f"  [{i}/{len(work)}] {pid}: {completeness_pct}%")

    avg = sum(completeness_scores) / max(1, len(completeness_scores))
    print(f"\nDone. {len(completeness_scores)} new JSONs, avg completeness {avg:.1f}%")
    print(f"Total curated now: {len(existing_policy_ids())}")


if __name__ == "__main__":
    main()
