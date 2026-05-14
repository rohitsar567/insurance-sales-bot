#!/usr/bin/env python3
"""Mirror today's data + design work into kb/.

Reads:
  - data/policy_facts/*.json  -> kb/policies/<id>.md (yaml frontmatter + per-field MD)
  - backend.scorecard METHODOLOGY_BLUEPRINT / WEIGHTS / SCORED_FIELDS -> kb/methodology/scorecard.json
  - frontend/src/lib/i18n.ts GLOSSARY (hand-mirrored)                  -> kb/methodology/glossary.json
  - 70-docs/discovery-script.md                                            -> kb/methodology/discovery-script.md
  - 70-docs/scorecard-knowledge-graph.md                                   -> kb/methodology/knowledge-graph.md
  - 70-docs/tie-breaker-rubric.md                                          -> kb/methodology/tie-breakers.md

Rewrites:
  - kb/INDEX.md  (top-level index with policies table + methodology links)

Appends to:
  - kb/AUDIT_TRAIL.md ("Batch — 2026-05-14" section)

Run from project root:
    .venv/bin/python3 tools/build_kb_mirror.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "kb"
POLICIES_OUT = KB / "policies"
METHOD_OUT = KB / "methodology"
DATA_IN = ROOT / "data" / "policy_facts"
DOCS = ROOT / "docs"

POLICIES_OUT.mkdir(parents=True, exist_ok=True)
METHOD_OUT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Insurer slug -> human-readable name
# ---------------------------------------------------------------------------
INSURER_NAMES = {
    "aditya-birla": "Aditya Birla Health Insurance",
    "bajaj-allianz": "Bajaj Allianz General Insurance",
    "care-health": "Care Health Insurance",
    "hdfc-ergo": "HDFC ERGO General Insurance",
    "icici-lombard": "ICICI Lombard General Insurance",
    "manipalcigna": "ManipalCigna Health Insurance",
    "new-india": "The New India Assurance Co.",
    "niva-bupa": "Niva Bupa Health Insurance",
    "star-health": "Star Health and Allied Insurance",
    "tata-aig": "Tata AIG General Insurance",
}


# ---------------------------------------------------------------------------
# 2. Field ordering: identity -> eligibility -> waiting -> coverage ->
#    cost-share -> claims -> bonuses. Any unclassified fields fall through
#    into "Other fields" (alphabetical).
# ---------------------------------------------------------------------------
FIELD_GROUPS = [
    ("Identity", [
        "uin_code",
        "policy_type",
    ]),
    ("Eligibility", [
        "min_entry_age",
        "max_entry_age",
        "max_renewal_age",
        "sum_insured_options",
    ]),
    ("Waiting periods", [
        "initial_waiting_period_days",
        "pre_existing_disease_waiting_months",
        "specific_disease_waiting_months",
        "maternity_waiting_months",
    ]),
    ("Coverage scope", [
        "pre_hospitalization_days",
        "post_hospitalization_days",
        "day_care_treatments_count",
        "ayush_coverage",
        "maternity_coverage",
        "newborn_coverage",
        "organ_donor_expenses",
        "ambulance_cover",
        "domiciliary_treatment",
        "preventive_health_checkup",
        "critical_illness_cover",
        "worldwide_emergency_cover",
        "restoration_benefit",
        "room_rent_capping",
    ]),
    ("Cost-share", [
        "copayment_pct",
        "deductible_amount",
        "sub_limits",
    ]),
    ("Claims & service", [
        "network_hospital_count",
        "cashless_treatment_supported",
        "claim_settlement_ratio",
        "tat_cashless_authorization_hours",
        "claim_process_summary",
    ]),
    ("Bonuses & loyalty", [
        "no_claim_bonus_pct",
        "wellness_program",
    ]),
]

FIELD_TITLES = {
    "uin_code": "UIN code",
    "policy_type": "Policy type",
    "min_entry_age": "Minimum entry age",
    "max_entry_age": "Maximum entry age",
    "max_renewal_age": "Maximum renewal age",
    "sum_insured_options": "Sum insured options",
    "initial_waiting_period_days": "Initial waiting period (days)",
    "pre_existing_disease_waiting_months": "Pre-existing disease waiting (months)",
    "specific_disease_waiting_months": "Specific disease waiting (months)",
    "maternity_waiting_months": "Maternity waiting (months)",
    "pre_hospitalization_days": "Pre-hospitalization (days)",
    "post_hospitalization_days": "Post-hospitalization (days)",
    "day_care_treatments_count": "Day-care treatments covered",
    "ayush_coverage": "AYUSH coverage",
    "maternity_coverage": "Maternity coverage",
    "newborn_coverage": "Newborn coverage",
    "organ_donor_expenses": "Organ donor expenses",
    "ambulance_cover": "Ambulance cover",
    "domiciliary_treatment": "Domiciliary treatment",
    "preventive_health_checkup": "Preventive health checkup",
    "critical_illness_cover": "Critical illness cover",
    "worldwide_emergency_cover": "Worldwide emergency cover",
    "restoration_benefit": "Restoration benefit",
    "room_rent_capping": "Room rent capping",
    "copayment_pct": "Co-payment (%)",
    "deductible_amount": "Deductible",
    "sub_limits": "Sub-limits",
    "network_hospital_count": "Network hospital count",
    "cashless_treatment_supported": "Cashless treatment supported",
    "claim_settlement_ratio": "Claim settlement ratio",
    "tat_cashless_authorization_hours": "Cashless TAT (hours)",
    "claim_process_summary": "Claim process summary",
    "no_claim_bonus_pct": "No-claim bonus (%)",
    "wellness_program": "Wellness program",
}


def fmt_value(field: dict) -> str:
    """Render a {value, unit?, ...} triple into a human-readable scalar."""
    if not isinstance(field, dict):
        return "_n/a_"
    v = field.get("value")
    unit = field.get("unit")
    if v is None or v == "":
        return "_not specified_"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if unit:
        return f"{v} {unit}"
    return str(v)


def render_field(field_key: str, field: dict) -> str:
    title = FIELD_TITLES.get(field_key, field_key.replace("_", " ").title())
    if not isinstance(field, dict):
        return f"### {title}\n\n_no data_\n"
    value_md = fmt_value(field)
    quote = (field.get("source_quote") or "").strip()
    pdf = field.get("source_pdf_path") or field.get("source_url") or ""
    quote_block = f"> {quote}" if quote else "> _(no verbatim quote on record)_"
    src = f"`{pdf}`" if pdf else "_(no source path on record)_"
    return (
        f"### {title}\n\n"
        f"**Value:** {value_md}\n\n"
        f"**Source quote:**\n\n{quote_block}\n\n"
        f"**Source:** {src}\n"
    )


def render_policy_md(p: dict, source_json_path: Path) -> str:
    pid = p.get("policy_id") or source_json_path.stem
    insurer_slug = p.get("insurer_slug") or pid.split("__", 1)[0]
    insurer_name = INSURER_NAMES.get(insurer_slug, insurer_slug)
    policy_name = p.get("policy_name") or pid

    uin = ""
    if isinstance(p.get("uin_code"), dict):
        uin = p["uin_code"].get("value") or ""

    meta = p.get("_meta", {}) if isinstance(p.get("_meta"), dict) else {}
    primary_pdf = meta.get("primary_source_pdf") or ""
    completeness = meta.get("completeness_pct")
    curated_at = meta.get("curated_at") or ""
    notes = meta.get("notes") or ""

    lines: list[str] = []
    # YAML frontmatter
    lines.append("---")
    lines.append(f"policy_id: {pid}")
    lines.append(f"insurer_slug: {insurer_slug}")
    lines.append(f"insurer_name: {insurer_name}")
    lines.append(f"policy_name: {json.dumps(policy_name, ensure_ascii=False)}")
    if uin:
        lines.append(f"uin_code: {uin}")
    if primary_pdf:
        lines.append(f"source_pdf_path: {primary_pdf}")
    if completeness is not None:
        lines.append(f"completeness_pct: {completeness}")
    if curated_at:
        lines.append(f"curated_at: {curated_at}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {policy_name}")
    lines.append("")

    # Header block
    header = (
        f"**Insurer:** {insurer_name} (`{insurer_slug}`)  \n"
        f"**Policy ID:** `{pid}`"
    )
    if uin:
        header += f"  \n**UIN:** `{uin}`"
    if completeness is not None:
        header += f"  \n**Curation completeness:** {completeness}%"
    if primary_pdf:
        header += f"  \n**Primary source PDF:** `{primary_pdf}`"
    if curated_at:
        header += f"  \n**Curated at:** {curated_at}"
    lines.append(header)
    if notes:
        lines.append("")
        lines.append(f"> _Curation note: {notes}_")
    lines.append("")

    # Render grouped sections (skip empty groups)
    rendered_keys: set[str] = set()
    for grp_name, grp_fields in FIELD_GROUPS:
        section_blocks: list[str] = []
        for fkey in grp_fields:
            if fkey in p:
                section_blocks.append(render_field(fkey, p[fkey]))
                rendered_keys.add(fkey)
        if not section_blocks:
            continue
        lines.append(f"## {grp_name}")
        lines.append("")
        lines.append("\n".join(section_blocks))

    # Anything else that's in the JSON but not in a group
    remaining = [
        k for k in p.keys()
        if k not in rendered_keys
        and not k.startswith("_")
        and k not in ("policy_id", "policy_name", "insurer_slug")
    ]
    if remaining:
        lines.append("## Other fields")
        lines.append("")
        for fkey in sorted(remaining):
            lines.append(render_field(fkey, p[fkey]))

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"_Mirrored from `data/policy_facts/{source_json_path.name}`. "
        "Provenance — every field's verbatim quote and source PDF path is "
        "preserved exactly as curated. Do not hand-edit; regenerate via "
        "`tools/build_kb_mirror.py`._"
    )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 3. GLOSSARY (mirror of frontend/src/lib/i18n.ts) — 13 terms × {en, hi}
# ---------------------------------------------------------------------------
GLOSSARY = {
    "PED": {
        "en": {
            "title": "Pre-Existing Disease (PED)",
            "body": "A health condition you already have when you buy the policy — diabetes, BP, thyroid, anything chronic. Most policies don't cover it for the first 24-48 months. Be honest about yours: hiding it gets your claim denied later.",
        },
        "hi": {
            "title": "Pre-Existing Disease (पहले से चली आ रही बीमारी)",
            "body": "जो बीमारी आपको policy खरीदते समय पहले से है — diabetes, BP, थायरॉइड etc. ज़्यादातर policies शुरू के 24-48 महीनों में cover नहीं करतीं। ईमानदारी से बताइए, छिपाने से claim बाद में reject हो जाता है।",
        },
    },
    "AYUSH": {
        "en": {
            "title": "AYUSH coverage",
            "body": "Whether the policy pays for Ayurveda, Yoga, Unani, Siddha, and Homeopathy treatments at recognised hospitals. If you use these traditional systems, this matters; if you only use allopathic care, less so.",
        },
        "hi": {
            "title": "AYUSH कवर",
            "body": "क्या policy आयुर्वेद, योग, यूनानी, सिद्ध, और होम्योपैथी treatments को cover करती है। अगर आप इन पारंपरिक चिकित्सा का उपयोग करते हैं, यह ज़रूरी है।",
        },
    },
    "NCB": {
        "en": {
            "title": "No-Claim Bonus (NCB)",
            "body": "Reward for not claiming in a year — your sum insured goes up (typically 25-50%) without raising your premium. Bigger NCB compounds over years if you stay claim-free.",
        },
        "hi": {
            "title": "No-Claim Bonus (NCB)",
            "body": "बिना claim किए साल पूरा करने का इनाम — sum insured बढ़ जाता है (आम तौर पर 25-50%) बिना premium बढ़ाए।",
        },
    },
    "SI": {
        "en": {
            "title": "Sum Insured (SI)",
            "body": "The maximum amount the insurer pays in a policy year. For a single hospitalisation in a metro, ₹10L is the floor; ₹20L+ is safer if you have parents or family to cover.",
        },
        "hi": {
            "title": "Sum Insured (बीमित राशि)",
            "body": "एक policy साल में बीमाकर्ता अधिकतम कितना देगा। Metro में एक hospitalisation के लिए ₹10L न्यूनतम; ₹20L+ माता-पिता या परिवार के लिए सुरक्षित।",
        },
    },
    "CSR": {
        "en": {
            "title": "Claim Settlement Ratio (CSR)",
            "body": "Of every 100 claims the insurer received, how many they paid. IRDAI publishes this annually. <90% = caution; 95%+ = excellent. Single most predictive metric of 'will my claim get paid'.",
        },
        "hi": {
            "title": "Claim Settlement Ratio",
            "body": "100 claims में से बीमाकर्ता कितने pay करता है। IRDAI सालाना publish करता है। <90% = सावधान; 95%+ = बढ़िया।",
        },
    },
    "Cashless": {
        "en": {
            "title": "Cashless treatment",
            "body": "You don't pay the hospital — the insurer pays them directly via a pre-authorisation. Only works at network hospitals. Without it, you pay upfront and file for reimbursement later.",
        },
        "hi": {
            "title": "Cashless इलाज",
            "body": "आप hospital को सीधे payment नहीं करते — बीमाकर्ता pre-authorisation से payment करता है। सिर्फ network hospitals पर काम करता है।",
        },
    },
    "TAT": {
        "en": {
            "title": "Cashless TAT (Turnaround Time)",
            "body": "How fast the insurer approves your cashless pre-auth at the hospital desk. ≤2 hours = gold standard; ≥24h = your family pays cash first and waits for reimbursement.",
        },
        "hi": {
            "title": "Cashless TAT",
            "body": "बीमाकर्ता hospital में cashless approval कितनी जल्दी देता है। ≤2 घंटे = बढ़िया; ≥24 घंटे = परिवार को पहले cash देना पड़ेगा।",
        },
    },
    "UIN": {
        "en": {
            "title": "Unique Identification Number (UIN)",
            "body": "IRDAI-assigned ID for each policy product — proves it's a regulator-approved plan. You can search a UIN on irdai.gov.in to verify the policy exists and see its filed terms.",
        },
        "hi": {
            "title": "UIN (Unique ID)",
            "body": "IRDAI द्वारा हर policy को दिया गया ID — यह साबित करता है कि policy regulator से approved है।",
        },
    },
    "CoPay": {
        "en": {
            "title": "Co-payment",
            "body": "The % of every claim YOU pay out of pocket. 20% co-pay on a ₹5L hospital bill = you pay ₹1L; insurer pays ₹4L. Lower premium upfront, but bigger surprise at claim time.",
        },
        "hi": {
            "title": "Co-payment",
            "body": "हर claim का जो % आप अपनी जेब से देते हैं। ₹5L hospital bill पर 20% co-pay = आप ₹1L दें, बीमाकर्ता ₹4L।",
        },
    },
    "Deductible": {
        "en": {
            "title": "Deductible",
            "body": "Fixed rupee amount you pay BEFORE the insurer starts paying. ₹50k deductible = first ₹50k of every claim is on you. Reduces premium significantly but adds out-of-pocket risk.",
        },
        "hi": {
            "title": "Deductible",
            "body": "वो fixed amount जो आप बीमाकर्ता के payment शुरू करने से पहले देते हैं।",
        },
    },
    "Floater": {
        "en": {
            "title": "Family Floater",
            "body": "One sum insured shared by everyone in the family. ₹15L floater for 4 people = anyone (or everyone) can use up to ₹15L combined. Cheaper than individual policies if claims are rare.",
        },
        "hi": {
            "title": "Family Floater",
            "body": "एक sum insured पूरे परिवार के लिए share होती है। 4 लोगों के लिए ₹15L floater = कोई भी ₹15L तक use कर सकता है।",
        },
    },
    "SubLimit": {
        "en": {
            "title": "Sub-limit",
            "body": "A cap WITHIN your sum insured for a specific treatment — e.g., room rent capped at 1% of SI, or maternity capped at ₹50k. Watch for these — they're the #1 reason actual reimbursement < bill.",
        },
        "hi": {
            "title": "Sub-limit",
            "body": "Sum insured के अंदर कुछ खास treatments पर एक सीमा — जैसे room rent SI का 1%, या maternity ₹50k तक। यह सबसे बड़ी वजह है कि real payment bill से कम होता है।",
        },
    },
    "RoomRent": {
        "en": {
            "title": "Room rent capping",
            "body": "Some policies pay only up to a % of SI per day of hospital room — e.g., 1% of ₹5L = ₹5k/day. Choose a more expensive room and ALL your other charges get scaled down proportionally. Look for 'No room rent limit'.",
        },
        "hi": {
            "title": "Room rent capping",
            "body": "कई policies hospital room के लिए सिर्फ SI का % देती हैं — जैसे 1% का ₹5L = ₹5k/दिन। महंगा कमरा लें तो सभी अन्य charges भी scale down हो जाते हैं।",
        },
    },
}


# ---------------------------------------------------------------------------
# 4. Drive
# ---------------------------------------------------------------------------
def main() -> int:
    sys.path.insert(0, str(ROOT))
    from backend.scorecard import (  # type: ignore
        METHODOLOGY_BLUEPRINT,
        WEIGHTS,
        SCORED_FIELDS,
    )

    new_files = 0
    updated_files = 0

    # 4a. methodology/scorecard.json
    method_path = METHOD_OUT / "scorecard.json"
    existed_method = method_path.exists()
    method_path.write_text(
        json.dumps(
            {
                "weights": WEIGHTS,
                "scored_fields": SCORED_FIELDS,
                "methodology": METHODOLOGY_BLUEPRINT,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if existed_method:
        updated_files += 1
    else:
        new_files += 1

    # 4b. methodology/glossary.json
    gloss_path = METHOD_OUT / "glossary.json"
    existed_gloss = gloss_path.exists()
    gloss_path.write_text(
        json.dumps(GLOSSARY, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if existed_gloss:
        updated_files += 1
    else:
        new_files += 1

    # 4c. verbatim copies of three 70-docs/*.md files
    copy_map = {
        "discovery-script.md": "discovery-script.md",
        "scorecard-knowledge-graph.md": "knowledge-graph.md",
        "tie-breaker-rubric.md": "tie-breakers.md",
    }
    for src_name, dest_name in copy_map.items():
        src = DOCS / src_name
        dest = METHOD_OUT / dest_name
        if src.exists():
            existed = dest.exists()
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            if existed:
                updated_files += 1
            else:
                new_files += 1

    # 4d. one MD per data/policy_facts/*.json
    index_rows: list[tuple[str, str, str, str, str]] = []  # insurer, name, uin, completeness, kb path
    written = 0
    skipped = 0
    written_pids: set[str] = set()
    for j in sorted(DATA_IN.glob("*.json")):
        if j.name.startswith("_"):
            continue
        try:
            data = json.loads(j.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"SKIP {j.name}: {e}", file=sys.stderr)
            skipped += 1
            continue
        pid = data.get("policy_id") or j.stem
        md = render_policy_md(data, j)
        out = POLICIES_OUT / f"{pid}.md"
        existed = out.exists()
        out.write_text(md, encoding="utf-8")
        if existed:
            updated_files += 1
        else:
            new_files += 1
        written += 1
        written_pids.add(pid)

        insurer_slug = data.get("insurer_slug") or pid.split("__", 1)[0]
        insurer_name = INSURER_NAMES.get(insurer_slug, insurer_slug)
        policy_name = data.get("policy_name") or pid
        uin = ""
        if isinstance(data.get("uin_code"), dict):
            uin = data["uin_code"].get("value") or ""
        meta = data.get("_meta") or {}
        completeness = meta.get("completeness_pct")
        completeness_str = f"{completeness}%" if completeness is not None else "—"
        rel = f"policies/{pid}.md"
        index_rows.append((insurer_name, policy_name, uin or "—", completeness_str, rel))

    # 4d-clean. remove stale MD files (no longer backed by data/policy_facts/)
    stale_removed = 0
    for f in POLICIES_OUT.glob("*.md"):
        if f.stem not in written_pids:
            f.unlink()
            stale_removed += 1
    if stale_removed:
        print(f"  (removed {stale_removed} stale MD files no longer in data/policy_facts/)", file=sys.stderr)

    # 4e. kb/INDEX.md
    today = date.today().isoformat()
    idx: list[str] = []
    idx.append("# Knowledge Base — Insurance Sales Bot")
    idx.append("")
    idx.append(f"_Last synced: {today}._")
    idx.append("")
    idx.append(
        "Canonical knowledge base for the Insurance Sales Bot. Every user-facing "
        "answer, scorecard, and comparison surface must trace back to a file in "
        "this directory."
    )
    idx.append("")

    idx.append(f"## Policies ({len(index_rows)})")
    idx.append("")
    idx.append("| Insurer | Policy | UIN | Completeness | KB path |")
    idx.append("| --- | --- | --- | --- | --- |")
    for insurer, name, uin, comp, rel in sorted(index_rows):
        idx.append(f"| {insurer} | {name} | `{uin}` | {comp} | [`{rel}`]({rel}) |")
    idx.append("")

    idx.append("## Methodology")
    idx.append("")
    idx.append("| File | What it contains |")
    idx.append("| --- | --- |")
    idx.append(
        "| [`methodology/scorecard.json`](methodology/scorecard.json) | "
        "Authoritative methodology contract: 6 sub-scores, weights, scored-field "
        "list, consumer rationale, anchors. Exported from `backend/scorecard.py`. |"
    )
    idx.append(
        "| [`methodology/glossary.json`](methodology/glossary.json) | "
        "User-facing jargon explanation contract — 13 terms × {en, hi} × "
        "{title, body}. Mirror of `frontend/src/lib/i18n.ts` GLOSSARY. |"
    )
    idx.append(
        "| [`methodology/discovery-script.md`](methodology/discovery-script.md) | "
        "Profile Builder discovery script — verbatim copy of `70-docs/discovery-script.md`. |"
    )
    idx.append(
        "| [`methodology/knowledge-graph.md`](methodology/knowledge-graph.md) | "
        "Profile-field ↔ sub-score weight-shift map — verbatim copy of "
        "`70-docs/scorecard-knowledge-graph.md`. |"
    )
    idx.append(
        "| [`methodology/tie-breakers.md`](methodology/tie-breakers.md) | "
        "Recommendation tie-breaker rubric — verbatim copy of `70-docs/tie-breaker-rubric.md`. |"
    )
    idx.append(
        "| [`methodology/INDEX.md`](methodology/INDEX.md) | "
        "Pointer index to all design / decision docs. |"
    )
    idx.append("")

    idx.append("## Data lineage")
    idx.append("")
    idx.append(
        "- [`AUDIT_TRAIL.md`](AUDIT_TRAIL.md) — end-to-end pipeline lineage + "
        "per-batch curation log."
    )
    idx.append("")

    idx.append("## Layout")
    idx.append("")
    idx.append("```")
    idx.append("kb/")
    idx.append("├── INDEX.md                       (this file)")
    idx.append("├── AUDIT_TRAIL.md                 (data lineage + curation history)")
    idx.append(f"├── policies/<policy_id>.md       ({len(index_rows)} files — one per curated policy)")
    idx.append("├── methodology/")
    idx.append("│   ├── scorecard.json             (6 sub-scores + weights + anchors)")
    idx.append("│   ├── glossary.json              (13 terms × en/hi)")
    idx.append("│   ├── discovery-script.md")
    idx.append("│   ├── knowledge-graph.md")
    idx.append("│   ├── tie-breakers.md")
    idx.append("│   └── INDEX.md")
    idx.append("├── research/")
    idx.append("├── calculations/")
    idx.append("├── reviews/")
    idx.append("├── premiums/")
    idx.append("├── security/")
    idx.append("└── eval/")
    idx.append("```")
    idx.append("")

    idx.append("## Provenance convention")
    idx.append("")
    idx.append(
        "Every `policies/<id>.md` file is generated from "
        "`data/policy_facts/<id>.json` and preserves the verbatim source quote and "
        "source PDF path for every field. JSON is the machine source; markdown is "
        "the human-readable mirror. Regenerate the entire kb/ tree by running "
        "`.venv/bin/python3 tools/build_kb_mirror.py`."
    )
    idx.append("")

    index_path = KB / "INDEX.md"
    existed_idx = index_path.exists()
    index_path.write_text("\n".join(idx), encoding="utf-8")
    if existed_idx:
        updated_files += 1
    else:
        new_files += 1

    # 4f. AUDIT_TRAIL.md — append today's batch block
    audit_path = KB / "AUDIT_TRAIL.md"
    existing = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    batch_marker = "## Batch — 2026-05-14"
    appended = False
    if batch_marker not in existing:
        ap: list[str] = []
        ap.append("")
        ap.append(batch_marker)
        ap.append("")
        ap.append(
            "Three back-to-back curation passes brought the `data/policy_facts/` "
            f"directory to **{len(index_rows)} policies** with verbatim-quote "
            "provenance. Mirrored into `kb/policies/` today."
        )
        ap.append("")
        ap.append(
            "- **Batch 1 — human-research curation (22 policies).** Manual + "
            "agent-assisted verbatim extraction from local PDFs in `rag/corpus/` "
            "for the 22 highest-priority wordings. Schema: "
            "`{value, unit?, source_pdf_path, source_quote}` per field with a "
            "`_meta` block (`curated_at`, `primary_source_pdf`, `completeness_pct`, "
            "`notes`). Average completeness ≈83.5%. Recorded in "
            "[`data/policy_facts/_curation_report.md`](../data/policy_facts/_curation_report.md)."
        )
        ap.append(
            "- **Batch 2 — regex + pdfplumber pass (43 policies).** Automated "
            "pattern extraction across the remaining retail health policy PDFs. "
            "Each field carries the same provenance triple; numeric values were "
            "validated against the verbatim quote before being written."
        )
        ap.append(
            "- **Batch 3 — group / specialty policies (37 policies).** "
            "`tools/curate_remaining.py` extended coverage to group, top-up, "
            "critical-illness, personal-accident, and specialty riders. Marked "
            "with `policy_type` (e.g. `hospital_cash`) where the wording diverged "
            "from indemnity templates."
        )
        ap.append("")
        ap.append(
            "**Verification.** `tools/info_source_map.py` produced "
            "[`eval/info_source_map.json`](../eval/info_source_map.json) and "
            "[`data/information_source_map.md`](../data/information_source_map.md) "
            "with verdict counts: **✅ 798 / ⚠️ 321 / ❌ 0 / ⏳ 1385.** No ❌ "
            "(broken-link) verdicts remain; the ⏳ tail tracks deferred "
            "verifications. The ✅:⚠️ ratio is the canonical KPI for "
            "source-grounding quality on this dataset."
        )
        ap.append("")
        ap.append("**UI / runtime changes shipped today:**")
        ap.append("")
        ap.append(
            "- **Profile Builder tab** — guided 8-question discovery flow "
            "(`70-docs/discovery-script.md`). Profile-completeness gate (≥0.6) "
            "controls whether the personalised scorecard renders."
        )
        ap.append(
            "- **Score gate on policy cards** — recommendations suppress the "
            "per-buyer letter grade until completeness ≥ 0.6 (universal IRDAI "
            "metrics like CSR and complaints/10K still render, since they're "
            "insurer-level)."
        )
        ap.append(
            "- **EN ↔ हिं i18n** — full bilingual UI with the 13-term jargon "
            "glossary at `frontend/src/lib/i18n.ts` (mirrored to "
            "`kb/methodology/glossary.json`)."
        )
        ap.append(
            "- **Scorecard methodology expander** — every grade opens a "
            "transparency panel sourced from `METHODOLOGY_BLUEPRINT` (mirrored "
            "to `kb/methodology/scorecard.json`)."
        )
        ap.append(
            "- **Source-quote popovers** — hovering a fact on a policy card "
            "surfaces the verbatim PDF quote that backed it."
        )
        ap.append(
            "- **Cerebras Qwen-3-235B wired as primary judge** — replaces the "
            "previous Groq Llama-3.1 grader for the eval pipeline; legacy "
            "provider retained as fallback."
        )
        audit_path.write_text(
            existing.rstrip() + "\n" + "\n".join(ap) + "\n", encoding="utf-8"
        )
        appended = True
        updated_files += 1

    print(
        f"Synced {len(index_rows)} policies + methodology to kb/. "
        f"New files: {new_files}. Updated: {updated_files}."
    )
    if skipped:
        print(f"  (skipped {skipped} unparseable JSON files)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
