# `tests/` — Unit + live-verification tests

Deliberately small. The bulk of behavioural quality lives in `eval/` (gold-QA accuracy) and `tools/audit/` (multi-persona stress). This folder pins the **invariants** — the specific bugs we have ever shipped and never want back.

## Files

| File | Role |
| --- | --- |
| `test_routing_regression.py` | 15 `unittest` cases pinning the KI-018 / KI-023 / KI-080 invariants — see "Routing invariants" in the root `CLAUDE.md`. Includes legacy `TestProviderLoadBalancing` which asserts the 50/50 NIM ↔ Groq split when `_balanced_brain_chain` is invoked directly; with KI-080 ([ADR-031](../70-docs/60-decisions/ADR-031-sticky-primary-election.md)) live traffic uses probe-elected primary instead, but the rotation invariant is retained as a regression pin for the bypassed legacy path. |
| `live_verify.py` | End-to-end production drift detector. Hits the **deployed** API with a 20-Q gold subset and asserts HTTP 200, non-empty `reply_text`, ≥1 citation, faithfulness pass, and Doc-01 latency budget (p95 ≤ 7000ms). Writes `tests/live_results_<ts>.md`. Cron-able for nightly. |

## What each test pins

| KI / ADR | Assertion | Why it matters |
| --- | --- | --- |
| KI-018 (D-003) | `classify_intent("What is the waiting period for PED in Activ Assure?")` returns `"qa"` and `should_route_to_fact_find` returns `False` on empty profile. | Headline 30% gold-QA accuracy bug — direct QA was force-routed to fact-find. |
| KI-018 | `CONTEXT_DEPENDENT_INTENTS = {"recommendation", "comparison"}` — no `"qa"`. | Adding `"qa"` re-introduces the headline bug. |
| KI-023 | `FACT_FIND_TRIGGERS` uses word-boundary regex, not substring. | Stops `"hi"` firing on `"which"` / `"this"` / `"high"`. |
| ADR-026 / KI-025 (legacy; superseded by [ADR-031](../70-docs/60-decisions/ADR-031-sticky-primary-election.md) / KI-080) | `_balanced_brain_chain(..., groq_first_probability=0.5)` lands Groq-primary between 400 and 600 of 1000 seeded calls. | Catches the shared-counter pathology where every brain call lands on one provider. Test retained as a bypassed-path regression pin. |

## Running

```bash
# Unit (in-process, no API)
.venv/bin/python -m unittest tests.test_routing_regression -v

# Live (against deployed HF Space)
python tests/live_verify.py

# Live (against any other deploy)
TARGET_URL=http://localhost:8000 python tests/live_verify.py
```

## Retired tests (KI-167, 2026-05-15)

- `test_fact_find_loop_break.py` — retired because the loop-break + `_canonical_fallback` architecture was replaced by the LLM-driven `backend/sales_brain.py`. The test pinned internals (`_canonical_fallback`, `_MAX_FAILED_ATTEMPTS`, `FIELD_TO_QUESTION_ID`) that no longer exist after the `fact_find_brain.py` rip-out. The equivalent behavioural invariant (bot doesn't re-ask answered slots) is now covered end-to-end by the gold-QA accuracy eval and the multi-persona stress audit.

## Related

- Root `CLAUDE.md` § Routing invariants — the four lines this folder protects
- `80-audit/ENTERPRISE_AUDIT.md` D-003 — full incident report for KI-018
- [ADR-026](../70-docs/60-decisions/ADR-026-provider-load-balancing.md) — load-balance behaviour pinned by `TestProviderLoadBalancing`
- `eval/run.py` — the broader 96-Q accuracy eval. ⚠️ Pending re-port to the single-brain architecture (hard-guarded from running; see its module docstring).
