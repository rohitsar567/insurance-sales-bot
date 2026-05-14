#!/usr/bin/env python3
"""Count non-null populated top-level fields in the 19 batch-3 JSON files."""
import json
import os

files = [
    "cholamandalam__flexi-health__wordings.json",
    "cholamandalam__super-topup__wordings.json",
    "go-digit__arogya-sanjeevani__wordings.json",
    "go-digit__digit-complete-care__wordings.json",
    "go-digit__digit-health-care-plus__wordings.json",
    "go-digit__digit-health-insurance__wordings.json",
    "go-digit__digit-supreme-care__wordings.json",
    "go-digit__digit-top-up__wordings.json",
    "hdfc-ergo__energy-diabetes-hypertension__wordings.json",
    "hdfc-ergo__group-health-insurance__wordings.json",
    "hdfc-ergo__my-health-medisure-prime__wordings.json",
    "hdfc-ergo__my-health-sampoorna-suraksha__brochure.json",
    "hdfc-ergo__my-health-suraksha__brochure.json",
    "hdfc-ergo__my-health-women-suraksha__brochure.json",
    "hdfc-ergo__my-optima-secure-older-variant__wordings.json",
    "hdfc-ergo__my-optima-secure__wordings.json",
    "hdfc-ergo__optima-enhance__wordings.json",
    "hdfc-ergo__optima-plus__wordings.json",
    "hdfc-ergo__optima-restore__brochure.json",
]
base = "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot/rag/extracted"
total = 0
for f in files:
    path = os.path.join(base, f)
    with open(path) as fh:
        data = json.load(fh)
    count = sum(1 for v in data.values() if v not in (None, "", [], {}))
    total += count
    print(f"{f}: {count}")
print(f"average: {total/len(files):.1f}")
print(f"total files: {len(files)}")
