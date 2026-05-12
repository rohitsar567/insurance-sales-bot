# Activ Assure

_Policy KB sheet — auto-generated from `rag/extracted/aditya-birla__activ-assure-diamond__wordings.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Insurer | [Aditya Birla Health Insurance Co. Limited](https://www.adityabirlacapital.com/healthinsurance) | curated · verified `eval/verified_urls.json` |
| Insurer slug | `aditya-birla` | derived from `data/corpus_urls.md` |
| Policy | **Activ Assure** | extracted from policy wordings |
| Policy id | `aditya-birla__activ-assure-diamond__wordings` | minted by us (`<insurer-slug>__<doc-slug>`) |
| Source PDF | […]() | downloaded + verified at ingest time |
| Extraction confidence | None% (self-rated by extractor) | computed |

## Scorecard — single A-F view

### **Grade: B** (72/100)
> Good policy with a few notable gaps.

**Data completeness:** 37.5% of the 24 scored fields have data.

| Sub-score | Bar | Score & Signals |
| --- | --- | --- |
| **Coverage Breadth** | `██████████████······` | **72/100** · Standard coverage |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;AYUSH covered<br/>&nbsp;&nbsp;&nbsp;organ donor expenses<br/>&nbsp;&nbsp;&nbsp;ambulance covered<br/>&nbsp;&nbsp;&nbsp;free health checkups |  |
| **Cost Predictability** | `███████████████·····` | **75/100** · Predictable costs |
| **Waiting-Period Friction** | `████████████████····` | **80/100** · Quick activation |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 24mo PED waiting |  |
| **Claim Experience** | `███████████████·····` | **75/100** · Smooth claims |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;cashless supported |  |
| **Renewal Protection** | `████████████········` | **60/100** · Adequate |
| **Bonus & Loyalty** | `███████████·········` | **58/100** · Standard sweeteners |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;free preventive checkup |  |

_Methodology: [`docs/scorecard-methodology.md`](../../docs/scorecard-methodology.md) · 24 of 48 schema fields drive this grade._

## All extracted data points — by group

**Derivation legend:**
- **[E]** Extracted directly from policy PDF by LLM
- **[E?]** Field was in schema but extraction returned null (data missing or unclear in source)
- **[C]** Computed from extracted fields (e.g. scorecard sub-score)
- **[I]** Implied / canonicalised by us
- **[V]** Verified externally (HEAD-check, URL probe)

### Identity  _4/6 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `policy_id` | `aditya-birla__activ-assure-diamond__wordings` | [I] |
| `insurer_slug` | `aditya-birla` | [I] |
| `insurer_name` | `Aditya Birla Health Insurance Co. Limited` | [I] |
| `policy_name` | `Activ Assure` | [I] |
| `policy_type` | _null (not in document)_ | [E?] |
| `uin_code` | _null (not in document)_ | [E?] |

### Eligibility  _0/1 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `residency_requirement` | _null (not in document)_ | [E?] |

### Sum insured & premium  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `premium_payment_modes` | _null (not in document)_ | [E?] |
| `grace_period_days` | _null (not in document)_ | [E?] |

### Waiting periods  _1/5 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `initial_waiting_period_days` | _null (not in document)_ | [E?] |
| `pre_existing_disease_waiting_months` | `24` | [E] |
| `specific_disease_waiting_months` | _null (not in document)_ | [E?] |
| `maternity_waiting_months` | _null (not in document)_ | [E?] |
| `specific_diseases_listed` | _null (not in document)_ | [E?] |

### Coverage scope  _6/12 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `pre_hospitalization_days` | _null (not in document)_ | [E?] |
| `post_hospitalization_days` | _null (not in document)_ | [E?] |
| `domiciliary_treatment` | Yes, "up to the limits as specified in the Policy Schedule / Product Benefit Table of this Policy", (Must continue for at least 3 consecutive days; certain conditions excluded.) | [E] |
| `ayush_coverage` | Yes, "up to the limits as specified in the Policy Schedule / Product Benefit Table of this Policy", (Treatment must be in recognized AYUSH hospitals; pre and post-hospitalization expenses not covered.) | [E] |
| `maternity_coverage` | _null (not in document)_ | [E?] |
| `newborn_coverage` | _null (not in document)_ | [E?] |
| `organ_donor_expenses` | Yes, "up to the limits as specified in the Policy Schedule / Product Benefit Table of this Policy", (Only covers harvesting expenses; excludes pre/post-hospitalization, screening, and other donor-related expenses.) | [E] |
| `ambulance_cover` | Yes, "up to the limits as specified in the Policy Schedule / Product Benefit Table of this Policy", (Covers transportation to nearest Hospital; excludes transportation from Hospital to residence.) | [E] |
| `critical_illness_cover` | _null (not in document)_ | [E?] |
| `restoration_benefit` | Yes, "Reload of Sum Insured up to the limits as specified in the Policy Schedule / Product Benefit Table of this Policy", (Available once per Policy Year; unlimited reload option available as an optional cover.) | [E] |
| `no_claim_bonus_pct` | _null (not in document)_ | [E?] |
| `preventive_health_checkup` | Yes, "once in a Policy Year", (Tests vary based on Sum Insured and age of the insured person.) | [E] |

### Sub-limits & caps  _1/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `room_rent_capping` | `Single Private A/C Room (upgradable to next level, only if Single Private A/C Room is not available)` | [E] |
| `icu_capping` | _null (not in document)_ | [E?] |
| `copayment_pct` | _null (not in document)_ | [E?] |
| `disease_wise_sub_limits` | _null (not in document)_ | [E?] |

### Geography & network  _2/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `worldwide_emergency_cover` | Yes, "Emergency medical assistance outside India when travelling 150 km or more away from residential address for less than 90 days", (Excludes travel for medical treatment, injuries from war, unlawful acts, etc.) | [E] |
| `network_hospital_count` | _null (not in document)_ | [E?] |
| `cashless_treatment_supported` | Yes | [E] |

### Exclusions  _1/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `permanent_exclusions` | `Asthma, bronchitis, tonsillitis and upper respiratory tract infection including laryngitis and pharyngitis, cough and cold, influenza`, `Arthritis, gout and rheumatism`, `Chronic nephritis and nephritic syndrome`, `Diarrhea and all type of dysenteries, including gastroenteritis`, `Diabetes mellitus and insipidus`, `Epilepsy`, `Hypertension`, `Psychiatric or psychosomatic disorders of all kinds` | [E] |
| `temporary_exclusions` | _null (not in document)_ | [E?] |
| `notable_exclusions_summary` | _null (not in document)_ | [E?] |

### Claim & service  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `claim_process_summary` | _null (not in document)_ | [E?] |
| `tat_cashless_authorization_hours` | _null (not in document)_ | [E?] |

### Riders / optional  _1/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `available_riders` | `Reduction in PED Waiting Period`, `Unlimited Reload of Sum Insured`, `Super NCB`, `Accidental Hospitalization Booster`, `Cancer Hospitalization Booster` | [E] |
| `top_rider_examples` | _null (not in document)_ | [E?] |

### Source metadata  _0/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `source_pdf_path` | _null (not in document)_ | [V] |
| `source_pdf_url` | _null (not in document)_ | [V] |
| `last_updated_date` | _null (not in document)_ | [V] |
| `extraction_confidence_pct` | _null (not in document)_ | [E?] |

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
                   → rag/extracted/aditya-birla__activ-assure-diamond__wordings.json (this file's source data)
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
- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id=aditya-birla__activ-assure-diamond__wordings`.
