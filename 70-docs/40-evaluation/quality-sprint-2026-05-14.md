# Quality Sprint — 2026-05-14

One-day sprint focused on hardening the non-speech parts of the bot via
code review, large-scale audit simulation, and real user-testing
feedback. Every defect found in the same day shipped a fix in the same
day; commit hashes below.

## Scoreboard

| Metric | Before sprint | After sprint |
|---|---|---|
| Critical (P0) issues open | 5 | 0 |
| High (P1) issues open | 5 | 0 |
| Medium (P2) issues open | — | 3 (KI-007, KI-009, KI-016 — backlog) |
| Live deploy SHA | `dae8de8` | `ffefacf` (+11 quality commits) |
| Audit framework | none | 100 personas × 30-turn flows + runner + analyzer |
| Documented "known issues" | 0 | 16 (10 closed, 6 backlog) |

## Findings & fixes by source

### From code review (P0/P1, 6 closed)

| KI | Severity | Issue | Commit |
|---|---|---|---|
| KI-001 | P0 | Gate 4 LLM judge fails OPEN on judge error (BFSI risk — unsupported claims leaked through) | `2412797` |
| KI-002 | P1 | Session disk flush silently swallows errors | `2412797` |
| KI-003 | P1 | Session disk load silently returns None on schema drift | `2412797` |
| KI-004 | P1 | Indic translator failure → original Indic to English brain silently | `2412797` |
| KI-005 | P1 | Profile-RAG chunk upsert failure silently swallowed | `2412797` |
| KI-006 | P2 | Profile extractor failure silently swallowed | `2412797` |

Fix shape: KI-001 flips default to fail-CLOSED with FAITHFULNESS_FAIL_CLOSED env override; KI-002-006 add `logging.warning` calls so the silent failures now surface in HF Space logs.

### From 100-persona audit framework (P0, 2 closed)

| KI | Severity | Issue | Commit |
|---|---|---|---|
| KI-011 | P0 | Fact-find re-ask infinite loop under NIM rate-limit | `171f2a4` |
| KI-012 | P0 | Bot stuck in fact_find_complete readback loop (19/30 turns wasted) | `75b229d` |

Both surfaced within the first 6 minutes of the first audit run. KI-011 was a normalizer-LLM-failure-at-load issue; fixed by hand-curated keyword fast-path + re-ask cap. KI-012 was a missing `free_form_session=True` flag on fact-find completion.

### From real user testing (P0/P1, 3 closed)

| KI | Severity | Issue | Commit |
|---|---|---|---|
| KI-013 | P0 | Bot recommended "Care Senior" (senior-only policy) to non-senior on vague opener | `f93292f` |
| KI-014 | P1 | "family" auto-mapped to "self+spouse+kids" (could have meant joint family) | `f93292f` |
| KI-015 | P1 | Age 30 vs 31 mismatch — readback didn't invite corrections explicitly | `82a40d3` |

Plus the system-prompt reinforcement for KI-013 (rules 8 + 9 in `ADVISOR_SYSTEM_PROMPT_V1`) in commit `ffefacf` — defense-in-depth so the brain LLM avoids demographic-mismatched recommendations even if profile-completeness gating is bypassed.

## What's still open

| KI | Severity | Status |
|---|---|---|
| KI-007 | P2 | Indic cascade fall-back logging — backlog |
| KI-008 | P3 | TTS preprocess regression test — backlog |
| KI-009 | P2 | VAD threshold calibration on entry — backlog |
| KI-010 | P3 | (Fixed in same session — audit runner unbuffered) |
| KI-016 | P2 | NIM promoted Qwen3-next over V4-Flash — needs empirical re-eval |

## Audit run summary

- **Run id:** `full_20260514_145243`
- **Sample:** 100 personas × 30 turns = 3000 chat calls against live HF Space
- **Concurrency:** 4 async workers with 2.0s/dispatch global rate limit (under NIM's 40 req/min cap)
- **Expected wall time:** ~2 hours
- **Output:** `80-audit/full_20260514_145243/transcripts/<persona_id>.json` + `report.md` after analyzer pass
- **Resumable:** completed personas' JSONs persist; re-running the script skips them.

The audit is the empirical truth-source for the quality sprint. Pre-sprint we expected: high refusal rate, infinite re-ask loops, demographic-mismatched recommendations. Post-sprint we expect: 0 infinite loops (KI-011 + KI-012 fixed), fact-find always asked before recommendations (KI-013), and verbose/casual/Hinglish styles handled gracefully (keyword fast-path in KI-011 fix).

## Commit chain

```
dae8de8  (sprint start — pre-quality)
2412797  KI-001 through KI-006 — observability + fail-closed judge
171f2a4  KI-011 — keyword fast-path + re-ask cap
75b229d  KI-012 — free_form_session on fact-find completion
f93292f  KI-013 + KI-014 — force fact-find on empty profile + drop vague-term auto-mapping
82a40d3  KI-015 — invite corrections in readback
ffefacf  KI-013 reinforcement — persona rules 8 + 9 for demographic-aware recs
```

Every commit pushed to BOTH `origin` (HF Space) and `github` per the
triple-mirror contract in [ADR-024](../60-decisions/ADR-024-triple-mirror-code-and-data.md).

---

## Follow-on sprint — 2026-05-15 (KI-167 → KI-179)

A second-day sprint after live testing flagged that the scripted `<FF>` trailer convention was leaking into every fallback turn ("zero natural LLM chat — it always defaults to the script"). The day produced one ADR (ADR-039), one provider integration (Google AI Studio), and a chain-level rewrite (ADR-040).

### Findings & fixes by source

| KI | Severity | Issue | Resolution |
|---|---|---|---|
| KI-167 | P0 | `<FF>` trailer + `_canonical_fallback` dominated user-visible turns when LLM contract violations occurred; bot felt robotic | Ripped out `fact_find_brain.py` + canonical_fallback + scripted prompts; new `backend/sales_brain.py` with one LLM call/turn using native provider JSON mode. ADR-039. |
| KI-168 | P1 | Voice UX waited for full Sarvam STT round-trip before showing transcript | Hybrid Web Speech (interim) + MediaRecorder (authoritative blob → Sarvam STT on silence-detect). |
| KI-171 | P1 | Faithfulness judge ran on `fact_find` + `recommendation` turns where there's no retrieval context | Skip Gate 4 on those intents; Gates 1-3 still run. |
| KI-173 | P2 | Mic died when user switched tabs / minimised the app | Heartbeat keeps mic stream alive. |
| KI-174 | P2 | Mic state did not recover on `visibilitychange` / `focus` | Revival hooks reattach the mic on tab return. |
| KI-175 | P2 | NIM Nemotron 49B was a chain primary but consistently underperformed Mistral 675B / Qwen 80B | Demoted Nemotron 49B to last resort across all chains. |
| KI-176 | P2 | OpenRouter dropped from chains in ADR-038 was no longer needed after ADR-039 retired `<FF>` | Re-added OR `:free` candidates with verified JSON mode as cross-provider diversity. |
| KI-178 | P2 | Audit of which OR `:free` models support `response_format` | Llama 3.3 70B / Hermes 3 405B excluded (no native JSON mode); Nemotron-3-Super 120B + Qwen 80B `:free` + Gemma-4 31B included. |
| KI-179 | P2 | Google AI Studio key obtained; need to wire it into chains | New `backend/providers/google_gemini_llm.py`; Gemini 2.0 Flash → Brain Fast primary, Gemini 2.5 Flash → Brain Main primary. ADR-040. |

### What changed in the architecture

- **Brain Fast (sales_brain):** Gemini 2.0 Flash → NIM Qwen 80B → NIM Mistral Large 3 675B → NIM Llama-4 Maverick → OR Nemotron-3-Super 120B → OR Qwen 80B `:free` → NIM Nemotron 49B (last resort).
- **Brain Main:** Gemini 2.5 Flash → NIM Mistral Large 3 675B → NIM Llama-4 Maverick → NIM Qwen 80B → OR Nemotron-3-Super → NIM Nemotron 49B (last resort).
- **Judge:** NIM Mistral Large 3 675B → NIM Llama-4 Maverick → OR Qwen 80B `:free` → NIM Nemotron 49B (last resort).

### What was deleted

- `backend/fact_find_brain.py` (441 LOC).
- `_canonical_fallback`, `_normalize_for_slot`, `_pick_opener`, `_NEUTRAL_OPENERS`, `_FAMILY_OPENERS`, `_contains_self_introduction` in `backend/orchestrator.py`.
- The `<FF>{...}</FF>` trailer convention + lenient parser ladder + `:no_trailer` / `:empty_reply` / `:llm_error` telemetry variants from `TurnResult.brain_used`.
- Dead `prompt_en` / `prompt_hi` strings in `backend/needs_finder.py::GRAPH` are no longer rendered (data structure retained for the LLM's system-prompt schema).

### What stayed

- KI-080 sticky-primary election machinery (now scores Google / NIM / OpenRouter candidates uniformly).
- KI-084 per-phase httpx timeouts.
- KI-085 proactive credit gating (extended to Google quota).
- KI-091 / KI-094 None-guards on the profile extractor (still relevant for QA-mode turns).
- KI-102 / KI-107 session-isolated profile RAG.
- KI-106 graceful `TimeoutError` + `Exception` handling on `/api/chat`.
