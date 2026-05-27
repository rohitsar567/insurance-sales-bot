# Ingestion Policy (2026-05-27, supersedes 2026-05-14 state per ADR-044)

**Headline:** All extraction, embedding, and Chroma writes for the curated
policy corpus run on the developer's Mac. The deployed Hugging Face Space
serves pre-built indexes only. The single exception is user-uploaded PDFs,
which are embedded on-demand and — per ADR-044 (2026-05-27) — **dual-write**
into BOTH a per-session `user_uploads_quarantine` Chroma collection AND the
global `policies` Chroma collection, so the upload becomes a first-class
marketplace card with the same scorecard / premium / RAG endpoints as the
catalogued 148.

---

## Where things run

| Pipeline stage                              | Runs on Mac (`.venv`) | Runs in Space (Docker) |
|---------------------------------------------|:---------------------:|:----------------------:|
| Source-PDF download (`rag/corpus/**`)       | yes                   | no                     |
| Text extraction (pdfplumber → JSON)         | yes                   | no                     |
| Chunking (800-token windows, 120 overlap)   | yes                   | no                     |
| Embedding (BAAI/bge-small-en-v1.5)          | yes                   | **only quarantine**    |
| Chroma write — `policies` collection (curated 148 corpus) | yes        | no                     |
| Chroma write — `policies` (per-upload chunks via dual-write) | no       | yes (on-demand)        |
| Chroma write — `user_uploads_quarantine`    | no                    | yes (on-demand)        |
| HF dataset push (`rag/vectors/**`, `rag/extracted/**`, `rag/corpus/**`) | yes | no |
| HF Space push (code only)                   | yes                   | no                     |
| Serving (retrieval, LLM, voice, frontend)   | no                    | yes                    |

Reading the column "Runs in Space": the only embedding work the deployed
container ever performs is on-demand quarantine embedding of a user-uploaded
PDF inside `POST /api/upload-policy`. Every other embedding has been baked
into the image at Docker build time via `huggingface_hub.snapshot_download`
of the companion dataset (`rohitsar567/insurance-bot-data`).

## Local workflow

After any change to the curated corpus, run these on the Mac in order:

```bash
# 1. Re-build the Chroma index against the local PDFs + extracted JSONs.
.venv/bin/python -m rag.ingest

# 2. Push the freshly-built rag/vectors/ to the HF dataset so the next
#    Space rebuild pulls a ready-to-serve index.
.venv/bin/python tools/upload_vectors_to_dataset.py

# 3. Only if structured extraction JSONs changed (rag/extracted/*.json) —
#    e.g. you re-ran an NIM extraction batch.
.venv/bin/python tools/upload_extracted_to_dataset.py

# 4. Push the code to the HF Space. This triggers a Docker rebuild; the
#    build pulls rag/vectors/ + rag/extracted/ + rag/corpus/ from the
#    dataset (see Dockerfile `allow_patterns`).
.venv/bin/python tools/upload_to_hf.py
```

The four steps are sequential — step 4 must come last because the Space
rebuild snapshots whatever state the dataset is in at build time. If you
push code before the vectors are synced, the Space will boot against a
stale index (or, if the schema changed, fail the entrypoint validation).

## Why

Prior policy auto-ingested on Space boot if Chroma looked empty or broken.
That created two bad failure modes:

1. **Confusing schema breakage with normal boot.** A breaking Chroma schema
   change (e.g. a chromadb version bump) silently triggered a 20+ minute
   re-ingest during `APP_STARTING`. The Space logged nothing user-visible
   while it churned, then either succeeded (slow, but fine) or failed deep
   inside the embedder. Operators could not tell which.
2. **Resource cost on the wrong machine.** Free-tier Spaces have a CPU
   budget and a 1 GB image cap. Embedding the curated corpus (148 policies
   across 21 insurers as of 2026-05-27; older docs referencing 190/19 or
   256 are pre-dedup file counts) on Space CPU was slower than on a
   developer Mac and pushed boot far past the platform's health-check
   window.

Fail-fast is better: `entrypoint.sh` now validates Chroma is readable and
populated, and exits with a loud error if not. The fix is documented at
exit time (run `rag.ingest` locally, push vectors, redeploy). Total
boot-to-serving time on the Space is now seconds, not tens of minutes.

## Exception — user uploads (ADR-044, 2026-05-27)

`POST /api/upload-policy` accepts an arbitrary PDF from the public web and
must embed + extract it before the chatbot can answer questions about it.
The pipeline lives in `backend/uploaded_docs.py` + `backend/security.py`
and is governed by **ADR-044** (with hardening bundle KI-330 / KI-331 /
KI-332 / KI-333; live on `e7f799a`). See README §2.8 and
[ADR-044](../60-decisions/ADR-044-uploaded-pdf-parity.md) for the full
spec.

**Dual-write model.** Unlike the original v1 design (quarantine-only),
every accepted upload is written into BOTH:

- a per-session `user_uploads_quarantine` Chroma collection (session-scoped,
  24h TTL, never persisted across Space rebuilds), AND
- the global `policies` Chroma collection — so the uploaded policy becomes
  a **first-class marketplace card** with the same scorecard / premium /
  RAG endpoints as the catalogued 148 policies across 21 insurers.

**8-gate defence in `backend/security.py`** runs before any embedding:
(1) file mechanics + size 5KB-25MB + `%%EOF` + no executable / JS payloads,
(2) content quality ≥1,500 chars + ≥3 pages + ≥1 insurance keyword,
(3) prompt-injection sweep, (4) per-session rate limit, (5) per-IP rate
limit, (6) encrypted / locked PDF reject, (7) page-count ceiling >200,
(8) hash dedupe + reject-cache. A UIN net-new check and PDF-text fuzzy
match against the catalogued 148 also run beyond the 8. Failed uploads
log to `logs/upload_blocks.jsonl`.

**Heuristic floor — HARD guarantee.** `build_record()` runs sub-second
on every accepted upload and writes `record.json` BEFORE the LLM ever
fires. Post-KI-332 it lifts the floor from ~47% to ~65-70% via 28+
regex patterns. This is the parity guarantee on the fail path
(KI-331 + KI-333: status ↔ scorecard card always agree).

**LLM extraction chain.** hash-cache → multi-pass (≥25K chars, 7 sections
in parallel) → single-pass Gemini 2.5-flash (3 jittered retries) →
NIM fallback → heuristic floor. Provenance surfaces on status as
`llm_used` + `llm_response_chars`.

**Open caveat — cross-session leakage.** The dual-write into `policies`
means another session's retrieval can surface a user's upload. Mitigated
today by a per-session quarantine boost in retrieval; full per-session
scoping of the `policies` collection is a tracked follow-up.
