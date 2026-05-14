# Information Source Map

| Field | Value |
| --- | --- |
| Generated | 2026-05-12T21:18:43Z |
| Total chunks in vector store | 2 |
| Policies indexed | 1 |
| Topics auto-tagged | 16 |

## 0. Purpose

This document is the **authoritative catalog of what the bot can answer**. Every chunk in the Chroma vector store is summarized here, grouped by policy. For each policy, the high-value extracted fields are listed alongside.

A reviewer can use this file to answer two questions:

1. **"Could the bot know this?"** → look up the policy + topic.
2. **"Is the bot's answer plausibly grounded?"** → cross-reference the policy_id and field in the runtime audit log.

This artifact is regenerated after every ingestion or extraction run via `python -m rag.source_map`.

## 1. Topic inverted index — what is covered, where

- **claim_process** — covered in 1 policies: aditya-birla__activ-secure-cancer-secure__brochure
- **coverage_scope** — covered in 1 policies: aditya-birla__activ-secure-cancer-secure__brochure
- **critical_illness** — covered in 1 policies: aditya-birla__activ-secure-cancer-secure__brochure
- **exclusions** — covered in 1 policies: aditya-birla__activ-secure-cancer-secure__brochure
- **ncb** — covered in 1 policies: aditya-birla__activ-secure-cancer-secure__brochure
- **sum_insured** — covered in 1 policies: aditya-birla__activ-secure-cancer-secure__brochure
- **waiting_period** — covered in 1 policies: aditya-birla__activ-secure-cancer-secure__brochure

## 2. Per-policy catalog

### Activ Secure - Cancer Secure  
_aditya-birla · brochure · 2 chunks · pages 1-4_

**Topics covered:** coverage_scope(2), exclusions(2), claim_process(2), sum_insured(2), critical_illness(2), waiting_period(1), ncb(1)

**Extracted fields:**
  - (extraction not yet run for this policy)

`policy_id`: `aditya-birla__activ-secure-cancer-secure__brochure`


---

## 3. Machine-readable index

A JSON form of this catalog is at `rag/source_map.json` — used by the faithfulness verifier to look up whether a claim could plausibly trace to a chunk before allowing it through.

## 4. Coverage gaps (transparent)

These are areas where the corpus is thin. Bot questions on these should refuse:

- **Regulatory documents (IRDAI):** Deferred — see `decisions.md` D-017. The bot's faithfulness Gate 1 (retrieval floor) refuses these correctly.
- **Premium pricing:** Out of scope (advisor, not broker). See `decisions.md` D-007.
- **Categories beyond Health (Life, Motor, Travel):** Out of scope v1.
- **Star Health policies (11 PDFs):** Star Health's CDN actively blocks scripted downloads. Mitigation pending in v2.
