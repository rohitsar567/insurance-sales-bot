# ADR-019: NVIDIA NIM as the single non-Sarvam provider

**Status:** Locked
**Date:** 2026-05-14

## Context

By mid-May 2026 the LLM stack had accreted **four third-party providers** in overlapping roles, each with its own free-tier ceiling that surfaced as quality problems:

| Provider | Role | Failure mode hit |
|---|---|---|
| OpenRouter (DeepSeek-V3 via meta-router) | Brain | $0 balance → HTTP 402 on every brain call |
| api.deepseek.com (direct) | Judge / fallback brain | Starter credits not applied to new keys → HTTP 402 |
| Cerebras (Qwen-3-235B) | Brain fallback / judge | Free-tier model swap broke chain; redundant once NIM was wired |
| Groq (Llama-3.3-70B) | Judge / extraction fallback | 30 req/min cap → chunk-sweep took 4-5h (ADR-018) |

Plus Sarvam-M used as brain — wrong fit because Sarvam-M's 2048 output cap + `<think>` tags consume the budget, truncating mid-JSON in extraction and mid-answer in advisory.

Trying to wire a fifth provider (DeepSeek direct) after OpenRouter ran out yielded HTTP 402 on a brand-new key. The marginal cost of every additional provider was real but invisible — each one shipped with its own retry/backoff, model id quirks, auth flow, and free-tier ceiling. Total: ~600 LOC of provider wiring for $0 of incremental capability.

## Decision

**NVIDIA NIM (`integrate.api.nvidia.com`) as the single non-Sarvam provider.** Tiered model routing inside one NIM key replaces the four-provider cascade.

### Final stack

| Role | Model id (NIM) | Why this model |
|---|---|---|
| Heavy brain | `deepseek-ai/deepseek-v4-pro` (1.6T / 49B MoE, 1M context, MIT) | Frontier on factual recall + reasoning. Beats Opus-4.6 + GPT-5.4 on SimpleQA-Verified (57.9% vs 46.2% / 45.3%) and LiveCodeBench. |
| Fast brain | `deepseek-ai/deepseek-v4-flash` (284B / 13B MoE, 1M context, MIT) | ~27% of V3.2 FLOPs → lower TTFT for voice. Still frontier-tier (HMMT 2026 94.8%). |
| Judge | `meta/llama-4-maverick-17b-128e-instruct` (400B / 17B MoE) | Meta family, not DeepSeek → non-circular grading. Used for faithfulness Gate 4, Hinglish drift LLM-judge, eval grader, cross-check rescue. |
| Indic translation | `sarvam-m` (Sarvam) | Best-in-class Hinglish/Hindi translation |
| STT | `saarika:v2.5` (Sarvam) | Best-in-class Indian-accent speech recognition |
| TTS | `bulbul:v2` (Sarvam) | Best-in-class Hinglish TTS |
| Embeddings | `BAAI/bge-small-en-v1.5` (local CPU) | 384-d, no network, free (ADR-011) |

## Alternatives considered

| Option | Why rejected |
|---|---|
| Deposit $10 to OpenRouter for 1000 req/day `:free` tier | Real bank transaction; v1 stays $0. |
| GitHub Models (GPT-4o with rate limits) | 50/day fragility identical to OpenRouter's. |
| Gemini 2.5 Flash on AI Studio | Frontier closed-source, 15 req/min, no cap. Strong but adds a second provider ecosystem. |
| Self-host DeepSeek-V4 | Weights are MIT-licensed and downloadable, but 671B params requires 8×H100 — impractical for take-home. |

## Consequences

**Positive:**

- $0 to deposit, $0 to run, no monthly minimum, no card on file.
- **Cross-family judge** preserved (Meta marks DeepSeek's homework, not the other way around).
- Single key, single provider → ~600 LOC deleted from `backend/providers/`, orchestrator fallback chains, faithfulness, translation_check, eval, smoke tests.
- Unblocks the deferred chunk-sweep (ADR-018) and 77 previously-failed extractions.
- 40 req/min cap is plenty for demo traffic.

**Negative:**

- 40 req/min would constrain a production deployment with many concurrent users.

**Mitigations:**

- v2: NIM enterprise tier or self-host the same models on a single H100 (FP8 + KV-cache compression makes V4-Pro feasible). Quality identical because weights are identical.

## Files touched

- Added: `backend/providers/nvidia_nim_llm.py` (single new module, ~140 LOC).
- Modified: `backend/config.py`, `backend/orchestrator.py`, `backend/faithfulness.py`, `backend/translation_check.py`, `backend/providers/__init__.py`, `backend/providers/_smoke_test.py`, `eval/run.py`, `rag/extract.py`.
- Deleted: `backend/providers/openrouter_llm.py`, `backend/providers/deepseek_llm.py`, `backend/providers/cerebras_llm.py` (Groq retained as optional fallback).
- `.env`: replaced `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, `DEEPSEEK_API_KEY` with single `NVIDIA_NIM_API_KEY`.

## Smoke-test evidence (2026-05-14)

- V4-Pro brain: "What does PED mean?" → grounded, citation-shaped answer ✅
- V4-Flash fast brain: same prompt → grounded answer ✅
- Maverick judge: same prompt → grounded answer ✅
- All three HTTP 200 through `backend/providers/nvidia_nim_llm.py`.

## Revisit at scale (v2)

- Move to paid NIM tier or self-host V4-Pro on a single H100 if demo traffic justifies it.
- Add Gemini 2.5 Pro as a closed-frontier comparison brain behind a feature flag, A/B against open-weights DeepSeek-V4-Pro.
- Profile-based routing: force fast brain on `comparison` intent if `profile_completeness < 0.4` (fact-find ongoing).
