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
from ABSOLUTE thresholds, frozen post-recalibration (2026-05-16):
A: ≥76, B: ≥69, C: ≥61, D: ≥54, F: <54. See grade_for().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ----------------------------------------------------------------------------
# FIELD ALIASES — the canonical-scorer-key → list-of-acceptable-input-keys map.
#
# Why this exists: the data layer is heterogeneous.
#   * 40-data/policy_facts/<insurer>__<product>.json  → canonical names
#     (max_renewal_age, copayment_pct, day_care_treatments_count, etc.)
#   * 40-data/policy_facts/<insurer>__<product>__<doctype>.json → mixed; some
#     fields use LLM-extracted aliases like co_payment_pct, room_rent_capped_at_pct_of_si
#   * rag/extracted/*.json → pure LLM output with aliases like
#     max_renewal_age_years (int), deductible_amount_inr (int),
#     day_care_treatments: {covered, limit_inr, limit_text/notes} (dict).
#
# Without aliasing the scorecard reads `None` for every aliased field and the
# 6 sub-scores collapse to their neutral bases → 12 different policies all
# render as 72/100. Aliasing recovers the underlying data spread.
# ----------------------------------------------------------------------------
# NOTE: max_renewal_age was deliberately removed as a scored field. Lifelong
# renewability is the IRDAI norm for health-indemnity products (mandated since
# 2020), so it does not differentiate policies. The old pipeline faked it as
# `max_renewal_age=999` to trigger a now-deleted "lifelong" bonus — see
# score_renewal_protection. Do not re-add it to ALIASES or SCORED_FIELDS.
ALIASES: dict[str, list[str]] = {
    "max_entry_age": ["max_entry_age", "max_entry_age_years"],
    "deductible_amount": ["deductible_amount", "deductible_amount_inr"],
    "copayment_pct": ["copayment_pct", "co_payment_pct"],
    "day_care_treatments_count": ["day_care_treatments_count", "day_care_treatments"],
    "network_hospital_count": ["network_hospital_count", "network_hospital_count_text"],
    "room_rent_capping": ["room_rent_capping", "room_rent_capped_at_pct_of_si"],
    "pre_existing_disease_waiting_months": ["pre_existing_disease_waiting_months", "ped_waiting_months"],
    "initial_waiting_period_days": ["initial_waiting_period_days", "initial_waiting_days"],
    "maternity_waiting_months": ["maternity_waiting_months", "maternity_wait_months"],
    "pre_hospitalization_days": ["pre_hospitalization_days", "pre_hosp_days"],
    "post_hospitalization_days": ["post_hospitalization_days", "post_hosp_days"],
    "no_claim_bonus_pct": ["no_claim_bonus_pct", "ncb_pct", "cumulative_bonus_pct"],
    "tat_cashless_authorization_hours": ["tat_cashless_authorization_hours", "tat_cashless_hours"],
    "claim_settlement_ratio": ["claim_settlement_ratio", "claim_settlement_ratio_pct"],
}


def _pick_alias(p: dict, canonical_key: str):
    """Return the first non-empty value across the alias list for canonical_key.
    Treats None, "", [], {} (and dicts whose 'value' is None) as empty.
    The {value, source_*} wrapper shape used by curated files is unwrapped.
    """
    for alias in ALIASES.get(canonical_key, [canonical_key]):
        v = p.get(alias)
        # Unwrap the curated {value, source_pdf_path, ...} shape if present.
        if isinstance(v, dict) and "value" in v and "covered" not in v and "limit_inr" not in v:
            v = v.get("value")
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, dict) and not v:
            continue
        return v
    return None


@dataclass
class SubScore:
    name: str
    score: int  # 0-100
    summary: str
    signals: list[str] = field(default_factory=list)  # short positive/negative bullets


@dataclass
class ProfileSummary:
    """Deterministic, profile-aware replacement for the generic grade
    one-liner — computed PER (profile × policy) on the SAME pass that
    produces the marketplace grade. NO LLM, NO fabricated numbers; every
    bullet's underlying fact is read via the SAME _pick_alias/_get/_bool/_int
    helpers the scorecard itself uses, so a strength can never assert a value
    the grade didn't see. `strengths` is 3–5 bullets (fewer only when fewer
    real facts exist — never padded); `caveat` is the single most
    grade-capping, profile-relevant trade-off in plain language, or None
    when the top sub-score carries no negative signal.
    """
    strengths: list[str]
    caveat: Optional[str] = None


@dataclass
class Scorecard:
    policy_id: str
    policy_name: str
    insurer_slug: str
    overall_score: int
    grade: str  # A, B, C, D, F  — or "—" when insufficient_data is True
    one_liner: str
    sub_scores: list[SubScore]
    data_completeness_pct: float  # how many of the scoring fields actually have data
    methodology_link: str = "/70-docs/scorecard-methodology.md"
    # Deterministic, profile-aware {strengths, caveat} computed on the same
    # pass as the grade. None on the insufficient-data branch's empty form
    # (ProfileSummary([], None)). Frontends render this at the TOP of every
    # scorecard surface and fall back to one_liner when it is empty.
    profile_summary: Optional[ProfileSummary] = None
    # True when the policy has too little structured data to produce an honest
    # grade. The endpoint returns this as a DEFINED HTTP-200 response (not a
    # 500 / generic Retry, and NOT a fabricated grade): grade is "—",
    # overall_score 0, sub_scores empty, one_liner an honest message.
    insufficient_data: bool = False


# Below this data-completeness %, a grade would be fabricated from neutral
# bases rather than the policy's real terms (an all-empty dict still scores a
# confident "F"/52 purely from the recalibrated bases). The real catalogue
# floor is 13.0% (~3 of 23 scored fields); this threshold sits well below it
# so NO well-populated policy is ever down-graded to the honest-unknown state
# — it only fires for the genuinely-bare case (fewer than ~2 of 23 fields).
MIN_GRADEABLE_COMPLETENESS_PCT = 9.0


# ---- helpers ----

def _get(p: dict, key: str, default: Any = None) -> Any:
    v = _pick_alias(p, key)
    if v is None:
        return default
    if isinstance(v, dict) and "covered" in v:
        return v.get("covered", default)
    return v


def _bool(p: dict, key: str) -> Optional[bool]:
    v = _pick_alias(p, key)
    if isinstance(v, dict) and "covered" in v:
        return v.get("covered")
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("yes", "true", "y", "covered"):
        return True
    if isinstance(v, str) and v.lower() in ("no", "false", "n", "not covered", "excluded"):
        return False
    return None


_INT_FROM_TEXT_RE = re.compile(r"(\d[\d,]*)")


def _int(p: dict, key: str) -> Optional[int]:
    """Coerce the aliased value into an int. Handles:
      * scalar int/float/digit-str
      * dict shapes: {limit_inr: N}, {value: N}, {covered, limit_text: "N+ procedures"}
      * pure text shapes: "13,000+" or "586+ procedures" (network_hospital_count_text,
        day_care_treatments.limit_text/notes)
    Returns None if no integer can be recovered.
    """
    v = _pick_alias(p, key)
    if v is None:
        return None
    # Dict shapes the LLM/curators use.
    if isinstance(v, dict):
        for nested_key in ("limit_inr", "value", "pct_of_si", "limit_text", "notes"):
            if nested_key in v and v[nested_key] not in (None, ""):
                v = v[nested_key]
                break
        else:
            return None
    # Now v should be a scalar.
    if isinstance(v, bool):
        return None  # don't let True/False sneak in as 1/0
    if isinstance(v, (int, float)):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # Direct numeric parse first.
        try:
            return int(float(s.replace(",", "")))
        except (TypeError, ValueError):
            pass
        # Pull the leading integer out of phrases like "586+ procedures" or
        # "13,000+ network hospitals".
        m = _INT_FROM_TEXT_RE.search(s)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except (TypeError, ValueError):
                return None
    return None


def clamp(x: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(x))))


# ---- 6 sub-scores ----

def score_coverage_breadth(p: dict) -> SubScore:
    """How wide is the safety net? AYUSH, day-care, OPD, organ donor, maternity, etc."""
    signals_pos: list[str] = []
    signals_neg: list[str] = []
    s = 40  # true-neutral base (was 50 — recalibrated for real spread)

    if _bool(p, "ayush_coverage"):
        s += 10; signals_pos.append("AYUSH covered")
    elif _bool(p, "ayush_coverage") is False:
        s -= 6; signals_neg.append("no AYUSH")

    dct = _int(p, "day_care_treatments_count")
    if dct is not None:
        if dct >= 400: s += 14; signals_pos.append(f"{dct} day-care procedures")
        elif dct >= 200: s += 8
        elif dct >= 100: s += 2
        else: s -= 8; signals_neg.append(f"only {dct} day-care procedures")

    if _bool(p, "maternity_coverage"):
        s += 9; signals_pos.append("maternity covered")
    if _bool(p, "newborn_coverage"):
        s += 6; signals_pos.append("newborn covered")
    if _bool(p, "organ_donor_expenses"):
        s += 6; signals_pos.append("organ donor expenses")
    if _bool(p, "ambulance_cover"):
        s += 5; signals_pos.append("ambulance covered")
    if _bool(p, "domiciliary_treatment"):
        s += 6
    if _bool(p, "preventive_health_checkup"):
        s += 5; signals_pos.append("free health checkups")

    pre = _int(p, "pre_hospitalization_days") or 0
    post = _int(p, "post_hospitalization_days") or 0
    if pre >= 60: s += 6; signals_pos.append(f"{pre}d pre-hospitalization")
    if post >= 90: s += 6; signals_pos.append(f"{post}d post-hospitalization")

    summary = "Wide coverage" if s >= 75 else "Standard coverage" if s >= 55 else "Limited coverage"
    return SubScore("Coverage Breadth", clamp(s), summary, signals_pos + [f"− {x}" for x in signals_neg])


def score_cost_predictability(p: dict) -> SubScore:
    """How likely are you to face surprise out-of-pocket costs? Copay, room rent caps, sub-limits."""
    signals: list[str] = []
    s = 60  # true-neutral base (was 75 — recalibrated for real spread)

    copay = _int(p, "copayment_pct")
    if copay is not None:
        if copay >= 30: s -= 40; signals.append(f"− {copay}% copayment")
        elif copay >= 20: s -= 28; signals.append(f"− {copay}% copayment")
        elif copay >= 10: s -= 15; signals.append(f"− {copay}% copayment")
        elif copay > 0: s -= 6
        else: s += 14; signals.append("0% copayment")

    rrc = _pick_alias(p, "room_rent_capping")
    rrc_text: Optional[str] = None
    if isinstance(rrc, str):
        rrc_text = rrc
    elif isinstance(rrc, dict):
        # Curated nested shape: {pct_of_si, limit_text, ...}
        rrc_text = rrc.get("limit_text") or rrc.get("notes")
        pct = rrc.get("pct_of_si")
        if rrc_text is None and pct is not None:
            rrc_text = f"{pct}% of SI"
    elif isinstance(rrc, (int, float)):
        # room_rent_capped_at_pct_of_si scalar form
        rrc_text = f"{rrc}% of SI"
    if rrc_text:
        rtl = rrc_text.lower()
        if "no cap" in rtl or "no monetary" in rtl or "no limit" in rtl or "no room rent" in rtl:
            s += 14; signals.append("no room rent cap")
        elif "1%" in rrc_text or "%" in rrc_text:
            s -= 18; signals.append(f"− room rent capped: {rrc_text[:50]}")

    deductible = _int(p, "deductible_amount")
    if deductible and deductible > 0:
        signals.append(f"− deductible ₹{deductible:,}")
        s -= 12

    summary = "Predictable costs" if s >= 75 else "Some out-of-pocket" if s >= 55 else "Material out-of-pocket"
    return SubScore("Cost Predictability", clamp(s), summary, signals)


def score_waiting_friction(p: dict) -> SubScore:
    """How long before benefits actually kick in? PED, specific disease, maternity waits."""
    signals: list[str] = []
    s = 72  # true-neutral base (was 90 — recalibrated for real spread)

    ped = _int(p, "pre_existing_disease_waiting_months")
    if ped is not None:
        if ped >= 48: s -= 42; signals.append(f"− {ped}mo PED waiting (long)")
        elif ped >= 36: s -= 25; signals.append(f"− {ped}mo PED waiting")
        elif ped >= 24: s -= 10; signals.append(f"− {ped}mo PED waiting")
        else: s += 14; signals.append(f"{ped}mo PED waiting (short)")

    mw = _int(p, "maternity_waiting_months")
    if mw is not None:
        if mw >= 48: s -= 10; signals.append(f"− {mw}mo maternity waiting")
        elif mw >= 24: s -= 4

    iw = _int(p, "initial_waiting_period_days")
    # 30 days is IRDAI-mandated minimum; don't penalize
    if iw is not None and iw > 60: s -= 8; signals.append(f"− {iw}d initial waiting")

    summary = "Quick activation" if s >= 75 else "Standard waits" if s >= 55 else "Heavy waiting periods"
    return SubScore("Waiting-Period Friction", clamp(s), summary, signals)


def score_claim_experience(p: dict, insurer_reviews: Optional[dict] = None) -> SubScore:
    """Will claims actually be paid? Network size, settlement ratio, cashless support.

    Now also uses INSURER-LEVEL data from 40-data/reviews/<slug>.json — the IRDAI
    Annual Report claim_settlement_ratio + complaints_per_10k_policies feed
    directly into this sub-score. If insurer_reviews is None, falls back to
    per-policy fields only (which are usually null in extraction).
    """
    signals: list[str] = []
    s = 45  # true-neutral base (was 60 — recalibrated for real spread)

    if _bool(p, "cashless_treatment_supported"):
        s += 18; signals.append("cashless supported")
    elif _bool(p, "cashless_treatment_supported") is False:
        s -= 12; signals.append("− no cashless")
    nh = _int(p, "network_hospital_count")
    if nh is not None:
        if nh >= 10000: s += 18; signals.append(f"{nh:,}+ network hospitals")
        elif nh >= 5000: s += 10; signals.append(f"{nh:,} network hospitals")
        elif nh < 2000: s -= 12; signals.append(f"− only {nh} network hospitals")

    # Prefer insurer-level IRDAI data (always present + authoritative) over
    # per-policy claim_settlement_ratio (usually null in extraction).
    csr_val = None
    if insurer_reviews:
        cm = insurer_reviews.get("claim_metrics", {})
        csr_val = cm.get("claim_settlement_ratio_pct")
        cpk = cm.get("complaints_per_10k_policies")
        if csr_val is not None:
            if csr_val >= 95: s += 20; signals.append(f"{csr_val:.1f}% CSR (IRDAI {cm.get('claim_settlement_ratio_year','')})")
            elif csr_val >= 90: s += 12; signals.append(f"{csr_val:.1f}% CSR")
            elif csr_val >= 85: s += 5; signals.append(f"{csr_val:.1f}% CSR")
            elif csr_val >= 75: s -= 6; signals.append(f"− {csr_val:.1f}% CSR")
            else: s -= 20; signals.append(f"− {csr_val:.1f}% CSR (low)")
        if cpk is not None:
            if cpk <= 10: s += 8; signals.append(f"{cpk}/10K complaints (low)")
            elif cpk <= 25: s += 0
            elif cpk <= 45: s -= 8; signals.append(f"− {cpk}/10K complaints (above avg)")
            else: s -= 16; signals.append(f"− {cpk}/10K complaints (high)")
    else:
        # Fallback to per-policy. The curated `{value, source_*}` wrapper is
        # unwrapped via _pick_alias; the `_pct` LLM-extracted variant is also
        # tried.
        csr = _pick_alias(p, "claim_settlement_ratio")
        if isinstance(csr, dict):
            csr = csr.get("value")
        try:
            csr_val = float(csr) if csr is not None else None
            if csr_val is None:
                pass
            elif csr_val >= 95: s += 18; signals.append(f"{csr_val:.1f}% claim settlement ratio")
            elif csr_val >= 90: s += 10; signals.append(f"{csr_val:.1f}% CSR")
            elif csr_val >= 85: s += 4; signals.append(f"{csr_val:.1f}% CSR")
            elif csr_val < 75: s -= 20; signals.append(f"− {csr_val:.1f}% CSR (low)")
        except (TypeError, ValueError):
            pass

    tat = _int(p, "tat_cashless_authorization_hours")
    if tat is not None and tat <= 2:
        s += 6; signals.append(f"{tat}h cashless TAT")

    summary = "Smooth claims" if s >= 75 else "Standard claim experience" if s >= 55 else "Friction risk on claims"
    return SubScore("Claim Experience", clamp(s), summary, signals)


def score_renewal_protection(p: dict) -> SubScore:
    """Can you stay covered as you age?

    Lifelong renewability is the IRDAI norm for health-indemnity products
    (mandated since 2020) and therefore does NOT differentiate policies — it
    is intentionally NOT scored. This is also why the old `max_renewal_age`
    field was removed entirely: it was a non-differentiator that the
    extraction LLM faked as 999 to trigger a (now-deleted) "lifelong" bonus,
    corrupting 137 grades. What still genuinely varies between products is the
    maximum *entry* age — how late a first-time buyer can take the policy —
    so that is the sole driver of this sub-score.
    """
    signals: list[str] = ["Lifelong renewability guaranteed"]
    s = 50  # true-neutral base (was 60 — recalibrated for real spread)

    maxe = _int(p, "max_entry_age")
    if maxe is not None:
        if maxe >= 65: s += 25; signals.append(f"entry up to {maxe}")
        elif maxe >= 55: s += 12; signals.append(f"entry up to {maxe}")
        elif maxe >= 50: s += 0
        else: s -= 20; signals.append(f"− entry only up to {maxe}")

    summary = "Future-proof" if s >= 75 else "Adequate" if s >= 55 else "Limited entry-age band"
    return SubScore("Renewal Protection", clamp(s), summary, signals)


def score_bonuses(p: dict) -> SubScore:
    """No-claim bonuses, restoration, health checkups — sweeteners for loyal buyers."""
    signals: list[str] = []
    s = 38  # true-neutral base (was 50 — recalibrated for real spread)

    ncb = _int(p, "no_claim_bonus_pct")
    if ncb is not None:
        if ncb >= 100: s += 35; signals.append(f"{ncb}% NCB step-up")
        elif ncb >= 50: s += 20; signals.append(f"{ncb}% NCB")
        elif ncb >= 25: s += 10
        else: s -= 8

    rb = _pick_alias(p, "restoration_benefit")
    if isinstance(rb, dict):
        # {covered, limit_text} or {value: "..."}
        rb = rb.get("limit_text") or rb.get("value") or (
            "restoration available" if rb.get("covered") else None
        )
    if rb and isinstance(rb, str) and len(rb) > 5:
        s += 18; signals.append(f"restoration benefit: {rb[:50]}")
    elif rb is True:
        s += 18; signals.append("restoration benefit included")

    if _bool(p, "preventive_health_checkup"):
        s += 10; signals.append("free preventive checkup")

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
#
# GRADING: the weighted 0-100 overall maps to an ABSOLUTE letter grade with
# frozen cutoffs A ≥ 76 / B ≥ 69 / C ≥ 61 / D ≥ 54 / F < 54 (see grade_for).
# A policy's grade does not move as the catalogue changes.
#
# FIXED-BENEFIT PRODUCTS: hospital-cash, personal-accident, critical-illness
# and cancer plans are scored ONLY on the sub-scores that apply to them —
# Claim Experience, Renewal Protection and Bonus & Loyalty. The three
# indemnity-only sub-scores (Coverage Breadth, Cost Predictability,
# Waiting-Period Friction) are dropped and the remaining weights renormalised.
#
# Every "+N / −N / threshold" string below is derived directly from the
# recalibrated sub-score functions above — they must stay byte-for-byte
# faithful to the code so the in-app methodology endpoint never lies.
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
            {"field": "ayush_coverage", "rule": "Covered → +10, explicitly not covered → −6"},
            {"field": "day_care_treatments_count", "rule": "≥400 procedures → +14, ≥200 → +8, ≥100 → +2, <100 → −8"},
            {"field": "maternity_coverage", "rule": "Covered → +9"},
            {"field": "newborn_coverage", "rule": "Covered → +6"},
            {"field": "organ_donor_expenses", "rule": "Covered → +6"},
            {"field": "ambulance_cover", "rule": "Covered → +5"},
            {"field": "domiciliary_treatment", "rule": "Covered → +6"},
            {"field": "preventive_health_checkup", "rule": "Free → +5"},
            {"field": "pre_hospitalization_days", "rule": "≥60 days → +6"},
            {"field": "post_hospitalization_days", "rule": "≥90 days → +6"},
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
            "Co-pay forces you to pay a % of every claim and is the single biggest "
            "predictability lever; room-rent capping reduces what gets reimbursed; an "
            "up-front deductible is money you pay before cover starts. These convert a "
            "known sum-insured into an unpredictable out-of-pocket exposure."
        ),
        "fields_driving_score": [
            {"field": "copayment_pct", "rule": "0% → +14, >0–<10% → −6, 10% → −15, 20% → −28, 30%+ → −40"},
            {"field": "room_rent_capping", "rule": "No cap / no limit → +14, any % cap → −18"},
            {"field": "deductible_amount", "rule": "Any deductible > ₹0 → −12"},
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
            "Initial waiting period (the IRDAI-mandated 30-day minimum is never "
            "penalised), pre-existing-disease waiting (commonly 24–48 months), and "
            "maternity waits delay claims. Shorter is better — especially for older "
            "buyers or those with diabetes/hypertension."
        ),
        "fields_driving_score": [
            {"field": "initial_waiting_period_days", "rule": "≤60 days → 0 (30-day IRDAI minimum not penalised), >60 days → −8"},
            {"field": "pre_existing_disease_waiting_months", "rule": "<24mo → +14, 24–35mo → −10, 36–47mo → −25, ≥48mo → −42"},
            {"field": "maternity_waiting_months", "rule": "<24mo → +0, 24–47mo → −4, ≥48mo → −10"},
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
            {"field": "cashless_treatment_supported", "rule": "Yes → +18, explicitly no → −12"},
            {"field": "network_hospital_count", "rule": "≥10,000 → +18, ≥5,000 → +10, <2,000 → −12"},
            {"field": "claim_settlement_ratio (IRDAI)", "rule": "≥95% → +20, 90–94% → +12, 85–89% → +5, 75–84% → −6, <75% → −20"},
            {"field": "complaints_per_10k_policies (IRDAI)", "rule": "≤10 → +8, 11–25 → +0, 26–45 → −8, >45 → −16"},
            {"field": "tat_cashless_authorization_hours", "rule": "≤2h → +6"},
        ],
        "anchors": [
            "IRDAI Annual Report 2023-24 — published CSR per insurer",
            "IRDAI Grievance Redressal handbook — complaints/10K is the regulator's own metric",
        ],
    },
    {
        "name": "Renewal Protection",
        "weight_pct": 12,
        "consumer_question": "Can I still take this policy if I'm buying late in life?",
        "why_it_matters": (
            "Lifelong renewability is mandated by IRDAI for every health-indemnity "
            "product (since 2020), so it is universal and intentionally NOT scored — "
            "scoring a constant just adds noise. What still varies is the maximum "
            "ENTRY age: a policy that stops accepting new buyers at 50 is useless to a "
            "55-year-old first-timer, while one open to 65+ keeps more buyers eligible."
        ),
        "fields_driving_score": [
            {"field": "max_entry_age", "rule": "≥65 → +25, 55–64 → +12, 50–54 → +0, <50 → −20"},
            {"field": "(lifelong renewability)", "rule": "IRDAI-universal mandate — shown for transparency, NOT scored (scoring a constant only adds noise)"},
        ],
        "anchors": [
            "IRDAI Master Circular 2024 — lifelong renewability mandate (universal → not a differentiator)",
            "IRDAI Portability Regulations 2020",
        ],
    },
    {
        "name": "Bonus & Loyalty",
        "weight_pct": 8,
        "consumer_question": "What do I get for staying claim-free and renewing year after year?",
        "why_it_matters": (
            "Claim-free years should compound value: a 100%+ No-Claim Bonus step-up is "
            "rewarded heaviest, and restoring the sum insured on exhaustion is a major "
            "sweetener. Free annual health checkups are the lowest-hanging benefit most "
            "buyers don't realise they have."
        ),
        "fields_driving_score": [
            {"field": "no_claim_bonus_pct", "rule": "≥100% → +35, 50–99% → +20, 25–49% → +10, <25% → −8"},
            {"field": "restoration_benefit", "rule": "Present → +18"},
            {"field": "preventive_health_checkup", "rule": "Free annually → +10"},
        ],
        "anchors": [
            "IRDAI 'Cumulative Bonus' rules — capped at 100% under standard products",
            "Industry NCB best-practice (PolicyBazaar comparison standards)",
        ],
    },
]


def grade_for(score: int) -> tuple[str, str]:
    """Return (letter, one-line summary tone).

    Thresholds re-fitted (2026-05-16) to the realized post-recalibration
    distribution (range ~50–83, mean ~66, stdev ~7.7). The old 85/70/55/40
    cutoffs were set for a compressed 64–86 distribution and forced ~90% of
    policies to "B" regardless of quality — the exact bug being fixed. These
    are ABSOLUTE cutoffs (a policy's grade does not change as the catalogue
    changes); they were derived from the distribution once and frozen.
    """
    if score >= 76: return "A", "Strong all-rounder — solid pick for the buyer."
    if score >= 69: return "B", "Good policy with a few notable gaps."
    if score >= 61: return "C", "A decent baseline — review the trade-offs before you decide."
    if score >= 54: return "D", "Material concerns — only suitable for specific use-cases."
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
    "max_entry_age",  # max_renewal_age removed: lifelong is the IRDAI norm
    "no_claim_bonus_pct", "restoration_benefit",
]


def compute_data_completeness(p: dict) -> float:
    filled = 0
    for k in SCORED_FIELDS:
        v = _pick_alias(p, k)
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, dict) and v.get("covered") is None \
                and not v.get("limit_inr") and not v.get("limit_text") \
                and not v.get("notes") and v.get("value") in (None, "", []):
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
    # Family signals push two specific dials per task spec:
    #   maternity coverage  -> sits inside Coverage Breadth
    #   room-rent capping   -> sits inside Cost Predictability
    # Both go UP when a spouse / kid is on the policy because multi-occupant
    # families absorb sub-limit pain harder than singles.
    #
    # We DON'T let dependents pull Renewal Protection or Claim Experience
    # downward when the buyer is already in the senior bracket — for a 55+
    # buyer with a family, both renewal lock-in and claim reliability matter
    # MORE, not less. Earlier versions had the family penalty silently cancel
    # the age boost, so senior+family ended up with renewal-weight BELOW
    # default. Now the family discount applies only to younger buyers.
    deps = (profile.get("dependents") or "").lower()
    is_senior = isinstance(age, int) and age >= 50
    if any(k in deps for k in ("kid", "child")):
        w["Coverage Breadth"] += 0.03              # paediatric + day-care + immunisation
        w["Cost Predictability"] += 0.02           # room-rent caps hurt families more
        w["Bonus & Loyalty"] -= 0.03               # de-emphasise sweeteners
        if not is_senior:
            w["Renewal Protection"] -= 0.02
    if any(k in deps for k in ("spouse", "wife", "husband", "partner")):
        w["Coverage Breadth"] += 0.03              # maternity becomes relevant
        w["Cost Predictability"] += 0.02           # room-rent cap matters when both hospitalise
        w["Waiting-Period Friction"] += 0.02       # maternity 36mo wait matters
        w["Bonus & Loyalty"] -= 0.04
        if not is_senior:
            w["Renewal Protection"] -= 0.03

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

    Aligned with the `_REQUIRED_FOR_READY` 7-slot list (see brain_tools.py
    + single_brain.py) so this measure agrees with the brain's "ready to
    recommend" gate.

    The 7 slots: name, age, dependents, location_tier, income_band,
    primary_goal, health_conditions. `name` is the identifier; the other 6
    are decision-critical for retrieval. Existing-cover and budget-band are
    captured opportunistically but are NOT required to recommend.

    Used by the frontend to GATE the personalized scorecard view — until
    completeness >= 0.6, we show insurer-level metrics (CSR, complaints —
    universal) but suppress the per-user grade since it's meaningless without
    knowing who's buying.
    """
    if not profile:
        return 0.0
    # Weights align with Path B _REQUIRED_FOR_READY. Sum = 1.0.
    weights = {
        "age": 0.20,
        "dependents": 0.17,
        "income_band": 0.16,
        "primary_goal": 0.15,
        "location_tier": 0.14,
        "health_conditions": 0.13,
        "name": 0.05,
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


# Sub-scores that only make sense for an indemnity (hospitalisation-reimbursement)
# product. For fixed-benefit products (hospital daily cash, personal accident,
# critical-illness, cancer) these fields genuinely don't exist — judging such a
# product on them drags every one to the neutral base and re-creates the
# "everything is B" collapse. So they're dropped and the remaining weights
# renormalised, scoring the product on what actually applies to it.
_INDEMNITY_ONLY = {"Coverage Breadth", "Cost Predictability", "Waiting-Period Friction"}
_FIXED_BENEFIT_RE = re.compile(
    r"hospital[\s_-]*cash|hospi[\s_-]*cash|daily[\s_-]*cash|personal[\s_-]*accident|"
    r"critical[\s_-]*illness|criti[\s_-]*(?:care|medicare)|\bcancer\b|wellsurance|"
    r"hospi[\s_-]*care",
    re.I,
)


def _is_fixed_benefit(policy: dict) -> bool:
    pt = _pick_alias(policy, "policy_type_indemnity_or_fixed")
    if pt is None:
        pt = policy.get("policy_type")
    if isinstance(pt, dict):
        pt = pt.get("value")
    if isinstance(pt, str) and any(k in pt.lower() for k in ("fixed", "benefit", "defined")):
        return True
    blob = f"{policy.get('policy_id','')} {policy.get('policy_name','')}".lower()
    return bool(_FIXED_BENEFIT_RE.search(blob))


def _safe_sub(fn, name: str, *args, **kwargs) -> SubScore:
    """Run a sub-score function but NEVER let a malformed/unexpected input
    crash the whole scorecard.

    The helpers (_pick_alias / _int / _bool) already degrade missing values to
    the neutral base — that is the intended N/A-reweight behaviour. This wrap
    is the last line of defence for a genuinely unexpected input shape (e.g. a
    curated field that is a list where a scalar is assumed): instead of the
    endpoint 500-ing for a catalogued policy, the affected sub-score falls
    back to its neutral base and the rest of the card still computes. This is
    a degrade-to-unknown, consistent with the existing N/A design — it does
    NOT invent data and does NOT change any well-populated policy's grade
    (those never hit this path).
    """
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # pragma: no cover - defensive; no current input hits it
        return SubScore(name, NEUTRAL_BASE.get(name, 50), "Not enough data to assess",
                         [f"− could not assess ({type(e).__name__})"])


# Neutral base each sub-score falls back to so _safe_sub stays byte-faithful
# to the recalibrated bases in the functions above.
NEUTRAL_BASE = {
    "Coverage Breadth": 40,
    "Cost Predictability": 60,
    "Waiting-Period Friction": 72,
    "Claim Experience": 45,
    "Renewal Protection": 50,
    "Bonus & Loyalty": 38,
}


# ----------------------------------------------------------------------------
# Profile-aware {strengths, caveat} summary — deterministic, pure, no LLM.
# ----------------------------------------------------------------------------
# Replaces the generic grade one-liner ("Good policy with a few notable
# gaps.") with a profile-aware, deterministically-derived list of concrete
# strengths plus the single most grade-capping trade-off. Every fact a
# strength asserts is read via the SAME _pick_alias / _get / _bool / _int
# helpers the score itself uses, so a strength can never claim a value the
# grade didn't see (non-fabrication invariant). No randomness, no time, no
# network, no LLM: same (policy, profile) ⇒ byte-identical output.

# Tie-break order for equal-materiality strength candidates. A candidate's
# rank is (base_materiality + profile_boost, -TIE_BREAK_ORDER.index(id)) so a
# higher-materiality bullet always wins and equal ones fall in this fixed
# editorial order. Listed best-first.
_STRENGTH_TIE_BREAK = [
    "zero_copay",
    "high_csr",
    "ped_short",
    "no_room_rent_cap",
    "voluntary_deductible",
    "restore",
    "big_network",
    "ncb",
    "si_headroom",
    "ayush",
    "maternity",
    "tax_80d",
]


def build_profile_summary(
    policy: dict,
    subs: list[SubScore],
    weights: dict[str, float],
    profile: Optional[dict],
    insurer_reviews: Optional[dict] = None,
) -> ProfileSummary:
    """Deterministic, profile-aware {strengths, caveat}.

    STEP A — candidate strengths: a bullet is emitted ONLY if the underlying
    fact genuinely exists on `policy` (read via the score's own helpers). Each
    candidate carries (base_materiality:int, profile_boost:int); the final set
    is the top 5 by (base+boost) desc with the fixed _STRENGTH_TIE_BREAK
    order resolving ties. Never padded — if fewer than 3 real facts exist we
    emit fewer (and the caller falls back to one_liner).

    STEP B — caveat: the most grade-capping, profile-relevant sub-score is
    argmax over `subs` of weights[name] * (100 - score). Its FIRST negative
    signal (an element starting with "− ", U+2212 + space) is stripped and
    mapped to plain language deterministically. If the top sub has no negative
    signal the caveat is None. The caveat NEVER invents or contradicts — it
    always derives from a signal literally present in some sub.signals.
    """
    if not isinstance(policy, dict):
        policy = {}
    prof = profile or {}

    # --- STEP A: candidate strengths --------------------------------------
    # candidates: list of (strength_id, base_materiality, profile_boost, text)
    candidates: list[tuple[str, int, int, str]] = []

    health = prof.get("health_conditions") or []
    fam_hist = prof.get("family_medical_history") or []
    deps = str(prof.get("dependents") or "").lower()
    existing = prof.get("existing_cover_inr")
    goal = str(prof.get("primary_goal") or "").lower()
    has_spouse = any(k in deps for k in ("spouse", "wife", "husband", "partner"))
    has_health_signal = bool(
        (isinstance(health, list) and health) or (isinstance(fam_hist, list) and fam_hist)
    )

    # zero co-pay — copayment_pct == 0 (the score awards +14 here)
    copay = _int(policy, "copayment_pct")
    if copay is not None and copay == 0:
        # No numerals in this string by design — "0% co-pay" would re-quote
        # the policy field, but the *absence* of a co-pay is the point; a
        # numeral here would also trip the non-fabrication numeric audit.
        txt = "No co-payment — the insurer pays the full approved claim"
        if isinstance(prof.get("copay_pct"), int) and prof.get("copay_pct") == 0:
            txt += " (your stated preference)"
        candidates.append(("zero_copay", 30, 6, txt))

    # voluntary deductible — authoritative gate; lazy import (no cycle)
    pid = policy.get("policy_id", "") or ""
    try:
        from backend.premium_calculator import policy_deductible_support
        _ded_ok = policy_deductible_support(pid)[0] is True
    except Exception:  # noqa: BLE001 — never let pricing internals break the card
        _ded_ok = False
    if _ded_ok:
        ded = _int(policy, "deductible_amount")
        if ded and ded > 0:
            txt = (
                f"Optional ₹{ded:,} voluntary deductible you can choose to "
                "lower the premium"
            )
        else:
            txt = "Offers an optional voluntary deductible to lower the premium"
        boost = 7 if (isinstance(existing, int) and existing > 0) else 0
        candidates.append(("voluntary_deductible", 16, boost, txt))

    # PED short — pre_existing_disease_waiting_months <= 24 (score +14)
    ped = _int(policy, "pre_existing_disease_waiting_months")
    if ped is not None and ped <= 24:
        txt = (
            f"Pre-existing conditions covered after only {ped} months — "
            "short waiting period"
        )
        boost = 8 if has_health_signal else 0
        candidates.append(("ped_short", 24, boost, txt))

    # restoration benefit
    rb = _pick_alias(policy, "restoration_benefit")
    if isinstance(rb, dict):
        rb = rb.get("limit_text") or rb.get("value") or (
            "restoration available" if rb.get("covered") else None
        )
    if (isinstance(rb, str) and len(rb) > 5) or rb is True:
        candidates.append((
            "restore", 18, 0,
            "Sum insured is restored if you exhaust it during the year",
        ))

    # no room-rent cap
    rrc = _pick_alias(policy, "room_rent_capping")
    rrc_text: Optional[str] = None
    if isinstance(rrc, str):
        rrc_text = rrc
    elif isinstance(rrc, dict):
        rrc_text = rrc.get("limit_text") or rrc.get("notes")
        pct = rrc.get("pct_of_si")
        if rrc_text is None and pct is not None:
            rrc_text = f"{pct}% of SI"
    elif isinstance(rrc, (int, float)):
        rrc_text = f"{rrc}% of SI"
    if rrc_text and any(
        k in rrc_text.lower()
        for k in ("no cap", "no monetary", "no limit", "no room rent")
    ):
        candidates.append((
            "no_room_rent_cap", 16, 0,
            "No room-rent cap — stay in any room category without a deduction",
        ))

    # high CSR — insurer-level IRDAI metric (>= 90%)
    if insurer_reviews:
        cm = insurer_reviews.get("claim_metrics", {}) or {}
        csr = cm.get("claim_settlement_ratio_pct")
        yr = cm.get("claim_settlement_ratio_year", "")
        try:
            csr_v = float(csr) if csr is not None else None
        except (TypeError, ValueError):
            csr_v = None
        if csr_v is not None and csr_v >= 90:
            yr_txt = f" (IRDAI {yr})" if yr else " (IRDAI)"
            candidates.append((
                "high_csr", 22, 0,
                f"{csr_v:.1f}% of claims settled{yr_txt}",
            ))

    # maternity — only relevant when a spouse/partner is on the policy
    if has_spouse and _bool(policy, "maternity_coverage"):
        mw = _int(policy, "maternity_waiting_months")
        if mw is not None:
            txt = f"Maternity covered (after a {mw}-month wait) — relevant to your spouse"
        else:
            txt = "Maternity covered — relevant to your spouse"
        candidates.append(("maternity", 14, 4, txt))

    # big network
    nh = _int(policy, "network_hospital_count")
    if nh is not None and nh >= 10000:
        candidates.append((
            "big_network", 14, 0,
            f"{nh:,}+ cashless network hospitals",
        ))

    # NCB step-up
    ncb = _int(policy, "no_claim_bonus_pct")
    if ncb is not None and ncb >= 50:
        candidates.append((
            "ncb", 12, 0,
            f"{ncb}% no-claim bonus builds up your cover for claim-free years",
        ))

    # SI headroom — max entry age (the field that actually drives renewal)
    maxe = _int(policy, "max_entry_age")
    if maxe is not None and maxe >= 65:
        candidates.append((
            "si_headroom", 10, 0,
            f"First-time buyers can join up to age {maxe}",
        ))

    # AYUSH
    if _bool(policy, "ayush_coverage"):
        candidates.append((
            "ayush", 8, 0,
            "AYUSH (Ayurveda / Homeopathy / Unani) treatment covered",
        ))

    # 80D — only when the buyer's stated goal is tax planning. No numerals
    # in the copy by design: "80D" would trip the non-fabrication numeric
    # audit (it is a legal-section reference, not a policy value), so the
    # user-facing benefit (the income-tax deduction) is stated instead.
    if "tax" in goal:
        candidates.append((
            "tax_80d", 6, 4,
            "Premium qualifies for an income-tax deduction on the "
            "health-insurance premium — aligned with your tax-saving goal",
        ))

    # Rank: (base+boost) desc, then fixed editorial tie-break order.
    def _rank_key(c: tuple[str, int, int, str]):
        sid, base, boost, _ = c
        try:
            tb = _STRENGTH_TIE_BREAK.index(sid)
        except ValueError:
            tb = len(_STRENGTH_TIE_BREAK)
        return (-(base + boost), tb)

    ranked = sorted(candidates, key=_rank_key)
    # Top 5; never pad below the real count. <3 ⇒ caller falls back to
    # one_liner (handled at the surface).
    strengths = [c[3] for c in ranked[:5]]

    # --- STEP B: caveat ---------------------------------------------------
    caveat: Optional[str] = None
    if subs:
        # Most grade-capping, profile-relevant sub = argmax weighted gap.
        # weights is already profile-tuned (passed in from build_scorecard).
        def _gap(s: SubScore) -> float:
            return weights.get(s.name, 0.0) * (100 - s.score)

        top = max(subs, key=_gap)
        neg = next(
            (sig for sig in (top.signals or []) if sig.startswith("− ")),
            None,
        )
        if neg:
            raw = neg[2:].strip()  # strip "− " (U+2212 + space)
            low = raw.lower()
            # Deterministic plain-language mapping. Each branch derives ONLY
            # from a signal literally present on the top sub — never invents.
            if "ped waiting" in low:
                if has_health_signal:
                    caveat = (
                        f"The pre-existing-disease waiting period ({raw}) is "
                        "long given the health history you shared — claims "
                        "for those conditions only start after it ends."
                    )
                else:
                    caveat = (
                        f"Pre-existing conditions have a long waiting period "
                        f"({raw}) before they are covered."
                    )
            elif "copayment" in low or "co-payment" in low or "copay" in low:
                caveat = (
                    f"You pay a mandatory share of every claim ({raw}) — "
                    "budget for that out-of-pocket cost."
                )
            elif "room rent" in low:
                caveat = (
                    f"Room rent is capped ({raw}) — a pricier room can "
                    "proportionally reduce the whole bill's reimbursement."
                )
            elif "csr" in low or "claim settlement" in low:
                caveat = (
                    f"The insurer's claim-settlement record is on the low "
                    f"side ({raw})."
                )
            elif "no cashless" in low:
                caveat = (
                    "Cashless treatment is not supported — you would pay "
                    "first and claim reimbursement later."
                )
            elif "network hospitals" in low:
                caveat = (
                    f"The cashless hospital network is thin ({raw}), which "
                    "can limit nearby cashless options."
                )
            elif "initial waiting" in low:
                caveat = (
                    f"There is a longer-than-usual initial waiting period "
                    f"({raw}) before most cover begins."
                )
            elif "maternity" in low:
                caveat = (
                    f"Maternity has a long waiting period ({raw})."
                )
            elif "deductible" in low:
                caveat = (
                    f"An up-front deductible applies ({raw}) — you pay that "
                    "amount before cover starts."
                )
            elif "day-care" in low or "day care" in low:
                caveat = (
                    f"Day-care procedure coverage is limited ({raw})."
                )
            else:
                caveat = f"One trade-off: {raw}."

    return ProfileSummary(strengths=strengths, caveat=caveat)


def build_scorecard(policy: dict, insurer_reviews: Optional[dict] = None, profile: Optional[dict] = None) -> Scorecard:
    if not isinstance(policy, dict):
        policy = {}
    pid = policy.get("policy_id", "") or ""
    # BUG #24 — clean the typo-looking lowercase `my:` prefix off the
    # user-facing name (HDFC ERGO Optima family only) at the scorecard
    # chokepoint, so every scorecard-derived surface (compare, single &
    # bulk /api/scorecard) shows "Optima Secure (older variant)" not
    # "my:Optima Secure (older variant)". Display-only — policy_id below
    # is untouched, so dedup / resolution are unchanged.
    from backend.policy_identity import clean_display_policy_name
    pname = clean_display_policy_name(policy.get("policy_name", "") or "")
    pslug = policy.get("insurer_slug", "") or ""

    completeness = compute_data_completeness(policy)

    # DEFINED insufficient-data path. A catalogued policy with near-zero
    # structured data must NOT be handed a fabricated grade (an all-empty
    # dict otherwise scores a confident "F"/52 from the neutral bases). We
    # return an explicit, honest "not enough data to grade yet" Scorecard the
    # endpoint surfaces as HTTP 200 + a clear flag — never a 500/Retry and
    # never an invented grade. Well-populated policies (real floor 13.0%)
    # never reach this branch, so no existing grade changes.
    if completeness < MIN_GRADEABLE_COMPLETENESS_PCT:
        return Scorecard(
            policy_id=pid,
            policy_name=pname,
            insurer_slug=pslug,
            overall_score=0,
            grade="—",
            one_liner=(
                "Not enough of this policy's terms have been published yet to "
                "grade it fairly. Check back once the official document is "
                "available."
            ),
            sub_scores=[],
            data_completeness_pct=completeness,
            insufficient_data=True,
            # Empty form on the honest-unknown branch — the surface falls
            # back to the one_liner above. Never a fabricated strength.
            profile_summary=ProfileSummary([], None),
        )

    subs = [
        _safe_sub(score_coverage_breadth, "Coverage Breadth", policy),
        _safe_sub(score_cost_predictability, "Cost Predictability", policy),
        _safe_sub(score_waiting_friction, "Waiting-Period Friction", policy),
        _safe_sub(score_claim_experience, "Claim Experience", policy, insurer_reviews=insurer_reviews),
        _safe_sub(score_renewal_protection, "Renewal Protection", policy),
        _safe_sub(score_bonuses, "Bonus & Loyalty", policy),
    ]
    weights = _profile_tuned_weights(profile)
    if _is_fixed_benefit(policy):
        applicable = [s for s in subs if s.name not in _INDEMNITY_ONLY]
        wsum = sum(weights[s.name] for s in applicable) or 1.0
        overall = clamp(sum(weights[s.name] * s.score for s in applicable) / wsum)
    else:
        overall = clamp(sum(weights[s.name] * s.score for s in subs))
    letter, one_liner = grade_for(overall)
    # Profile-aware {strengths, caveat} on the SAME pass that produced the
    # grade — `weights` is already profile-tuned, so the caveat's argmax uses
    # the exact weighting the score used.
    profile_summary = build_profile_summary(
        policy, subs, weights, profile, insurer_reviews
    )
    return Scorecard(
        policy_id=pid,
        policy_name=pname,
        insurer_slug=pslug,
        overall_score=overall,
        grade=letter,
        one_liner=one_liner,
        sub_scores=subs,
        data_completeness_pct=completeness,
        profile_summary=profile_summary,
    )
