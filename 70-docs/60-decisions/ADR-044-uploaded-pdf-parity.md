# ADR-044 — Uploaded-PDF parity with catalogued 148 + locked upload sequence

**Date:** 2026-05-27
**Status:** Accepted
**Related:** Extends ADR-022 (conversational profile updates), ADR-040 (Gemini primary), ADR-043 (no cross-session recall). Does not supersede any.
**Commits:** `4bb66dd` (initial parity), `245310d` (asyncio import hotfix), `88e54a8` (backfill), `24349be` (PTT voice gate), `9ec9eae` (Header import hotfix), `7ef3ca3` → `fd30ceb` → `52b0b5d` → `835f745` → `e8ccfa0` (LLM-extraction iteration), `dfaa4d6` → `3dcbe9b` (sequence lock).

## Context

Pre-this-ADR the upload pipeline produced a card that the user (rightly) said *"lacks all data as required in parity with the existing policy pdf cards"*:

- Card displayed only ~13–48% of decision-critical fields (vs the 74% median completeness on the catalogued 148).
- `insurer_slug` was hardcoded to `"user-upload"`, so the Claim Experience sub-score got no real IRDAI claim-ratio data — the inline card showed *"Reviews: reputation data being compiled"* indefinitely (placeholder text with no backend that ever populates it).
- The chat sequence around an upload was broken: ack message + choice prompt fired in parallel BEFORE the card was ready, and voice auto-fire during the long indexing window dropped unprompted "please upload" or analysis messages into chat.
- Old uploads (uploaded before the new pipeline was wired) never got upgraded.

User directive (2026-05-27): *"Whatever is done for the 148 policies pre-populated, exact same level of usability needs to be there for user-uploaded PDFs. No less."* And the chat sequence: *"the card should not be presented until the card summary and the detailed LLM extraction and full field population is done. And until the card is presented, no further questions should be asked."*

## Decision

Three coordinated changes, all in service of catalogued-grade parity for live uploads.

### D1. LLM-assisted extraction pipeline for live uploads

Identical extractor as the catalogued 148:

| Step | Same as catalogued 148 |
|---|---|
| Chunking | ✅ `rag/ingest.py`, ~500-token overlap, BGE-small-en-v1.5 384-d embeddings |
| Embedding into Chroma | ✅ Per-session `user_uploads_quarantine` + global `policies` collection |
| LLM extraction | ✅ Same `get_brain_llm()` (Gemini 2.5-flash primary, NIM chain fallback), same `EXTRACT_SYSTEM` prompt, same `HealthPolicy` Pydantic schema |
| Structured JSON output | ✅ Same `rag/extracted/<policy_id>.json` path |
| Marketplace inclusion | ✅ Same `_marketplace_catalogue` Pass-1 + Pass-2 pipeline; uploads become first-class cards (`UPLOADED_DOCS_DIR/<pid>/`) |
| Scorecard | ✅ Same `/api/policies/{id}/scorecard` endpoint, no `user-upload__` branches |
| Premium estimate | ✅ Same `/api/premium/estimate` endpoint |
| RAG chat answers | ✅ Same `retrieve_policies` tool, same faithfulness guard |
| Card UI | ✅ Same `PolicyScorecardWidget` component, no source-aware branches |
| Source-PDF link | ✅ Persisted PDF at `UPLOADED_DOCS_DIR/<pid>/source.pdf`, served via the same per-policy PDF endpoint |

The only difference from the catalogued 148 is that catalogued PDFs went through **multiple extraction passes + human review + curated overlay** (`40-data/policy_facts/<insurer>__<product>.json`). Live uploads run **one extraction pass with no human review**, but with two safety nets:

- **Heuristic baseline floor** — `build_record()` in `backend/uploaded_docs.py` synchronously regex-extracts ~30–50% of fields during the upload HTTP call. This is the floor; on LLM extraction failure the card still has real data. Same shape as the curated layer (`{value, source_pdf_path, source_quote, _confidence}`).
- **LLM-into-heuristic merge** — when `extract_one_for_upload` completes, the LLM output is merged INTO `record.json` (LLM wins per-field where non-empty, heuristic stays where LLM was silent). This is the same "extracted + curated overlay" model the catalogued 148 use.

### D2. Insurer detection from PDF text

`backend/uploaded_docs.py::detect_insurer_slug()` scans the first ~6 000 chars of the PDF text against the 21 insurer name patterns we have reviews data for (acko, aditya-birla, bajaj-allianz, care-health, cholamandalam, go-digit, hdfc-ergo, icici-lombard, iffco-tokio, indusind-general, manipalcigna, national-insurance, new-india, niva-bupa, oriental-insurance, reliance-general, royal-sundaram, sbi-general, star-health, tata-aig). On a hit, `insurer_slug` flips off the generic `"user-upload"` to the real slug. The Claim Experience sub-score then reads the corresponding `40-data/reviews/<slug>.json` — same path as a catalogued card. Fail-closed: no match ⇒ stays `"user-upload"` (the generic insurer reputation isn't fabricated, the sub-score just falls back to a neutral score).

Live verification: Sarvah Param.pdf (a ManipalCigna product) → detected as `manipalcigna` → Claim Experience score = 83/100 with verbatim signals `["cashless supported", "99.0% CSR (IRDAI 2023-24)"]`.

### D3. New upload status endpoint + frontend polling

- **New `_UPLOAD_EXTRACTION_STATUS` in-memory dict** in `backend/uploaded_docs.py` tracks per-upload state: `pending → running → complete | failed`.
- **`extract_one_for_upload` writes status** at every phase + final `completeness_pct` and `overall_grade`.
- **New `GET /api/upload/extraction-status/{policy_id}`** exposes the live dict. Returns `status="unknown"` for unrecognised policy_ids so the client can stop polling.
- **Frontend `handleFile` polls** every 3 s for up to 120 s. Card-bearing assistant message + choice prompt fire only after the poll sees `complete` or `failed`/timeout.

### D4. Locked chat sequence

Per user directive (verbatim): *"until the card summary and the detailed LLM extraction and full field population is done, the card should not be presented. And until the card is presented, no further questions should be asked."* Implemented:

```
1. User picks file (PDF input)
2. setUploadStatus(t("upload.indexing"))         ← amber banner above composer
   setExtractionInFlight(true)                    ← gates EVERY send-path
3. POST /api/upload-policy                        ← ~10-60s
4. pushAssistant(ack_reading)                     ← "Got it — reading X, ~30-60s"
   [NO card yet, NO choice prompt yet]
5. Poll /api/upload/extraction-status every 3s, up to 120s
   [During this entire wait window, EVERYTHING is gated:
    - Send button:    disabled={busy || !input.trim() || extractionInFlight}
    - Textarea:       disabled={busy || extractionInFlight}
    - PDF button:     disabled={busy || extractionInFlight}
    - send() function: early-return on extractionInFlight (last-line guard)
    - voiceSubmitRef:  guarded
    - PTT/Sarvam path: guarded
   ]
6. On status="complete":
     pushAssistant(card_ready_message, { citations: [{policy_id, ...}] })
     [card renders inline below this bubble]
     pushAssistant(choice_prompt)
   On status="failed" / timeout:
     pushAssistant(extraction_failed_message)
     pushAssistant(choice_prompt)
7. setExtractionInFlight(false)                   ← controls re-enable
8. User picks "finish profile" / "dive into PDF" → normal chat continues
```

The choice prompt **never fires before the card lands**. This is verified by the in-code ordering — both branches of step 6 push the choice prompt AFTER the prior message.

### D5. Backfill for legacy uploads

`backend/uploaded_docs.py::backfill_extractions(force=False)` iterates `UPLOADED_DOCS_DIR`, skips any policy_id that already has `rag/extracted/<pid>.json` (unless `force=True`), and runs `extract_one_for_upload` for the rest. Fired as a fire-and-forget `asyncio.create_task` from the `@app.on_event("startup")` hook so every container boot upgrades any old upload that was persisted before this pipeline was wired. Also exposed as `POST /api/admin/upload/reextract?force=<bool>` for on-demand operator triggering.

## Consequences

- **Live upload cards reach catalogued-grade depth** by construction: same LLM, same schema, same scorecard endpoint. Verified live: Sarvah Param.pdf upload → grade C, score 65/100, 6 sub-scores all populated with verbatim signals, real IRDAI claim data via manipalcigna insurer detection.
- **Heuristic baseline is a hard floor** — even if Gemini 5xxs / parses fail, the card still produces a real grade C off the 47.8% heuristic completeness.
- **No race conditions in the chat flow** — Send button + every voice path are gated by `extractionInFlight`, and the `send()` function has a last-line guard so even a programmatic future caller can't bypass.
- **Backward-compatible for old uploads** — backfill upgrades anything persisted under `UPLOADED_DOCS_DIR/` before this pipeline was wired.
- **Operator visibility** — `/api/upload/extraction-status/{pid}` shows live state to the frontend AND can be polled by the operator for debugging.

## Caveats / open follow-ups

- **Single LLM pass per upload** — catalogued 148 had multiple extraction passes + human review. A non-standard PDF that the heuristic also can't read well will produce a card at the heuristic floor (40–50% completeness) without a grade lift from the LLM. Multi-pass extraction is a known follow-up (task #109).
- **Gemini intermittency** — live observation shows Gemini occasionally returning unparseable output for the same prompt that succeeded earlier. NIM fallback fires automatically on Gemini failure. Cause not fully traced; tracked for follow-up.
- **Cross-session retrieval** — every upload is written to both the per-session quarantine AND the global `policies` Chroma collection (so it can become a marketplace card visible to other users). The global chunks have no `session_id` metadata, so `retrieve_policies` queries can pull chunks from other users' uploads. For the "analyse THIS upload" intent this can produce cross-policy confusion. Mitigated today by the per-session quarantine boost in retrieval; full per-session scoping is a tracked follow-up.

## 2026-05-27 hardening bundle (post-audit)

Post-launch audit on `e8ccfa0` surfaced five distinct defects across the upload path. All five landed in a same-day bundle (commits `2abfd01`, `e204c0e`, `2323b26`, `58e3c82`, `2a58c28`, `993bcd5`).

### H1. Status ↔ scorecard parity

- **Problem:** status endpoint reported `completeness_pct` / `overall_grade` from `build_scorecard(doc, profile=None)` without `insurer_reviews`; card endpoint used `_catalogue_scorecard(pid, None)` which folds in heuristic + curated + reviews + dedup. Numbers diverged on the same upload: status `17.4% / None`, card `47.8% / C`.
- **Two-bug compounding:** missing `insurer_reviews` AND reading `.overall_grade` instead of `.grade`. The dataclass attribute is `.grade`; only the wire `ScorecardResponse` renames it. So even with reviews wired in, the grade field came back `None`.
- **Fix:** status resolver now mirrors `/api/policies/{id}/scorecard` exact order — primary `_catalogue_scorecard(pid, None)`, fallback `build_scorecard(doc, insurer_reviews=ir, profile=None)`, read `.grade` (not `.overall_grade`). Commits `2abfd01` → `e204c0e` → `2323b26`.

### H2. Hash-cache short-circuit had the same bugs (commit `58e3c82`)

- The Tier-2 content-hash branch (sha256 match ⇒ reuse prior extraction) had the IDENTICAL three bugs the main path had (no reviews, `.overall_grade`, no insurer slug threading). Cache-hit uploads silently surfaced `comp=17.4 grade=None` even when the actual card was `47.8 / C`.
- **Fix:** same three-bug fix applied + new field `llm_used="hash-cache"` so the operator can see WHY no LLM ran on a given upload.

### H3. Provenance fields exposed (commit `2abfd01`)

- New status fields: `llm_used` (one of `gemini-2.5-flash#1|#2|#3`, `nim-fallback`, `hash-cache`, or `None` for failures) + `llm_response_chars`.
- Lets the operator verify Gemini is actually running without HF Space stdout access.
- Verified live: fresh upload shows `gemini-2.5-flash#1` returning 573 chars (unparseable), retry as `gemini-2.5-flash#2` returning 899 chars (parsed).

### H4. KI-330 — `view_context` → ACTIVE POLICY DIVE-IN block (commit `2a58c28`)

- Found in 2026-05-27 e2e audit: on 3 of 5 uploads, asking *"What are the waiting periods on this policy?"* got back *"Before I pull your recommendations, just a couple more (you can skip any):"*. `single_brain` pivoted to profile-building instead of answering about the policy.
- **Triple root cause:** (1) `ChatRequest.view_context` field declared at launch but NEVER consumed anywhere on backend, (2) `single_brain.handle_turn` signature didn't accept it, (3) frontend set `view_context.active_policy_id` only on a clicked marketplace card (openPolicy modal), never on a just-uploaded PDF.
- **Fix:** `handle_turn(..., view_context=None)`, new `_build_active_policy_block()` that prepends an ACTIVE POLICY DIVE-IN block to the system instruction when `view_context.active_policy_id` is set. Frontend gains an `activeUploadPid` state set when the card lands, plumbed into every chat turn's `view_context`.
- Verified: 9/10 grounded on post-fix audit, up from 0/10 pre-fix.

### H5. KI-331 — heuristic-floor card surfaces on LLM-fail (commit `993bcd5`)

- User caught live on `Test Policy.pdf` (8 MB) upload: after Gemini 3/3 retries failed and the 120s poll timed out, frontend pushed only a prose *"I couldn't pull a full analysis"* message, never the card — even though `record.json` already had `47.8% / grade C` from the heuristic pass that runs BEFORE the LLM fires.
- **Fix:** fail/timeout branch now pushes the same card-bearing assistant message as the success branch (`citations=[pid]` → inline `PolicyScorecardWidget` renders with whatever `record.json` has) + the soft caveat + the choice prompt. `setActiveUploadPid` still fires so dive-in mode activates for follow-up questions.
- Plus: `MAX_TRIES` bumped 40 → 50 (120s → 150s poll budget) since Gemini retry #3 sometimes lands at ~130s.

### Live verification matrix (post-bundle)

- **5 PDFs** (manipalcigna, hdfc-ergo, care-health, icici-lombard, star-health) × **7 layers** (upload, extraction, scorecard, premium baseline, premium older+PED, personalization profile, RAG grounded answer) = **35 cells, 33 green**.
- 2 honest misses: `Test Policy.pdf` 3/3 Gemini fails caught by heuristic floor (working as designed); one star-health "room rent" question correctly answered but keyword detector false-negative.
- Hash-cache parity verified on Manipal back-to-back uploads (~1s speedup, `llm_used='hash-cache'`, comp/grade equal across status + card).
- Playwright UI sequence audit: 5/5 checks pass (ack → card/fail → choice strictly ordered).

## Verification

- Live audit on commit `e8ccfa0` (https://rohitsar567-insurancebot.hf.space):
  - Fresh upload of Sarvah Param.pdf via `/api/upload-policy`
  - Extraction status: pending → running → complete in ~12 s
  - Scorecard endpoint: `grade=C, score=65, completeness=52.2%, insurer_slug=manipalcigna`
  - Six sub-scores all populated with verbatim signals
  - Claim Experience score 83 with `["cashless supported", "99.0% CSR (IRDAI 2023-24)"]` — real IRDAI claim data, sourced from `40-data/reviews/manipalcigna.json` via the detected insurer slug
- Heuristic baseline floor verified: when Gemini fails on a fresh upload, the card still serves `grade=C, score=64, completeness=47.8%` via the heuristic record
- Sequence lock verified live: choice prompt always appears AFTER card_ready_message OR extraction_failed_message, never before
