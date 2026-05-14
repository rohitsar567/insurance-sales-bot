"""Sync rag/extracted/*.json into the insurance-bot-data HF dataset.

Run after every extraction batch so the HF Space rebuild sees the latest
structured policy facts at Docker build time.
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
    print("Syncing rag/extracted to dataset rohitsar567/insurance-bot-data ...")
    api.upload_folder(
        folder_path=str(ROOT / "rag" / "extracted"),
        path_in_repo="rag/extracted",
        repo_id="rohitsar567/insurance-bot-data",
        repo_type="dataset",
        commit_message="sync rag/extracted JSONs (post-NIM-extraction)",
        ignore_patterns=["*._raw.txt"],
    )
    n = len(list((ROOT / "rag" / "extracted").glob("*.json")))
    print(f"  Synced {n} JSONs.")

if __name__ == "__main__":
    main()
