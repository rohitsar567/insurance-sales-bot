"""Validate against HealthPolicy Pydantic schema."""
import json
import os
import sys

sys.path.insert(0, "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot")

from rag.schema import HealthPolicy

BATCH = "/tmp/extract_batch_5.txt"
ROOT = "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot/rag/extracted"

with open(BATCH) as f:
    pdfs = [line.strip() for line in f if line.strip()]

errors = []
for rel in pdfs:
    base = os.path.basename(rel).replace(".pdf", "")
    insurer = rel.split("/")[2]
    pid = f"{insurer}__{base}"
    fp = os.path.join(ROOT, f"{pid}.json")
    try:
        with open(fp) as fh:
            d = json.load(fh)
        HealthPolicy(**d)
        print(f"OK {pid}")
    except Exception as e:
        errors.append((pid, str(e)[:200]))
        print(f"FAIL {pid}: {e!s:.200}")

print(f"\nTotal errors: {len(errors)}/{len(pdfs)}")
