"""One-command sync: extracted + corpus + vectors -> insurance-bot-data dataset.

Runs the three companion uploaders sequentially. Use this after a full local
pipeline run on the developer Mac (extract -> corpus refresh -> rag.ingest).

  .venv/bin/python tools/upload_all_to_dataset.py

Each step exits non-zero on failure and short-circuits the rest, so a partial
sync never silently happens. The Space's Dockerfile snapshot_downloads the
whole dataset at build time, so all three folders need to be in sync before
running `tools/upload_to_hf.py`.
"""
from __future__ import annotations
import sys

from tools import (
    upload_extracted_to_dataset,
    upload_corpus_to_dataset,
    upload_vectors_to_dataset,
)


STEPS = [
    ("extracted JSONs", upload_extracted_to_dataset.main),
    ("corpus PDFs",     upload_corpus_to_dataset.main),
    ("Chroma vectors",  upload_vectors_to_dataset.main),
]


def main() -> int:
    n = len(STEPS)
    for i, (label, fn) in enumerate(STEPS, start=1):
        print(f"\n=== [{i}/{n}] {label} ===")
        rc = fn() or 0
        if rc != 0:
            print(f"\nABORT: step {i}/{n} ({label}) returned exit {rc}. "
                  "Fix and re-run; later steps were NOT executed.")
            return rc
    print(f"\nAll {n} dataset folders synced. Next: `tools/upload_to_hf.py` to deploy the Space.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
