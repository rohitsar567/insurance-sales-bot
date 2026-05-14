#!/bin/sh
# Container entrypoint (2026-05-14 policy update):
#   1. Validate Chroma is readable + populated.
#   2. If empty/broken: FAIL FAST with a loud error — DO NOT auto-ingest.
#      Ingestion runs on the developer's local Mac (faster CPU, visible
#      progress). The deployed Space serves pre-built indexes only.
#      The single exception is the user_uploads_quarantine collection,
#      which /api/upload-policy embeds on-demand per uploading session.
#   3. Start uvicorn.
#
# Why: previously the Space silently re-ingested for 20+ min during APP_STARTING
# (output piped through `tail -30` so nothing visible), making schema breakage
# look identical to "still booting". Fail-fast surfaces ingest gaps immediately.

set -e

# KI-119 (2026-05-15) — DISABLE persistent-disk symlink for vectors.
#
# Pre-fix, this block unconditionally rm -rf'd the /app/rag/vectors
# directory (freshly snapshot_downloaded from the HF dataset at build
# time) and replaced it with a symlink to /data/vectors. On second+
# builds, /data/vectors held STALE Chroma from a prior run — including
# the corrupted profile_anonymous row from the KI-102 deploy that broke
# every collection.query() call. The dataset upload couldn't help because
# the entrypoint's symlink overrode it.
#
# We don't need persistent vectors. The whole point of pushing rag/vectors
# to the companion HF Dataset is that EVERY Space rebuild pulls a fresh
# copy. /data persistence is now disabled for vectors; the app reads
# directly from /app/rag/vectors which contains the just-downloaded fresh
# snapshot. DuckDB persistence kept (it's only used for cached metadata
# and benefits from cross-rebuild persistence with no corruption surface).
#
# If you NEED to test with a clean /data, manually `rm -rf /data/vectors`
# on the Space's persistent disk via Settings → Reset.
if [ -d "/data" ] && [ -w "/data" ]; then
    export DUCKDB_PATH="/data/policies.duckdb"
    if [ ! -f /data/policies.duckdb ] && [ -f /app/rag/policies.duckdb ]; then
        cp /app/rag/policies.duckdb /data/policies.duckdb
    fi
    rm -f /app/rag/policies.duckdb
    ln -sf /data/policies.duckdb /app/rag/policies.duckdb
    # Vectors stay at /app/rag/vectors — read from the fresh dataset
    # snapshot. The previous /data/vectors symlink is intentionally removed.
    if [ -L "/app/rag/vectors" ]; then
        # Pre-existing symlink from older deploys — unlink it so /app reads
        # the fresh snapshot_download'd directory underneath.
        rm /app/rag/vectors 2>/dev/null || true
    fi
fi

# Validate Chroma is readable + populated; rebuild if not.
echo "[entrypoint] validating Chroma vector store..."
python -c "
import sys
sys.path.insert(0, '/app')
try:
    from rag.retrieve import get_collection
    c = get_collection()
    n = c.count()
    if n <= 0:
        print(f'[entrypoint] Chroma is empty')
        sys.exit(1)
    # Smoke test: do an actual retrieval to surface any deserialization bug
    res = c.get(limit=1, include=['metadatas'])
    if not res.get('ids'):
        print(f'[entrypoint] Chroma reports {n} chunks but get() returns empty')
        sys.exit(1)
    print(f'[entrypoint] Chroma OK: {n} chunks, sample policy: {res[\"metadatas\"][0].get(\"policy_id\")}')
    sys.exit(0)
except Exception as e:
    print(f'[entrypoint] Chroma load FAILED: {type(e).__name__}: {e}')
    sys.exit(1)
" || (
    echo "[entrypoint] ============================================================"
    echo "[entrypoint] FATAL: Chroma vector store is empty or schema-incompatible."
    echo "[entrypoint] Auto-ingest is DISABLED (2026-05-14 policy)."
    echo "[entrypoint]"
    echo "[entrypoint] Fix on the developer Mac:"
    echo "[entrypoint]   .venv/bin/python -m rag.ingest"
    echo "[entrypoint]   .venv/bin/python tools/upload_extracted_to_dataset.py"
    echo "[entrypoint]   # plus sync rag/vectors/ to the dataset, then redeploy"
    echo "[entrypoint] ============================================================"
    exit 1
)

# Start the server
echo "[entrypoint] starting uvicorn on port ${PORT:-7860}..."
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-7860}" --log-level info
