#!/bin/bash
# Wait for extraction PID to finish, then sync + restart Space.
set -uo pipefail
cd "$(dirname "$0")/.."
EXTRACT_PID="${1:-6056}"
LOG="logs/post_extract_deploy_$(date +%Y%m%d_%H%M%S).log"
exec > "$LOG" 2>&1

echo "=== POST-EXTRACT DEPLOY START $(date) ==="

echo "[1/5] waiting for extraction PID $EXTRACT_PID..."
while ps -p "$EXTRACT_PID" > /dev/null 2>&1; do sleep 30; done
echo "[1/5] extraction done. Valid JSONs: $(ls rag/extracted/*.json 2>/dev/null | grep -v _raw | grep -v .old | grep -v _backup | wc -l | tr -d ' ')"

echo "[2/5] sync rag/extracted + rag/corpus to dataset..."
.venv/bin/python tools/upload_extracted_to_dataset.py
.venv/bin/python -c "
import os
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
api.upload_folder(folder_path='rag/corpus', path_in_repo='rag/corpus',
    repo_id='rohitsar567/insurance-bot-data', repo_type='dataset',
    commit_message='Corpus expansion final: 19 insurers, 190 PDFs',
    ignore_patterns=['_playwright_results.md','*.tmp','.DS_Store'])
print('corpus synced')
"

echo "[3/5] re-ingest Chroma to include new PDFs..."
.venv/bin/python -m rag.ingest 2>&1 | tail -30

echo "[4/5] sync rag/vectors to dataset..."
.venv/bin/python -c "
import os
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
api.upload_folder(folder_path='rag/vectors', path_in_repo='rag/vectors',
    repo_id='rohitsar567/insurance-bot-data', repo_type='dataset',
    commit_message='Vectors re-ingested over expanded 190-PDF corpus')
print('vectors synced')
"

echo "[5/5] factory-restart HF Space..."
.venv/bin/python -c "
import os, time
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
api.restart_space('rohitsar567/InsuranceBot', factory_reboot=True)
print('factory_reboot triggered')
"

echo ""
echo "=== POST-EXTRACT DEPLOY DONE $(date) ==="
echo "Valid JSONs: $(ls rag/extracted/*.json 2>/dev/null | grep -v _raw | grep -v .old | grep -v _backup | wc -l | tr -d ' ')"
