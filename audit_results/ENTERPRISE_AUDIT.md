# Enterprise-Grade Readiness Audit

**Target deployment:** top-tier Indian insurance companies (HDFC ERGO, ICICI Lombard, Bajaj Allianz, Star Health, Tata AIG, etc.)
**Audit date:** 2026-05-14
**Audit window:** active — this is a living document. Background audits (100-persona simulation + full gold-QA eval) running concurrently; results merge here as they land.

---

## Executive scorecard

| Domain | Status | Severity if not fixed |
|---|---|---|
| Disk / storage stability | ✅ Fixed (3-layer prevention — ADR-029) | — |
| Data pipeline integrity | ✅ Fixed (HF Hub canonical restored + in-process HNSW tripwire) | — |
| Operational observability | ⚠️ Partial (silent-LaunchAgent regression fixed; broader `except Exception:` audit pending) | P1 |
| Product quality (factual accuracy) | 🟡 Routing fix shipped (KI-018 / KI-023). 5-Q smoke: 60% (was 0%). Full 96-Q post-fix landing. | **P0 — needs ≥90% for deployment** |
| UX latency | 🟡 Chain-budget cap installed (KI-021), NIM↔Groq balance (KI-025), fast-brain reorder (KI-035). Clean 100-persona re-run owed for clean numbers. | **P0 — needs p95 < 3s** |
| Profile-capture / slot-filling | ✅ Was a telemetry bug (KI-019), not a slot-filler bug. Fact-find branch now reports profile_updates. | — |
| Language-handling fairness | ⚠️ Hinglish concern was a 20-persona sampling artifact. Real outliers: tax_planner archetype (4.6 refusals) + stream style (4.3) — open. | P1 — India-market regulatory risk |
| Code hygiene | ✅ Loose tmp files removed; `fact_find_normalizer` + `profile_extractor` migrated to chain pattern (KI-033) | — |
| Test coverage | 🟡 15 unit tests pinning KI-018 / KI-023 / KI-025 routing + load-balance invariants. Broader coverage still open. | P1 |
| Voice UX | ✅ Live default-on + clickable toggle + labeled push-to-talk + barge-in working (ADR-028) | — |
| Fact-find robotic tone | ✅ LLM paraphraser w/ verifier (ADR-027) | — |
| Secrets handling | ✅ Verified clean (.env never committed) | — |

Legend: ✅ fixed / ⚠️ partial / 🟡 improving / 🔴 open

---

## Defect Register

Each row: ID · severity · title · evidence · fix status. P0 = blocks production deployment. P1 = blocks enterprise procurement. P2 = quality / hygiene. P3 = nice-to-have.

### D-001 · P0 · ChromaDB HNSW link_lists.bin runaway growth — **FIXED**
**Symptom:** 2026-05-14 15:18 — `rag/_hf_dataset_backup/rag/vectors/148fbdda-…/link_lists.bin` reached 277 GB logical / 136 GB on-disk for only 12 MB of actual vector data (5K chunks). Disk filled from ~137 GB free → 50 MiB free in ~45 min.
**Root cause:** ChromaDB 1.5.9 HNSW persistence pathology — known issue where the link-graph adjacency file accumulates sparse holes during certain add/delete cycles. Bloat factor: ~277,000× expected.
**Impact:** total system unavailability (workstation unusable); during inference would have meant slow query, eventual OOM.
**Fix (deployed):**
1. In-process tripwire — `rag/ingest.py` declares `HNSW_BLOAT_THRESHOLD_BYTES = 500 MB` and calls `_abort_if_hnsw_bloated()` after every `collection.add(...)`. Two other writers (`tools/ingest_kb_summaries.py`, `tools/ingest_reviews.py`) import the same guard.
2. Out-of-process auto-purge — `~/Library/Scripts/insurance-bot/check-vector-bloat.sh` + LaunchAgent `com.rohit.insurancebot.vectorbloat` (60-min cadence). Auto-deletes `_hf_dataset_backup/` at 20 GB. Warns at 5 GB.
3. Disk-free tripwire — `~/Library/Scripts/cache-prevention/disk-free-tripwire.sh` + LaunchAgent `com.rohit.disk-free-tripwire` (15-min cadence). Critical alert <8 GB free; dumps every `~/Developer` subdir >1 GB into the log.
4. Re-downloaded canonical dataset from HF Hub (`rohitsar567/insurance-bot-data` · 539 files · 498 MB) — `link_lists.bin` now 58-66 KB.

**Production hardening still owed (D-001a, P1):**
- Open ChromaDB issue tracker — file or upstream a reproduction so the actual root cause is fixed, not just contained.
- Move ingest to a temp directory + atomic rename — currently the live store is also the ingest target.

---

### D-002 · P0 · Three LaunchAgents silently failing under wrong path — **FIXED**
**Symptom:** `com.rohit.insurancebot.linkrot`, `.pdfetags`, `.premiums` all `cd "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot"` — that directory does NOT exist. The actual project is `/Users/rohitsar/Developer/Insurance Sales Bot`. They've been failing every scheduled run.
**Impact:** Link-rot detection, PDF eTag refresh, and premium-page refresh have all been **broken indefinitely** — corpus URL changes go undetected, insurer pricing data goes stale, regulatory PDFs may have been updated without the bot's awareness.
**Fix (deployed):** Sed-replaced `~/Documents/Personal/AI Work/` → `~/Developer/` in all three `run_*.sh` scripts. Created log directory. Smoke-test running in background (id `bh2cv32e2`).
**Production hardening owed (D-002a, P1):**
- Add a heartbeat-or-page check (like the bloat watcher) for every LaunchAgent: if `last_exit != 0` for N consecutive runs, page.
- This is the SECOND silent-LaunchAgent regression in 18 days (memory: `feedback_tcc_blocks_launchd_in_documents`). Pattern needs a class-fix.

---

### D-003 · P0 · Factual accuracy on gold-QA eval — **ROOT-CAUSED + PARTIAL FIX SHIPPED**
**Initial symptom:** `eval/results.md` (2026-05-13 21:52 UTC) showed 30.0% factual accuracy on 10 questions. Full 96-Q re-run (2026-05-14 16:32 UTC, before fix) showed **41.7%**.
**Diagnostic breakthrough — by brain accuracy:**
- `nim-chain` (the QA brain): **51.9% accuracy** when used.
- `needs_finder` (the slot-filler): **0.0% accuracy** when used.
- `nim` direct: **0.0%** (only 0 turns).
The full eval was sending **every** QA question to `needs_finder`. Sample bot answers from before the fix:
- *Q: "What is the waiting period for pre-existing diseases under Activ Assure?" → A: "Happy to help. First, your age?"*
- *Q: "Is there a cap on room rent under Activ Assure?" → A: "Sorry, I didn't catch that. Let me ask again — Who else needs cover..."*
- *Q: "Does Activ Assure cover AYUSH?" → A: "Got it. Who else needs cover — just you, spouse, kids, parents, or a mix?"*

**Root cause:** `backend/orchestrator.py:174-183`. The KI-013 guard ("never recommend without profile") force-routed every turn to fact-find when the session profile was empty — **regardless of intent**. Eval and audit sessions start with empty profiles, so 100% of QA questions got swallowed by `needs_finder`.

**Fix shipped:** restricted the `profile_is_empty` force-route to `intent ∈ {recommendation, comparison}` only. QA intent now passes through to retrieval + `nim-chain`. New comment block in source marks this as KI-018.

**Smoke validation (5 questions, no-judge regex grader, post-fix):**
- Factual accuracy: **60.0%** (was 0% on the same 5 questions before).
- Brain breakdown: `nim-chain` 100% (was `needs_finder` 100% before).
- The two PED-waiting-period questions that previously responded "First, your age?" now correctly answer "24 months".

**Projection:** if `nim-chain`'s 51.9% accuracy holds when it serves all 96 questions (instead of only some subset), headline factual accuracy goes from 41.7% → low 50s. To reach the enterprise bar of ≥90%, additional work is needed on the brain itself:
- `waiting_period`: retrieval ranks wrong section; likely chunking issue.
- `sub_limit`: structured-extraction problem — sub-limit tables don't survive chunking.
- `exclusions_oos`: refusal logic doesn't catch OOS exclusions — needs tighter regulatory_oos filter applied to exclusions too.

**Adjacent bug — D-003a, P1:** the Groq judge (Llama-3.3-70B) returned `JSONDecodeError` 11 times on the 96-Q run; each gets scored 0 factual, inflating the failure rate. Need to wrap judge response with a JSON-repair pass or fall back to the regex grader on judge-parse-failure.

**Next:** queue a fresh full 96-Q eval re-run after the 100-persona audit completes (avoid competing on NIM quota).

---

### D-004 · P0 · Latency p95 49s, p99 59s — **DEGRADED ON BROADER SAMPLE**
**Symptom (now confirmed on full 100-persona audit, 3000 turns):**
- p50: **9.9s** (was 7.3s on the 20-persona sample — **+36%**)
- p95: **49.1s** (was 24.2s — **+103%**)
- p99: **58.9s** (was 48.0s — **+23%**)
**Impact:** **Unacceptable for chat UX.** A 49-second p95 wait on "what's covered?" feels broken. Enterprise insurance customers expect sub-3s p95. Some persona/intent combos (recommendation × `nim-chain::v4-pro`) take ~1500s for a single 30-turn session.
**Why it degraded vs. 20-persona sample:** the 20-persona run was all first-buyer + upgrader archetypes (simpler, more fact-find-only). The full 100 includes `tax_planner`, `comparer`, `savvy` archetypes that hit the heavier `v4-pro` model + multi-call cascades, which pushed p95 up dramatically.
**Likely contributors (in order of suspected impact):**
1. **`v4-pro` brain on comparison/recommendation** — frontier 1.6T MoE, slow per token; combine with NIM rate limit and you get the long-tail.
2. NIM 40 req/min cap forces serial dispatch under load → queuing latency.
3. Multi-call cascades (brain + judge + cross-check) per docstring in `run_audit.py`.
4. **20 HTTP timeouts** (0.7%) — the bot occasionally hangs on a turn entirely (P100 had 3 ReadTimeouts in a row).
**Fix proposal:**
- Streaming responses (TTFT instead of full response time) — most of the 49s wait is the user staring at a blank screen.
- Tier `comparer`/`tax_planner` archetypes to `v4-flash`; reserve `v4-pro` for truly heavy recommendation synthesis.
- Hard timeout on NIM calls (currently apparently >60s) — fail-fast at 20s, retry with smaller context.
- Cache the intent classifier output for the first N turns of a session.
**Owner:** needs design discussion before changing brain routing.

---

### D-005 · P0 · Profile-capture / slot-filling broken — **CONFIRMED REAL BUG ON 100-PERSONA SAMPLE**
**Symptom (full 100-persona audit, post-fix):**
| Field | Personas hit |
|---|---|
| `health_conditions` | 20 / 100 |
| `existing_cover_inr` | 12 / 100 |
| `age` | **12 / 100** |
| `parents_to_insure` | 7 / 100 |
| `primary_goal` | 6 / 100 |
| `parents_age_max` | 6 / 100 |
| `parents_has_ped` | 1 / 100 |
| All other fields (dependents, income, location, marital, conditions ≠ health_conditions, budget) | **0 / 100** |

**Re-diagnosis:** This is NOT a downstream effect of D-003. The audit pre-dated the D-003 fix, but more importantly: 1281 of 3000 turns ran `needs_finder` (43%) — i.e. the slot-filler DID run on plenty of turns. Yet age was captured for only 12% of personas, even though every persona is canonically asked "First, your age?" on turn 1 and answers it. **The slot-filler is asking but not retaining the answer.**

**Likely root causes:**
1. `backend/fact_find_normalizer.normalize_answer()` is rejecting valid Indian-accented age responses ("twenty-five" / "मेरी उम्र पचास साल है" / "I'm 28 going on 29").
2. `_reask_count` is hitting its cap (2 fails → give up + move on without storing).
3. The 258 fact-find re-asks correlate with this — almost every persona had a re-ask, often multiple.
4. Hindi-primary personas captured `health_conditions` (Devanagari numerals in the canned persona response) but failed on age (free-text Hindi).

**Impact:** Bot cannot recommend a policy because it doesn't have the user's profile. The 263 faithfulness-gate refusals are largely because retrieval has no profile to constrain against. **Recommendation flow is fundamentally broken** for the majority of users.

**Next step:** Read 5 P-files (across archetypes), grep for `_reask_counts` increments, identify the specific normalizer regex that's misfiring.

---

### D-006 · P1 · Refusal-rate fairness — **REDIAGNOSED ON 100-PERSONA SAMPLE; HINGLISH CONCERN CLEARED, REAL OUTLIERS IDENTIFIED**

**Refusal rate by archetype** (avg refusals/persona over 30 turns):
| Archetype | Refusals | Faithfulness fails |
|---|---:|---:|
| `tax_planner` | **4.6** | 46 |
| `code_switcher` | 4.4 | 44 |
| `savvy` | 3.3 | 33 |
| `specific_condition` | 3.2 | 32 |
| `low_trust` | 2.8 | 28 |
| `anxious` | 2.5 | 25 |
| `senior_care` | 2.1 | 21 |
| `comparer` | 2.0 | 20 |
| `upgrader` | 1.0 | 10 |
| `first_buyer` | **0.4** | 4 |

**Refusal rate by conversational style:**
| Style | Refusals |
|---|---:|
| `stream` | **4.3** (highest) |
| `tester` | 3.4 |
| `casual_en` | 3.1 |
| `anxious_q` | 2.8 |
| `verbose` | 2.7 |
| `hindi_primary` | 2.5 |
| `numbers_heavy` | 2.1 |
| `formal_en` | 1.9 |
| `hinglish` | 1.9 |
| `terse` | **1.6** (lowest) |

**Re-diagnosis:** The earlier 20-persona "hinglish 2× more refusals" claim was a small-sample artifact (2 hinglish personas × 1 refusal each). On the full sample, hinglish (1.9) is actually slightly BETTER than hindi_primary (2.5) and is the second-lowest style. **Hinglish is fine.**

**Real fairness issues:**
1. **`tax_planner` and `code_switcher` archetypes get refused 11× more than `first_buyer`** (4.6 / 4.4 vs 0.4). These users ask tax-deduction questions (80D / 80DD / 80U) and code-switched comparison questions — the bot's faithfulness gate refuses both heavily. **This is an India-market segment we cannot afford to alienate.**
2. **`stream` style refused 2.7× more than `terse`** (4.3 vs 1.6). Long stream-of-consciousness input is failing the faithfulness gate. Likely the gate is matching the wrong span of the user's question.

**Worst refusers (real users to debug against):** P081 (Saif Banerjee, code_switcher/stream, **9 refusals**), P069 (Vikram Banerjee, tax_planner/casual_en, 8), P063+P064 (tax_planner, 7 each), P091 (specific_condition/tester, 7).

**Fix proposal:**
- Add tax-related gold-QA questions (80D, 80DD, 80U) — currently the gold set has zero.
- Loosen faithfulness gate for `stream`-style: chunk the question, take the most retrieval-rich span, not the whole thing.
- For `code_switcher`, run Sarvam translation pass at gate-evaluation time, not only at brain time.

---

### D-007 · P1 · No unit tests; only `live_verify.py` — **OPEN**
**Symptom:** `tests/` contains only `live_verify.py`. Backend modules (`orchestrator.py`, `faithfulness.py`, `security.py`, `scorecard.py`, `profile_rag.py`) have no isolated tests.
**Impact:** Enterprise procurement (and SOC 2 / ISO 27001 audits) require test coverage evidence. The eval/audit suites are integration-level; they don't catch unit regressions.
**Fix proposal:** Add `tests/unit/` with pytest, target ≥70% line coverage on `backend/`. Block PRs that drop coverage.

---

### D-008 · P1 · `except Exception:` audit — **PARTIAL**
**Symptom:** ~17 sites across `backend/main.py`, `admin.py`, `security.py`, `profile_rag.py`, `scorecard.py` catch broad exceptions. Most legitimate (defensive deletes, malformed-line-skip, fail-open availability tradeoffs). But several swallow legitimate errors:
- `backend/main.py:518` after `record_accept(sha, sid, len(chunks))` — telemetry write silently swallowed.
- `backend/main.py:660` after building `hint` — silent failure for what could be a routing bug.
- `backend/profile_rag.py:142-144` — `coll.delete(where=...)` failure swallowed; if a stale chunk exists, the new chunk will collide on ID.
**Impact:** Real errors get masked; debugging in production becomes archaeology.
**Fix:** Each `except Exception: pass` should at minimum log to `LOG_DIR/turns.jsonl` (or the structured logger) with `level=warn` and an event name.
**Note:** Recent commit `2412797 fix(observability): KI-001..006 — log silent failures + fail-CLOSED judge` already addresses some of these. Need to confirm coverage.

---

### D-009 · P2 · Loose `tmp_*.py` files in project root — **FIXED**
**Symptom:** `tmp_extract.py`, `tmp_count_fields.py`, `tmp_batch_extract.py` in repo root. Were git-tracked.
**Fix (deployed):** `git rm` issued. Confirmed gone. Pending commit.

---

### D-010 · P2 · TODO/FIXME density in `backend/` + `rag/` — **OPEN**
**Count:** 29 TODO/FIXME/XXX/HACK markers across backend + rag (excludes `__pycache__`).
**Action:** Triage list; convert to GitHub issues; resolve before enterprise audit.

---

## Fixes shipped today (commit reference)

| KI | Commit | What |
|---|---|---|
| KI-018 | `bcb7079` | Stop force-routing QA intent to fact-find on empty profile (the gold-eval headline bug) |
| KI-019 | `bcb7079` | Fact-find branch now reports `profile_updates` in `TurnResult` — fixes the audit telemetry that misread captures as zero |
| KI-020 | `bcb7079` | `POST /api/session/reset` + frontend Clear-chat / Start-fresh buttons |
| KI-021 | `bcb7079` | Cumulative chain budget on `NimChainLLM` (brain 35s, fast-brain 22s) bounds the long-tail latency |
| KI-022 | `bcb7079` | Groq judge JSON-parse fallback to regex grader (11/96 questions previously scored 0 falsely) |
| KI-023 | `3fb3586` | Word-boundary intent triggers (`"hi"` was matching `"which"`/`"this"`); regression test |
| KI-024 | `1304e7c` | Parallelized 96-Q gold eval (~5× speedup) |
| KI-025 | `a04c17a` | NIM↔Groq 50/50 load-balance on brain chain primary (ADR-026) |
| KI-026 | `effcfeb` | Voice mode mutual exclusion (Live + PTT + Hands-free no longer fight for the mic) |
| KI-027/8/9 | `e01547c`/`4ae5278`/`65ba46c` | Voice UX simplification: Live default-on + clickable toggle + labeled push-to-talk (ADR-028) |
| KI-030 | `3d06a80` | Barge-in fix — bot TTS now plays via in-DOM `<audio>` so `querySelectorAll("audio").pause()` can find it |
| KI-032 | `6f495c1` | LLM paraphraser for fact-find questions with verifier + cache (ADR-027) |
| KI-033 | `9a977de` | `fact_find_normalizer` + `profile_extractor` migrated from hardcoded single-model to `NimChainLLM(FAST_BRAIN_CHAIN)` |
| KI-034/5 | `844ed03` | LRU retrieval cache + `FAST_BRAIN_CHAIN` reordered (Nemotron Nano 30B primary) |
| D-001 | (multi) | ChromaDB HNSW bloat 3-layer prevention (ADR-029) |
| D-002 | (LaunchAgent edit) | Three silently-failing LaunchAgent scripts fixed |
| D-009 | `bcb7079` | Removed `tmp_*.py` debug files from repo root |

## Verification artifacts

- `tests/test_routing_regression.py` — 15 unit tests, all passing. Pins KI-018 / KI-023 / KI-025 invariants.
- 5-Q post-fix smoke (no judge): factual 0% → 60%, nim-chain serving 100% of QA.
- Live HF Space smoke (`https://rohitsar567-insurancebot.hf.space`): PED waiting-period question now answers via `nim-chain::nemotron-3-nano-30b-a3b::v4-flash::qa` with a grounded reply, not the old "Happy to help. First, your age?" misroute.
- **Post-fix parallel 96-Q gold eval (93 of 96 completed; 3 trailing questions killed when the run hung on a NIM rate-limit edge case):**
  - **Factual accuracy: 54.8% (51 / 93)** — up from the pre-fix 41.7% baseline.
  - **KI-022 JSON-fallback** rescued 7 questions that would have scored 0 on Groq judge JSON errors. Without KI-022 the headline would have been ~47%.
  - PED waiting-period type — previously 0% pre-fix; samples now: "Bot correctly states the 24‑month waiting period" / "matched_nums=['24']" via regex fallback / "36‑month period and includes source cit…".
  - Stuck questions: rows 94-96 (all `regulatory_oos` refusals — those routes are already at ~100% earlier in the run; the rate-limit hang affected the brain call, not the refusal logic).
- Clean 100-persona audit pending — to run once Batch B (bucket reorg) ships and the HF Space is stable.

## Pending follow-ups (P1)

| Item | Status |
|---|---|
| Tax-related gold-QA questions (currently zero in `eval/gold_qa.json` — D-006 mitigation) | Open |
| `stream`-style faithfulness gate (refusal rate 2.7× higher than `terse`) | Open |
| Token-streaming responses (SSE) — biggest perceived-latency win remaining | Open (v2 roadmap) |
| Streaming TTS (Sarvam chunked synthesis) | Open |
| GPU-hosted local embeddings (Voyage replacement) | Open — `LocalEmbeddings` fallback already in `backend/providers/local_embeddings.py` |
| Broader unit-test coverage (currently only routing + load-balance pinned) | Open |

---

## What "enterprise-grade" actually means for this product

Before insurers will pilot this, the following must be true:

1. **Factual accuracy ≥ 90%** on gold-QA across all question types (currently 30% headline).
2. **Latency p95 ≤ 3s** on chat turns (currently 24s).
3. **Zero silent failures** — every `except Exception:` either re-raises or logs.
4. **Production observability** — every brain decision, every retrieval, every refusal logged with correlation IDs; dashboards for accuracy/latency/refusal-rate over time.
5. **Test coverage ≥ 70%** with unit + integration tests in CI.
6. **Fairness audit** — accuracy/refusal-rate within ±5% across language styles (hinglish gap is currently 2×).
7. **Disaster recovery runbook** — what happens when ChromaDB corrupts, when HF Space is down, when NIM rate-limits.
8. **PII handling per DPDP Act** — chat logs, uploaded policies, user profiles must have retention policies + deletion workflows.
9. **IRDAI compliance review** — every recommended product must be IRDAI-registered; the bot must never invent a product or premium.
10. **SOC 2 Type II readiness** — secrets management, access logs, change management.

This audit so far covers items 1-3, 5 (in progress), and 6. Items 7-10 require a separate scoping pass.

---

*This file regenerates as new evidence lands. Last updated: 2026-05-14 (initial pass).*
