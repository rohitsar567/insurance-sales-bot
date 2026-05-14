#!/usr/bin/env python3
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
ok = 0
fail = []
for f in files:
    try:
        with open(os.path.join(base, f)) as fh:
            data = json.load(fh)
        assert "policy_id" in data and "insurer_name" in data and "insurer_slug" in data and "policy_name" in data
        ok += 1
    except Exception as e:
        fail.append((f, str(e)))
print(f"json OK {ok}/{len(files)}")
for f, e in fail:
    print(f"FAIL {f}: {e}")
