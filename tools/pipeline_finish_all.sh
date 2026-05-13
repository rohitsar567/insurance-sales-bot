#!/bin/bash
# End-to-end finish for the Stack A migration.
#  1. Wait for redo_extract (PID 86578) to complete
#  2. Upload rag/extracted JSONs to dataset
#  3. Run Stage 1 chunk-size sweep
#  4. Run Stage 2 retrieval sweep
#  5. Final upload to dataset + Space push
# Logs everything to logs/pipeline_*.log so I can read state on resume.

set -uo pipefail
cd "$(dirname "$0")/.."
LOG="logs/pipeline_$(date +%Y%m%d_%H%M%S).log"
exec > "$LOG" 2>&1

echo "=== PIPELINE START $(date) ==="

# ---- 1. Wait for redo extraction ----
EXTRACT_PID=86578
echo "[1/5] waiting for extract_failed.py (PID $EXTRACT_PID) ..."
while ps -p $EXTRACT_PID > /dev/null 2>&1; do sleep 30; done
echo "[1/5] extraction done. Valid JSONs: $(ls rag/extracted/*.json 2>/dev/null | grep -v _raw | wc -l | tr -d ' ')"

# ---- 2. Upload extracted to dataset ----
echo "[2/5] uploading rag/extracted to HF dataset..."
.venv/bin/python tools/upload_extracted_to_dataset.py

# ---- 3. Stage 1 chunk-size sweep ----
echo "[3/5] running Stage 1 chunk-size sweep on NIM judge..."
.venv/bin/python tools/chunk_sweep.py
echo "[3/5] chunk sweep complete."

# ---- 4. Stage 2 retrieval sweep ----
echo "[4/5] running Stage 2 retrieval sweep (top_k × MIN_TOP_SCORE)..."
.venv/bin/python tools/sweep_retrieval.py
echo "[4/5] retrieval sweep complete."

# ---- 5. Re-sync vectors (in case chunk sweep produced a different winner) + final dataset upload ----
echo "[5/5] re-uploading rag/vectors to dataset (winning chunk config)..."
.venv/bin/python -c "
import os
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
api.upload_folder(
    folder_path='rag/vectors',
    path_in_repo='rag/vectors',
    repo_id='rohitsar567/insurance-bot-data',
    repo_type='dataset',
    commit_message='sync rag/vectors (post-sweep, winning chunk config)',
)
print('vectors synced.')
"

echo ""
echo "=== PIPELINE DONE $(date) ==="
echo "Final state:"
echo "  Valid JSONs: $(ls rag/extracted/*.json 2>/dev/null | grep -v _raw | wc -l | tr -d ' ')"
echo "  eval/results.md headline:"
head -20 eval/results.md 2>/dev/null || echo "(no results.md)"
echo ""
echo "  Stage 1 winner:"
test -f eval/chunk_sweep_results.json && head -50 eval/chunk_sweep_results.json
echo ""
echo "  Stage 2 winner:"
test -f eval/retrieval_sweep_results.json && head -50 eval/retrieval_sweep_results.json
