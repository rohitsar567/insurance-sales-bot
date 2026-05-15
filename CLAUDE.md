# CLAUDE.md — project memory for AI assistants

This file is read by Claude Code (and any compatible AI tool) at the start of a session in this repo. Keep it under ~200 lines and focused on **stable, non-obvious facts a new contributor would need**. For change history, look at git log, `80-audit/ENTERPRISE_AUDIT.md`, and `70-docs/60-decisions/`.

## Project at a glance

- **What:** a voice-first AI advisor for Indian health insurance — RAG over a curated 224-document corpus (206 policies + 18 regulatory docs, 7,295 chunks), Sarvam STT/TTS, 4-gate faithfulness, 20-insurer scorecard.
- **Live:** https://rohitsar567-insurancebot.hf.space (HF Space; rebuild triggered on every push to `origin main`).
- **Repos:** `origin` is the HF Space at `huggingface.co/spaces/rohitsar567/InsuranceBot`. `github` is the mirror at `github.com/rohitsar567/insurance-sales-bot`. Data lives separately at `huggingface.co/datasets/rohitsar567/insurance-bot-data` (with a GitHub mirror that uses LFS).
- **Local dev path:** `~/Developer/Insurance Sales Bot/` (NOT `~/Documents/Personal/AI Work/...` — the older path that occasionally shows up in stale scripts; iCloud-synced + TCC-restricted).

## Voice UX (ADR-028)

**One default voice mode, one fallback.**

- **Live ✓ (default ON)** — `useLiveConversation` keeps the mic continuously open with VAD barge-in. The user can speak over the bot and it pauses TTS + aborts in-flight `/api/chat`. Pill in the toolbar is the toggle: green = on, red = off. State persists in `localStorage.insurance_live_pref`.
- **🎤 Push-to-talk** — a labeled button. Click → suspends Live for one turn → fresh recorder with VAD silence-cutoff → submits → resumes Live (only if `userPrefersLive` is still on).
- **Hands-free was removed entirely** in KI-027. Anything in the codebase still referring to it is stale.
- **Bot TTS plays via the in-DOM `<audio>` element** inside `Message` (autoplay-on-mount via ref'd `useEffect`). Never use `new Audio(url).play()` — those detached instances are invisible to `document.querySelectorAll("audio").pause()` in the barge-in handler.

## LLM stack (ADR-019 + ADR-026 → ADR-031 + ADR-032) — KI-080 → KI-087

Every LLM role is a `NimChainLLM` candidate pool, NOT a hardcoded single model. End-to-end spec: [ADR-032](70-docs/60-decisions/ADR-032-llm-chain-architecture.md). Chains preserve brain ↔ judge family diversity (Qwen brain ↔ Mistral judge) so failovers can't accidentally produce circular grading.

- **Probe-driven sticky primary election (KI-080, [ADR-031](70-docs/60-decisions/ADR-031-sticky-primary-election.md)).** All three chains (`BRAIN_CHAIN`, `FAST_BRAIN_CHAIN`, `JUDGE_CHAIN`) elect a sticky PRIMARY + provider-diverse BACKUP from a background probe. `backend/llm_health.py` scores every candidate on `(1 / max(50, latency_ms)) * success_rate` and writes the current election to process state. `NimChainLLM.chat()` calls PRIMARY once; on real-time failure it falls to BACKUP (cross-provider by construction) and triggers an immediate probe refresh. **Per-turn LLM call count: 1 (most cases) or 2 (PRIMARY fails real-time → BACKUP).** Pre-KI-080 worst case was 5-6 NIM calls per turn, all queued and timing out.
- **NIM-first election preference (KI-087, `d90f8c0`).** Election prefers ANY eligible NIM candidate over ALL non-NIM candidates. Within the NIM pool, score still picks the fastest healthy NIM model. Only when the NIM pool is empty (every NIM model down, throttled, or quota-exhausted) does election fall through to Groq / OpenRouter as PRIMARY. Rationale: NIM is the strategic free provider ($0, no daily cap, 110+ models); Groq has 100K tokens/day; OpenRouter charges real USD. Both serve as emergency fallback only.
- **Probe cadence + per-phase timeouts (KI-084, `119e0fd`).** Probe loop ticks at `PROBE_INTERVAL_SEC = 300s` (was 60s — raised so probe-driven token spend stays inside Groq's 100K/day free-tier cap). Probe `max_tokens` cut `5 → 1`. Every chat call uses explicit `httpx.Timeout(connect=2, read=12, write=2, pool=2)` so a stuck NIM pool releases its TCP socket independently of the outer `asyncio.wait_for`, preventing NIM concurrency-slot leaks across PRIMARY → BACKUP. Rate-limit failures (HTTP 429 / `RateLimit` body) get a **1h sin-bin** (`DEGRADE_DURATION_LONG_S = 3600s`) instead of the 30s transient window — free-tier daily quotas don't reset in 30 seconds.
- **Proactive credit gating (KI-085, `8fc7979`).** Election is gated by `is_alive AND has_credits` so quota-exhausted candidates are excluded BEFORE the user hits a 429. Signal sources: Groq response headers (`x-ratelimit-remaining-tokens-day`, low-water 5,000 tokens); OpenRouter `GET /api/v1/credits` polled every 10 min (low-water $0.05); NIM local 60-second rate-meter (gate at 35-of-40 req/min, headroom 5). Closes the one-turn reactive gap KI-084 alone leaves.
- **HF Space secrets (KI-081, no commit; HF Space env secrets push).** `GROQ_API_KEY` + `OPENROUTER_API_KEY` pushed to the Space repository secrets so KI-080 cross-provider election candidates have working keys in production. Pre-KI-081 only `NVIDIA_NIM_API_KEY` was set on the Space; the elector marked Groq + OR as `no_api_key` and election degraded to NIM-only candidates.
- **Admin telemetry (KI-086, `d90f8c0`).** `GET /api/admin/llm-health` returns `{chains, candidates, recent_turns, snapshot_ts}` with per-chain elected primary/backup, per-candidate health + credits + degraded-until, and last 20 turn outcomes. Surfaced in the admin "LLM Chain" tab with auto-refresh every 30s.
- **KI-025's 50/50 NIM ↔ Groq rotation ([ADR-026](70-docs/60-decisions/ADR-026-provider-load-balancing.md)) is deprecated** — `_balanced_brain_chain` retained behind a feature flag for one-release rollback; the probe-driven election picks the actually-faster candidate dynamically.
- **Cold-start fallback.** Before the first probe completes (process restart, HF Space rebuild), `chain[0]` is the initial primary and `chain[1]` (preferring a different provider) is the initial backup. The probe loop runs immediately on startup; OpenRouter credits are polled on startup so the elector has a non-None USD balance before the first chat call.
- **Brain / fast-brain / judge primaries in steady state** are typically **NIM Qwen 3-Next 80B** (heavy brain, KI-087 NIM-first), **NIM Nemotron Nano 30B** (fast brain), and **Mistral Large 3 675B** (judge). Not hardcoded — the elected primary follows live `latency × success_rate × credits_available` with NIM-first preference.
- **KI-079 escalation as last bite (`87ee522`).** If both PRIMARY and BACKUP fail in a single fact-find turn, orchestrator retries once on `BRAIN_CHAIN` (heavy brain, `_TIMEOUT_S_ESCALATION = 15s`, 35s chain budget) before falling to `_canonical_fallback` (KI-072 / KI-074 greedy slot capture). Worst-case wall-clock before canonical: 25s FAST + 15s heavy = 40s.
- **NIM concurrency semaphore + serial probe (KI-088, `14ee008`).** Module-level `asyncio.Semaphore(2)` wraps every NIM HTTP call so our process never has >2 NIM requests in flight simultaneously, regardless of source (probe loop + admin polls + per-user turns all serialise through the same semaphore). Probe loop changed parallel→serial so the 6-NIM probe burst becomes a 1-slot trickle over ~12s. Inner 4-attempt exponential-backoff retry deleted from `NvidiaNimLLM.chat()` — KI-080 election + KI-079 escalation now handle failover. Result: latency-based failures (41s timeouts under self-saturation) dropped to zero; replaced by a parser-side bottleneck (KI-090).
- **Lenient FF-block parser (KI-090, `11cf4b3`).** Real LLMs (Qwen, Nemotron under load, Groq Llama-3.3) sometimes drop the literal `<FF>...</FF>` tags around their JSON tail. Pre-KI-090 those replies fell to `fallback:no_trailer` even though the brain had produced a perfectly valid structured response. Now `_parse_ff_block` tries strict → fenced ```` ```json``` ```` → bare-JSON-tail, each candidate validated by presence of a contract key (`captured` / `slot_driving` / `complete`). `_strip_ff_block` mirrors the strategies so prose-only output never leaks structured metadata.
- **Skip `profile_extractor` + faithfulness judge on fact-find turns (KI-091, `9813994`).** Both chains were credit-exhausted on the steady-state primary, hung fact-find turns for 20+s, and the extractor periodically returned `{"name": null}` which wrote into `session.update_profile_field` and wiped the captured name mid-session — causing `next_question` to re-ask the name slot the user had already answered. Orchestrator now short-circuits both chains behind `intent == "fact_find"`; the fact-find brain (KI-070) extracts fields natively from its `<FF>` JSON tail, and faithfulness scoring is meaningless on "what's your annual income?". QA-mode turns still run both chains. Live: name re-ask loop gone; fact-find p95 28s → 6-8s.
- **Defensive `None`-guard in extractor merge (KI-094, `f068094`).** Belt-and-braces companion to KI-091. On QA-mode turns the extractor still runs (correct: "I'm now 35" mid-recommendation should update the profile), but under load it periodically returns `{"name": null, "age": null, ...}` and the merge loop was writing every key including nulls back into the session. Added `if new_value in (None, "", []): continue` at the top of the extracted-fields loop in `backend/orchestrator.py` — null / empty-string / empty-list returns are now no-ops. Closes the same root cause (LLM-returned nulls wiping state) at a second layer; KI-091 prevents the extractor from running on fact-find turns at all, KI-094 makes it safe even when it does run.
- **Remove IP allowlist from admin — password-only gate (KI-097, pending).** Dropped `ADMIN_IP_ALLOWLIST` env + `_ip_allowed()` from `backend/admin.py`; `_check_admin` is now password-only against `X-Admin-Password` → `ADMIN_PASSWORD` env. Backend returns 401 Unauthorized (previously 404-to-hide). Frontend admin panel is always visible; password unlocks the live data. ADR-023 superseded — IP gating added zero security beyond a strong password and locked the operator out whenever the home IP changed.
- **Drop function-local `import logging` in orchestrator (KI-101, `66eb4ed`).** Removed 6 inline `import logging` lines from `backend/orchestrator.py`; Python's scoping rule was promoting `logging` to function-local, causing `UnboundLocalError` when the `asyncio.wait_for` `TimeoutError` branch fired before reaching the inline import. Module-level import is the sole binding now.
- **Profile RAG session isolation (KI-102, `4bb8da0`).** `upsert_profile_chunk` stamps `session_id` metadata on every chunk; `retrieve()` excludes `doc_type == "profile"` from the main pass; per-session profile lookup triple-checks `meta.session_id == session_id` in Python after the Chroma where-clause. Legacy chunks without `session_id` are silently refused (fail-closed). Cross-session PII leak (age / dependents / health conditions) closed. ADR-022 extended with session-isolation subsection.
- **`_canonical_fallback` no_trailer loop-breaker (KI-103, `8ef5c43`).** Added `session._ff_failed_attempts[slot_id]` + `session._ff_skipped_slots: set[str]`. After 2 failed attempts on the same slot, mark skipped and advance to the next unfilled slot. Caps the worst-case wedge at ~18 turns to escape fact-find instead of unbounded.
- **CoT / instruction-echo strip in voice_format (KI-104, `407f2a1`).** `tts_preprocess` now kills `<think>...</think>` blocks, `**Reasoning:**` / `**Thought:**` labels, `[INTERNAL]` blocks, sentence-anchored CoT starters ("Let me think...", "Step 1:..."). Emergency fallback to a generic acknowledger if the whole reply is CoT-shaped. Stops Sarvam from TTS-ing the bot's internal monologue.
- **Recommendation closer wired (KI-105, `8a58fa1`).** `RECOMMENDATION_CLOSER_PHRASES` frozenset ("show me the top 3", "rank", "pitch me", "compare X vs Y") classified as `recommendation` / `comparison` BEFORE the `FACT_FIND_TRIGGERS` check, so a fully-fact-found user can never get bounced back into fact-find. Persona prompt gets `RECOMMENDATION_CLOSER_ADDENDUM` with a strict 3-policy ranked-shortlist contract (3 policies, one-line rationale each, IRDAI disclaimer, no hedging). ADR-008 extended with closer-mode subsection.
- **Graceful TimeoutError + Exception on `/api/chat` (KI-106, `565bf31`).** `handle_turn(...)` wrapped in `asyncio.wait_for(45s)` with explicit `except asyncio.TimeoutError` + broad `except Exception`. Both return HTTP 200 with `source="graceful_timeout"` / `graceful_exception"` and an in-character recovery sentence instead of HTTP 500. Internal `logger.exception` still captures the full traceback for admin observability.
- **`_safe_collection_get` helper for Chroma (KI-107, `3a9a14f`).** Wraps every `collection.get(ids=[...])` and `collection.get(where=...)` call in `backend/profile_rag.py` in `try / except Exception`, returns `None` on miss with `logger.warning(...)`. Closes the KI-102 per-session profile lookup raising on never-existed sessions on HF Space (Chroma version-dependent behaviour). `None` return is treated identically to a `session_id` mismatch — fail-closed.
- **Chroma collection re-ingested + profile-write hardening (KI-112).** KI-111 wrapped `.query()` so the bot survived the corruption, but every embedding query was raising `InternalError: Error executing plan: Internal error: Error finding id` and silently returning empty retrieval — the bot was answering 206 policies' worth of Qs without access to any policy chunk. Root cause: a pre-KI-102 deploy wrote a `profile_anonymous` chunk with NO `session_id` metadata; that legacy row poisoned every later `coll.query(where={"doc_type": {"$ne": "profile"}})` and the damage spread across HNSW segments (full collection extraction surfaced 1580 / 7356 chunks across 148 policies as `Error getting embedding`). Fix: full re-ingest from `rag/corpus/` PDFs → clean `rag/vectors/` + two new write-time guards in `backend/profile_rag.py::upsert_profile_chunk` — (a) reject `session_id` that isn't a non-empty `str`, (b) reject any embedding whose length ≠ `embedder.dimension` or that contains `None`. Both guards log a `WARNING` and return without writing, so a future model-drift or bad-input event can't re-poison HNSW. 4 new regression tests in `tests/test_profile_rag_isolation.py::TestUpsertRejectsBadInputs`. Repaired vectors uploaded to HF dataset `rohitsar567/insurance-bot-data` via `tools/upload_vectors_to_dataset.py` so the Space rebuild picks up the clean index. Old corrupted Chroma archived at `rag/_hf_dataset_backup/rag/vectors.corrupted.<ts>/`.
- **Chain budgets:** brain 20s × 35s total, fast-brain 12s × 22s total, judge 30s × 75s total. With KI-080 only PRIMARY + BACKUP consume budget in the common case — leaves headroom for KI-079 escalation. KI-084 per-phase httpx timeouts are nested inside these budgets.
- **STT/TTS/Translator** = Sarvam (Saarika v2.5 / Bulbul v2 / Sarvam-M). **Embeddings** = local BGE-small-en-v1.5.
- **Provider keys.** `NVIDIA_NIM_API_KEY` + `GROQ_API_KEY` + `OPENROUTER_API_KEY` required in `.env` (local) and HF Space environment (production — KI-081).

## Fact-find loop (ADR-030, supersedes ADR-027) — KI-070

**One LLM call per turn drives the entire fact-find conversation.** The pre-KI-070 three-layer stitching (hardcoded `GRAPH` question text + paraphraser + opener / acknowledger rotation) read as robotic copy-paste in user testing and is retired. `backend/fact_find_brain.py::drive_fact_find()` issues a single `NimChainLLM(FAST_BRAIN_CHAIN, timeout=12s)` call whose system prompt contains the 9-slot schema + current profile state + recent chat history, and emits natural conversational prose followed by a JSON tail block `<FF>{"captured":{...}, "slot_driving":"...", "complete":<bool>}</FF>`. Orchestrator strips the `<FF>` block before sending prose to the user; the JSON updates `session.profile` and selects the next slot in one pass.

- **Native multi-fact capture.** A single user utterance like *"I'm 34, in Mumbai, just myself"* fills age + city + dependents in one turn. Verified live on 2026-05-15: `profile_updates: {name: 'Rohit Sar', age: 32, dependents: 'self', location_tier: 'metro'}` from one opener.
- **Safeguards.** JSON-block-must-parse → fall to canonical `next_question(slot_id)`. Slot-not-progressing (3 turns same slot, no captures) → bail to canonical. Hard 12s budget. Any chain exhaustion → canonical fallback. Fact-find can never wedge.
- **`backend/needs_finder.py::GRAPH`** retained as the safeguard fallback path only — never the primary path in steady state, but always available.
- **DELETED in KI-070** (~500 LOC): `backend/question_paraphraser.py` module, `_pick_opener`, `_NEUTRAL_OPENERS` / `_FAMILY_OPENERS` constants, `_contains_self_introduction`, the KI-067 first-policy regex (brain captures natively from prose), the acknowledger template selection.
- **Fallback path is no longer a dead-end (KI-072 / KI-074).** When the brain times out / chain exhausts / `<FF>` block fails to parse, `_canonical_fallback` doesn't just return the canonical question — it GREEDILY applies the user's current message to every unfilled slot via `_normalize_for_slot`, applies captures to the profile, then picks the next still-empty slot. Slot-specific trigger guards (age needs "years old" or bare number; existing_cover needs ₹/lakh/Cr cue or denial; name needs explicit "I'm X" intro pattern) prevent cross-contamination. So a NIM-degraded session walks fact-find via canonical questions but still progresses — never wedges.
- **Brain timeout: 25s (KI-075).** The `asyncio.wait_for` wrap around `drive_fact_find` was 12s originally — too tight. NIM cold-start eats 10-15s after a Space rebuild, killing the brain call before the chain's internal 22s `total_budget_s` could try Groq/OpenRouter fallbacks. 25s gives cold-start headroom + 1 chain fallback hop.
- **Natural-conversation escape (KI-045):** intent_change phrases or off-topic questions still exit fact-find by routing through `should_route_to_fact_find` — handled upstream of `drive_fact_find` so the safeguard mechanism here applies to in-fact-find pivots.
- **Indic queries** route through Sarvam-M for translation on input + output; the fact-find brain itself runs in English on the translated text.

## Refusal precision (KI-046)

- Persona prompt now explicitly instructs the bot to refuse on **fanciful / out-of-scope scenarios** (space tourism, diamond-tipped surgery, fictional procedures) with a specific refusal sentence.
- Anti-pattern guarded against: "policy doesn't explicitly exclude it → maybe it's covered". This is wrong; absence-of-exclusion is not evidence-of-inclusion.

## Routing invariants (ADR-N/A — orchestrator.py)

These are pinned by `tests/test_routing_regression.py`:

- `classify_intent("What is the waiting period for PED in Activ Assure?")` MUST return `"qa"`, never `"fact_find"`.
- `should_route_to_fact_find("qa", profile_is_empty=True, ...)` MUST return `False` — direct QA questions don't need a profile.
- The empty-profile force-route guard only applies when `intent ∈ {"recommendation", "comparison"}` (the `CONTEXT_DEPENDENT_INTENTS` frozenset).
- `FACT_FIND_TRIGGERS` matches with word-boundary regex (`\b...\b`), NOT substring — `"hi"` no longer fires on `"which"` / `"this"` / `"high"`.

## Retrieval cache (ADR not yet written — code self-documents)

`rag/retrieve.py` has an in-process LRU cache keyed by `(query_normalized, top_k, sorted policy_ids, sorted insurer_slugs)`. Cap 256. Cache hit skips both Voyage embed + Chroma query. Invalidates on process restart.

**Top-k boost for table-cell questions (KI-049):** room rent / sub-limit / cap on / single-private / NCB / co-pay / day-care-limit / etc. triggers bump `top_k` from 5 → 10 for that one query, so the policy's structured cap-table chunk has a higher chance of landing in context. Confined to the trigger query only — does not pollute the cache for downstream non-table queries.

## Repo bucket layout (KI-047 / KI-050 / KI-051)

Numbered top-level buckets for non-code artifacts (sort lexicographically in `ls`):

- `40-data/` ← formerly `data/` — runtime/cached data. All Python string-path refs updated (KI-050). Dockerfile `COPY` paths updated (KI-051).
- `70-docs/` ← formerly `docs/` — ADRs, design notes, decisions.
- `80-audit/` ← formerly `audit_results/` — defect register + eval artifacts (this audit lives here).

**Code dirs (`backend/`, `frontend/`, `rag/`, `tools/`, `eval/`, `tests/`, `kb/`) kept as-is** — Python forbids leading-digit / hyphen package names, so renaming code dirs would break imports.

## Admin panel (KI-048 / KI-052)

- **Backend:** `GET /api/admin/profiles` + `GET /api/admin/performance`, both behind `_check_admin` (`X-Admin-Password` header only, post-KI-097). Auth failure returns 401 Unauthorized.
- **Frontend:** admin HTML has 3 lazy-loaded tabs — **Profile + Visitor Log** (pulls `/api/admin/profiles`), **Performance** (pulls `/api/admin/performance`), **LLM Chain** (unchanged from prior). Auth state preserved across tab switches.

## Disk + storage hardening (ADR-029)

Three independent safety layers against ChromaDB HNSW bloat:

1. **In-process tripwire** — `rag/ingest.py::_abort_if_hnsw_bloated` aborts ingest if `link_lists.bin > 500 MB`. Called from `rag/ingest.py`, `tools/ingest_kb_summaries.py`, `tools/ingest_reviews.py`.
2. **Hourly LaunchAgent** — `com.rohit.insurancebot.vectorbloat` auto-deletes `_hf_dataset_backup/` at > 20 GB; warns at 5 GB.
3. **Disk-free tripwire** — `com.rohit.disk-free-tripwire` alerts at < 20 GB free; critical at < 8 GB, dumps every `~/Developer` subdir > 1 GB into the log.

**All LaunchAgents must live under `~/Library/Scripts/`, NOT `~/Documents/`.** macOS TCC blocks `launchd` from executing scripts inside iCloud-synced `~/Documents/` paths, silently exit-126.

## What to read for what

- **System tour:** `README.md` (the master entry).
- **Decisions with alternatives:** `70-docs/60-decisions/ADR-*.md` (28 ADRs as of 2026-05-15).
- **Production-readiness defect register:** `80-audit/ENTERPRISE_AUDIT.md`.
- **Data lineage:** `kb/AUDIT_TRAIL.md`.
- **Tests:** `tests/test_routing_regression.py` (15 tests pinning routing + load-balance invariants).

## Working-style note (personal memory, not a project decision)

**Always parallelize independent work** (per `feedback_always_parallelize.md` in personal memory). On any task touching this project: dispatch agents in parallel when subtasks are independent, batch tool calls in a single message when there are no dependencies. Sequential-by-default wastes wall-clock time.

## Watch-outs

- **Never use detached `new Audio()`** — see "Voice UX" above.
- **Never hardcode a single LLM model client (`NvidiaNimLLM(model=...)`)** — always go through `NimChainLLM(chain=...)` so the call survives single-pool rate limits. (KI-033 migrated the last two stragglers — `profile_extractor` and `fact_find_normalizer`.)
- **Never let new code add `"hi"` (or any single-word trigger) to `FACT_FIND_TRIGGERS` without word-boundary regex** — substring matching brings back the KI-023 misrouting bug.
- **Never add `"qa"` to `CONTEXT_DEPENDENT_INTENTS`** — that brings back the headline KI-018 bug where QA questions get trapped in fact-find.
- **Voyage free tier is 3 RPM.** Affects only ingest (corpus rebuild); query-time uses Chroma vectors, no Voyage call. Don't worry about it on the hot path.
- **HF Space rebuild is 5-8 min per push.** Audits running against the live endpoint should be done AFTER the desired image is stably deployed, or the persona transcripts span multiple builds and become useless for A/B.
- **Two image-only PDFs are explicitly EXCLUDED from the ingest pipeline:** `royal-sundaram/family-plus__brochure.pdf` and `aditya-birla/activ-one__brochure.pdf` (pdfplumber returns 0 chars; OCR is out of scope). Activ One coverage is provided via the `activ-health-individual` wordings policy — do not re-add either brochure.

---

*Last reviewed 2026-05-15 — KI-101..KI-112 landed (orchestrator stability + profile-RAG session isolation + recommendation closer + graceful chat error handling + Chroma re-ingest + profile-write hardening).*
