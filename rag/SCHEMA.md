# HealthPolicy Schema — design notes

Canonical record for one Indian health insurance policy variant. One row = one
`(insurer, policy_name, variant)` tuple. Schema lives in
[`rag/schema.py`](./schema.py).

The shape is grounded in the **IRDAI Customer Information Sheet (CIS)** —
the regulator-mandated one-page disclosure every insurer must publish for
every product — supplemented by the comparison dimensions used by
PolicyBazaar / InsuranceDekho / Acko so that extracted records support both
regulator-grade and consumer-grade filtering.

## Field groupings

| # | Group | Fields | Purpose |
|---|---|---|---|
| 1 | Identity & metadata | `policy_id`, `insurer_name`, `insurer_slug`, `policy_name`, `policy_type`, `uin_code` | Primary key + IRDAI cross-reference. |
| 2 | Eligibility | `min_entry_age_years`, `max_entry_age_years`, `max_renewal_age_years`, `min_child_entry_age_days`, `family_composition_allowed`, `residency_requirement` | First-cut filter: "can this person even buy it?" |
| 3 | Sum insured & premium | `sum_insured_options_inr`, `premium_payment_modes`, `premium_range_indicative_inr`, `premium_payment_term_years`, `grace_period_days`, `free_look_period_days` | Affordability + billing flexibility. |
| 4 | Waiting periods | `initial_waiting_period_days`, `pre_existing_disease_waiting_months`, `specific_disease_waiting_months`, `specific_diseases_listed`, `maternity_waiting_months`, `sub_limits_waiting_notes` | The single biggest source of claim disputes — buyer must understand these before signing. |
| 5 | Coverage scope | `inpatient_hospitalization`, `pre/post_hospitalization_days`, `day_care_treatments`, `domiciliary_treatment`, `ayush_coverage`, `maternity_coverage`, `newborn_coverage`, `organ_donor_expenses`, `ambulance_cover`, `critical_illness_cover`, `restoration_benefit`, `no_claim_bonus_pct`, `no_claim_bonus_cap_pct`, `preventive_health_checkup` | What's covered. Each benefit uses the reusable `CoverageItem` shape (`covered`, `limit_inr`, `limit_text`, `notes`) so the verbatim CIS wording stays available for citation. |
| 6 | Sub-limits & caps | `room_rent_capping`, `icu_capping`, `copayment_pct`, `copayment_trigger_notes`, `disease_wise_sub_limits`, `deductible_amount_inr` | What's **not** fully covered — the hidden gotchas. |
| 7 | Geography & network | `geographic_coverage`, `worldwide_emergency_cover`, `network_hospital_count`, `cashless_treatment_supported` | Where the policy works. |
| 8 | Exclusions | `permanent_exclusions`, `temporary_exclusions`, `notable_exclusions_summary` | IRDAI standardised the permanent-exclusion list in 2020 — relatively easy to extract. |
| 9 | Claim & service | `claim_settlement_ratio_pct`, `claim_process_summary`, `tat_cashless_authorization_hours` | Trust signals. |
| 10 | Riders | `available_riders`, `top_rider_examples`, `rider_premium_indicative_inr` | Up-sell surface. |
| 11 | Source metadata | `source_pdf_path`, `source_pdf_url`, `last_updated_date`, `extraction_confidence_pct` | Provenance & quality gating. |

Field count: ~48, mostly `Optional[...]` because PDF extraction is lossy.

## Critical vs nice-to-have

**Critical for side-by-side comparison** — these drive almost every buyer
decision and must be extracted reliably:

- `sum_insured_options_inr`, `policy_type`
- `pre_existing_disease_waiting_months`, `initial_waiting_period_days`,
  `specific_disease_waiting_months`, `maternity_waiting_months`
- `room_rent_capping`, `icu_capping`, `copayment_pct`, `deductible_amount_inr`
- `pre_hospitalization_days`, `post_hospitalization_days`
- `no_claim_bonus_pct`, `restoration_benefit`
- `ayush_coverage`, `maternity_coverage`, `critical_illness_cover`
- `network_hospital_count`, `cashless_treatment_supported`

**Nice-to-have** — useful for narrative / pitch but not deal-breakers:

- `top_rider_examples`, `rider_premium_indicative_inr`
- `preventive_health_checkup`, `domiciliary_treatment`
- `worldwide_emergency_cover`
- `disease_wise_sub_limits` (the dict can stay sparse)

## Likely-hard-to-extract fields (and why)

| Field | Why it's hard | Where to actually get it |
|---|---|---|
| `claim_settlement_ratio_pct` | Not in the policy wordings PDF at all. Insurer-level, not policy-level. | IRDAI Annual Report (Statement 11) — separate scrape, joined on `insurer_slug`. |
| `network_hospital_count` | Quoted in marketing pages, rarely in wordings. Changes weekly. | Insurer's hospital-locator API or the IRDAI "Network Hospital" portal. |
| `premium_range_indicative_inr` | Wordings never contain pricing. | Public quote engines (PolicyBazaar etc.) for a fixed benchmark profile. |
| `disease_wise_sub_limits` | Usually buried in an annexure with inconsistent table layouts. | Targeted second-pass extraction with table-aware models (Camelot / pdfplumber). |
| `tat_cashless_authorization_hours` | IRDAI mandated 1 hour in 2024, but older PDFs still say "as per regulations". | Default to 1.0 if absent and policy is post-2024; flag otherwise. |
| `uin_code` | Present but easy to confuse with similar product codes; same insurer reuses prefixes. | Regex `[A-Z]{4,6}HLIP\d{5}V\d{6}` with cross-check against IRDAI's product master. |
| `specific_diseases_listed` | Listed in an annexure, often with sub-bullets; needs structure-preserving extraction. | LLM extraction with a clear schema example shot. |

`extraction_confidence_pct` is the gating signal — records below ~70 should be
flagged for human review before being served to users.

## v2 expansion: Life / Motor / Travel

The schema is forward-compatible without breaking changes:

1. **Shared header.** `policy_id`, `insurer_name`, `insurer_slug`, `policy_name`,
   `policy_type`, `uin_code`, plus the entire **Source metadata** group, apply
   to every line of business. Move them into a shared `PolicyBase` mixin when
   the second LOB lands.
2. **Sibling models.** Create `LifePolicy`, `MotorPolicy`, `TravelPolicy`
   alongside `HealthPolicy`. Each inherits the shared header and adds its own
   category-specific groups (e.g. `LifePolicy` adds `policy_term_years`,
   `death_benefit_inr`, `maturity_benefit_inr`, `surrender_value_table`).
3. **Discriminator field.** Add `line_of_business: Literal["health","life","motor","travel"]`
   at the base. Storage and retrieval layers route by this field.
4. **Backward compatibility.** `HealthPolicy` keeps `Config.extra = "allow"`,
   so any v2 keys that briefly leak into a health record (during migration)
   are preserved rather than dropped. **Never remove or rename existing
   fields** — downstream extractors and the RAG vector store key off them.

## Storage & embedding notes

- One record per JSON file under `rag/extracted/`. The `policy_id` is the
  filename.
- For the vector store, embed two views of each record:
  - The full prose of `notable_exclusions_summary` + `claim_process_summary`
    (high-signal narrative chunks for semantic queries).
  - A flattened key-value string for every populated field (so structured
    queries like "policies with PED waiting < 24 months" can still match).
- The original policy wordings PDF stays in `rag/corpus/` for citation
  fallback. The schema's `source_pdf_path` field is the link back.

## Chroma chunk metadata

Each chunk persisted in Chroma carries the following metadata keys (set by
`rag/ingest.py`):

| Key | Type | Notes |
|---|---|---|
| `policy_id` | str | e.g. `aditya-birla__activ-one`. Primary filter for per-policy retrieval. |
| `insurer_slug` | str | e.g. `aditya-birla`. Secondary filter. |
| `source_pdf` | str | Relative path under `rag/corpus/`. |
| `page` | int | 1-indexed PDF page number. |
| `chunk_index` | int | Position within the policy's chunk sequence. |
| `doc_type` | str | `'wordings'` / `'brochure'` / `'cis'` / `'prospectus'` / `'curated'`. **`'curated'` (KI-137)** marks chunks ingested from hand-curated `40-data/policy_facts/<id>.json` rather than raw PDF text. |
| `legacy_issuer` | str (optional) | **KI-144.** Present on `indusind-general__*` chunks whose source PDFs carry the previous `reliance-general` issuer branding. Value: `'reliance-general'`. Lets retrieval surface legacy citations without breaking the canonical slug. |
