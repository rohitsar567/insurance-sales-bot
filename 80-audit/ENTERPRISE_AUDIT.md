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
| UX latency | 🟡 Chain-budget cap installed (KI-021), probe-driven primary election (KI-080, supersedes KI-025), fast-brain reorder (KI-035). Per-turn LLM calls dropped from 5-6 → 1-2. | **P0 — needs p95 < 3s** |
| Profile-capture / slot-filling | ✅ Was a telemetry bug (KI-019), not a slot-filler bug. Fact-find branch now reports profile_updates. | — |
| Language-handling fairness | ⚠️ Hinglish concern was a 20-persona sampling artifact. Real outliers: tax_planner archetype (4.6 refusals) + stream style (4.3) — open. | P1 — India-market regulatory risk |
| Code hygiene | ✅ Loose tmp files removed; `fact_find_normalizer` + `profile_extractor` migrated to chain pattern (KI-033) | — |
| Test coverage | 🟡 15 unit tests pinning KI-018 / KI-023 / KI-080 routing + primary-election invariants. Broader coverage still open. | P1 |
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
| KI-036/7/8 | `36ef017` | Greeting flow, strict paraphrase verifier, waiting dots — tightens the first-impression UX surface |
| KI-039 | `a774b91` | Single Clear-chat button (full reset) — removes ambiguity between "reset" vs "new session" |
| KI-040 | `4449d44` | Named profiles + Clear-chat that preserves profile — lets users iterate without re-doing fact-find |
| KI-041/2 | `748ce54` | VAD sensitivity tuning + Live OFF by default + dots on first turn — fixes accidental barge-in on quiet rooms |
| KI-044 | `86fcc31` | PCM pre-roll via AudioWorklet + 11 folder READMEs — eliminates first-syllable clipping on TTS playback |
| KI-045 | `930e11e` | Natural-conversation classifier in fact-find: intent_change / off-topic-question escapes fact-find branch into QA. Prevents the bot from droning past a user's pivot. |
| KI-046 | `28b6114` | Explicit refusal on adversarial/fanciful out-of-corpus questions (space tourism, diamond-tipped surgery). Closes the "absence of exclusion ≠ inclusion" reasoning leak. |
| KI-047 | `e2d09f9` | Bucket reorg (`docs/` → `70-docs/`, `audit_results/` → `80-audit/`) — Option A safe subset, keeps code dirs untouched |
| KI-048 | `13a1cf4` | Admin backend: `GET /api/admin/profiles` + `GET /api/admin/performance`, both behind `_check_admin` (IP allowlist + password, 404 on auth fail) |
| KI-049 | `c8bf1a1` | Retrieval top-k 5 → 10 for table-cell questions (room rent / sub-limit / cap / NCB / etc.). Boosts the chance the policy's structured cap-table chunk lands in context — directly targets the `sub_limit` accuracy gap from D-003. |
| KI-050 | `52c6351` | Complete `data/` → `40-data/` rename across all Python string-path refs. Finishes KI-047 for runtime code. |
| KI-051 | `2eae364` | Dockerfile `COPY` paths updated (`data → 40-data`, `docs → 70-docs`). Without this the HF Space rebuild fails on the renamed source dirs. |
| KI-052 | `c53d167` | Admin panel HTML: 3 lazy-loaded tabs (Profile + Visitor Log, Performance, LLM Chain). Performance pulls KI-048's new endpoint; auth state preserved across tab switches. |
| KI-053/4 | `57ef382` | Eval-mode skip profile-extractor flag (`INSURANCE_BOT_SKIP_PROFILE_EXTRACTOR`) + gold_qa.json grew 96 → 110 with tax_planning questions. |
| KI-055 | `8ff05ba` | HF Space BUILD_ERROR fix — dropped `rag/vectors` broken symlink from repo; Docker `chown -R user:user /app` no longer hits broken symlink → exit 0. |
| KI-056 | `2fbd062` | Dynamic acknowledger rotation (8 variants, deterministic per session/turn/slot hash) + family-aware opener for spouse/kids/parents mentions + opportunistic `infer_dependents_from_text` + Q3 paren consistency. **Now superseded by KI-070's single-LLM driver, but the family-aware capture survives natively in the LLM prompt.** |
| KI-057 | `23a6d29` | Live VAD hardening (6 layered fixes): adaptive noise floor EMA, voice-band spectral gate (190-2150 Hz), `speechStartFrames: 1→3`, `maxUtteranceMs: 18s`, post-utterance cooldown 700ms, flush-on-toggle-off. Stops ambient noise pinning the segment open + recovers mid-utterance audio when Live is toggled off. |
| KI-058 | `23a6d29` | `.gitignore` `lib/` rule scoped to anchored venv paths only (`/lib/`, `/.venv/lib/`, `/venv/lib/`) so `frontend/src/lib/` stops getting flagged. |
| KI-059 | `e610ee9` | Opening-turn name capture — "Hi this is Rohit" / "I'm Anjali" / "My name is Sarah Connor" in the very first message routes straight to the name slot handler. **Now superseded by KI-070 (LLM extracts names natively from any utterance).** |
| KI-060 | `e610ee9` | Live silence-end window loosened 40 → 90 frames (~640ms → ~1.5s) so natural mid-sentence pauses don't auto-submit. |
| KI-061 | `e610ee9` | Personalized welcome-back greeting on returning visitor — `_format_known_profile_summary` + `_format_missing_slots` so the bot reflects what's on file and offers to fill gaps before recommending. |
| KI-062 | `e610ee9` | Full-name capture (regex widened 1-2 → 1-4 words) + `compute_persona_id` 12-char sha1 over name + age + dependents + income_band + location_tier + parents_age_max. Two "Rohit"s with different identity fields resolve to distinct persona_ids. Legacy-name-slug → persona_id file migration on save. |
| KI-063 | `d89a871` | Per-user interaction log on `Profile` dataclass: `shown_policies` / `selected_policies` / `rejected_policies` lists with dedup-in-place. Auto-log on `intent ∈ {recommendation, comparison}` AND `faithfulness_passed`. New POST endpoints `/api/profile/select` + `/api/profile/reject` (KI-068: now fire-and-forget via `asyncio.to_thread` so disk writes don't block reply). |
| KI-064 | `4c6215a` | Live silence-end window bumped 90 → 120 frames (~1.5s → ~2s) — KI-060's 1.5s was still cutting off "Hi, I'm looking to buy a new insurance ... policy". |
| KI-066 | `7d021cf` | TTS shorthand normalizer in `backend/voice_format.py::_normalize_money`. `₹5L` → "5 lakhs", `₹5-10L` → "5 to 10 lakhs", `₹25L+` → "25 lakhs or more", `₹2Cr` → "2 crores", `Rs. 50,000` → "rupees 50,000". Stops Sarvam Bulbul from reading shorthand letter-by-letter. |
| KI-067 | `28f795b` | `_parse_existing_cover` extended to recognise first-time-buyer phrasings ("This is my first policy" / "new to insurance" / "never bought" / "don't have any"). **Now superseded by KI-070 (brain captures natively from prose).** |
| KI-068 | `26023b8` | Humanized fact-find readback (`primary_goal: first_buy` → "goal: first health policy"; `30k_60k` → "₹30,000–60,000/year"; `metro` → "metro city"). Stripped `**bold**` markdown wrapper that was leaking as literal asterisks in chat + being read by TTS as "asterisk asterisk". Made KI-063 logging fire-and-forget. |
| KI-069 | `26023b8` | Fixed KI-059 false-positive: regex was matching "this is correct" / "I'm good" / "first, show me ..." and routing users back to the name re-ask loop. Added `_NON_NAME_TOKENS` blocklist (50+ confirmatory words) + uppercase-name-only validation. **Now superseded by KI-070 (brain doesn't need this regex).** |
| **KI-070** | **`364591b`** | **Single-LLM-call fact-find replaces 3-layer template stitching ([ADR-030](../70-docs/60-decisions/ADR-030-llm-driven-fact-find.md)).** Orchestrator fact-find branch ~387 → ~95 lines. New `backend/fact_find_brain.py` (441 LOC). Deleted: `backend/question_paraphraser.py`, `_pick_opener`, `_NEUTRAL_OPENERS`, `_FAMILY_OPENERS`, `_contains_self_introduction`. Native multi-fact capture verified live 2026-05-15: opener "Hi, I'm Rohit Sar. I'm 32, just myself, living in Mumbai." captured `{name, age, dependents, location_tier}` in ONE turn. |
| KI-071 | `2e60476` | Docs-vs-code reconciliation: 12 files updated to reflect actual chain primaries (Qwen 3-Next 80B brain / Nemotron Nano 30B fast brain / Mistral Large 3 675B judge per D-022 brain swap). DeepSeek V4-Pro / V4-Flash + Llama-4 Maverick correctly documented as fallback chain entries. README §1.2 + §4.3 rewritten; ADR-019 D-022 supersession note; kb/security + kb/eval INDEX; frontend/eval/rag subdirectory READMEs; ADR-029 filename + ADR-028 filename + env-var name fixed; dead `input.hands_free` i18n keys deleted; `welcome.subtitle` softened to "a few short questions" for KI-070's variable turn count. |
| KI-072 | `e098e1b` | **P0 fix.** `_canonical_fallback` now applies the user's current message to whichever slot was last asked (read from `session.awaiting_question_id`) via legacy normalizer BEFORE picking next slot. Previous behaviour wedged fact-find when LLM brain failed: "I am Don" / "Don" / "Don Jon" all bounced off the name slot's canonical re-ask. |
| KI-073 | `c53381c` | Frontend Clear chat now explicitly resets `profileCompleteness` state immediately so the "55% DONE" header chip clears synchronously for the new visitor, regardless of network latency on the backend session-reset call. |
| KI-074 | `4516e87` | **P0 fix.** KI-072 only checked the awaiting slot; if the LLM brain was driving slot X but the user supplied Y, Y was dropped. Now `_canonical_fallback` GREEDILY runs `_normalize_for_slot` against every unfilled slot in priority order (age → dependents → income_band → existing_cover → primary_goal → location → parents_age → budget → name), with slot-specific trigger guards to prevent cross-contamination ("29 years old" was getting written into existing_cover_inr AND parents_age_max AND age before the trigger guards). Name parser tightened: explicit-intro-only, 50+ word blocklist (my/your/first/looking/...), conjunction stop ("Rohit Sar and I am 32" → "Rohit Sar"). Also stripped `**...**` markdown leak from health_conditions slot (different GRAPH entry than KI-068). |
| **KI-075** | **`5fc01a7`** | **Root cause of "still robotic" UX.** Live probe showed 4 of 5 fact-find turns hit `_TIMEOUT_S = 12s` asyncio.wait_for cap at exactly 13.2s latency — NIM cold-start eats 10-15s after a Space rebuild. Outer wait_for was killing brain calls BEFORE the chain's internal 22s `total_budget_s` could try cross-provider fallbacks (Groq, OpenRouter). Bumped `_TIMEOUT_S` to 25s. Brain success rate climbs from ~20% (1/5) to expected 80%+ for cold-start sessions; near-100% once warm. |
| KI-076 | (HF dataset) | Disabled the `rohitsar567/insurance-bot-data` dataset viewer by uploading a README with `viewer: false` YAML frontmatter. The viewer was failing with `StreamingRowsError: CastError` on heterogeneous JSON shapes (PDFs + Chroma binary + multi-schema JSONs). Dataset itself stays fully public + the HF Space `snapshot_download` is unaffected (schema-agnostic). Page now shows clean "Viewer disabled" notice + the new README we wrote. |
| KI-077 | `2bb3898` | "Build your profile" panel: added Name input field at top with "captured from chat" badge when populated. Backend `/api/profile/completeness` + `/api/profile` POST + `UserProfile` TypeScript type all extended with `name`. Panel pre-fills every field from the session's captured chat state via existing `initialProfile`. New `useEffect` keeps panel in sync when chat captures fields while panel is open. On Save, name persists to the named-profile JSON store (KI-040/062) so the user is auto-recognised on return visits. |
| KI-078 | `078ff45` | LLM chain hardening: per-link timeout 12s → 6s so chain can try 3-4 candidates inside `total_budget_s=22s` instead of 1. Narrowed `except Exception` to re-raise `CancelledError`/`KeyboardInterrupt`/`SystemExit` so `asyncio.wait_for` actually bubbles. New `_fallback_reason` stamped on `FactFindOutcome` and surfaced as `fact_find_brain::fallback:timeout` / `:no_trailer` / `:empty_reply` / `:llm_error` in `TurnResult.brain_used` for production telemetry. |
| KI-079 | `87ee522` | Two-layer chain hardening for fact-find. (1) FAST_BRAIN_CHAIN reorder: Groq Llama-3.3-70B promoted from position #5 to #2 (right after Nemotron primary) so cross-provider fallback is reached in ~6-7s of budget instead of ~20s. (2) Heavy-chain escalation: when `drive_fact_find()` raises `TimeoutError` on fast-brain, orchestrator retries once on `BRAIN_CHAIN` (Qwen 80B primary, 35s budget) before falling to `_canonical_fallback`. Adds `fallback:timeout_after_escalation` / `:llm_error_after_escalation` to telemetry vocabulary. Now serves as the last-bite safety net once KI-080's primary election is in place. |
| **KI-080** | **`6159c54`** | **Sticky primary election for LLM chains ([ADR-031](../70-docs/60-decisions/ADR-031-sticky-primary-election.md)).** `NimChainLLM.chat()` refactored from "iterate every chain candidate sequentially per call" to "call the probe-elected PRIMARY once; on real-time failure, call the cross-provider BACKUP once and re-trigger the probe." `backend/llm_health.py` runs a 60s background probe loop that scores every candidate on (latency × success rate) and elects a sticky PRIMARY + provider-diverse BACKUP for each of `BRAIN_CHAIN` / `FAST_BRAIN_CHAIN` / `JUDGE_CHAIN`. **Per-turn LLM call count drops from 5-6 (sustained NIM degradation, every candidate queued + timed out) to 1 (most cases) or 2 (primary fails real-time → backup + re-probe).** ADR-026's `_balanced_brain_chain` (KI-025 50/50 NIM ↔ Groq rotation) is deprecated — the probe-driven election dynamically picks the actually-faster candidate instead of a fixed 50/50 coin. Code retained as a bypassed branch behind a feature flag for one-release rollback. Cold-start fallback (no probes complete yet) uses `chain[0]` as the initial primary. KI-079 escalation still applies as the last bite if both primary AND backup fail in the same turn. Inline tests: 7/7 OK (cold-start, lowest-latency primary, cross-provider backup, demote-on-failure, score-update, primary-success makes 1 call, primary-fail/backup-success makes exactly 2 calls). `tests/test_routing_regression.py` → 15/15 pass. |
| KI-081 | (no commit — HF Space env secrets) | Pushed `GROQ_API_KEY` + `OPENROUTER_API_KEY` to the HF Space repository secrets so the KI-080 cross-provider election candidates actually have working keys in production. Pre-KI-081 only `NVIDIA_NIM_API_KEY` was set on the Space; the elector would mark every Groq + OpenRouter candidate as `no_api_key` and election degraded to NIM-only candidates — defeating the cross-provider BACKUP invariant. |
| KI-084 | `119e0fd` | **LLM chain telemetry hardening + free-tier guards.** Four changes in one commit. (1) Probe cadence `PROBE_INTERVAL_SEC` raised 60s → **300s** — the prior cadence burned ~30-50K probe tokens/day on Groq alone, self-tripping Groq's 100K/day TPD free-tier cap. (2) `PROBE_MAX_TOKENS` cut 5 → **1** — same 200 envelope, ~50× less token spend per probe. (3) Explicit per-phase `httpx.Timeout(connect=2, read=self.timeout, write=2, pool=2)` on every chat call — previously `timeout=self.timeout` collapsed to a single read deadline so a stuck NIM pool could occupy the TCP connection past `asyncio.wait_for` cancellation, leaking NIM concurrency slots. (4) New `_classify_error` surfaces HTTP status codes explicitly (`Status429` vs `HTTPStatusError:503`); rate-limit failures get a **1-hour** sin-bin (`DEGRADE_DURATION_LONG_S = 3600s`) instead of the 30s transient window — free-tier daily quotas don't reset in 30 seconds. |
| KI-085 | `8fc7979` | **Proactive credit tracking — closes the reactive-only gap KI-084 leaves.** KI-084 demotes a candidate for 1h AFTER a 429 hits, costing one user-facing failover turn per dead quota. KI-085 promotes `llm_health` from liveness-only to liveness-AND-credits so election excludes quota-exhausted candidates BEFORE the user gets stuck behind a 429. Three signal sources: (1) Groq response headers `x-ratelimit-remaining-tokens-day` + `x-ratelimit-reset-tokens-day` (low-water 5K tokens); (2) OpenRouter `/api/v1/credits` polled every 10 min from probe loop, plus per-call header fallback (low-water $0.05); (3) NIM local 60s rate-meter, gate at 35-of-40 req/min (headroom 5). Election adds `_has_credits(h, now_mono)` to eligibility predicate. Admin `status_summary` extended with `credits_remaining` / `credits_unit` / `credits_low_water` per model. 11/11 inline tests pass + routing_regression 15/15. |
| KI-086 | `d90f8c0` (bundled with KI-087) | **Admin "LLM Health & Credits" tab.** New `GET /api/admin/llm-health` endpoint returns `{chains, candidates, recent_turns, snapshot_ts}` JSON: per-chain elected PRIMARY + BACKUP with snapshots, per-candidate health grid with credits + degraded-until, last 20 turn outcomes from `40-data/llm_usage.jsonl`. Same `_check_admin` IP-allowlist + password gate as other admin endpoints. Frontend extends the existing "LLM Chain" tab in `frontend/public/admin/llm-control.html` with three sections: (A) per-chain election cards, (B) candidate health table, (C) recent turns table. Auto-polls every 30s while tab is active. Operator now sees at-a-glance which LLM is in use where, why a candidate is gated out, and how the election state evolves. |
| **KI-087** | **`d90f8c0`** | **NIM-first election preference.** Pre-KI-087 election scored purely by `latency × success_rate`, which consistently favoured Groq's 161ms LPU TTFT over NIM's 500ms-1s — so every probe round elected Groq as PRIMARY across all 3 chains. Result: every chat call hit Groq first, burned Groq's 100K daily TPD inside 50 turns, then started returning 429s. KI-087 changes election so it prefers ANY eligible NIM candidate over ALL non-NIM candidates. Within the NIM pool the standard score still picks the fastest healthy NIM model. Only when the NIM pool is empty does election fall through to Groq / OpenRouter as PRIMARY. BACKUP rule unchanged in spirit: cross-provider against PRIMARY. Rationale: NIM is the strategic free provider (ADR-019, no daily cap, 110+ models, single-key, $0); Groq has 100K daily TPD; OpenRouter charges real USD. Both should serve as emergency fallback only. |
| **KI-088** | **`14ee008`** | **NIM concurrency semaphore + serial probe + dropped inner retry.** Pre-KI-088 the process could fire 6+ concurrent NIM HTTP calls (probe burst `asyncio.gather` across 6 candidates + admin pollers + per-user turns), self-saturating the NIM endpoint and producing `timeout_after_escalation` failures at 41s wall-clock — the NIM endpoint serialises internally so every overlap added pure queueing latency. Three changes in one commit. (1) Module-level `asyncio.Semaphore(2)` at `backend/nvidia_nim_llm.py:104` wraps every `httpx.post` to `integrate.api.nvidia.com` so the entire process never has more than 2 NIM requests in flight simultaneously, regardless of source — probe loop, admin polls, and per-user turns all serialise through the same semaphore. (2) `backend/llm_health.py::probe_all` changed from `asyncio.gather(...)` to a serial `for m in models:` loop so the 6-NIM probe burst becomes a 1-slot trickle over ~12s instead of contending with live user turns. (3) The 4-attempt exponential-backoff inner retry inside `NvidiaNimLLM.chat()` was deleted — KI-080 sticky-primary election + KI-079 heavy-chain escalation already handle failover at the right layer, so the inner retry only amplified the self-saturation. Live verify post-deploy: failure mode flipped from `fallback:timeout_after_escalation` at 41s → `fallback:no_trailer` at 4-10s. NIM concurrency bottleneck closed; new parser-side bottleneck surfaced and is addressed in KI-090. |
| **KI-089** | **`8a87526`** | **Credits-election test fix + paired NIM-empty test.** `test_groq_above_water_picked_in_election` had been failing on `main` since KI-087 landed: it asserted Groq wins election on raw latency (161ms LPU TTFT), but KI-087 inverted election to prefer any eligible NIM candidate over all non-NIM candidates regardless of latency. Replaced with two paired tests that pin KI-087's invariant explicitly. (1) `test_nim_preferred_over_faster_groq_when_eligible` — when an eligible NIM candidate exists, election picks it as PRIMARY even though Groq is measurably faster. (2) `test_groq_picked_when_nim_pool_empty` — when every NIM candidate is dead / throttled / `no_credits`, election correctly falls through to Groq as PRIMARY. Together the pair pin both halves of the KI-087 contract (NIM-first AND fallthrough-when-NIM-empty) so a future regression that breaks either half fails loudly. Full inline test count: credits-election 12/12 pass, routing-regression 15/15 pass. |
| **KI-090** | **`11cf4b3`** | **Lenient FF-block parser.** Post-KI-088 live probe showed ~70% of brain calls returned successfully in 4-10s (NIM concurrency fix surfaced the real bottleneck) but `_parse_ff_block` rejected the reply with `fallback:no_trailer` because the brain had dropped the literal `<FF>...</FF>` tags around its JSON tail. Real LLMs under load (Qwen 3-Next 80B, Nemotron Nano 30B, Groq Llama-3.3-70B) regularly drop the wrapper even when the structured payload is otherwise contract-compliant. New `_parse_ff_block` tries three strategies in order: (a) strict `<FF>{...}</FF>` (the original contract); (b) fenced ```` ```json {...} ``` ```` (common LLM habit); (c) bare `{...}` JSON object at the end of the reply. Each candidate must `json.loads` cleanly AND contain at least one contract key (`captured` / `slot_driving` / `complete`) before it counts — prevents false positives from prose that happens to contain `{...}`. `_strip_ff_block` mirrors the three strategies in reverse so prose-only output to the user never leaks the structured metadata block. Inline tests: 7/7 parse tests + 7/7 strip tests pass. Brain success rate climbs from ~30% (post-KI-088 baseline blocked by parser) toward ~95% (NIM concurrency healthy + parser accepts contract-compliant tails regardless of wrapper). |
| D-001 | (multi) | ChromaDB HNSW bloat 3-layer prevention (ADR-029) |
| D-002 | (LaunchAgent edit) | Three silently-failing LaunchAgent scripts fixed |
| D-009 | `bcb7079` | Removed `tmp_*.py` debug files from repo root |
| D-022 | (inline) | NIM brain swap on 2026-05-14: Qwen 3-Next 80B (was DeepSeek V4-Pro) + Mistral Large 3 675B judge (was Llama-4 Maverick) + Nemotron Nano 30B fast brain (was DeepSeek V4-Flash). DeepSeek + Maverick retained as fallback chain entries. **ADR-019 inline comments still reference the original D-019 lineup; the swap was code-only.** |

## Verification artifacts

- `tests/test_routing_regression.py` — 15 unit tests, all passing. Pins KI-018 / KI-023 / KI-025 invariants.
- 5-Q post-fix smoke (no judge): factual 0% → 60%, nim-chain serving 100% of QA.
- Live HF Space smoke (`https://rohitsar567-insurancebot.hf.space`): PED waiting-period question now answers via `nim-chain::nemotron-3-nano-30b-a3b::v4-flash::qa` with a grounded reply, not the old "Happy to help. First, your age?" misroute.
- **Post-fix parallel 96-Q gold eval (93 of 96 completed; 3 trailing questions killed when the run hung on a NIM rate-limit edge case) — captured BEFORE KI-046 (refusal precision) and KI-049 (retrieval top-k boost) shipped:**
  - **Factual accuracy: 54.8% (51 / 93)** — up from the pre-fix 41.7% baseline. **This number is now stale; a fresh eval is running to measure the KI-046 + KI-049 lift and the headline will be updated once it lands.**
  - **KI-022 JSON-fallback** rescued 7 questions that would have scored 0 on Groq judge JSON errors. Without KI-022 the headline would have been ~47%.
  - PED waiting-period type — previously 0% pre-fix; samples now: "Bot correctly states the 24‑month waiting period" / "matched_nums=['24']" via regex fallback / "36‑month period and includes source cit…".
  - Stuck questions: rows 94-96 (all `regulatory_oos` refusals — those routes are already at ~100% earlier in the run; the rate-limit hang affected the brain call, not the refusal logic).
  - **Expected directional lift from the two pending fixes:** KI-049 directly targets the `sub_limit` accuracy gap called out under D-003 (structured cap-table chunks now have ~2× chance of landing in context); KI-046 directly targets the `exclusions_oos` refusal-logic gap. Both gaps were the explicit "next bottleneck" in the 54.8% post-mortem.
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
