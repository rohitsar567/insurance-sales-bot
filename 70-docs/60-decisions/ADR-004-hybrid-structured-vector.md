# ADR-004: Hybrid structured + vector retrieval

**Status:** Locked
**Date:** 2026-05-13

## Context

A health-insurance assistant has two qualitatively different query classes:

1. **Filter / comparison / recommendation** — "Show me policies with restoration benefit AND PED waiting < 24 months AND room rent ≥ ₹10k." These need structured fields, not retrieval.
2. **Free-form Q&A with citations** — "What does Care Supreme say about cataract waiting?" These need unstructured text with clause-level citation.

A single retrieval mechanism is wrong for both.

## Decision

**Hybrid architecture:**

| Concern | Store | Used for |
|---|---|---|
| Structured fields per policy (48 fields) | **DuckDB** (`rag/policies.duckdb`) | Marketplace filters, scorecard inputs, side-by-side comparison |
| Free-form text chunks with provenance | **Chroma** (`rag/vectors/chroma.sqlite3`) | Citation-bearing Q&A, regulatory grounding |

The two stores are linked by canonical `policy_id` (e.g., `care-health__care-supreme__wordings`).

## Alternatives considered

| Approach | Why rejected |
|---|---|
| Pure RAG | Filter queries become 50-line LLM prompts that hallucinate; comparison can't scale. |
| Pure structured DB | No clause-level citation; can't answer "what does the policy say about X" for fields not in schema. |
| Single store with structured-as-metadata | Possible but couples schema evolution to vector index rebuild. |

## Consequences

**Positive:**

- Each query class hits the right store.
- Schema evolution doesn't trigger expensive re-embedding.
- DuckDB is single-file, embeddable; no infra burden.

**Negative:**

- Two stores to keep in sync at ingest time (`rag/ingest.py`).
- Cross-cutting queries ("policies where wording mentions 'restoration' AND structured.network_hospital_count > 10000") require small bridge code.

## Revisit at scale

| Pressure | Migration path |
|---|---|
| Multi-tenant | DuckDB → Postgres |
| 10× corpus | Chroma → Pinecone or Qdrant (interface stays the same; see `rag/retrieve.py`) |
