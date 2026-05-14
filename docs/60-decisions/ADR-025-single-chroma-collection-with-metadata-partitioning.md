# ADR-025: Single Chroma collection with metadata partitioning (not 4 separate collections)

**Status:** Locked
**Date:** 2026-05-14

## Context

The bot retrieves four logically distinct kinds of content at request time:

1. **Policy chunks** (PDF wordings, brochures, CIS, prospectuses) — what the policy says
2. **Regulatory chunks** (IRDAI master circulars, Insurance Act, etc.) — what the regulator mandates
3. **Review chunks** (per-insurer claim metrics, aggregator ratings, Reddit/YouTube sentiment, news) — what users say
4. **Profile chunks** (one chunk per active session, holding the user's age / dependents / income / conditions / budget) — what we know about THIS user

A reasonable mental model is "four separate databases". The literal Chroma equivalent would be four collections (`policies`, `regulatory`, `reviews`, `profiles`) queried separately, with results merged in the orchestrator.

## Decision

**Keep ONE Chroma collection (`policies`) partitioned by a `doc_type` metadata field.** All four logical types co-exist in the same vector index. Retrieval is a single cosine-similarity call; the orchestrator applies metadata filters only when the user's intent is clearly type-specific (e.g., a regulatory-only question).

```
collection: policies (Chroma; 7,366 chunks; BGE-small 384-d cosine)
  doc_type=wordings    (5,401 chunks)  — policy wordings
  doc_type=brochure    (  611 chunks)
  doc_type=cis         (  303 chunks)
  doc_type=prospectus  (  483 chunks)
  doc_type=regulatory  (  498 chunks)  — IRDAI + Insurance Act
  doc_type=review      (   60 chunks)  — 10 insurers × 6 facets (KI-017)
  doc_type=profile     (variable)      — one per active session_id
```

## Alternatives considered

| Approach | Why rejected |
|---|---|
| 4 separate collections (`policies`, `regulatory`, `reviews`, `profiles`) | Joint retrieval requires N queries + manual merge + N embedding-pool warmups. Loses the natural "user profile boosted in the same retrieval as the policy text" trick. ~2× higher per-turn latency for marginal lifecycle benefit. |
| Per-insurer collections | Cross-insurer comparison (the killer feature) becomes N queries. No upside. |
| Per-doc_type with shared embedder | Same as 4-collection but with one embedder. Still N queries. |

## Why one-collection wins for THIS bot

1. **Joint retrieval is the headline RAG move.** The bot's most cited feature is "the brain sees policy text + regulatory mandate + user's own profile in the same context window". One Chroma query returns all three. Splitting would require an explicit fan-out + merge.

2. **Profile-chunk boost is organic.** When `session_id` is passed to `retrieve()`, the chunk with `policy_id="profile_<session_id>"` is fetched separately and prepended to the top-k. This works cleanly because the profile lives in the same collection — it's a metadata lookup, not a cross-collection fetch.

3. **Free-tier resource discipline.** Each Chroma collection in the deployment has its own HNSW index (~50 MB of RAM warmup per collection on first use). The HF Space free tier has 16 GB RAM total; 4 collections means ~200 MB of cold-start memory pressure for negligible UX gain.

4. **Metadata partitioning is functionally equivalent.** When intent IS type-specific (e.g., the user asks about IRDAI), the orchestrator can apply `where={"doc_type": "regulatory"}` filters. The retrieval is single-query, same speed, same ergonomics as a separate-collection design — just with a 0.1ms metadata filter step.

## Consequences

**Positive:**

- Joint retrieval stays cheap (one query, one warmup).
- Profile chunk boost is a simple Chroma `get(ids=[profile_<session>])` call.
- Single backup target (`rag/vectors/chroma.sqlite3` + HNSW binaries) — simpler dataset hygiene.
- Mental model: "one vector store, one schema, doc_type tells you what kind".

**Negative:**

- A bad embedding for one doc_type can pollute global recall on unrelated queries. We mitigate with retrieval-floor + faithfulness gates.
- We cannot iterate embedding models per type (e.g., a legal-domain embedder for regulatory only) without changing the architecture.

## When we'd revisit

| Condition | Migration |
|---|---|
| Per-type embedding models become necessary | Split off the heaviest type (regulatory) into its own collection with its own embedder. |
| Multi-tenant deployment (profiles for thousands of concurrent users) | Move profiles to a separate `profiles` collection with TTL-based eviction policy. |
| Review content grows to >5,000 chunks | Move reviews to a dedicated collection so policy retrieval isn't diluted. |

## Related KIs and ADRs

- **KI-017** — Enriched per-facet review chunks (10 → 60). Validates that doc_type partitioning scales for richer content without requiring a separate collection.
- **KI-018** — Empty `rag/policies.duckdb` cleanup. The structured store from ADR-004 is currently dormant; structured filtering reads `data/policy_facts/*.json` directly.
- **ADR-004** — Hybrid structured + vector retrieval (the original design — DuckDB for filtering, Chroma for citing).
- **ADR-011** — Local BGE-small embeddings.
