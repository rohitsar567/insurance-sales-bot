# CLAUDE.md — project memory for AI assistants

This file is read by Claude Code (and any compatible AI tool) at the start of a session in this repo. Keep it under ~200 lines and focused on **stable, non-obvious facts a new contributor would need**. For change history, look at git log, `80-audit/ENTERPRISE_AUDIT.md`, and `70-docs/60-decisions/`.

## Project at a glance

- **What:** a voice-first AI advisor for Indian health insurance — RAG over a curated 208-document corpus, Sarvam STT/TTS, 4-gate faithfulness, 19-insurer scorecard.
- **Live:** https://rohitsar567-insurancebot.hf.space (HF Space; rebuild triggered on every push to `origin main`).
- **Repos:** `origin` is the HF Space at `huggingface.co/spaces/rohitsar567/InsuranceBot`. `github` is the mirror at `github.com/rohitsar567/insurance-sales-bot`. Data lives separately at `huggingface.co/datasets/rohitsar567/insurance-bot-data` (with a GitHub mirror that uses LFS).
- **Local dev path:** `~/Developer/Insurance Sales Bot/` (NOT `~/Documents/Personal/AI Work/...` — the older path that occasionally shows up in stale scripts; iCloud-synced + TCC-restricted).

## Voice UX (ADR-028)

**One default voice mode, one fallback.**

- **Live ✓ (default ON)** — `useLiveConversation` keeps the mic continuously open with VAD barge-in. The user can speak over the bot and it pauses TTS + aborts in-flight `/api/chat`. Pill in the toolbar is the toggle: green = on, red = off. State persists in `localStorage.insurance_live_pref`.
- **🎤 Push-to-talk** — a labeled button. Click → suspends Live for one turn → fresh recorder with VAD silence-cutoff → submits → resumes Live (only if `userPrefersLive` is still on).
- **Hands-free was removed entirely** in KI-027. Anything in the codebase still referring to it is stale.
- **Bot TTS plays via the in-DOM `<audio>` element** inside `Message` (autoplay-on-mount via ref'd `useEffect`). Never use `new Audio(url).play()` — those detached instances are invisible to `document.querySelectorAll("audio").pause()` in the barge-in handler.

## LLM stack (ADR-019 + ADR-026 partial supersession)

Every LLM role is a `NimChainLLM` fallback chain, NOT a hardcoded single model. Chains preserve brain↔judge family diversity (Qwen brain ↔ Mistral judge) so failovers can't accidentally produce circular grading.

- **Brain primary** rotates 50/50 between **NIM Qwen 80B** and **Groq Llama-3.3-70B** via per-call `random.random()`. Effectively 2× throughput across two independent rate caps.
- **Fast-brain primary** is **NIM Nemotron Nano 30B** (~1.6s TTFT) with Qwen 80B as next fallback. Fast brain serves: fact-find, QA, paraphrase, normalize, extract — every latency-sensitive role.
- **Judge** = Mistral Large 3 675B primary. Different family from brain → non-circular grading.
- **STT/TTS/Translator** = Sarvam (Saarika v2.5 / Bulbul v2 / Sarvam-M).
- **Embeddings** = local BGE-small-en-v1.5 (`backend/providers/local_embeddings.py`). Voyage is configured in `.env` for ingest if needed but not on the hot path.
- **Chain budgets:** brain 20s × 35s total, fast-brain 12s × 22s total, judge 30s × 75s total. Per-link timeout is dynamically clipped to remaining budget.

## Fact-find loop (ADR-027)

- 9 canonical slots in `backend/needs_finder.py::GRAPH`, asked in order.
- The canonical question text is **rewritten by an LLM paraphraser** (`backend/question_paraphraser.py`) so each session sounds fresh. A verifier rejects paraphrases that drift off-slot, lack a question mark, or are out of length bounds.
- Paraphrases cached per `(session_id, slot_id)` → max 9 paraphrase calls per session.
- Re-asks (`"Sorry, I didn't catch that..."`) skip paraphrase so the user has a stable anchor.
- Indic queries use canonical Hindi text (paraphraser is English-only).
- **Natural-conversation escape (KI-045):** mid-fact-find, if the user message is intent_change (`"never mind"`, `"actually let me ask"`, `"stop asking"`, etc.) OR off-topic question (ends with `?`, ≥4 words, no slot keywords), `orchestrator.py` exits the fact-find branch, sets `session.free_form_session=True`, and lets the QA path handle it. Prevents the bot from droning past a user's pivot.

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

- **Backend:** `GET /api/admin/profiles` + `GET /api/admin/performance`, both behind `_check_admin` (IP allowlist + `X-Admin-Password` header). Auth failure returns 404 (not 401) so unauthenticated probes can't enumerate endpoints.
- **Frontend:** admin HTML has 3 lazy-loaded tabs — **Profile + Visitor Log** (pulls `/api/admin/profiles`), **Performance** (pulls `/api/admin/performance`), **LLM Chain** (unchanged from prior). Auth state preserved across tab switches.

## Disk + storage hardening (ADR-029)

Three independent safety layers against ChromaDB HNSW bloat:

1. **In-process tripwire** — `rag/ingest.py::_abort_if_hnsw_bloated` aborts ingest if `link_lists.bin > 500 MB`. Called from `rag/ingest.py`, `tools/ingest_kb_summaries.py`, `tools/ingest_reviews.py`.
2. **Hourly LaunchAgent** — `com.rohit.insurancebot.vectorbloat` auto-deletes `_hf_dataset_backup/` at > 20 GB; warns at 5 GB.
3. **Disk-free tripwire** — `com.rohit.disk-free-tripwire` alerts at < 20 GB free; critical at < 8 GB, dumps every `~/Developer` subdir > 1 GB into the log.

**All LaunchAgents must live under `~/Library/Scripts/`, NOT `~/Documents/`.** macOS TCC blocks `launchd` from executing scripts inside iCloud-synced `~/Documents/` paths, silently exit-126.

## What to read for what

- **System tour:** `README.md` (the master entry).
- **Decisions with alternatives:** `70-docs/60-decisions/ADR-*.md` (28 ADRs as of 2026-05-14).
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

---

*Last reviewed 2026-05-14 enterprise-readiness sprint.*
