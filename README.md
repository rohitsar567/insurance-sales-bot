---
title: Insurance Sales Portfolio Expert
emoji: 🏥
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Voice-first AI advisor for Indian health insurance
---

# Insurance Sales Portfolio Expert

**A voice-first, BFSI-compliance-grade AI advisor for Indian health insurance.** Built as a Sarvam AI take-home, deployed on HuggingFace Spaces, with grounding, citations, faithfulness gates, and a curated 206-document corpus.

| | |
|---|---|
| **Live demo** | https://rohitsar567-insurancebot.hf.space |
| **Code (production)** | [`huggingface.co/spaces/rohitsar567/InsuranceBot`](https://huggingface.co/spaces/rohitsar567/InsuranceBot) · [`github.com/rohitsar567/insurance-sales-bot`](https://github.com/rohitsar567/insurance-sales-bot) |
| **Data (corpus + vectors)** | [`huggingface.co/datasets/rohitsar567/insurance-bot-data`](https://huggingface.co/datasets/rohitsar567/insurance-bot-data) · [`github.com/rohitsar567/insurance-sales-bot-data`](https://github.com/rohitsar567/insurance-sales-bot-data) (LFS) |
| **Author** | Rohit Saraf · rohitsar567@gmail.com |
| **Read time** | ~20 minutes (this doc) · or jump to [§7 Document Ecosystem Guide](#7-document-ecosystem-guide) |

---

## Table of Contents

1. [Executive summary](#1-executive-summary)
2. [Project vision & requirements](#2-project-vision--requirements)
3. [Two parallel flows — customer vs. technology](#3-two-parallel-flows)
4. [Exhaustive tech architecture](#4-exhaustive-tech-architecture)
5. [Data architecture — corpus, extraction, embeddings](#5-data-architecture)
6. [Quality & safety — eval, faithfulness, refusal](#6-quality--safety)
7. [Document ecosystem guide](#7-document-ecosystem-guide)
8. [Quick start & local development](#8-quick-start--local-development)
9. [Deployment & storage topology](#9-deployment--storage-topology)
10. [What a fresh Claude Code session needs to rebuild this](#10-rebuild-from-scratch)

---

## 1. Executive summary

A **voice-first health-insurance advisor** for Indian buyers, grounded in a curated corpus of **206 documents** — 188 product documents from 19 leading insurers plus 18 IRDAI / regulatory documents — extracted into a 62-field structured schema with a rules-based A–F scorecard and a **4-gate hallucination defense** on every reply. The marketplace surfaces **166 cards** across the 19 real insurers (one card per IRDAI-filed product after KI-133 / KI-141 / KI-142 / KI-145 dedup); the `profile` and `regulatory` Chroma slugs are filtered out of all user-facing counts (KI-129 / KI-130 / KI-132).

The bot is **consumer-facing in experience, B2B in commercial application.** The realistic deployment is an insurer or aggregator white-labelling this advisor on top of Sarvam's ASR/TTS/LLM stack. The build deliberately optimises for the artifacts a BFSI buyer would audit: provenance, refusal behaviour, eval rigor, citation grammar.

**Try on the live demo:** *"What's the pre-existing disease waiting period under Care Supreme, and how does that compare to ICICI Elevate?"* — comparative answer with `[Source: ...]` citations linking to specific policy PDFs and page ranges, brain tag showing which model handled it, audio synthesised by Sarvam Bulbul. Ask the same in Hinglish — *"Care Supreme mein PED ka waiting period kya hai?"* — and the response flows through the Indic translation cascade with three drift checks.

### 1.0 Today's session (2026-05-15) — KI-125 → KI-150

Thirty-plus knowledge increments landed today. The corpus was rebuilt, the marketplace dedup logic was rewritten, the voice stack was retuned, and an insurer rename was migrated end-to-end. One-line each:

- **KI-125 / KI-126 / KI-127** — full corpus rebuild + dedup; **Chroma chunks 3,799 → 7,317** (wordings 5,401 · brochure 611 · regulatory 498 · prospectus 483 · cis 302 · curated 21 · profile 1); **marketplace cards 138 → 166** correctly counted; **2 image-only PDFs dropped** (`royal-sundaram/family-plus__brochure.pdf` + `aditya-birla/activ-one__brochure.pdf`) so source-PDF total is now 206 (188 product + 18 regulatory) with 201 extracted JSONs.
- **KI-128** — `tools/upload_to_hf.py` LFS quota silent-failure fix.
- **KI-129 / KI-130 / KI-132** — filter `profile` + `regulatory` slugs from user-facing marketplace counts (19 real insurers + 1 regulatory bucket = 20 internal slugs).
- **KI-131 / KI-134 / KI-139 / KI-148** — voice mode now defaults **OFF**; AudioContext.resume() unlocks autoplay; VAD threshold retuned (`rmsThreshold=18`, `voiceBandMinProp=0.20`, `noiseFloor * 1.8`); TTS `k → thousand` expansion.
- **KI-133 / KI-141 / KI-142 / KI-145** — marketplace dedup: one card per IRDAI-filed product; aliases handle marketing renames; sub-variants stay separate when material terms differ.
- **KI-136** — named-SKU comparison routes to `qa` instead of `fact_find`.
- **KI-137** — ingested 21 curated-facts policies (Activ One, Optima Secure, …) into Chroma so the bot can retrieve them.
- **KI-138** — canonicalized 84 `policy_name` mismatches across extracted JSONs + Chroma metadata.
- **KI-143** — `bajaj/group-health-guard` slug correction (gold → silver per the PDF).
- **KI-144** — `reliance-general` → `indusind-general` migration (Reliance General Insurance was rebranded to IndusInd General). The `indusind-general` slug did not exist anywhere in the codebase before today.
- **KI-149** — budget + income parser captures bare numerals like `"30000"`.
- **KI-150** — `fact_find_brain` `max_tokens` 420 → 700 (one of eight band-aids on the prose+`<FF>`-trailer prompt shape; superseded by KI-167).
- **KI-160** — chains locked to NIM-only ([ADR-038](70-docs/60-decisions/ADR-038-nim-only-chains.md)) after KI-155 demonstrated Groq Llama-3.3 silently ignores the `<FF>` structured-output trailer contract.
- **KI-167** — `backend/fact_find_brain.py` ripped out and replaced by `backend/sales_brain.py` ([ADR-039](70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md)). Single LLM call per turn using native JSON mode; no scripted `Question.prompt_en`, no `<FF>` trailer, no `_canonical_fallback`, no `"Got that — {slot}."` prefix. Deterministic post-processor (`backend/sales_brain_normalizer.py`) normalizes captures; profile persists via `profile_store` + `profile_rag` as before.
- **KI-171** — orchestrator skips the Judge chain on `fact_find` + `recommendation` queries (faithfulness scoring is meaningless on profile-capture turns and on multi-policy ranked shortlists where the rubric is structural, not factual grounding).
- **KI-175** — NIM chain reorder: Mistral Large 3 675B promoted to primary, nemotron-49b demoted to last resort across all three chains. The old "nemotron primary" ordering predated the 675B model's availability.
- **KI-176** — OpenRouter re-added to the chain pool with `models: [...]` server-side fallback within the OR free pool. KI-178 live-audit then confirmed which OR free-tier models actually support `response_format` (nemotron-3-super-120b, qwen3-next-80b, gemma-4 — not Llama 3.3 70B or Hermes 3 405B).
- **KI-179** — Google AI Studio added as Tier 0 primary on Brain Fast + Brain Main ([ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md)). `backend/providers/google_gemini_llm.py` wrapper matches the `LLMProvider` interface; chains now span {Google → NIM → OpenRouter}. ADR-038 (NIM-only lock) is superseded — the lock was relaxed once KI-167 retired the `<FF>` trailer convention that originally motivated it. Brain Fast primary is `gemini-2.0-flash`; Brain Main primary is `gemini-2.5-flash`; Judge stays NIM Mistral Large 3 675B (different family from Gemini, preserving the brain ↔ judge non-circular grading invariant). Google's free tier: 1500 req/day, 15 req/min, native JSON via `response_mime_type=application/json`.

Per-insurer card counts (166 total across 19 real insurers): HDFC ERGO 15 · National Insurance 14 · Niva Bupa 14 · Bajaj Allianz 13 · ICICI Lombard 13 · Star Health 11 · Care Health 10 · New India Assurance 9 · Tata AIG 9 · Acko 7 · Aditya Birla 7 · Royal Sundaram 7 · Cholamandalam MS 6 · Go Digit 6 · IFFCO Tokio 6 · ManipalCigna 6 · SBI General 6 · IndusInd General 3 · Oriental Insurance 3 · Reliance General 1.

### 1.1 Demo runbook — 7 questions to try

Live URL: **https://rohitsar567-insurancebot.hf.space**. For each: try voice and text. The reply panel shows `brain_used` and per-citation source links.

| # | Question | What you should see | Why this question |
|---|---|---|---|
| 1 | *"What's the pre-existing disease waiting period under Care Supreme?"* | Specific number + `[Source: care-health/care-supreme/wordings, p.18]`. Brain: fast-brain chain (Nemotron 30B primary). | Single-field lookup — easiest competence check. |
| 2 | *"Compare cataract waiting period in ICICI Elevate vs HDFC Optima Secure."* | Two-policy comparison with citations from both PDFs. Brain: BRAIN_CHAIN with probe-elected primary (KI-080). | Multi-policy reasoning — tests retrieval + tiered brain routing. |
| 3 | *"Care Supreme mein PED ka waiting period kya hai?"* (Hinglish) | Answer in Hinglish with citations preserved. Brain tag includes `cascade::sarvam-trans+...` if drift gates fire. | Indic cascade + 3-gate drift verification. |
| 4 | *"What does IRDAI say about cataract waiting-period caps under the 2024 Master Circular?"* | Cited answer from `irdai-master-circular-health-2024.pdf`. | Demonstrates the IRDAI corpus rescue past Akamai (ADR-017). |
| 5 | *"Does Bajaj Silver Health cover space-tourism injuries?"* | **Safe refusal.** | Adversarial out-of-corpus — refusal is the correct behaviour. |
| 6 | *"Should I get the cataract surgery covered under this policy?"* | Bot answers what's covered + refuses clinical advice; suggests a doctor. | No medical advice (persona rule 4). |
| 7 | *"Show me the scorecard for ICICI Elevate."* | Side-panel A/B/C grade with 6 sub-scores. Hover → ✓ and − signals per sub-score citing specific schema fields. | The rules-based scorecard ([§6.3](#63-eval-methodology)). |

If a question refuses unexpectedly, that's the safe failure mode — open `logs/hallucinations.jsonl` and the failing gate is recorded.

### 1.2 What this signals about how I'd ship

A take-home is a sample of how the engineer thinks under constraint. Three things this submission is meant to signal:

1. **Scope to a vertical slice, not a demo.** [ADR-001](70-docs/60-decisions/ADR-001-vertical-slice-scope.md) explicitly chose vertical slice over single-document RAG or full-platform. The build's 7 commitments — per-insurer adapters, category-agnostic schema, pluggable extraction, schema-driven filter UI, provider-agnostic STT/TTS/LLM, eval harness that scales linearly, stateless services — make v2 (life / motor insurance) a data + config change, not a rewrite.

2. **Hallucination defense and refusal as product features.** BFSI deployments get fined for mis-selling; the bot is biased toward refusal over confident wrong answers. The 4 faithfulness gates + cross-check retry + 3 Indic drift checks + audit log are the BFSI-compliance-grade version of "we shipped a chatbot." When the eval shows a headline accuracy below 100% because the gates are aggressive, the right response is to soften the gates carefully — not to ship a higher number by relaxing the verifier.

3. **Honest model picks — Sarvam where Sarvam is uniquely strong, frontier-tier free brains for reasoning.** Voice and Indic are non-substitutable: **Sarvam Saarika v2.5** for speech-to-text, **Sarvam Bulbul v2** (speaker `anushka`) for text-to-speech, and **Sarvam-M** for Hindi/Hinglish/vernacular translation — no closed-source frontier matches Sarvam on Indian accents or code-mixed Hinglish. Reasoning runs on a three-tier candidate pool per role (KI-179 / [ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md)), not a single hardcoded brain. Every candidate supports native JSON mode server-side — Google's `response_mime_type=application/json`, NIM's `response_format={"type":"json_object"}`, OpenRouter's `response_format` pass-through on the audited free-tier models (KI-178). Cross-provider fallback is safe again now that the `<FF>` trailer convention that triggered KI-160 / ADR-038's NIM-only lock has been retired by KI-167 / [ADR-039](70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md):

   - **Brain Main** (comparison, recommendation, synthesis) — primary **Google `gemini-2.5-flash`** via Google AI Studio (1500 req/day free, native JSON mode), Tier 1 fallback **NIM Mistral Large 3 675B** (`mistralai/mistral-large-3-675b-instruct-2512`), then **NIM Llama-4 Maverick 17B/128E**, then **NIM Qwen 3-Next 80B**, then **OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`**, last resort **NIM Nemotron-Super 49B v1.5**.
   - **Brain Fast** (sales-brain fact-find turns per KI-167, fast QA) — primary **Google `gemini-2.0-flash`**, Tier 1 fallback **NIM Qwen 3-Next 80B**, then **NIM Mistral Large 3 675B**, then **NIM Llama-4 Maverick 17B/128E**, then **OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`** + `qwen/qwen3-next-80b-a3b-instruct:free`, last resort **NIM Nemotron-Super 49B v1.5**.
   - **Judge** (faithfulness Gate 4, Hinglish drift LLM-judge, eval grader; KI-171 skips this on `fact_find` + `recommendation` queries) — primary **NIM Mistral Large 3 675B** (different family from the Gemini brain — preserves cross-family non-circular grading), Tier 1 fallback **NIM Llama-4 Maverick 17B/128E**, then **OpenRouter `qwen/qwen3-next-80b-a3b-instruct:free`**, last resort **NIM Nemotron-Super 49B v1.5**.

   **Three-tier election (KI-179, [ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md)).** Google → NIM → OpenRouter, with the chain falling through naturally when an upper tier 429s, exhausts quota, or fails real-time. On total chain exhaustion the orchestrator returns a graceful error message — fail-loud > fail-silent-with-garbage is preserved. The 50/50 NIM ↔ Groq rotation of KI-025 ([ADR-026](70-docs/60-decisions/ADR-026-provider-load-balancing.md)) and the NIM-only lock of KI-160 ([ADR-038](70-docs/60-decisions/ADR-038-nim-only-chains.md)) are both superseded. KI-085's proactive credit gating applies per-candidate (60-second rate-meter, gate at 35-of-40 req/min). `GROQ_API_KEY` remains in HF Space secrets but is dormant — no chain references it.

   The result: a Sarvam customer deploying this stack gets a product that *uses Sarvam exactly where Sarvam beats the world*, uses **Google Gemini Flash on the free path for frontier-tier conversational quality**, and uses MIT-licensed open-weights frontier models (Mistral Large 3 675B, Llama-4 Maverick, Qwen 3-Next 80B, Nemotron-Super 49B) for everything else — $0 inference, three independent free-tier providers, single-key-per-provider for the entire non-voice stack.

The rest is craftsmanship. The 8-section KB ([`kb/`](kb/)) is regeneratable from primary sources in <40 minutes for <$2 cold. Every numeric value in every reviewer-facing artifact traces to a source PDF + page + clause. Every architectural decision is in [`70-docs/60-decisions/`](70-docs/60-decisions/) with alternatives and revisit-at-scale notes. Every production-readiness defect is in [`80-audit/ENTERPRISE_AUDIT.md`](80-audit/ENTERPRISE_AUDIT.md). The repo is structured so a new engineer joining on Monday could ship v1.1 by Friday.

---

## 2. Project vision & requirements

### 2.1 The problem

Indian health insurance has 19+ insurers, 250+ products, and a regulatory layer (IRDAI master circulars, Insurance Act 1938) that materially overrides individual policy clauses. Buyers face:

- **Information asymmetry** — premiums are hidden behind callback flows; product wordings are 60-page PDFs.
- **Comparison fatigue** — features named differently across insurers (room-rent cap vs. category-of-room limit vs. accommodation eligibility — same thing).
- **Mis-selling risk** — agents are paid on conversion; advice is rarely consultative.
- **Regulatory complexity** — IRDAI mandates (waiting periods, free-look windows, standard exclusions) override insurer-specific clauses.

### 2.2 What we built

A **voice-first conversational advisor** that:

- **Listens** in English, Hindi, or Hinglish (Sarvam Saarika v2.5 STT).
- **Grounds** every factual claim in a retrieved PDF clause with `[Source: ...]` citation.
- **Refuses** when the corpus doesn't have the answer (4 faithfulness gates).
- **Compares** policies side-by-side using a 62-field structured schema.
- **Scores** each policy A–F via a rules-based scorecard (24 of 62 fields → 6 sub-scores).
- **Personalizes** — once the user shares profile info (age, dependents, income, conditions), scorecards re-compute and chat answers ground against the user's situation.
- **Speaks** in the user's language (Sarvam Bulbul v2 TTS), with three Indic drift gates checking the translated reply preserves numbers, citations, and meaning.

### 2.3 Success criteria (and current state)

| Goal | v1 status |
|---|---|
| Voice-first, full-duplex with barge-in | ✓ live ("Live ✓" toggle, mic continuously open, speak over the bot to interrupt) |
| Push-to-talk fallback | ✓ live (labeled `🎤 Push-to-talk` button; momentarily suspends Live for one turn, then resumes) |
| Hindi/Hinglish bidirectional | ✓ live (Sarvam translation cascade + 3 drift gates) |
| Cited answers grounded in PDFs | ✓ live (4-gate faithfulness) |
| Cross-policy comparison | ✓ live (DuckDB structured + Chroma vectors) |
| Personalised scorecards | ✓ live (profile RAG — profile becomes a vector chunk) |
| Refusal precision (refuse > mis-cite) | ✓ live (Gate 1-4) |
| Regulatory grounding (IRDAI) | ✓ live (after Playwright rescue past Akamai bot protection — [ADR-017](70-docs/60-decisions/ADR-017-irdai-corpus-playwright-rescue.md)) |
| Admin LLM control panel | ✓ live (in-app tab, IP+password gated — [ADR-023](70-docs/60-decisions/ADR-023-admin-panel-ip-gated.md)) |

### 2.4 Explicit non-goals (v1)

- **Real-time quotes** — premiums are illustrative bands with disclaimer ([ADR-007](70-docs/60-decisions/ADR-007-illustrative-pricing.md)).
- **Medical advice** — bot answers coverage questions, never clinical ones (persona rule 4).
- **Token-streaming LLM responses** — replies arrive as full messages today; token-by-token SSE is a v2 roadmap item.
- **Life / motor insurance** — v1 is health-only; v2 generalises ([ADR-002](70-docs/60-decisions/ADR-002-health-category-vertical.md)).
- **Sentiment classifier on raw scraped reviews** — IRDAI complaint numbers are primary-source; sentiment labels are curated snippet roll-ups, not LLM-extracted.

---

## 3. Two parallel flows

The bot is two flows running together — the customer's experience and the technology underneath. They run in lockstep; the table below pairs each step of the user journey with the technology underneath. On narrow screens both columns are scrollable; on a desktop GitHub view they sit side-by-side.

<table>
<tr>
<th width="50%">3A · Customer / Process Flow</th>
<th width="50%">3B · Technology Flow (same turn, underneath)</th>
</tr>
<tr><td>

**Step 1 — Land on the bot**
- Sees: chat panel + Marketplace · Premium · Profile · Admin tabs
- Suggested questions in the chat box
- "Voice on — just speak" pill (Live mode, default ON) with green dot
- "🎤 Push-to-talk" button as labeled fallback
- "Voice reply" toggle controls whether the bot speaks back

</td><td>

**Tech 1 — Page load**
- Next.js 14 SSR ships HTML in ~200 ms
- React hydrates; useEffect fetches `/api/health`, `/api/coverage`, `/api/profile/completeness`
- `localStorage` rehydrates `messages[]` + `sessionId` if returning user
- `useLiveConversation` opens the persistent mic stream

</td></tr>

<tr><td>

**Step 2 — Ask the first question (voice or text)**
- User just talks — VAD detects speech start/end automatically
- *"Suggest a health insurance plan for me"*
- Bot recognises fact-find intent. **`backend/sales_brain.py`** issues one NIM call per turn (KI-167 / [ADR-039](70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md)) using `response_format={"type":"json_object"}`; the LLM owns voice + flow + slot order, returning a `{reply, captures, slot_driving, complete}` JSON body. A deterministic post-processor normalizes the captured fields against the 9-slot schema.
- Slots in order: age → dependents → income → existing cover → primary goal → location → parents (conditional) → health → budget

</td><td>

**Tech 2 — User submits**
- Voice path: `MediaRecorder` blob → `POST /api/transcribe` → Sarvam Saarika v2.5 STT → text
- Text path: direct `POST /api/chat`
- Payload: `user_text`, `session_id`, `chat_history[]`, `return_audio`, `tts_language_code`, `view_context { active_view, active_policy_id, filters }`

**Tech 3 — Orchestrator entry, `handle_turn()`**
- `classify_intent(user_text)` → `fact_find / qa / comparison / recommendation`
- `detect_language(user_text)` → `english / indic`
- Indic cascade: if indic, Sarvam-M translates → English for reasoning, response translated back

**Tech 4 — Fact-find branch (KI-167 / [ADR-039](70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md); primary candidate is Google Gemini 2.0 Flash post-KI-179 / [ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md))**
- If profile empty AND intent ∈ {fact_find, recommendation, comparison}: hand the turn to `backend/sales_brain.py::drive_sales_brain()` — one LLM call against the Brain Fast chain (Google `gemini-2.0-flash` primary → NIM Qwen / Mistral 675B / Llama-4 Maverick → OpenRouter free → NIM Nemotron-49b last resort) using native JSON mode (`response_mime_type=application/json` on Gemini, `response_format={"type":"json_object"}` on NIM / OR). System prompt carries the 9-slot schema + current profile state. Response: `{reply, captures, slot_driving, complete}`. `backend/sales_brain_normalizer.py` normalizes captures → `session.update_profile_field()` → `profile_store.save_profile()` + `profile_rag.upsert_profile_chunk()`. Emit `reply` to the user.
- Else: continue to retrieval

</td></tr>

<tr><td>

**Step 3 — Profile-driven personalisation unlocks**
- Completeness bar reaches ≥ 60% → "personalised scores unlocked"
- Marketplace tab shows per-policy A-F grades **re-computed for this user's specific situation**
- Chat references your profile inline ("at 32 with 1 dependent…")

</td><td>

**Tech 5 — Profile update extraction (free-form turns)**
- `extract_profile_updates(user_text, session.profile)` via the fast-brain chain (Nemotron 30B primary)
- Validation: enum / type / bounds checks; drop on failure
- Apply via `session.update_profile_field()`
- `upsert_profile_chunk()` re-embeds profile → Chroma so this turn's retrieval sees the fresh state

</td></tr>

<tr><td>

**Step 4 — Free-form questions with citations**
- *"What's the room rent cap on Care Supreme?"*
- *"Compare cataract waiting in HDFC Optima vs ICICI Elevate"*
- Every factual claim gets `[Source: <policy>, p.<page>]` citation
- Click a citation → opens the policy detail modal

</td><td>

**Tech 6 — Retrieval, `retrieve(query, session_id)`**
- LRU cache (key = normalised query + filters) — cache hit skips Voyage + Chroma
- BGE-small embeds user text → 384-d vector
- Chroma cosine search, `top_k=5`
- Profile chunk (`doc_type='profile'`) boosted to top
- Second pass on regulatory chunks if query mentions IRDAI / Section
- Returns `list[RetrievedChunk(policy_id, page_start, page_end, text, source_url, score)]`

</td></tr>

<tr><td>

**Step 5 — Conversational profile updates (mid-chat)**
- User says: *"I was just diagnosed with diabetes"*
- Bot silently extracts → updates `session.profile` → re-upserts profile chunk → completeness bar ticks up → scores refresh
- No form-filling required mid-conversation

</td><td>

**Tech 7 — Brain selection, `pick_brain(intent, language)`**
- `intent ∈ {comparison, recommendation}` → Brain Main chain with probe-elected primary across the three-tier pool ([ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md)): Google `gemini-2.5-flash` → NIM Mistral Large 3 675B → NIM Llama-4 Maverick → NIM Qwen 3-Next 80B → OpenRouter `nemotron-3-super-120b:free` → NIM Nemotron-49b
- `intent ∈ {qa, fact_find}` → Brain Fast chain: Google `gemini-2.0-flash` → NIM Qwen → NIM Mistral 675B → NIM Llama-4 Maverick → OpenRouter free pool → NIM Nemotron-49b. `fact_find` turns go through `backend/sales_brain.py` with native JSON mode (KI-167 / [ADR-039](70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md))
- KI-080 election: 1 LLM call per turn (most cases) or 2 (primary fails real-time → next tier). Per-chain primary refreshed every 300s by background probe. On total three-tier exhaustion: graceful error to user (fail-loud — KI-179 preserves the [ADR-038](70-docs/60-decisions/ADR-038-nim-only-chains.md) principle even though the NIM-only candidate-pool scope is superseded).

**Tech 8 — System prompt construction, `build_messages()`**
- `[System: ADVISOR_PROMPT + USER PROFILE block + USER IS LOOKING AT (view_context) block]`
- `[Assistant/User: last 5 turns of chat_history]`
- `[User: USER QUESTION + RETRIEVED POLICY CLAUSES + reply instructions]`

**Tech 9 — Brain LLM call → reply text**
- `NimChainLLM.chat(...)` with cumulative chain budget (brain 35 s, fast-brain 22 s)
- `strip_think_tags()` removes `<think>…</think>` reasoning
- Capture `brain_model_actual` for non-circular judge selection

</td></tr>

<tr><td>

**Step 6 — View-aware grounding (the copilot effect)**
- User opens a policy detail modal
- Asks *"What's the waiting period on this?"*
- Bot resolves *"this"* → answers without re-stating the policy name

</td><td>

**Tech 10 — Faithfulness gates (`backend/faithfulness.py`)**
- Gate 1 — retrieval floor: top score ≥ 0.30
- Gate 2 — citation integrity: every cited policy was retrieved
- Gate 3 — numeric grounding: every ₹/%/days/months/years in the reply appears in retrieved chunks
- Gate 4 — JUDGE_CHAIN (Mistral Large 3 675B primary, different family from brain)
- All 4 pass → reply ships
- Any fail (non-Gate-1) → cross-check retry with the judge model as rescue brain → tagged `crosscheck-rescued-by-judge`
- Still fails → safe refusal + log to `logs/hallucinations.jsonl`

</td></tr>

<tr><td>

**Step 7 — Refusal as a feature**
- User: *"Does this policy cover space-tourism injuries?"*
- Bot: *"I'd rather not answer that without stronger evidence in the policy documents I have."*
- The SAFE failure mode in BFSI is refuse > mis-cite

</td><td>

**Tech 11 — Indic cascade (only if user spoke Hinglish)**
- Sarvam-M translates English reply → Hinglish
- Gate A — regex: digits / citations / currency preserved
- Gate B — Mistral Large 3 675B LLM-judge for semantic preservation
- Gate C — back-translation cosine ≥ 0.80
- Any gate fails → fall back to the English reply

</td></tr>

<tr><td>

**Step 8 — Hindi / Hinglish flow**
- User switches the UI language toggle, or just speaks Hinglish
- *"Care Supreme mein PED ka waiting period kya hai?"*
- Bot responds in Hinglish with citations preserved

</td><td>

**Tech 12 — TTS synthesis (if `return_audio=true`)**
- `tts_preprocess()` expands acronyms (PED → pre-existing disease) + strips markdown
- Sarvam Bulbul v2 synthesises → base64 WAV
- Bundled into the `ChatResponse` alongside `reply_text`

</td></tr>

<tr><td>

**Step 9 — Persistent chat across sessions**
- Close the tab, come back tomorrow: chat history + profile restored
- Sessions persisted to disk on the backend; `localStorage` on the frontend

</td><td>

**Tech 13 — Response delivered**
- `ChatResponse { reply_text, citations[], audio_base64, brain_used, profile_updates, faithfulness_passed, blocked }`
- Frontend renders text → plays the in-DOM `<audio controls>` (autoplay-on-mount; barge-in pauses it) → updates `profileCompleteness`
- `localStorage` persists chat history; `session_state._flush()` writes the on-disk session JSON
- `log_turn()` writes to `logs/turns.jsonl`

</td></tr>
</table>

```
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 1.  Page load                                                       │
│  -------                                                                  │
│  · Next.js 14 SSR ships HTML in ~200ms                                    │
│  · React hydrates; useEffect fetches /api/health, /api/coverage,         │
│    /api/profile/completeness                                              │
│  · localStorage rehydrates messages[] + sessionId if returning user      │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 2.  User submits a question                                         │
│  -------                                                                  │
│  · Voice path: MediaRecorder blob → POST /api/transcribe → Sarvam        │
│    Saarika v2.5 STT → text                                                │
│  · Text path: direct POST /api/chat                                       │
│  · Payload includes: user_text, session_id, chat_history[],              │
│    return_audio, tts_language_code, view_context{active_view,            │
│    active_policy_id, filters}                                             │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 3.  Orchestrator entry — handle_turn()                              │
│  -------                                                                  │
│  · classify_intent(user_text) → fact_find / qa / comparison /            │
│    recommendation                                                         │
│  · detect_language(user_text) → english / indic                          │
│  · Indic cascade entry: if indic, Sarvam-M translates → English for      │
│    reasoning, response will be translated back                            │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 4.  Fact-find OR free-form branch (KI-167 / ADR-039 / ADR-040)      │
│  -------                                                                  │
│  · If intent ∈ {fact_find, recommendation, comparison} AND profile        │
│    incomplete:                                                            │
│      drive_sales_brain() — one LLM call against Brain Fast chain         │
│      (Gemini 2.0 Flash → NIM Qwen / Mistral 675B → OR free)              │
│      using native JSON mode (response_mime_type / response_format)        │
│      → {reply, captures, slot_driving, complete}                          │
│      normalize captures → session.update_profile_field()                 │
│      → profile_store.save_profile() + profile_rag.upsert_profile_chunk() │
│      emit reply to user, RETURN early                                     │
│  · Else: set free_form_session = True, continue to retrieval             │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 5.  Profile update extraction (free-form only)                     │
│  -------                                                                  │
│  · extract_profile_updates(user_text, session.profile)                    │
│  · NIM Llama-3.3-70B returns JSON of high-confidence updates             │
│  · Validation: enum / type / bounds checks; drop on failure              │
│  · Apply via session.update_profile_field()                              │
│  · upsert_profile_chunk() re-embeds profile → Chroma                     │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 6.  Retrieval — retrieve(query, session_id)                         │
│  -------                                                                  │
│  · BGE-small embeds user_text → 384-d vector                              │
│  · Chroma cosine search, top_k=5                                          │
│  · Profile chunk (doc_type='profile') boosted to top of results          │
│  · Second pass on regulatory chunks if query mentions IRDAI / Section    │
│  · Returns list[RetrievedChunk(policy_id, page_start, page_end, text,    │
│    source_url, score)]                                                    │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 7.  Brain selection — pick_brain(intent, language)                  │
│  -------                                                                  │
│  · intent ∈ {comparison, recommendation} → Brain Main chain                │
│    Gemini 2.5 Flash → NIM Mistral 675B → NIM Llama-4 Maverick →           │
│    NIM Qwen 80B → OR nemotron-3-super-120b:free → NIM Nemotron-49b       │
│  · intent ∈ {qa, fact_find} → Brain Fast chain                            │
│    Gemini 2.0 Flash → NIM Qwen 80B → NIM Mistral 675B → NIM Llama-4 →     │
│    OR free pool → NIM Nemotron-49b last resort                            │
│  · fact_find turns dispatched to sales_brain.py (KI-167 / ADR-039)        │
│  · Three-tier election: Google → NIM → OpenRouter (KI-179 / ADR-040)      │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 8.  System prompt construction                                      │
│  -------                                                                  │
│  · build_messages() composes:                                             │
│    [System: ADVISOR_PROMPT + USER PROFILE block + USER IS LOOKING AT     │
│       (view_context) block]                                               │
│    [Assistant/User: last 5 turns of chat_history]                         │
│    [User: USER QUESTION + RETRIEVED POLICY CLAUSES + reply instructions] │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 9.  Brain LLM call → reply text                                     │
│  -------                                                                  │
│  · Provider-agnostic chat call via NimChainLLM (Google / NIM / OR)       │
│  · strip_think_tags() removes <think>…</think> chain-of-thought          │
│  · Capture brain_model_actual for non-circular judge selection           │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 10. Faithfulness gates                                              │
│  -------                                                                  │
│  · Gate 1: retrieval floor — top score ≥ 0.30                             │
│  · Gate 2: citation integrity — every cited policy was retrieved         │
│  · Gate 3: numeric grounding — every ₹/%/days/months/years in reply      │
│    appears in retrieved chunks                                            │
│  · Gate 4: LLM-judge (Mistral Large 3 675B, different family from brain)     │
│  · If all 4 pass → reply ships                                            │
│  · If any fails (non-Gate-1) → cross-check retry with Maverick           │
│  · If still fails → safe refusal + log to logs/hallucinations.jsonl      │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 11. Indic cascade (if user spoke Hinglish)                          │
│  -------                                                                  │
│  · Sarvam-M translates English reply → Hinglish                           │
│  · Gate A: regex check that digits/citations/currency preserved          │
│  · Gate B: Mistral Large 3 675B LLM-judge for semantic preservation          │
│  · Gate C: back-translation cosine ≥ 0.80                                 │
│  · Any gate fails → fall back to English reply                            │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 12. TTS synthesis (if return_audio=true)                            │
│  -------                                                                  │
│  · tts_preprocess() expands acronyms (PED → pre-existing disease) +      │
│    strips markdown                                                        │
│  · Sarvam Bulbul v2 synthesizes → base64 WAV                              │
│  · Bundled into ChatResponse alongside reply_text                         │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TECH 13. Response delivered                                              │
│  -------                                                                  │
│  · ChatResponse{reply_text, citations[], audio_base64, brain_used,       │
│    profile_updates, faithfulness_passed, blocked}                         │
│  · Frontend renders text → plays audio → updates profileCompleteness     │
│  · localStorage persists chat history                                    │
│  · log_turn() writes to logs/turns.jsonl                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Exhaustive tech architecture

### 4.1 System diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Next.js 14 (Vercel-ready)                                               │
│  · App Router · Tailwind · shadcn/ui                                     │
│  · Push-to-talk via MediaRecorder · localStorage persistence            │
│  · 5 view tabs: chat | marketplace | premium | profile | admin           │
└─────────────────────────────────────────────────────────────────────────┘
                                  │  HTTPS
                                  │  /api/chat · /api/transcribe · /api/profile
                                  │  /api/policies/* · /api/admin/* (IP-gated)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend  (HF Spaces / Render)                                   │
│  ┌──────────────┐    ┌────────────────────┐    ┌────────────────────┐   │
│  │ STT          │    │ ORCHESTRATOR       │    │ TTS                │   │
│  │ Sarvam       │───▶│ - intent classify  │───▶│ Sarvam Bulbul v2   │   │
│  │ Saarika v2.5 │    │ - profile extract  │    │ (acronym pre-exp)  │   │
│  └──────────────┘    │ - retrieve         │    └────────────────────┘   │
│                      │ - brain router     │                              │
│                      │ - 4-gate verifier  │                              │
│                      │ - cross-check retry│                              │
│                      │ - Indic cascade    │                              │
│                      │   (3 drift checks) │                              │
│                      └─────────┬──────────┘                              │
│                                │                                         │
│        ┌───────────────────────┼──────────────────────────┐              │
│        ▼                       ▼                          ▼              │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────────────┐   │
│  │ STRUCTURED   │    │ VECTOR STORE     │    │ 3-TIER BRAIN ROUTER  │   │
│  │ DuckDB       │    │ Chroma 0.5.20    │    │ Tier 0: Google AI    │   │
│  │ 62 fields    │    │ BGE-small (384d) │    │   Studio (Gemini     │   │
│  │ per policy   │    │ 800/120 chunk    │    │   2.0 / 2.5 Flash)   │   │
│  └──────────────┘    │ +profile chunk   │    │ Tier 1: NIM (Mistral │   │
│                      │   per session    │    │   675B, Llama-4,     │   │
│                      └──────────────────┘    │   Qwen 80B, Nemo-49) │   │
│                                              │ Tier 2: OR free pool │   │
│                                              │   (nemotron-120b,    │   │
│                                              │    qwen-80b:free)    │   │
│                                              │ KI-179 / ADR-040     │   │
│                                              │ $0 inference path    │   │
│                                              └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  │  (build time only)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  INGEST  (rag/ingest.py + rag/extract.py + tools/*)                      │
│  pdfplumber → 800-tok chunks → BGE embed → Chroma                        │
│  fast-brain chain (Nemotron 30B / Qwen 80B / Groq Llama-3.3 fallback) structured extract → 62-field Pydantic schema                │
│  Self-critique → confidence_pct per field                                │
└────────────────┬────────────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  206 source PDFs in HF Dataset rohitsar567/insurance-bot-data            │
│  · 188 product PDFs across 19 insurers (2 image-only dropped, KI-126)    │
│  · 18 regulatory PDFs (IRDAI master circulars, Insurance Act, etc.)      │
│  · 7,317 Chroma chunks (KI-125→127) · 166 marketplace cards              │
│  · Playwright same-origin fetch past Akamai for irdai.gov.in            │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Components in one line each

| Component | Path | Role |
|---|---|---|
| **Frontend** | `frontend/` | Next.js 14 App Router; chat + marketplace + premium + profile + admin tabs |
| **API gateway** | `backend/main.py` | FastAPI + Pydantic; OpenAPI auto-served; `openapi-typescript` codegens FE types |
| **Orchestrator** | `backend/orchestrator.py` | Intent classify → profile-extract → retrieve → brain router → 4-gate faithfulness → cross-check retry → Indic cascade |
| **Faithfulness verifier** | `backend/faithfulness.py` | Retrieval floor + citation integrity + numeric grounding + LLM-judge |
| **Indic cascade** | `backend/translator.py`, `backend/translation_check.py` | Sarvam-M translates in & out; 3 drift gates |
| **Profile extractor** | `backend/profile_extractor.py` | Lightweight LLM extracts profile updates from free-form messages |
| **Profile RAG** | `backend/profile_rag.py` | Profile becomes a single Chroma chunk (`doc_type='profile'`) per session |
| **Persona** | `backend/persona.py` | System prompt; `build_messages()` injects profile + view_context |
| **Session state** | `backend/session_state.py` | Per-session profile, fact-find awaiting state, disk-backed JSON |
| **Retrieval** | `rag/retrieve.py` | Chroma cosine search + profile boost + regulatory second-pass |
| **Structured extraction** | `rag/extract.py` | fast-brain chain (Nemotron 30B / Qwen 80B / Groq Llama-3.3 fallback) JSON extraction over 62-field Pydantic schema |
| **Scorecard** | `backend/scorecard.py` | Pure Python; 24 of 62 fields → 6 sub-scores → A–F |
| **Admin** | `backend/admin.py` | LLM health, chain reorder, force-fresh probe; IP-gated |
| **Eval** | `eval/` | Gold Q&A pipelines + the judge chain (Mistral Large 3 675B primary, different family from brain) grader |
| **Knowledge base** | `kb/` | 222 markdown policy sheets + scorecard + reviews + premiums + audit trail |

### 4.3 Model stack

Every LLM role is served by a **three-tier candidate pool** elected over by a probe-driven sticky-primary scheme ([ADR-031](70-docs/60-decisions/ADR-031-sticky-primary-election.md), end-to-end spec in [ADR-032](70-docs/60-decisions/ADR-032-llm-chain-architecture.md), current candidate-pool shape in [ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md)), never a hardcoded single model. Chains preserve brain ↔ judge family diversity (Gemini brain ↔ Mistral judge) so any failover still produces non-circular grading. Tier 0 (Google AI Studio) is the steady-state primary; Tier 1 (NIM) is the fallback; Tier 2 (OpenRouter free pool) is the diversity tail before the last-resort NIM Nemotron-49b. Every candidate enforces native JSON mode server-side — `response_mime_type=application/json` on Google, `response_format={"type":"json_object"}` on NIM and on the audited OR free-tier models (KI-178).

| Role | Primary | Fallback chain (in order) | Provider(s) | Why this primary |
|---|---|---|---|---|
| **Brain Main** (comparison, recommendation, synthesis) | **Google `gemini-2.5-flash`** (1500 req/day free, native JSON mode) | NIM Mistral Large 3 675B → NIM Llama-4 Maverick 17B/128E → NIM Qwen 3-Next 80B → OpenRouter `nvidia/nemotron-3-super-120b-a12b:free` → NIM Nemotron-Super 49B v1.5 (last resort) | Google + NIM + OpenRouter | Best-in-class conversational + synthesis quality on the free path; 1500 req/day adequate for demo + early production. NIM Mistral 675B Tier 1 fallback preserves dense 675B reasoning when Google quota / 429 trips. KI-179 / [ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md). |
| **Brain Fast** (voice turns, sales-brain fact-find KI-167, QA) | **Google `gemini-2.0-flash`** (same 1500 req/day quota, lower-latency tier for fast turns) | NIM Qwen 3-Next 80B → NIM Mistral Large 3 675B → NIM Llama-4 Maverick → OpenRouter `nemotron-3-super-120b:free` + `qwen3-next-80b:free` → NIM Nemotron-Super 49B v1.5 (last resort) | Google + NIM + OpenRouter | Bottleneck on these short jobs is TTFT — Gemini 2.0 Flash optimised for low-latency JSON. Sales brain (KI-167) calls this chain with native JSON mode for guaranteed structured output. KI-179 / [ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md). |
| **Judge** (faithfulness Gate 4, Hinglish drift, eval grader; KI-171 skips on `fact_find` + `recommendation`) | **NIM Mistral Large 3 675B** (`mistralai/mistral-large-3-675b-instruct-2512`) | NIM Llama-4 Maverick 17B/128E → OpenRouter `qwen/qwen3-next-80b-a3b-instruct:free` → NIM Nemotron-Super 49B v1.5 | NIM + OpenRouter | Different family from the Gemini brain (Mistral, not Google / Llama / Qwen) so the judge sees the brain's output from a genuinely different decision surface. 675B dense, MIT, ~4.3s on NIM. Brain ↔ judge family diversity is the structural defence against circular grading. |
| **Profile extractor** (free-form profile updates → 9-slot schema, ADR-022) | Brain Fast chain (Gemini 2.0 Flash primary) | inherits Brain Fast chain | Google + NIM + OpenRouter | Short prompt, structured JSON out — same chain as sales brain. |
| **Fact-find normalizer** (user answer → typed slot value) | Brain Fast chain (Gemini 2.0 Flash primary) | inherits Brain Fast chain | Google + NIM + OpenRouter | Same shape as profile extractor — narrow input, narrow JSON output. |
| **Sales brain** (fact-find conversation driver — KI-167 / [ADR-039](70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md), KI-179 / [ADR-040](70-docs/60-decisions/ADR-040-google-gemini-primary.md)) | Brain Fast chain (Gemini 2.0 Flash primary) | inherits Brain Fast chain | Google + NIM + OpenRouter | One LLM call per turn using native JSON mode. System prompt carries the 9-slot schema + current profile state; LLM owns voice + flow + slot order, returns `{reply, captures, slot_driving, complete}`. Replaces the scripted-`prompt_en` + paraphraser + opener-rotation + `<FF>` trailer + `_canonical_fallback` machinery (ADR-027 / ADR-030 retired). |
| **Indic translation** (English ↔ Hindi / Hinglish / vernacular) | Sarvam-M | — (no fallback; Indic is non-substitutable) | Sarvam | Best-in-class on Hinglish and code-mixed Indian languages; no open-weights frontier model is competitive here. |
| **STT** (speech-to-text) | Sarvam Saarika v2.5 (`saarika:v2.5`) | — | Sarvam | Best-in-class on Indian-accented English and Hinglish; WebM transcoded to 16kHz mono WAV at the gateway (`backend/providers/sarvam_stt.py`). |
| **TTS** (text-to-speech) | Sarvam Bulbul v2 (`bulbul:v2`, speaker `anushka`) | — | Sarvam | Best-in-class on Hinglish prosody; 22.05kHz output, base64 WAV from Sarvam endpoint. |
| **Embeddings** | Voyage `voyage-3` (legacy ingest only; hot path uses BGE) | BGE-small-en-v1.5 (384d, local CPU) | Voyage (offline) + Local (hot path) | BGE on-CPU avoids network on every retrieval ([ADR-011](70-docs/60-decisions/ADR-011-bge-local-embeddings.md)); Voyage retained for back-compat with already-extracted artifacts. |

**Chain budgets.** Brain `20s` per-link / `35s` total; fast brain `12s` / `22s`; judge `30s` / `75s`. The per-link timeout is dynamically clipped to the remaining chain budget so a single fallback can never blow past the ceiling (KI-021).

**Health-aware filtering.** A background probe loop (`backend/llm_health.py`) marks individual NIM models as up/down. `NimChainLLM.filter_chain()` skips known-down models before the first call; if every candidate is filtered, the full chain is tried anyway since the probe may be stale. On total chain exhaustion, one synchronous probe refresh is triggered before raising.

### 4.4 Major design decisions (the short list)

| Decision | Choice | Detail |
|---|---|---|
| Scope shape | Vertical slice, one category | [ADR-001](70-docs/60-decisions/ADR-001-vertical-slice-scope.md) |
| Category | Health | [ADR-002](70-docs/60-decisions/ADR-002-health-category-vertical.md) |
| Corpus origin | Curated, not user-upload | [ADR-003](70-docs/60-decisions/ADR-003-curated-corpus.md) |
| Retrieval | Hybrid structured + vector | [ADR-004](70-docs/60-decisions/ADR-004-hybrid-structured-vector.md) |
| Frontend stack | Next.js 14 + FastAPI | [ADR-005](70-docs/60-decisions/ADR-005-nextjs-fastapi-frontend.md) |
| Pricing | Illustrative, never quote | [ADR-007](70-docs/60-decisions/ADR-007-illustrative-pricing.md) |
| Persona | Consultative advisor (IFA) | [ADR-008](70-docs/60-decisions/ADR-008-consultative-advisor-persona.md) |
| Embeddings | Local BGE-small | [ADR-011](70-docs/60-decisions/ADR-011-bge-local-embeddings.md) |
| LLM provider | NIM single provider | [ADR-019](70-docs/60-decisions/ADR-019-nim-single-provider-consolidation.md) |
| Code vs data | Two-repo + HF Dataset | [ADR-020](70-docs/60-decisions/ADR-020-code-data-split-hf-dataset.md) |
| View-aware chat | system prompt injection | [ADR-021](70-docs/60-decisions/ADR-021-view-aware-system-prompt.md) |
| Profile updates in chat | LLM extractor | [ADR-022](70-docs/60-decisions/ADR-022-conversational-profile-updates.md) |
| Admin panel | IP+password gated, in-app tab | [ADR-023](70-docs/60-decisions/ADR-023-admin-panel-ip-gated.md) |
| Resilience | Triple-mirror code + data | [ADR-024](70-docs/60-decisions/ADR-024-triple-mirror-code-and-data.md) |

Every D-NNN in the legacy decisions log is now a stand-alone ADR — see [`70-docs/60-decisions/README.md`](70-docs/60-decisions/README.md) for the full ADR index.

---

## 5. Data architecture

### 5.1 The corpus (206 documents)

| Type | Count | Source | Notes |
|---|---|---|---|
| Product PDFs | 188 | 19 insurers' public websites | Wordings + Brochures + CIS (2 image-only PDFs dropped in KI-126) |
| Regulatory PDFs | 18 | irdai.gov.in, indiacode.nic.in, others | Playwright rescue past Akamai |
| Structured extractions (JSON) | 201 | fast-brain chain (Nemotron 30B / Qwen 80B / Groq Llama-3.3 fallback) extraction | 62-field Pydantic schema |
| Curated `policy_facts` JSONs | 253 | Hand-curated marketplace facts | KI-137 ingested 21 of these into Chroma so the bot can retrieve them |
| Vector chunks (Chroma) | 7,317 | BGE-small @ 800/120 | wordings 5,401 · brochure 611 · regulatory 498 · prospectus 483 · cis 302 · curated 21 · profile 1 |
| Marketplace cards | 166 | Aggregated across 19 real insurers | One card per IRDAI-filed product after KI-133 / KI-141 / KI-142 / KI-145 dedup |
| Policy markdown sheets | 222 | Generated from extractions | One per policy_id in `kb/policies/` |

19 real insurers (alphabetical): Acko, Aditya Birla, Bajaj Allianz, Care Health, Cholamandalam MS, Go Digit, HDFC ERGO, ICICI Lombard, IFFCO Tokio, IndusInd General (formerly Reliance General — renamed in KI-144), ManipalCigna, National Insurance, New India Assurance, Niva Bupa, Oriental Insurance, Reliance General, Royal Sundaram, SBI General, Star Health, Tata AIG. Internally there are 20 Chroma slugs (the 19 above + a `regulatory` bucket); the regulatory + `profile` slugs are filtered out of every user-facing marketplace count (KI-129 / KI-130 / KI-132).

### 5.2 Ingestion pipeline

```
PDF (rag/corpus/<insurer>/<policy>__<doctype>.pdf)
  │
  ▼
rag/extract.py    →    rag/extracted/<policy_id>.json    (62-field structured)
  │                                                       (fast-brain chain (Nemotron 30B / Qwen 80B / Groq Llama-3.3 fallback) with Pydantic schema)
  │
  ▼
rag/ingest.py     →    rag/vectors/chroma.sqlite3        (text chunks + embeddings)
  │                    + HNSW binary files in vectors/   (800-token chunks, 120 overlap)
  │
  ▼
40-data/policy_facts/<policy_id>.json                       (hand-curated facts for marketplace UI)
40-data/premiums/illustrative_premiums.json                  (anchor table for premium calculator)
40-data/reviews/<insurer-slug>.json                          (IRDAI complaints/10K + sentiment roll-ups)
  │
  ▼
kb/policies/<policy_id>.md                               (markdown writeup; regen via rag/build_kb.py)
```

Full per-stage detail: [`70-docs/20-data-pipeline/ingestion-policy.md`](70-docs/20-data-pipeline/ingestion-policy.md).

### 5.3 Storage topology

| Storage | What lives there | Quota |
|---|---|---|
| HF Space repo (origin) | Code only (`backend/`, `frontend/`, `rag/*.py`, `eval/`, `kb/`, `40-data/`, configs) | 1 GB free tier |
| HF Dataset | Heavy data: `rag/corpus/`, `rag/vectors/`, `rag/extracted/` | 50 GB free tier (we use ~500 MB) |
| GitHub code repo | Mirror of HF Space | Standard GitHub |
| GitHub data repo (LFS) | Mirror of HF Dataset | 1 GB LFS + 1 GB/mo bandwidth |
| Local Mac | Working tree + `rag/_hf_dataset_backup/` for offline recovery | Filesystem |

Runtime path: Docker container running on HF Space `snapshot_download`s the dataset at build time → serves everything from local container disk → end users never hit GitHub or the dataset at request time.

---

## 6. Quality & safety

### 6.1 The four faithfulness gates ([`backend/faithfulness.py`](backend/faithfulness.py))

Every reply, every turn:

| Gate | Function | Blocks if |
|---|---|---|
| 1. Retrieval floor | `_gate_retrieval_floor` | Top retrieval score < 0.30 — nothing to ground in |
| 2. Citation integrity | `_gate_citation_integrity` | Reply cites a policy_name not in retrieved chunks |
| 3. Numeric grounding (regex) | `_gate_numeric_grounding` | Any ₹/%/days/months/years in reply doesn't appear in chunks |
| 4. LLM-judge | `_gate_llm_judge` | the judge chain (Mistral Large 3 675B primary, different family from brain) (different family from brain) flags unsupported claim |

If any gate (other than Gate 1) fails, the **cross-check retry** re-runs the same prompt on Mistral Large 3 675B. If that passes its gates, the rescued reply ships with `crosscheck-rescued-by-judge` brain tag. Otherwise → safe refusal + log to `logs/hallucinations.jsonl`.

### 6.2 Indic drift gates ([`backend/translation_check.py`](backend/translation_check.py))

When the user speaks Hinglish:

| Gate | Method |
|---|---|
| A — Regex anchors | Every digit / currency / citation in the English reply must appear in the Indic reply |
| B — LLM-judge | the judge chain (Mistral Large 3 675B primary, different family from brain) scores semantic faithfulness across languages |
| C — Back-translation cosine | Sarvam back-translates Hinglish → English; cosine vs original ≥ 0.80 |

Any gate fails → revert to English reply (correct facts even if not preferred language).

### 6.3 Eval methodology

Gold Q&A in three pipelines:

- **A — Auto-templated:** 15 templates × ~80 policies = 1,100 candidates, ~300 committed.
- **B — LLM-drafted nuanced:** V4-Pro drafts 5 buyer-style multi-clause questions per top policy.
- **C — Adversarial:** 30-40 hand-written out-of-corpus / out-of-policy-type / Hinglish / multi-policy.

Grader: the judge chain (Mistral Large 3 675B primary, different family from brain) (non-circular — different family from the DeepSeek brain). Source: [`eval/generate_gold.py`](eval/generate_gold.py), [`eval/run.py`](eval/run.py).

Honest current numbers (2026-05-12 run on 25 questions):

| Metric | Value | Comment |
|---|---|---|
| Factual accuracy | 40.0% | See [`80-audit/ENTERPRISE_AUDIT.md`](80-audit/ENTERPRISE_AUDIT.md) for the post-fix baseline + per-question-type breakdown |
| Citation accuracy | 50.0% | Same |
| Refusal precision | 44.4% | Same |
| Blocked by faithfulness | 12 / 25 | Gates working; aggressively biased toward refusal |

The headline number reflects an aggressive gate posture (refuse > mis-cite) — the safe failure mode in BFSI. v1.1 work: soften Gate 3 regex; null-skip Pipeline A on missing source fields.

---

## 7. Document ecosystem guide

The repo's documentation lives in `70-docs/`, organised into 8 numbered buckets. Numeric prefixes sort buckets in reading order; files inside each bucket use `kebab-case` for URL safety and grep-friendliness.

```
70-docs/
├── 00-overview/              ← "what is this and why does it exist"
├── 10-architecture/          ← "how it works" (deep dives)
├── 20-data-pipeline/         ← "where the knowledge comes from"
├── 30-engineering/           ← "how the code is laid out"
├── 40-evaluation/            ← "how we measured success"
├── 50-operations/            ← "how to run / maintain / debug"
├── 60-decisions/             ← Architecture Decision Records (ADRs 001-028)
└── 70-reference/             ← Schemas, glossary, indexes

80-audit/                 ← Production-readiness audit + defect register
├── ENTERPRISE_AUDIT.md        ← Master defect log (severity, evidence, fix status)
└── full_<run-id>/             ← Per-run audit transcripts + analyzed reports
```

### 7.1 Per-bucket guide

| Bucket | What's there now | What to read for what |
|---|---|---|
| `00-overview/` | [`problem-statement.md`](70-docs/00-overview/problem-statement.md), [`roadmap.md`](70-docs/00-overview/roadmap.md) | Start here for product context, requirements, success criteria, v2 plan |
| `10-architecture/` | [`system-overview.md`](70-docs/10-architecture/system-overview.md), [`stack-rationale.md`](70-docs/10-architecture/stack-rationale.md), [`safety-architecture.md`](70-docs/10-architecture/safety-architecture.md), [`scoring-methodology.md`](70-docs/10-architecture/scoring-methodology.md), [`scoring-knowledge-graph.md`](70-docs/10-architecture/scoring-knowledge-graph.md), [`scoring-tie-breaker-rubric.md`](70-docs/10-architecture/scoring-tie-breaker-rubric.md) | How the system is built; the why behind each stack choice; how scoring works |
| `20-data-pipeline/` | [`ingestion-policy.md`](70-docs/20-data-pipeline/ingestion-policy.md), [`information-source-map.md`](70-docs/20-data-pipeline/information-source-map.md) | How PDFs become chunks; where every source URL lives + audit status |
| `30-engineering/` | [`needs-analysis-flow.md`](70-docs/30-engineering/needs-analysis-flow.md), [`discovery-script.md`](70-docs/30-engineering/discovery-script.md) | How the fact-find loop works; discovery script for new contributors |
| `40-evaluation/` | [`eval-methodology.md`](70-docs/40-evaluation/eval-methodology.md) | Gold-Q&A design, grader choice, results interpretation |
| `50-operations/` | (operational runbooks — to be filled) | How to run, deploy, debug |
| `60-decisions/` | 28 ADRs + [`README.md`](70-docs/60-decisions/README.md) index + `legacy-decisions-monolith.md` archive | The full decision history with alternatives and supersession tracking |
| `70-reference/` | (schemas + glossary — to be filled) | BFSI terms, insurer slug map, citation grammar |
| `80-audit/` | [`ENTERPRISE_AUDIT.md`](80-audit/ENTERPRISE_AUDIT.md) + per-run audit transcripts | Production-readiness defect register with severity, evidence, fix status. Multi-persona simulation transcripts from the audit runner. |

### 7.2 Root-level documents

| File | Purpose |
|---|---|
| `README.md` (this file) | Master entry point + executive bible |
| `ARCHITECTURE.md` | One-page diagram + index into `70-docs/10-architecture/` |
| `QUICKSTART.md` | Run-locally-in-5-minutes guide (see §8 below) |
| `Dockerfile` | Production build — pulls dataset at build time, serves Next.js + FastAPI |
| `entrypoint.sh` | Docker entrypoint — boots both processes |
| `requirements.txt` | Python deps (pinned) |
| `render.yaml` | Legacy Render config (HF Space is current production) |

### 7.3 Navigation shortcuts

- **Reviewer with 20 minutes:** read this README end-to-end.
- **Engineer joining the project:** read [`70-docs/00-overview/problem-statement.md`](70-docs/00-overview/problem-statement.md) → [`70-docs/10-architecture/system-overview.md`](70-docs/10-architecture/system-overview.md) → [`70-docs/60-decisions/README.md`](70-docs/60-decisions/README.md) → trace `backend/orchestrator.py`.
- **Compliance auditor:** read [`70-docs/10-architecture/safety-architecture.md`](70-docs/10-architecture/safety-architecture.md) → `logs/hallucinations.jsonl` → [`kb/AUDIT_TRAIL.md`](kb/AUDIT_TRAIL.md).
- **Production readiness reviewer:** read [`80-audit/ENTERPRISE_AUDIT.md`](80-audit/ENTERPRISE_AUDIT.md) for the master defect register (severity-tagged, with evidence + fix status) and [`80-audit/full_<run-id>/report.md`](80-audit/) for the latest persona-simulation findings.
- **Replicating the data pipeline:** read [`70-docs/20-data-pipeline/ingestion-policy.md`](70-docs/20-data-pipeline/ingestion-policy.md) → trace `rag/extract.py` + `rag/ingest.py`.

---

## 8. Quick start & local development

### 8.1 Prerequisites

- macOS or Linux
- Python 3.11+
- Node.js 20+
- ~5 GB free disk for venv + node_modules + dataset

### 8.2 Clone & install

```bash
# Code
git clone https://github.com/rohitsar567/insurance-sales-bot.git
cd insurance-sales-bot

# Data (for local-only runs — Docker pulls automatically)
git clone https://github.com/rohitsar567/insurance-sales-bot-data.git rag/_hf_dataset_backup
# OR via huggingface_hub:
# python -c "from huggingface_hub import snapshot_download; \
#   snapshot_download(repo_id='rohitsar567/insurance-bot-data', repo_type='dataset', \
#                     local_dir='rag/_hf_dataset_backup')"

# Move data into place for the bot
cp -R rag/_hf_dataset_backup/rag/* rag/   # corpus/, extracted/, vectors/
```

### 8.3 Configure secrets

```bash
cp .env.example .env
# Edit .env and fill in the keys (see 70-docs/60-decisions/ADR-010-secret-handling.md):
#   GOOGLE_API_KEY        — Tier 0 brain (Gemini 2.0/2.5 Flash, KI-179 / ADR-040)
#   NVIDIA_NIM_API_KEY    — Tier 1 brain fallback + Judge primary
#   OPENROUTER_API_KEY    — Tier 2 diversity pool (free-tier OR models)
#   SARVAM_API_KEY        — STT / TTS / Indic translation (non-substitutable)
#   VOYAGE_API_KEY        — offline ingest embeddings only (BGE on hot path)
#   HF_TOKEN              — data-dataset pull at boot
#   ADMIN_PASSWORD        — /api/admin/* gating (post-KI-097, password-only)
#   GROQ_API_KEY          — DORMANT (no chain references it post-KI-160)
chmod 600 .env
```

### 8.4 Run backend

```bash
# Recommended: uv-managed venv
uv venv ~/.cache/uv-venvs/insurance-sales-bot --python 3.11
ln -s ~/.cache/uv-venvs/insurance-sales-bot .venv
uv pip install -r requirements.txt

uvicorn backend.main:app --reload --port 7860
```

### 8.5 Run frontend

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

### 8.6 Verify

- Frontend at `localhost:3000` should show the chat UI.
- `localhost:7860/api/health` should return `{"status":"ok", "providers_ok": {"sarvam": true, "nvidia_nim": true}}`.
- `localhost:7860/api/coverage` should return 166 marketplace cards across 19 real insurers (the `profile` and `regulatory` slugs are filtered out of user-facing counts per KI-129 / KI-130 / KI-132).

---

## 9. Deployment & storage topology

### 9.1 Triple-mirror layout

| Layer | Code | Data |
|---|---|---|
| **HuggingFace** | Space `rohitsar567/InsuranceBot` (origin) | Dataset `rohitsar567/insurance-bot-data` |
| **GitHub** | Repo `rohitsar567/insurance-sales-bot` | Repo `rohitsar567/insurance-sales-bot-data` (Git LFS) |
| **Local Mac** | `~/Developer/Insurance Sales Bot/` working tree | `~/Developer/Insurance Sales Bot/rag/_hf_dataset_backup/` |

### 9.2 Push fan-out

```bash
# After a commit:
git push origin main      # → HF Space (auto-deploys via Docker rebuild)
git push github main      # → GitHub mirror

# For data updates (rare):
cd rag/_hf_dataset_backup  # or wherever the data lives
git push origin main      # → HF Dataset
git push github main      # → GitHub LFS data repo
```

Verification: `git rev-list --count main...origin/main` and `…main...github/main` should both equal 0.

### 9.3 Production runtime path

```
End user
  │  HTTPS
  ▼
rohitsar567-insurancebot.hf.space   (HF Space Docker container)
  │
  │  Container has data baked in (snapshot_download at build time)
  ▼
FastAPI + Next.js serve from local container disk; calls go out to:
  - NIM (integrate.api.nvidia.com) for brain + judge + extractor
  - Sarvam (api.sarvam.ai) for STT + TTS + Indic translation
```

End users **never** touch GitHub. The data repo is for portfolio mirroring + disaster recovery — not a runtime dependency.

### 9.4 Admin operations

| Operation | Command / surface |
|---|---|
| Rotate any API key | Edit local `.env` → `python tools/set_hf_secrets.py` → Space auto-restarts |
| Add admin IP | Edit `ADMIN_IP_ALLOWLIST` in `.env` (comma-separated) → `set_hf_secrets.py` |
| Reorder LLM chain | In-app **Admin · Access panel** tab → "Force fresh probe" → drag-reorder |
| Force fresh model probe | Same tab → "Force fresh probe (slow)" |
| Disaster recovery (HF Dataset lost) | Re-upload from local `rag/_hf_dataset_backup/` via `huggingface_hub.HfApi.upload_folder` |
| Cold start | First request after ~15 min idle takes ~50s; subsequent are fast |

---

## 10. Rebuild from scratch

This section is written so a fresh **Claude Code** session pointed at an empty directory could rebuild the entire project end-to-end. The goal is to make the project reproducible from this document plus the linked dependencies.

### 10.1 Required external accounts

1. **Sarvam** — STT, TTS, Indic translation. Get API key at dashboard.sarvam.ai.
2. **NVIDIA NIM** — brain + judge + extractor. Get `nvapi-...` key at build.nvidia.com.
3. **HuggingFace** — Space + Dataset hosting. Get write token at huggingface.co/settings/tokens.
4. **GitHub** — mirror code + data. Personal Access Token with `repo` scope.
5. **Voyage / Groq / OpenRouter** — optional fallback (kept for flexibility).

### 10.2 Build sequence

1. **Corpus acquisition** — use `tools/` agent crawl + Playwright fallback (see [ADR-017](70-docs/60-decisions/ADR-017-irdai-corpus-playwright-rescue.md)) to fetch 19 insurers' PDFs (188 product PDFs total) + 18 regulatory PDFs into `rag/corpus/`.

2. **Structured extraction** — `rag/extract.py` runs fast-brain chain (Nemotron 30B / Qwen 80B / Groq Llama-3.3 fallback) over each PDF with the 62-field Pydantic schema. Output to `rag/extracted/<policy_id>.json`.

3. **Embedding + indexing** — `rag/ingest.py` chunks each PDF at 800/120, embeds with BGE-small-en-v1.5, writes to `rag/vectors/chroma.sqlite3` + HNSW binaries.

4. **Data publish** — push `rag/corpus/`, `rag/extracted/`, `rag/vectors/` to HF Dataset `rohitsar567/insurance-bot-data`.

5. **Knowledge base regeneration** — `python -m rag.build_kb` regenerates 222 markdown sheets in `kb/policies/` from extracted JSONs.

6. **Backend** — implement FastAPI app with the endpoints listed in §4.2. Key files: `backend/main.py`, `backend/orchestrator.py`, `backend/faithfulness.py`, `backend/persona.py`, `backend/translator.py`, `backend/translation_check.py`, `backend/profile_extractor.py`, `backend/profile_rag.py`, `backend/session_state.py`, `backend/scorecard.py`, `backend/admin.py`, `backend/providers/*`.

7. **Frontend** — Next.js 14 App Router. Tailwind + shadcn/ui. Key file: `frontend/src/app/page.tsx` (orchestrates all views: chat, marketplace, premium, profile, admin). Voice via MediaRecorder. Persistent state via localStorage.

8. **Eval harness** — `eval/generate_gold.py` builds the gold Q&A. `eval/run.py` grades using the judge chain (Mistral Large 3 675B primary, different family from brain). Output to `eval/results.md`.

9. **Deploy** — Dockerfile bundles backend + frontend (Next.js standalone) into one image. `snapshot_download` pulls data at build time. Push to HF Space — auto-deploys.

10. **Mirror to GitHub** — `git remote add github https://github.com/rohitsar567/insurance-sales-bot.git` + push. Same for data repo with Git LFS.

### 10.3 Critical files to seed first (priority order)

A new Claude Code session should ingest these to bootstrap understanding:

1. **This README** — entire project context.
3. **[`70-docs/60-decisions/`](70-docs/60-decisions/)** — 24 ADRs covering every meaningful decision.
4. **[`backend/orchestrator.py`](backend/orchestrator.py)** — the single file that defines a turn.
5. **[`backend/faithfulness.py`](backend/faithfulness.py)** — the 4-gate verifier.
6. **[`backend/persona.py`](backend/persona.py)** — the system prompt + message builder.
7. **[`rag/retrieve.py`](rag/retrieve.py)** — retrieval logic with profile boost.
8. **[`frontend/src/app/page.tsx`](frontend/src/app/page.tsx)** — full UI orchestration.

### 10.4 Hidden gotchas a rebuilder will hit

| Gotcha | Where it bites | Fix |
|---|---|---|
| IRDAI URLs return Akamai bot-challenge HTML | Initial corpus crawl | Playwright same-origin fetch (ADR-017) |
| Sarvam-M output cap 2048 tokens truncates JSON | Extraction phase | Use fast-brain chain (Nemotron 30B / Qwen 80B / Groq Llama-3.3 fallback) instead (ADR-019) |
| Voyage 3 RPM free-tier blocks 206-PDF ingest | Embedding phase | Switch to local BGE-small (ADR-011) |
| Multiple LLM providers' free-tier limits collide on grader | Eval phase | Consolidate to NIM (ADR-019) |
| HF Space 1 GB cap rejects vector DB | Deploy phase | Split data to HF Dataset (ADR-020) |
| Chroma sqlite3 (157 MB) exceeds GitHub 100 MB per-file limit | GitHub mirror | Use Git LFS for the data repo (ADR-024) |
| iCloud-synced `~/Documents/` causes node_modules upload churn | Local dev | Move project to `~/Developer/` (not in iCloud) |
| Apple Python 3.9 lacks `int | None` syntax | LaunchAgent scripts | Add `from __future__ import annotations` |
| Admin endpoints return 404 (not 401) for unauthorized callers | Admin panel | Intentional — hides existence; pair IP allowlist with X-Admin-Password header |

---

## Footer

**Authored 2026-05-13. Last updated 2026-05-15 (KI-125 → KI-150 — corpus rebuild + marketplace dedup + voice retune + IndusInd migration).**

Live demo: https://rohitsar567-insurancebot.hf.space  
Code: https://github.com/rohitsar567/insurance-sales-bot  
Data: https://github.com/rohitsar567/insurance-sales-bot-data  
Contact: rohitsar567@gmail.com

If you're reviewing this for Sarvam: thank you for your time. Every architectural choice is in [`70-docs/60-decisions/`](70-docs/60-decisions/) with alternatives and reasoning. Every refusal in the demo is logged in `logs/hallucinations.jsonl` with the failing gate. The project is structured so a new engineer could ship v1.1 by Friday.
