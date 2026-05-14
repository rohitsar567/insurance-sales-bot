"""Validate batch 5 JSON extractions."""
import json
import os

BATCH = "/tmp/extract_batch_5.txt"
ROOT = "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot/rag/extracted"

with open(BATCH) as f:
    pdfs = [line.strip() for line in f if line.strip()]

ok = 0
total_fields = 0
fails = []
for rel in pdfs:
    base = os.path.basename(rel).replace(".pdf", "")
    insurer = rel.split("/")[2]
    pid = f"{insurer}__{base}"
    fp = os.path.join(ROOT, f"{pid}.json")
    if not os.path.exists(fp):
        fails.append((pid, "MISSING"))
        continue
    try:
        with open(fp) as fh:
            d = json.load(fh)
        populated = sum(1 for v in d.values() if v not in (None, "", [], {}))
        total_fields += populated
        ok += 1
        print(f"OK {pid}: {populated} populated, conf={d.get('extraction_confidence_pct')}")
    except Exception as e:
        fails.append((pid, str(e)))

print(f"\nTOTAL: {ok}/{len(pdfs)}, avg populated = {total_fields / max(ok, 1):.1f}")
print("Failures:", fails)
