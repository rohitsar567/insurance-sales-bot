# Care Advantage

_Policy KB sheet — auto-generated from `rag/extracted/care-health__care-advantage-add-ons-protect-plus-care-shield__brochure.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Insurer | [Care Health Insurance Limited](https://www.careinsurance.com/) | curated · verified `eval/verified_urls.json` |
| Insurer slug | `care-health` | derived from `data/corpus_urls.md` |
| Policy | **Care Advantage** | extracted from policy wordings |
| Policy id | `care-health__care-advantage-add-ons-protect-plus-care-shield__brochure` | minted by us (`<insurer-slug>__<doc-slug>`) |
| Source PDF | […]() | downloaded + verified at ingest time |
| Extraction confidence | 85.0% (self-rated by extractor) | computed |

## Scorecard — single A-F view

### **Grade: C** (66/100)
> Decent baseline; check the trade-offs before signing.

**Data completeness:** 54.2% of the 24 scored fields have data.

| Sub-score | Bar | Score & Signals |
| --- | --- | --- |
| **Coverage Breadth** | `█████████████·······` | **68/100** · Standard coverage |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;AYUSH covered<br/>&nbsp;&nbsp;&nbsp;organ donor expenses<br/>&nbsp;&nbsp;&nbsp;ambulance covered<br/>&nbsp;&nbsp;&nbsp;free health checkups |  |
| **Cost Predictability** | `███████████·········` | **57/100** · Some out-of-pocket |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 20% copayment |  |
| **Waiting-Period Friction** | `██████████████······` | **70/100** · Standard waits |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 36mo PED waiting |  |
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

### Identity  _5/6 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `policy_id` | `care-health__care-advantage-add-ons-protect-plus-care-shield__brochure` | [I] |
| `insurer_slug` | `care-health` | [I] |
| `insurer_name` | `Care Health Insurance Limited` | [I] |
| `policy_name` | `Care Advantage` | [I] |
| `policy_type` | _null (not in document)_ | [E?] |
| `uin_code` | `CHIHLIP26049V042526` | [E] |

### Eligibility  _1/1 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `residency_requirement` | `Indian resident only` | [E] |

### Sum insured & premium  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `premium_payment_modes` | _null (not in document)_ | [E?] |
| `grace_period_days` | _null (not in document)_ | [E?] |

### Waiting periods  _3/5 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `initial_waiting_period_days` | `30` | [E] |
| `pre_existing_disease_waiting_months` | `36` | [E] |
| `specific_disease_waiting_months` | `24` | [E] |
| `maternity_waiting_months` | _null (not in document)_ | [E?] |
| `specific_diseases_listed` | _null (not in document)_ | [E?] |

### Coverage scope  _8/12 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `pre_hospitalization_days` | `30` | [E] |
| `post_hospitalization_days` | `60` | [E] |
| `domiciliary_treatment` | _null (not in document)_ | [E?] |
| `ayush_coverage` | Yes, "Up to SI" | [E] |
| `maternity_coverage` | _null (not in document)_ | [E?] |
| `newborn_coverage` | _null (not in document)_ | [E?] |
| `organ_donor_expenses` | Yes, "Up to SI" | [E] |
| `ambulance_cover` | Yes, "Up to SI" | [E] |
| `critical_illness_cover` | _null (not in document)_ | [E?] |
| `restoration_benefit` | Yes, "Up to SI and available for unrelated or same illness" | [E] |
| `no_claim_bonus_pct` | `10.0` | [E] |
| `preventive_health_checkup` | Yes, "Annual" | [E] |

### Sub-limits & caps  _3/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `room_rent_capping` | `No sub-limit` | [E] |
| `icu_capping` | `No sub-limit` | [E] |
| `copayment_pct` | `20.0` | [E] |
| `disease_wise_sub_limits` | _null (not in document)_ | [E?] |

### Geography & network  _2/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `worldwide_emergency_cover` | Yes, "Up to SI", (With Protect Plus add-on policy against payment of additional premium) | [E] |
| `network_hospital_count` | _null (not in document)_ | [E?] |
| `cashless_treatment_supported` | Yes | [E] |

### Exclusions  _0/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `permanent_exclusions` | _null (not in document)_ | [E?] |
| `temporary_exclusions` | _null (not in document)_ | [E?] |
| `notable_exclusions_summary` | _null (not in document)_ | [E?] |

### Claim & service  _0/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `claim_process_summary` | _null (not in document)_ | [E?] |
| `tat_cashless_authorization_hours` | _null (not in document)_ | [E?] |

### Riders / optional  _2/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `available_riders` | `Protect Plus`, `Care OPD`, `Care Advanced` | [E] |
| `top_rider_examples` | `Protect Plus`, `Care OPD` | [E] |

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
                   → rag/extracted/care-health__care-advantage-add-ons-protect-plus-care-shield__brochure.json (this file's source data)
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
- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id=care-health__care-advantage-add-ons-protect-plus-care-shield__brochure`.
