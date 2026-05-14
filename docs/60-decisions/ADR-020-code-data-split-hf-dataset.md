# ADR-020: Code in Space repo, data in companion HF Dataset

**Status:** Locked
**Date:** 2026-05-14

## Context

Free-tier HuggingFace Spaces have a hard **1 GB combined git+LFS storage cap**. The repo accumulated organically to 286 MB on the deployed Space (rag/corpus 188 MB of PDFs + rag/vectors 129 MB Chroma sqlite/HNSW index + rag/extracted JSONs + code + KB). New pushes that touched large blobs hit `403 Forbidden: Repository storage limit reached`.

Worse, the 87 MB IRDAI master-circular PDF and 110 MB Chroma DB exceeded HF's 10 MB per-file plain-git push threshold, so even before the repo cap they were rejected with the "use git-lfs.com" hint.

## Decision

**Split the architecture: code in the Space repo, data in a companion HF Dataset.** The Space's Dockerfile fetches the data at build time via `huggingface_hub.snapshot_download`.

| Component | Lives in |
|---|---|
| `rag/corpus/*.pdf` (208 PDFs, 188 MB) | HF Dataset `rohitsar567/insurance-bot-data` |
| `rag/vectors/chroma.sqlite3` + HNSW binaries (157 MB) | HF Dataset |
| `rag/extracted/*.json` (285 files, 1.6 MB) | HF Dataset (also kept locally for git history) |
| Code (`backend/`, `frontend/`, `rag/*.py`, `eval/`, `kb/`, `data/`) | HF Space git repo |

## Alternatives considered

| Option | Why rejected |
|---|---|
| Upgrade HF Pro ($9/mo, 50 GB Space repos) | "No funding" constraint. |
| `git lfs migrate import` on existing history | Would rewrite all 90+ historical commits; required installing `git-lfs` binary (no Homebrew on the dev machine at decision time). |
| Strip corpus + Chroma from Space repo; rebuild on every cold start | Chroma rebuild from 208 PDFs is ~25 min cold boot → painful for every demo reviewer. |
| Object store (S3, GCS) | Adds AWS/GCP credential dependency the take-home wasn't supposed to need. |

## Why HF Dataset is the right answer

- **Quota-isolated** from Spaces — datasets get their own 50 GB free quota.
- **Public dataset → no token at Docker build time** — `snapshot_download` runs without secrets, simplifying the build environment.
- **Data-is-the-moat framing** — the dataset can iterate independently of the code (re-extraction syncs just update the dataset; no Space rebuild needed unless code changes).
- **Reproducibility** — a reviewer can clone the dataset and run the bot locally against the exact same corpus + vectors used in the demo.
- **$0 cost** — free tier datasets are 50 GB; current usage ~493 MB is 1% of quota.

## Implementation details

Dockerfile snippet (live):

```dockerfile
RUN python -c "from huggingface_hub import snapshot_download; \
  snapshot_download( \
    repo_id='rohitsar567/insurance-bot-data', \
    repo_type='dataset', \
    local_dir='/app/rag', \
    allow_patterns=['rag/corpus/**','rag/vectors/**','rag/extracted/**'])"
```

`.gitignore` (relevant lines):

```
rag/corpus/
rag/vectors/
rag/extracted/
```

Sync tools:

- `tools/upload_extracted_to_dataset.py` — push regenerated extractions.
- `tools/upload_vectors_to_dataset.py` — push rebuilt Chroma.
- `tools/upload_corpus_to_dataset.py` — push new/updated PDFs.

## Consequences

**Positive:**

- Space repo stays code-only (~3 MB) → fast clone, fast Docker layer cache.
- Dataset and Space are versioned independently — small code change doesn't trigger a 500 MB data re-upload.
- Local backup of the dataset lives in `rag/_hf_dataset_backup/` (gitignored, 493 MB) — see ADR-024.

**Negative:**

- Dataset becomes unavailable during Docker build → Space build fails.

**Mitigations:**

- Dataset is on HF's CDN, same uptime SLA as the Space. If HF is down, neither would work anyway.
- Local backup at `rag/_hf_dataset_backup/` can be re-uploaded if HF Dataset is ever corrupted.

## Revisit at scale (v2)

- Move to a private dataset + token-gated build if the corpus contains material we don't want public-archive-indexed (currently all PDFs are public).
- Add a `DATASET_VERSION` env var to pin Space builds to a specific dataset commit (currently always latest).
