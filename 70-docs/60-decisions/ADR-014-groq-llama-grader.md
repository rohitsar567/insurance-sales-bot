# ADR-014: Groq Llama-3.3-70B as eval grader

**Status:** Superseded by [ADR-019](ADR-019-nim-single-provider-consolidation.md)
**Date:** 2026-05-13

## Context

The eval harness needed a grading LLM that was NOT the same family as the brain LLM (to avoid circular evaluation). Available options:

- GPT-4o-mini — rejected (no OpenAI API on this build).
- Claude Haiku via API — rejected (no Anthropic API key; Claude Code Max is terminal-only).
- Groq Llama-3.3-70B-versatile — free tier 30 req/min, different family from Sarvam-M brain.

## Decision (v1, since superseded)

**Groq Llama-3.3-70B-versatile** as the eval grader. Different model family from Sarvam-M brain → non-circular grade.

## What changed

ADR-019's consolidation moved the eval grader to **NIM Llama-4 Maverick** instead. The reasoning was the same (different family from the brain, non-circular) but the consolidation eliminated the Groq dependency entirely — one less provider, one less rate-limit, one less API key.

Groq Llama-3.3-70B remains wired as a fallback in `backend/providers/groq_llm.py` for cases where NIM Maverick is rate-limited (40 req/min cap), but it is no longer the primary grader.

## Why this ADR is kept as a historical record

The chunk-sweep failures documented in [ADR-018](ADR-018-chunk-size-sweep-deferred.md) were caused by Groq's 30 req/min rate limit interacting with the grader's per-question API call pattern. Documenting why Groq was chosen and the rate-limit collision is useful context for anyone reproducing the eval pipeline.

## Risk that motivated supersession

Groq's 30 req/min free tier was insufficient for systematic eval runs at corpus scale (96 gold questions × N model variants = N×96 requests, often hitting the rate ceiling and producing flat results across model variants — see ADR-018).

## Revisit at scale

n/a — superseded.
