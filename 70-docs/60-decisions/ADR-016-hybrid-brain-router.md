# ADR-016: Hybrid brain router (Sarvam primary + Llama / DeepSeek fallback)

**Status:** Superseded by [ADR-040](ADR-040-google-gemini-primary.md) (2026-05-15) — the cross-provider router intent survives, but the candidate pool now leads with Google AI Studio (Gemini 2.0 / 2.5 Flash) on Brain Fast / Brain Main with NIM Mistral / Maverick / Qwen and OpenRouter `:free` as cross-provider diversity. Originally superseded by [ADR-019](ADR-019-nim-single-provider-consolidation.md).
**Date:** 2026-05-13

## Why superseded

The original Sarvam-primary + Llama/DeepSeek-fallback router was retired in two steps. ADR-019 first collapsed cross-provider sprawl onto a single NIM key. ADR-040 then re-opened the cross-provider surface — but on a different basis: native JSON-mode contract validation (`response_mime_type=application/json` / `response_format={"type":"json_object"}`) replaces empirical query-class routing as the candidate-selection rule. The current chain is Google → NIM → OpenRouter, scored by KI-080 probe-driven election rather than a static "Indic → Sarvam, comparison → DeepSeek" heuristic.

## Context

A single brain LLM was insufficient: Sarvam-M's 2048 output cap + `<think>` tokens caused mid-JSON truncation in extraction and mid-answer truncation in advisory. But Sarvam-first is non-negotiable narrative for the assignment.

## Decision (v1, since superseded)

**Hybrid router**: Sarvam-M as primary, escalate to Llama-3.3-70B (Groq) or DeepSeek-V3 (OpenRouter) for queries where Sarvam-M underperforms in benchmark.

### v1 router heuristic

- Indic language detected → Sarvam-M
- Comparison of 3+ policies → fallback brain (longer context, stronger reasoning)
- Open-ended recommendation requiring multi-hop reasoning → fallback brain
- Simple single-policy Q&A → Sarvam-M
- Empirical override: if gold Q&A eval shows Sarvam-M wins a query class we expected to lose, keep Sarvam-M for that class. Data > heuristic.

## What changed

ADR-019 collapsed this to **tiered routing inside a single NIM provider**:

- Heavy brain: DeepSeek-V4-Pro for comparison / recommendation intents.
- Fast brain: DeepSeek-V4-Flash for voice / qa / fact-find intents.
- Judge: Llama-4 Maverick (cross-family rescue) for faithfulness Gate 4 + cross-check retry.
- Sarvam-M demoted from brain role to **Indic translation cascade only** — keeps Sarvam where Sarvam is uniquely strong without exposing the 2048-token cap.

## Why supersession

The cross-provider router was complex (3 providers, 3 free-tier ceilings, 3 retry models, ~600 LOC of wiring). Tiered routing inside one NIM provider achieves the same intent-aware quality/latency trade-off with one API key, one rate-limit, and one consistent error model.

## Consequences (historical)

Router pattern itself was sound; the cost was the multi-provider sprawl. The current state preserves the router intent at the model-tier level.

## Revisit at scale

n/a — superseded. See ADR-019 for the current routing logic.
