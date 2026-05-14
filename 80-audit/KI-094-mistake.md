# KI-094 — Mistake log entry

**Date:** 2026-05-15
**Commits:** `9813994` (KI-091), `f068094` (KI-094)

## Symptom

Fact-find conversational flow exhibited a "logical failure": bot captured `name` correctly on turn N, advanced to `income` on turn N+1, advanced to `existing_cover` on turn N+2, then on turn N+3 re-asked the user for `name`. The `name` field had clearly been captured and used to ask follow-up questions, then silently disappeared from the profile.

## Root cause

The LLM `profile_extractor` chain returns a structured dict (`{"name": ..., "income": ..., "existing_cover": ..., ...}`) on every turn. On turns where the user did not restate their name, the extractor returned `{"name": null, ...}` — the LLM's way of saying "I did not extract a name from this turn." The orchestrator's merge loop iterated those keys and called `session.update_profile_field("name", None)`, which `setattr`'d `None` onto the profile, **overwriting the previously captured value**. Then `next_question(profile)` correctly re-returned the `name` slot because `profile.name` was `None` again.

Two distinct semantics — "I didn't extract anything for this field" vs. "this field should now be empty" — were conflated under the same `null` return, and the merge loop trusted the extractor's nulls as authoritative.

## Fix (two layers)

1. **KI-091 (commit `9813994`)** — orchestrator now skips both `extract_profile_updates` and the faithfulness `judge` on fact-find turns. Rationale:
   - BRAIN_CHAIN + JUDGE_CHAIN are credit-exhausted and were hanging the response loop ~20s per turn.
   - The extractor was the field-clearing culprit. Fact-find turns already capture fields deterministically from the slot-filler — running the LLM extractor on top was both redundant and destructive.

2. **KI-094 (commit `f068094`)** — defensive guard inside the extractor merge loop:
   ```python
   for field, new_value in extracted.items():
       if new_value in (None, "", []):
           continue
       session.update_profile_field(field, new_value)
   ```
   This ensures that even in QA-mode (non fact-find) turns where the extractor still runs, a `None` / empty return from the LLM cannot wipe an already-filled field. Belt-and-suspenders alongside KI-091.

## Prevention rule

**When an LLM extractor returns a structured dict, NEVER pass `None` / `""` / `[]` values straight to `setattr` on a profile / state / session object.** Always filter Nones before merging.

The extractor LLM's `null` for a field means "I didn't extract anything for this field this turn" — it does NOT mean "this field is now empty." Conflating those two semantics silently destroys captured state.

### Checklist for any future extractor pipeline in this codebase

- [ ] Merge loop skips `None`, `""`, `[]`, `{}` by default — filter is opt-out, not opt-in.
- [ ] Unit test: feed the extractor a turn with no relevant info; assert no profile fields are cleared.
- [ ] Grep audit: any `setattr(profile, field, None)` callsite is a code smell. Add to pre-commit.
- [ ] If a field legitimately needs to be cleared, expose an explicit `clear_profile_field(field)` API. Never use a `None` return from a generic extractor as the clear-signal channel.

## Cross-references

- `MUST_FIX.md` — KI-091, KI-094 entries
- `backend/orchestrator/orchestrator.py` — fact-find skip path
- `backend/extractors/profile_extractor.py` (or merge loop callsite) — None filter
