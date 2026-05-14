"""Canonical Pydantic schema for an Indian health insurance policy.

This schema is grounded in the IRDAI Customer Information Sheet (CIS) format
mandated by the "Health Insurance Standardisation" guidelines and IRDAI master
circular on health insurance products. Field choices mirror the disclosures
insurers are legally required to publish, supplemented by the comparison
dimensions used by aggregators (PolicyBazaar, InsuranceDekho, Acko) so that the
extracted records support both regulator-grade comparison and consumer-grade
filtering.

Design principles
-----------------
1. Most fields are Optional[...] because PDF extraction is lossy; the
   `extraction_confidence_pct` field captures uncertainty.
2. Enums are used only where the value set is bounded by regulation
   (policy type, geographic scope, premium mode). Free-text fields stay as
   `str` to avoid over-constraining the extractor.
3. The schema is forward-compatible: `Config.extra = "allow"` keeps unknown
   keys; v2 additions for Life / Motor / Travel can layer category-specific
   optional fields without breaking existing consumers.
4. Money is stored as INR integers (paise precision is not needed for
   policy-level comparison). Percentages are stored as floats in 0..100, not
   0..1, to match how policy documents present them.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Normalisation helpers — let the LLM emit natural-language variants while
# we still store a clean enum value. The extractor frequently produces
# "family floater" (with space) or "self+spouse+children" (kids spelled as
# children) — these are semantically correct, just lexically off from our
# canonical enum string. Without these normalisers the strict Pydantic
# validator rejects ~40% of otherwise-good NIM V4-Pro extractions.
# ---------------------------------------------------------------------------


def _norm_token(s: str) -> str:
    """Lower, trim, collapse whitespace, replace spaces+hyphens with underscore."""
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    return s


_FAMILY_SYNONYMS = {
    "children": "kids",
    "child": "kids",
    "kid": "kids",
    "spouse_and_children": "self+spouse+kids",
    "spouse_and_kids": "self+spouse+kids",
}


def _norm_family(v: str) -> str:
    """Map common LLM variants to canonical FamilyComposition values."""
    s = _norm_token(v).replace("_", "+")
    parts = []
    for p in s.split("+"):
        parts.append(_FAMILY_SYNONYMS.get(p, p))
    return "+".join(parts)


_GEO_SYNONYMS = {
    "india": "pan_india",
    "india_only": "pan_india",
    "pan_india_only": "pan_india",
    "domestic": "pan_india",
    "domestic_india": "pan_india",
    "global": "worldwide",
    "global_ex_usa_canada": "worldwide_ex_usa_canada",
    "worldwide_excluding_usa_canada": "worldwide_ex_usa_canada",
}


def _norm_geo(v: str) -> str:
    """Map common LLM variants to canonical GeographicScope values. Falls back
    to pan_india if the value isn't recognised — most Indian policies are
    pan-India by default; honestly recording unknown as 'pan_india' is safer
    than rejecting the whole extraction."""
    s = _norm_token(v)
    s = _GEO_SYNONYMS.get(s, s)
    return s if s in _VALID_GEO_SCOPES else "pan_india"


_POLICY_TYPE_SYNONYMS = {
    "individual_family_floater": "family_floater",
    "individual_or_family_floater": "family_floater",
    "indemnity": "individual",
    "fixed_benefit": "other",
    "hospital_cash": "other",
    "personal_accident_insurance": "personal_accident",
    "pa": "personal_accident",
    "ci": "critical_illness",
    "cancer": "critical_illness",
    "diabetes": "disease_specific",
    "specific_disease": "disease_specific",
}

_VALID_POLICY_TYPES = {
    "individual", "family_floater", "senior_citizen", "critical_illness",
    "top_up", "super_top_up", "disease_specific", "group",
    "personal_accident", "other",
}


def _norm_policy_type_val(v: str) -> str:
    """Map common LLM variants to canonical PolicyType values. Strips slashes
    and falls back to 'other' for anything we cannot pin to a known type
    (better to record 'other' than to reject the whole policy)."""
    # Strip slashes / commas / pipes — V4-Pro sometimes emits compound values
    # like 'individual / family floater'. Take the first segment.
    s = v.strip()
    for sep in ("/", ",", "|", ";"):
        if sep in s:
            s = s.split(sep)[0]
            break
    s = _norm_token(s)
    s = _POLICY_TYPE_SYNONYMS.get(s, s)
    return s if s in _VALID_POLICY_TYPES else "other"


_VALID_GEO_SCOPES = {"pan_india", "regional", "worldwide", "worldwide_ex_usa_canada"}


# ---------------------------------------------------------------------------
# Enumerations (bounded value sets)
# ---------------------------------------------------------------------------


class PolicyType(str, Enum):
    """Product category as defined by IRDAI product filings."""

    INDIVIDUAL = "individual"
    FAMILY_FLOATER = "family_floater"
    SENIOR_CITIZEN = "senior_citizen"
    CRITICAL_ILLNESS = "critical_illness"
    TOP_UP = "top_up"
    SUPER_TOP_UP = "super_top_up"
    DISEASE_SPECIFIC = "disease_specific"
    GROUP = "group"
    PERSONAL_ACCIDENT = "personal_accident"
    OTHER = "other"


class GeographicScope(str, Enum):
    """Territorial cover for hospitalization claims."""

    PAN_INDIA = "pan_india"
    REGIONAL = "regional"
    WORLDWIDE = "worldwide"
    WORLDWIDE_EX_USA_CANADA = "worldwide_ex_usa_canada"


class PremiumMode(str, Enum):
    """Premium payment frequency permitted by the insurer."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    ANNUAL = "annual"
    SINGLE = "single"


class FamilyComposition(str, Enum):
    """Allowed family composition under a single policy contract."""

    SELF = "self"
    SELF_SPOUSE = "self+spouse"
    SELF_SPOUSE_KIDS = "self+spouse+kids"
    SELF_SPOUSE_KIDS_PARENTS = "self+spouse+kids+parents"
    MULTI_GENERATION = "multi_generation"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Composite value objects
# ---------------------------------------------------------------------------


class CoverageItem(BaseModel):
    """Reusable shape for a boolean-with-detail benefit.

    Many CIS rows are 'Yes, with limit X subject to Y waiting period'. Storing
    this as `{covered, limit_inr, limit_text, notes}` preserves comparability
    while keeping the original CIS wording for citation in the voice answer.
    """

    covered: Optional[bool] = Field(None, description="True if the benefit is included.")
    limit_inr: Optional[int] = Field(None, description="Numeric monetary limit in INR, if any.")
    limit_text: Optional[str] = Field(
        None,
        description="Verbatim limit text from the policy (e.g. '1% of SI per day' or 'up to ₹50,000').",
    )
    notes: Optional[str] = Field(None, description="Conditions, sub-clauses, or carve-outs.")


# ---------------------------------------------------------------------------
# Main schema
# ---------------------------------------------------------------------------


class HealthPolicy(BaseModel):
    """Canonical record for one Indian health insurance policy variant.

    One row == one (insurer, policy_name, variant) tuple. Different sum-insured
    options live in `sum_insured_options_inr`, not as separate rows, because
    the underlying contract is the same.

    Extensibility: adding Life / Motor / Travel in v2 is done by introducing
    sibling models (`LifePolicy`, `MotorPolicy`) that share Identity + Source
    fields. Do NOT mutate or remove existing fields here; downstream extractors
    and the RAG store depend on stable keys.
    """

    # === 1. Identity & metadata =============================================
    policy_id: str = Field(
        ...,
        description="Slug we mint, e.g. 'niva-bupa-reassure-2'. Stable primary key.",
    )
    insurer_name: str = Field(..., description="Legal insurer name, e.g. 'Niva Bupa Health Insurance Co. Ltd.'")
    insurer_slug: str = Field(..., description="Slug for insurer, e.g. 'niva-bupa'.")
    policy_name: str = Field(..., description="Marketing name of the policy, e.g. 'Reassure 2.0'.")
    policy_type: Optional[PolicyType] = Field(None, description="Product category per IRDAI filing.")

    @field_validator("policy_type", mode="before")
    @classmethod
    def _norm_policy_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _norm_policy_type_val(v)
        return v
    uin_code: Optional[str] = Field(
        None,
        description="IRDAI Unique Identification Number (UIN) — regulator-issued, "
        "e.g. 'NBHHLIP23068V012223'. Required for any IRDAI cross-reference.",
    )

    # === 2. Eligibility =====================================================
    min_entry_age_years: Optional[int] = Field(None, description="Minimum entry age for adult insured.")
    max_entry_age_years: Optional[int] = Field(None, description="Maximum entry age (often 65).")
    max_renewal_age_years: Optional[int] = Field(
        None,
        description="Maximum renewal age. IRDAI now mandates lifelong renewability; encode as 999 for 'lifelong'.",
    )
    min_child_entry_age_days: Optional[int] = Field(
        None,
        description="Minimum entry age for dependent children in days (e.g. 91 for '91 days').",
    )
    family_composition_allowed: Optional[List[FamilyComposition]] = Field(
        None, description="Composition options the contract supports."
    )

    @field_validator("family_composition_allowed", mode="before")
    @classmethod
    def _norm_family_list(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [_norm_family(x) if isinstance(x, str) else x for x in v]
        return v
    residency_requirement: Optional[str] = Field(
        None, description="e.g. 'Indian resident only', 'NRI eligible with conditions'."
    )

    # === 3. Sum insured & premium structure =================================
    sum_insured_options_inr: Optional[List[int]] = Field(
        None,
        description="All sum insured tiers offered, in INR. e.g. [500000, 1000000, 2500000, 5000000, 10000000].",
    )
    premium_payment_modes: Optional[List[PremiumMode]] = Field(
        None, description="Allowed billing frequencies."
    )
    premium_range_indicative_inr: Optional[Dict[str, int]] = Field(
        None,
        description="Illustrative annual premium for a benchmark profile, keyed by age band. "
        "e.g. {'30-35_SI_10L': 12000, '50-55_SI_10L': 28000}. Filled from public quote pages, not the PDF.",
    )
    premium_payment_term_years: Optional[int] = Field(
        None, description="Years over which premium must be paid (usually 1 for non-life)."
    )

    @field_validator("premium_payment_term_years", mode="before")
    @classmethod
    def _coerce_premium_term(cls, v: Any) -> Any:
        # Accept list-of-options (e.g. [1, 2, 3]) by taking the smallest /
        # default option. Most non-life policies are billed annually so the
        # first int is the right "default term" for downstream consumers.
        if isinstance(v, list) and v:
            try:
                return int(v[0])
            except (TypeError, ValueError):
                return None
        return v
    grace_period_days: Optional[int] = Field(
        None, description="Days past renewal date during which the policy stays continuously covered."
    )
    free_look_period_days: Optional[int] = Field(
        None, description="Days the buyer can cancel for a full refund (IRDAI mandates 30 for digital sales)."
    )

    # === 4. Waiting periods (CRITICAL for comparison) =======================
    initial_waiting_period_days: Optional[int] = Field(
        None, description="Days from inception before any non-accident claim is payable (typically 30)."
    )
    pre_existing_disease_waiting_months: Optional[int] = Field(
        None,
        description="Months before pre-existing conditions are covered. "
        "Industry range: 12 / 24 / 36 / 48. Lower is better for buyers.",
    )
    specific_disease_waiting_months: Optional[int] = Field(
        None,
        description="Months before listed conditions (cataract, hernia, joint replacement etc.) are covered.",
    )
    specific_diseases_listed: Optional[List[str]] = Field(
        None, description="The actual list of named conditions under the specific-disease waiting period."
    )
    maternity_waiting_months: Optional[int] = Field(
        None, description="Months before maternity benefit kicks in (commonly 24-48 if maternity is included)."
    )
    sub_limits_waiting_notes: Optional[str] = Field(
        None, description="Any other waiting-period sub-clauses not captured above."
    )

    # === 5. Coverage scope ==================================================
    inpatient_hospitalization: Optional[CoverageItem] = Field(
        None, description="Core in-hospital cover. Almost always covered; details capture room/ICU caps."
    )
    pre_hospitalization_days: Optional[int] = Field(
        None, description="Days of pre-admission expenses covered (commonly 30 / 60)."
    )
    post_hospitalization_days: Optional[int] = Field(
        None, description="Days of post-discharge expenses covered (commonly 60 / 90 / 180)."
    )
    day_care_treatments: Optional[CoverageItem] = Field(
        None, description="Procedures under 24 hrs. Encode count or 'all listed' in limit_text."
    )
    domiciliary_treatment: Optional[CoverageItem] = Field(
        None, description="Home treatment when hospitalization is not possible."
    )
    ayush_coverage: Optional[CoverageItem] = Field(
        None, description="Ayurveda / Yoga / Unani / Siddha / Homeopathy in-patient cover."
    )
    maternity_coverage: Optional[CoverageItem] = Field(
        None, description="Normal and C-section delivery cover, with limit and waiting period."
    )
    newborn_coverage: Optional[CoverageItem] = Field(
        None, description="Newborn from day 1, sometimes contingent on maternity benefit."
    )
    organ_donor_expenses: Optional[CoverageItem] = Field(
        None, description="Donor's hospitalization expenses for a covered organ transplant."
    )
    ambulance_cover: Optional[CoverageItem] = Field(
        None, description="Road / air ambulance, with per-claim limit."
    )
    critical_illness_cover: Optional[CoverageItem] = Field(
        None,
        description="Lump-sum on diagnosis of listed CIs. Use limit_text to record the count "
        "(e.g. 'Covers 32 critical illnesses').",
    )
    restoration_benefit: Optional[CoverageItem] = Field(
        None,
        description="Sum-insured refill once exhausted. Capture 'unlimited / once / unrelated illness only' in notes.",
    )
    no_claim_bonus_pct: Optional[float] = Field(
        None,
        description="Annual SI step-up percentage on claim-free renewal (e.g. 50.0 for 50%). "
        "Cap is recorded in `no_claim_bonus_cap_pct`.",
    )
    no_claim_bonus_cap_pct: Optional[float] = Field(
        None, description="Maximum cumulative NCB as % of base SI (e.g. 100 means up to 2x base SI)."
    )
    preventive_health_checkup: Optional[CoverageItem] = Field(
        None, description="Free check-up frequency and limit (commonly annual or every 2 years)."
    )

    # === 6. Sub-limits & caps (what's NOT fully covered) ====================
    room_rent_capping: Optional[str] = Field(
        None,
        description="Room-rent restriction text, e.g. '1% of SI per day', 'Single private AC room', 'No cap'.",
    )
    icu_capping: Optional[str] = Field(
        None, description="ICU rent restriction text. 'No cap' for premium plans."
    )
    copayment_pct: Optional[float] = Field(
        None,
        description="Mandatory % the insured pays from each claim. "
        "Often age-triggered (e.g. 20% for entry age > 60). Use 0 if none.",
    )
    copayment_trigger_notes: Optional[str] = Field(
        None, description="When the copay applies: age band, zone, voluntary discount, etc."
    )
    disease_wise_sub_limits: Optional[Dict[str, str]] = Field(
        None,
        description="Per-procedure caps, e.g. {'cataract': '₹25,000 per eye', 'knee_replacement': '₹1,60,000'}.",
    )
    deductible_amount_inr: Optional[int] = Field(
        None,
        description="Aggregate deductible in INR before the policy pays. "
        "Non-zero for top-up / super top-up plans; usually 0 for base indemnity.",
    )

    # === 7. Geography & network =============================================
    geographic_coverage: Optional[GeographicScope] = Field(
        None, description="Territorial scope for non-emergency claims."
    )

    @field_validator("geographic_coverage", mode="before")
    @classmethod
    def _norm_geo_coverage(cls, v: Any) -> Any:
        # V4-Pro sometimes emits {'scope': 'India'} (dict) instead of a plain
        # string. Pull the value out + run through the geo synonym map.
        if isinstance(v, dict):
            for key in ("scope", "value", "name", "type"):
                if key in v and v[key]:
                    v = v[key]
                    break
            else:
                return None
        if isinstance(v, str):
            return _norm_geo(v)
        return v
    worldwide_emergency_cover: Optional[CoverageItem] = Field(
        None, description="Cover for emergencies abroad — distinct from full international cover."
    )
    network_hospital_count: Optional[int] = Field(
        None, description="Approximate empanelled cashless hospital count published by the insurer."
    )
    cashless_treatment_supported: Optional[bool] = Field(
        None, description="Whether cashless is offered (essentially always True for indemnity products)."
    )

    # === 8. Exclusions ======================================================
    permanent_exclusions: Optional[List[str]] = Field(
        None,
        description="Never-covered items (cosmetic surgery, self-inflicted injury, war, etc.). "
        "IRDAI mandates a standardised list since 2020.",
    )
    temporary_exclusions: Optional[List[str]] = Field(
        None,
        description="Time-bound exclusions; usually mirrors specific_diseases_listed but kept separate "
        "because some policies list explicit time-bound carve-outs (e.g. 'bariatric surgery: 36 months').",
    )
    notable_exclusions_summary: Optional[str] = Field(
        None,
        description="One-paragraph human summary of the most consumer-relevant exclusions. "
        "Used directly in the voice answer when a buyer asks 'what's NOT covered'.",
    )

    # === 9. Claim & service =================================================
    claim_settlement_ratio_pct: Optional[float] = Field(
        None,
        description="Last published IRDAI claim settlement ratio (%) for the INSURER (not the policy). "
        "Sourced from IRDAI's annual report, NOT the policy PDF.",
    )
    claim_process_summary: Optional[str] = Field(
        None,
        description="One paragraph: how to file cashless and reimbursement, including helpline / portal.",
    )
    tat_cashless_authorization_hours: Optional[float] = Field(
        None,
        description="Turn-around time for pre-authorization decisions, in hours. IRDAI 2024 mandate: 1 hour.",
    )

    # === 10. Riders / optional add-ons ======================================
    available_riders: Optional[List[str]] = Field(
        None,
        description="Names of optional add-on covers, e.g. ['Personal Accident', 'Critical Illness', "
        "'Hospital Cash', 'OPD Cover'].",
    )
    top_rider_examples: Optional[List[str]] = Field(
        None, description="Subset of riders most relevant for a typical buyer — used in the voice pitch."
    )
    rider_premium_indicative_inr: Optional[Dict[str, int]] = Field(
        None,
        description="Indicative annual rider cost, keyed by rider name. Sourced from public quotes.",
    )

    # === 11. Source metadata ================================================
    source_pdf_path: Optional[str] = Field(
        None, description="Local filesystem path to the policy wordings PDF used for extraction."
    )
    source_pdf_url: Optional[str] = Field(
        None, description="Canonical insurer-hosted URL for the policy wordings PDF."
    )
    last_updated_date: Optional[date] = Field(
        None, description="Date this record was last extracted or human-reviewed."
    )
    extraction_confidence_pct: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Self-reported confidence (0-100) from the extraction pipeline. "
        "Records below ~70 should be flagged for human review before being served to users.",
    )

    # -----------------------------------------------------------------------
    class Config:
        extra = "allow"  # Forward-compatibility: keep unknown keys for v2 expansion.
        use_enum_values = True  # Serialize enums as their string values for JSON store.
        validate_assignment = True
