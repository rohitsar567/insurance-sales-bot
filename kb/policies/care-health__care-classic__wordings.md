# Care Classic

_Policy KB sheet — auto-generated from `rag/extracted/care-health__care-classic__wordings.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Insurer | [Care Health Insurance Limited](https://www.careinsurance.com/) | curated · verified `eval/verified_urls.json` |
| Insurer slug | `care-health` | derived from `data/corpus_urls.md` |
| Policy | **Care Classic** | extracted from policy wordings |
| Policy id | `care-health__care-classic__wordings` | minted by us (`<insurer-slug>__<doc-slug>`) |
| Source PDF | […]() | downloaded + verified at ingest time |
| Extraction confidence | None% (self-rated by extractor) | computed |

## Scorecard — single A-F view

### **Grade: C** (68/100)
> Decent baseline; check the trade-offs before signing.

**Data completeness:** 62.5% of the 24 scored fields have data.

| Sub-score | Bar | Score & Signals |
| --- | --- | --- |
| **Coverage Breadth** | `█████████████████···` | **87/100** · Wide coverage |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;AYUSH covered<br/>&nbsp;&nbsp;&nbsp;maternity covered<br/>&nbsp;&nbsp;&nbsp;newborn covered<br/>&nbsp;&nbsp;&nbsp;organ donor expenses<br/>&nbsp;&nbsp;&nbsp;ambulance covered<br/>&nbsp;&nbsp;&nbsp;60d pre-hospitalization<br/>&nbsp;&nbsp;&nbsp;90d post-hospitalization |  |
| **Cost Predictability** | `█████████···········` | **49/100** · Material out-of-pocket |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 20% copayment<br/>&nbsp;&nbsp;&nbsp;− room rent capped: 1% of SI per day or Single Private AC Room |  |
| **Waiting-Period Friction** | `█████████████·······` | **66/100** · Standard waits |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 36mo PED waiting |  |
| **Claim Experience** | `███████████████·····` | **75/100** · Smooth claims |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;cashless supported |  |
| **Renewal Protection** | `████████████········` | **60/100** · Adequate |
| **Bonus & Loyalty** | `███████████·········` | **58/100** · Standard sweeteners |

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
| `policy_id` | `care-health__care-classic__wordings` | [I] |
| `insurer_slug` | `care-health` | [I] |
| `insurer_name` | `Care Health Insurance Limited` | [I] |
| `policy_name` | `Care Classic` | [I] |
| `policy_type` | _null (not in document)_ | [E?] |
| `uin_code` | `CHIHLIP22071V012122` | [E] |

### Eligibility  _0/1 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `residency_requirement` | _null (not in document)_ | [E?] |

### Sum insured & premium  _1/2 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `premium_payment_modes` | _null (not in document)_ | [E?] |
| `grace_period_days` | `30` | [E] |

### Waiting periods  _2/5 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `initial_waiting_period_days` | _null (not in document)_ | [E?] |
| `pre_existing_disease_waiting_months` | `36` | [E] |
| `specific_disease_waiting_months` | _null (not in document)_ | [E?] |
| `maternity_waiting_months` | `36` | [E] |
| `specific_diseases_listed` | _null (not in document)_ | [E?] |

### Coverage scope  _10/12 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `pre_hospitalization_days` | `60` | [E] |
| `post_hospitalization_days` | `90` | [E] |
| `domiciliary_treatment` | Yes, "Amount specified against this Benefit in the Policy Schedule", (Domiciliary Hospitalization must continue for a period exceeding 3 consecutive days.) | [E] |
| `ayush_coverage` | Yes, "Sum Insured as specified in the Policy Schedule", (Treatment must be rendered from a registered Medical Practitioner.) | [E] |
| `maternity_coverage` | Yes, "Amount specified against this Benefit in the Policy Schedule", (Waiting period of 36 months from the date of first inception of the policy.) | [E] |
| `newborn_coverage` | Yes, "Baby born during the Policy Period and is aged up to 90 days" | [E] |
| `organ_donor_expenses` | Yes, "Limit specified against this Benefit in the Policy Schedule", (Organ donor must be an eligible donor in accordance with The Transplantation of Human Organs Act, 1994.) | [E] |
| `ambulance_cover` | Yes, "Amount specified against this Benefit in the Policy Schedule", (Ambulance transportation must be offered by a Hospital or by an Ambulance service provider.) | [E] |
| `critical_illness_cover` | _null (not in document)_ | [E?] |
| `restoration_benefit` | Yes, "Unlimited Automatic Recharge", (Recharge shall be utilized only after the base Sum Insured, 'No Claims Bonus' and 'Additional Sum Insured for Accidental Hospitalization' has been completely exhausted.) | [E] |
| `no_claim_bonus_pct` | `25.0` | [E] |
| `preventive_health_checkup` | _null (not in document)_ | [E?] |

### Sub-limits & caps  _3/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `room_rent_capping` | `1% of SI per day or Single Private AC Room` | [E] |
| `icu_capping` | `2% of SI per day or no limit` | [E] |
| `copayment_pct` | `20.0` | [E] |
| `disease_wise_sub_limits` | _null (not in document)_ | [E?] |

### Geography & network  _1/3 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `worldwide_emergency_cover` | _null (not in document)_ | [E?] |
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
                   → rag/extracted/care-health__care-classic__wordings.json (this file's source data)
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
- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id=care-health__care-classic__wordings`.
