"""Policy health scorecard — turns 48 structured fields into a human-readable
A-F grade with 6 sub-scores.

Why this exists: a buyer reading 48 fields can't tell if it's "good." A
single letter grade + 6 sub-bars + 1-line summary makes the answer obvious.
Inspired by what people like Beli / Ditto have done to simplify insurance.

Score philosophy: optimize for the *buyer*, not the insurer. So:
  - Generous coverage, low frictions, predictable claims = higher score
  - Heavy waiting periods, copays, sub-limits = lower score
  - Regulatory-mandated minimums (IRDAI 30-day initial) don't hurt the score

Each sub-score is 0-100. Overall is a weighted average. Letter grade comes
from thresholds (A: 85+, B: 70-84, C: 55-69, D: 40-54, F: <40).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SubScore:
    name: str
    score: int  # 0-100
    summary: str
    signals: list[str] = field(default_factory=list)  # short positive/negative bullets


@dataclass
class Scorecard:
    policy_id: str
    policy_name: str
    insurer_slug: str
    overall_score: int
    grade: str  # A, B, C, D, F
    one_liner: str
    sub_scores: list[SubScore]
    data_completeness_pct: float  # how many of the scoring fields actually have data
    methodology_link: str = "/70-docs/scorecard-methodology.md"


# ---- helpers ----

def _get(p: dict, key: str, default: Any = None) -> Any:
    v = p.get(key, default)
    if isinstance(v, dict) and "covered" in v:
        return v.get("covered", default)
    return v


def _bool(p: dict, key: str) -> Optional[bool]:
    v = p.get(key)
    if isinstance(v, dict) and "covered" in v:
        return v.get("covered")
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("yes", "true", "y"):
        return True
    if isinstance(v, str) and v.lower() in ("no", "false", "n"):
        return False
    return None


def _int(p: dict, key: str) -> Optional[int]:
    v = p.get(key)
    if isinstance(v, dict) and "limit_inr" in v:
        v = v.get("limit_inr")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def clamp(x: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(x))))


# ---- 6 sub-scores ----

def score_coverage_breadth(p: dict) -> SubScore:
    """How wide is the safety net? AYUSH, day-care, OPD, organ donor, maternity, etc."""
    signals_pos: list[str] = []
    signals_neg: list[str] = []
    s = 50  # neutral base

    if _bool(p, "ayush_coverage"):
        s += 8; signals_pos.append("AYUSH covered")
    elif _bool(p, "ayush_coverage") is False:
        signals_neg.append("no AYUSH")

    dct = _int(p, "day_care_treatments_count")
    if dct is not None:
        if dct >= 400: s += 10; signals_pos.append(f"{dct} day-care procedures")
        elif dct >= 200: s += 6
        elif dct < 100: s -= 5; signals_neg.append(f"only {dct} day-care procedures")

    if _bool(p, "maternity_coverage"):
        s += 6; signals_pos.append("maternity covered")
    if _bool(p, "newborn_coverage"):
        s += 4; signals_pos.append("newborn covered")
    if _bool(p, "organ_donor_expenses"):
        s += 4; signals_pos.append("organ donor expenses")
    if _bool(p, "ambulance_cover"):
        s += 3; signals_pos.append("ambulance covered")
    if _bool(p, "domiciliary_treatment"):
        s += 4
    if _bool(p, "preventive_health_checkup"):
        s += 3; signals_pos.append("free health checkups")

    pre = _int(p, "pre_hospitalization_days") or 0
    post = _int(p, "post_hospitalization_days") or 0
    if pre >= 60: s += 4; signals_pos.append(f"{pre}d pre-hospitalization")
    if post >= 90: s += 4; signals_pos.append(f"{post}d post-hospitalization")

    summary = "Wide coverage" if s >= 75 else "Standard coverage" if s >= 55 else "Limited coverage"
    return SubScore("Coverage Breadth", clamp(s), summary, signals_pos + [f"− {x}" for x in signals_neg])


def score_cost_predictability(p: dict) -> SubScore:
    """How likely are you to face surprise out-of-pocket costs? Copay, room rent caps, sub-limits."""
    signals: list[str] = []
    s = 75  # most policies start fine

    copay = _int(p, "copayment_pct")
    if copay is not None and copay > 0:
        if copay >= 30: s -= 25; signals.append(f"− {copay}% copayment")
        elif copay >= 20: s -= 18; signals.append(f"− {copay}% copayment")
        elif copay >= 10: s -= 10; signals.append(f"− {copay}% copayment")
        else: s -= 4

    rrc = p.get("room_rent_capping")
    rrc_text = rrc if isinstance(rrc, str) else (rrc.get("limit_text") if isinstance(rrc, dict) else None)
    if rrc_text:
        if "no cap" in rrc_text.lower() or "no monetary" in rrc_text.lower():
            s += 6; signals.append("no room rent cap")
        elif "1%" in rrc_text or "%" in rrc_text:
            s -= 8; signals.append(f"− room rent capped: {rrc_text[:50]}")

    deductible = _int(p, "deductible_amount")
    if deductible and deductible > 0:
        signals.append(f"− deductible ₹{deductible:,}")
        s -= 6

    summary = "Predictable costs" if s >= 75 else "Some out-of-pocket" if s >= 55 else "Material out-of-pocket"
    return SubScore("Cost Predictability", clamp(s), summary, signals)


def score_waiting_friction(p: dict) -> SubScore:
    """How long before benefits actually kick in? PED, specific disease, maternity waits."""
    signals: list[str] = []
    s = 90

    ped = _int(p, "pre_existing_disease_waiting_months")
    if ped is not None:
        if ped >= 48: s -= 30; signals.append(f"− {ped}mo PED waiting (long)")
        elif ped >= 36: s -= 20; signals.append(f"− {ped}mo PED waiting")
        elif ped >= 24: s -= 10; signals.append(f"− {ped}mo PED waiting")
        else: signals.append(f"{ped}mo PED waiting (short)")

    mw = _int(p, "maternity_waiting_months")
    if mw is not None:
        if mw >= 48: s -= 10; signals.append(f"− {mw}mo maternity waiting")
        elif mw >= 24: s -= 4

    iw = _int(p, "initial_waiting_period_days")
    # 30 days is IRDAI-mandated minimum; don't penalize
    if iw is not None and iw > 60: s -= 5; signals.append(f"− {iw}d initial waiting")

    summary = "Quick activation" if s >= 75 else "Standard waits" if s >= 55 else "Heavy waiting periods"
    return SubScore("Waiting-Period Friction", clamp(s), summary, signals)


def score_claim_experience(p: dict, insurer_reviews: Optional[dict] = None) -> SubScore:
    """Will claims actually be paid? Network size, settlement ratio, cashless support.

    Now also uses INSURER-LEVEL data from data/reviews/<slug>.json — the IRDAI
    Annual Report claim_settlement_ratio + complaints_per_10k_policies feed
    directly into this sub-score. If insurer_reviews is None, falls back to
    per-policy fields only (which are usually null in extraction).
    """
    signals: list[str] = []
    s = 60

    if _bool(p, "cashless_treatment_supported"):
        s += 15; signals.append("cashless supported")
    nh = _int(p, "network_hospital_count")
    if nh is not None:
        if nh >= 10000: s += 15; signals.append(f"{nh:,}+ network hospitals")
        elif nh >= 5000: s += 8; signals.append(f"{nh:,} network hospitals")
        elif nh < 2000: s -= 8; signals.append(f"− only {nh} network hospitals")

    # Prefer insurer-level IRDAI data (always present + authoritative) over
    # per-policy claim_settlement_ratio (usually null in extraction).
    csr_val = None
    if insurer_reviews:
        cm = insurer_reviews.get("claim_metrics", {})
        csr_val = cm.get("claim_settlement_ratio_pct")
        cpk = cm.get("complaints_per_10k_policies")
        if csr_val is not None:
            if csr_val >= 95: s += 12; signals.append(f"{csr_val:.1f}% CSR (IRDAI {cm.get('claim_settlement_ratio_year','')})")
            elif csr_val >= 85: s += 6; signals.append(f"{csr_val:.1f}% CSR")
            elif csr_val < 75: s -= 12; signals.append(f"− {csr_val:.1f}% CSR (low)")
        if cpk is not None:
            if cpk <= 10: s += 6; signals.append(f"{cpk}/10K complaints (low)")
            elif cpk <= 25: s += 0
            elif cpk <= 45: s -= 6; signals.append(f"− {cpk}/10K complaints (above avg)")
            else: s -= 12; signals.append(f"− {cpk}/10K complaints (high)")
    else:
        # Fallback to per-policy
        csr = p.get("claim_settlement_ratio")
        try:
            csr_val = float(csr)
            if csr_val >= 95: s += 10; signals.append(f"{csr_val:.1f}% claim settlement ratio")
            elif csr_val >= 85: s += 6; signals.append(f"{csr_val:.1f}% CSR")
            elif csr_val < 75: s -= 12; signals.append(f"− {csr_val:.1f}% CSR (low)")
        except (TypeError, ValueError):
            pass

    tat = _int(p, "tat_cashless_authorization_hours")
    if tat is not None and tat <= 2:
        s += 4; signals.append(f"{tat}h cashless TAT")

    summary = "Smooth claims" if s >= 75 else "Standard claim experience" if s >= 55 else "Friction risk on claims"
    return SubScore("Claim Experience", clamp(s), summary, signals)


def score_renewal_protection(p: dict) -> SubScore:
    """Can you keep this policy as you age? Lifelong renewability + wide age band."""
    signals: list[str] = []
    s = 60

    maxr = _int(p, "max_renewal_age")
    if maxr is not None:
        if maxr >= 99: s += 25; signals.append("lifelong renewability")
        elif maxr >= 80: s += 15; signals.append(f"renewable up to {maxr}")
        elif maxr < 65: s -= 15; signals.append(f"− only renewable up to {maxr}")

    maxe = _int(p, "max_entry_age")
    if maxe is not None:
        if maxe >= 65: s += 10; signals.append(f"entry up to {maxe}")
        elif maxe < 50: s -= 6

    summary = "Future-proof" if s >= 75 else "Adequate" if s >= 55 else "Renewal risk at older ages"
    return SubScore("Renewal Protection", clamp(s), summary, signals)


def score_bonuses(p: dict) -> SubScore:
    """No-claim bonuses, restoration, health checkups — sweeteners for loyal buyers."""
    signals: list[str] = []
    s = 50

    ncb = _int(p, "no_claim_bonus_pct")
    if ncb is not None:
        if ncb >= 100: s += 25; signals.append(f"{ncb}% NCB step-up")
        elif ncb >= 50: s += 15; signals.append(f"{ncb}% NCB")
        elif ncb >= 25: s += 8

    rb = p.get("restoration_benefit")
    if rb and isinstance(rb, str) and len(rb) > 5:
        s += 12; signals.append(f"restoration benefit: {rb[:50]}")

    if _bool(p, "preventive_health_checkup"):
        s += 8; signals.append("free preventive checkup")

    summary = "Generous bonuses" if s >= 75 else "Standard sweeteners" if s >= 55 else "Few extras"
    return SubScore("Bonus & Loyalty", clamp(s), summary, signals)


# ---- aggregate + grade ----

# Weights reflect what affects the buyer's real-world experience most.
WEIGHTS = {
    "Coverage Breadth": 0.22,
    "Cost Predictability": 0.20,
    "Waiting-Period Friction": 0.18,
    "Claim Experience": 0.20,
    "Renewal Protection": 0.12,
    "Bonus & Loyalty": 0.08,
}


# ----------------------------------------------------------------------------
# METHODOLOGY BLUEPRINT — the buyer-facing transparency layer
# ----------------------------------------------------------------------------
# Maps each of the 6 sub-scores to:
#   - the consumer rationale (why this matters in plain English)
#   - the concrete policy fields that drive its score (subset of the 48-field
#     HealthPolicy schema)
#   - the regulatory / industry anchors that justify the weight
# Used by /api/scorecard/methodology to render a customer-centric explanation
# of how the headline number is computed.
METHODOLOGY_BLUEPRINT = [
    {
        "name": "Coverage Breadth",
        "weight_pct": 22,
        "consumer_question": "When I actually need to claim, what's covered vs what's not?",
        "why_it_matters": (
            "Determines whether your hospital bill is fully reimbursed or whether you pay "
            "out-of-pocket for gaps like AYUSH, maternity, newborn care, or ambulance."
        ),
        "fields_driving_score": [
            {"field": "ayush_coverage", "rule": "AYUSH covered → +8"},
            {"field": "day_care_treatments_count", "rule": "≥400 procedures → +10, ≥200 → +6, <100 → −5"},
            {"field": "maternity_coverage", "rule": "Covered → +6"},
            {"field": "newborn_coverage", "rule": "Covered → +4"},
            {"field": "organ_donor_expenses", "rule": "Covered → +4"},
            {"field": "ambulance_cover", "rule": "Covered → +3"},
            {"field": "domiciliary_treatment", "rule": "Covered → +4"},
            {"field": "preventive_health_checkup", "rule": "Free → +3"},
            {"field": "pre_hospitalization_days", "rule": "≥60 days → +4"},
            {"field": "post_hospitalization_days", "rule": "≥90 days → +4"},
        ],
        "anchors": [
            "IRDAI Health Insurance Master Circular 2024 — emphasises comprehensive cover",
            "Acko buying guide: coverage breadth most-cited buyer concern",
        ],
    },
    {
        "name": "Cost Predictability",
        "weight_pct": 20,
        "consumer_question": "Will I face surprise bills I can't plan for?",
        "why_it_matters": (
            "Co-pay forces you to pay a % of every claim; room-rent capping reduces what gets "
            "reimbursed; sub-limits cap specific treatments below your sum insured. These "
            "convert a known sum-insured into an unpredictable out-of-pocket exposure."
        ),
        "fields_driving_score": [
            {"field": "copayment_pct", "rule": "0% → +0, 10% → −5, 20%+ → −12"},
            {"field": "room_rent_capping", "rule": "No limit → +6, capped → −5 to −10"},
            {"field": "deductible_amount", "rule": "₹0 → +0, ≥₹1L → −8"},
            {"field": "sub_limits", "rule": "No condition-specific caps → +5"},
            {"field": "icu_charges_capping", "rule": "No cap → +3"},
        ],
        "anchors": [
            "IRDAI Master Circular — disclosure norms on co-pay/sub-limits",
            "Common consumer complaint themes (IRDAI complaint logs)",
        ],
    },
    {
        "name": "Waiting-Period Friction",
        "weight_pct": 18,
        "consumer_question": "How soon can I actually use this policy if something happens?",
        "why_it_matters": (
            "Initial waiting period (30 days typical), pre-existing-disease waiting "
            "(commonly 24–48 months), and maternity waits delay claims. Shorter is better — "
            "especially for older buyers or those with diabetes/hypertension."
        ),
        "fields_driving_score": [
            {"field": "initial_waiting_period_days", "rule": "≤30 days → 0, >30 days → −3"},
            {"field": "pre_existing_disease_waiting_months", "rule": "≤24mo → +10, 36mo → 0, ≥48mo → −15"},
            {"field": "maternity_waiting_months", "rule": "≤24mo → +5, ≥36mo → −5"},
            {"field": "specific_disease_waiting_months", "rule": "≤24mo → +3"},
        ],
        "anchors": [
            "IRDAI standard product specifications (Arogya Sanjeevani UIN guideline: 36-month PED max)",
            "PolicyBazaar comparison data: 24-month PED is the buyer benchmark",
        ],
    },
    {
        "name": "Claim Experience",
        "weight_pct": 20,
        "consumer_question": "Will the insurer actually pay when I claim?",
        "why_it_matters": (
            "Coverage on paper means nothing if claims get denied or take weeks. We measure "
            "cashless network reach, IRDAI's published Claim Settlement Ratio (CSR), the "
            "complaint count per 10,000 policies, and how fast cashless pre-auth happens."
        ),
        "fields_driving_score": [
            {"field": "cashless_treatment_supported", "rule": "Yes → +5"},
            {"field": "network_hospital_count", "rule": "≥10,000 → +10, ≥5,000 → +5, <2,000 → −5"},
            {"field": "claim_settlement_ratio (IRDAI)", "rule": "≥95% → +12, 90–95 → +6, <85% → −10"},
            {"field": "complaints_per_10k_policies (IRDAI)", "rule": "<5 → +4, >20 → −8"},
            {"field": "tat_cashless_authorization_hours", "rule": "≤2h → +4, ≥24h → −4"},
        ],
        "anchors": [
            "IRDAI Annual Report 2023-24 — published CSR per insurer",
            "IRDAI Grievance Redressal handbook — complaints/10K is the regulator's own metric",
        ],
    },
    {
        "name": "Renewal Protection",
        "weight_pct": 12,
        "consumer_question": "Can I keep this policy when I'm 70 and need it most?",
        "why_it_matters": (
            "Health insurance only works if you can keep renewing. Lifelong renewability is "
            "the IRDAI default since 2020, but entry-age caps and porting friction still "
            "matter. Buyers who don't check this often lose cover when claims rise."
        ),
        "fields_driving_score": [
            {"field": "max_renewal_age", "rule": "Lifelong → +12, 80 → +6, ≤70 → −5"},
            {"field": "max_entry_age", "rule": "≥65 → +4 (more buyers eligible)"},
            {"field": "guaranteed_renewability", "rule": "Stated explicitly → +4"},
        ],
        "anchors": [
            "IRDAI Master Circular 2024 — lifelong renewability mandate",
            "IRDAI Portability Regulations 2020",
        ],
    },
    {
        "name": "Bonus & Loyalty",
        "weight_pct": 8,
        "consumer_question": "What do I get for staying claim-free and renewing year after year?",
        "why_it_matters": (
            "Claim-free years should compound value: most policies give 20–50% No-Claim Bonus "
            "and some restore the sum insured on exhaustion. Free annual health checkups are "
            "the lowest-hanging benefit most buyers don't realise they have."
        ),
        "fields_driving_score": [
            {"field": "no_claim_bonus_pct", "rule": "≥50% → +8, ≥25% → +4"},
            {"field": "restoration_benefit", "rule": "Present → +6"},
            {"field": "preventive_health_checkup", "rule": "Free annually → +3"},
            {"field": "wellness_program_present", "rule": "Yes → +2"},
        ],
        "anchors": [
            "IRDAI 'Cumulative Bonus' rules — capped at 100% under standard products",
            "Industry NCB best-practice (PolicyBazaar comparison standards)",
        ],
    },
]


def grade_for(score: int) -> tuple[str, str]:
    """Return (letter, one-line summary tone)."""
    if score >= 85: return "A", "Strong all-rounder — solid pick for the buyer."
    if score >= 70: return "B", "Good policy with a few notable gaps."
    if score >= 55: return "C", "Decent baseline; check the trade-offs before signing."
    if score >= 40: return "D", "Material concerns — only suitable for specific use-cases."
    return "F", "Significant gaps — alternative options are likely better."


# Fields the scorecard touches — used to compute data_completeness_pct
SCORED_FIELDS = [
    "ayush_coverage", "day_care_treatments_count", "maternity_coverage",
    "newborn_coverage", "organ_donor_expenses", "ambulance_cover",
    "domiciliary_treatment", "preventive_health_checkup",
    "pre_hospitalization_days", "post_hospitalization_days",
    "copayment_pct", "room_rent_capping", "deductible_amount",
    "pre_existing_disease_waiting_months", "maternity_waiting_months",
    "initial_waiting_period_days",
    "cashless_treatment_supported", "network_hospital_count",
    "claim_settlement_ratio", "tat_cashless_authorization_hours",
    "max_renewal_age", "max_entry_age",
    "no_claim_bonus_pct", "restoration_benefit",
]


def compute_data_completeness(p: dict) -> float:
    filled = 0
    for k in SCORED_FIELDS:
        v = p.get(k)
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, dict) and v.get("covered") is None and not v.get("limit_inr") and not v.get("limit_text"):
            continue
        filled += 1
    return round(filled / max(1, len(SCORED_FIELDS)) * 100, 1)


def _profile_tuned_weights(profile: Optional[dict]) -> dict[str, float]:
    """Return a per-sub-score weight dict adapted to the buyer profile.

    Every signal we collect should MOVE the weighting — collecting input and
    then ignoring it is wasted attention. The weights re-normalise to 1.0 at
    the end. Each adjustment is small (typically ±0.02–0.06) so accumulated
    drift never crosses the validity boundary of the rules.

    Audit trail per delta is in 70-docs/scorecard-methodology.md §6 (knowledge
    graph: profile-field → weight-shift table).
    """
    if not profile:
        return WEIGHTS
    w = dict(WEIGHTS)

    # ---- AGE ----
    age = profile.get("age")
    if isinstance(age, int):
        if age < 30:
            w["Waiting-Period Friction"] += 0.04   # PED + maternity waits hit hardest
            w["Claim Experience"] += 0.02
            w["Renewal Protection"] -= 0.04
            w["Bonus & Loyalty"] -= 0.02
        elif age >= 50:
            w["Renewal Protection"] += 0.06        # can I keep it past 70?
            w["Claim Experience"] += 0.02          # actually getting paid matters more
            w["Bonus & Loyalty"] -= 0.04
            w["Waiting-Period Friction"] -= 0.04

    # ---- DEPENDENTS ----
    deps = (profile.get("dependents") or "").lower()
    if any(k in deps for k in ("kid", "child")):
        w["Coverage Breadth"] += 0.03              # paediatric + day-care + immunisation
        w["Bonus & Loyalty"] += 0.01               # free checkups for family
        w["Cost Predictability"] -= 0.02           # family floater premiums are higher
        w["Renewal Protection"] -= 0.02
    if any(k in deps for k in ("spouse", "wife", "husband", "partner")):
        w["Coverage Breadth"] += 0.02              # maternity becomes relevant
        w["Waiting-Period Friction"] += 0.02       # maternity 36mo wait matters
        w["Bonus & Loyalty"] -= 0.02
        w["Renewal Protection"] -= 0.02

    if profile.get("parents_to_insure") or "parent" in deps:
        w["Coverage Breadth"] += 0.04
        w["Claim Experience"] += 0.04              # network matters more for elderly access
        w["Bonus & Loyalty"] -= 0.04
        w["Cost Predictability"] -= 0.04
        # Older parents with PED → renewal+claim become survival metrics
        if profile.get("parents_has_ped") or profile.get("parents_age_max", 0) >= 65:
            w["Renewal Protection"] += 0.04
            w["Waiting-Period Friction"] += 0.02
            w["Bonus & Loyalty"] -= 0.04
            w["Cost Predictability"] -= 0.02

    # ---- EXISTING COVER ----
    existing = profile.get("existing_cover_inr")
    if isinstance(existing, int) and existing > 0:
        # Already has cover → super-top-up territory; cost predictability less
        # critical, claim experience more (you only need this when claim hits big)
        w["Cost Predictability"] -= 0.03
        w["Claim Experience"] += 0.03
    elif existing == 0:
        # First-time buyer → predictable bill + simple terms matter most
        w["Cost Predictability"] += 0.03
        w["Coverage Breadth"] += 0.02
        w["Bonus & Loyalty"] -= 0.03
        w["Waiting-Period Friction"] -= 0.02

    # ---- PRIMARY GOAL ----
    goal = (profile.get("primary_goal") or "").lower()
    if "tax" in goal:
        w["Cost Predictability"] += 0.02           # premium is the tax-deduction itself
        w["Bonus & Loyalty"] -= 0.02
    if "upgrade" in goal:
        w["Coverage Breadth"] += 0.03              # whole point of upgrading
        w["Renewal Protection"] += 0.02
        w["Bonus & Loyalty"] -= 0.05
    if "compare" in goal or "specific" in goal:
        # User already knows what they want — flatten weights, defer to facts
        for k in w:
            w[k] = 0.95 * w[k] + 0.05 * (1.0 / 6)

    # ---- HEALTH CONDITIONS ----
    conditions = profile.get("health_conditions") or []
    if isinstance(conditions, list) and conditions:
        condition_str = " ".join(str(c).lower() for c in conditions)
        if any(c in condition_str for c in ("diab", "bp", "hyper", "thyroid", "heart", "cancer", "asthma")):
            # Pre-existing → PED waiting is the most important thing in the universe
            w["Waiting-Period Friction"] += 0.06
            w["Claim Experience"] += 0.03          # PED claim disputes are common
            w["Bonus & Loyalty"] -= 0.04
            w["Cost Predictability"] -= 0.03
            w["Renewal Protection"] -= 0.02

    # ---- BUDGET ----
    budget = profile.get("budget_band")
    if budget in ("under_15k", "15k_30k"):
        w["Cost Predictability"] += 0.04           # every rupee counts
        w["Bonus & Loyalty"] -= 0.02
        w["Waiting-Period Friction"] -= 0.02
    elif budget == "60k+":
        # High budget → comprehensive coverage + best claim experience matter
        w["Coverage Breadth"] += 0.02
        w["Claim Experience"] += 0.02
        w["Cost Predictability"] -= 0.04

    # ---- INCOME ----
    income = profile.get("income_band")
    if income == "under_5L":
        w["Cost Predictability"] += 0.03
        w["Bonus & Loyalty"] -= 0.03
    elif income in ("10L-25L", "25L+"):
        w["Coverage Breadth"] += 0.02
        w["Claim Experience"] += 0.02
        w["Cost Predictability"] -= 0.04

    # ---- LOCATION ----
    loc = profile.get("location_tier")
    if loc in ("tier2", "tier3"):
        # Smaller city → network density + cashless TAT critical
        w["Claim Experience"] += 0.04
        w["Coverage Breadth"] -= 0.02
        w["Bonus & Loyalty"] -= 0.02
    elif loc == "metro":
        # Metros have hospital depth → coverage breadth differentiates
        w["Coverage Breadth"] += 0.02
        w["Claim Experience"] -= 0.02

    # Clamp + normalise (no weight should go below 5%)
    for k in w:
        if w[k] < 0.05:
            w[k] = 0.05
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def profile_completeness(profile: Optional[dict]) -> float:
    """0.0–1.0 measure of how much we know about the buyer.

    Used by the frontend to GATE the personalized scorecard view — until
    completeness >= 0.6, we show insurer-level metrics (CSR, complaints —
    universal) but suppress the per-user grade since it's meaningless without
    knowing who's buying.
    """
    if not profile:
        return 0.0
    # Weighted by signal importance: age + dependents + budget are core; goal
    # + conditions + location are deep-dives that further refine.
    weights = {
        "age": 0.20,
        "dependents": 0.15,
        "budget_band": 0.15,
        "existing_cover_inr": 0.10,
        "primary_goal": 0.10,
        "location_tier": 0.10,
        "health_conditions": 0.10,
        "income_band": 0.05,
        "parents_age_max": 0.05,
    }
    total = 0.0
    for field_name, weight in weights.items():
        v = profile.get(field_name)
        if v is None:
            continue
        if isinstance(v, (list, str)) and len(v) == 0:
            continue
        total += weight
    return round(total, 2)


def build_scorecard(policy: dict, insurer_reviews: Optional[dict] = None, profile: Optional[dict] = None) -> Scorecard:
    subs = [
        score_coverage_breadth(policy),
        score_cost_predictability(policy),
        score_waiting_friction(policy),
        score_claim_experience(policy, insurer_reviews=insurer_reviews),
        score_renewal_protection(policy),
        score_bonuses(policy),
    ]
    weights = _profile_tuned_weights(profile)
    overall = clamp(sum(weights[s.name] * s.score for s in subs))
    letter, one_liner = grade_for(overall)
    return Scorecard(
        policy_id=policy.get("policy_id", ""),
        policy_name=policy.get("policy_name", ""),
        insurer_slug=policy.get("insurer_slug", ""),
        overall_score=overall,
        grade=letter,
        one_liner=one_liner,
        sub_scores=subs,
        data_completeness_pct=compute_data_completeness(policy),
    )
