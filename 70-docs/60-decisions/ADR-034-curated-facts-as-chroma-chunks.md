# ADR-034 — Curated facts as Chroma chunks (`doc_type='curated'`)

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Related KIs:** KI-137

## Context

21 policies in the corpus had **no extracted JSON** (the standard structured-extraction pipeline failed on the source PDF — typically image-only filings, OCR failures, or policy wordings the LLM extractor couldn't slot-fill above the confidence floor) but DID have **hand-curated YAML fact sheets** in `40-data/curated/` written during the early corpus build.

These curated facts were rendered into the marketplace via a separate read path, so they showed up in `/api/policies/all` cards. But the chat brain could not retrieve them. When a user asked a question that should have hit one of these 21 policies, the retrieval layer returned nothing (Chroma had no chunks for those products) and the brain either declined to answer or — worse — answered from a near-miss policy whose embeddings happened to be close.

The asymmetry was: marketplace surfaces could see curated data, but chat / advisor surfaces could not. This violated the "every fact in the product is retrievable and citable" invariant.

## Decision

**Render curated YAML → text → embed → ingest as Chroma chunks** with metadata `doc_type='curated'`.

- A new ingestion entry `tools/ingest_curated.py` walks `40-data/curated/*.yaml`, renders each fact sheet into one structured text blob per policy (sections: identity, premium, benefits, sub-limits, exclusions, renewal terms), and writes to the main Chroma collection.
- Chunks carry the same metadata schema as PDF-extracted chunks (`insurer_slug`, `product_slug`, `uin`, `source_uri`) plus `doc_type='curated'` so retrieval can boost / filter.
- Retrieval treats `doc_type='curated'` as equally citable as `doc_type='brochure'` / `doc_type='wording'` — the bot cites the curated source URL written in the YAML front matter.

## Consequences

| Win | Cost |
|---|---|
| 21 previously-invisible policies are now retrievable + citable in chat. The marketplace ↔ chat asymmetry is closed | Curated YAML is hand-maintained; stale curation now affects retrieval, not just display. Mitigated by `last_verified_at` metadata on every chunk |
| The chat brain can answer "what's the room rent cap on X" for any policy in the corpus, including those that broke the extractor | Curated chunks lack the granular slot structure of extracted JSONs — retrieval relevance per chunk is slightly lower because chunks are coarser-grained |
| Establishes a general pattern: any structured data we hold about a product can be rendered → embedded → ingested without disrupting the rest of the pipeline | Two source-of-truth paths now exist for the same product (curated YAML vs extracted JSON); a future consolidation will reconcile them |

## Related

- KI-137 — the ingest run that emitted the 21 chunks
- ADR-003 (curated corpus) — original decision to hold curated YAML
- ADR-025 (single Chroma collection with metadata partitioning) — `doc_type` is the partitioning field
