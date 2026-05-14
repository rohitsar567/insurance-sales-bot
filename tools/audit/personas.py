"""Generate 100 diverse personas for end-to-end bot audit.

Each persona is the product of:
  - 10 archetypes (intent shape: first-buyer, upgrader, senior-care, …)
  - 10 demographic profiles (age × dependents × income × city tier)
  - 1 conversational style (deterministic-pick from a curated list)

The cross-product is engineered so that no two personas have the same
(archetype, demo, style) triple. Each persona is stable across runs (the
order is index-based), so report diffs reveal regressions rather than
shuffle noise.

Run as a script:
    python tools/audit/personas.py
  → writes tools/audit/personas.json (100 entries)

Read from another script:
    from tools.audit.personas import generate
    personas = generate()  # → list[dict]
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Archetypes — the user's INTENT shape. Drives flow design downstream.
# ----------------------------------------------------------------------------
ARCHETYPES = [
    {
        "id": "first_buyer",
        "label": "First-time health insurance buyer",
        "primary_goal": "first_buy",
        "anchor_concerns": ["coverage_breadth", "premium_value", "claim_settlement"],
    },
    {
        "id": "upgrader",
        "label": "Existing-cover upgrader",
        "primary_goal": "upgrade",
        "anchor_concerns": ["sum_insured_size", "ped_waiting", "restoration_benefit"],
    },
    {
        "id": "senior_care",
        "label": "Buying for senior parents",
        "primary_goal": "first_buy",
        "anchor_concerns": ["parents_age_max", "ped_waiting", "specific_disease_waiting"],
    },
    {
        "id": "comparer",
        "label": "Comparing 2-3 specific policies",
        "primary_goal": "compare_specific",
        "anchor_concerns": ["sub_limits", "network_hospitals", "no_claim_bonus"],
    },
    {
        "id": "anxious",
        "label": "Anxious skeptic — claim-denial fears",
        "primary_goal": "first_buy",
        "anchor_concerns": ["claim_settlement", "exclusions", "free_look"],
    },
    {
        "id": "savvy",
        "label": "Financially literate, asks pointed questions",
        "primary_goal": "compare_specific",
        "anchor_concerns": ["irdai_mandate", "tax_treatment", "ombudsman"],
    },
    {
        "id": "tax_planner",
        "label": "Tax-planning oriented",
        "primary_goal": "tax_planning",
        "anchor_concerns": ["section_80d", "premium_band", "sum_insured_size"],
    },
    {
        "id": "low_trust",
        "label": "Low trust in insurers; needs proof",
        "primary_goal": "first_buy",
        "anchor_concerns": ["claim_settlement", "reviews", "regulatory_overlay"],
    },
    {
        "id": "code_switcher",
        "label": "Switches English ↔ Hinglish mid-flow",
        "primary_goal": "first_buy",
        "anchor_concerns": ["language_switch", "premium_band", "coverage_breadth"],
    },
    {
        "id": "specific_condition",
        "label": "Has a specific condition driving the buy",
        "primary_goal": "upgrade",
        "anchor_concerns": ["ped_waiting", "specific_disease_waiting", "sub_limits"],
    },
]

# ----------------------------------------------------------------------------
# Demographic variations — 10 stable cells across age × family × income × city.
# Each cell is a different life situation; together they span ~70% of Indian
# middle-class health-insurance buyers.
# ----------------------------------------------------------------------------
DEMOGRAPHICS = [
    {"age": 24, "dependents": "self",                       "income_band": "under_5L",  "location_tier": "metro",  "marital": "single"},
    {"age": 28, "dependents": "self",                       "income_band": "5L-10L",    "location_tier": "metro",  "marital": "single"},
    {"age": 31, "dependents": "self+spouse",                "income_band": "10L-25L",   "location_tier": "metro",  "marital": "married"},
    {"age": 34, "dependents": "self+spouse+kids",           "income_band": "10L-25L",   "location_tier": "tier1",  "marital": "married_kids"},
    {"age": 38, "dependents": "self+spouse+kids",           "income_band": "25L+",      "location_tier": "metro",  "marital": "married_kids"},
    {"age": 42, "dependents": "self+spouse+kids+parents",   "income_band": "25L+",      "location_tier": "metro",  "marital": "sandwich_gen"},
    {"age": 47, "dependents": "self+spouse+parents",        "income_band": "10L-25L",   "location_tier": "tier1",  "marital": "married_no_kids"},
    {"age": 52, "dependents": "self+spouse",                "income_band": "10L-25L",   "location_tier": "tier2",  "marital": "empty_nester"},
    {"age": 58, "dependents": "self+spouse",                "income_band": "5L-10L",    "location_tier": "tier2",  "marital": "near_retire"},
    {"age": 63, "dependents": "self+spouse",                "income_band": "under_5L",  "location_tier": "tier3",  "marital": "retired"},
]

# ----------------------------------------------------------------------------
# Conversational styles — how the persona TALKS, independent of what they
# want. The 10 styles cycle so each (archetype × demo) cell gets a different
# voice across the 100 personas.
# ----------------------------------------------------------------------------
STYLES = [
    {"id": "terse",        "label": "Terse — 3-7 word answers",                "lang": "en", "hedges": []},
    {"id": "verbose",      "label": "Verbose — paragraphs of context",         "lang": "en", "hedges": ["um, ", "well, ", "you know, "]},
    {"id": "hinglish",     "label": "English with Hindi words sprinkled",      "lang": "hinglish", "hedges": []},
    {"id": "formal_en",    "label": "Formal English, full sentences",          "lang": "en", "hedges": []},
    {"id": "casual_en",    "label": "Casual English with typos and lowercase", "lang": "en", "hedges": ["btw ", "lol ", "tbh "]},
    {"id": "hindi_primary","label": "Mostly Hindi (Devanagari letters)",       "lang": "hi", "hedges": []},
    {"id": "anxious_q",    "label": "Asks lots of follow-up questions",        "lang": "en", "hedges": ["but ", "wait, ", "what about "]},
    {"id": "numbers_heavy","label": "Quotes specific numbers and policies",    "lang": "en", "hedges": []},
    {"id": "stream",       "label": "Stream-of-consciousness, rambles",        "lang": "en", "hedges": ["uh ", "hmm ", "and ", "also "]},
    {"id": "tester",       "label": "Tries to trip up the bot",                "lang": "en", "hedges": []},
]

# ----------------------------------------------------------------------------
# Synthetic name pool — 10 first names × 10 surnames covers our 100 personas
# without recycling the same identity. Names are common Indian names with a
# light regional spread (north + south + west).
# ----------------------------------------------------------------------------
FIRST_NAMES = ["Aarav", "Diya", "Rohan", "Ananya", "Vikram", "Priya", "Karthik", "Meera", "Saif", "Ishita"]
SURNAMES    = ["Sharma", "Iyer", "Mehta", "Reddy", "Banerjee", "Kapoor", "Nair", "Joshi", "Khan", "Pillai"]

# Health condition presets keyed by archetype + age bucket.
def _condition_for(archetype_id: str, age: int) -> list[str]:
    if archetype_id == "specific_condition":
        if age < 35:
            return ["asthma"]
        if age < 50:
            return ["hypertension"]
        return ["diabetes", "hypertension"]
    if archetype_id == "senior_care":
        return []  # parents' conditions handled separately in flow
    if age >= 50:
        return ["hypertension"]  # age-bucket baseline
    return []


def generate() -> list[dict[str, Any]]:
    personas: list[dict[str, Any]] = []
    pid = 1
    for ai, arch in enumerate(ARCHETYPES):
        for di, demo in enumerate(DEMOGRAPHICS):
            style = STYLES[(ai + di) % len(STYLES)]  # rotates so styles spread evenly across archetypes
            name_first = FIRST_NAMES[(ai + di) % len(FIRST_NAMES)]
            name_last = SURNAMES[(ai * 3 + di * 7) % len(SURNAMES)]
            persona = {
                "persona_id": f"P{pid:03d}",
                "name": f"{name_first} {name_last}",
                "archetype": arch["id"],
                "archetype_label": arch["label"],
                "primary_goal": arch["primary_goal"],
                "anchor_concerns": arch["anchor_concerns"],
                "age": demo["age"],
                "dependents": demo["dependents"],
                "income_band": demo["income_band"],
                "location_tier": demo["location_tier"],
                "marital_stage": demo["marital"],
                "existing_cover_inr": 0 if arch["id"] in ("first_buyer", "senior_care", "anxious", "tax_planner", "low_trust") else 500_000,
                "health_conditions": _condition_for(arch["id"], demo["age"]),
                "parents_to_insure": arch["id"] == "senior_care" or "parents" in demo["dependents"],
                "parents_age_max": 75 if arch["id"] == "senior_care" or "parents" in demo["dependents"] else None,
                "parents_has_ped": arch["id"] == "senior_care",
                "budget_band": {
                    "under_5L": "under_15k",
                    "5L-10L": "15k_30k",
                    "10L-25L": "30k_60k",
                    "25L+": "60k+",
                }[demo["income_band"]],
                "style": style["id"],
                "style_label": style["label"],
                "lang": style["lang"],
                "style_hedges": style["hedges"],
            }
            personas.append(persona)
            pid += 1
    return personas


def main() -> None:
    personas = generate()
    out = Path(__file__).resolve().parent / "personas.json"
    out.write_text(json.dumps(personas, indent=2, ensure_ascii=False))
    print(f"wrote {out}  ({len(personas)} personas)")
    # Quick distribution sanity check
    by_arch: dict[str, int] = {}
    by_style: dict[str, int] = {}
    for p in personas:
        by_arch[p["archetype"]] = by_arch.get(p["archetype"], 0) + 1
        by_style[p["style"]] = by_style.get(p["style"], 0) + 1
    print(f"  archetypes (each should be 10): {by_arch}")
    print(f"  styles (each should be 10):     {by_style}")


if __name__ == "__main__":
    main()
