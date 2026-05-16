# 05 — Needs Analysis Flow (LLM-driven sales brain)

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Version | 0.2 |
| Date | 2026-05-17 |
| Implementation | `backend/single_brain.py` (single-brain rewrite — replaced the removed `backend/sales_brain.py`). The slot schema still lives in `backend/needs_finder.py::GRAPH`. |

> ℹ️ **Still conceptually accurate, one pointer updated.** The core design
> below — *one LLM call per turn owns the entire fact-find surface* — is
> exactly what `backend/single_brain.py` does today; only the module name
> changed (`sales_brain.py` → `single_brain.py`, which also subsumes QA +
> recommendation). Present-state authority: [`README.md`](../../README.md) §4.

## 0. Why one LLM call per turn (not "scripted question graph + paraphraser")

A good Independent Financial Advisor opens with a stable, repeatable set of slots to fill — and adapts which slot to drive next based on what the buyer just said, what they've already told us, and what's still missing. We replicate this with **one LLM call per turn** that owns the entire fact-find surface (voice, cadence, slot-selection, multi-fact capture in a single turn), not a hardcoded state machine with a paraphraser glued on top.

The original v0.1 of this doc was an explicit `GRAPH` of canonical questions with `prompt_en` / `prompt_hi` strings. That design shipped with ADR-027 (LLM paraphraser on top of the graph) and ADR-030 (one-call brain with `<FF>` trailer + canonical fallback). Both architectures hit a hard wall: scripted prompts leaked into every fallback turn ("Got that — {slot}.") and produced robotic cadence even when the brain succeeded. KI-167 / [ADR-039](../60-decisions/ADR-039-llm-driven-sales-brain.md) ripped it all out: no canonical_fallback, no scripted prompts, no `<FF>` trailer. Native provider JSON mode (`response_mime_type=application/json` on Gemini, `response_format={"type":"json_object"}` on NIM) provides server-side parseable output.

What we kept and what we changed:

| Concern | v0.1 (scripted graph) | Current (LLM-driven sales_brain) |
|---|---|---|
| **Slot schema** | `GRAPH` of 9 `Question(id, prompt_en, prompt_hi, field, is_core, condition, parser)` entries | `GRAPH` data structure retained as the schema source; `prompt_en` / `prompt_hi` are dead strings now |
| **Question text** | Hardcoded `prompt_en` rendered to user | LLM generates natural prose in the advisor's voice per turn (no template) |
| **Slot selection** | `next_question(profile)` walked the graph in order | LLM picks `slot_driving` in its JSON response (constrained by the schema to required slots first) |
| **Multi-fact capture** | One slot per turn (a user saying "I'm 32, just myself, in Mumbai" only filled `age`) | LLM emits a `captures` map per turn — one utterance can fill 2-4 slots |
| **Auditable behavior** | Graph order was the trace | Per-turn LLM response is logged (intent, brain_used, captures, slot_driving, complete) in `40-data/llm_usage.jsonl` + `logs/turns.jsonl` |
| **Fail-soft** | Graph survived LLM degradation | Outer 25s `asyncio.wait_for` on the brain call; on total NIM + Google exhaustion the orchestrator returns the [ADR-038](../60-decisions/ADR-038-nim-only-chains.md) graceful error message to the user — no scripted reply |
| **Bilingual** | Hand-authored `prompt_en` + `prompt_hi` | Sarvam-M translation cascade on the LLM's output (English authoring + Indic translation), same UX, fewer hand-authored strings |

## 1. The 9-slot schema (data-only — the LLM consults this through its system prompt)

```
                    ┌─────────────────────────────┐
                    │  Q1: age (core)             │
                    │  "What is your age?"        │
                    └────────────┬────────────────┘
                                 ▼
                    ┌─────────────────────────────┐
                    │  Q2: dependents (core)      │
                    │  "Who else do you cover?"   │
                    └────────────┬────────────────┘
                                 ▼
                    ┌─────────────────────────────┐
                    │  Q3: income_band (core)     │
                    │  "Annual income?"           │
                    └────────────┬────────────────┘
                                 ▼
                    ┌─────────────────────────────┐
                    │  Q4: existing_cover (core)  │
                    │  "Already have health ins?" │
                    └────────────┬────────────────┘
                                 ▼
                    ┌─────────────────────────────┐
                    │  Q5: primary_goal (core)    │
                    │  "Why are you here?"        │
                    └────────────┬────────────────┘
                                 ▼
                    ┌─────────────────────────────┐
                    │  Q6: location (core)        │
                    │  "Which city / tier?"       │
                    └────────────┬────────────────┘
                                 ▼
                         (conditional branches)
                                 ▼
        ┌────────────────────────┴────────────────────────┐
        ▼                                                  ▼
┌──────────────────────────────┐         ┌────────────────────────────────┐
│ Q7: parents_age (cond)       │         │ Q8: health_conditions (always) │
│ asked IF dependents include  │         │ "Any pre-existing condition    │
│ 'parent'                     │         │  on your side?"                │
└──────────────┬───────────────┘         └─────────────────┬──────────────┘
               ▼                                            ▼
                                ┌──────────────────────────────────────┐
                                │  Q9: budget_band (core)              │
                                │  "Premium budget?"                   │
                                └──────────────┬───────────────────────┘
                                               ▼
                          ┌─────────────────────────────────────┐
                          │  Profile complete → readback +      │
                          │  policy recommendation              │
                          └─────────────────────────────────────┘
```

## 2. Termination criteria

The LLM sets `complete: true` in its JSON response when all required slots are filled. `backend/sales_brain_normalizer.py` double-checks: if `complete == true` while any required slot is empty, the normalizer overrides to `complete = false` — the LLM cannot prematurely declare fact-find done.

Or when the user opts out — if the user immediately asks "compare Star and HDFC ERGO" or "show me the top 3 policies for me" (KI-105 closer phrases), the intent classifier forces intent to `comparison` / `recommendation` BEFORE the fact-find branch is even reached, and the orchestrator routes straight to retrieval + Brain Main.

## 3. Bilingual handling

The LLM authors in English; Sarvam-M translates Hinglish ↔ English at the cascade boundary. The "Indic-native, not translated" principle (problem-statement §5.3) is preserved by the LLM's native-quality output rather than by hand-authored Hindi strings — the cascade itself is the bilingual surface.

## 4. Parsing user answers

The LLM produces a `captures` map per turn (e.g. `{"age": 34, "dependents": "self", "location_tier": "metro"}`). `backend/sales_brain_normalizer.py` is a pure-function post-processor that:

- Resolves field-name aliases (`location` → `location_tier`)
- Coerces enums (`Bangalore` → `metro`)
- Parses INR amounts (`"32 lakh"` → `3200000`)
- Drops null / empty values (KI-094 None-guard — a `None` capture never overwrites a filled field)
- Validates types and bounds (rejects `age: 250`)

No LLM calls in the normalizer — pure rules. The orchestrator applies the normalized map via `session.update_profile_field()` exactly as before. Profile persistence remains unchanged (`backend/profile_store.save_profile()` + `backend/profile_rag.upsert_profile_chunk()` with session-scoped metadata per KI-102).

## 5. How the orchestrator uses this

```python
profile = Profile.from_session(session_id)

if intent == "fact_find":
    # KI-167: one LLM call per turn against FAST_BRAIN_CHAIN
    outcome = await sales_brain.drive_sales_turn(
        profile=profile,
        history=session.chat_history[-N:],
        user_text=user_text,
    )
    # outcome.reply, outcome.captures, outcome.slot_driving, outcome.complete
    apply_normalized_captures(session, outcome.captures)
    if outcome.complete:
        session.free_form_session = True
    reply = outcome.reply
else:
    # QA / comparison / recommendation → Brain Main
    chunks = retrieve(user_query, top_k=10)
    reply = await BRAIN_CHAIN.chat(persona_prompt + history + chunks + user_text)
    if intent not in {"fact_find", "recommendation"}:
        # KI-171: judge skipped on fact-find + recommendation intents
        verdict = await faithfulness.check(reply, chunks)
        if not verdict.supported:
            reply = SAFE_REFUSAL
```

## 6. What the user sees

A natural conversation, not a script. Example (real session, 2026-05-15):

```
Bot: "Hi! I'm your insurance advisor. To get you the right shortlist I'll need
      to learn a bit about you and your situation. What's your name and a rough
      sense of who we're looking to protect?"
User: "Hi, I'm Rohit Sar. I'm 32, just myself, living in Mumbai."
Bot: "Nice to meet you, Rohit. Got it — 32, covering yourself, Mumbai-based.
      Two more quick things and we can look at policies: roughly what's your
      annual income, and have you had any health conditions diagnosed —
      diabetes, BP, anything chronic?"
User: "around 18 lakhs, no conditions"
Bot: "Perfect. And what's drawing you to insurance right now — first policy,
      top-up on employer cover, or something specific?"
User: "first policy, employer cover feels too thin"
Bot: "Makes sense. Last one: comfortable annual premium range — under 15K,
      15-30K, 30-60K, or higher?"
User: "15-30 is fine"
Bot: "Great — that's everything I need. Based on your profile, three policies
      stand out: [...]"
```

Note: the LLM captured `{name, age, dependents, location_tier}` in a single turn from the user's opener. Pre-KI-167 each fact required its own turn. The exact turn count varies — some sessions finish in 3-4 turns, others in 6-7 depending on what the user volunteers.

## 7. v2 enhancements

| # | Enhancement | Why |
| --- | --- | --- |
| 1 | Stream the prose token-by-token to the frontend | The JSON tail is parsed server-side after stream completion — would cut perceived latency below the canonical-fallback path |
| 2 | Promote the same single-call pattern to the QA path | Orchestrator's QA + fact-find branches collapse into one brain call with a richer schema (intent in the JSON block instead of out-of-band classification) |
| 3 | Skip-confirm flow ("you can skip this — say 'skip'") | Buyer autonomy |
| 4 | Save profile across sessions ✅ shipped KI-040 | Returning user picks up where they left off |
| 5 | Tone-match the user's energy (formal vs casual) | The LLM already does this implicitly via the system prompt; could be explicit per-session signal |
