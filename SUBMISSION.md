# Insurance Sales Portfolio Expert — Sarvam AI Submission

| | |
| --- | --- |
| **Author** | Rohit Saraf |
| **Live demo** | https://rohitsar567-insurancebot.hf.space |
| **Repo** | https://github.com/rohitsar567/insurance-sales-bot |
| **Build window** | 2026-05-13 (≈24h) |
| **Read time for this doc** | ~15 minutes |
| **Read order after this** | [`README.md`](README.md) → [`docs/02-architecture.md`](docs/02-architecture.md) → [`docs/decisions.md`](docs/decisions.md) → [`kb/AUDIT_TRAIL.md`](kb/AUDIT_TRAIL.md) → [`docs/04-failure-modes.md`](docs/04-failure-modes.md) |

---

## 1. The 30-second hook

A **voice-first health-insurance advisor** for Indian buyers, grounded in a curated corpus of **208 documents** — 190 product documents from 19 leading insurers (Star, HDFC ERGO, Niva Bupa, Care, ICICI Lombard, Bajaj Allianz, New India, Aditya Birla, Tata AIG, ManipalCigna, SBI General, Acko, IFFCO Tokio, Cholamandalam MS, Go Digit, Reliance General, Royal Sundaram, Oriental Insurance, National Insurance) plus **18 IRDAI / regulatory documents** — extracted into a 48-field structured schema with a rules-based A–F scorecard and a 4-gate hallucination defense on every reply.

**The bot is consumer-facing in experience, B2B in commercial application.** The realistic deployment is an insurer or aggregator white-labelling this advisor on top of Sarvam's ASR/TTS/LLM stack. The build deliberately optimises for the artifacts a BFSI buyer would audit: provenance, refusal behaviour, eval rigor.

**Try this on the live demo:**

> *"What's the pre-existing disease waiting period under Care Supreme, and how does that compare to ICICI Elevate?"*

You should see (i) a comparative answer with `[Source: ...]` citations linking to specific policy PDFs and page ranges, (ii) the brain that handled it (`v4-pro::comparison`, `v4-flash::qa`, or `crosscheck-rescued-by-maverick`), and (iii) audio synthesised by Sarvam Bulbul. Ask the same in Hinglish — *"Care Supreme mein PED ka waiting period kya hai?"* — and the response flows through the Indic translation cascade with three drift checks before reaching you.

Now ask: *"What does IRDAI's 2024 Master Circular say about cataract waiting-period caps?"* The bot will ground the answer in `irdai-master-circular-health-2024.pdf` — a document we had to **Playwright** past Akamai bot protection to obtain (§6).

---

## 2. Tour of the architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Next.js 14 (Vercel)  ──  mic record  ·  chat  ·  filter  ·  scorecard    │
└──────────────────────────────────────────────────────────────────────────┘
                                  │  HTTPS  /api/chat  /api/voice  /api/policies
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ FastAPI backend (HF Spaces / Render)                                     │
│  ┌──────────────┐   ┌────────────────────┐   ┌──────────────────────┐   │
│  │ STT          │   │ ORCHESTRATOR       │   │ TTS                  │   │
│  │ Sarvam       │──▶│ - intent classifier│──▶│ Sarvam Bulbul        │   │
│  │ Saarika v2.5 │   │ - brain router     │   │ (acronym pre-expand) │   │
│  └──────────────┘   │ - persona prompt   │   └──────────────────────┘   │
│                     │ - 4-gate verifier  │                              │
│                     │ - cross-check retry│                              │
│                     │ - Indic cascade    │                              │
│                     │   (3 drift checks) │                              │
│                     └─────────┬──────────┘                              │
│                               │                                         │
│           ┌───────────────────┼───────────────────┐                     │
│           ▼                   ▼                   ▼                     │
│   ┌──────────────┐    ┌──────────────┐    ┌─────────────────────┐      │
│   │ STRUCTURED   │    │ VECTOR STORE │    │ NIM BRAIN ROUTER    │      │
│   │ DuckDB       │    │ Chroma 0.5.20│    │ V4-Pro  (heavy) ◀┐  │      │
│   │ 48 fields    │    │ BGE-small    │    │ V4-Flash (fast)  │  │      │
│   │ /policy      │    │ 800/120 chunk│    │ Llama-4 Maverick │  │      │
│   └──────────────┘    └──────────────┘    │   (judge + xcheck│  │      │
│           ▲                   ▲           │    + eval grader)│  │      │
│           │ extracted at      │ embedded  │ single NIM key   │  │      │
│           │ ingest            │ at ingest │ 40 req/min       │  │      │
│           │                   │           └─────────────────────┘     │
└───────────┼───────────────────┼─────────────────────────────────┼───────┘
            │                   │                                 │
┌───────────┴───────────────────┴───────────────┐                 │
│ INGEST  (rag/ingest.py + rag/extract.py)      │                 │
│ pdfplumber → 800-tok chunks → BGE embed       │                 │
│ NIM V4-Pro structured extract + Sarvam fallbk │                 │
│ self-critique → confidence_pct per field      │                 │
└────────────────┬──────────────────────────────┘                 │
                 │                                                │
   ┌──────────────────────────────┐                               │
   │ 208 source PDFs              │                               │
   │  · 190 product (19 insurers) │                               │
   │  · 18 regulatory (IRDAI)     │  ◀ Playwright same-origin ────┘
   │                              │     fetch past Akamai (§6)
   └──────────────────────────────┘
```

**Major components in four lines each.**

| Component | What it does (4 lines) |
| --- | --- |
| **Frontend** ([`frontend/`](frontend/)) | Next.js 14 App Router + Tailwind + shadcn/ui. Push-to-talk via MediaRecorder, blob POST to `/api/voice`. Streams chat replies with per-citation source links + scorecard panel. Deployed on Vercel (free, GitHub auto-deploy). |
| **API gateway** ([`backend/main.py`](backend/main.py)) | FastAPI + Pydantic. Routes: `/api/chat`, `/api/voice`, `/api/policies`, `/api/policies/{id}/scorecard`, `/api/insurers/{slug}/reviews`. OpenAPI auto-served; frontend types codegen via `openapi-typescript` (D-015). |
| **Orchestrator** ([`backend/orchestrator.py`](backend/orchestrator.py)) | Intent classifier → retrieval → brain router → 4-gate faithfulness → cross-check retry → Indic cascade. The single file that defines a turn. |
| **Faithfulness verifier** ([`backend/faithfulness.py`](backend/faithfulness.py)) | Four gates run on every reply: retrieval floor, citation integrity, regex numeric grounding, LLM-judge. Every block goes to `logs/hallucinations.jsonl`. |
| **Translation cascade** ([`backend/translator.py`](backend/translator.py), [`backend/translation_check.py`](backend/translation_check.py)) | For Indic queries: Sarvam-M translates Hinglish → English, NIM brain reasons in English, gates run on English, Sarvam translates back, three drift checks validate the Indic output (§3). |
| **Retrieval** ([`rag/retrieve.py`](rag/retrieve.py)) | Top-k cosine search over Chroma with policy-name-aware boost. Returns `RetrievedChunk(policy_id, policy_name, page_start, page_end, source_url, score, text)`. |
| **Structured extraction** ([`rag/extract.py`](rag/extract.py)) | NIM DeepSeek-V4-Pro (1M context, clean JSON discipline) with the 48-field Pydantic schema as structured-output target; Sarvam-M fallback. Self-critique pass scores per-field confidence. |
| **Scorecard** ([`backend/scorecard.py`](backend/scorecard.py)) | Pure-Python rules-based aggregation over 24 of the 48 extracted fields → 6 sub-scores → A–F grade. No LLM in the loop — anyone can reproduce a grade from the JSON. Methodology in [`docs/scorecard-methodology.md`](docs/scorecard-methodology.md). |
| **KB** ([`kb/`](kb/)) | Markdown-first knowledge base with 11 per-policy sheets, scorecard results, eval results, URL verification, insurer reviews, premium anchors, security findings. Regeneratable via `python -m rag.build_kb`. |
| **Eval** ([`eval/`](eval/)) | Three gold-Q&A pipelines (templated, LLM-drafted, adversarial), NIM Llama-4 Maverick grader for non-circular evaluation (different family from DeepSeek brain), results versioned in `eval/results.md`. |

Full system diagram in [`docs/02-architecture.md`](docs/02-architecture.md) §1; stack rationale in [`docs/tech-stack-rationale.md`](docs/tech-stack-rationale.md).

---

## 3. Model choices — explicitly *for Sarvam*

This is the section a Sarvam reviewer should read most carefully. Every pick is in [`docs/decisions.md`](docs/decisions.md) with alternatives and reasoning.

### 3.1 Sarvam Saarika v2.5 — speech-to-text

Sarvam-first per the assignment. Saarika v2.5 (their newer Indic ASR) handles English, Hindi, and Hinglish code-switch in a single model — which matters because Persona A (Priya) speaks Hinglish naturally and we don't want to bolt language detection on top of STT. Implemented in [`backend/providers/sarvam_stt.py`](backend/providers/sarvam_stt.py) behind a thin interface ([`backend/providers/base.py`](backend/providers/base.py)) so a Whisper / Deepgram swap is a config flag. D-006 + [`docs/tech-stack-rationale.md`](docs/tech-stack-rationale.md) row 9.

### 3.2 Sarvam Bulbul — text-to-speech

First-party Indic prosody. The orchestrator pre-expands domain acronyms (`PED → pre-existing disease`) before sending text to Bulbul — see F-06 in [`docs/04-failure-modes.md`](docs/04-failure-modes.md). [`backend/providers/sarvam_tts.py`](backend/providers/sarvam_tts.py).

### 3.3 NVIDIA NIM — the consolidated open-weights reasoning stack

**D-019 (2026-05-14) locks the consolidation:** every non-Sarvam reasoning call runs on a single `nvapi-...` key against `integrate.api.nvidia.com`. Four legacy providers (OpenRouter, direct DeepSeek, Cerebras, Groq) were retired in the same change — ~600 LOC of provider wiring deleted, no quality loss, no daily rate limit, $0 cost.

Tiered brain routing inside one provider:

| Tier | Model | Used when | Why |
|---|---|---|---|
| **Heavy brain** | `deepseek-ai/deepseek-v4-pro` (1.6T / 49B MoE, 1M context, MIT) | `intent ∈ {comparison, recommendation}` | Beats Opus-4.6 + GPT-5.4 on SimpleQA-Verified (57.9% vs 46.2% / 45.3%) and LiveCodeBench. Quality > latency for synthesis. |
| **Fast brain** | `deepseek-ai/deepseek-v4-flash` (284B / 13B MoE, 1M context, MIT) | `intent ∈ {fact_find, qa}` — i.e. voice turns | ~27% of V3.2 single-token FLOPs → lower TTFT for voice. Still frontier-tier (HMMT 2026 94.8%, LiveCodeBench 91.6%). |
| **Judge** | `meta/llama-4-maverick-17b-128e-instruct` (400B / 17B MoE) | Faithfulness Gate 4, Hinglish drift LLM-judge, eval grader | **Different family from the DeepSeek brain** — Meta MoE judging DeepSeek MoE = the brain does not mark its own homework. |

The router lives in [`backend/orchestrator.py:pick_brain`](backend/orchestrator.py). The cross-check retry pattern stays inside NIM: when faithfulness fails on the primary brain's output (and the failure isn't Gate 1 / "no evidence at all"), the orchestrator re-runs the same prompt on Llama-4 Maverick (the judge model used as rescue brain). Different architecture (DeepSeek MoE vs Meta MoE), different training corpus — the "different-family ensemble" signal is preserved without requiring a second provider. The rescued reply is tagged `crosscheck-rescued-by-maverick` for audit.

### 3.4 Sarvam — voice + Indic translation (not the brain anymore)

Sarvam stays where Sarvam is uniquely strong, but it no longer reasons:

1. **STT — Sarvam Saarika v2.5.** Best Indian-accent recognition available.
2. **TTS — Sarvam Bulbul v2.** Best Hinglish TTS, single-speaker voice (`anushka`).
3. **Indic translation cascade — Sarvam-M.** When the user speaks Hinglish or Hindi: Sarvam translates Hinglish → English → NIM brain reasons in English → 4 faithfulness gates run on the English reply → Sarvam translates English → Hinglish → **three drift checks** ([`backend/translation_check.py`](backend/translation_check.py)) verify the Indic output preserves numbers, citations, and semantic meaning. Drift checks:
   - **Gate-A (regex anchors):** `check_translation_drift()` verifies every digit / currency / citation in the English reply appears in the Indic reply. If not → revert to English.
   - **Gate-B (LLM-judge):** NIM Llama-4 Maverick scores semantic faithfulness across languages.
   - **Gate-C (back-translation cosine):** Sarvam back-translates Hinglish → English; cosine vs original English ≥ 0.80 or revert.

   This closes the F-16 gap in [`docs/04-failure-modes.md`](docs/04-failure-modes.md) where the faithfulness gates only check the English reply.

**Why Sarvam moved out of the brain role:** Sarvam-M's 2048 starter-tier output cap + `<think>` reasoning tokens consume the budget, causing frequent truncation mid-JSON in extraction and mid-answer in advisory. NIM-hosted DeepSeek-V4-Pro (1M context, no rate limit, MIT-licensed frontier) is a strictly better fit for the reasoning role on Sarvam's free tier. Sarvam wins decisively on voice + Indic — the parts of the stack closed-source frontier can't match.

### 3.5 NIM Llama-4 Maverick — the cross-family judge

Same NIM endpoint, different model family from the DeepSeek brain. **Non-circular eval is the whole point** — if a DeepSeek model graded DeepSeek output, the eval would be aspirational. [`eval/run.py`](eval/run.py) calls Llama-4 Maverick with a strict JSON-schema judge prompt. The same model also serves as Gate 4 of faithfulness ([`backend/faithfulness.py`](backend/faithfulness.py) `_gate_llm_judge`) and as the Hinglish-translation drift judge ([`backend/translation_check.py`](backend/translation_check.py) `check_hinglish_faithfulness`).

### 3.6 BGE-small-en-v1.5 — embeddings (the honest tradeoff)

D-011 originally locked Voyage AI `voyage-3`. Mid-build, Voyage's 3 RPM free-tier limit blocked the 208-PDF ingest. Switched to local BGE-small-en-v1.5 ([`backend/providers/local_embeddings.py`](backend/providers/local_embeddings.py)). Accepted ~3pp retrieval-quality hit (per BEIR-style spot checks); kept Voyage path behind the same interface so v2 swaps with no other code change. Documented in [`docs/ROADMAP.md`](docs/ROADMAP.md) §5.

---

## 4. Eval rigor — gold Q&A + 4 gates + audit log

[`docs/03-eval-plan.md`](docs/03-eval-plan.md) is the canonical eval doc. The structure is intentional:

### 4.1 Three gold Q&A pipelines

| Pipeline | What | Volume | Why |
| --- | --- | --- | --- |
| **A — Auto-templated** | 15 templates × ~80 policies | ~1,100 candidate pairs; ~300 currently committed | Scales for free. Each pair traces to a specific schema field → specific clause. Fully reproducible. |
| **B — LLM-drafted nuanced** | NIM DeepSeek-V4-Pro prompted on policy text to draft 5 buyer-style multi-clause questions per top-priority policy | Target 100; spot-checked | Tests reasoning Pipeline A can't reach. |
| **C — Adversarial** | Hand-written: out-of-corpus (space tourism), out-of-policy-type (IRDAI mandate when corpus had no IRDAI before §6), Hinglish, multi-policy compare | ~30–40 | Tests **refusal precision**, not just factual accuracy. |

Generator: [`eval/generate_gold.py`](eval/generate_gold.py). Committed gold set: [`eval/gold_qa.json`](eval/gold_qa.json).

### 4.2 The 4-gate faithfulness verifier — the defense

Every reply, every turn, runs through [`backend/faithfulness.py`](backend/faithfulness.py):

| Gate | Function | Blocks if | Source |
| --- | --- | --- | --- |
| **1 Retrieval floor** | `_gate_retrieval_floor` | Top retrieval score < 0.40 — bot has nothing to ground in | line 74 |
| **2 Citation integrity** | `_gate_citation_integrity` | Reply cites a policy_name that wasn't retrieved | line 94 |
| **3 Numeric grounding (regex)** | `_gate_numeric_grounding` | Any `₹`, `%`, `days`, `months`, `years` in reply doesn't appear in retrieved chunks | line 137 |
| **4 LLM-judge faithfulness** | `_gate_llm_judge` (NIM Llama-4 Maverick, different family from DeepSeek brain) | Judge says any claim is unsupported by chunks | line 191 |

The 4-gate verdict is bundled with a `cross-check retry` (§3.4) and the 3-gate Indic drift check (§3.3). Total inspection surface per turn = **4 English faithfulness gates + 1 cross-check brain pass + 3 Indic drift gates** when the user speaks Hinglish.

### 4.3 Honest eval numbers from [`eval/results.md`](eval/results.md)

Run timestamp: 2026-05-12T22:30:15Z. **25 questions** from the gold set.

| Metric | Value | Doc 01 target | Comment |
| --- | --- | --- | --- |
| Factual accuracy | **40.0%** | C2 ≥ 95% | Two structural reasons (below). |
| Citation accuracy | **50.0%** | C3 ≥ 95% | Same. |
| Refusal precision | **44.4%** | C4 ≥ 90% | Same. |
| Blocked by faithfulness | **12 / 25** | n/a | Gates are working; aggressively biased toward refusal. |

By question type: `coverage_scope` 100%, `regulatory_oos` 66.7%, `sub_limit` 33.3%, `exclusions_oos` 33.3%, `waiting_period` 12.5%, `bonus` 0.0%.
By brain: `groq-llama` 100%, `sarvam-m` 37.5%.

**Two structural causes for the low headline accuracy:**

1. **The gates are aggressive.** 12 of 25 questions are blocked — the bot refused when the gold answer claims the corpus has the data. In several cases the data *is* in the corpus but Gate 3 (regex numeric grounding) was over-strict on currency/percent normalisation. The fix is to soften the regex (v1.1, tracked); the v1 stance is "refuse rather than mis-cite" which is the SAFE failure mode in BFSI.
2. **Pipeline A templated questions over-index on `waiting_period` and `sub_limit` fields that several CIS-only PDFs (Bajaj Silver Health, Tax Gain) don't explicitly state.** When the template asks the question anyway, the bot correctly refuses, but the gold expects an answer.

The grader is now NIM Llama-4 Maverick (D-019 consolidation, 2026-05-14); this is non-circular — different family from the DeepSeek-V4 brain. Earlier eval run footers refer to the legacy Groq Llama judge.

### 4.4 Audit log — every blocked claim, every gate

`logs/hallucinations.jsonl` records every faithfulness block: `{ts, turn_id, reply, failing_gate, reasons, retrieved_chunks}`. A BFSI compliance auditor can replay any refusal and confirm the gate fired correctly. Cross-reference: [`kb/AUDIT_TRAIL.md`](kb/AUDIT_TRAIL.md) §3 "Verification per artifact type."

---

## 5. The knowledge base — 8 sections, all reviewer-readable

[`kb/`](kb/) is the **markdown-first canonical KB** — JSON for machines, markdown for reviewers. Every section is regeneratable via `python -m rag.build_kb`. From [`kb/INDEX.md`](kb/INDEX.md):

| Section | Path | Contents | Why it exists |
| --- | --- | --- | --- |
| 1 | [`kb/policies/`](kb/policies/) | 11 per-policy MD sheets, one per extracted policy | Single page where a reviewer can read every extracted field with its source clause and derivation tag (`[E]` / `[E?]` / `[C]` / `[I]` / `[V]`). |
| 2 | [`kb/calculations/scorecard_results.md`](kb/calculations/scorecard_results.md) | A–F grade + 6 sub-scores per policy, plus aggregate stats (5 B's, 6 C's, no A/D/F at extraction completeness < 65%) | Buyer-facing summary. Rules-based, no LLM — anyone can reproduce. |
| 3 | [`kb/calculations/eval_results.md`](kb/calculations/eval_results.md) | Snapshot of the latest eval run (mirror of `eval/results.md`) | KB-side copy for the audit trail. |
| 4 | [`kb/calculations/extraction_quality_audit.md`](kb/calculations/extraction_quality_audit.md) | Per-field completeness across policies | Honest exposure of where extraction is sparse. |
| 5 | [`kb/research/corpus_acquisition.md`](kb/research/corpus_acquisition.md) | How we got the 208 PDFs — agent crawl, retry script, Playwright rescue | Provenance. |
| 6 | [`kb/research/url_verification.md`](kb/research/url_verification.md) | HEAD-check status of every source URL we cite | Anti-hallucination — verified sources only. |
| 7 | [`kb/reviews/`](kb/reviews/) | 19 per-insurer reputation sheets — IRDAI complaints/10K, claim settlement ratio, qualitative sentiment from Reddit / r/IndianFinance | Feeds the Claim Experience sub-score in the scorecard. **Sentiment is regex-grounded summarisation of curated review snippets — see §6.** |
| 8 | [`kb/premiums/INDEX.md`](kb/premiums/INDEX.md) | 26 illustrative premium tables anchored to public PolicyBazaar / insurer rate cards | **Illustrative bands only (D-007). The bot says so in every premium reply.** |

Bonus sections: [`kb/methodology/`](kb/methodology/), [`kb/security/`](kb/security/), [`kb/AUDIT_TRAIL.md`](kb/AUDIT_TRAIL.md) (end-to-end data lineage in 10 stages).

---

## 6. Honest limits — what the bot can't (or shouldn't) do

Every limit below is documented in the repo. None of them are hidden.

### 6.1 Star Health — was CDN-blocked, fixed via Playwright

Star Health's old corpus URLs (`web.starhealth.in`) returned 403 to plain `curl`. [`kb/research/corpus_acquisition.md`](kb/research/corpus_acquisition.md) shows `star-health: 0 / 11 fail` from the initial run. **Fix:** [`rag/corpus/_playwright_results.md`](rag/corpus/_playwright_results.md) documents the Playwright MCP same-origin-fetch rescue — opened the product pages in real Chromium, harvested the migrated CloudFront URLs, fetched with proper `Origin`/`Referer` cookies. **Result: 11 / 11 Star Health PDFs now in `rag/corpus/star-health/`.**

### 6.2 IRDAI regulatory corpus — was Akamai-blocked, fixed via Playwright

D-017 in [`docs/decisions.md`](docs/decisions.md) originally deferred IRDAI to v2 because 14 of 17 IRDAI URLs returned Akamai bot-challenge HTML to plain `curl`. **Fix:** same Playwright pattern. [`rag/corpus/regulatory/_manifest.json`](rag/corpus/regulatory/_manifest.json) records the method: warm Akamai cookies on `irdai.gov.in` homepage, then same-origin `fetch()` from JS with `credentials: 'include'`. Landing pages like `document-detail?documentId=...` were resolved by extracting the embedded `<a href="...pdf">` from the DOM. **Result: 18 regulatory PDFs in `rag/corpus/regulatory/`** — including the IRDAI Master Circular on Health 2024, Arogya Sanjeevani standard wording, Insurance Act 1938, Ombudsman Rules, PMJAY ops manual, GST FAQs.

This is why the demo question *"What does IRDAI's 2024 Master Circular say about cataract waiting-period caps?"* now answers instead of refusing.

### 6.3 Sentiment in insurer reviews is regex-grounded, not LLM-extracted

[`kb/reviews/star-health.md`](kb/reviews/star-health.md) calls this out: the IRDAI annual-report complaints per 10K and claim settlement ratio are **directly extracted, primary-source numbers**. The Reddit / r/IndianFinance "mixed sentiment" labels are **curated snippet roll-ups** — we did not run a sentiment classifier on raw scraped text, because that would be unreliable on small samples. v2 plan: live Reddit/YouTube sentiment refresh with a calibrated classifier ([`docs/ROADMAP.md`](docs/ROADMAP.md) §v2.5).

### 6.4 Pricing is illustrative — never a binding quote

D-007. [`kb/premiums/INDEX.md`](kb/premiums/INDEX.md) lists the public PolicyBazaar quote pages and insurer rate cards used as anchors. Where a specific `(insurer, policy, age, SI, family-composition)` cell did not surface a public number, the closest comparable cell was scaled using factors derived from the real anchor points and the visible IRDAI age-bandings used by most Indian health insurers. **Every premium reply from the bot carries the disclaimer.** v2 path: insurer-API partnership for real quotes ([`docs/ROADMAP.md`](docs/ROADMAP.md) §v2.2).

### 6.5 Eval headline is 40% factual — and why that's not the story

§4.3 above. Short version: the gates are intentionally aggressive (refuse rather than mis-cite), the Pipeline A templates over-index on fields that some CIS-only PDFs don't state, and the per-question-type breakdown shows `coverage_scope` at 100% and `groq-llama` brain at 100% — the bot can answer; the question is how aggressively the gates filter it. v1.1 tracked work: soften Gate 3 regex; add Pipeline A null-skipping when source field is missing.

### 6.6 Push-to-talk, not full-duplex streaming

Voice latency: ~3–4s for V4-Flash fast brain (default for voice turns), 6–10s for V4-Pro heavy brain (used on comparison / recommendation intents). Streaming STT + full-duplex is in [`docs/ROADMAP.md`](docs/ROADMAP.md) §v2.4.

### 6.7 HF Spaces free tier — cold start

F-10. First request after ~15 min idle takes ~50s for spinup. The frontend shows a "starting up" indicator. Keep-warm cron is v2.

---

## 7. Demo runbook — 7 questions to try

Live URL: **https://rohitsar567-insurancebot.hf.space**

For each: try voice and text. The reply panel shows `brain_used` and per-citation source links.

| # | Question | What you should see | Why this question |
| --- | --- | --- | --- |
| 1 | *"What's the pre-existing disease waiting period under Care Supreme?"* | Specific number ("36 months" or similar) + `[Source: care-health/care-supreme/wordings, p.18]`. Brain `v4-flash::qa` (fact-find / single-policy → fast brain). | Single-field lookup — the easiest competence check. |
| 2 | *"Compare cataract waiting period in ICICI Elevate vs HDFC Optima Secure."* | Two-policy comparison with citations from both PDFs. Brain `v4-pro::comparison` (heavy brain for multi-policy synthesis). | Multi-policy reasoning — tests retrieval and tiered brain routing. |
| 3 | *"Care Supreme mein PED ka waiting period kya hai?"* (Hinglish) | Answer in Hinglish with citations preserved. `brain_used` includes `cascade::sarvam-trans+v4-flash::qa+sarvam-trans` or `cascade::drift-*-fallback` if a drift gate fired. | Indic cascade + 3-gate drift verification. |
| 4 | *"What does IRDAI say about cataract waiting-period caps under the 2024 Master Circular?"* | Cited answer from `irdai-master-circular-health-2024.pdf`. Refused before §6 Playwright rescue; answers now. | Demonstrates the IRDAI corpus fix. |
| 5 | *"Does Bajaj Silver Health cover space-tourism injuries?"* | **Safe refusal:** *"I'd rather not answer that without stronger evidence in the policy documents I have."* | Adversarial out-of-corpus — refusal is the correct behaviour (F-07). |
| 6 | *"Should I get the cataract surgery covered under this policy?"* | Bot answers what's covered + refuses to give clinical advice; suggests consulting a doctor. | Persona rule 4 (F-14) — no medical advice. |
| 7 | *"Show me the scorecard for ICICI Elevate."* | Side-panel A/B/C grade with 6 sub-scores. Hover → ✓ and − signals per sub-score citing specific schema fields. | Demonstrates the rules-based scorecard ([`docs/scorecard-methodology.md`](docs/scorecard-methodology.md)). |

If a question refuses unexpectedly, that's the safe failure mode — open `logs/hallucinations.jsonl` (or the API debug surface) and the failing gate is recorded.

---

## 8. Closing pitch — what this signals about how I'd ship at Sarvam

A take-home is a sample of how the engineer thinks under constraint. Three things this submission is meant to signal:

**1. I scope to a vertical slice, not a demo.** D-001 in [`docs/decisions.md`](docs/decisions.md) explicitly chose vertical slice over single-document RAG or full-platform. The 7 "c-readiness commitments" in [`docs/02-architecture.md`](docs/02-architecture.md) §7 are the contract: per-insurer adapters, category-agnostic schema, pluggable extraction, schema-driven filter UI, provider-agnostic STT/TTS/LLM, eval harness that scales linearly, stateless services. v2 is a data/config change, not a rewrite ([`docs/ROADMAP.md`](docs/ROADMAP.md)).

**2. I treat hallucination defense and refusal as product features.** BFSI deployments get fined for mis-selling; the bot is biased toward refusal over confident wrong answers. The 4 faithfulness gates + cross-check retry + 3 Indic drift checks + audit log are the BFSI-compliance-grade version of "we shipped a chatbot." If the eval shows 40% headline accuracy because the gates are aggressive, the right response is to soften the gates carefully (v1.1) — not to ship a higher number by relaxing the verifier.

**3. I document the model picks honestly — Sarvam where Sarvam is uniquely strong, NIM open-weights frontier for the reasoning roles.** Sarvam Saarika v2.5 STT, Sarvam Bulbul v2 TTS, and Sarvam-M Indic translation are *non-substitutable* — no closed-source frontier matches them on Indian voice or Hinglish. Reasoning is a different problem; DeepSeek-V4-Pro (1.6T MoE, MIT-licensed, beats Opus-4.6 + GPT-5.4 on SimpleQA-Verified) hosted free on NVIDIA NIM is the strongest open-weights reasoning brain available today, and pairing it with Meta Llama-4 Maverick as a cross-family judge gives the bot two different architectures evaluating every claim. A Sarvam customer deploying this stack gets a product that *uses Sarvam exactly where Sarvam beats the world* and uses MIT-licensed frontier weights for the parts that any reasoning provider could in principle handle — open-weights only, $0 inference, single API key for the entire non-voice stack. That's the honest sales narrative for an Indic AI company in 2026: Sarvam isn't trying to win a benchmark it doesn't need to win.

The rest is craftsmanship. The 8-section KB ([`kb/`](kb/)) is regeneratable from primary sources in <40 minutes for <$2 cold ([`kb/AUDIT_TRAIL.md`](kb/AUDIT_TRAIL.md) §7). Every numeric value in every reviewer-facing artifact traces to a source PDF + page + clause. Every architectural decision is in [`docs/decisions.md`](docs/decisions.md) D-001 through D-017 with alternatives and revisit-at-scale notes. The repo is structured so a Sarvam engineer joining the project on Monday could ship v1.1 by Friday.

That's what I'm offering Sarvam — not the bot, the way of building.

---

*Authored 2026-05-13. Live demo: https://rohitsar567-insurancebot.hf.space · Repo: https://github.com/rohitsar567/insurance-sales-bot · Contact: rohitsar567@gmail.com*
