#!/bin/sh
# Container entrypoint:
#   1. If the Chroma vector store is empty, run ingestion from rag/corpus/
#      (one-time ~5 min on first boot; cached on HF persistent disk for
#      subsequent boots)
#   2. Start uvicorn

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

# Ingest if Chroma collection is empty
echo "[entrypoint] checking if Chroma needs to be built..."
python -c "
import sys
sys.path.insert(0, '/app')
from rag.retrieve import get_collection
c = get_collection()
n = c.count()
print(f'[entrypoint] Chroma chunks: {n}')
sys.exit(0 if n > 0 else 1)
" && echo "[entrypoint] Chroma already populated — skipping ingest" || (
    echo "[entrypoint] Chroma empty — running ingest (~5 min one-time)..."
    python -m rag.ingest 2>&1 | tail -20
)

# Start the server
echo "[entrypoint] starting uvicorn on port ${PORT:-7860}..."
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-7860}" --log-level info
