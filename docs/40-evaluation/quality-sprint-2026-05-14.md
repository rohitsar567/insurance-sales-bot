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
- **Output:** `audit_results/full_20260514_145243/transcripts/<persona_id>.json` + `report.md` after analyzer pass
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
