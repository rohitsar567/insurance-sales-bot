# 04 — Failure Modes Register

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Version | 1.0 |
| Date | 2026-05-17 |
| Status | **Superseded as a present-state map — see banner below** |

> ⚠️ **This document predates the single-brain rewrite and is NOT the current
> safety architecture.** It describes a `backend/faithfulness.py` 4-gate
> LLM-judge (Gate 1 retrieval floor → Gate 2 citation integrity → Gate 3
> regex grounding → Gate 4 cross-family judge). **None of that exists** —
> `faithfulness.py` and the separate judge were removed in the single-brain
> consolidation.
>
> **Current safety model (authoritative: [`README.md`](../../README.md) §6):**
> faithfulness is now **structural** — the single Gemini 2.5-flash brain
> (one call/turn with `save_profile_field` / `retrieve_policies` /
> `mark_recommendation`) can only state what `retrieve_policies` returned
> and must cite it; recommendation fit is gated in `backend/scorecard.py` /
> `retrieval_filters.py`. There is **no separate faithfulness-judge LLM**,
> no `orchestrator`, and no Brain-Fast/Brain-Main split. The hardened
> surface is the **8-gate uploaded-PDF defence in `backend/security.py`**
> (file mechanics, content quality, prompt-injection, per-session + per-IP
> rate limits, encrypted-PDF, page-ceiling, hash-dedupe) plus per-session
> quarantine isolation with a 24 h TTL. A small `backend/nim_fallback.py`
> (NVIDIA NIM) only covers transient Gemini errors so the turn still
> completes. **Everything below this banner is the historical failure-modes
> analysis that informed today's design — the per-mode "Status: Live"
> lines describe the pre-rewrite system, not the present one.**

## 0. Purpose

This is the explicit register of **how the system can fail**, **how we detect each failure**, and **what we do about it**. The goal is that no failure mode is implicit — every one is named, tracked, and mitigated.

In a regulated BFSI domain, the worst failure isn't "the bot looks slow" — it's "the bot mis-sold a policy by hallucinating a benefit." This document is therefore biased toward grounding and refusal failures.

## 1. Failure mode register

### F-01 — Bot hallucinates a coverage detail

**Description:** Bot claims a policy covers something it doesn't, or quotes a wrong waiting period / sub-limit.
**Detection:**
- Run-time: Faithfulness Gate 4 (LLM-judge — JUDGE_CHAIN with NIM Mistral Large 3 675B primary; different family from the Gemini brain so grading stays non-circular; see [ADR-040](../60-decisions/ADR-040-google-gemini-primary.md)). KI-171 skips Gate 4 on `fact_find` + `recommendation` intents.
- Run-time: Faithfulness Gate 3 (regex grounding — every ₹, %, day/month/year in the reply must appear in retrieved chunks).
- Post-hoc: Gold Q&A eval (factual_accuracy metric).
**Mitigation:** Block reply → return safe refusal → log to `logs/hallucinations.jsonl`. Audit log enables manual review and corpus improvement.
**Owner:** `backend/faithfulness.py`
**Status:** Live and proven (caught a real cross-policy citation error during smoke test).

### F-02 — Bot fabricates a citation (cites a clause that doesn't exist)

**Description:** Reply contains `[Source: ...]` pointing at a policy or page that wasn't retrieved.
**Detection:** Faithfulness Gate 2 — every cited policy_name must match a retrieved chunk's metadata.
**Mitigation:** Block → refusal.
**Owner:** `backend/faithfulness.py::_gate_citation_integrity`
**Status:** Live.

### F-03 — Retrieval misses the relevant clause

**Description:** Question is answerable from the corpus, but vector search ranked the wrong chunks first.
**Detection:**
- Gate 1 (retrieval floor) catches "no decent chunks at all" cases (top score < 0.40).
- Doesn't catch "wrong chunks at high scores" cases.
**Mitigation:**
- Re-tune embedding model (currently BGE-small; tested upgrade to BGE-large in v2).
- Add hybrid retrieval (BM25 + vector) — captures exact-term matches that semantic embedding misses.
- Add policy-name awareness — if query mentions a specific policy, filter retrieval to that policy.
**Status:** Hybrid retrieval is a v2 enhancement.

### F-04 — Sarvam-M reasoning chain truncates mid-thought (HISTORICAL, fixed by D-019)

**Description:** Sarvam-M emits `<think>...</think>` reasoning. If `max_tokens` is exhausted before `</think>` is reached, the reply is unusable. This was a recurring issue when Sarvam-M was the primary brain.
**Detection:** `strip_think_tags()` checks for `<think>` without matching `</think>`.
**Resolution (2026-05-14, D-019 → refined 2026-05-15 across KI-080 / KI-167 / KI-179):** Sarvam-M moved out of the brain role entirely. The current Brain Fast + Brain Main chains lead with Google AI Studio (Gemini 2.0 / 2.5 Flash) with NIM (Qwen 80B / Mistral Large 3 675B / Llama-4 Maverick) and OpenRouter `:free` (Nemotron-3-Super, Qwen 80B) as fallbacks; see [ADR-040](../60-decisions/ADR-040-google-gemini-primary.md). All chain primaries emit direct responses without `<think>` preambles, and native JSON mode (`response_mime_type=application/json` on Gemini, `response_format={"type":"json_object"}` on NIM / OpenRouter) provides server-side parseable output. Per-turn primary is elected from a 300s background probe (KI-080, [ADR-031](../60-decisions/ADR-031-sticky-primary-election.md)) — adapts to live provider degradation. Sarvam-M remains only for Indic translation. F-04 cannot fire on the current stack.
**Owner:** `backend/orchestrator.py`
**Status:** Resolved by architecture change.

### F-05 — STT mis-transcribes a number ("₹50 lakh" → "₹50 lakhs" → "₹50 lakhs of crores")

**Description:** Saarika hears a noisy audio clip and produces incorrect numerals.
**Detection:** Currently no automated check — transcript is shown in chat so user sees it before bot replies.
**Mitigation v1:**
- Show transcribed text to user (already in UI) so user can re-record if wrong.
- Confidence threshold from Saarika exposed to caller.
**Mitigation v2:**
- Compare STT confidence to a threshold; below 0.7 → ask user to confirm.
- Add "Did you mean X?" disambiguation flow.

### F-06 — TTS mispronounces medical / insurance terms

**Description:** "Bulbul" reads "PED" as "ped" instead of "P-E-D"; "subrogation" gets garbled.
**Detection:** Manual listening; eventually a pronunciation gold set.
**Mitigation:**
- Pre-process the LLM reply: expand domain acronyms before TTS (e.g., "PED" → "pre-existing disease" before sending to Bulbul).
- v2: Use Bulbul's SSML support (if available) for explicit pronunciation hints.

### F-07 — User asks regulatory / tax question, bot has no IRDAI corpus

**Description:** "What does IRDAI say about cataract waiting periods?" — corpus doesn't include IRDAI text (D-017 deferred).
**Detection:** Faithfulness Gate 1 (retrieval floor) catches it — top retrieval score on regulatory queries is < 0.40 since no regulatory chunks exist.
**Mitigation:** Refusal returned: *"I'd rather not answer that without stronger evidence in the policy documents I have."* This is the SAFE failure mode.
**Future:** Acquire IRDAI corpus via Playwright (v2). Then bot can ground regulatory claims.
**Status:** Working as designed — verified via smoke test.

### F-08 — Cross-policy citation (bot answers about Policy A but cites Policy B)

**Description:** Top retrieved chunk is from a different policy than the one the user asked about. Bot generates an answer that's textually correct but cites the wrong source.
**Detection:** Gate 4 LLM-judge — caught a real instance of this in smoke test (Activ Health question, Activ Secure citation → flagged + blocked).
**Mitigation:**
- Pass `policy_filter_ids` to retrieval when user mentions a specific policy name (v1.1 enhancement, simple).
- Embed policy_name into chunk + add per-policy fine-grained retrieval boost.
**Status:** Detected & blocked correctly by Gate 4. User-side fix pending.

### F-09 — Voice latency exceeds budget

**Description:** End-to-end (user speech end → bot speech start) p95 > 7s (Doc 01 C1).
**Detection:** Per-turn latency logged in `logs/turns.jsonl`. Aggregate via eval harness.
**Mitigation v1:**
- Tiered brain routing (ADR-040): voice turns and fact-find go to Brain Fast (Gemini 2.0 Flash primary, ~1-2s TTFT); `comparison` and `recommendation` intents hit Brain Main (Gemini 2.5 Flash primary, slightly higher latency but stronger synthesis). Sarvam-M no longer in the brain hot path.
- TTS happens server-side and is streamed via base64 in same response. Future: WebSocket for streaming TTS.
**Status:** Brain Fast / Brain Main typically respond in 2-6s; long-tail latency comes from heavy fallback candidates (NIM Mistral 675B) only when Google quota is exhausted.

### F-10 — Streamlit-Cloud-Free / Render-Free cold start

**Description:** Render free tier spins down after 15 min idle. First user after sleep waits ~50s for spinup.
**Detection:** Render dashboard + manual.
**Mitigation:**
- Frontend shows a "starting up" indicator if backend takes > 5s on the first health check.
- Keep-warm cron pinging `/api/health` every 14 min (v2 — costs free-tier hours).
**Status:** Accepted for v1 (free tier).

### F-11 — Corpus drift (insurer publishes a new policy version, ours is stale)

**Description:** Our corpus has the 2024 wording; insurer publishes 2025 with different waiting periods.
**Detection:** None automated in v1.
**Mitigation v2:**
- Per-policy `last_updated_date` field + a cron job that re-crawls every 7 days.
- Diff detector → alert on changes.
**Status:** v2 work.

### F-12 — Sarvam silently updates a model

**Description:** Sarvam updates Saarika v2.5 → v2.6 with subtly different STT behavior (e.g., worse on Hinglish numbers). Our eval scores drift.
**Detection:** Nightly synthetic eval — sudden accuracy drop alerts.
**Mitigation:** Pin model version where supported; document the version in `decisions.md` D-006.
**Status:** Monitoring pattern documented; cron job is v2.

### F-13 — User uploads a malicious PDF (XSS / macro)

**Description:** F-13 is LIVE per ADR-044 (2026-05-27). Mitigated by the 8 gates in `backend/security.py` (file mechanics / dangerous PDF features / content quality / prompt injection / per-session rate limit / per-IP rate limit / encrypted-PDF reject / page-ceiling / hash dedupe). Failed uploads are logged to `logs/upload_blocks.jsonl`. The 8-gate state is the same one this file's top banner already lists.

### F-14 — User asks for medical advice ("should I get this surgery?")

**Description:** Bot is asked a clinical question outside its role.
**Detection:** Persona prompt forbids medical advice (rule 4).
**Mitigation:** Bot replies: "I can tell you what's covered. For whether to get a treatment, please consult a doctor."
**Owner:** `backend/persona.py`
**Status:** Live in prompt; needs explicit gold Q&A test case.

### F-15 — User asks "should I buy this?" (transactional recommendation)

**Description:** Bot is asked for a final buy decision.
**Detection:** Persona rule 5.
**Mitigation:** Bot recommends with reasoning tied to user profile, but ends with: "I'd recommend you confirm with the insurer directly before finalizing."
**Status:** Live.

## 2. Hallucination defense — defense in depth (summary)

Layers stack in this order; each catches different failure classes:

```
User query
   ↓
[Retrieval]
   ↓
[Gate 1] retrieval-floor — refuse if no evidence
   ↓
LLM generates reply
   ↓
[Gate 2] citation integrity — block if cited a non-retrieved source
   ↓
[Gate 3] numeric grounding (regex) — block if number not in chunks
   ↓
[Gate 4] LLM-judge faithfulness — block if claims unsupported
   ↓
Reply to user
   ↓
[Audit log] every block → logs/hallucinations.jsonl
```

Run-time. Auditable. Tested.

### F-16 — Translation cascade introduces drift after faithfulness gate

**Description:** Indic queries use the cascade: Sarvam translates Hinglish → English, the elected Brain Main candidate (Gemini 2.5 Flash by default, NIM Mistral 675B / Maverick / Qwen as fallbacks) reasons → English answer, faithfulness gates run on English answer, Sarvam translates English answer → Hinglish. **Faithfulness does NOT re-verify the final Hinglish output.** If Sarvam corrupts the translation (drops a citation, changes a number, invents a benefit), we wouldn't catch it.
**Detection v1:** None automated. Manual spot-check of bilingual eval set.
**Mitigation v1:** Sarvam translator system prompt explicitly forbids changing numbers/citations + caps at 60 words; preserves `[Source: ...]` tags.
**Mitigation v2:** Back-translate Hinglish→English; compare against original English; block if cosine similarity < 0.85.
**Status:** Accepted limitation for v1.

## 3. Open mitigations (this document tracks status)

| # | Mitigation | Owner | Status |
| --- | --- | --- | --- |
| M-01 | Hybrid retrieval (BM25 + vector) | RAG | v2 |
| M-02 | Policy-name filter for retrieval when user mentions a policy | Orchestrator | v1.1 |
| M-03 | STT confidence threshold + clarify-on-low-confidence | Orchestrator | v1.1 |
| M-04 | Acronym expansion pre-TTS | TTS provider | v2 |
| M-05 | Acquire IRDAI corpus via Playwright | Ingest | v2 |
| M-06 | Render keep-warm cron | Infra | v2 |
| M-07 | Nightly synthetic eval cron | Infra | v2 |
| M-08 | Live-traffic spot grading | Eval | v2 |
| M-09 | 3-judge consensus for grader | Eval | v2 |
| M-10 | Sarvam-M scoped to Indic translation only; brain is one Gemini 2.5-flash call/turn with tools, small `nim_fallback` for transient errors | single_brain | Live ✅ |
| M-11 | Structural faithfulness (LLM can only cite what `retrieve_policies` returned; recommendation fit gated in `scorecard.py` / `retrieval_filters.py`) — the separate 4-gate judge was removed | single_brain | Live ✅ (superseded the 4-gate judge) |
