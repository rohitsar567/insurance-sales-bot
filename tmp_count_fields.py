#!/usr/bin/env python3
"""Validate batch 4 JSONs and count populated fields."""
import json
import os

BASE = "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot/rag/extracted"
BATCH = [
    "hdfc-ergo__total-health-plan__wordings",
    "icici-lombard__arogya-sanjeevani__wordings",
    "icici-lombard__complete-health-insurance-health-shield__wordings",
    "icici-lombard__complete-health-insurance-umbrella__wordings",
    "icici-lombard__elevate__wordings",
    "icici-lombard__health-advantedge__wordings",
    "icici-lombard__health-booster-top-up__wordings",
    "icici-lombard__health-elite-plus__wordings",
    "icici-lombard__health-shield-360-retail__cis",
    "icici-lombard__health-shield-360-retail__wordings",
    "iffco-tokio__critical-illness-benefit__wordings",
    "iffco-tokio__essential-health-plan__wordings",
    "iffco-tokio__family-health-protector__wordings",
    "iffco-tokio__health-protector-assure__wordings",
    "iffco-tokio__health-protector-plus__wordings",
    "iffco-tokio__individual-health-protector__wordings",
    "manipalcigna__prohealth-insurance-all-variants__wordings",
    "manipalcigna__prohealth-select__wordings",
    "manipalcigna__sarvah-param__brochure",
]


def is_populated(v):
    if v is None:
        return False
    if isinstance(v, (list, dict)) and len(v) == 0:
        return False
    if isinstance(v, dict):
        # CoverageItem: populated if any subfield set
        return any(is_populated(x) for x in v.values())
    if isinstance(v, str) and v.strip() == "":
        return False
    return True


total = 0
ok = 0
field_counts = []
for pid in BATCH:
    path = os.path.join(BASE, pid + ".json")
    if not os.path.exists(path):
        print(f"MISSING {pid}")
        continue
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        print(f"ERR {pid} {e}")
        continue
    ok += 1
    n_pop = sum(1 for v in data.values() if is_populated(v))
    field_counts.append((pid, n_pop, len(data)))

print(f"OK: {ok}/{len(BATCH)}")
print(f"Average populated fields: {sum(c[1] for c in field_counts)/len(field_counts):.1f}")
print()
for pid, n_pop, total_f in field_counts:
    print(f"  {pid}: {n_pop}/{total_f}")
