# Ingestion Policy (2026-05-14)

**Headline:** All extraction, embedding, and Chroma writes for the curated
policy corpus run on the developer's Mac. The deployed Hugging Face Space
serves pre-built indexes only. The single exception is user-uploaded PDFs,
which are embedded on-demand into a SEPARATE `user_uploads_quarantine`
Chroma collection isolated from the main `policies` corpus.

---

## Where things run

| Pipeline stage                              | Runs on Mac (`.venv`) | Runs in Space (Docker) |
|---------------------------------------------|:---------------------:|:----------------------:|
| Source-PDF download (`rag/corpus/**`)       | yes                   | no                     |
| Text extraction (pdfplumber → JSON)         | yes                   | no                     |
| Chunking (800-token windows, 120 overlap)   | yes                   | no                     |
| Embedding (BAAI/bge-small-en-v1.5)          | yes                   | **only quarantine**    |
| Chroma write — `policies` collection        | yes                   | no                     |
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
   budget and a 1 GB image cap. Embedding ~190 PDFs on Space CPU was
   slower than on a developer Mac and pushed boot far past the platform's
   health-check window.

Fail-fast is better: `entrypoint.sh` now validates Chroma is readable and
populated, and exits with a loud error if not. The fix is documented at
exit time (run `rag.ingest` locally, push vectors, redeploy). Total
boot-to-serving time on the Space is now seconds, not tens of minutes.

## Exception — user uploads

`POST /api/upload-policy` accepts an arbitrary PDF from the public web and
must embed it before the chatbot can answer questions about it. To keep
this off the main corpus while still allowing the feature:

- The endpoint writes into a SEPARATE Chroma collection named
  `user_uploads_quarantine` (created lazily via
  `rag.ingest.get_quarantine_collection`).
- Every chunk in that collection is tagged with the uploading session's
  `session_id`. Retrieval against the quarantine collection is scoped to
  that `session_id`, so one user's upload never surfaces in another
  user's session — and never surfaces in queries against the main
  `policies` collection at all.
- The quarantine collection is the ONLY place the deployed Space ever
  writes embeddings. It does not back-propagate to the dataset, does not
  persist across Space rebuilds (it lives only in the Space's working
  Chroma directory), and is not part of any evaluation set.

If a user-uploaded PDF turns out to be worth curating, the operator pulls
it into `rag/corpus/<insurer>/` on the Mac and re-runs the local workflow
above. There is no in-place promotion from quarantine to `policies`.
