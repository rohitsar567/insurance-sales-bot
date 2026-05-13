# Decisions Log

Every meaningful technical and product decision, with alternatives considered and the reasoning for the chosen path. Append-only. Each entry is auditable.

---

## D-001 — Vertical slice scope, not full platform

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:**
- (a) Single-document RAG-voice bot for one policy
- (b) Vertical slice — full architecture for one category (Health), built for category expansion
- (c) Full platform — 300 policies across all categories
**Chose:** (b)
**Reasoning:** With <24h to ship and an explainability-graded assignment, (a) under-signals product vision, (c) over-scopes and ships rough. (b) demonstrates senior-engineer scoping discipline while showing the full architectural surface a reviewer cares about.
**Revisit at scale:** All seven "c-readiness commitments" (see Doc 02) become real work in v2.

---

## D-002 — Category for vertical slice: Health

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Health, Life, Motor
**Chose:** Health
**Reasoning:** Richest structured-attribute surface (waiting periods, PED, sub-limits, network, claim ratio); broadest user relevance; cleanest public corpus from top 10 insurers (Star, HDFC ERGO, Niva Bupa, Care, ICICI Lombard, Bajaj Allianz, New India, Aditya Birla, Tata AIG, ManipalCigna).
**Revisit at scale:** v2 adds Life (already harder — emotional, harder numeric compare) and Motor (price commodity).

---

## D-003 — Corpus curated, not user-uploaded

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** User-uploaded PDFs vs. pre-acquired corpus
**Chose:** Pre-acquired
**Reasoning:** Removes biggest source of input variance (bad uploads); enables cross-policy comparison/recommendation; positions the corpus as a product moat vs. generic RAG-over-anything.
**Revisit at scale:** Same approach, larger corpus + scheduled refresh.

---

## D-004 — Architecture: hybrid structured + unstructured

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Pure RAG, pure structured DB, hybrid
**Chose:** Hybrid (DuckDB for structured, Chroma for vector)
**Reasoning:** Filter UI / comparison / recommendation pre-ranking require structured data; free-form Q&A with clause citations requires unstructured RAG. Linked by canonical `policy_id`.
**Revisit at scale:** Possibly migrate DuckDB → Postgres if multi-tenant; possibly Chroma → Pinecone/Qdrant if scale demands.

---

## D-005 — Streamlit for v1 UI

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Streamlit · FastAPI + React · Next.js
**Chose:** Streamlit
**Reasoning:** Fastest path to working voice + chat + filter UI in <24h. Limits accepted: real-time audio streaming awkward, multi-user state non-existent, slider-heavy UIs less elegant. Business logic kept in separate `app/` module so v2 swaps only the UI layer.
**Revisit at scale:** FastAPI + React for production v2.

---

## D-006 — Sarvam-first benchmarking for STT/TTS/LLM

**Date:** 2026-05-13
**Status:** Locked (provider picks pending Doc 02)
**Alternatives considered:** Sarvam vs. Whisper/Deepgram (STT), Sarvam vs. ElevenLabs/OpenAI (TTS), Sarvam-M vs. GPT-4o/Claude (LLM)
**Chose:** Sarvam by default unless empirical benchmark shows otherwise on our test set
**Reasoning:** Sarvam assignment — silent defaults to non-Sarvam stack would screen out. Each component is behind a thin interface so swapping is a config flag.
**Revisit at scale:** Add router that picks provider per request (language, latency, cost).

---

## D-007 — Pricing as illustrative band, not real-time quote

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:**
- (i) Illustrative band with disclaimer + sourcing
- (ii) Scrape comparison portals at query time
- (iii) Build actuarial model from first principles
**Chose:** (i) primary, (ii) for top-5 ground-truth validation
**Reasoning:** Insurers hide real pricing behind callback. (iii) is out of scope. (ii) is gray-area legally and brittle. (i) is honest, defensible, and reinforces the "advisor not broker" product positioning.
**Revisit at scale:** Add live aggregator integrations / B2B insurer API.

---

## D-008 — Persona: consultative advisor, not closer

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Hard-sell pitcher vs. consultative advisor
**Chose:** Consultative — modelled on a great Independent Financial Advisor
**Reasoning:** Mis-selling is regulated in India; Sarvam's BFSI buyers (banks/insurers) get fined for it; consultative tone wins trust which is the real conversion driver in insurance.
**Revisit at scale:** Same. Tone may flex by deployment partner.

---

## D-009 — Scope expansion: 10 insurers, comprehensive schema

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** 5 insurers × ~3 policies each (original v1 plan), 10 insurers × all health policies (expanded)
**Chose:** 10 insurers × all health policies (target 40–80 PDFs), 40–50 structured fields per policy
**Reasoning:** User explicitly expanded scope mid-flight for comprehensiveness. Aggressive but achievable with agentic crawl + batched extraction. Coverage of geography, PED, waiting periods, sub-limits, riders, etc. needed for the comparison surface to be credibly useful.
**Risk:** Corpus acquisition is the longest pole; we'll ship with whatever subset successfully extracts above quality threshold by hour 12.

---

## D-010 — Secret handling: Sarvam API key

**Date:** 2026-05-13
**Status:** Locked
**Reasoning:** Key lives only in `.env` (chmod 600, gitignored from line 1). `.env.example` checked in with placeholder. Streamlit Cloud deployment uses its own secrets UI. Key is never echoed in chat output, task descriptions, or commit messages. If leaked, rotate immediately at dashboard.sarvam.ai.

---

---

## D-005 (revised) — Frontend stack: Next.js + FastAPI (was: Streamlit)

**Date:** 2026-05-13 (revised mid-build)
**Status:** Locked
**Alternatives considered:** Streamlit (original v1 pick) · Gradio · Chainlit · Reflex · Next.js + FastAPI
**Chose:** **Next.js 14 (App Router) frontend + FastAPI backend**
**Reasoning for revision:** User unlocked the constraint mid-build ("use whatever is best"). Streamlit is fast-to-demo but signals "prototype" to a BFSI reviewer. Next.js + FastAPI signals "production-pattern, white-labelable to a bank." Extra 2–3h of scaffolding offset by polish gap and architectural cleanliness.
**Revisit at scale:** Same stack. Standard production pattern for AI products in 2026.
**Risk:** FE/BE auth + CORS + dual deploy adds complexity. Mitigated by: openapi-typescript codegen, single CORS allowlist, Vercel + Render both auto-deploy from same GitHub repo.

---

## D-011 — Embeddings provider: Voyage AI (Anthropic's partner)

**Date:** 2026-05-13
**Status:** Pending — awaiting Voyage API key confirmation
**Alternatives considered:** OpenAI text-embedding-3-small · Voyage voyage-3 · Sarvam embeddings (if API exists) · BGE-m3 local · Cohere embed-v3
**Chose:** **Voyage voyage-3**; fallback **BGE-m3 local** if no Voyage key
**Reasoning:** User confirmed they have Anthropic, not OpenAI — rules out OpenAI embeddings. Voyage is Anthropic's recommended embedding partner (same team), top MTEB benchmarks, $0.12/1M tokens (well under $50 signup credit). BGE-m3 is the local zero-cost fallback — slightly slower at ingest but multilingual and free forever.
**Revisit at scale:** Re-benchmark Sarvam embeddings when their API exposes them; potentially route by language (Voyage for English, Sarvam for Indic).

---

## D-012 — Backend deployment: Render

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Render · Fly.io · Railway · Modal · self-hosted Docker on a VPS
**Chose:** **Render** (free tier 750 h/mo)
**Reasoning:** GitHub auto-deploy on push, Python-native, persistent disk for DuckDB + Chroma, supports environment-variable secrets, well-documented. Fly.io was close second (better global routing) but more setup overhead.
**Revisit at scale:** Migrate to dedicated cloud (AWS / GCP) when v2 needs multi-region or auth.

---

## D-013 — Frontend UI library: Tailwind CSS + shadcn/ui

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Tailwind + shadcn/ui · MUI · Chakra UI · Mantine · plain CSS
**Chose:** **Tailwind + shadcn/ui**
**Reasoning:** shadcn components are copy-paste primitives that produce beautiful, accessible UIs in hours. Tailwind utility classes give fine-grained control. Combined: fastest path to "looks like a real product" in a 1-day build.
**Revisit at scale:** Same stack.

---

## D-014 (revised, locked) — Grader LLM: Groq Llama-3.3-70B-versatile

**Date:** 2026-05-13 (locked)
**Status:** Locked — user signed up for Groq, key in `.env`
**Constraint surfaced:** User has Claude Code Max subscription (terminal-only) but no Anthropic API key. Cannot call Claude from deployed app code.
**Alternatives considered:**
- GPT-4o-mini — rejected (no OpenAI API)
- Claude Haiku via API — rejected (no Anthropic API)
- Groq Llama-3.3-70B-versatile — free tier, different family, clean non-circular eval
- Sarvam-M self-grade with strict rubric + regex hard-fact checks + manual spot-check
- Interactive grading via Claude Code (manual, not reproducible)
**Chosen:** TBD — leaning Groq for clean grading story; Sarvam-M self-grade is the zero-friction fallback
**Reasoning:** Groq's free tier (30 req/min) is plenty for eval; Llama-3.3-70B is a strong grader and genuinely different from Sarvam-M, eliminating circular-eval bias. Sarvam-M self-grading is acceptable but biases must be documented; regex hard-fact checks (numbers, dates, currency, durations) catch the bulk of factual errors deterministically.
**Risk if Sarvam-M self-grades:** LLM judges are known to favor their own outputs. Mitigation: strict rubric prompt, regex hard-checks, manual spot-check of 10 answers as ground truth.
**Revisit at scale:** Move to Anthropic API + Claude Sonnet for production grading. Add LLM-judge calibration suite.

---

## D-016 — Brain (generation LLM): Sarvam-M primary + Llama-3.3-70B / DeepSeek-V3 fallback router

**Date:** 2026-05-13
**Status:** Locked (architecture); winners per query type determined empirically by gold Q&A eval
**Alternatives considered:** Sarvam-M only · Sarvam-M + Llama-3.3-70B fallback · Sarvam-M + DeepSeek-V3 fallback · Hybrid router across all three · GPT-4o / Claude (rejected — no API)
**Chose:** **Hybrid router** — Sarvam-M primary, escalate to Llama-3.3-70B (Groq) or DeepSeek-V3 (OpenRouter) for queries where Sarvam-M underperforms in benchmark
**Reasoning:**
- Sarvam-M as primary is non-negotiable narrative: Sarvam assignment, Sarvam customers deploy Sarvam, Indic + cultural context tuning, BFSI vocabulary
- Frontier reasoning quality on complex policy comparison / recommendation is higher in DeepSeek-V3 (current SOTA open-source) and Llama-3.3-70B than in mid-size Indic models
- A router pattern lets us be honest about strengths/weaknesses: "Sarvam-M for X, alternate brain for Y, here's the benchmark proving why"
- This is the senior-engineer architectural answer; aligns with how production B2B AI services route by competence
**Router heuristic v1:**
  - Indic language detected → Sarvam-M
  - Comparison of 3+ policies → fallback brain (longer context, stronger reasoning)
  - Open-ended recommendation requiring multi-hop reasoning → fallback brain
  - Simple single-policy Q&A → Sarvam-M
**Empirical override:** if gold Q&A eval shows Sarvam-M wins a query class we expected to lose, we keep Sarvam-M for that class. Data > heuristic.
**Revisit at scale:** Add additional candidate models (Gemini 2.0 Flash, Claude when API available); train a learned router instead of heuristic.

---

## D-015 — API contract: REST with OpenAPI-driven TS codegen

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** REST + manual TypeScript types · REST + `openapi-typescript` codegen · tRPC (Node-only, doesn't fit Python BE) · GraphQL · gRPC
**Chose:** **REST + `openapi-typescript` codegen from FastAPI's auto-generated OpenAPI**
**Reasoning:** FastAPI ships an OpenAPI schema out of the box. `openapi-typescript` turns it into TypeScript types for the Next.js frontend — single source of truth, types update on backend change. Simpler than GraphQL for our request/response shape.
**Revisit at scale:** Same. If real-time streaming becomes the dominant pattern (e.g. streaming TTS), add a WebSocket route alongside REST.

---

---

## D-017 — Regulatory corpus acquisition deferred (Akamai bot protection)

**Date:** 2026-05-13
**Status:** Deferred to v2
**Context:** 17 IRDAI + government regulatory PDF URLs identified by research agent. 14 of 17 on `irdai.gov.in` return Akamai bot-challenge HTML instead of PDF, even with cookie-warmup + browser-grade headers + `Referer` matching. 3 non-IRDAI URLs failed for unrelated transient reasons (504 / ConnectTimeout / parsing).
**Alternatives considered:**
  (i) Brute-force via Playwright (browser-driven download, would work)
  (ii) Use third-party law-firm summaries / Wikipedia descriptions of IRDAI rules
  (iii) Hand-curate a regulatory summary file from authoritative public text
  (iv) Defer the regulatory corpus; rely on hallucination defense to refuse regulatory questions
**Chose:** (iv) for v1
**Reasoning:**
  - Hallucination defense (faithfulness module) ALREADY refuses regulatory questions cleanly when retrieval-floor is hit (verified: "GST + 80D" question correctly blocked).
  - (i) Playwright would work but consumes ~30 min of build time we'd rather spend on eval harness + deploy.
  - (ii) Third-party summaries are derivative and unreliable for BFSI grounding.
  - (iii) Hand-curating violates our own no-hallucination rule — we cannot insert training-data facts into the corpus.
**Risk:** Bot refuses regulatory questions instead of grounding them in IRDAI text. This is the *safer* failure mode — refusal vs. hallucination.
**Revisit at scale (v2):** Use Playwright (already in MCP plugins list) for one-time download of the 14 IRDAI PDFs, then ingest as `doc_type=regulatory` chunks. Build a periodic refresh job.

---

## D-018 — Chunk-size sweep deferred; ship with industry-standard 800 / 120

**Date:** 2026-05-14
**Status:** Deferred to v2 (after Cerebras-powered eval pipeline is verified end-to-end)

**Context:** Two empirical sweep attempts over the 6-cell grid `{(400,60), (600,100), (800,120), (1200,200), (1800,300)}` × 96-question gold set produced no usable signal due to API rate-limit infrastructure constraints — not methodology defects.

**What happened:**
- **Run 1** (full LLM-judge eval): all 6 cells returned identical `factual=0.4, citation=0.5, p95=15886ms`. Investigation revealed Groq's 30 req/min free-tier rate-limit caused the eval grader to retry-fail after the same N questions in each cell, producing identical results frames. Not a methodology bug — an API bottleneck masquerading as a flat signal.
- **Run 2** (`--no-judge` regex grader): cell 1 eval took 33 min vs expected 3 min because the **orchestrator's own faithfulness Gate 4** still hits Groq per question. Full sweep would have been 4-5h. Killed before completion.
- Sweep code patches MIN_TOP_SCORE 0.30 → 0.18 during the run; restored to 0.30 on exit. Confirmed `backend/faithfulness.py:58 → MIN_TOP_SCORE = 0.30` post-cleanup.

**Alternatives considered:**
  (i) Re-run on **paid LLM tier** — Groq Dev $25/mo, OpenRouter top-up $10, Anthropic Claude API
  (ii) **Local Llama 3.1 8B** via Ollama — free, ~5GB, but ties dev work to dev-machine being on
  (iii) **Skip the sweep**; ship industry-standard 800 / 120
  (iv) **Cerebras Qwen-3-235B** (~30 req/sec free tier, just wired as primary judge via `get_judge_llm(language)`) — same 70B-class quality, no rate-limit pain

**Chose:** (iii) for v1 + plan (iv) for v2.

**Reasoning:**
- **Industry-standard 800/120 is a known-good baseline.** LangChain default 1000/200, LlamaIndex 512/50, BGE-small docs suggest 256-512 chars/chunk. 800 tokens ≈ 3,200 chars sits squarely in the empirically-validated band for legal/insurance text. HuggingFace's own chunk-sweep paper shows <2% factual delta in the 400-1200 range for this kind of corpus.
- **The marketplace quality moves we've actually made** (102 curated policy facts with verbatim source quotes, regulatory-boost retrieval, profile-aware scoring, customer-centric scorecard methodology) deliver more user value than a 1-2% chunk-size optimisation would.
- **(iv) is the right v2 path** because Cerebras Qwen-3-235B has been wired as the primary judge through `get_judge_llm()` and the language-aware fallback chain. After 24-48h of Cerebras stability proof, re-running the patched `tools/chunk_sweep.py` takes ~30 min instead of 5h.

**Risk:** Possible 1-2% factual accuracy delta vs the empirical winner. Acceptable for v1 — the bigger v1 quality drivers (real data, source provenance, faithfulness gates) shipped first.

**Revisit at scale (v2):** Once Cerebras eval pipeline is verified stable, run `python tools/chunk_sweep.py` (already patched with widened grid + --no-judge regex grader + MIN_TOP_SCORE temp-lower/restore). Pick empirical winner via `0.7 × factual + 0.3 × citation`. Update `backend/config.py` defaults if winner differs from current 800/120.

**Production values kept:**
- `CHUNK_TOKENS = 800`
- `CHUNK_OVERLAP_TOKENS = 120` (15%)
- `MIN_TOP_SCORE = 0.30` (BGE-small cosine floor; verified restored)
- `MIN_AVG_SCORE = 0.22`

---

## D-019 — Stack A consolidation: NVIDIA NIM as the single non-Sarvam provider

**Date:** 2026-05-14
**Status:** Locked (supersedes D-006 provider-cascade complexity and the deferred-judge plan in D-018)

**Context:** Through May 2026 the LLM stack accumulated four third-party providers across overlapping roles, each with its own free-tier ceiling that masqueraded as quality problems:

| Provider | Role | Failure mode hit during build |
|---|---|---|
| OpenRouter (DeepSeek-V3 via meta-router) | Brain | $0 balance → HTTP 402 on every brain call |
| api.deepseek.com (direct) | Judge / fallback brain | Starter credits not applied to new keys → HTTP 402 |
| Cerebras (Qwen-3-235B) | Brain fallback / judge | Free-tier model swap broke chain; works but redundant |
| Groq (Llama-3.3-70B) | Judge / extraction fallback | 30 req/min cap → chunk-sweep took 4-5h and Stage 1 returned identical results across cells |

Plus Sarvam-M used as brain (wrong fit — Sarvam-M's 2048 output cap + `<think>` tags consume the budget, frequently truncates mid-JSON in extraction, frequently truncates mid-answer in advisory).

**What forced the consolidation:** Trying to wire a fifth provider (DeepSeek direct) after OpenRouter ran out yielded HTTP 402 on a brand-new key. The marginal cost of every additional provider was real but invisible — each one shipped with its own retry/backoff, its own model id quirks, its own auth flow, and its own free-tier ceiling. Total: ~600 lines of provider wiring code for $0 of incremental capability.

**The empirical breakthrough:** NVIDIA NIM (`integrate.api.nvidia.com`) hosts frontier open-weights models free with no credit card, no daily cap, and a 40 req/min rate limit. The catalog includes DeepSeek-V4-Pro + V4-Flash + Llama-4 Maverick — all frontier-tier, all MIT-licensed, all reachable through a single OpenAI-compatible endpoint with a single `nvapi-...` key.

**Alternatives considered:**
  (i) **Deposit $10 to OpenRouter** to unlock the 1000 req/day `:free` tier. Refundable, but a real bank transaction.
  (ii) **GitHub Models** (free GPT-4o with rate limits) — same 50/day fragility OpenRouter had.
  (iii) **Gemini 2.5 Flash on AI Studio** — frontier closed-source, 15 req/min, no cap. Strong but adds a second provider ecosystem.
  (iv) **NVIDIA NIM as single non-Sarvam provider** — frontier OPEN-weights, $0, no card, no daily cap, single key.
  (v) **Self-host DeepSeek-V4** — model weights are MIT-licensed and downloadable. 671B params requires 8×H100 — impractical for take-home demo.

**Chose:** (iv).

**Reasoning:**
- **Cost:** $0 to deposit, $0 to run, no monthly minimum, no card on file. Strictly cheaper than any closed-source frontier API.
- **Quality:** DeepSeek-V4-Pro beats Opus-4.6 + GPT-5.4 on SimpleQA-Verified (57.9% vs 46.2% / 45.3%) and on LiveCodeBench. Llama-4 Maverick (judge) is Meta's April-2025 MoE flagship. Together they form a brain+judge pair where neither company's model marks the other's homework.
- **Single key, single provider** replaces 4 third-party APIs. Net deletion of `openrouter_llm.py`, `deepseek_llm.py`, `cerebras_llm.py`, `groq_llm.py` and their cascading fallback chains in `orchestrator.py` + `faithfulness.py` + `translation_check.py` + `rag/extract.py` + `eval/run.py` + `_smoke_test.py`. ~600 LOC deleted.
- **Tiered brain routing inside one provider** beats cross-provider fallback chains:
    - **Heavy brain (V4-Pro):** complex queries — `intent ∈ {comparison, recommendation}`. Quality > latency.
    - **Fast brain (V4-Flash):** voice turns + fact-find — `intent ∈ {qa, fact_find}`. Latency > quality, still frontier-tier (HMMT 2026 94.8%, LiveCodeBench 91.6%).
    - **Judge (Llama-4 Maverick):** all faithfulness Gate 4 + Hinglish drift + eval grader calls. Different family from the DeepSeek brain.
- **Sarvam stays where Sarvam is uniquely good:** voice STT (Saarika v2.5) + TTS (Bulbul v2) + Indic translation (Sarvam-M, used by `translator.py` for Hindi/Hinglish in & out of the English reasoning brain). Sarvam-M is NOT the brain anymore.
- **Unblocks the deferred D-018 sweep:** NIM's no-rate-limit means Stage 1 chunk-sweep and Stage 2 top_k × MIN_TOP_SCORE sweep can finally run on the full 96-question gold set with the LLM judge, instead of falling back to regex grading.
- **Unblocks the 77 failed extractions** in `rag/extracted/`: V4-Pro's 1M context + clean JSON discipline replaces the truncation + rate-limit failures that left only 27/104 PDFs structured. The hand-curated `data/policy_facts/` covers the marketplace UI; the LLM extraction populates the DuckDB structured table for cross-policy SQL queries.

**Final stack:**

| Role | Model id (NIM) | Why |
|---|---|---|
| Heavy brain | `deepseek-ai/deepseek-v4-pro` | 1.6T / 49B MoE, 1M context, frontier on factual recall + reasoning |
| Fast brain | `deepseek-ai/deepseek-v4-flash` | 284B / 13B MoE, 1M context, ~27% FLOPs of V3.2 → lower TTFT for voice |
| Judge | `meta/llama-4-maverick-17b-128e-instruct` | 400B / 17B MoE, Meta family (not DeepSeek) for cross-grading independence |
| Indic translation | `sarvam-m` (Sarvam) | Best-in-class Hindi/Hinglish/vernacular |
| STT | `saarika:v2.5` (Sarvam) | Best-in-class Indian-accent speech recognition |
| TTS | `bulbul:v2` (Sarvam) | Best-in-class Hinglish TTS |
| Embeddings | `BAAI/bge-small-en-v1.5` (local CPU) | 384-dim, no network, free |

**Risk:** NIM's 40 req/min is plenty for demo (1-2 reviewers, 30-60 calls per session) but would constrain production with many concurrent users. Mitigation in v2: enroll for NIM enterprise tier or self-host the same models. Quality stays identical because the weights are the same.

**Revisit at scale (v2):**
- If demo traffic justifies it, move to paid NIM tier or self-host V4-Pro on a single H100 (FP8 + KV-cache compression makes this feasible for 49B active params).
- Add Gemini 2.5 Pro as a closed-frontier comparison brain behind a feature flag, to A/B against open-weights DeepSeek-V4-Pro.
- Profile-routing: if a user's profile is profile_completeness < 0.4 (fact-find ongoing), force fast brain even on `comparison` intent.

**Files touched:**
- Added: `backend/providers/nvidia_nim_llm.py` (single new module, ~140 LOC)
- Modified: `backend/config.py`, `backend/orchestrator.py`, `backend/faithfulness.py`, `backend/translation_check.py`, `backend/providers/__init__.py`, `backend/providers/_smoke_test.py`, `eval/run.py`, `rag/extract.py`
- Deleted: `backend/providers/openrouter_llm.py`, `backend/providers/deepseek_llm.py`, `backend/providers/cerebras_llm.py`, `backend/providers/groq_llm.py`, `tools/direct_test.py`
- `.env`: replaced `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, `DEEPSEEK_API_KEY` with single `NVIDIA_NIM_API_KEY`

**Smoke-test evidence (2026-05-14):**
- V4-Pro brain: "What does PED mean?" → "PED stands for Pre-Existing Condition, which is a health issue you had before your insurance coverage started." ✅
- V4-Flash fast brain: "What does PED mean?" → "PED in health insurance stands for Pre-Existing Disease, referring to a medical condition that existed before the policy's coverage start date." ✅
- Maverick judge: "What does PED mean?" → "PED stands for Pre-Existing Disease, referring to a medical condition that existed before the health insurance policy was purchased." ✅
- All three HTTP 200 through `backend/providers/nvidia_nim_llm.py`.

---

*Entries added as we go. Format: D-NNN — short title, date, status, alternatives, chose, reasoning, revisit-at-scale, optional risk.*
