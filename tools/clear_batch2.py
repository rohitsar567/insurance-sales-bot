"""Remove only batch-2 policy_facts JSONs (preserve batch-1)."""
import os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "40-data/policy_facts")

BATCH1 = {
    "aditya-birla__activ-assure-diamond",
    "aditya-birla__activ-one",
    "bajaj-allianz__extra-care-plus",
    "bajaj-allianz__health-guard-gold",
    "care-health__care-classic",
    "care-health__care-senior",
    "care-health__care-supreme",
    "hdfc-ergo__optima-restore",
    "hdfc-ergo__optima-secure",
    "icici-lombard__complete-health-insurance",
    "icici-lombard__elevate",
    "icici-lombard__health-shield-360",
    "manipalcigna__prohealth-prime",
    "manipalcigna__prohealth-protect",
    "new-india__floater-mediclaim",
    "niva-bupa__health-companion",
    "niva-bupa__reassure-2",
    "niva-bupa__senior-first",
    "star-health__family-health-optima",
    "star-health__star-comprehensive",
    "tata-aig__medicare-premier",
    "tata-aig__medicare",
}

removed = 0
for fn in os.listdir(OUT_DIR):
    if not fn.endswith(".json"):
        continue
    stem = fn[:-5]
    if stem not in BATCH1:
        os.remove(os.path.join(OUT_DIR, fn))
        removed += 1
        print(f"removed {fn}")
print(f"Removed {removed} batch-2 JSONs")
