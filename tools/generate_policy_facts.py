"""Generate data/policy_facts/<policy_id>.json (marketplace cards) from
rag/extracted/<policy_id>.json (LLM-extracted structured fields).

The marketplace UI reads data/policy_facts/. Each card needs the wrapped-value
+ source-quote shape ({value, source_pdf_path, source_quote, unit}). We
convert from the flat HealthPolicy schema and wire the source_pdf_path from
the manifest's local_path field.

Existing hand-curated entries are preserved (we don't overwrite cards that
already exist with richer hand-research data). Only NEW policy_ids that
don't already have a card are generated.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "rag" / "extracted"
FACTS = ROOT / "data" / "policy_facts"
MANIFEST = ROOT / "rag" / "corpus" / "_manifest.json"

FACTS.mkdir(parents=True, exist_ok=True)

# Field-to-card mapping. Each entry: (extracted_field, card_field, unit).
# Card field gets wrapped as {value, source_pdf_path, source_quote, unit}.
FIELD_MAP = [
    ("uin_code",                              "uin_code",                              None),
    ("min_entry_age_years",                   "min_entry_age",                         "years"),
    ("max_entry_age_years",                   "max_entry_age",                         "years"),
    ("max_renewal_age_years",                 "max_renewal_age",                       "years"),
    ("min_child_entry_age_days",              "min_child_entry_age",                   "days"),
    ("sum_insured_options_inr",               "sum_insured_options",                   "INR"),
    ("grace_period_days",                     "grace_period",                          "days"),
    ("free_look_period_days",                 "free_look_period",                      "days"),
    ("initial_waiting_period_days",           "initial_waiting_period_days",           None),
    ("pre_existing_disease_waiting_months",   "pre_existing_disease_waiting_months",   None),
    ("specific_disease_waiting_months",       "specific_disease_waiting_months",       None),
    ("maternity_waiting_months",              "maternity_waiting_months",              None),
    ("pre_hospitalization_days",              "pre_hospitalization_days",              None),
    ("post_hospitalization_days",             "post_hospitalization_days",             None),
    ("day_care_treatments_count",             "day_care_treatments_count",             None),
    ("network_hospital_count",                "network_hospital_count",                None),
    ("co_payment_pct",                        "co_payment_pct",                        "percent"),
    ("room_rent_capped_at_pct_of_si",         "room_rent_capped_at_pct_of_si",         "percent"),
    ("policy_term_options_years",             "policy_term_options_years",             "years"),
    ("claim_settlement_ratio_pct",            "claim_settlement_ratio_pct",            "percent"),
    ("incurred_claim_ratio_pct",              "incurred_claim_ratio_pct",              "percent"),
    ("complaint_per_10k_claims",              "complaint_per_10k_claims",              None),
]


def manifest_index() -> dict[str, str]:
    """policy_id -> local_path mapping."""
    m = json.loads(MANIFEST.read_text())
    out = {}
    for r in m.get("results", []):
        lp = r.get("local_path", "")
        if not lp.endswith(".pdf"):
            continue
        from pathlib import PurePath
        pid = PurePath(lp).stem.replace("/", "__")
        # The convention: policy_id = "<insurer-slug>__<filename-stem>"
        slug = r.get("insurer_slug", "")
        if slug:
            pid = f"{slug}__{PurePath(lp).stem}"
        out[pid] = lp
    return out


def to_card_value(value, unit, pdf_path: str):
    """Wrap a flat value into the marketplace-card {value, unit, source_pdf_path, source_quote} shape."""
    if isinstance(value, dict):
        return {"value": value, "unit": unit, "source_pdf_path": pdf_path,
                "source_quote": "extracted from PDF policy document by NIM DeepSeek-V4 (D-019); see source PDF for verbatim"}
    return {"value": value, "unit": unit, "source_pdf_path": pdf_path,
            "source_quote": "extracted from PDF policy document by NIM DeepSeek-V4 (D-019); see source PDF for verbatim"}


def main():
    manifest = manifest_index()
    existing = {p.stem for p in FACTS.glob("*.json")}
    extracted = sorted(EXTRACTED.glob("*.json"))
    extracted = [p for p in extracted if "_raw" not in p.name
                 and ".old" not in p.name and "_backup" not in p.name]

    new = 0
    skipped_existing = 0
    skipped_no_pdf = 0

    for ext_path in extracted:
        pid = ext_path.stem
        if pid in existing:
            skipped_existing += 1
            continue
        pdf_path = manifest.get(pid, "")
        if not pdf_path:
            # Try a fuzzy match: extracted pid may include doc_type suffix
            for mp_pid in manifest:
                if pid.startswith(mp_pid) or mp_pid.startswith(pid):
                    pdf_path = manifest[mp_pid]
                    break

        try:
            ext_data = json.loads(ext_path.read_text())
        except Exception as e:
            print(f"  skip {pid} — parse error: {e}")
            continue

        card = {
            "policy_id": pid,
            "policy_name": ext_data.get("policy_name", pid.replace("__", " - ").replace("-", " ").title()),
            "insurer_slug": ext_data.get("insurer_slug", pid.split("__")[0]),
            "source_pdf_path": pdf_path,
        }
        for ext_field, card_field, unit in FIELD_MAP:
            v = ext_data.get(ext_field)
            if v not in (None, "", [], {}):
                card[card_field] = to_card_value(v, unit, pdf_path)
            else:
                card[card_field] = {"value": None, "unit": unit, "source_pdf_path": pdf_path,
                                    "source_quote": "not extracted from this PDF (field absent or LLM unable to infer)"}

        out_path = FACTS / f"{pid}.json"
        out_path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n")
        new += 1

    print(f"Generated {new} new policy_facts cards.")
    print(f"Skipped {skipped_existing} that already exist (hand-curated cards preserved).")
    print(f"  Total cards in data/policy_facts/: {len(list(FACTS.glob('*.json')))}")


if __name__ == "__main__":
    main()
