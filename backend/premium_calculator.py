"""Illustrative premium calculator — rules-based estimate from a curated grid
of public PolicyBazaar / InsuranceDekho quotes.

The output is explicitly an **illustrative band**, not a quote. See decisions.md
D-007 — we are an advisor, not a broker. Real premiums depend on underwriting.

How it works:
  1. Load `data/premiums/illustrative_premiums.json` (curated by research agent
     from real quote-page scrapes; every value has a source_url).
  2. Given user inputs (age, sum_insured, city_tier, smoker, family_size,
     optional policy_id):
       - Look up the policy's base sample points
       - Find the closest sample (or interpolate between two)
       - Apply scaling multipliers for age, sum_insured, city_tier, smoker,
         family_floater
  3. Return a band of (low, mid, high) — low/high are ±15% wings around the
     point estimate, reflecting underwriting variance.
"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.config import settings

ROOT = settings.CORPUS_DIR.parent.parent
PREMIUM_DATA = ROOT / "data" / "premiums" / "illustrative_premiums.json"


@dataclass
class PremiumEstimate:
    policy_id: str
    point_estimate_inr: int
    low_inr: int
    high_inr: int
    base_sample_used: Optional[dict] = None
    methodology: str = ""
    sources: list[str] = None


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
FALLBACK_FLOATER = {1: 1.0, 2: 1.65, 3: 2.0, 4: 2.4, 5: 2.7, 6: 2.95}


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
