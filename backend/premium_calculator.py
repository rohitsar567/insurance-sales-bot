"""Illustrative premium calculator — rules-based estimate from a curated grid
of public PolicyBazaar / InsuranceDekho quotes.

The output is explicitly an **illustrative band**, not a quote. See decisions.md
D-007 — we are an advisor, not a broker. Real premiums depend on underwriting.

How it works:
  1. Load `40-data/premiums/illustrative_premiums.json` (curated by research agent
     from real quote-page scrapes; every value has a source_url).
  2. Given user inputs (age, sum_insured, city_tier, smoker, family_size,
     optional policy_id):
       - Look up the policy's base sample points
       - Find the closest sample (or interpolate between two)
       - Apply scaling multipliers for age, sum_insured, city_tier, smoker,
         family_floater
  3. Return a band of (low, mid, high) — low/high are ±15% wings around the
     point estimate, reflecting underwriting variance.

═══════════════════════════════════════════════════════════════════════════
SLOT_UNION → pricing-influence map (B6, 2026-05-15)
═══════════════════════════════════════════════════════════════════════════
The full slot list lives in `backend/brain_tools.py::SLOT_UNION`. Slots
that influence the per-policy premium estimate (in addition to age /
location / family_size that B2 already handles):

  health_conditions       → health_loading 1.0× / 1.2× / 1.4× / 1.5×
  existing_cover_inr      → existing_cover_loading 1.0× / 0.95× / 0.85×
  desired_sum_insured_inr → overrides default SI per-policy
  parents_age_max         → parents_loading 1.0× / 1.4× / 1.8×
                            (only when `dependents` mentions "parents")
  parents_has_ped         → adds +0.10× on top of parents_loading
  copay_pct (D2)          → copay_discount 1.0× / 0.95× / 0.88× / 0.80×
  family_medical_history  → family_history_loading 1.0× / 1.03× / 1.05× / 1.10×
  (D2)                      (cancer/heart +5%, 2+ conditions +10%, other +3%)
  smoker (KI-275)         → smoker_loading 1.0× / 1.40× (+30-50% premium load)

Slots that are profile-only (no pricing effect): name, primary_goal,
income_band, budget_band (matched against output, not folded into the
multiplicative chain). budget_band is a band-MATCH input downstream
(e.g. scorecard fit), not a premium-direction input.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.config import settings

ROOT = settings.CORPUS_DIR.parent.parent
PREMIUM_DATA = settings.DATA_DIR / "premiums" / "illustrative_premiums.json"


@dataclass
class PremiumEstimate:
    policy_id: str
    point_estimate_inr: int
    low_inr: int
    high_inr: int
    base_sample_used: Optional[dict] = None
    methodology: str = ""
    sources: list[str] = None
    # D2 (2026-05-16) — set ONLY when the policy publishes no corroborated
    # Sum Insured and the estimate therefore had to price against a fallback
    # cover (the user's desired_sum_insured_inr, else ₹10 L). The frontend
    # renders this verbatim under the per-policy estimate so the user knows
    # the SI is assumed, not the policy's own.
    sum_insured_disclosure: Optional[str] = None


# Fallback factors when no premium data file is available — used so the bot
# can still calculate plausible numbers in dev / cold-start.
FALLBACK_BASE_INR = 8500  # age 30, SI ₹5L, metro, non-smoker, individual
FALLBACK_AGE = {
    "18-25": 0.85, "26-35": 1.0, "36-45": 1.4,
    "46-55": 2.1, "56-65": 3.2, "65+": 4.5,
}
FALLBACK_SI = {
    "500000": 1.0, "1000000": 1.7, "1500000": 2.2,
    "2500000": 3.1, "5000000": 4.6, "10000000": 7.2,
}
FALLBACK_CITY = {"metro": 1.0, "tier1": 0.92, "tier2": 0.82}
# family_size = NUMBER OF DEPENDENTS COVERED (in addition to self).
#   0 = self only (individual policy, no floater premium uplift)
#   1 = self + 1 dependent (couple cover)
#   2 = self + 2 dependents (small family)
#   ...
# Source: typical retail family-floater rate cards from PolicyBazaar +
# InsuranceDekho — individual base, ~1.5× for couple, ~2× for family of 3,
# ~2.4× for family of 4, scaling thereafter.
FALLBACK_FLOATER = {0: 1.0, 1: 1.50, 2: 1.85, 3: 2.20, 4: 2.55, 5: 2.85, 6: 3.10}

# Pre-existing-disease loading factors. Sources: Acko + PolicyBazaar coverage
# articles on PED loading (typical 25-50% premium uplift depending on severity).
FALLBACK_PED = {
    "none": 1.0,
    "diabetes_or_hypertension": 1.30,
    "heart_disease": 1.45,
    "multiple": 1.55,
}

# ───────────────────────────────────────────────────────────────────────────
# B6 loadings — profile-driven multipliers consumed by BOTH estimate() and
# bulk_estimate() so the per-policy point estimate and the slider widget
# agree by construction.
# ───────────────────────────────────────────────────────────────────────────

# Health condition loading — applied multiplicatively after PED loading.
# Source band: PolicyBazaar PED articles + Acko underwriting guides.
#   • diabetes / BP (hypertension)        → 1.20×
#   • heart / cancer (severe chronic)     → 1.40×
#   • 2+ chronic conditions (compounded)  → 1.50× (overrides the above)
_HEALTH_DIABETES_BP = {"diabetes", "bp", "hypertension", "high bp", "hi-bp", "high-bp"}
_HEALTH_SEVERE = {"heart", "heart disease", "cardiac", "cancer", "stroke"}


def _health_loading(health_conditions) -> tuple[float, str]:
    """Return (multiplier, label) for a health_conditions list.

    Accepts list[str] (canonical), comma-joined string, or None. The empty
    list and the sentinel ["none"] both map to 1.0×. Real conditions are
    matched against the diabetes/BP and severe buckets case-insensitively.
    """
    if not health_conditions:
        return 1.0, "no_conditions"
    if isinstance(health_conditions, str):
        items = [t.strip().lower() for t in health_conditions.split(",") if t.strip()]
    else:
        items = [str(t).strip().lower() for t in health_conditions if str(t).strip()]
    # Strip the explicit-negation sentinel.
    items = [t for t in items if t != "none"]
    if not items:
        return 1.0, "no_conditions"
    has_diabetes_bp = any(t in _HEALTH_DIABETES_BP for t in items)
    has_severe = any(any(s in t for s in _HEALTH_SEVERE) for t in items)
    # 2+ chronic conditions → highest multiplier (overrides the others).
    if len(items) >= 2:
        return 1.50, "two_plus_chronic"
    if has_severe:
        return 1.40, "severe_chronic"
    if has_diabetes_bp:
        return 1.20, "diabetes_or_bp"
    # Unrecognised single condition — treat as mild loading.
    return 1.10, "other_single"


def _existing_cover_loading(existing_cover_inr) -> tuple[float, str]:
    """Return (multiplier, label) for existing_cover_inr.

    Rationale: if the user already has cover, a top-up policy is cheaper
    than a full base policy (insurer collects less risk + can price for the
    cover gap only). Thresholds: <₹5L = mild discount (corporate top-up),
    ≥₹5L = larger discount (only super-top-up needed).
    """
    try:
        ec = int(existing_cover_inr or 0)
    except (TypeError, ValueError):
        ec = 0
    if ec <= 0:
        return 1.0, "no_existing_cover"
    if ec < 500_000:
        return 0.95, "corporate_topup"
    return 0.85, "significant_existing_cover"


def _parents_loading(dependents, parents_age_max, parents_has_ped=None) -> tuple[float, str]:
    """Return (multiplier, label) for parents-on-cover scenarios.

    Only fires when `dependents` mentions "parent" (case-insensitive). The
    multiplier is age-banded:
      • <60         → 1.0× (parents counted in family loading already)
      • 60–70       → 1.40×
      • 70+         → 1.80×
    `parents_has_ped=True` adds a flat +0.10× on top (PED loading inflated
    for the older age cohort).
    """
    has_parents = False
    if dependents:
        has_parents = "parent" in str(dependents).lower()
    if not has_parents or parents_age_max in (None, "", 0):
        return 1.0, "no_parents_on_cover"
    try:
        age = int(parents_age_max)
    except (TypeError, ValueError):
        return 1.0, "no_parents_on_cover"
    if age < 60:
        base, label = 1.0, "parents_under_60"
    elif age <= 70:
        base, label = 1.40, "parents_60_70"
    else:
        base, label = 1.80, "parents_70_plus"
    if parents_has_ped is True and base > 1.0:
        base += 0.10
        label = f"{label}_with_ped"
    return base, label


# ───────────────────────────────────────────────────────────────────────────
# D2 (2026-05-15) — copay_pct + family_medical_history loadings
# ───────────────────────────────────────────────────────────────────────────

def _copay_discount(copay_pct) -> tuple[float, str]:
    """Return (multiplier, label) for SLOT_UNION's `copay_pct` slot.

    Distinct from the legacy `_copay_multiplier` (formula-based, used by the
    `copayment_pct` arg on estimate()). This is a profile-driven step-discount
    grid keyed to the 4 buckets RULE 2.5 asks the user about (0/10/20/30):

      0%  → 1.00× ("no copay")          — insurer pays it all (highest premium)
      10% → 0.95× ("10% copay")          — mild tier
      20% → 0.88× ("20% copay")          — typical
      30% → 0.80× ("30% copay")          — aggressive
      other → linear interpolate between the two nearest buckets, clamped to [0,50]
    """
    if copay_pct is None:
        return 1.0, "no_copay"
    try:
        pct = int(copay_pct)
    except (TypeError, ValueError):
        return 1.0, "no_copay"
    if pct <= 0:
        return 1.0, "no_copay"
    # Clamp to [0, 50] to match _coerce_copay_pct.
    pct = min(50, pct)
    # Step grid (exact buckets).
    if pct == 10:
        return 0.95, "10_pct_copay"
    if pct == 20:
        return 0.88, "20_pct_copay"
    if pct == 30:
        return 0.80, "30_pct_copay"
    # Linear interpolation for off-grid values (e.g. 15, 25, 40).
    grid = [(0, 1.00), (10, 0.95), (20, 0.88), (30, 0.80), (50, 0.70)]
    for i in range(len(grid) - 1):
        p0, m0 = grid[i]
        p1, m1 = grid[i + 1]
        if p0 <= pct <= p1:
            t = (pct - p0) / (p1 - p0) if p1 != p0 else 0
            mult = m0 + (m1 - m0) * t
            return round(mult, 3), f"{pct}_pct_copay"
    return 1.0, "no_copay"


# Family medical history canonical condition keywords. Matches the canonical
# tokens emitted by brain_tools._coerce_family_medical_history (cancer /
# diabetes / heart / hypertension).
_FAM_CANCER_KEYWORDS = {"cancer"}
_FAM_HEART_KEYWORDS = {"heart"}


def _family_history_loading(family_medical_history) -> tuple[float, str]:
    """Return (multiplier, label) for blood-family medical history.

    Logic (D2 spec):
      • empty list / None / ["none"]  → (1.00, "no_family_history")
      • 2+ family conditions          → (1.10, "multi_family_history")
                                        (highest — compounded genetic risk)
      • contains "cancer"             → (1.05, "family_cancer")
      • contains "heart"              → (1.05, "family_heart")
      • other single condition (e.g. diabetes / hypertension) → (1.03, "family_history")

    Order: 2+ check FIRST so a profile with both cancer + diabetes lands on
    the multi-family multiplier (not the cancer-only +5%).
    """
    if not family_medical_history:
        return 1.0, "no_family_history"
    if isinstance(family_medical_history, str):
        items = [t.strip().lower() for t in family_medical_history.split(",") if t.strip()]
    else:
        items = [str(t).strip().lower() for t in family_medical_history if str(t).strip()]
    # Drop the "none" sentinel if a caller passed it (defensive).
    items = [t for t in items if t != "none"]
    if not items:
        return 1.0, "no_family_history"
    # 2+ conditions wins — compounded genetic risk loading.
    if len(items) >= 2:
        return 1.10, "multi_family_history"
    # Single condition — bucket by keyword.
    single = items[0]
    if any(k in single for k in _FAM_CANCER_KEYWORDS):
        return 1.05, "family_cancer"
    if any(k in single for k in _FAM_HEART_KEYWORDS):
        return 1.05, "family_heart"
    return 1.03, "family_history_single"


# Co-pay reduces premium. Industry norm (PolicyBazaar/Acko): each 10 pct
# points of co-pay yields ~7% premium reduction, capped at 40% co-pay.
def _copay_multiplier(pct: float) -> float:
    if not pct or pct <= 0:
        return 1.0
    pct = min(40.0, float(pct))
    return 1.0 - (pct / 100.0 * 0.70)


def _age_bucket(age: int) -> str:
    if age <= 25: return "18-25"
    if age <= 35: return "26-35"
    if age <= 45: return "36-45"
    if age <= 55: return "46-55"
    if age <= 65: return "56-65"
    return "65+"


def _si_bucket(si: int) -> str:
    keys = [500000, 1000000, 1500000, 2500000, 5000000, 10000000]
    i = bisect.bisect_left(keys, si)
    i = max(0, min(len(keys) - 1, i))
    return str(keys[i])


def _load_data() -> dict:
    if not PREMIUM_DATA.exists():
        return {}
    try:
        return json.loads(PREMIUM_DATA.read_text())
    except Exception:
        return {}


def _interpolate_from_samples(samples: list[dict], age: int, sum_insured: int) -> Optional[int]:
    """Pick or interpolate the closest two samples by (age, sum_insured) and
    return the closest premium. Simple — not statistically principled, but
    'directionally right' is the bar (D-007)."""
    if not samples:
        return None
    # Score each sample by distance in (age, log(SI)) space
    import math
    def dist(s):
        return (
            (s["age"] - age) ** 2
            + (math.log(max(1, s["sum_insured_inr"])) - math.log(max(1, sum_insured))) ** 2 * 50
        )
    best = min(samples, key=dist)
    return best.get("annual_premium_inr")


def estimate(
    age: int,
    sum_insured_inr: int,
    city_tier: str = "metro",
    smoker: bool = False,
    family_size: int = 1,
    policy_id: Optional[str] = None,
    pre_existing_conditions: str = "none",
    copayment_pct: float = 0.0,
    # B6 additions — SLOT_UNION pricing inputs. All optional so legacy
    # callers (B2's bulk_estimate, tests) keep working unchanged.
    health_conditions: Optional[list] = None,
    existing_cover_inr: Optional[int] = None,
    dependents: Optional[str] = None,
    parents_age_max: Optional[int] = None,
    parents_has_ped: Optional[bool] = None,
    # D2 additions (2026-05-15) — copay_pct + family_medical_history.
    copay_pct: Optional[int] = None,
    family_medical_history: Optional[list] = None,
) -> PremiumEstimate:
    data = _load_data()
    base_premiums = data.get("base_premiums", {})
    scaling = data.get("scaling_factors", {})
    age_mults = scaling.get("age_multipliers", FALLBACK_AGE)
    si_mults = scaling.get("sum_insured_multipliers", FALLBACK_SI)
    city_mults = scaling.get("city_tier_multipliers", FALLBACK_CITY)
    smoker_mult = scaling.get("smoker_multiplier", 1.35)
    floater_mults_raw = scaling.get("family_floater_multipliers", {})
    floater_mults = {int(k): v for k, v in floater_mults_raw.items()} if floater_mults_raw else FALLBACK_FLOATER
    ped_mults = scaling.get("ped_load_multipliers", FALLBACK_PED)

    sources = []
    sample_used = None
    base = FALLBACK_BASE_INR

    # Try policy-specific sample first
    if policy_id and policy_id in base_premiums:
        entry = base_premiums[policy_id]
        samples = entry.get("samples", [])
        guess = _interpolate_from_samples(samples, age, sum_insured_inr)
        if guess is not None:
            base = guess
            sample_used = min(samples, key=lambda s: abs(s["age"] - age) + abs(s["sum_insured_inr"] - sum_insured_inr) / 100000)
            if sample_used.get("source_url"):
                sources.append(sample_used["source_url"])
            # The sample's age/SI may differ from user's — adjust via ratios from base
            sample_age_bucket = _age_bucket(sample_used["age"])
            user_age_bucket = _age_bucket(age)
            base *= age_mults.get(user_age_bucket, 1.0) / age_mults.get(sample_age_bucket, 1.0)
            sample_si_bucket = _si_bucket(sample_used["sum_insured_inr"])
            user_si_bucket = _si_bucket(sum_insured_inr)
            base *= si_mults.get(user_si_bucket, 1.0) / si_mults.get(sample_si_bucket, 1.0)
        else:
            # No samples for this policy — use generic base
            base = FALLBACK_BASE_INR * age_mults.get(_age_bucket(age), 1.0) * si_mults.get(_si_bucket(sum_insured_inr), 1.0)
    else:
        # No policy specified or no data — generic
        base = FALLBACK_BASE_INR * age_mults.get(_age_bucket(age), 1.0) * si_mults.get(_si_bucket(sum_insured_inr), 1.0)

    # City + smoker + family floater modifiers always apply
    base *= city_mults.get(city_tier, 1.0)
    if smoker:
        base *= smoker_mult
    base *= floater_mults.get(family_size, 1.0)
    # PED load — diabetes/hypertension/heart raise premiums materially
    base *= ped_mults.get(pre_existing_conditions, 1.0)
    # Co-pay discount — opting into co-payment lowers premium
    base *= _copay_multiplier(copayment_pct)

    # B6 loadings — health, existing cover, parents-on-cover. Each is
    # 1.0× when the corresponding SLOT_UNION field is absent so legacy
    # callers see no change in output.
    health_mult, health_label = _health_loading(health_conditions)
    base *= health_mult
    ec_mult, ec_label = _existing_cover_loading(existing_cover_inr)
    base *= ec_mult
    parents_mult, parents_label = _parents_loading(
        dependents, parents_age_max, parents_has_ped
    )
    base *= parents_mult

    # D2 — copay_pct discount + family_medical_history loading. Each is 1.0×
    # when the corresponding SLOT_UNION field is None / empty, so legacy
    # callers see no change.
    copay_mult, copay_label = _copay_discount(copay_pct)
    base *= copay_mult
    fam_mult, fam_label = _family_history_loading(family_medical_history)
    base *= fam_mult

    point = int(round(base / 100) * 100)  # round to nearest ₹100
    return PremiumEstimate(
        policy_id=policy_id or "generic",
        point_estimate_inr=point,
        low_inr=int(point * 0.85),
        high_inr=int(point * 1.15),
        base_sample_used=sample_used,
        methodology=(
            "Rules-based estimate from curated public quote samples; ±15% band "
            "to reflect underwriting variance. NOT a binding quote."
        ),
        sources=sources or [],
    )


# ---------------------------------------------------------------------------
# Bulk / slider widget heuristic — used by /api/premium/bulk so the
# PolicyCompareModal premium widget can render fast estimates for several
# policies at once. Same shape per policy: a transparent multiplicative
# breakdown the UI can render as bullets.
#
# This is intentionally simpler than estimate(): a fixed ₹500 per ₹1L SI per
# year base × age × location × family × deductible × tenure. When the curated
# illustrative_premiums.json HAS a real sample for a policy we anchor the base
# to it (assumed=False); otherwise we use the flat base rate (assumed=True)
# and the UI labels the value "Estimate".
# ---------------------------------------------------------------------------

# ₹500 per ₹1L SI per year — typical Indian retail health entry-tier base.
BULK_BASE_INR_PER_LAKH = 500

BULK_AGE_BANDS = [
    (30, 1.0),    # 18–30
    (45, 1.5),    # 30–45
    (60, 2.5),    # 45–60
    (200, 4.0),   # 60+
]

BULK_LOCATION_LOADING = {
    "metro": 1.2,
    "tier1": 1.0,
    "tier-1": 1.0,
    "tier_1": 1.0,
    "tier2": 1.0,
    "tier-2": 1.0,
    "tier_2": 1.0,
    "tier3": 0.85,
    "tier-3": 0.85,
    "tier_3": 0.85,
}

# Family-floater uplift over individual (1.6× family floater per spec).
BULK_FAMILY_FLOATER_MULT = 1.6

# Deductible discount — higher voluntary deductible lowers the premium.
# Linear approximation; sources: PolicyBazaar deductible guides.
BULK_DEDUCTIBLE_DISCOUNT = {
    0: 1.0,
    25000: 0.92,
    50000: 0.85,
    100000: 0.75,
}

# Tenure loading — multi-year policies typically get a 5–10% per-year discount.
BULK_TENURE_MULT = {
    1: 1.0,
    2: 0.95,
    3: 0.90,
}


def _bulk_age_mult(age: int) -> tuple[float, str]:
    for ceiling, mult in BULK_AGE_BANDS:
        if age < ceiling:
            band = (
                "18-30" if ceiling == 30 else
                "30-45" if ceiling == 45 else
                "45-60" if ceiling == 60 else
                "60+"
            )
            return mult, band
    return 4.0, "60+"


def _bulk_location_mult(tier: Optional[str]) -> tuple[float, str]:
    key = (tier or "metro").lower().strip()
    return BULK_LOCATION_LOADING.get(key, 1.0), key


def _bulk_family_size_from_dependents(dependents: Optional[str], family_size: Optional[int]) -> int:
    """Coerce the profile's free-text `dependents` string OR explicit
    family_size into an integer headcount (self + dependents)."""
    if isinstance(family_size, int) and family_size > 0:
        return family_size
    if not dependents:
        return 1
    s = str(dependents).lower()
    # Count keyword hits + digit-prefixed counts (e.g. "2 kids", "1 child").
    import re as _re
    headcount = 1  # self
    has_spouse = any(k in s for k in ("spouse", "wife", "husband", "partner"))
    if has_spouse:
        headcount += 1
    # Children: try "N kid(s)/child/children" first, else any "kid/child" keyword = +1
    kid_match = _re.search(r"(\d+)\s*(kid|child|son|daughter)", s)
    if kid_match:
        headcount += max(1, int(kid_match.group(1)))
    elif any(k in s for k in ("kid", "child", "son", "daughter")):
        headcount += 1
    # Parents — explicit "parent(s)" keyword adds +1 each on a single mention.
    if "parent" in s:
        headcount += 1
    # Family-of-N pattern: "family of 4"
    fof = _re.search(r"family\s+of\s+(\d+)", s)
    if fof:
        headcount = max(headcount, int(fof.group(1)))
    # Bare integer at sentence start ("3 dependents") — only honour if no keywords matched
    if headcount == 1 and not has_spouse:
        m = _re.search(r"(\d+)", s)
        if m:
            try:
                headcount = max(1, int(m.group(1)))
            except ValueError:
                pass
    return max(1, headcount)


def _round_inr(x: float) -> int:
    return int(round(x / 10) * 10)


@dataclass
class BulkPolicyPremium:
    policy_id: str
    premium_inr_annual: int
    breakdown: dict
    sum_insured_inr: int
    tenure_years: int
    deductible_inr: int
    assumed: bool
    notes: list[str] = field(default_factory=list)


def bulk_estimate(
    policy_ids: list[str],
    profile: Optional[dict] = None,
    overrides: Optional[dict] = None,
) -> dict[str, BulkPolicyPremium]:
    """Compute heuristic per-policy premiums for the widget.

    profile keys (all optional): age, dependents, location_tier, family_size,
    smoker, pre_existing_conditions.
    overrides[policy_id]: sum_insured_inr / tenure_years / deductible_inr.
    """
    profile = profile or {}
    overrides = overrides or {}

    age = int(profile.get("age") or 35)
    location_tier = profile.get("location_tier") or "metro"
    family_size = _bulk_family_size_from_dependents(
        profile.get("dependents"), profile.get("family_size")
    )

    # B6 SLOT_UNION pricing inputs — read from the same profile dict so the
    # bulk widget and the per-policy estimate() agree by construction.
    health_conditions = profile.get("health_conditions")
    existing_cover_inr = profile.get("existing_cover_inr")
    dependents = profile.get("dependents")
    parents_age_max = profile.get("parents_age_max")
    parents_has_ped = profile.get("parents_has_ped")
    # D2 — copay_pct + family_medical_history (same read pattern).
    copay_pct = profile.get("copay_pct")
    family_medical_history = profile.get("family_medical_history")
    # KI-275 — smoker / tobacco use (+30-50% loading). Same read pattern as
    # the D2 fields above; mirrors how the panel slider already passes
    # `smoker` straight through to estimate() on the curated path.
    smoker = bool(profile.get("smoker") or False)
    # desired_sum_insured_inr — when present, becomes the default SI for
    # any policy without an explicit overrides entry (per-policy override
    # still wins, since this is the DEFAULT).
    desired_si = profile.get("desired_sum_insured_inr")

    data = _load_data()
    base_premiums_curated = data.get("base_premiums", {})

    age_mult, age_band = _bulk_age_mult(age)
    loc_mult, loc_label = _bulk_location_mult(location_tier)
    family_mult = BULK_FAMILY_FLOATER_MULT if family_size >= 2 else 1.0
    health_mult, health_label = _health_loading(health_conditions)
    ec_mult, ec_label = _existing_cover_loading(existing_cover_inr)
    parents_mult, parents_label = _parents_loading(
        dependents, parents_age_max, parents_has_ped
    )
    # D2 — copay_pct discount + family_medical_history loading. Each is 1.0×
    # when the corresponding SLOT_UNION field is None / empty, so legacy
    # callers see no change.
    copay_mult, copay_label = _copay_discount(copay_pct)
    fam_mult, fam_label = _family_history_loading(family_medical_history)
    # KI-275 — smoker loading. 1.40× (+40%) standard tobacco loading.
    # 1.0× when smoker is False / None so legacy callers see no change.
    smoker_mult = 1.4 if smoker else 1.0
    smoker_label = "smoker_loading" if smoker else "non_smoker"

    out: dict[str, BulkPolicyPremium] = {}
    for pid in policy_ids:
        ov = overrides.get(pid) or {}
        # Override precedence: per-policy override > desired_sum_insured_inr
        # from profile > ₹10L hard default. This is how
        # desired_sum_insured_inr propagates through the widget.
        sum_insured_inr = int(
            ov.get("sum_insured_inr") or desired_si or 1_000_000
        )
        tenure_years = int(ov.get("tenure_years") or 1)
        if tenure_years not in BULK_TENURE_MULT:
            tenure_years = 1
        deductible_inr = int(ov.get("deductible_inr") or 0)
        if deductible_inr not in BULK_DEDUCTIBLE_DISCOUNT:
            # snap to nearest known bucket
            deductible_inr = min(BULK_DEDUCTIBLE_DISCOUNT.keys(), key=lambda d: abs(d - deductible_inr))

        notes: list[str] = []
        assumed = True

        # Anchor base to curated sample if we have one, else flat per-lakh rate.
        anchored_base: Optional[int] = None
        if pid in base_premiums_curated:
            try:
                ce = estimate(
                    age=age,
                    sum_insured_inr=sum_insured_inr,
                    city_tier="metro" if loc_label == "metro" else ("tier1" if "1" in loc_label else "tier2"),
                    smoker=smoker,
                    family_size=max(0, family_size - 1),
                    policy_id=pid,
                    pre_existing_conditions=profile.get("pre_existing_conditions") or "none",
                    copayment_pct=0.0,
                    # B6 — pass SLOT_UNION pricing inputs through so the
                    # curated path absorbs health/existing-cover/parents
                    # loadings inside estimate(). We then mark these as
                    # 1.0× in the breakdown to avoid double-counting.
                    health_conditions=health_conditions,
                    existing_cover_inr=existing_cover_inr,
                    dependents=dependents,
                    parents_age_max=parents_age_max,
                    parents_has_ped=parents_has_ped,
                    # D2 — copay + family-history threaded through too
                    copay_pct=copay_pct,
                    family_medical_history=family_medical_history,
                )
                # estimate() already folded age/location/family AND the B6
                # loadings — unwind so the widget can display the same
                # multiplicative bullets uniformly.
                anchored_base = ce.point_estimate_inr
                assumed = False
                notes.append("Anchored to curated public-quote sample.")
            except Exception:
                anchored_base = None

        si_lakhs = max(1, sum_insured_inr // 100_000)
        flat_base = BULK_BASE_INR_PER_LAKH * si_lakhs

        if anchored_base is not None:
            # Apply tenure + deductible only — the curated path already
            # absorbed age/location/family + B6 loadings inside estimate().
            tenure_mult = BULK_TENURE_MULT.get(tenure_years, 1.0)
            ded_mult = BULK_DEDUCTIBLE_DISCOUNT.get(deductible_inr, 1.0)
            final = anchored_base * tenure_mult * ded_mult
            breakdown = {
                "base_inr": int(anchored_base),
                "age_loading_x": 1.0,
                "location_loading_x": 1.0,
                "family_loading_x": 1.0,
                "tenure_discount_x": round(tenure_mult, 3),
                "deductible_discount_x": round(ded_mult, 3),
            }
        else:
            tenure_mult = BULK_TENURE_MULT.get(tenure_years, 1.0)
            ded_mult = BULK_DEDUCTIBLE_DISCOUNT.get(deductible_inr, 1.0)
            final = (
                flat_base
                * age_mult
                * loc_mult
                * family_mult
                * health_mult
                * ec_mult
                * parents_mult
                * copay_mult
                * fam_mult
                * smoker_mult
                * tenure_mult
                * ded_mult
            )
            breakdown = {
                "base_inr": int(flat_base),
                "age_loading_x": round(age_mult, 3),
                "age_band": age_band,
                "location_loading_x": round(loc_mult, 3),
                "location_tier": loc_label,
                "family_loading_x": round(family_mult, 3),
                "family_size": family_size,
                "tenure_discount_x": round(tenure_mult, 3),
                "deductible_discount_x": round(ded_mult, 3),
            }
            notes.append(
                "Heuristic estimate — no exact actuarial data for this policy. "
                "Base ₹500 per ₹1L SI per year × age × location × family × tenure × deductible."
            )

        # B6 — surface non-1.0× SLOT_UNION loadings in the breakdown
        # regardless of which branch produced the base. UI can render
        # "Diabetes/BP loading × 1.20" bullets when the user has the
        # corresponding profile slot captured.
        if health_mult != 1.0:
            breakdown["health_loading_x"] = round(health_mult, 3)
            breakdown["health_loading_reason"] = health_label
        if ec_mult != 1.0:
            breakdown["existing_cover_loading_x"] = round(ec_mult, 3)
            breakdown["existing_cover_loading_reason"] = ec_label
        if parents_mult != 1.0:
            breakdown["parents_loading_x"] = round(parents_mult, 3)
            breakdown["parents_loading_reason"] = parents_label
        if copay_mult != 1.0:
            breakdown["copay_discount_x"] = round(copay_mult, 3)
            breakdown["copay_discount_reason"] = copay_label
        if fam_mult != 1.0:
            breakdown["family_history_loading_x"] = round(fam_mult, 3)
            breakdown["family_history_loading_reason"] = fam_label
        if smoker_mult != 1.0:
            breakdown["smoker_loading_x"] = round(smoker_mult, 3)
            breakdown["smoker_loading_reason"] = smoker_label
        if desired_si and not ov.get("sum_insured_inr"):
            breakdown["desired_si_default_inr"] = int(desired_si)

        out[pid] = BulkPolicyPremium(
            policy_id=pid,
            premium_inr_annual=_round_inr(final),
            breakdown=breakdown,
            sum_insured_inr=sum_insured_inr,
            tenure_years=tenure_years,
            deductible_inr=deductible_inr,
            assumed=assumed,
            notes=notes,
        )
    return out


# ---------------------------------------------------------------------------
# Profile-level premium BAND — used by the chat-UI "Est. premium ₹X–₹Y/yr"
# chip that sits next to the profile-completeness pill. Aggregates the bulk
# heuristic across a representative basket of marketplace policies so the
# user sees what their personal premium envelope looks like as the profile
# fills in (reactively updates with each completeness change).
# ---------------------------------------------------------------------------

# Representative basket for the band — 26 curated marketplace policies that
# span every major insurer + product tier. Mirrors keys in
# 40-data/premiums/illustrative_premiums.json so anchored samples are used
# where available and the flat per-lakh fallback fills the rest.
_DEFAULT_BAND_POLICY_IDS: list[str] = [
    "hdfc-ergo__optima-secure",
    "hdfc-ergo__optima-restore",
    "hdfc-ergo__optima-plus",
    "hdfc-ergo__energy",
    "care-health__care-supreme",
    "care-health__care-classic",
    "care-health__care-senior",
    "care-health__care-advantage",
    "aditya-birla__activ-assure-diamond",
    "aditya-birla__group-activ-health",
    "bajaj-allianz__health-guard",
    "bajaj-allianz__silver-health",
    "bajaj-allianz__tax-gain",
    "icici-lombard__elevate",
    "icici-lombard__health-advantedge",
    "niva-bupa__reassure",
    "niva-bupa__health-premia",
    "niva-bupa__aspire",
    "new-india__asha-kiran",
    "new-india__mediclaim",
    "tata-aig__medicare",
    "tata-aig__medicare-premier",
    "manipalcigna__prohealth-prime-active",
    "star-health__family-health-optima",
    "star-health__comprehensive",
    "star-health__senior-citizens-red-carpet",
]


# ───────────────────────────────────────────────────────────────────────────
# SI RECONCILIATION (KI-278, 2026-05-16) — single source of truth for the
# sum-insured the header band AND the per-settings panel both price at.
#
# THE HEADER≠PANEL BUG: PremiumCalculatorPanel (page.tsx) seeds its SI slider
# from `desired_sum_insured_inr ?? existing_cover_inr ?? 1_000_000`, but
# estimate_premium_band() used to hard-code sum_insured_default=₹10L and
# IGNORE the profile entirely. So a user who stated a ₹25L target saw the
# header chip priced at ₹10L (₹6,500–₹26,500) while the panel priced the
# same profile at ₹25L (point ₹19,100) — contradictory numbers for one
# profile. Resolving BOTH surfaces' SI from this one function makes them
# reconcile by construction: the panel's point estimate now falls inside
# the header band because the header band is the SAME basket priced at the
# SAME profile-resolved SI.
#
# Precedence MUST stay byte-identical to PremiumCalculatorPanel's
# useState initialiser (frontend/src/app/page.tsx ~L2417) and
# PolicyPremiumWidget's initialSumInsured contract:
#   1. profile.desired_sum_insured_inr  (user's stated target SI)
#   2. profile.existing_cover_inr       (closest available signal)
#   3. fallback default                 (legacy ₹10L)
# ───────────────────────────────────────────────────────────────────────────

# SI RATIONALISATION (D2, 2026-05-16) — the global ₹5 L / ₹1 Cr clamp
# (SI_FLOOR_INR / SI_CEILING_INR) was REMOVED. Pricing now respects each
# policy's own real SI bounds and the user's actual stated target instead of
# squashing every profile into one synthetic envelope. A ₹2 Cr aspiration
# now prices at ₹2 Cr; a ₹1 L corporate top-up prices at ₹1 L. When a policy
# has no published SI the caller prices against the user's
# desired_sum_insured_inr (else ₹10 L default) and surfaces a disclosure.


def resolve_profile_sum_insured(
    profile: Optional[dict],
    fallback_default: int = 1_000_000,
) -> int:
    """Resolve the sum-insured to price a profile at.

    Single source of truth shared by estimate_premium_band() (header chip)
    and — via the documented contract below — the per-settings panel /
    PolicyPremiumWidget. Precedence is byte-identical to the panel's slider
    seed so the header band and the panel price the SAME profile at the SAME
    SI and therefore reconcile.

    Accepts the raw profile dict (any SLOT_UNION-shaped mapping). Coerces
    string/None gracefully and snaps to the nearest ₹50k so the resolved SI
    lands on a representable slider stop.

    D2 (2026-05-16) — the global ₹5 L / ₹1 Cr clamp was REMOVED: the user's
    actual stated target is honoured (a ₹2 Cr aspiration prices at ₹2 Cr, a
    ₹1 L top-up at ₹1 L) rather than squashed into a synthetic envelope.
    """
    profile = profile or {}

    def _coerce(v) -> Optional[int]:
        if v is None or v == "":
            return None
        try:
            iv = int(float(v))
        except (TypeError, ValueError):
            return None
        return iv if iv > 0 else None

    si = (
        _coerce(profile.get("desired_sum_insured_inr"))
        or _coerce(profile.get("existing_cover_inr"))
        or int(fallback_default)
    )
    # Snap to nearest ₹50k — keeps the band stable and on a slider stop.
    # (No clamp — D2: price the SI the user actually stated.)
    return int(round(si / 50_000) * 50_000)


# D2 (2026-05-16) — fallback SI when a policy publishes no corroborated Sum
# Insured. Precedence: the user's stated desired_sum_insured_inr, else ₹10 L.
NO_SI_FALLBACK_DEFAULT_INR = 1_000_000


def fallback_sum_insured_for_unpublished(
    profile: Optional[dict],
    default_inr: int = NO_SI_FALLBACK_DEFAULT_INR,
) -> int:
    """The SI to price a policy at when it publishes no corroborated SI:
    the user's desired_sum_insured_inr if set, else ₹10 L (D2). No clamp."""
    profile = profile or {}
    v = profile.get("desired_sum_insured_inr")
    try:
        iv = int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        iv = 0
    return iv if iv > 0 else int(default_inr)


def _fmt_inr_cover(v: int) -> str:
    """Human SI for the disclosure string: ₹10 L / ₹1.5 Cr (no stray .0)."""
    if v >= 10_000_000:
        return f"₹{v / 10_000_000:g} Cr"
    return f"₹{v / 100_000:g} L"


def unpublished_si_disclosure(sum_insured_inr: int) -> str:
    """The exact, verbatim disclosure the frontend renders when a policy has
    no published SI and the estimate was priced against a fallback cover."""
    return (
        f"Estimate shown for {_fmt_inr_cover(int(sum_insured_inr))} cover — "
        "this policy's sum insured isn't published."
    )


def _round_to_500(x: float) -> int:
    """Round to nearest ₹500 — band-display granularity (per spec).

    Retained for `median_inr` (the typical-plan anchor, where nearest is the
    right rounding). The band EDGES use the directional rounders below so the
    displayed [min, max] is always a true superset of every basket member —
    otherwise nearest-rounding can pull max_inr *below* a real per-policy
    point and re-introduce a header≠panel contradiction at the band edge.
    """
    return int(round(float(x) / 500.0) * 500)


def _floor_to_500(x: float) -> int:
    """Round DOWN to ₹500 — used for min_inr so the band's lower edge never
    sits above the cheapest basket member (the panel's number for that plan)."""
    import math
    return int(math.floor(float(x) / 500.0) * 500)


def _ceil_to_500(x: float) -> int:
    """Round UP to ₹500 — used for max_inr so the band's upper edge always
    contains the priciest basket member, keeping the header band a strict
    superset of any per-settings panel point for the same profile+SI."""
    import math
    return int(math.ceil(float(x) / 500.0) * 500)


def _median(xs: list[int]) -> int:
    n = len(xs)
    if n == 0:
        return 0
    s = sorted(xs)
    mid = n // 2
    if n % 2 == 1:
        return int(s[mid])
    return int((s[mid - 1] + s[mid]) / 2)


def estimate_premium_band(
    profile: Optional[dict] = None,
    candidate_policy_ids: Optional[list[str]] = None,
    sum_insured_default: int = 1_000_000,
) -> dict:
    """Compute the user's predicted-premium BAND across a representative basket.

    ═══════════════════════════════════════════════════════════════════════
    HEADER-CHIP DATA CONTRACT  (read this before wiring the chip in page.tsx)
    ═══════════════════════════════════════════════════════════════════════
    THIS is the single stable function the "Premium range" header chip MUST
    derive its ₹min–₹max from. It is reached over HTTP via
    GET /api/profile/predicted-premium-band?session_id=... →
    PredictedPremiumBandResponse, surfaced in the frontend as
    `getPredictedPremiumBand()` / the `premiumBand` state in page.tsx.

    Contract guarantees (KI-278, 2026-05-16):
      • The chip band = the SAME 26-policy basket priced at the SAME
        profile-resolved SI the per-settings panel uses. The panel's point
        estimate for the user's stated SI therefore lands INSIDE
        [min_inr, max_inr] by construction (it's literally one member of
        the basket the band aggregates), so the two surfaces can never
        contradict the way they did pre-fix (header ₹6.5k–₹26.5k vs panel
        ₹19.1k for one profile).
      • SI precedence is resolved by `resolve_profile_sum_insured(profile)`
        — byte-identical to PremiumCalculatorPanel's slider seed
        (`desired_sum_insured_inr ?? existing_cover_inr ?? default`). The
        page.tsx panel/PolicyPremiumWidget MUST seed their SI slider from
        the same precedence (or call this function's resolved value via the
        `sum_insured_used` field below) so they stay aligned.
      • EVERY pricing-relevant SLOT_UNION field the caller puts in `profile`
        is folded in (age, location_tier/city_tier, dependents/family_size,
        smoker, copay_pct, family_medical_history, health_conditions,
        existing_cover_inr, parents_age_max/parents_has_ped,
        desired_sum_insured_inr). `smoker` adds the +25-40% tobacco load and
        `family_medical_history` adds the +3-10% genetic-risk load on BOTH
        the band path and the per-policy path (proven in
        tests/test_premium_reconciliation.py).
      • The chip should render `₹{min_inr}–₹{max_inr}/yr`. `median_inr` is
        the typical-plan anchor; `sum_insured_used` is the SI both surfaces
        priced at (display it so the user knows what SI the band reflects).

    Returns: {min_inr, median_inr, max_inr, sample_size, assumed,
              sum_insured_used}. Money values rounded to the nearest ₹500;
    `assumed` is True whenever ANY policy in the basket used the heuristic
    fallback (effectively always for now).
    ═══════════════════════════════════════════════════════════════════════
    """
    profile = profile or {}
    pids = candidate_policy_ids or list(_DEFAULT_BAND_POLICY_IDS)

    # KI-278 — resolve the SI from the profile with the EXACT precedence the
    # per-settings panel uses, instead of hard-coding ₹10L. This is the core
    # header≠panel reconciliation fix: both surfaces now price at the same
    # profile-driven SI. `sum_insured_default` is only the floor fallback
    # when the profile carries no SI signal at all.
    resolved_si = resolve_profile_sum_insured(
        profile, fallback_default=sum_insured_default
    )

    # Reuse B2's bulk heuristic so the chip and the slider widget agree by
    # construction. Price the WHOLE basket at the profile-resolved SI.
    overrides = {pid: {"sum_insured_inr": resolved_si} for pid in pids}
    try:
        rows = bulk_estimate(policy_ids=pids, profile=profile, overrides=overrides)
    except Exception:
        rows = {}

    premiums = [int(r.premium_inr_annual) for r in rows.values() if r.premium_inr_annual]
    any_assumed = any(r.assumed for r in rows.values()) if rows else True

    if not premiums:
        return {
            "min_inr": 0,
            "median_inr": 0,
            "max_inr": 0,
            "sample_size": 0,
            "assumed": True,
            "sum_insured_used": resolved_si,
        }

    return {
        # Directional rounding (KI-278) — floor the min / ceil the max so the
        # displayed band is a strict superset of every basket member. This is
        # what guarantees the per-settings panel's point estimate (one basket
        # member at the same profile-resolved SI) always reads as INSIDE the
        # header band the user sees.
        "min_inr": _floor_to_500(min(premiums)),
        "median_inr": _round_to_500(_median(premiums)),
        "max_inr": _ceil_to_500(max(premiums)),
        "sample_size": len(premiums),
        "assumed": bool(any_assumed),
        "sum_insured_used": resolved_si,
    }
