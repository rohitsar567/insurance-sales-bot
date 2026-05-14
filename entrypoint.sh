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

# Use HF Spaces persistent disk if mounted at /data, else fall back to /app
if [ -d "/data" ] && [ -w "/data" ]; then
    export VECTOR_DIR="/data/vectors"
    export DUCKDB_PATH="/data/policies.duckdb"
    # Symlink so the app code (which reads from rag/vectors and
    # rag/policies.duckdb) finds them on the persistent disk
    mkdir -p /data/vectors
    rm -rf /app/rag/vectors
    ln -sf /data/vectors /app/rag/vectors
    if [ ! -f /data/policies.duckdb ] && [ -f /app/rag/policies.duckdb ]; then
        cp /app/rag/policies.duckdb /data/policies.duckdb
    fi
    rm -f /app/rag/policies.duckdb
    ln -sf /data/policies.duckdb /app/rag/policies.duckdb
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
