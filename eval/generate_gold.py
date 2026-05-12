"""Generate gold Q&A pairs (Pipeline A — auto from structured extraction).

For every successfully extracted policy in DuckDB, generate templated questions
whose answers come directly from the structured fields. Output to eval/gold_qa.json.

Run AFTER extraction:
  python -m eval.generate_gold
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import duckdb

from backend.config import settings

ROOT = settings.CORPUS_DIR.parent.parent
OUTPUT = ROOT / "eval" / "gold_qa.json"


# Question templates per field. Each tuple: (question_format, answer_format, type, difficulty).
# {pn} is replaced with policy_name, {v} with the field value.
QUESTION_TEMPLATES: dict[str, list[tuple[str, str, str, str]]] = {
    "pre_existing_disease_waiting_months": [
        ("What is the waiting period for pre-existing diseases under {pn}?", "{v} months from policy inception", "waiting_period", "easy"),
        ("If I have diabetes, how long do I have to wait before I can claim under {pn}?", "{v} months — pre-existing diseases have a waiting period of {v} months from policy start", "waiting_period", "medium"),
    ],
    "initial_waiting_period_days": [
        ("What is the initial waiting period under {pn}?", "{v} days from policy inception", "waiting_period", "easy"),
    ],
    "maternity_waiting_months": [
        ("What's the maternity benefit waiting period under {pn}?", "{v} months", "waiting_period", "easy"),
    ],
    "pre_hospitalization_days": [
        ("How many days of pre-hospitalization expenses does {pn} cover?", "{v} days", "coverage_scope", "easy"),
    ],
    "post_hospitalization_days": [
        ("How many days of post-hospitalization expenses does {pn} cover?", "{v} days", "coverage_scope", "easy"),
    ],
    "day_care_treatments_count": [
        ("How many day-care treatments are covered under {pn}?", "{v} day-care treatments", "coverage_scope", "easy"),
    ],
    "ayush_coverage": [
        ("Does {pn} cover AYUSH (Ayurveda, Yoga, Unani, Siddha, Homeopathy)?", "{v_yes_no}", "coverage_scope", "easy"),
    ],
    "no_claim_bonus_pct": [
        ("What's the no-claim bonus on {pn}?", "{v}% step-up on sum insured per claim-free year", "bonus", "easy"),
    ],
    "room_rent_capping": [
        ("Is there a cap on room rent under {pn}?", "{v}", "sub_limit", "medium"),
    ],
    "copayment_pct": [
        ("Is there a copayment under {pn}?", "{v_copay}", "sub_limit", "easy"),
    ],
    "network_hospital_count": [
        ("How many hospitals are in the {pn} cashless network?", "Approximately {v:,} hospitals", "network", "easy"),
    ],
    "max_renewal_age": [
        ("Up to what age can {pn} be renewed?", "{v_renewal}", "eligibility", "easy"),
    ],
    "min_entry_age": [
        ("What is the minimum entry age for {pn}?", "{v} years", "eligibility", "easy"),
    ],
    "max_entry_age": [
        ("What is the maximum entry age for {pn}?", "{v} years", "eligibility", "easy"),
    ],
}


# Adversarial questions appended for every policy — bot should refuse.
REFUSAL_TEMPLATES = [
    ("Does {pn} cover injuries from space tourism?", "expected_refusal", "exclusions_oos", "hard"),
    ("What is the maximum claim amount for diamond-tipped surgical procedures under {pn}?", "expected_refusal", "exclusions_oos", "hard"),
    ("What is the IRDAI mandate on dental coverage that {pn} must follow?", "expected_refusal", "regulatory_oos", "hard"),
]


def yes_no(v: Any) -> str:
    if v is True or v == "true" or v == 1:
        return "Yes"
    if v is False or v == "false" or v == 0:
        return "No"
    if isinstance(v, dict) and "covered" in v:
        return "Yes" if v["covered"] else "No"
    return str(v)


def copay_str(v: Any) -> str:
    try:
        v = float(v)
    except Exception:
        return str(v)
    if v <= 0:
        return "No copayment"
    return f"{v:.0f}% copayment applies"


def renewal_str(v: Any) -> str:
    try:
        n = int(v)
        if n >= 100:
            return "Lifelong renewability"
        return f"Up to age {n}"
    except Exception:
        return str(v)


def format_answer(template: str, v: Any) -> str:
    out = template
    out = out.replace("{v_yes_no}", yes_no(v))
    out = out.replace("{v_copay}", copay_str(v))
    out = out.replace("{v_renewal}", renewal_str(v))
    if "{v:,}" in out:
        try:
            out = out.replace("{v:,}", f"{int(v):,}")
        except Exception:
            out = out.replace("{v:,}", str(v))
    out = out.replace("{v}", str(v))
    return out


def load_policies() -> list[dict]:
    if not settings.STRUCTURED_DB.exists():
        return []
    con = duckdb.connect(str(settings.STRUCTURED_DB), read_only=True)
    rows = con.execute("SELECT policy_id, policy_name, data_json FROM policies").fetchall()
    con.close()
    out = []
    for pid, pname, data in rows:
        try:
            d = json.loads(data)
            d["_policy_id"] = pid
            d["_policy_name"] = pname or d.get("policy_name", pid)
            out.append(d)
        except Exception:
            continue
    return out


def is_present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and v.strip() == "":
        return False
    if isinstance(v, list) and len(v) == 0:
        return False
    if isinstance(v, dict):
        # Pydantic CoverageItem dict — present if covered is set
        return v.get("covered") is not None
    return True


def extract_value_for_template(v: Any) -> Any:
    """Some fields are CoverageItem dicts; pull a representative value."""
    if isinstance(v, dict) and "covered" in v:
        return v["covered"]  # used by yes_no formatter
    return v


def generate() -> list[dict]:
    policies = load_policies()
    gold: list[dict] = []

    for p in policies:
        pid = p["_policy_id"]
        pname = p["_policy_name"]

        for field, templates in QUESTION_TEMPLATES.items():
            raw_v = p.get(field)
            if not is_present(raw_v):
                continue
            v = extract_value_for_template(raw_v)

            for question_fmt, answer_fmt, qtype, difficulty in templates:
                answer = format_answer(answer_fmt, v)
                if not answer.strip():
                    continue
                gold.append({
                    "id": f"{pid}::{field}::{difficulty}",
                    "policy_id": pid,
                    "question": question_fmt.format(pn=pname),
                    "expected_answer": answer,
                    "question_type": qtype,
                    "difficulty": difficulty,
                    "expected_refusal": False,
                    "language": "en",
                    "generated_by": "pipeline_a",
                    "source_field": field,
                })

        for question_fmt, marker, qtype, difficulty in REFUSAL_TEMPLATES:
            gold.append({
                "id": f"{pid}::REFUSE::{qtype}::{difficulty}",
                "policy_id": pid,
                "question": question_fmt.format(pn=pname),
                "expected_answer": "Bot should refuse or say not in document.",
                "question_type": qtype,
                "difficulty": difficulty,
                "expected_refusal": True,
                "language": "en",
                "generated_by": "pipeline_c_refusal",
            })

    return gold


def main():
    gold = generate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(gold, indent=2))
    print(f"Wrote {len(gold)} gold Q&A pairs to {OUTPUT.relative_to(ROOT)}")
    # Quick breakdown
    by_policy: dict[str, int] = {}
    by_type: dict[str, int] = {}
    refusal_count = 0
    for g in gold:
        by_policy[g["policy_id"]] = by_policy.get(g["policy_id"], 0) + 1
        by_type[g["question_type"]] = by_type.get(g["question_type"], 0) + 1
        if g["expected_refusal"]:
            refusal_count += 1
    print(f"Policies covered: {len(by_policy)}")
    print(f"Refusal questions: {refusal_count}")
    print("By type:")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {t:>25s}: {n}")


if __name__ == "__main__":
    main()
