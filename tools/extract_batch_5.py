"""Extract text from all 19 PDFs in batch 5."""
import os
import sys

import pdfplumber

BATCH = "/tmp/extract_batch_5.txt"
ROOT = "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot"

with open(BATCH) as f:
    pdfs = [line.strip() for line in f if line.strip()]

for rel in pdfs:
    abs_path = os.path.join(ROOT, rel)
    base = os.path.basename(rel).replace(".pdf", "")
    out = f"/tmp/batch5_{base}.txt"
    if os.path.exists(out):
        print(f"skip {base}")
        continue
    try:
        with pdfplumber.open(abs_path) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:30])
        text = text[:25000]
        with open(out, "w") as o:
            o.write(text)
        print(f"OK {base}: {len(text)} chars")
    except Exception as e:
        print(f"FAIL {base}: {e}")
