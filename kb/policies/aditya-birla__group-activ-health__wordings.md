# Group Activ Health

_Policy KB sheet — auto-generated from `rag/extracted/aditya-birla__group-activ-health__wordings.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Insurer | [Aditya Birla Health Insurance Co. Ltd.](https://www.adityabirlacapital.com/healthinsurance) | curated · verified `eval/verified_urls.json` |
| Insurer slug | `aditya-birla` | derived from `data/corpus_urls.md` |
| Policy | **Group Activ Health** | extracted from policy wordings |
| Policy id | `aditya-birla__group-activ-health__wordings` | minted by us (`<insurer-slug>__<doc-slug>`) |
| Source PDF | [https://www.adityabirlacapital.com/healthinsurance/downloads…](https://www.adityabirlacapital.com/healthinsurance/downloads) | downloaded + verified at ingest time |
| Extraction confidence | 85.0% (self-rated by extractor) | computed |

## Scorecard — single A-F view

### **Grade: B** (70/100)
> Good policy with a few notable gaps.

**Data completeness:** 25.0% of the 24 scored fields have data.

| Sub-score | Bar | Score & Signals |
| --- | --- | --- |
| **Coverage Breadth** | `██████████████······` | **72/100** · Standard coverage |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;AYUSH covered<br/>&nbsp;&nbsp;&nbsp;maternity covered<br/>&nbsp;&nbsp;&nbsp;newborn covered |  |
| **Cost Predictability** | `███████████████·····` | **75/100** · Predictable costs |
| **Waiting-Period Friction** | `██████████████······` | **70/100** · Standard waits |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 36mo PED waiting |  |
| **Claim Experience** | `███████████████·····` | **75/100** · Smooth claims |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;cashless supported |  |
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

### Identity  _6/6 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `policy_id` | `aditya-birla__group-activ-health__wordings` | [I] |
| `insurer_slug` | `aditya-birla` | [I] |
| `insurer_name` | `Aditya Birla Health Insurance Co. Ltd.` | [I] |
| `policy_name` | `Group Activ Health` | [I] |
| `policy_type` | `group` | [E] |
| `uin_code` | `ADIHLGP26041V052526` | [E] |

### Eligibility  _0/1 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `residency_requirement` | _null (not in document)_ | [E?] |

### Sum insured & premium  _2/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `premium_payment_modes` | `monthly`, `annual` | [E] |
| `grace_period_days` | `15` | [E] |

### Waiting periods  _2/5 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `initial_waiting_period_days` | _null (not in document)_ | [E?] |
| `pre_existing_disease_waiting_months` | `36` | [E] |
| `specific_disease_waiting_months` | `36` | [E] |
| `maternity_waiting_months` | _null (not in document)_ | [E?] |
| `specific_diseases_listed` | _null (not in document)_ | [E?] |

### Coverage scope  _5/12 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `pre_hospitalization_days` | _null (not in document)_ | [E?] |
| `post_hospitalization_days` | _null (not in document)_ | [E?] |
| `domiciliary_treatment` | Yes, "Medical treatment for an illness/disease/injury which in the normal course would require care and treatment at a Hospital but is actually taken while confined at home" | [E] |
| `ayush_coverage` | Yes, "Medical Expenses for medically required AYUSH Treatments undergone as an In-patient Treatment or Day Care Treatment", (Comfort treatment involving steam bath/sauna/oil massages are excluded.) | [E] |
| `maternity_coverage` | Yes, "Medical treatment expenses traceable to childbirth (including complicated deliveries and caesarean sections incurred during hospitalization); expenses towards lawful medical termination of pregnancy during the policy period." | [E] |
| `newborn_coverage` | Yes, "Baby born during the Policy Period and is Aged upto 90 days" | [E] |
| `organ_donor_expenses` | _null (not in document)_ | [E?] |
| `ambulance_cover` | _null (not in document)_ | [E?] |
| `critical_illness_cover` | Yes, "Cover for specified critical illnesses like Cancer, Myocardial Infarction, Stroke, etc.", (Detailed definitions of each critical illness are provided in the policy document.) | [E] |
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

### Geography & network  _1/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `worldwide_emergency_cover` | _null (not in document)_ | [E?] |
| `network_hospital_count` | _null (not in document)_ | [E?] |
| `cashless_treatment_supported` | Yes | [E] |

### Exclusions  _2/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `permanent_exclusions` | `Cosmetic surgery`, `Self-inflicted injury`, `War`, `Non-medical expenses`, `Experimental treatments` | [E] |
| `temporary_exclusions` | _null (not in document)_ | [E?] |
| `notable_exclusions_summary` | `Exclusions include cosmetic surgery, self-inflicted injury, war, non-medical expenses, and experimental treatments. Pre-existing diseases are covered after 36 months.` | [E] |

### Claim & service  _1/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `claim_process_summary` | `Claims must be made in accordance with the procedure set out in the policy document. Cashless facility is available at Network Providers.` | [E] |
| `tat_cashless_authorization_hours` | _null (not in document)_ | [E?] |

### Riders / optional  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `available_riders` | _null (not in document)_ | [E?] |
| `top_rider_examples` | _null (not in document)_ | [E?] |

### Source metadata  _2/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `source_pdf_path` | _null (not in document)_ | [V] |
| `source_pdf_url` | `https://www.adityabirlacapital.com/healthinsurance/downloads` | [V] |
| `last_updated_date` | _null (not in document)_ | [V] |
| `extraction_confidence_pct` | `85.0` | [E] |

## Lineage — end-to-end audit trail for this policy

Every data point above traces through this exact pipeline:

```
1. SOURCE        — https://www.adityabirlacapital.com/healthinsurance/downloads…
                   (curated by corpus-discovery agent, verified at download)
2. DOWNLOAD      — rag/download_corpus.py + rag/download_retry.py
                   PDF magic-byte check + size > 50 KB enforced
3. PARSE         — pdfplumber → per-page text (rag/ingest.py:read_pdf_pages)
4. CHUNK         — 800 tok / 120 overlap, sentence-aware (rag/ingest.py:chunk_pages)
5. EMBED         — BGE-small-en-v1.5 → 384-dim vector (backend/providers/local_embeddings.py)
6. INDEX         — Chroma persistent client (rag/vectors/) with metadata
7. EXTRACT       — Sarvam-M (DeepSeek-V3 fallback) prompt with HealthPolicy schema
                   → rag/extracted/aditya-birla__group-activ-health__wordings.json (this file's source data)
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
- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id=aditya-birla__group-activ-health__wordings`.
