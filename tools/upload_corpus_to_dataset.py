"""Sync rag/corpus/ (raw policy PDFs) to insurance-bot-data HF dataset.

Run after any corpus refresh (new PDFs added or refreshed via
rag/download_corpus.py). The Space's Dockerfile pulls rag/corpus/** at build
time so the deployed container has the source PDFs available for citation +
ingestion pipelines.
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def main():
    api = HfApi(token=os.environ["HF_TOKEN"])
    corpus_dir = ROOT / "rag" / "corpus"
    if not corpus_dir.exists() or not any(corpus_dir.iterdir()):
        print("ERROR: rag/corpus/ is empty. Run `rag/download_corpus.py` first.")
        return 1
    # Print size + file count before upload
    total = 0
    nfiles = 0
    for p in corpus_dir.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            nfiles += 1
    print(f"Syncing rag/corpus/ ({total/1024/1024:.1f} MB, {nfiles} files) to insurance-bot-data ...")
    api.upload_folder(
        folder_path=str(corpus_dir),
        path_in_repo="rag/corpus",
        repo_id="rohitsar567/insurance-bot-data",
        repo_type="dataset",
        commit_message="Sync rag/corpus/ — raw policy PDFs (Mac local refresh)",
        ignore_patterns=["**/.DS_Store", "*.tmp"],
    )
    print("Done.")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
