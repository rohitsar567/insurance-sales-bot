#!/bin/bash
# Full post-extraction pipeline:
#   1. wait for extraction PID
#   2. run info_source_map.py (URL/content audit)
#   3. generate 40-data/policy_facts/*.json from rag/extracted/*.json
#   4. build_kb_mirror.py (regenerate kb/policies/*.md)
#   5. sync rag/extracted + 40-data/policy_facts to dataset
#   6. re-ingest Chroma over the full 190-PDF corpus
#   7. sync rag/vectors to dataset
#   8. factory-restart HF Space + wait for RUNNING
set -uo pipefail
cd "$(dirname "$0")/.."
EXTRACT_PID="${1:-8392}"
LOG="logs/full_pipeline_$(date +%Y%m%d_%H%M%S).log"
exec > "$LOG" 2>&1

echo "=== FULL PIPELINE START $(date) ==="
echo "extract pid: $EXTRACT_PID"

echo ""
echo "[1/8] waiting for extraction ..."
while ps -p "$EXTRACT_PID" > /dev/null 2>&1; do sleep 30; done
JSON_COUNT=$(ls rag/extracted/*.json 2>/dev/null | grep -v _raw | grep -v .old | grep -v _backup | wc -l | tr -d ' ')
echo "[1/8] done. valid JSONs: $JSON_COUNT"

echo ""
echo "[2/8] info source map (URL+content audit on every claim) ..."
.venv/bin/python tools/info_source_map.py 2>&1 | tail -20

echo ""
echo "[3/8] generate marketplace cards from extractions ..."
.venv/bin/python tools/generate_policy_facts.py

echo ""
echo "[4/8] build kb mirror (regenerate kb/policies/*.md + INDEX) ..."
.venv/bin/python tools/build_kb_mirror.py 2>&1 | tail -20

echo ""
echo "[5/8] sync extracted + policy_facts to dataset ..."
.venv/bin/python -c "
import os
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
for folder, path_in_repo in [('rag/extracted','rag/extracted'),('40-data/policy_facts','40-data/policy_facts'),('rag/corpus','rag/corpus')]:
    api.upload_folder(folder_path=folder, path_in_repo=path_in_repo,
        repo_id='rohitsar567/insurance-bot-data', repo_type='dataset',
        commit_message=f'sync {folder} post-extraction (190 PDFs, 19 insurers)',
        ignore_patterns=['*._raw.txt','*.old','*_backup_old_prompt/**','_playwright_results.md','*.tmp'])
    print(f'  synced {folder}')
"

echo ""
echo "[6/8] re-ingest Chroma over 190-PDF corpus ..."
.venv/bin/python -m rag.ingest 2>&1 | tail -10

echo ""
echo "[7/8] sync rag/vectors to dataset ..."
.venv/bin/python -c "
import os
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
api.upload_folder(folder_path='rag/vectors', path_in_repo='rag/vectors',
    repo_id='rohitsar567/insurance-bot-data', repo_type='dataset',
    commit_message='vectors re-ingested over 190-PDF corpus, 19 insurers')
print('  vectors synced')
"

echo ""
echo "[8/8] HF Space factory_reboot ..."
.venv/bin/python -c "
import os, time
from dotenv import load_dotenv
from huggingface_hub import HfApi
load_dotenv('.env')
api = HfApi(token=os.environ['HF_TOKEN'])
api.restart_space('rohitsar567/InsuranceBot', factory_reboot=True)
print('  factory_reboot fired')
time.sleep(15)
rt = api.get_space_runtime('rohitsar567/InsuranceBot')
print(f'  stage: {rt.stage}')
# Wait for RUNNING
import time
deadline = time.time() + 25*60
prev = None
while time.time() < deadline:
    rt = api.get_space_runtime('rohitsar567/InsuranceBot')
    if rt.stage != prev:
        print(f'  [{time.strftime(\"%H:%M:%S\")}] stage={rt.stage}')
        prev = rt.stage
    if rt.stage == 'RUNNING':
        print('  Space LIVE'); break
    if rt.stage in ('RUNTIME_ERROR','BUILD_ERROR','CONFIG_ERROR'):
        print(f'  Space failed: {rt.stage}'); break
    time.sleep(30)
"

echo ""
echo "=== FULL PIPELINE DONE $(date) ==="
