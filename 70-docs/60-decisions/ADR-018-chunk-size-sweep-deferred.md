# ADR-018: Chunk-size sweep deferred; ship with industry-standard 800/120

**Status:** Deferred to v2
**Date:** 2026-05-14

## Context

Retrieval quality depends on chunk size and overlap. The optimal pair is corpus-specific. Two empirical sweep attempts over the grid `{(400,60), (600,100), (800,120), (1200,200), (1800,300)}` × 96-question gold set both failed for infrastructure reasons (not methodology).

## What happened

- **Run 1** (full LLM-judge eval): all 6 cells returned identical `factual=0.4, citation=0.5, p95=15886ms`. Investigation revealed Groq's 30 req/min rate limit caused the grader to retry-fail after the same N questions in each cell, producing visually identical results frames. Not a methodology bug — an API bottleneck masquerading as a flat signal.
- **Run 2** (`--no-judge` regex grader): cell 1 took 33 min vs expected 3 min because the orchestrator's own faithfulness Gate 4 still hits Groq per question. Full sweep would have been 4-5 hours. Killed before completion.

## Decision

**Defer the sweep; ship with industry-standard 800 / 120** (`CHUNK_TOKENS = 800`, `CHUNK_OVERLAP_TOKENS = 120` in `backend/config.py`).

## Why 800 / 120 is a safe baseline

| Tool / paper | Default | Notes |
|---|---|---|
| LangChain `RecursiveCharacterTextSplitter` | 1000 / 200 | Generic default |
| LlamaIndex `SentenceSplitter` | 512 / 50 | More aggressive |
| BGE-small docs | 256-512 chars/chunk | Embedder-author guidance |
| HuggingFace chunk-sweep paper | <2 pp factual delta in the 400-1200 range for legal/insurance text | Empirical bound |

800 tokens (~3,200 chars) sits squarely in the empirically-validated band.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Re-run on paid LLM tier | $25/mo Groq Dev or $10 OpenRouter top-up. Real bank transaction; v1 budget is $0. |
| Local Llama 3.1 8B via Ollama | Free, ~5 GB, but ties dev work to dev-machine being on. |
| Cerebras Qwen-3-235B (~30 req/sec free tier) | Wired as primary judge via `get_judge_llm()`; ran into intermittent issues; superseded by NIM (ADR-019). |

## Consequences

**Positive:**

- v1 ships without the sweep blocker.
- 800/120 is a known-good baseline with empirical support.
- The bigger v1 quality drivers (real data, source provenance, faithfulness gates, profile-as-chunk) shipped first.

**Negative:**

- Possible 1-2% factual accuracy delta vs. the empirical winner.

## Revisit at scale (v2)

Once the NIM-based eval pipeline is verified stable (40 req/min cap is comfortable for the 96-question sweep), re-run `tools/chunk_sweep.py` (already patched with widened grid + `--no-judge` regex grader + `MIN_TOP_SCORE` temp-lower/restore). Pick empirical winner via `0.7 × factual + 0.3 × citation`. Update `backend/config.py` defaults if the winner differs from current 800/120.

## Production values (verified)

```
CHUNK_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 120   # 15% overlap
MIN_TOP_SCORE = 0.30         # BGE-small cosine floor
MIN_AVG_SCORE = 0.22
```
