# ADR-042 — Privacy hardening (recall redaction + match-before-merge), sticky-retry schedule, honest canned-failure copy, admin refresh wiring

**Date:** 2026-05-27
**Status:** **PARTIALLY SUPERSEDED by [ADR-043](ADR-043-remove-cross-session-recall.md) (same day).** The recall-prompt redaction (D3), match-before-merge guard (D4), v3 extended extractors and v4 two-fact gate are obsolete — cross-session recall was removed entirely. The sticky-retry schedule (D1), honest canned-failure copy (D2) and admin refresh wiring (D5) are KEPT and remain in production.
**Commits:** `2acdc9e` (v1), `10e6843` (v2 same-turn age contradiction), `2be56b4` (v3+v4 extended extractors + two-fact gate).

## Context

Four user-flagged behaviours surfaced in the same QA session:

1. **Canned reply "Sorry, I'm having trouble — could you say that again?"** appeared after a chat turn that had nothing wrong with the user's audio or phrasing. The actual cause was an upstream Gemini transient (HTTP 503 high-demand) that the previous single-retry policy could not absorb.
2. **Admin Console → LLM Chain → "Refresh now"** did not visibly change the top-left *"Last refresh / Next in"* timer, leading the operator to suspect that no real probing was happening on click.
3. **Welcome-Back prompt to a returning user named "Rohit" disclosed the stored profile's attributes** (`age: 34; metro; dependents: self+spouse+kids; primary_goal: first_buy`) in the user-visible question — to a *different* Rohit who had no relationship with the stored profile. Even though the merge gate (`apply_pending_recall`) discarded the staged recall on the user's "no", the disclosure had already happened.
4. **The recall key is name-only** (`<name_slug>.json` recall pointer rewritten by every save), so the only mechanism keeping a stranger from inheriting a prior visitor's profile was the explicit user "yes/no" confirm. A mistaken "yes" would partial-merge the wrong profile.

## Decision

### D1. Sticky-session retry schedule in `_gemini_call`

`backend/single_brain.py:_gemini_call` gains a `is_sticky: bool` kwarg threaded from `handle_turn` (which reads `session.single_brain_sticky` once per turn). The retry schedule branches:

| Path | Attempts (total) | Backoffs | Jitter | Reason |
|---|---|---|---|---|
| Non-sticky (no prior successful turn) | 2 | `[1.5 s]` | none | Fast-fail to `nim_fallback` — that's exactly what NIM exists for on cold-start 503. |
| **Sticky** (prior successful turn) | **3** | `[1.5 s, 3 s]` | ±25 % | A cross-fade would discard `last_recommendation_ids / last_retrieved_chunks / slug_to_insurer`; absorb the ~6–10 s Gemini high-demand bursts in place. |

Jitter prevents synchronised retry storms across concurrent sessions.

### D2. Honest canned-failure copy

`backend/main.py` sticky_graceful_retry text changes from

> *"Sorry, I'm having trouble — could you say that again?"*

to

> *"My model service had a brief blip on that turn — please send the same message again, it should go through now."*

The new wording locates blame correctly (upstream model service, not the user's audio/comprehension) and tells the user the exact next step.

### D3. Recall prompt — redact stored attributes

`backend/single_brain.py recall_block` no longer interpolates the staged summary into the user-facing prompt. The new template:

> *"Welcome back — have we spoken before? If yes, please share your age so I can pull up the right profile. If not, no problem — just say so and we'll start fresh."*

No `age:`, no `dependents:`, no `metro`, no `primary_goal` echoed. The prompt **inverts the burden of proof**: rather than asking the user to *confirm* a disclosed profile, it asks them to *re-state* one identifying fact (age) which the system then matches against the staged recall.

### D4. Match-before-merge guard in `apply_pending_recall`

`backend/session_state.apply_pending_recall(session, *, confirmed: bool, user_text: str = "")` adds a contradiction guard:

- **Prior-turn check (v1, 2acdc9e):** for each decision-critical field `(age, dependents, income_band, location_tier, primary_goal, parents_age_max)`, if the live session has already captured a value AND the staged value differs, discard the entire staged recall — no partial merge.
- **Same-turn check (v2, 10e6843):** a best-effort age regex (`_extract_age_from_text`) parses the just-said `user_text`. If the parsed age contradicts the staged age, discard. This closes the gap where `_affirm_or_deny` fires *before* the LLM iteration loop has saved fields, so the prior-turn check sees `live.age = None`.

The LLM (`save_profile_field`) remains the canonical extractor for the live session — the regex is a *safety net*, not a parallel extractor.

### D5. Admin LLM Chain refresh wiring

`frontend/public/admin/llm-control.html` — four sites updated in lockstep:

| Site | Before | After |
|---|---|---|
| `Refresh now` click handler | `apiPost('/api/admin/probe') → fetchLlmHealth → renderLlmHealth` | + `fetchHealth()` + `renderUpdatedLabel()` |
| 30 s auto-poll | `fetchLlmHealth → renderLlmHealth` | `Promise.all([fetchHealth, fetchLlmHealth]) → renderLlmHealth + renderUpdatedLabel` |
| `refreshChain()` (LLM Chain tab first entry) | same as auto-poll old | same as auto-poll new + `setLastUpdated()` |
| Tab re-entry partial refresh | `fetchLlmHealth → renderLlmHealth` | same as auto-poll new |

`STATE.health.updated_at` is now kept in sync with reality instead of frozen at the login-time snapshot.

## Consequences

- **Privacy.** A stranger sharing a name slug can no longer learn the prior visitor's age / dependents / location / goal from the prompt, and cannot inherit those attributes even with a mistaken "yes" — the same-turn age contradiction guard catches it.
- **Legitimate recall still works.** A returning visitor who states the matching age gets the full welcome-back experience (verified live, test E in `/tmp/audit-e2e-results.json`, commit `10e6843`).
- **Retry cost.** Worst-case sticky retry budget is now ~5 s of backoff plus 3 × per-call timeout. Still inside the 45 s outer `wait_for` budget in `main.py:957`.
- **Admin operator no longer left guessing.** Top-left timer resets to seconds on `Refresh now` click. Pairs with the existing bottom-right *FRESH* badge for two independent freshness indicators.
- **Operational note on `_extract_age_from_text`.** The regex is intentionally tight (`\b\d{2}\b` with optional `i'm` / `aged` / `years old` / `yrs` / `y/o` prefixes, range-gated [18, 99]). Mis-parses return `None` and skip the extra check, costing nothing. 11/11 unit cases pass (including the original screenshot "No, I am Rohit who is 29 years old").

## Verification

Live audit against `https://rohitsar567-insurancebot.hf.space` @ `10e6843`, 8/8 pass:

```
✓  G_canned_copy_deployed                  (build state RUNNING on target sha)
✓  H_probe_advances_tested_at              (19:59:32Z → 20:01:19Z)
✓  A_admin_refresh_resets_timer            (7s → 1s after click)
✓  B_no_attr_leak                          (prompt: no age/metro/kids/goal echoed)
✓  C_asks_for_age                          ("share your age")
✓  D_wrong_person_discards                 (deny + 29 → fresh fact-find, no leak)
✓  E_legitimate_recall_merges              ("Yes I'm 29" matching → welcome-back, no re-ask)
✓  F_same_turn_age_contradict_discards     ("Yes I'm 35" mismatch → fresh, no leak)
```

Script: `/tmp/playwright-e2e-full-10e6843.js` (preserved in the audit artifacts directory; screenshots under `/tmp/audit-*-final.png`).

## Open follow-ups

- The recall key is still name-only at the slug level. A stronger design would require *two* identifying facts to match before the merge gate even opens. Deferred — current redaction + match-before-merge is sufficient privacy hygiene for the data we hold.
- Same-turn extraction is age-only. Dependents, location, income are not parsed by `_extract_age_from_text`; the LLM's `save_profile_field` covers them on subsequent turns, so the *prior-turn* guard catches the rest. Acceptable.
