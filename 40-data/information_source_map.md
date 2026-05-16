# Information Source Map — Structured-Fact KB
_Last updated: 2026-05-15_

This file aggregates the source metadata embedded in `40-data/policy_facts/`, `40-data/reviews/`, and `40-data/premiums/illustrative_premiums.json` so every populated fact in the KB can be traced to a PDF, URL, or rate card. J3 verification will fill the `verified` / `last_verified` fields downstream.

## 1. Aggregate Stats

- **Total cited facts:** 5,616
  - policy_facts: **5,460**
  - insurer_reviews: **65**
  - premium samples: **91**
- **Distinct source URLs:** 97
- **Distinct PDF paths (policy wordings/brochures):** 188
- **% policy_facts with PDF citation:** 98.8%
- **% policy_facts with explicit URL:** 7.4%
- **% review facts with primary URL:** 100.0%
- **% premium samples with real (non-derived) URL:** 40.7%

### Source-type breakdown

**Policy facts**

| source_type | count |
|---|---:|
| policy_pdf | 5,046 |
| web_url | 405 |
| missing | 9 |

**Reviews**

| source_type | count |
|---|---:|
| irdai_annual_report | 43 |
| irdai_complaints | 19 |
| irdai | 3 |

**Premiums**

| source_type | count |
|---|---:|
| derived_anchor | 54 |
| insurer_or_other_url | 16 |
| policybazaar_tile | 10 |
| official_rate_card | 9 |
| joinditto_chart | 2 |

## 2. Coverage by Category

### 2.1 Policy facts — per-insurer policy counts

| insurer_slug | policies indexed | populated fact rows |
|---|---:|---:|
| acko | 9 | 203 |
| aditya-birla | 8 | 137 |
| bajaj-allianz | 17 | 301 |
| care-health | 15 | 249 |
| cholamandalam | 6 | 116 |
| go-digit | 6 | 154 |
| hdfc-ergo | 23 | 420 |
| icici-lombard | 17 | 322 |
| iffco-tokio | 6 | 165 |
| indusind-general | 3 | 48 |
| manipalcigna | 8 | 155 |
| national-insurance | 40 | 1,122 |
| new-india | 14 | 178 |
| niva-bupa | 19 | 375 |
| oriental-insurance | 6 | 141 |
| reliance-general | 1 | 9 |
| royal-sundaram | 14 | 414 |
| sbi-general | 12 | 343 |
| star-health | 18 | 372 |
| tata-aig | 11 | 236 |

### 2.2 Insurer reviews — CSR / complaint coverage

| insurer_slug | CSR % | CSR year | complaints/10K | source_irdai_url present? | source_complaints_url present? |
|---|---:|---|---:|---|---|
| acko | 96.31 | FY 2023-24 | 16 | yes | yes |
| aditya-birla | 92.97 | 2023-24 | 13 | yes | no |
| bajaj-allianz | 92.24 | 2023-24 | 3 | yes | no |
| care-health | 93.13 | 2023-24 (3-year avg) | 42 | yes | no |
| cholamandalam | 94.5 | FY 2023-24 | 13 | yes | yes |
| go-digit | 90.69 | FY 2023-24 | 19 | yes | yes |
| hdfc-ergo | 99.1 | 2023-24 | 15 | yes | no |
| icici-lombard | 85.0 | 2023-24 | 10 | yes | no |
| iffco-tokio | 96.33 | FY 2023-24 | 41 | yes | yes |
| indusind-general | 86.38 | FY 2024-25 | — | yes | yes |
| manipalcigna | 99.0 | 2023-24 | 24 | yes | no |
| national-insurance | 91.18 | FY 2023-24 | 29 | yes | yes |
| new-india | 95.04 | 2023-24 (by claim count) | 20 | yes | no |
| niva-bupa | 91.62 | 2023-24 (3-year avg through FY25) | 43 | yes | no |
| oriental-insurance | 93.96 | FY 2023-24 | — | yes | yes |
| reliance-general | 98.75 | FY 2023-24 | 5 | yes | yes |
| royal-sundaram | 95.95 | FY 2023-24 | 18 | yes | yes |
| sbi-general | 96.14 | FY 2022-25 (3-yr avg) | 15 | yes | yes |
| star-health | 82.31 | 2023-24 | 52 | yes | no |
| tata-aig | 88.72 | 2023-24 (3-year avg) | 11 | yes | no |

### 2.3 Premiums — per-policy sample coverage

| policy_id | sample count | real-URL samples | derived samples |
|---|---:|---:|---:|
| aditya-birla-activ-assure-diamond | 5 | 1 | 4 |
| aditya-birla-group-activ-health | 3 | 0 | 3 |
| bajaj-allianz-health-guard | 5 | 1 | 4 |
| bajaj-allianz-silver-health | 3 | 1 | 2 |
| bajaj-allianz-tax-gain | 1 | 1 | 0 |
| care-health-care-advantage | 1 | 1 | 0 |
| care-health-care-classic | 2 | 1 | 1 |
| care-health-care-senior | 3 | 1 | 2 |
| care-health-care-supreme | 5 | 2 | 3 |
| hdfc-ergo-energy | 3 | 0 | 3 |
| hdfc-ergo-optima-plus | 2 | 1 | 1 |
| hdfc-ergo-optima-restore | 3 | 0 | 3 |
| hdfc-ergo-optima-secure | 5 | 2 | 3 |
| icici-lombard-elevate | 4 | 1 | 3 |
| icici-lombard-health-advantedge | 2 | 1 | 1 |
| manipalcigna-prohealth-prime-active | 3 | 1 | 2 |
| new-india-asha-kiran | 2 | 1 | 1 |
| new-india-mediclaim | 3 | 1 | 2 |
| niva-bupa-aspire | 3 | 1 | 2 |
| niva-bupa-health-premia | 3 | 1 | 2 |
| niva-bupa-reassure | 4 | 2 | 2 |
| royal-sundaram-advanced-top-up | 3 | 3 | 0 |
| sbi-general-arogya-supreme | 2 | 2 | 0 |
| star-health-comprehensive | 3 | 1 | 2 |
| star-health-family-health-optima | 5 | 3 | 2 |
| star-health-senior-red-carpet | 5 | 1 | 4 |
| tata-aig-medicare | 3 | 2 | 1 |
| tata-aig-medicare-premier | 5 | 4 | 1 |

## 3. Needs-source (unverifiable until repaired)

- policy_facts rows lacking any source path/URL or explicitly marked "not extracted": **9**
- review rows lacking a primary IRDAI URL: **0**
- premium samples derived from anchors (not directly sourced): **54**
- premium samples with no URL at all: **0**

### Top 15 policy_fact fields with missing source metadata

| field | rows missing source |
|---|---:|
| network_hospital_count | 2 |
| maternity_coverage | 2 |
| pre_existing_disease_waiting_months | 2 |
| ayush_coverage | 2 |
| cashless_treatment_supported | 1 |

## 4. How to read the JSON twin

- `policy_facts[]` — one row per populated `(policy_id, field)` pair, with `source_path` (PDF), optional `source_url`, `source_quote`, `confidence`, and J3-bound `verified` / `last_verified` slots.
- `insurer_reviews[]` — one row per populated `(insurer_slug, metric)` pair, with primary IRDAI URL plus optional secondary / insurer-company URLs and the reporting `year`.
- `premiums[]` — one row per `(policy_id, sample_profile)` pair with `annual_premium_inr`, `source_url`, `source_note`, and a `source_type` of `policybazaar_tile` / `joinditto_chart` / `beshak_review` / `official_rate_card` / `derived_anchor`.
- `needs_source.*` — flat lists of rows where source metadata is absent or explicitly marked missing, so we know exactly what J3 verification cannot yet check.
