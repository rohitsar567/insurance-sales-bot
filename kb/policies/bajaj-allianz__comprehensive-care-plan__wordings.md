# Comprehensive Care Plan

_Policy KB sheet — auto-generated from `rag/extracted/bajaj-allianz__comprehensive-care-plan__wordings.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Insurer | [Bajaj Allianz General Insurance Co. Ltd.](https://www.bajajallianz.com/) | curated · verified `eval/verified_urls.json` |
| Insurer slug | `bajaj-allianz` | derived from `data/corpus_urls.md` |
| Policy | **Comprehensive Care Plan** | extracted from policy wordings |
| Policy id | `bajaj-allianz__comprehensive-care-plan__wordings` | minted by us (`<insurer-slug>__<doc-slug>`) |
| Source PDF | […]() | downloaded + verified at ingest time |
| Extraction confidence | 85.0% (self-rated by extractor) | computed |

## Scorecard — single A-F view

### **Grade: C** (63/100)
> Decent baseline; check the trade-offs before signing.

**Data completeness:** 12.5% of the 24 scored fields have data.

| Sub-score | Bar | Score & Signals |
| --- | --- | --- |
| **Coverage Breadth** | `███████████·········` | **58/100** · Standard coverage |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;AYUSH covered |  |
| **Cost Predictability** | `███████████████·····` | **75/100** · Predictable costs |
| **Waiting-Period Friction** | `█████████████·······` | **65/100** · Standard waits |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 36mo PED waiting<br/>&nbsp;&nbsp;&nbsp;− 90d initial waiting |  |
| **Claim Experience** | `████████████········` | **60/100** · Standard claim experience |
| **Renewal Protection** | `████████████········` | **60/100** · Adequate |
| **Bonus & Loyalty** | `██████████··········` | **50/100** · Few extras |

_Methodology: [`docs/scorecard-methodology.md`](../../docs/scorecard-methodology.md) · 24 of 48 schema fields drive this grade._

## All extracted data points — by group

**Derivation legend:**
- **[E]** Extracted directly from policy PDF by LLM
- **[E?]** Field was in schema but extraction returned null (data missing or unclear in source)
- **[C]** Computed from extracted fields (e.g. scorecard sub-score)
- **[I]** Implied / canonicalised by us
- **[V]** Verified externally (HEAD-check, URL probe)

### Identity  _5/6 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `policy_id` | `bajaj-allianz__comprehensive-care-plan__wordings` | [I] |
| `insurer_slug` | `bajaj-allianz` | [I] |
| `insurer_name` | `Bajaj Allianz General Insurance Co. Ltd.` | [I] |
| `policy_name` | `Comprehensive Care Plan` | [I] |
| `policy_type` | _null (not in document)_ | [E?] |
| `uin_code` | `BAJHLIP15002V011415` | [E] |

### Eligibility  _0/1 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `residency_requirement` | _null (not in document)_ | [E?] |

### Sum insured & premium  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `premium_payment_modes` | _null (not in document)_ | [E?] |
| `grace_period_days` | _null (not in document)_ | [E?] |

### Waiting periods  _2/5 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `initial_waiting_period_days` | `90` | [E] |
| `pre_existing_disease_waiting_months` | `36` | [E] |
| `specific_disease_waiting_months` | _null (not in document)_ | [E?] |
| `maternity_waiting_months` | _null (not in document)_ | [E?] |
| `specific_diseases_listed` | _null (not in document)_ | [E?] |

### Coverage scope  _2/12 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `pre_hospitalization_days` | _null (not in document)_ | [E?] |
| `post_hospitalization_days` | _null (not in document)_ | [E?] |
| `domiciliary_treatment` | _null (not in document)_ | [E?] |
| `ayush_coverage` | Yes, "AYUSH Hospital must have at least 5 in-patient beds, qualified AYUSH Medical Practitioner in charge round the clock, dedicated therapy sections, and maintain daily patient records.", (Cover includes medical expenses incurred on hospitalisation under Ayurveda, Yoga and Naturopathy Unani, Siddha and Homeopathy systems.) | [E] |
| `maternity_coverage` | _null (not in document)_ | [E?] |
| `newborn_coverage` | _null (not in document)_ | [E?] |
| `organ_donor_expenses` | _null (not in document)_ | [E?] |
| `ambulance_cover` | _null (not in document)_ | [E?] |
| `critical_illness_cover` | Yes, "Covers 17 critical illnesses including Cancer of Specified Severity, Kidney Failure Requiring Regular Dialysis, Multiple Sclerosis With Persisting Symptoms, Benign Brain Tumor, Parkinson’s Disease, Alzheimer’s Disease, End Stage Liver Disease, Primary Pulmonary Arterial Hypertension, Major Organ/Bone Marrow Transplant, Open Heart Replacement or Repair of Heart Valves, Open Chest CABG, Surgery of Aorta, Stroke Resulting in Permanent Symptoms, Permanent Paralysis of Limbs, First Heart Attack of Specified Severity, Major Burns, Coma of Specified Severity.", (Cover terminates after a claim is admitted and paid up to the full Sum Insured.) | [E] |
| `restoration_benefit` | _null (not in document)_ | [E?] |
| `no_claim_bonus_pct` | _null (not in document)_ | [E?] |
| `preventive_health_checkup` | _null (not in document)_ | [E?] |

### Sub-limits & caps  _0/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `room_rent_capping` | _null (not in document)_ | [E?] |
| `icu_capping` | _null (not in document)_ | [E?] |
| `copayment_pct` | _null (not in document)_ | [E?] |
| `disease_wise_sub_limits` | _null (not in document)_ | [E?] |

### Geography & network  _0/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `worldwide_emergency_cover` | _null (not in document)_ | [E?] |
| `network_hospital_count` | _null (not in document)_ | [E?] |
| `cashless_treatment_supported` | _null (not in document)_ | [E?] |

### Exclusions  _2/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `permanent_exclusions` | `Acts of Terrorism`, `War, war-like operations, act of foreign enemy, invasion of Indian territory or any part thereof, hostilities (whether war be declared or not), civil war, rebellion, revolution, insurrection, civil commotion, military or usurped power, or loot or pillage in connection with the foregoing, seizure, capture, confiscation, arrests, restraints and detainment by order of any governments or any other authority`, `Directly or indirectly caused by or contributed to by or arising from ionizing radiation or contamination by radioactivity from any nuclear fuel or from any nuclear waste or from the combustion of nuclear fuel`, `Directly or indirectly caused by or contributed to by or arising from nuclear weapon materials`, `Arising or resulting from the Insured committing any breach of the law with criminal intent`, `Any loss or damage resulting from deliberate or intentional acts of the insured`, `Directly or indirectly caused by or contributed to by or arising out of usage, consumption or abuse of alcohol and/or drugs`, `Arising out of or as a result of any act of self-destruction or self inflicted injury, attempted suicide or suicide` | [E] |
| `temporary_exclusions` | _null (not in document)_ | [E?] |
| `notable_exclusions_summary` | `The policy excludes coverage for acts of terrorism, war, nuclear events, criminal acts, intentional self-harm, alcohol/drug abuse, sexually transmitted diseases, pregnancy-related treatments, and military service during war. Additionally, pre-existing diseases are excluded for the first 36 months.` | [E] |

### Claim & service  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `claim_process_summary` | _null (not in document)_ | [E?] |
| `tat_cashless_authorization_hours` | _null (not in document)_ | [E?] |

### Riders / optional  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `available_riders` | _null (not in document)_ | [E?] |
| `top_rider_examples` | _null (not in document)_ | [E?] |

### Source metadata  _1/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `source_pdf_path` | _null (not in document)_ | [V] |
| `source_pdf_url` | _null (not in document)_ | [V] |
| `last_updated_date` | _null (not in document)_ | [V] |
| `extraction_confidence_pct` | `85.0` | [E] |

## Lineage — end-to-end audit trail for this policy

Every data point above traces through this exact pipeline:

```
1. SOURCE        — …
                   (curated by corpus-discovery agent, verified at download)
2. DOWNLOAD      — rag/download_corpus.py + rag/download_retry.py
                   PDF magic-byte check + size > 50 KB enforced
3. PARSE         — pdfplumber → per-page text (rag/ingest.py:read_pdf_pages)
4. CHUNK         — 800 tok / 120 overlap, sentence-aware (rag/ingest.py:chunk_pages)
5. EMBED         — BGE-small-en-v1.5 → 384-dim vector (backend/providers/local_embeddings.py)
6. INDEX         — Chroma persistent client (rag/vectors/) with metadata
7. EXTRACT       — Sarvam-M (DeepSeek-V3 fallback) prompt with HealthPolicy schema
                   → rag/extracted/bajaj-allianz__comprehensive-care-plan__wordings.json (this file's source data)
8. STORE         — DuckDB upsert into rag/policies.duckdb
9. SCORE         — backend/scorecard.py rules-based, no LLM-in-the-loop
10. KB SHEET     — rag/build_kb.py renders this markdown
```

**Re-running the audit trail:** delete `rag/extracted/{pid}.json` → run `python -m rag.extract --policy {pid}` → run `python -m rag.build_kb` → diff this file.

## What the bot will and won't say about this policy

Per the 4-gate faithfulness verifier (`backend/faithfulness.py`):
- Bot answers questions about this policy **only when retrieval scores for its chunks are ≥ 0.30 cosine** (BGE-small).
- Every factual claim cites this PDF with page numbers.
- If asked something whose answer is _null_ in the schema above (marked **[E?]**), the bot refuses — the data is not in the source PDF.
- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id=bajaj-allianz__comprehensive-care-plan__wordings`.
