# Scorecard Knowledge Graph

How the scorecard responds to (a) the policy's own attributes and (b) the buyer's profile inputs. Every entry is sourced from the rules baked into `backend/scorecard.py` — this doc is the human-readable spec, not free-form opinion.

---

## Part A — Policy attribute → sub-score delta

Each row: "if the policy has this value, the listed sub-score moves by this delta." Sub-scores are 0–100. The overall A–F grade is the weighted average (see Part B for weight tuning).

### Coverage Breadth (base 60, weight 22%)

| Attribute | Value condition | Sub-score delta | Source rule |
|---|---|---|---|
| `ayush_coverage` | true | +8 | "AYUSH covered" |
| `ayush_coverage` | false | −5 | "no AYUSH" |
| `day_care_treatments_count` | ≥400 | +10 | "{N} day-care procedures" |
| `day_care_treatments_count` | 200–399 | +6 | |
| `day_care_treatments_count` | <100 | −5 | "only {N} day-care procedures" |
| `maternity_coverage` | true | +6 | "maternity covered" |
| `newborn_coverage` | true | +4 | "newborn covered" |
| `organ_donor_expenses` | true | +4 | |
| `ambulance_cover` | true | +3 | |
| `domiciliary_treatment` | true | +4 | |
| `preventive_health_checkup` | true | +3 | "free health checkups" |
| `pre_hospitalization_days` | ≥60 | +4 | |
| `post_hospitalization_days` | ≥90 | +4 | |

### Cost Predictability (base 75, weight 20%)

| Attribute | Value condition | Sub-score delta |
|---|---|---|
| `copayment_pct` | 0 | +0 (baseline) |
| `copayment_pct` | 10 | −5 |
| `copayment_pct` | ≥20 | −12 |
| `room_rent_capping` | "No limit" / "Single Private A/C Room" | +6 |
| `room_rent_capping` | capped (any % or amount) | −5 to −10 |
| `deductible_amount` | 0 | +0 |
| `deductible_amount` | ≥100000 | −8 |
| `sub_limits` | absent | +5 |
| `icu_charges_capping` | none | +3 |

### Waiting-Period Friction (base 70, weight 18%)

| Attribute | Value condition | Sub-score delta |
|---|---|---|
| `initial_waiting_period_days` | ≤30 | +0 (industry standard) |
| `initial_waiting_period_days` | >30 | −3 |
| `pre_existing_disease_waiting_months` | ≤24 | +10 |
| `pre_existing_disease_waiting_months` | 36 | +0 (industry standard, IRDAI cap) |
| `pre_existing_disease_waiting_months` | ≥48 | −15 (non-conformant) |
| `maternity_waiting_months` | ≤24 | +5 |
| `maternity_waiting_months` | ≥36 | −5 |
| `specific_disease_waiting_months` | ≤24 | +3 |

### Claim Experience (base 65, weight 20%) — uses insurer-level data

| Attribute | Value condition | Sub-score delta |
|---|---|---|
| `cashless_treatment_supported` | true | +5 |
| `network_hospital_count` | ≥10,000 | +10 |
| `network_hospital_count` | 5,000–9,999 | +5 |
| `network_hospital_count` | <2,000 | −5 |
| `claim_settlement_ratio` (IRDAI) | ≥95% | +12 |
| `claim_settlement_ratio` | 90–95% | +6 |
| `claim_settlement_ratio` | <85% | −10 |
| `complaints_per_10k_policies` | <5 | +4 |
| `complaints_per_10k_policies` | >20 | −8 |
| `tat_cashless_authorization_hours` | ≤2 | +4 |
| `tat_cashless_authorization_hours` | ≥24 | −4 |

### Renewal Protection (base 50, weight 12%)

> Renewal Protection now = `max_entry_age` only. Lifelong renewability is the IRDAI universal norm for every health-indemnity product (mandated since 2020), so it is constant across the market and is **not scored** — `max_renewal_age` was removed entirely as a scored field.

| Attribute | Value condition | Sub-score delta |
|---|---|---|
| `max_entry_age` | ≥65 | +25 |
| `max_entry_age` | ≥55 | +12 |
| `max_entry_age` | ≥50 | +0 |
| `max_entry_age` | <50 | −20 |
| (lifelong renewability) | IRDAI-universal | not scored — shown for transparency only |

### Bonus & Loyalty (base 60, weight 8%)

| Attribute | Value condition | Sub-score delta |
|---|---|---|
| `no_claim_bonus_pct` | ≥50 | +8 |
| `no_claim_bonus_pct` | 25–49 | +4 |
| `restoration_benefit` | present | +6 |
| `preventive_health_checkup` | free annually | +3 |
| `wellness_program_present` | true | +2 |

---

## Part B — User input → weight redistribution

Same 6 sub-scores, but the WEIGHTS shift based on what we know about the buyer. Every collected signal moves at least one weight — if a field doesn't appear here, we wasted attention collecting it.

Each delta is applied to the base weights then **renormalised to sum 1.0** with a 5% per-criterion floor.

### Age

| Age band | Weight deltas |
|---|---|
| <30 | Waiting-Period Friction +0.04, Claim Experience +0.02, Renewal Protection −0.04, Bonus & Loyalty −0.02 |
| 30–49 | (no shift) |
| ≥50 | Renewal Protection +0.06, Claim Experience +0.02, Bonus & Loyalty −0.04, Waiting-Period Friction −0.04 |

### Dependents

| Dependent | Weight deltas |
|---|---|
| kids / children | Coverage Breadth +0.03, Bonus & Loyalty +0.01, Cost Predictability −0.02, Renewal Protection −0.02 |
| spouse | Coverage Breadth +0.02, Waiting-Period Friction +0.02, Bonus & Loyalty −0.02, Renewal Protection −0.02 |
| parents | Coverage Breadth +0.04, Claim Experience +0.04, Bonus & Loyalty −0.04, Cost Predictability −0.04 |
| parents with PED or age ≥65 | + extra: Renewal Protection +0.04, Waiting-Period Friction +0.02, Bonus & Loyalty −0.04, Cost Predictability −0.02 |

### Existing cover

| Condition | Weight deltas |
|---|---|
| Has existing cover >0 (super-top-up buyer) | Cost Predictability −0.03, Claim Experience +0.03 |
| No existing cover (first-time buyer) | Cost Predictability +0.03, Coverage Breadth +0.02, Bonus & Loyalty −0.03, Waiting-Period Friction −0.02 |

### Primary goal

| Goal | Weight deltas |
|---|---|
| Tax planning | Cost Predictability +0.02, Bonus & Loyalty −0.02 |
| Upgrade existing cover | Coverage Breadth +0.03, Renewal Protection +0.02, Bonus & Loyalty −0.05 |
| Compare specific policies | Flatten weights (5% pull to uniform — user already knows what matters) |

### Health conditions

| Condition | Weight deltas |
|---|---|
| Diabetes / BP / hyper / thyroid / heart / cancer / asthma | Waiting-Period Friction +0.06, Claim Experience +0.03, Bonus & Loyalty −0.04, Cost Predictability −0.03, Renewal Protection −0.02 |

### Budget band

| Band | Weight deltas |
|---|---|
| under_15k or 15k_30k | Cost Predictability +0.04, Bonus & Loyalty −0.02, Waiting-Period Friction −0.02 |
| 60k+ | Coverage Breadth +0.02, Claim Experience +0.02, Cost Predictability −0.04 |

### Income band

| Band | Weight deltas |
|---|---|
| under_5L | Cost Predictability +0.03, Bonus & Loyalty −0.03 |
| 10L–25L / 25L+ | Coverage Breadth +0.02, Claim Experience +0.02, Cost Predictability −0.04 |

### Location tier

| Tier | Weight deltas |
|---|---|
| tier2 / tier3 | Claim Experience +0.04, Coverage Breadth −0.02, Bonus & Loyalty −0.02 |
| metro | Coverage Breadth +0.02, Claim Experience −0.02 |

---

## Worked example

A 55-year-old with diabetic parents in tier-2 city, ₹30k budget, ₹5L existing cover, primary goal "upgrade":

- Age 55 → +0.06 Renewal, +0.02 Claim, −0.04 Bonus, −0.04 Waiting
- Dependents include parents (PED, age 70) → +0.04 Coverage, +0.04 Claim, −0.04 Bonus, −0.04 Cost; +0.04 Renewal, +0.02 Waiting, −0.04 Bonus, −0.02 Cost
- Existing cover >0 → −0.03 Cost, +0.03 Claim
- Goal "upgrade" → +0.03 Coverage, +0.02 Renewal, −0.05 Bonus
- Conditions diabetes (via parents) → +0.06 Waiting, +0.03 Claim, −0.04 Bonus, −0.03 Cost, −0.02 Renewal
- Tier-2 → +0.04 Claim, −0.02 Coverage, −0.02 Bonus

Result (post-renormalise + floor):
- Claim Experience: 20% → 31% (the dominant criterion — getting paid matters most)
- Coverage Breadth: 22% → 25%
- Waiting-Period Friction: 18% → 20%
- Renewal Protection: 12% → 16%
- Bonus & Loyalty: 8% → 5% (floor)
- Cost Predictability: 20% → 5% (floor)

The buyer's profile is correctly read as: "I'm older, my parents are sick, I'm in a smaller city, I already have basic cover. What I actually need is INSURER QUALITY (will they pay?) and RENEWAL CONTINUITY (can I keep this when I'm 70?)."

---

## Maintenance contract

Whenever a rule in `backend/scorecard.py` changes (new condition, different threshold, different delta), the matching row in this document must be updated in the same commit. Drift between code and this doc breaks the transparency promise the scorecard makes to the buyer.
