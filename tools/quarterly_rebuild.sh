#!/usr/bin/env bash
# Quarterly full rebuild — manual trigger only.
#
# Re-downloads every policy PDF, re-ingests the entire corpus, rebuilds the
# knowledge base index, and runs the gold eval suite. Should be run before
# every demo / submission / release.
#
# Usage:  bash tools/quarterly_rebuild.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/Library/Logs/insurance-bot"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/quarterly_rebuild.log"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cd "$PROJECT_ROOT"

echo "=== quarterly rebuild start $TS ===" | tee -a "$LOG"

PY="${PY:-python3}"

step() {
  local label="$1"; shift
  echo ">>> $label" | tee -a "$LOG"
  if "$@" 2>&1 | tee -a "$LOG"; then
    echo "    [ok] $label" | tee -a "$LOG"
  else
    echo "    [FAIL] $label — aborting" | tee -a "$LOG"
    osascript -e "display notification \"Quarterly rebuild failed at: $label\" with title \"Insurance Bot\""
    exit 1
  fi
}

step "link-rot pass"          "$PY" tools/check_link_rot.py || true
step "PDF freshness pass"     "$PY" tools/check_pdf_etags.py || true
step "Premium anchor refresh" "$PY" tools/refresh_premiums.py || true

# Full corpus re-ingest — wipe Chroma first so every PDF is re-processed
step "Wipe Chroma vectors"    bash -c 'rm -rf data/vectors/* 2>/dev/null || true'
step "Re-ingest full corpus"  "$PY" -m rag.ingest

# Re-extract structured schema for any newly added policies
step "Re-extract policy facts" "$PY" -m rag.extract

# Rebuild KB pages (policies / research / calculations / reviews / premiums / methodology)
step "Rebuild KB pages"        "$PY" -m rag.build_kb

# Run the gold eval suite (live URLs verified + faithfulness suite)
step "Gold eval"               "$PY" tests/live_verify.py || true

echo "=== quarterly rebuild done $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" | tee -a "$LOG"
osascript -e 'display notification "Quarterly rebuild complete" with title "Insurance Bot"'
