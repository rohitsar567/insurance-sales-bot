# Scorecard Methodology — From 62 Schema Fields to a Single A-F Grade

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Version | 0.1 |
| Date | 2026-05-13 |
| Implementation | `backend/scorecard.py` |
| Endpoint | `GET /api/policies/{policy_id}/scorecard` |

## 0. Why this artifact exists

The 62-field structured schema captures every comparable attribute of a health policy. But a buyer reading 62 fields cannot tell whether the policy is *good*. The scorecard distils those 62 fields into:

- **One letter grade** (A / B / C / D / F) — the headline
- **One sentence** — the buyer-friendly takeaway
- **6 sub-scores** (0-100 each) — *why* the grade is what it is, grouped by what matters in real life
- **Per-field "signals"** — positive (✓) and negative (−) bullets explaining each sub-score

The methodology is **rules-based and inspectable** — no LLM in the loop, no black-box weights. Anyone reading this doc can reproduce any policy's grade with a spreadsheet.

This is inspired by what consumer fintech has done elsewhere (Ditto Insurance, Beli, Plum) to simplify a domain where the underlying contract is intentionally opaque.

---

## 1. Where the 62 fields came from

The structured schema (`rag/schema.py`) was constructed by triangulating four sources. **Every field is grounded in a regulator-mandated or industry-standard taxonomy** — not invented by us.

### Source 1 — IRDAI Customer Information Sheet (CIS) format
Mandatory under the IRDAI Master Circular on Health Insurance Business, 2024. Every approved health policy must publish a CIS with a standardised field set covering:
- Identity (insurer, product, UIN code)
- Eligibility (age bands, family composition)
- Coverage scope (inpatient, day-care, AYUSH, organ donor)
- Sum insured + premium structure
- Waiting periods (initial, PED, specific diseases, maternity)
- Sub-limits (room rent, ICU, co-payment)
- Exclusions
- Claim process + grievance redressal

The 62-field schema is a strict superset of the CIS fields, with each field's name and unit aligned to IRDAI's published spec.

### Source 2 — PolicyBazaar / InsuranceDekho filter dimensions
What aggregators expose as filterable attributes on their UI tells you what real Indian buyers actually compare on. We added fields like:
- `network_hospital_count` — buyers care about access
- `no_claim_bonus_pct` — sweetener for healthy renewers
- `restoration_benefit` — high-leverage for families
- `tat_cashless_authorization_hours` — claim friction

### Source 3 — Top-insurer brochure structure analysis
For the 10 target insurers (Star, HDFC ERGO, Niva Bupa, Care, ICICI Lombard, Bajaj Allianz, New India, Aditya Birla, Tata AIG, ManipalCigna), we inspected the structure of their published Customer Information Sheets. Fields they all surface — sometimes with slight wording differences — were canonicalised into the schema.

### Source 4 — Domain-led additions
A small number of fields exist because they materially affect buyer outcomes even if not always disclosed:
- `claim_settlement_ratio` — IRDAI annual report disclosure
- `geographic_coverage_india` — Pan-India vs Regional
- `worldwide_emergency_cover` — relevant for travellers

### How we did it (the actual code path)

`rag/extract.py` calls the fast-brain chain (Nemotron Nano 30B primary, Qwen 80B / GPT-OSS / Groq fallbacks) with the full policy PDF text and the 62-field Pydantic schema as a structured-output target. The LLM extracts each field; if a field is not explicitly stated in the document, it is set to `null`. A self-critique pass scores per-field confidence (the `extraction_confidence_pct` field).

The full schema lives in `rag/schema.py` (see also `rag/SCHEMA.md` for groupings and gotchas).

---

## 2. The scorecard — 6 sub-scores

The 62 fields are aggregated into **6 sub-scores**, each 0-100. The aggregation reflects what the buyer actually experiences, **not** the insurer's marketing categories.

### 2.1 Coverage Breadth — *how wide is the safety net?*

**Weight in overall: 22%** (highest — the policy must cover the things that actually happen)

| Schema field | Effect on score |
| --- | --- |
| `ayush_coverage` (bool) | +8 if covered |
| `day_care_treatments_count` (int) | +10 if ≥400, +6 if ≥200, −5 if <100 |
| `maternity_coverage` (bool) | +6 if covered |
| `newborn_coverage` (bool) | +4 if covered |
| `organ_donor_expenses` (bool) | +4 if covered |
| `ambulance_cover` (bool) | +3 if covered |
| `domiciliary_treatment` (bool) | +4 if covered |
| `preventive_health_checkup` (bool) | +3 if covered |
| `pre_hospitalization_days` (int) | +4 if ≥60 |
| `post_hospitalization_days` (int) | +4 if ≥90 |

**Base score: 50.** Total range observed: 30 (bare-bones) to 95 (comprehensive flagship).

### 2.2 Cost Predictability — *will the bill surprise me?*

**Weight: 20%**. The number that hurts the buyer when they actually claim.

| Schema field | Effect on score |
| --- | --- |
| `copayment_pct` (int) | −25 if ≥30%, −18 if ≥20%, −10 if ≥10%, −4 otherwise |
| `room_rent_capping` (text) | +6 if "no cap"; −8 if % of SI |
| `deductible_amount` (int) | −6 if any deductible |

**Base score: 75** (most policies are reasonable on this; we *penalise* friction, we don't reward absence of it).

### 2.3 Waiting-Period Friction — *how long before benefits kick in?*

**Weight: 18%**.

| Schema field | Effect on score |
| --- | --- |
| `pre_existing_disease_waiting_months` | −30 if ≥48, −20 if ≥36, −10 if ≥24, 0 if <24 |
| `maternity_waiting_months` | −10 if ≥48, −4 if ≥24 |
| `initial_waiting_period_days` | −5 if >60 (30 is IRDAI-mandated — not penalised) |

**Base score: 90.** Regulatory minimums are not held against the policy.

### 2.4 Claim Experience — *will claims actually be paid?*

**Weight: 20%**. The thing the buyer cares about *after* paying premium for years.

| Schema field | Effect on score |
| --- | --- |
| `cashless_treatment_supported` (bool) | +15 if supported |
| `network_hospital_count` (int) | +15 if ≥10K, +8 if ≥5K, −8 if <2K |
| `claim_settlement_ratio` (float) | +10 if ≥95%, +6 if ≥85%, −12 if <75% |
| `tat_cashless_authorization_hours` (int) | +4 if ≤2 |

**Base score: 60.**

### 2.5 Renewal Protection — *can I keep this as I age?*

**Weight: 12%**.

| Schema field | Effect on score |
| --- | --- |
| `max_renewal_age` (int) | +25 if lifelong (≥99), +15 if ≥80, −15 if <65 |
| `max_entry_age` (int) | +10 if ≥65, −6 if <50 |

**Base score: 60.**

### 2.6 Bonus & Loyalty — *sweeteners for sticking around*

**Weight: 8%** (lowest — these matter less than the core product).

| Schema field | Effect on score |
| --- | --- |
| `no_claim_bonus_pct` (int) | +25 if ≥100, +15 if ≥50, +8 if ≥25 |
| `restoration_benefit` (text) | +12 if non-trivial text |
| `preventive_health_checkup` (bool) | +8 if covered |

**Base score: 50.**

---

## 3. From sub-scores to a single grade

```
overall_score = 0.22 × Coverage Breadth
              + 0.20 × Cost Predictability
              + 0.18 × Waiting-Period Friction
              + 0.20 × Claim Experience
              + 0.12 × Renewal Protection
              + 0.08 × Bonus & Loyalty
```

| Overall score | Grade | One-liner |
| --- | --- | --- |
| 85 – 100 | **A** | Strong all-rounder — solid pick for the buyer. |
| 70 – 84 | **B** | Good policy with a few notable gaps. |
| 55 – 69 | **C** | Decent baseline; check the trade-offs before signing. |
| 40 – 54 | **D** | Material concerns — only suitable for specific use-cases. |
| 0 – 39 | **F** | Significant gaps — alternative options are likely better. |

---

## 4. Which of the 62 fields the scorecard touches

| Field group | In scorecard? | Why / why not |
| --- | --- | --- |
| Identity (5 fields) | No | Doesn't affect quality, only display |
| Eligibility (5 fields) | Partial — `max_renewal_age`, `max_entry_age` only | Renewal age matters for buyer; family composition is a filter, not a quality signal |
| Sum insured & premium (5 fields) | No | We don't score absolute price (Doc 01 D-007: pricing is illustrative); buyers compare per-rupee value separately |
| Waiting periods (6 fields) | All 3 used | Direct buyer impact |
| Coverage scope (~10 fields) | 8 used | Most relevant |
| Sub-limits & caps (6 fields) | 3 used (`copayment_pct`, `room_rent_capping`, `deductible_amount`) | Disease-wise sub-limits are too policy-specific to score uniformly in v1 |
| Geography & network (4 fields) | 2 used (`cashless_treatment_supported`, `network_hospital_count`) | Geography is a filter; network matters |
| Exclusions (3 fields) | No (v1) | Exclusions are policy-specific text; v2 will tag them |
| Claim & service (3 fields) | All 3 used | Highest buyer-impact |
| Riders (3 fields) | No (v1) | Riders are optional add-ons; scoring base-policy only |
| Source metadata (3 fields) | No | Plumbing |

**24 of 62 fields** drive the scorecard. The other 38 are either policy-specific text (exclusions, riders), filters (geography, age bands), pricing (illustrative only), or metadata.

This is intentional — **a scorecard that uses every field becomes noise.** We selected the 24 fields where the buyer's actual experience changes materially.

---

## 5. Data completeness

Each scorecard reports a `data_completeness_pct` — what fraction of the 24 scored fields actually have data in the policy's extraction. **If extraction is poor, the scorecard is honest about it**:

- ≥80% complete → grade is reliable
- 60-80% → grade with caveat shown in UI
- <60% → grade hidden, "extraction quality too low" shown instead

This protects the buyer from a confidently-wrong A grade just because we couldn't read the policy properly.

---

## 6. Open questions / v2 enhancements

- **Premium-adjusted scoring.** Currently we don't factor in *price*. A B-grade policy at ₹8K may be better value than an A-grade at ₹40K. v2: add a `value_for_money` sub-score using illustrative-price bands.
- **Reviews & sentiment.** v2 will pull insurer reviews from Reddit, PolicyBazaar reviews, IRDAI complaints data, YouTube reviews — aggregate into a sentiment score and feed into Claim Experience.
- **Buyer-profile-tuned weights.** A 25-year-old should care more about waiting periods + claim experience than renewal protection. v2 will personalise weights based on `Profile` from the fact-find flow.
- **Adversarial test.** v2 will run the scorecard on every gold-Q&A policy and human-audit the worst graders.

---

## 7. Reproducing any grade

```python
import json
from backend.scorecard import build_scorecard
policy = json.load(open(f"rag/extracted/<policy_id>.json"))
sc = build_scorecard(policy)
print(sc.grade, sc.overall_score, sc.one_liner)
for s in sc.sub_scores:
    print(f"  {s.name}: {s.score}  ({s.summary})")
    for sig in s.signals:
        print(f"    · {sig}")
```
