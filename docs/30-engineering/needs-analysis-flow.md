# 05 — Needs Analysis Flow (Fact-Find)

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Version | 0.1 |
| Date | 2026-05-13 |
| Implementation | `backend/needs_finder.py` |

## 0. Why an explicit graph (not "let the LLM figure it out")

A good Independent Financial Advisor opens with a stable, repeatable set of questions — and conditionally deep-dives based on the buyer's signal. We replicate this with an **explicit question graph**, not an LLM that improvises every session.

Why:

1. **Auditable behavior.** A reviewer can see the graph, trace any session, and check why a question was (or wasn't) asked.
2. **Testable.** Pure functions of `Profile → Question`. Every branch can be unit-tested.
3. **Fail-soft.** Even if the LLM brain degrades, the question flow still works.
4. **Bilingual by construction.** Each node has English + Hindi prompts side by side.

## 1. The graph

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

The graph emits `next_question = None` (i.e., ready to recommend) when all of:

1. All 6 core questions answered (age, dependents, income, existing_cover, primary_goal, location)
2. All applicable conditional questions answered (parents_age if dependents include parents)
3. health_conditions and budget_band answered

Or when the user sets `profile.free_form_session = True` — the orchestrator skips fact-find and answers free questions directly.

## 3. Bilingual prompts

Every node has both `prompt_en` and `prompt_hi`. The orchestrator picks based on the user's detected language. Example:

| Node | English | Hindi |
| --- | --- | --- |
| age | "To start, what's your age?" | "शुरू करते हैं — आपकी उम्र क्या है?" |
| dependents | "Who else do you want to cover…" | "आपके अलावा किस-किस को cover करना है…" |
| budget | "What annual premium budget…" | "Premium के लिए सालाना कितना खर्च…" |

This is what "Indic-native, not translated" (Doc 01 §5.3) looks like in practice — the question is *authored* in both languages, not machine-translated at runtime.

## 4. Parsing user answers

Each node has a `parser` callable that normalizes the user's natural-language answer into a typed Profile field:

- `age`: `lambda s: int("".join(c for c in s if c.isdigit())[:3] or 0) or None`
- `dependents`: free-form string (will be normalized via LLM in v1.1)
- `income_band`: bucketed via keyword match ("5 lakh" → "under_5L")

When the parser returns `None`, we treat the question as unanswered and re-ask (v1 simply leaves the field as None and moves on — re-ask is v1.1).

## 5. How the orchestrator uses this

```python
profile = Profile.from_session(session_id)
q = next_question(profile, language=detected_language)
if q is not None:
    # Still in fact-find phase — ask the next question
    reply = q.prompt_hi if language == "indic" else q.prompt_en
    profile.asked.append(q.id)
else:
    # Profile complete — proceed to retrieval + recommendation
    chunks = retrieve(user_query, top_k=5)
    reply = brain.generate(...)
```

The orchestrator can also let the user drive the conversation freely — if the user immediately asks a specific question like "compare Star and HDFC ERGO," we set `free_form_session = True` and skip the fact-find.

## 6. What the user sees

```
Bot: "Hi, I'm your AI insurance advisor. To start, what's your age?"
User: "I'm 32"
Bot: "Got it. Who else do you want to cover — just yourself, spouse, kids, or parents?"
User: "self + spouse, kids later"
Bot: "Roughly what's your annual income — under 5 lakh, 5-10, 10-25, or 25+ lakh?"
User: "around 18 lakhs"
... [continues through the core 6] ...
Bot: "Here's what I've understood: 32 years old; covering self+spouse; income 10L-25L;
      no existing cover; goal: first health policy; in a metro; budget 15K-30K.
      Did I get that right?"
User: "yes"
Bot: "Based on that, three policies stand out: [...] — let me explain why."
```

## 7. v2 enhancements

| # | Enhancement | Why |
| --- | --- | --- |
| 1 | LLM-parser for free-form answers ("I'm in my early 30s with 2 kids") | Handles natural-language replies instead of rigid bucket-matching |
| 2 | Re-ask on `None` parse | Robust to user confusion |
| 3 | Skip-confirm flow ("you can skip this — say 'skip'") | Buyer autonomy |
| 4 | Save profile across sessions | Returning user picks up where they left off |
| 5 | Tone-match the user's energy (formal vs casual) | Conversation feels human |
