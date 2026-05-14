#!/usr/bin/env python3
"""Validate the 19 batch-3 JSONs against the HealthPolicy schema."""
import json
import os
import sys

sys.path.insert(0, "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot")
from rag.schema import HealthPolicy

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
ok = 0
fail = []
for f in files:
    path = os.path.join(base, f)
    with open(path) as fh:
        data = json.load(fh)
    try:
        HealthPolicy(**data)
        ok += 1
    except Exception as e:
        fail.append((f, str(e)[:300]))
print(f"OK {ok}/{len(files)}")
for f, err in fail:
    print(f"FAIL {f}: {err}")
