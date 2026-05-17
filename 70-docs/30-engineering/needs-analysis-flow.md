# 05 — Needs Analysis Flow (single-LLM-with-tools)

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Version | 0.3 |
| Date | 2026-05-17 |
| Implementation | `backend/single_brain.py` + `backend/brain_tools.py`. The slot schema still lives in `backend/needs_finder.py::GRAPH`. |

> ℹ️ **Design intent below is still accurate; implementation pointers
> updated.** The core idea — *one LLM call per turn owns the entire
> fact-find surface* — is what the single-LLM-with-tools handler does
> today. Fact-find, retrieval, QA, and recommendation are all handled in
> that one call via function-calling tools; the separate `sales_brain` /
> `qa_brain` split, the orchestrator, the profile_extractor, and the
> separate faithfulness judge were removed. Present-state authority:
> [`README.md`](../../README.md) §4.

## 0. Why one LLM call per turn (not "scripted question graph + paraphraser")

A good Independent Financial Advisor opens with a stable, repeatable set of slots to fill — and adapts which slot to drive next based on what the buyer just said, what they've already told us, and what's still missing. We replicate this with **one LLM call per turn** that owns the entire fact-find surface (voice, cadence, slot-selection, multi-fact capture in a single turn), not a hardcoded state machine with a paraphraser glued on top.

The original v0.1 of this doc was an explicit `GRAPH` of canonical questions with `prompt_en` / `prompt_hi` strings, rendered by a scripted state machine. That scripted approach (and a later one-call-brain variant with a structured trailer + canonical fallback) produced robotic cadence: scripted prompts leaked into fallback turns. The current design removes all of that. There are no scripted prompts and no canonical fallback. One **Gemini 2.5-flash call per turn with function-calling** owns voice, cadence, and slot selection; the `GRAPH` is consulted only as the slot *schema*.

What we kept and what we changed:

| Concern | v0.1 (scripted graph) | Current (single LLM + tools) |
|---|---|---|
| **Slot schema** | `GRAPH` of 9 `Question(id, prompt_en, prompt_hi, field, is_core, condition, parser)` entries | `GRAPH` data structure retained as the schema source; `prompt_en` / `prompt_hi` are dead strings now |
| **Question text** | Hardcoded `prompt_en` rendered to user | LLM generates natural prose in the advisor's voice per turn (no template) |
| **Slot selection** | `next_question(profile)` walked the graph in order | The single LLM decides which slot to drive next from the schema + conversation so far (required slots first) |
| **Multi-fact capture** | One slot per turn (a user saying "I'm 32, just myself, in Mumbai" only filled `age`) | One utterance can fill 2-4 slots; the LLM calls `save_profile_field` per captured fact |
| **Auditable behavior** | Graph order was the trace | Per-turn LLM call + tool calls are logged (`save_profile_field` / `retrieve_policies` / `mark_recommendation`) in `40-data/llm_usage.jsonl` + `logs/turns.jsonl` |
| **Fail-soft** | Graph survived LLM degradation | On a transient Gemini error the small `backend/nim_fallback.py` (NVIDIA NIM) completes the turn; fail-loud otherwise — no scripted reply |
| **Bilingual** | Hand-authored `prompt_en` + `prompt_hi` | Sarvam-M translation on the LLM's output (English authoring + Indic translation), same UX, fewer hand-authored strings |

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

Fact-find is "done" when all required slots are filled. Because the single LLM owns the whole turn, it stops asking discovery questions and moves to retrieval/recommendation once the profile is sufficiently complete; required-slot checks against the `GRAPH` schema keep it from declaring done prematurely.

The user can also opt out — if the user immediately asks "compare Star and HDFC ERGO" or "show me the top 3 policies for me", the same LLM call can skip remaining discovery and go straight to `retrieve_policies` / `mark_recommendation` within that turn.

## 3. Bilingual handling

The LLM authors in English; Sarvam-M translates Hinglish ↔ English at the cascade boundary. The "Indic-native, not translated" principle (problem-statement §5.3) is preserved by the LLM's native-quality output rather than by hand-authored Hindi strings — the cascade itself is the bilingual surface.

## 4. Capturing user answers

When the user states a fact, the LLM calls the `save_profile_field` tool (e.g. `save_profile_field(field="age", value=34)`). The tool implementation in `backend/brain_tools.py` is a pure-function handler that:

- Resolves field-name aliases (`location` → `location_tier`)
- Coerces enums (`Bangalore` → `metro`)
- Parses INR amounts (`"32 lakh"` → `3200000`)
- Drops null / empty values (a `None`/empty value never overwrites a filled field)
- Validates types and bounds (rejects `age: 250`)

The handler is pure rules — no nested LLM call. Captured fields are persisted to the session profile.

## 5. How the single LLM uses this

There is no separate orchestrator / intent-classifier / sales-brain split. One `single_brain` call per turn receives the system prompt (which embeds the slot schema), the recent history, and the user's text, and uses function-calling tools to do everything in that one turn:

- `save_profile_field` — record a captured fact (fact-find).
- `retrieve_policies` — structured + vector retrieval over the corpus when the turn needs policy evidence (QA / comparison / recommendation).
- `mark_recommendation` — record the policies the LLM recommends, surfaced with the profile-tuned scorecard.

Because the LLM can only answer from what `retrieve_policies` returned and must cite it, grounding is structural — there is no separate faithfulness-judge LLM in the loop. On a transient Gemini error, `backend/nim_fallback.py` completes the turn.

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

Note: the LLM captured `{name, age, dependents, location_tier}` in a single turn from the user's opener via `save_profile_field` calls. The exact turn count varies — some sessions finish in 3-4 turns, others in 6-7 depending on what the user volunteers.

## 7. v2 enhancements

| # | Enhancement | Why |
| --- | --- | --- |
| 1 | Stream the prose token-by-token to the frontend | Cuts perceived latency on longer replies |
| 2 | Skip-confirm flow ("you can skip this — say 'skip'") | Buyer autonomy |
| 3 | Save profile across sessions ✅ shipped KI-040 | Returning user picks up where they left off |
| 4 | Tone-match the user's energy (formal vs casual) | The LLM already does this implicitly via the system prompt; could be explicit per-session signal |
