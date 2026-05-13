"""Extract text from policy PDFs to a text cache for curating policy_facts JSON."""
import os, sys
import pdfplumber

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = "/tmp/claude/policy_extract/text_cache"
os.makedirs(CACHE, exist_ok=True)

PDFS = [
    ("aditya-birla", "rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf"),
    ("aditya-birla", "rag/corpus/aditya-birla/activ-one__brochure.pdf"),
    ("bajaj-allianz", "rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf"),
    ("bajaj-allianz", "rag/corpus/bajaj-allianz/extra-care-plus__wordings.pdf"),
    ("care-health", "rag/corpus/care-health/care-supreme__wordings.pdf"),
    ("care-health", "rag/corpus/care-health/care-classic__wordings.pdf"),
    ("care-health", "rag/corpus/care-health/care-senior__brochure.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/my-optima-secure__wordings.pdf"),
    ("hdfc-ergo", "rag/corpus/hdfc-ergo/optima-restore__brochure.pdf"),
    ("icici-lombard", "rag/corpus/icici-lombard/elevate__wordings.pdf"),
    ("icici-lombard", "rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf"),
    ("icici-lombard", "rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf"),
    ("manipalcigna", "rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf"),
    ("manipalcigna", "rag/corpus/manipalcigna/prohealth-select__wordings.pdf"),
    ("new-india", "rag/corpus/new-india/new-india-floater-mediclaim-policy__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/reassure-2-0__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/senior-first__wordings.pdf"),
    ("niva-bupa", "rag/corpus/niva-bupa/health-companion__wordings.pdf"),
    ("star-health", "rag/corpus/star-health/family-health-optima__wordings.pdf"),
    ("star-health", "rag/corpus/star-health/star-comprehensive__wordings.pdf"),
    ("tata-aig", "rag/corpus/tata-aig/medicare-premier__wordings.pdf"),
    ("tata-aig", "rag/corpus/tata-aig/medicare__wordings.pdf"),
]

for insurer, rel in PDFS:
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
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"OK {os.path.basename(dst)} ({len(text)} chars)")
    except Exception as e:
        print(f"ERR {src}: {e}")
print("Done.")
