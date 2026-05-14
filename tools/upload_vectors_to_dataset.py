"""Sync rag/vectors/ (pre-built Chroma index) to insurance-bot-data HF dataset.

Run AFTER `.venv/bin/python -m rag.ingest` on the developer Mac. The Space's
Dockerfile pulls rag/vectors/** at build time so the deployed container has a
ready-to-serve index. As of 2026-05-14 the Space no longer auto-ingests on
boot — see entrypoint.sh.
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
    vectors_dir = ROOT / "rag" / "vectors"
    if not vectors_dir.exists() or not any(vectors_dir.iterdir()):
        print("ERROR: rag/vectors/ is empty. Run `.venv/bin/python -m rag.ingest` first.")
        return 1
    # Print size + file count before upload
    total = 0
    nfiles = 0
    for p in vectors_dir.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            nfiles += 1
    print(f"Syncing rag/vectors/ ({total/1024/1024:.1f} MB, {nfiles} files) to insurance-bot-data ...")
    api.upload_folder(
        folder_path=str(vectors_dir),
        path_in_repo="rag/vectors",
        repo_id="rohitsar567/insurance-bot-data",
        repo_type="dataset",
        commit_message="Sync rag/vectors/ — pre-built Chroma index (Mac local ingest)",
        ignore_patterns=["*.tmp", "**/.DS_Store"],
    )
    print("Done. Now run `tools/upload_to_hf.py` to deploy Space.")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
