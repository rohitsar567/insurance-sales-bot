"""Extract text from policy PDFs (batch 2) into the same text cache."""
import os, sys, json
import pdfplumber

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = "/tmp/claude/policy_extract/text_cache"
os.makedirs(CACHE, exist_ok=True)

# Batch 2: PDFs not yet covered, filtered by retail/standalone relevance
BATCH2 = [
    ("aditya-birla", "rag/corpus/aditya-birla/activ-health-individual__wordings.pdf"),
    ("bajaj-allianz", "rag/corpus/bajaj-allianz/comprehensive-care-plan__wordings.pdf"),
    ("bajaj-allianz", "rag/corpus/bajaj-allianz/global-health-care__wordings.pdf"),
    ("bajaj-allianz", "rag/corpus/bajaj-allianz/health-guard__wordings.pdf"),
    ("bajaj-allianz", "rag/corpus/bajaj-allianz/silver-health__cis.pdf"),
    ("bajaj-allianz", "rag/corpus/bajaj-allianz/tax-gain__cis.pdf"),
    ("care-health", "rag/corpus/care-health/care-advantage__brochure.pdf"),
    ("care-health", "rag/corpus/care-health/care-supreme-enhance__wordings.pdf"),
    ("care-health", "rag/corpus/care-health/ultimate-care__wordings.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/energy-diabetes-hypertension__wordings.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/my-health-medisure-prime__wordings.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/my-health-sampoorna-suraksha__brochure.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/my-health-suraksha__brochure.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/my-health-women-suraksha__brochure.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/my-optima-secure-older-variant__wordings.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/optima-enhance__wordings.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/optima-plus__wordings.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/total-health-plan__wordings.pdf"),
    ("icici-lombard", "rag/corpus/icici-lombard/arogya-sanjeevani__wordings.pdf"),
    ("icici-lombard", "rag/corpus/icici-lombard/complete-health-insurance-umbrella__wordings.pdf"),
    ("icici-lombard", "rag/corpus/icici-lombard/health-advantedge__wordings.pdf"),
    ("icici-lombard", "rag/corpus/icici-lombard/health-booster-top-up__wordings.pdf"),
    ("icici-lombard", "rag/corpus/icici-lombard/health-elite-plus__wordings.pdf"),
    ("manipalcigna", "rag/corpus/manipalcigna/prohealth-select__wordings.pdf"),
    ("manipalcigna", "rag/corpus/manipalcigna/sarvah-param__wordings.pdf"),
    ("new-india", "rag/corpus/new-india/asha-kiran-policy__brochure.pdf"),
    ("new-india", "rag/corpus/new-india/janata-mediclaim-policy__wordings.pdf"),
    ("new-india", "rag/corpus/new-india/new-india-mediclaim-policy__wordings.pdf"),
    ("new-india", "rag/corpus/new-india/universal-health-insurance__wordings.pdf"),
    ("new-india", "rag/corpus/new-india/yuva-bharat-health-policy__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/aspire__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/health-plus-top-up__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/health-premia__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/reassure-3-0__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/rise__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/saral-suraksha-bima__wordings.pdf"),
    ("star-health", "rag/corpus/star-health/health-premier__wordings.pdf"),
    ("star-health", "rag/corpus/star-health/senior-citizens-red-carpet__brochure.pdf"),
    ("star-health", "rag/corpus/star-health/star-assure__wordings.pdf"),
    ("star-health", "rag/corpus/star-health/star-cardiac-care-platinum__wordings.pdf"),
    ("star-health", "rag/corpus/star-health/star-cardiac-care__wordings.pdf"),
    ("tata-aig", "rag/corpus/tata-aig/medicare-lite__cis.pdf"),
    ("tata-aig", "rag/corpus/tata-aig/medicare-select__brochure.pdf"),
]

for insurer, rel in BATCH2:
    src = os.path.join(BASE, rel)
    name = os.path.basename(rel).replace(".pdf", ".txt")
    dst = os.path.join(CACHE, f"{insurer}__{name}")
    if os.path.exists(dst):
        print(f"skip {os.path.basename(dst)}")
        continue
    if not os.path.exists(src):
        print(f"MISSING {src}")
        continue
    try:
        with pdfplumber.open(src) as pdf:
            # Extract up to 30 pages worth (most policy details in first ~20)
            pages = pdf.pages[:30]
            text = "\n".join((p.extract_text() or "") for p in pages)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"OK {os.path.basename(dst)} ({len(text)} chars, {len(pages)} pages)")
    except Exception as e:
        print(f"ERR {src}: {e}")
print("Done.")
