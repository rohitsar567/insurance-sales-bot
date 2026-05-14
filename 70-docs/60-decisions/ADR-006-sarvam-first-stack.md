# ADR-006: Sarvam-first defaults for STT/TTS/LLM

**Status:** Partially superseded by [ADR-019](ADR-019-nim-single-provider-consolidation.md)
**Date:** 2026-05-13

## Context

This is a Sarvam AI take-home assignment. Silent defaults to non-Sarvam providers would screen out, regardless of technical merit.

## Decision

**Sarvam-first benchmarking** for every voice and reasoning component:

- STT: Sarvam Saarika v2.5 vs. Whisper / Deepgram
- TTS: Sarvam Bulbul v2 vs. ElevenLabs / OpenAI TTS
- LLM: Sarvam-M vs. GPT-4o / Claude

Components live behind thin interfaces (`backend/providers/base.py`) so swapping any one is a config flag, not a refactor.

## Outcome — what's still Sarvam after ADR-019

| Layer | v1 (this ADR) | After ADR-019 |
|---|---|---|
| STT | Sarvam Saarika v2.5 | **Stays Sarvam** — best Indian-accent recognition |
| TTS | Sarvam Bulbul v2 | **Stays Sarvam** — best Hinglish TTS |
| Indic translation | Sarvam-M | **Stays Sarvam** — best Hinglish/Hindi translation |
| Reasoning brain | Sarvam-M | **Moved to NIM DeepSeek-V4-Pro / V4-Flash** — Sarvam-M's 2048 output cap + `<think>` tokens caused mid-JSON truncation. See ADR-019. |
| Judge | Sarvam-M self-judge | **Moved to NIM Llama-4 Maverick** — different family from the brain for non-circular evaluation. |

## Why partial supersession is the honest narrative

ADR-019's framing in the SUBMISSION doc: *"Sarvam isn't trying to win a benchmark it doesn't need to win."* The voice + Indic pieces are where Sarvam is uniquely strong and closed-source frontier cannot match. The reasoning role is a different problem; MIT-licensed open-weights frontier (DeepSeek-V4-Pro) is a strictly better fit on a free tier with no rate cap.

## Consequences

**Positive:** Project leads with Sarvam exactly where Sarvam beats the world.

**Negative:** Slight increased complexity — three providers instead of one (Sarvam + NIM + local BGE embeddings).

## Revisit at scale

When Sarvam ships a longer-context reasoning model (current cap 2048 is the blocker), benchmark it against DeepSeek-V4-Pro on the same gold set and consider unifying.
