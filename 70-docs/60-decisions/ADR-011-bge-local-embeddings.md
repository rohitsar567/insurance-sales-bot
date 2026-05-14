# ADR-011: Local BGE-small embeddings (Voyage was the original pick)

**Status:** Locked
**Date:** 2026-05-13

## Context

The retrieval store (Chroma) needs an embedding model. Original plan locked Voyage AI `voyage-3` (Anthropic's recommended partner, top MTEB benchmarks, $0.12 per 1M tokens). Mid-build, the Voyage free-tier 3 RPM rate limit blocked the 208-PDF ingest — at 3 requests per minute, a single chunk-by-chunk embed of the corpus would take ~30 hours.

## Decision

**Switch to local `BAAI/bge-small-en-v1.5`** via `sentence-transformers`. Voyage path retained behind the same `Embedder` interface (`backend/providers/base.py`) so v2 swap is a config flag.

## Alternatives considered

| Option | Why not |
|---|---|
| Voyage (original) | 3 RPM free-tier blocks the ingest. |
| OpenAI `text-embedding-3-small` | No OpenAI API available to this build. |
| Sarvam embeddings | Not exposed via API at build time. |
| BGE-m3 (multilingual) | Better for Indic; larger model; for v1 the queries route through Sarvam translator to English first, so a strong English embedder suffices. |

## Trade-offs

| Dimension | Voyage `voyage-3` | BGE-small-en-v1.5 |
|---|---|---|
| Retrieval quality (BEIR-style spot checks) | Baseline | ~3 pp below baseline |
| Cost | $0.12 / 1M tokens | $0 (local CPU) |
| Latency at ingest | Network bound | Local CPU bound (faster on Apple Silicon) |
| Rate limit | 3 RPM free / 300 RPM paid | None |
| Model size | API-only | 130 MB on disk |
| Dimensionality | 1024 | 384 |

## Consequences

**Positive:**

- Ingest finishes locally with no rate-limit drama (~3 minutes for the full 208 PDF embed at 800-token chunks).
- $0 cost.
- Same model available offline.

**Negative:**

- ~3 pp accuracy hit on retrieval recall vs. Voyage.
- 384-d vectors are smaller (less expressive) than Voyage's 1024-d.

**Mitigations:**

- Retrieval floor in `backend/faithfulness.py` Gate 1 (`MIN_TOP_SCORE = 0.30`) catches low-confidence retrievals before they reach the LLM.
- Profile-as-chunk boost (`rag/retrieve.py:155-170`) compensates for embedding weakness on personalized queries.

## Revisit at scale

v2: re-benchmark Voyage with a paid tier on the same gold set; route by language (Voyage for English queries, BGE-m3 or Sarvam embeddings for Indic) once Sarvam exposes embeddings.
