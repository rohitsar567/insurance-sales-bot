# Care for Senior Citizens

_Policy KB sheet — auto-generated from `rag/extracted/care-health__care-senior__brochure.json` + `backend/scorecard.py`. Do not hand-edit; regenerate via `python -m rag.build_kb`._

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Insurer | [Care Health Insurance Limited](https://www.careinsurance.com/) | curated · verified `eval/verified_urls.json` |
| Insurer slug | `care-health` | derived from `data/corpus_urls.md` |
| Policy | **Care for Senior Citizens** | extracted from policy wordings |
| Policy id | `care-health__care-senior__brochure` | minted by us (`<insurer-slug>__<doc-slug>`) |
| Source PDF | […]() | downloaded + verified at ingest time |
| Extraction confidence | 90.0% (self-rated by extractor) | computed |

## Scorecard — single A-F view

### **Grade: C** (62/100)
> Decent baseline; check the trade-offs before signing.

**Data completeness:** 54.2% of the 24 scored fields have data.

| Sub-score | Bar | Score & Signals |
| --- | --- | --- |
| **Coverage Breadth** | `████████████········` | **64/100** · Standard coverage |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;organ donor expenses<br/>&nbsp;&nbsp;&nbsp;ambulance covered<br/>&nbsp;&nbsp;&nbsp;free health checkups |  |
| **Cost Predictability** | `█████████···········` | **49/100** · Material out-of-pocket |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 20% copayment<br/>&nbsp;&nbsp;&nbsp;− room rent capped: 1% SI per day (Max. up to 1% of SI per day) for 3  |  |
| **Waiting-Period Friction** | `████████████········` | **60/100** · Standard waits |
|  | _signals:_<br/>&nbsp;&nbsp;&nbsp;− 48mo PED waiting (long) |  |
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
| `policy_id` | `care-health__care-senior__brochure` | [I] |
| `insurer_slug` | `care-health` | [I] |
| `insurer_name` | `Care Health Insurance Limited` | [I] |
| `policy_name` | `Care for Senior Citizens` | [I] |
| `policy_type` | _null (not in document)_ | [E?] |
| `uin_code` | `RHIHLIP21017V052021` | [E] |

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
| `pre_existing_disease_waiting_months` | `48` | [E] |
| `specific_disease_waiting_months` | `24` | [E] |
| `maternity_waiting_months` | _null (not in document)_ | [E?] |
| `specific_diseases_listed` | `Cataract`, `Total Knee Replacement`, `Hernia`, `Hysterectomy`, `Benign Prostate Hypertrophy (BPH)`, `Stones of renal system`, `Cerebrovascular and Cardiovasular disorders`, `Cancer` | [E] |

### Coverage scope  _8/12 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `pre_hospitalization_days` | `30` | [E] |
| `post_hospitalization_days` | `60` | [E] |
| `domiciliary_treatment` | Yes, "Up to 10% of SI, covered after 3 days" | [E] |
| `ayush_coverage` | _null (not in document)_ | [E?] |
| `maternity_coverage` | _null (not in document)_ | [E?] |
| `newborn_coverage` | _null (not in document)_ | [E?] |
| `organ_donor_expenses` | Yes, limit ₹50,000, "Up to ₹50,000 for 3 Lacs plan, Up to ₹1,00,000 for 5,7,10 Lacs plan" | [E] |
| `ambulance_cover` | Yes, limit ₹1,500, "Up to ₹1,500 per hospitalization for 3 Lacs plan, Up to ₹2,000 per hospitalization for 5,7,10 Lacs plan" | [E] |
| `critical_illness_cover` | _null (not in document)_ | [E?] |
| `restoration_benefit` | Yes, "Yes to SI (Once in a Policy Year)" | [E] |
| `no_claim_bonus_pct` | `10.0` | [E] |
| `preventive_health_checkup` | Yes, "Yes, all members" | [E] |

### Sub-limits & caps  _4/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `room_rent_capping` | `1% SI per day (Max. up to 1% of SI per day) for 3 Lacs plan, Single Private AC Room (Max. up to 1% of SI per day) for 5,7,10 Lacs plan` | [E] |
| `icu_capping` | `2% SI per day` | [E] |
| `copayment_pct` | `20.0` | [E] |
| `disease_wise_sub_limits` | `{'Cataract': '₹20,000 per eye for 3 Lacs plan, ₹30,000 per eye for 5,7,10 Lacs plan', 'Total Knee Replacement': '₹80,000 per knee for 3 Lacs plan, ₹1,00,000 per knee for 5,7,10 Lacs plan', 'Hernia': '₹50,000 for 3 Lacs plan, ₹65,000 for 5,7,10 Lacs plan', 'Hysterectomy': '₹50,000 for 3 Lacs plan, ₹65,000 for 5,7,10 Lacs plan', 'Benign Prostate Hypertrophy (BPH)': '₹50,000 for 3 Lacs plan, ₹65,000 for 5,7,10 Lacs plan', 'Stones of renal system': '₹50,000 for 3 Lacs plan, ₹65,000 for 5,7,10 Lacs plan', 'Cerebrovascular and Cardiovasular disorders': '₹2,00,000 for 3 Lacs plan, ₹2,50,000 for 5,7,10 Lacs plan', 'Cancer': '₹2,00,000 for 3 Lacs plan, ₹2,50,000 for 5,7,10 Lacs plan', 'Other renal complications and Disorders': '₹2,00,000 for 3 Lacs plan, ₹2,50,000 for 5,7,10 Lacs plan', 'Breakage of bones': '₹2,00,000 for 3 Lacs plan, ₹2,50,000 for 5,7,10 Lacs plan'}` | [E] |

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

### Source metadata  _1/4 fields populated_

| Field | Value | Type |
| --- | --- | --- |
| `source_pdf_path` | _null (not in document)_ | [V] |
| `source_pdf_url` | _null (not in document)_ | [V] |
| `last_updated_date` | _null (not in document)_ | [V] |
| `extraction_confidence_pct` | `90.0` | [E] |

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
                   → rag/extracted/care-health__care-senior__brochure.json (this file's source data)
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
- Blocked replies on this policy are logged to `logs/hallucinations.jsonl` with `policy_id=care-health__care-senior__brochure`.
