#!/bin/bash
# Wait for reextract_all.py (PID $REEXTRACT_PID) -> sync dataset -> restart Space
set -uo pipefail
cd "$(dirname "$0")/.."
LOG="logs/reextract_pipeline_$(date +%Y%m%d_%H%M%S).log"
exec > "$LOG" 2>&1

echo "=== DEPLOY PIPELINE START $(date) ==="

REEXTRACT_PID="${1:-95249}"
echo "[1/4] waiting for re-extraction (PID $REEXTRACT_PID)..."
while ps -p "$REEXTRACT_PID" > /dev/null 2>&1; do sleep 30; done
echo "[1/4] re-extraction done. Valid JSONs: $(ls rag/extracted/*.json 2>/dev/null | grep -v _raw | grep -v .old | wc -l | tr -d ' ')"

echo "[2/4] syncing rag/extracted to HF dataset..."
.venv/bin/python tools/upload_extracted_to_dataset.py

echo "[3/4] restarting HF Space (factory reboot to pull updated dataset)..."
.venv/bin/python -c "
import os
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
api.restart_space('rohitsar567/InsuranceBot', factory_reboot=True)
print('factory_reboot triggered')
"

echo "[4/4] waiting for Space to reach RUNNING..."
.venv/bin/python -c "
import os, time
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
deadline = time.time() + 20*60
prev = None
while time.time() < deadline:
    rt = api.get_space_runtime('rohitsar567/InsuranceBot')
    if rt.stage != prev:
        print(f'  [{time.strftime(\"%H:%M:%S\")}] stage={rt.stage}')
        prev = rt.stage
    if rt.stage == 'RUNNING':
        print(f'  Space LIVE')
        break
    if rt.stage in ('RUNTIME_ERROR','BUILD_ERROR','CONFIG_ERROR'):
        print(f'  Space failed: {rt.stage}')
        break
    time.sleep(30)
"

echo ""
echo "=== DEPLOY PIPELINE DONE $(date) ==="
echo "Valid JSONs: $(ls rag/extracted/*.json 2>/dev/null | grep -v _raw | grep -v .old | wc -l | tr -d ' ')"
