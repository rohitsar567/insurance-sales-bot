# ADR-027 — LLM paraphrasing of fact-find questions (Option B, with verifier)

**Status:** Accepted — 2026-05-14
**Owner:** Rohit Saraf
**Related:** [ADR-022](ADR-022-conversational-profile-updates.md) (free-form profile updates)

## Context

Real-user testing on 2026-05-14 surfaced a sharp critique: the bot's 9 fact-find questions are "mechanical and robotic" — identical wording, identical order, every session. The questions live in [`backend/needs_finder.py::GRAPH`](../../backend/needs_finder.py) as hardcoded strings. **No LLM runs in the question-asking loop.** The orchestrator iterates the graph, prepends a fixed opener ("Got it. " / "Sorry, I didn't catch that. "), and emits the canonical string verbatim. The only LLM in the fact-find flow is the *answer* normalizer, never the *question*.

Three options were enumerated:

| Option | Approach | Effort | Risk |
|---|---|---|---|
| A | Paraphrase all 9 questions ONCE at session start (single LLM call); cache for session | Small | Low — slots intact, order preserved |
| **B** | Paraphrase the *next* question per turn using conversation history; verify the paraphrase still targets the same slot | Medium | Medium — needs a verifier |
| C | Drop the canonical graph entirely; let the LLM decide the next question given (profile-so-far, conversation-so-far, target-fields) | Large | High — LLM may skip / repeat / drift off-topic |

The user picked **Option B with a verifier** — the sweet spot between perceived quality and risk.

## Decision

[`backend/question_paraphraser.py::paraphrase_question`](../../backend/question_paraphraser.py) wraps each fact-find emission with a single LLM call that:

1. Reads `(canonical, slot_id, recent_user_text)`.
2. Calls `NimChainLLM(FAST_BRAIN_CHAIN, timeout=4s, total_budget_s=6s)` — so the call benefits from the [ADR-026](ADR-026-provider-load-balancing.md) NIM↔Groq rotation, and the 6s budget is a hard ceiling.
3. The LLM is prompted to return strict JSON: `{"paraphrase": "...", "asks_about_slot": "<slot_id>"}`.
4. A lenient parser (strict JSON → trailing-comma repair → first-balanced-block) handles model-side JSON sloppiness.

### The verifier

A paraphrase is accepted only if **all** the following hold:

| Check | Why |
|---|---|
| `asks_about_slot == requested slot_id` | Catches drift — the LLM occasionally rewrites the question to be about a different topic. If it did, fall back to canonical. |
| `"?" in paraphrase` | Statement-shaped rewrites would break the conversational flow. |
| `30 ≤ len(paraphrase) ≤ 500` | Rejects truncations / runaway generation. |

On any rejection, the call returns `None` and the caller uses the canonical question text. **Verifier failures never block fact-find** — worst case is one canonical question, one paraphrased question, all in the same session.

### Caching

Module-level dict keyed by `(session_id, slot_id)`. Each slot is paraphrased AT MOST ONCE per session → max 9 LLM calls per session. A `None` result is also cached so a flaky LLM during one slot doesn't get retried mid-session. `clear_session_cache(session_id)` is called from `reset_session()` so a "Start fresh" click produces fresh paraphrase wording.

### Scope

- **English language only.** Indic queries continue to use the canonical Hindi (`prompt_hi`) for now; cross-language paraphrasing is a separate concern.
- **Re-asks skip paraphrase.** When the user's first answer doesn't normalize, the bot's "Sorry, I didn't catch that. Let me ask again — …" message keeps the canonical wording so the user has a stable anchor.

## Why not the other options

- **Option A (per-session paraphrase)** — less variety; same wording for the whole session.
- **Option C (LLM-driven slot-filler)** — high risk; the LLM might skip slots, repeat them, or ask out-of-scope. Would require an extensive evaluation harness to ship safely. Deferred.

## Consequences

| Win | Cost |
|---|---|
| Fact-find questions adapt their wording per session — same slot, warmer voice ("To get you the best plan, could you share your age?" vs "First, your age?") | One extra LLM call per fact-find turn — ~1-2s on average (Groq Llama-3.3 primary half the time) |
| Verifier guarantees no slot drift — bot can never accidentally ask about the wrong field | Variance is moderate, not radical — temperature 0.7 |
| Cached per session → max 9 calls per persona | Bounded |
| Graceful degradation — canonical wording is the floor | — |

## Observability

Every paraphrase decision logs at INFO level if verifier rejects (with the reason). Successful paraphrases are silent. Look in HF Space logs for `paraphraser slot drift` / `paraphraser missing '?'` / `paraphraser length oob` to see failure-mode distribution.

## Related

- [ADR-022](ADR-022-conversational-profile-updates.md) — free-form profile updates (the *answer-side* LLM)
- [`backend/question_paraphraser.py`](../../backend/question_paraphraser.py)
- [`backend/needs_finder.py::GRAPH`](../../backend/needs_finder.py) — canonical 9 questions
