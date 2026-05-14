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

## Closer-mode subsection (KI-105, `8a58fa1`, 2026-05-15)

The consultative persona above is the steady-state default. When the user issues an **explicit closer phrase** the persona is augmented (not replaced) with a strict ranked-shortlist contract.

**Trigger.** `classify_intent()` in `backend/orchestrator.py` matches the user message against `RECOMMENDATION_CLOSER_PHRASES` (frozenset, word-boundary regex). Matched phrases include `"show me the top 3"`, `"rank"`, `"pitch me"`, `"which is best for me"`, `"compare X vs Y"`. The closer-phrase check fires BEFORE the `FACT_FIND_TRIGGERS` check, so a fully-fact-found user can never get bounced back into fact-find by a closer phrase that contains words like `"me"`.

**Persona augmentation.** The base `ADVISOR_SYSTEM_PROMPT_V1` is concatenated with `RECOMMENDATION_CLOSER_ADDENDUM` for the closer turn only:

1. Output **exactly 3 ranked policies** — no more, no less.
2. One-line rationale per pick **tied to the user's profile** (age / dependents / income_band / city_tier / pre-existing conditions).
3. End with the **3-policy sum + IRDAI disclaimer** ("confirm policy details with the insurer before purchase; premium quoted is illustrative").
4. **No hedging language.** "You might want to consider..." / "It depends on..." / "Personal preference matters..." are explicitly forbidden in the addendum. The user asked for a ranked shortlist; deliver a ranked shortlist.

The core persona rules from the section above (grounded answers, citation grammar, no medical advice, no scare tactics, no final transactional advice — confirm with insurer) all still apply. The closer addendum is additive, not a replacement.

**Rationale.** Without KI-105, a user who has completed fact-find and then says "OK, now pitch me the top 3" got either (a) re-routed into fact-find (because "me" sat inside `FACT_FIND_TRIGGERS` before KI-023's word-boundary fix), or (b) a hedge-heavy 6-bullet exploration of options without a clear ranked recommendation. Both UX failures — the bot needs to be able to close the conversation when the user explicitly asks it to, while staying inside the consultative guard-rails (still cites, still discloses, still refuses transactional advice).
