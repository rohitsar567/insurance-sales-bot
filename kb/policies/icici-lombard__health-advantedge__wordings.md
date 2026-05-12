# Health AdvantEdge

_Policy KB sheet — auto-generated from `rag/extracted/icici-lombard__health-advantedge__wordings.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Insurer | [ICICI Lombard General Insurance Company Limited](https://www.icicilombard.com/) | curated · verified `eval/verified_urls.json` |
| Insurer slug | `icici-lombard` | derived from `data/corpus_urls.md` |
| Policy | **Health AdvantEdge** | extracted from policy wordings |
| Policy id | `icici-lombard__health-advantedge__wordings` | minted by us (`<insurer-slug>__<doc-slug>`) |
| Source PDF | […]() | downloaded + verified at ingest time |
| Extraction confidence | 85.0% (self-rated by extractor) | computed |

## Scorecard — single A-F view

### **Grade: B** (70/100)
> Good policy with a few notable gaps.

**Data completeness:** 41.7% of the 24 scored fields have data.

| Sub-score | Bar | Score & Signals |
| --- | --- | --- |
| **Coverage Breadth** | `███████████████·····` | **77/100** · Wide coverage |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;AYUSH covered<br/>&nbsp;&nbsp;&nbsp;organ donor expenses<br/>&nbsp;&nbsp;&nbsp;ambulance covered<br/>&nbsp;&nbsp;&nbsp;60d pre-hospitalization<br/>&nbsp;&nbsp;&nbsp;180d post-hospitalization |  |
| **Cost Predictability** | `████████████████····` | **81/100** · Predictable costs |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;no room rent cap |  |
| **Waiting-Period Friction** | `████████████········` | **60/100** · Standard waits |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 48mo PED waiting (long) |  |
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

### Identity  _5/6 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `policy_id` | `icici-lombard__health-advantedge__wordings` | [I] |
| `insurer_slug` | `icici-lombard` | [I] |
| `insurer_name` | `ICICI Lombard General Insurance Company Limited` | [I] |
| `policy_name` | `Health AdvantEdge` | [I] |
| `policy_type` | _null (not in document)_ | [E?] |
| `uin_code` | `ICIHLIP24182V042324` | [E] |

### Eligibility  _0/1 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `residency_requirement` | _null (not in document)_ | [E?] |

### Sum insured & premium  _1/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `premium_payment_modes` | _null (not in document)_ | [E?] |
| `grace_period_days` | `30` | [E] |

### Waiting periods  _3/5 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `initial_waiting_period_days` | _null (not in document)_ | [E?] |
| `pre_existing_disease_waiting_months` | `48` | [E] |
| `specific_disease_waiting_months` | `24` | [E] |
| `maternity_waiting_months` | _null (not in document)_ | [E?] |
| `specific_diseases_listed` | `bariatric surgery` | [E] |

### Coverage scope  _7/12 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `pre_hospitalization_days` | `60` | [E] |
| `post_hospitalization_days` | `180` | [E] |
| `domiciliary_treatment` | Yes, "Covered for at least 3 consecutive days", (Excludes certain conditions like asthma, bronchitis, tonsillitis, etc.) | [E] |
| `ayush_coverage` | Yes, "Covered up to Annual Sum Insured", (Treatment must be at a registered AYUSH Hospital or AYUSH Day Care Centre.) | [E] |
| `maternity_coverage` | _null (not in document)_ | [E?] |
| `newborn_coverage` | _null (not in document)_ | [E?] |
| `organ_donor_expenses` | Yes, "Covered up to Annual Sum Insured", (Organ donation must conform to the Transplantation of Human Organs Act 1994.) | [E] |
| `ambulance_cover` | Yes, limit ₹10,000, "1% of Annual Sum Insured maximum up to INR 10,000", (Road ambulance services only; air ambulance covered separately up to Annual Sum Insured.) | [E] |
| `critical_illness_cover` | _null (not in document)_ | [E?] |
| `restoration_benefit` | Yes, "Reset up to 100% of Annual Sum Insured for policies less than Rs. 10 Lakhs (once) and unlimited for Rs. 10 Lakhs and above", (Not available for the first claim made during the Policy Year.) | [E] |
| `no_claim_bonus_pct` | _null (not in document)_ | [E?] |
| `preventive_health_checkup` | _null (not in document)_ | [E?] |

### Sub-limits & caps  _2/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `room_rent_capping` | `1% of Annual Sum Insured for policies up to Rs. 4 Lakhs, no capping for Rs. 5 Lakhs and above` | [E] |
| `icu_capping` | `2% of Annual Sum Insured for policies up to Rs. 4 Lakhs, no capping for Rs. 5 Lakhs and above` | [E] |
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
| `permanent_exclusions` | _null (not in document)_ | [E?] |
| `temporary_exclusions` | `bariatric surgery` | [E] |
| `notable_exclusions_summary` | _null (not in document)_ | [E?] |

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
                   → rag/extracted/icici-lombard__health-advantedge__wordings.json (this file's source data)
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
- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id=icici-lombard__health-advantedge__wordings`.
