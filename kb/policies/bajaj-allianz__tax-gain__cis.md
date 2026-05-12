# Tax Gain

_Policy KB sheet — auto-generated from `rag/extracted/bajaj-allianz__tax-gain__cis.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Insurer | [Bajaj Allianz General Insurance Co. Ltd.](https://www.bajajallianz.com/) | curated · verified `eval/verified_urls.json` |
| Insurer slug | `bajaj-allianz` | derived from `data/corpus_urls.md` |
| Policy | **Tax Gain** | extracted from policy wordings |
| Policy id | `bajaj-allianz__tax-gain__cis` | minted by us (`<insurer-slug>__<doc-slug>`) |
| Source PDF | […]() | downloaded + verified at ingest time |
| Extraction confidence | None% (self-rated by extractor) | computed |

## Scorecard — single A-F view

### **Grade: C** (68/100)
> Decent baseline; check the trade-offs before signing.

**Data completeness:** 25.0% of the 24 scored fields have data.

| Sub-score | Bar | Score & Signals |
| --- | --- | --- |
| **Coverage Breadth** | `███████████·········` | **56/100** · Standard coverage |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;ambulance covered<br/>&nbsp;&nbsp;&nbsp;free health checkups |  |
| **Cost Predictability** | `███████████████·····` | **75/100** · Predictable costs |
| **Waiting-Period Friction** | `██████████████······` | **70/100** · Standard waits |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 36mo PED waiting |  |
| **Claim Experience** | `███████████████·····` | **79/100** · Smooth claims |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;cashless supported<br/>&nbsp;&nbsp;&nbsp;2h cashless TAT |  |
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

### Identity  _5/6 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `policy_id` | `bajaj-allianz__tax-gain__cis` | [I] |
| `insurer_slug` | `bajaj-allianz` | [I] |
| `insurer_name` | `Bajaj Allianz General Insurance Co. Ltd.` | [I] |
| `policy_name` | `Tax Gain` | [I] |
| `policy_type` | _null (not in document)_ | [E?] |
| `uin_code` | `BAJHLIP21184V022021` | [E] |

### Eligibility  _0/1 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `residency_requirement` | _null (not in document)_ | [E?] |

### Sum insured & premium  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `premium_payment_modes` | _null (not in document)_ | [E?] |
| `grace_period_days` | _null (not in document)_ | [E?] |

### Waiting periods  _4/5 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `initial_waiting_period_days` | `30` | [E] |
| `pre_existing_disease_waiting_months` | `36` | [E] |
| `specific_disease_waiting_months` | `24` | [E] |
| `maternity_waiting_months` | _null (not in document)_ | [E?] |
| `specific_diseases_listed` | `gastric or duodenal ulcers`, `benign prostatic hypertrophy`, `all types of sinuses`, `hemorrhoids`, `dysfunctional uterine bleeding`, `endometriosis`, `stones in the urinary and biliary systems`, `surgery on ears/tonsils/adenoids/paranasal sinuses` | [E] |

### Coverage scope  _2/12 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `pre_hospitalization_days` | _null (not in document)_ | [E?] |
| `post_hospitalization_days` | _null (not in document)_ | [E?] |
| `domiciliary_treatment` | _null (not in document)_ | [E?] |
| `ayush_coverage` | _null (not in document)_ | [E?] |
| `maternity_coverage` | _null (not in document)_ | [E?] |
| `newborn_coverage` | _null (not in document)_ | [E?] |
| `organ_donor_expenses` | _null (not in document)_ | [E?] |
| `ambulance_cover` | Yes, limit ₹1,000, "Max up to Rs 1000 per valid hospitalization claim." | [E] |
| `critical_illness_cover` | _null (not in document)_ | [E?] |
| `restoration_benefit` | _null (not in document)_ | [E?] |
| `no_claim_bonus_pct` | _null (not in document)_ | [E?] |
| `preventive_health_checkup` | Yes, "Preventive Health check up at the end of every 4 continuous policy years as per limits specified in policy wordings." | [E] |

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

### Exclusions  _1/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `permanent_exclusions` | `Any hospital admission primarily for investigation diagnostic purpose`, `Expenses related to any admission primarily for enforced bed rest and not for receiving treatment`, `Obesity/Weight Control`, `Change-of-gender treatments`, `Expenses for cosmetic or plastic surgery or any treatment to change appearance unless for reconstruction following an Accident, Burn(s) etc.`, `Expenses for treatment arising from Insured committing or attempting to commit a breach of law with criminal intent`, `Treatment for Alcoholism, drug or substance abuse`, `Treatments received in health hydros, nature cure clinics, etc. where admission is arranged wholly or partly for domestic reasons` | [E] |
| `temporary_exclusions` | _null (not in document)_ | [E?] |
| `notable_exclusions_summary` | _null (not in document)_ | [E?] |

### Claim & service  _2/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `claim_process_summary` | `Cashless treatment is only available at Network Hospitals. You or Your representative must intimate Us 48 hours before the planned Hospitalization and within 24 hours of emergency hospitalization and request pre-authorization by way of the written form. We will review each claim for Medical Expenses, coverage and accordingly issue an authorisation letter either to You or the Network Hospital. For reimbursement claims where treatment is taken at a Non network hospital or if cashless claim is denied, you must intimate Us 48 hours before the planned Hospitalization and within 48 hours of emergency hospitalization. You must submit all necessary documents within 30 days of discharge from a Hospital. The Company shall settle or reject the claim within 45 days from the date of receipt of last necessary document.` | [E] |
| `tat_cashless_authorization_hours` | `2.0` | [E] |

### Riders / optional  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `available_riders` | _null (not in document)_ | [E?] |
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
                   → rag/extracted/bajaj-allianz__tax-gain__cis.json (this file's source data)
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
- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id=bajaj-allianz__tax-gain__cis`.
