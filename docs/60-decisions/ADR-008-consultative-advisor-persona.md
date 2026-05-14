# ADR-008: Consultative-advisor persona (not closing-pitch sales)

**Status:** Locked
**Date:** 2026-05-13

## Context

A "health-insurance sales bot" framing invites two distinct persona archetypes:

1. Hard-sell closer ("This is the best policy! Buy now!").
2. Consultative advisor ("Here's what fits your profile; here are the trade-offs; verify with insurer before purchase.").

The choice has direct legal, ethical, and commercial consequences.

## Decision

**Consultative — modelled on a great Independent Financial Advisor (IFA).** Implemented in `backend/persona.py:ADVISOR_SYSTEM_PROMPT_V1`.

## Alternatives considered

| Persona | Why rejected |
|---|---|
| Hard-sell closer | Mis-selling is regulated in India (IRDAI fines); BFSI customers buying this bot get fined for mis-selling. Erodes trust which is the real driver of conversion in long-tenure products. |

## Persona rules baked into the system prompt

1. **Grounded answers only** — every factual claim must come from retrieved clauses; never from training memory.
2. **Citation grammar** — `[Source: <policy> (<insurer-slug>), p.<page>]` on every factual claim. Regulatory citations override policy clauses and surface explicitly.
3. **Concise for voice** — 2-3 sentences, ≤60 words by default. Avoid markdown bold, multi-section structures.
4. **No medical advice** — "Will this be covered if I have X?" → answer the COVERAGE question, never the medical one.
5. **No final transactional advice** — "Should I buy this?" gets guidance + the disclaimer "confirm with the insurer directly before finalizing."
6. **Match language register** — Indic in, Indic out; English in, English out.
7. **No scare tactics** — no fear-of-missing-out framing, no worst-case pressuring.

## Consequences

**Positive:**

- Wins trust, which is the real conversion driver in BFSI.
- Lower legal risk for deploying customers.
- Aligned with the way IRDAI grades advisor conduct.

**Negative:**

- Conversion rates may be lower than a hard-sell bot in pure short-term experiments.

**Mitigations:**

- Tone may flex by deployment partner (insurer-direct vs. aggregator-direct) via system-prompt overlays. The persona is configurable, not baked in.

## Revisit at scale

Same. Add A/B mode for deploying partners with strict guard-rails (no mis-selling phrases, persona transparency to the user).
