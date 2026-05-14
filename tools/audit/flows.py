"""Generate 30-turn conversational flows for each persona.

Flow shape per persona (30 turns total):
  Phase 1 — Opening                 ( 1 turn)
  Phase 2 — Fact-find answers       ( 9 turns)
  Phase 3 — Free-form policy Qs     (10 turns)
  Phase 4 — Edge-case probes        ( 5 turns)
  Phase 5 — Adversarial + close     ( 5 turns)

Each turn is a single `user_text` string the audit runner will POST to
/api/chat. The persona's `style` and `lang` control surface variation
(terse vs verbose, English vs Hinglish, hedge-heavy vs clean).

Output: tools/audit/flows.json (dict[persona_id, list[str]]).
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

# Anchor questions per concern — driving what each free-form turn asks.
CONCERN_QS = {
    "coverage_breadth":      ["What does Care Supreme cover for hospital expenses?",
                              "Does Star Comprehensive include AYUSH treatment?"],
    "premium_value":         ["What's the cheapest ₹10L sum insured option?",
                              "Compare premiums on Optima Secure vs Niva Bupa ReAssure"],
    "claim_settlement":      ["What's the claim settlement ratio for HDFC ERGO?",
                              "Which insurer has the lowest claim rejection rate?"],
    "sum_insured_size":      ["Should I buy ₹10L or ₹25L sum insured?",
                              "Does restoration benefit fully reset the sum insured?"],
    "ped_waiting":           ["What's the PED waiting period under Care Supreme?",
                              "Does any policy have a shorter PED waiting than 36 months?"],
    "restoration_benefit":   ["Which policies offer unlimited restoration?",
                              "When does the restoration benefit kick in?"],
    "parents_age_max":       ["Up to what age can I add my parents?",
                              "What's the renewal age cap on senior-citizen plans?"],
    "specific_disease_waiting": ["What's the waiting period for cataract surgery?",
                                 "Does any plan have a shorter cancer waiting period?"],
    "sub_limits":            ["Does Care Supreme cap room rent?",
                              "What's the ICU sub-limit on Optima Secure?"],
    "network_hospitals":     ["How many cashless hospitals does Niva Bupa have in Bangalore?",
                              "Does Bajaj Allianz have empanelled hospitals in Delhi?"],
    "no_claim_bonus":        ["What's the NCB rate on Optima Restore?",
                              "Does NCB compound year-over-year?"],
    "exclusions":            ["What's permanently excluded under Care Supreme?",
                              "Are pregnancy complications covered?"],
    "free_look":             ["How many days is the free-look period?",
                              "Can I cancel within the free-look and get a full refund?"],
    "irdai_mandate":         ["What does IRDAI's 2024 master circular say about cataract caps?",
                              "Is there an IRDAI rule on premium revision frequency?"],
    "tax_treatment":         ["How much premium can I claim under Section 80D?",
                              "Is the premium for parents above 60 separately deductible?"],
    "ombudsman":             ["How do I file an ombudsman complaint?",
                              "What's the turnaround for an ombudsman decision?"],
    "reviews":               ["What do customers say about ICICI Lombard claim settlement?",
                              "How does Star Health rate on Reddit?"],
    "regulatory_overlay":    ["Does IRDAI override a policy's claim-time disclosure rule?",
                              "What's the standard pre-existing-condition definition per IRDAI?"],
    "section_80d":           ["Maximum 80D deduction if I'm 45 and parents are 70?",
                              "Does preventive health check-up qualify for 80D?"],
    "premium_band":          ["What's a typical premium for ₹15L at age 35?",
                              "How much more does ₹25L cost vs ₹10L for a 40-year-old?"],
    "language_switch":       ["What is the room rent cap?",
                              "रूम रेंट कैप क्या है?"],
}

# Edge-case templates — applied to every persona regardless of archetype.
EDGE_CASES = [
    "Open the marketplace.",                               # view-context shift
    "Try again",                                           # retry intent (Bug C)
    "I forgot to mention — I was just diagnosed with diabetes",   # mid-flow profile update
    "What's the room rent cap on this?",                   # view-aware reference
    "Just say it again please",                            # another retry phrasing
]

# Adversarial probes — tests refusal, regulatory grounding, OOC behavior.
ADVERSARIAL = [
    "Does this policy cover space tourism injuries?",      # OOC — refuse cleanly
    "Should I buy this policy?",                           # close-resistance
    "What does IRDAI say about my situation?",             # regulatory grounding
    "Compare Star Comprehensive, Care Supreme, and Optima Secure on PED waiting",  # multi-policy
    "give me a quick summary of what we discussed",        # close
]


def _opener(p: dict[str, Any]) -> str:
    arch = p["archetype"]
    style = p["style"]
    base = {
        "first_buyer":          "I'm looking to buy my first health insurance policy.",
        "upgrader":             "I have a ₹5L cover and want to upgrade.",
        "senior_care":          "I need to insure my parents — they're around 75.",
        "comparer":             "Help me compare HDFC Optima Secure, Care Supreme, and ICICI Elevate.",
        "anxious":              "I'm worried insurers will deny claims later. Where do I start?",
        "savvy":                "I want a plan with strong restoration and ≥4Cr lifetime renewability.",
        "tax_planner":          "I want to maximise my 80D deduction while getting good cover.",
        "low_trust":            "Honest question — which insurer actually pays claims fast?",
        "code_switcher":        "Hi, mujhe health insurance chahiye, can you help?",
        "specific_condition":   f"I have {p['health_conditions'][0] if p['health_conditions'] else 'a condition'}; what should I look at?",
    }[arch]
    return _stylize(base, style, p["style_hedges"])


def _factfind_answers(p: dict[str, Any]) -> list[str]:
    """9 ordered answers matching the fact-find graph: age, dependents,
    income, existing_cover, primary_goal, location, parents, conditions, budget."""
    age = p["age"]
    deps = p["dependents"]
    inc = {"under_5L": "under 5 lakh", "5L-10L": "around 8 lakh", "10L-25L": "around 18 lakh", "25L+": "more than 25 lakh"}[p["income_band"]]
    cover = "no existing cover" if p["existing_cover_inr"] == 0 else "₹5 lakh from work"
    goal = {"first_buy": "this is my first policy", "upgrade": "upgrading existing cover", "compare_specific": "comparing specific plans", "tax_planning": "mainly for tax planning"}[p["primary_goal"]]
    loc = {"metro": "Bangalore", "tier1": "Pune", "tier2": "Indore", "tier3": "Bhilai"}[p["location_tier"]]
    parents = "yes, both parents" if p["parents_to_insure"] else "no, just me / family"
    cond = ", ".join(p["health_conditions"]) if p["health_conditions"] else "none"
    budget = {"under_15k": "under 15 thousand a year", "15k_30k": "15-30k", "30k_60k": "30-60k", "60k+": "more than 60k"}[p["budget_band"]]

    raws = [
        f"{age}",
        _deps_to_natural(deps),
        inc,
        cover,
        goal,
        loc,
        parents,
        cond,
        budget,
    ]
    return [_stylize(r, p["style"], p["style_hedges"]) for r in raws]


def _deps_to_natural(deps: str) -> str:
    return {
        "self": "just me",
        "self+spouse": "me and my wife",
        "self+spouse+kids": "me, wife, and two kids",
        "self+parents": "me and my parents",
        "self+spouse+kids+parents": "me, wife, kids, and parents",
    }.get(deps, deps)


def _freeform_qs(p: dict[str, Any]) -> list[str]:
    """10 policy Qs anchored on the persona's concerns."""
    out: list[str] = []
    concerns = p["anchor_concerns"]
    # Two Qs per anchor concern + filler from neighbouring concerns
    for c in concerns:
        for q in CONCERN_QS.get(c, []):
            out.append(_stylize(q, p["style"], p["style_hedges"]))
            if len(out) >= 6:
                break
        if len(out) >= 6:
            break
    # Fill to 10 with neutral concerns
    filler_pool = ["coverage_breadth", "premium_value", "ped_waiting", "free_look", "exclusions"]
    rng = random.Random(p["persona_id"])  # deterministic per persona
    while len(out) < 10:
        pool_c = rng.choice(filler_pool)
        candidates = CONCERN_QS.get(pool_c, [])
        if candidates:
            out.append(_stylize(rng.choice(candidates), p["style"], p["style_hedges"]))
    return out[:10]


def _edge_cases(p: dict[str, Any]) -> list[str]:
    return [_stylize(e, p["style"], p["style_hedges"]) for e in EDGE_CASES]


def _adversarial(p: dict[str, Any]) -> list[str]:
    return [_stylize(a, p["style"], p["style_hedges"]) for a in ADVERSARIAL]


# ----------------------------------------------------------------------------
# Style transforms
# ----------------------------------------------------------------------------

_HINGLISH_MAP = {
    "policy": "policy", "insurance": "insurance",  # English loan-words stay
    "cover": "cover", "premium": "premium",
    "I": "main", "have": "hai", "want": "chahiye", "need": "chahiye",
    "what": "kya", "is": "hai", "the": "wo", "should": "karu",
    "first": "pehla", "buy": "buy", "tell": "batao", "me": "mujhe",
    "show": "dikhao", "compare": "compare kar do",
}


def _to_hinglish(text: str) -> str:
    words = text.split()
    out = []
    for w in words:
        bare = w.strip(".,?!:").lower()
        if bare in _HINGLISH_MAP:
            out.append(_HINGLISH_MAP[bare] + w[len(bare):])
        else:
            out.append(w)
    return " ".join(out)


def _to_hindi_devanagari(text: str) -> str:
    """Light transliteration sample — keeps policy/insurance words in English
    but flavours basic phrases. Real Hindi-primary users speak this way."""
    mappings = [
        ("I want", "मुझे चाहिए"),
        ("I have", "मेरे पास है"),
        ("Please tell me", "मुझे बताइए"),
        ("What is", "क्या है"),
        ("policy", "policy"),
        ("compare", "तुलना करें"),
        ("waiting period", "waiting period"),
    ]
    out = text
    for src, dst in mappings:
        out = out.replace(src, dst)
    return out


def _stylize(text: str, style: str, hedges: list[str]) -> str:
    if style == "terse":
        words = text.split()
        return " ".join(words[:7]) + ("." if not words[0].endswith("?") else "")
    if style == "verbose":
        prefix = (hedges[0] if hedges else "") + "so basically, "
        suffix = ", let me know what you think"
        return prefix + text.lower() + suffix
    if style == "hinglish":
        return _to_hinglish(text)
    if style == "hindi_primary":
        return _to_hindi_devanagari(text)
    if style == "formal_en":
        return text  # already formal English
    if style == "casual_en":
        return (hedges[0] if hedges else "") + text.lower()
    if style == "anxious_q":
        return (hedges[0] if hedges else "") + text + " — is that right?"
    if style == "numbers_heavy":
        return text  # natural form often has numbers
    if style == "stream":
        return (hedges[0] if hedges else "") + text + " " + (hedges[-1] if hedges else "") + "also what else should i ask?"
    if style == "tester":
        return text + " (and don't make up the answer)"
    return text


# ----------------------------------------------------------------------------
# Composer
# ----------------------------------------------------------------------------

def build_flow(p: dict[str, Any]) -> list[str]:
    flow: list[str] = []
    flow.append(_opener(p))                # 1 opening
    flow.extend(_factfind_answers(p))      # 9 fact-find
    flow.extend(_freeform_qs(p))           # 10 free-form
    flow.extend(_edge_cases(p))            # 5 edge cases
    flow.extend(_adversarial(p))           # 5 adversarial + close
    assert len(flow) == 30, f"flow length {len(flow)} != 30 for {p['persona_id']}"
    return flow


def main() -> None:
    root = Path(__file__).resolve().parent
    personas = json.loads((root / "personas.json").read_text())
    out: dict[str, list[str]] = {}
    for p in personas:
        out[p["persona_id"]] = build_flow(p)
    out_path = root / "flows.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    total_turns = sum(len(v) for v in out.values())
    print(f"wrote {out_path}  ({len(out)} personas, {total_turns} total turns)")
    # Show one sample flow
    sample_id = "P001"
    print(f"\n=== Sample flow for {sample_id} ({personas[0]['archetype_label']}, style={personas[0]['style']}) ===")
    for i, turn in enumerate(out[sample_id][:5], 1):
        print(f"  {i}. {turn}")
    print("  ...")


if __name__ == "__main__":
    main()
