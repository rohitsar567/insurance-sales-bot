"""Curate batch 2 policy_facts JSONs from extracted text cache.

Pattern-based field extraction matched to the schema used by batch 1.
Writes one JSON per policy into data/policy_facts/.
"""
import os
import re
import json
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = "/tmp/claude/policy_extract/text_cache"
OUT_DIR = os.path.join(BASE, "data/policy_facts")
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Batch 2 manifest: (policy_id, policy_name, insurer_slug, primary_pdf_rel,
#                   text_cache_filename, [supporting_pdf_rel ... optional])
# Excluded from batch (already curated):
# aditya-birla activ-assure-diamond + activ-one
# bajaj-allianz health-guard-gold + extra-care-plus
# care-health care-supreme + care-classic + care-senior
# hdfc-ergo my-optima-secure + optima-restore
# icici-lombard elevate + health-shield-360 + complete-health
# manipalcigna prohealth-prime + prohealth-protect (both from all-variants)
# new-india new-india-floater-mediclaim
# niva-bupa reassure-2 + senior-first + health-companion
# star-health family-health-optima + star-comprehensive
# tata-aig medicare + medicare-premier
# ---------------------------------------------------------------------------
MANIFEST = [
    # ABHI
    ("aditya-birla__activ-health", "Aditya Birla Activ Health (Platinum Enhanced / Essential)", "aditya-birla",
        "rag/corpus/aditya-birla/activ-health-individual__wordings.pdf",
        "aditya-birla__activ-health-individual__wordings.txt", []),
    # Bajaj
    ("bajaj-allianz__comprehensive-care-plan", "Bajaj Allianz Comprehensive Care Plan", "bajaj-allianz",
        "rag/corpus/bajaj-allianz/comprehensive-care-plan__wordings.pdf",
        "bajaj-allianz__comprehensive-care-plan__wordings.txt", []),
    ("bajaj-allianz__global-health-care", "Bajaj Allianz Global Health Care", "bajaj-allianz",
        "rag/corpus/bajaj-allianz/global-health-care__wordings.pdf",
        "bajaj-allianz__global-health-care__wordings.txt", []),
    ("bajaj-allianz__health-guard", "Bajaj Allianz Health Guard (Silver / Gold / Platinum)", "bajaj-allianz",
        "rag/corpus/bajaj-allianz/health-guard__wordings.pdf",
        "bajaj-allianz__health-guard__wordings.txt", []),
    ("bajaj-allianz__silver-health", "Bajaj Allianz Silver Health (Senior Citizen)", "bajaj-allianz",
        "rag/corpus/bajaj-allianz/silver-health__cis.pdf",
        "bajaj-allianz__silver-health__cis.txt", []),
    ("bajaj-allianz__tax-gain", "Bajaj Allianz Tax Gain", "bajaj-allianz",
        "rag/corpus/bajaj-allianz/tax-gain__cis.pdf",
        "bajaj-allianz__tax-gain__cis.txt", []),
    # Care
    ("care-health__care-advantage", "Care Health Care Advantage", "care-health",
        "rag/corpus/care-health/care-advantage__brochure.pdf",
        "care-health__care-advantage__brochure.txt", []),
    ("care-health__care-supreme-enhance", "Care Health Care Supreme Enhance (Top-up)", "care-health",
        "rag/corpus/care-health/care-supreme-enhance__wordings.pdf",
        "care-health__care-supreme-enhance__wordings.txt", []),
    ("care-health__ultimate-care", "Care Health Ultimate Care", "care-health",
        "rag/corpus/care-health/ultimate-care__wordings.pdf",
        "care-health__ultimate-care__wordings.txt", []),
    # HDFC ERGO
    ("hdfc-ergo__energy", "HDFC ERGO Energy (Diabetes / Hypertension)", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/energy-diabetes-hypertension__wordings.pdf",
        "hdfc-ergo__energy-diabetes-hypertension__wordings.txt", []),
    ("hdfc-ergo__my-health-medisure-prime", "HDFC ERGO my:health Medisure Prime", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/my-health-medisure-prime__wordings.pdf",
        "hdfc-ergo__my-health-medisure-prime__wordings.txt", []),
    ("hdfc-ergo__my-health-sampoorna-suraksha", "HDFC ERGO my:health Sampoorna Suraksha", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/my-health-sampoorna-suraksha__brochure.pdf",
        "hdfc-ergo__my-health-sampoorna-suraksha__brochure.txt", []),
    ("hdfc-ergo__my-health-suraksha", "HDFC ERGO my:health Suraksha", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/my-health-suraksha__brochure.pdf",
        "hdfc-ergo__my-health-suraksha__brochure.txt", []),
    ("hdfc-ergo__my-health-women-suraksha", "HDFC ERGO my:health Women Suraksha", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/my-health-women-suraksha__brochure.pdf",
        "hdfc-ergo__my-health-women-suraksha__brochure.txt", []),
    ("hdfc-ergo__optima-secure-older-variant", "HDFC ERGO Optima Secure (Older / Legacy Variant)", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/my-optima-secure-older-variant__wordings.pdf",
        "hdfc-ergo__my-optima-secure-older-variant__wordings.txt", []),
    ("hdfc-ergo__optima-enhance", "HDFC ERGO Optima Enhance (Top-up)", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/optima-enhance__wordings.pdf",
        "hdfc-ergo__optima-enhance__wordings.txt", []),
    ("hdfc-ergo__optima-plus", "HDFC ERGO Optima Plus", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/optima-plus__wordings.pdf",
        "hdfc-ergo__optima-plus__wordings.txt", []),
    ("hdfc-ergo__total-health-plan", "HDFC ERGO Total Health Plan", "hdfc-ergo",
        "rag/corpus/hdfc-ergo/total-health-plan__wordings.pdf",
        "hdfc-ergo__total-health-plan__wordings.txt", []),
    # ICICI Lombard
    ("icici-lombard__arogya-sanjeevani", "ICICI Lombard Arogya Sanjeevani (Standard)", "icici-lombard",
        "rag/corpus/icici-lombard/arogya-sanjeevani__wordings.pdf",
        "icici-lombard__arogya-sanjeevani__wordings.txt", []),
    ("icici-lombard__complete-health-umbrella", "ICICI Lombard Complete Health Insurance — Umbrella", "icici-lombard",
        "rag/corpus/icici-lombard/complete-health-insurance-umbrella__wordings.pdf",
        "icici-lombard__complete-health-insurance-umbrella__wordings.txt", []),
    ("icici-lombard__health-advantedge", "ICICI Lombard Health Advantedge", "icici-lombard",
        "rag/corpus/icici-lombard/health-advantedge__wordings.pdf",
        "icici-lombard__health-advantedge__wordings.txt", []),
    ("icici-lombard__health-booster", "ICICI Lombard Health Booster (Top-up)", "icici-lombard",
        "rag/corpus/icici-lombard/health-booster-top-up__wordings.pdf",
        "icici-lombard__health-booster-top-up__wordings.txt", []),
    ("icici-lombard__health-elite-plus", "ICICI Lombard Health Elite Plus", "icici-lombard",
        "rag/corpus/icici-lombard/health-elite-plus__wordings.pdf",
        "icici-lombard__health-elite-plus__wordings.txt", []),
    # ManipalCigna
    ("manipalcigna__prohealth-select", "ManipalCigna ProHealth Select", "manipalcigna",
        "rag/corpus/manipalcigna/prohealth-select__wordings.pdf",
        "manipalcigna__prohealth-select__wordings.txt", []),
    ("manipalcigna__sarvah-param", "ManipalCigna Sarvah Param", "manipalcigna",
        "rag/corpus/manipalcigna/sarvah-param__wordings.pdf",
        "manipalcigna__sarvah-param__wordings.txt", []),
    # New India
    ("new-india__asha-kiran", "New India Asha Kiran (Girl Child Family Floater)", "new-india",
        "rag/corpus/new-india/asha-kiran-policy__brochure.pdf",
        "new-india__asha-kiran-policy__brochure.txt", []),
    ("new-india__janata-mediclaim", "New India Janata Mediclaim", "new-india",
        "rag/corpus/new-india/janata-mediclaim-policy__wordings.pdf",
        "new-india__janata-mediclaim-policy__wordings.txt", []),
    ("new-india__mediclaim-policy", "New India Mediclaim Policy (Individual)", "new-india",
        "rag/corpus/new-india/new-india-mediclaim-policy__wordings.pdf",
        "new-india__new-india-mediclaim-policy__wordings.txt", []),
    ("new-india__universal-health", "New India Universal Health Insurance", "new-india",
        "rag/corpus/new-india/universal-health-insurance__wordings.pdf",
        "new-india__universal-health-insurance__wordings.txt", []),
    ("new-india__yuva-bharat", "New India Yuva Bharat Health Policy", "new-india",
        "rag/corpus/new-india/yuva-bharat-health-policy__wordings.pdf",
        "new-india__yuva-bharat-health-policy__wordings.txt", []),
    # Niva Bupa
    ("niva-bupa__aspire", "Niva Bupa Aspire", "niva-bupa",
        "rag/corpus/niva-bupa/aspire__wordings.pdf",
        "niva-bupa__aspire__wordings.txt", []),
    ("niva-bupa__health-plus-top-up", "Niva Bupa Health Plus (Top-up)", "niva-bupa",
        "rag/corpus/niva-bupa/health-plus-top-up__wordings.pdf",
        "niva-bupa__health-plus-top-up__wordings.txt", []),
    ("niva-bupa__health-premia", "Niva Bupa Health Premia", "niva-bupa",
        "rag/corpus/niva-bupa/health-premia__wordings.pdf",
        "niva-bupa__health-premia__wordings.txt", []),
    ("niva-bupa__reassure-3", "Niva Bupa ReAssure 3.0", "niva-bupa",
        "rag/corpus/niva-bupa/reassure-3-0__wordings.pdf",
        "niva-bupa__reassure-3-0__wordings.txt", []),
    ("niva-bupa__rise", "Niva Bupa Rise", "niva-bupa",
        "rag/corpus/niva-bupa/rise__wordings.pdf",
        "niva-bupa__rise__wordings.txt", []),
    ("niva-bupa__saral-suraksha", "Niva Bupa Saral Suraksha Bima (Standard)", "niva-bupa",
        "rag/corpus/niva-bupa/saral-suraksha-bima__wordings.pdf",
        "niva-bupa__saral-suraksha-bima__wordings.txt", []),
    # Star
    ("star-health__health-premier", "Star Health Premier", "star-health",
        "rag/corpus/star-health/health-premier__wordings.pdf",
        "star-health__health-premier__wordings.txt", []),
    ("star-health__senior-citizens-red-carpet", "Star Senior Citizens Red Carpet", "star-health",
        "rag/corpus/star-health/senior-citizens-red-carpet__brochure.pdf",
        "star-health__senior-citizens-red-carpet__brochure.txt", []),
    ("star-health__star-assure", "Star Assure Insurance Policy", "star-health",
        "rag/corpus/star-health/star-assure__wordings.pdf",
        "star-health__star-assure__wordings.txt", []),
    ("star-health__star-cardiac-care", "Star Cardiac Care Insurance", "star-health",
        "rag/corpus/star-health/star-cardiac-care__wordings.pdf",
        "star-health__star-cardiac-care__wordings.txt", []),
    ("star-health__star-cardiac-care-platinum", "Star Cardiac Care Platinum", "star-health",
        "rag/corpus/star-health/star-cardiac-care-platinum__wordings.pdf",
        "star-health__star-cardiac-care-platinum__wordings.txt", []),
    # Tata AIG
    ("tata-aig__medicare-lite", "Tata AIG MediCare Lite", "tata-aig",
        "rag/corpus/tata-aig/medicare-lite__cis.pdf",
        "tata-aig__medicare-lite__cis.txt", []),
    ("tata-aig__medicare-select", "Tata AIG MediCare Select", "tata-aig",
        "rag/corpus/tata-aig/medicare-select__brochure.pdf",
        "tata-aig__medicare-select__brochure.txt", []),
]

# ---------------------------------------------------------------------------
# Pattern-based field extractors
# Each returns (value, quote) or (None, quote_with_explanation) on miss
# ---------------------------------------------------------------------------

def find_context(text, pattern, max_len=200, flags=re.IGNORECASE):
    m = re.search(pattern, text, flags)
    if not m:
        return None, None
    start = max(0, m.start() - 30)
    end = min(len(text), m.end() + 160)
    ctx = re.sub(r"\s+", " ", text[start:end]).strip()
    return m, ctx[:max_len]

def extract_uin(text):
    # IRDAI UIN: 3-letter insurer + 3-5 letter product code + 5 digits + V + 6 digits
    # Examples: HDFHLIP25041V062425 (HDF + HLIP), SHAHLIP22032V052122 (SHA + HLIP),
    # CHIHLIP23128V012223 (CHI + HLIP), NBHHLIP26042V022526 (NBH + HLIP)
    pat = r"\b([A-Z]{6,9}[0-9]{5}V[0-9]{6})\b"
    m, ctx = find_context(text, pat)
    if m:
        return m.group(1), ctx
    return None, "UIN not found in extracted text"

def extract_min_entry_age(text):
    # Look for "minimum entry age" / "min age" / "91 days"
    pats = [
        (r"[Mm]inimum [Ee]ntry [Aa]ge[^.\n]{0,80}?(\d+)\s*(day|year|month)", "explicit min"),
        (r"[Aa]ge at [Ee]ntry[^.\n]{0,40}?(\d+)\s*(day|year|month)", "age at entry"),
        (r"[Cc]hild[^.\n]{0,40}?(\d+)\s*day", "child entry"),
        (r"(\d+)\s*[Dd]ays\s*(?:to|-|–)\s*\d+\s*[Yy]ears", "range form"),
    ]
    for pat, _ in pats:
        m, ctx = find_context(text, pat)
        if m:
            val = int(m.group(1))
            unit = m.group(2).lower() if m.lastindex and m.lastindex >= 2 else "days"
            return val, unit, ctx
    return None, None, "Min entry age not found"

def extract_max_entry_age(text):
    pats = [
        (r"[Mm]aximum [Ee]ntry [Aa]ge[^.\n]{0,80}?(\d+)\s*[Yy]ear", "explicit max"),
        (r"[Ee]ntry [Aa]ge[^.\n]{0,40}?[Uu]p to (\d+)\s*[Yy]ear", "entry age up to"),
        (r"(\d+)\s*[Dd]ays\s*(?:to|-|–)\s*(\d+)\s*[Yy]ears", "range"),
        (r"[Mm]aximum [Aa]ge[^.\n]{0,40}?(\d+)\s*[Yy]ear", "max age"),
    ]
    for pat, _ in pats:
        m, ctx = find_context(text, pat)
        if m:
            # Range pattern -> group 2 is max
            try:
                val = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2).isdigit() else int(m.group(1))
            except Exception:
                val = int(m.group(1))
            return val, "years", ctx
    return None, "years", "Max entry age not explicitly stated; check Policy Schedule"

def extract_renewal_age(text):
    if re.search(r"[Ll]ifelong\s*[Rr]enew|[Ll]ife[- ]?[Ll]ong|[Nn]o\s+maximum\s+(cover\s+)?ceas|continuous\s+life\s+long", text):
        m, ctx = find_context(text, r"[Ll]ifelong\s*[Rr]enew|[Ll]ife[- ]?[Ll]ong\s*[Rr]enew|[Nn]o\s+maximum\s+(cover\s+)?ceas|continuous\s+life\s+long")
        return None, "Lifelong renewability" + ((": " + ctx) if ctx else "")
    m, ctx = find_context(text, r"[Mm]aximum\s+[Rr]enewal\s+[Aa]ge[^.\n]{0,40}?(\d+)\s*[Yy]ear")
    if m:
        return int(m.group(1)), ctx
    return None, "Max renewal age not specified; check Policy Schedule"

def extract_sum_insured_options(text):
    # Look for currency lists e.g. "3 Lacs, 5 Lacs, 10 Lacs"
    m, ctx = find_context(text, r"[Ss]um\s+[Ii]nsured[^.\n]{0,300}?(\d+[\d,. ]{0,20}(?:Lakhs?|Lacs?|Crores?|L\b|Cr\b))")
    if m:
        # Try to gather numeric values from window
        window = text[max(0, m.start()-30): m.end()+400]
        nums = re.findall(r"(\d+(?:\.\d+)?)\s*(?:Lakhs?|Lacs?|L\b)", window, re.IGNORECASE)
        nums_cr = re.findall(r"(\d+(?:\.\d+)?)\s*(?:Crores?|Cr\b)", window, re.IGNORECASE)
        vals = []
        for n in nums:
            try:
                v = int(float(n) * 100000)
                if 50000 <= v <= 1000000000:
                    vals.append(v)
            except Exception:
                pass
        for n in nums_cr:
            try:
                v = int(float(n) * 10000000)
                if 50000 <= v <= 1000000000:
                    vals.append(v)
            except Exception:
                pass
        vals = sorted(set(vals))
        # Require at least 2 distinct values to count this as a real enumeration
        if len(vals) >= 2:
            return vals, ctx[:200]
    return None, "Sum Insured options not enumerated in extracted text; check Policy Schedule"

def extract_initial_waiting(text):
    m, ctx = find_context(text, r"(\d+)\s*[Dd]ays?\s+(?:from\s+the\s+(?:first|date of)|waiting period|of\s+the\s+inception)")
    if m and int(m.group(1)) in (15, 30):
        return int(m.group(1)), ctx
    m, ctx = find_context(text, r"[Ee]xcl03[^.\n]{0,200}?(\d+)\s*days?")
    if m:
        return int(m.group(1)), ctx
    m, ctx = find_context(text, r"within\s+(\d+)\s*days\s+from\s+the\s+first")
    if m:
        return int(m.group(1)), ctx
    return 30, "Default IRDAI 30-day waiting period applies (not explicitly quoted in extracted snippet)"

def extract_ped_waiting(text):
    # PED in months
    m, ctx = find_context(text, r"[Pp]re[- ]existing\s+[Dd]isease\s*(?:\([^)]+\))?[^.\n]{0,300}?(\d+)\s*(months|years)")
    if m:
        val = int(m.group(1))
        unit = m.group(2).lower()
        months = val * 12 if "year" in unit else val
        return months, ctx
    m, ctx = find_context(text, r"PED[^.\n]{0,200}?(\d+)\s*(months|years)")
    if m:
        val = int(m.group(1))
        unit = m.group(2).lower()
        months = val * 12 if "year" in unit else val
        return months, ctx
    m, ctx = find_context(text, r"[Ee]xcl01[^.\n]{0,200}?(\d+)\s*months")
    if m:
        return int(m.group(1)), ctx
    return None, "PED waiting period not extracted; check Section 5 / Excl01"

def extract_specific_disease_waiting(text):
    m, ctx = find_context(text, r"(?:listed|specified|named|specific)\s+(?:conditions?|ailments?|diseases?|treatments?)[^.\n]{0,300}?(\d+)\s*(months|years)")
    if m:
        val = int(m.group(1))
        unit = m.group(2).lower()
        months = val * 12 if "year" in unit else val
        return months, ctx
    m, ctx = find_context(text, r"[Ee]xcl02[^.\n]{0,200}?(\d+)\s*(months|years)")
    if m:
        val = int(m.group(1))
        unit = m.group(2).lower()
        return val * 12 if "year" in unit else val, ctx
    return 24, "Default IRDAI 24-month specific-disease waiting (not explicitly quoted)"

def extract_maternity_waiting(text):
    m, ctx = find_context(text, r"[Mm]aternity[^.\n]{0,200}?(\d+)\s*months?\s+(?:waiting|of continuous)")
    if m:
        return int(m.group(1)), ctx
    m, ctx = find_context(text, r"[Ww]aiting\s+[Pp]eriod[^.\n]{0,50}?[Mm]aternity[^.\n]{0,80}?(\d+)\s*months?")
    if m:
        return int(m.group(1)), ctx
    return None, "Maternity waiting not specified or maternity excluded"

def extract_pre_hosp_days(text):
    m, ctx = find_context(text, r"[Pp]re[- ]?[Hh]ospitalisation[^.\n]{0,200}?(\d+)\s*days?")
    if m:
        return int(m.group(1)), ctx
    m, ctx = find_context(text, r"(\d+)\s*days?\s+(?:prior to|before).{0,40}(?:admission|hospitali[sz]ation)")
    if m:
        return int(m.group(1)), ctx
    return None, "Pre-hospitalization days not extracted"

def extract_post_hosp_days(text):
    m, ctx = find_context(text, r"[Pp]ost[- ]?[Hh]ospitalisation[^.\n]{0,200}?(\d+)\s*days?")
    if m:
        return int(m.group(1)), ctx
    m, ctx = find_context(text, r"(\d+)\s*days?\s+(?:after|post|following).{0,40}discharge")
    if m:
        return int(m.group(1)), ctx
    return None, "Post-hospitalization days not extracted"

def extract_day_care_count(text):
    m, ctx = find_context(text, r"(\d{2,4})\s*(?:listed\s+)?[Dd]ay\s*[- ]?[Cc]are\s*(?:[Pp]rocedures?|[Tt]reatments?)")
    if m:
        v = int(m.group(1))
        if 50 <= v <= 2000:
            return v, ctx
    m, ctx = find_context(text, r"[Dd]ay\s*[- ]?[Cc]are[^.\n]{0,80}?(\d{2,4})\s*[Pp]rocedures?")
    if m:
        v = int(m.group(1))
        if 50 <= v <= 2000:
            return v, ctx
    return None, "Day-care count not enumerated; covered per policy definition"

def extract_ayush(text):
    if re.search(r"AYUSH", text):
        m, ctx = find_context(text, r"AYUSH[^.\n]{0,200}")
        return True, ctx
    if re.search(r"[Aa]lternative\s+[Tt]reatment", text):
        m, ctx = find_context(text, r"[Aa]lternative\s+[Tt]reatment[^.\n]{0,200}")
        return True, ctx
    return False, "AYUSH coverage not found in extracted text"

def extract_maternity(text):
    # Check explicit "maternity not covered" or "Excl18"
    m1 = re.search(r"[Mm]aternity[^.\n]{0,80}?(?:not\s+covered|excluded)", text)
    m2 = re.search(r"Excl18", text)
    m3 = re.search(r"[Mm]aternity\s+(?:[Ee]xpenses?|[Cc]over|[Bb]enefit)[^.\n]{0,300}?(?:lump\s+sum|Rs\.?\s*\d|INR|deliveries?)", text)
    if m3 and not m1:
        # Has positive maternity description
        m, ctx = find_context(text, r"[Mm]aternity\s+(?:[Ee]xpenses?|[Cc]over|[Bb]enefit)[^.\n]{0,300}")
        return True, ctx
    if m1 or m2:
        if m1:
            m, ctx = find_context(text, r"[Mm]aternity[^.\n]{0,200}?(?:not\s+covered|excluded)[^.\n]{0,100}")
        else:
            m, ctx = find_context(text, r"Excl18[^.\n]{0,200}")
        return False, ctx or "Maternity excluded (Excl18)"
    # No explicit mention -> default false for typical retail (most retail base excludes maternity)
    return False, "Maternity not explicitly mentioned; presumed excluded in base"

def extract_newborn(text):
    m = re.search(r"[Nn]ew[ -]?[Bb]orn[^.\n]{0,200}", text)
    if m:
        ctx = re.sub(r"\s+", " ", text[m.start():m.end()+50]).strip()
        if re.search(r"not\s+covered|excluded", ctx):
            return False, ctx[:200]
        return True, ctx[:200]
    return False, "Newborn cover not found; typically tied to maternity option"

def extract_organ_donor(text):
    m = re.search(r"[Oo]rgan\s+[Dd]onor", text)
    if m:
        ctx = re.sub(r"\s+", " ", text[m.start():m.end()+200]).strip()
        if re.search(r"not\s+covered|excluded", ctx):
            return False, ctx[:200]
        return True, ctx[:200]
    return False, "Organ donor cover not extracted"

def extract_ncb(text):
    m = re.search(r"(?:[Nn]o\s+[Cc]laim\s+[Bb]onus|[Cc]umulative\s+[Bb]onus|NCB|cumulative\s+bonus)[^.\n]{0,400}?(\d{1,3})\s*%", text)
    if m:
        v = int(m.group(1))
        if 5 <= v <= 100:
            ctx = re.sub(r"\s+", " ", text[max(0, m.start()):m.end()+50]).strip()[:220]
            return v, ctx
    m = re.search(r"(\d{1,3})\s*%\s+(?:increase|bonus)\s+(?:in|of)\s+(?:Sum\s+Insured|SI)", text, re.IGNORECASE)
    if m:
        v = int(m.group(1))
        if 5 <= v <= 100:
            ctx = re.sub(r"\s+", " ", text[max(0, m.start()-40):m.end()+30]).strip()[:220]
            return v, ctx
    return None, "NCB % not extracted; product may use booster/recharge structure"

def extract_restoration(text):
    # Patterns
    pats = [
        r"[Rr]estor[ae][^.\n]{0,300}",
        r"[Rr]echarge\s+of\s+[Ss]um\s+[Ii]nsured[^.\n]{0,300}",
        r"[Rr]efill[^.\n]{0,300}",
        r"[Rr]eset\s+[Bb]enefit[^.\n]{0,300}",
        r"[Rr]e[- ]?[Ii]nstatement[^.\n]{0,300}",
    ]
    for p in pats:
        m = re.search(p, text)
        if m:
            ctx = re.sub(r"\s+", " ", text[m.start():m.end()]).strip()[:280]
            return ctx[:240], ctx
    return None, "Restoration benefit not found in extracted text"

def extract_room_rent(text):
    # Prefer wording that's a capping description, not the room-rent definition
    pats = [
        r"[Nn]o\s+[Rr]oom\s+[Rr]ent\s+(?:[Cc]apping|[Ll]imit|[Ss]ub[- ]?[Ll]imit)[^.\n]{0,150}",
        r"[Rr]oom\s+[Rr]ent\s+No\s+Sub[- ]?Limit[^.\n]{0,100}",
        r"[Ss]ingle\s+[Pp]rivate\s+(?:AC\s+)?[Rr]oom[^.\n]{0,150}",
        r"[Rr]oom\s+[Rr]ent[^.\n]{0,200}?(?:up\s+to|maximum|capped\s+at|limit\s+of|sub[- ]?limit)\s*(?:Rs\.?|`|INR|\d+\s*%)[^.\n]{0,100}",
        r"[Rr]oom\s+[Cc]ategory[^.\n]{0,160}",
        r"[Rr]oom\s+[Rr]ent[^.\n]{0,160}?(\d+\s*%|Rs\.?\s*\d|`\s*\d|INR\s*\d)[^.\n]{0,80}",
    ]
    for p in pats:
        m = re.search(p, text)
        if m:
            ctx = re.sub(r"\s+", " ", text[m.start():m.end()]).strip()[:240]
            return ctx[:200], ctx
    return None, "Room rent capping not extracted (only definition found, no explicit cap)"

def extract_copayment(text):
    m = re.search(r"[Cc]o[- ]?payment\s+of\s+(\d{1,2})\s*%", text)
    if m:
        v = int(m.group(1))
        ctx = re.sub(r"\s+", " ", text[max(0, m.start()-30):m.end()+120]).strip()[:240]
        return v, ctx
    m = re.search(r"(\d{1,2})\s*%\s+[Cc]o[- ]?[Pp]ay", text)
    if m:
        v = int(m.group(1))
        ctx = re.sub(r"\s+", " ", text[max(0, m.start()-30):m.end()+120]).strip()[:240]
        return v, ctx
    return 0, "No mandatory copay extracted; product may have age-based or zone-based optional copay"

def extract_deductible(text):
    m = re.search(r"[Dd]eductible[^.\n]{0,300}?(?:Rs\.?\s*|INR\s*|₹\s*)(\d[\d,]{2,})", text)
    if m:
        amt = int(m.group(1).replace(",", ""))
        ctx = re.sub(r"\s+", " ", text[max(0, m.start()-30):m.end()+120]).strip()[:240]
        return amt, ctx
    m = re.search(r"[Aa]ggregate\s+[Dd]eductible[^.\n]{0,200}", text)
    if m:
        ctx = re.sub(r"\s+", " ", text[m.start():m.end()]).strip()[:220]
        return None, ctx
    return None, "No base deductible (or only optional voluntary deductible add-on)"

def extract_cashless(text):
    if re.search(r"[Cc]ashless", text):
        m, ctx = find_context(text, r"[Cc]ashless[^.\n]{0,200}")
        return True, ctx
    return None, "Cashless mention not found"

def extract_policy_type(text, policy_id=""):
    pid = policy_id.lower()
    # ID-based classification first (most reliable)
    if "top-up" in pid or "supreme-enhance" in pid or "health-booster" in pid or "optima-enhance" in pid or "health-plus-top-up" in pid or "extra-care-plus" in pid:
        return "top-up", "Top-up / super top-up policy (per product name)"
    if "cardiac-care" in pid or "cancer-care" in pid or "criti-medicare" in pid or "criti-care" in pid:
        return "benefit", "Specialty cardiac/critical illness — benefit-based lump-sum on diagnosis"
    if "hospital-cash" in pid or "daily-cash" in pid:
        return "hospital-cash", "Hospital cash / daily benefit policy"
    # Heuristic on text
    if re.search(r"[Ss]uper\s+[Tt]op[- ]?up|deductible[^.\n]{0,100}aggregate|[Aa]ggregate\s+[Dd]eductible[^.\n]{0,200}[Ss]um\s+[Ii]nsured", text):
        return "top-up", "Top-up / super top-up policy (kicks in above a deductible)"
    if re.search(r"[Hh]ospital\s+[Cc]ash|[Dd]aily\s+[Cc]ash\s+[Bb]enefit", text):
        return "hospital-cash", "Hospital cash / daily benefit policy"
    # Indemnity is the default for retail health
    if re.search(r"[Ii]ndemnity|[Hh]ospitali[sz]ation\s+[Ee]xpenses?\s+[Ii]ndemnif|[Ii]ndemnif", text):
        m, ctx = find_context(text, r"[Ii]ndemnity|[Ii]ndemnif")
        return "indemnity", ctx or "Indemnity-based health insurance"
    return "indemnity", "Default indemnity (no explicit alternate type detected)"

# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def curate_one(entry):
    policy_id, policy_name, insurer, primary_pdf, txt_name, supporting = entry
    txt_path = os.path.join(CACHE, txt_name)
    if not os.path.exists(txt_path):
        return None, f"text cache missing: {txt_name}"
    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read()
    if len(text) < 1000:
        return None, f"too short ({len(text)} chars)"

    uin_val, uin_quote = extract_uin(text)
    min_age, min_unit, min_q = extract_min_entry_age(text)
    max_age, max_unit, max_q = extract_max_entry_age(text)
    renewal_age, renewal_q = extract_renewal_age(text)
    si_vals, si_q = extract_sum_insured_options(text)
    init_wait, init_q = extract_initial_waiting(text)
    ped_m, ped_q = extract_ped_waiting(text)
    sd_m, sd_q = extract_specific_disease_waiting(text)
    mat_m, mat_q = extract_maternity_waiting(text)
    pre_d, pre_q = extract_pre_hosp_days(text)
    post_d, post_q = extract_post_hosp_days(text)
    dc_n, dc_q = extract_day_care_count(text)
    ayush_b, ayush_q = extract_ayush(text)
    mat_b, mat_bq = extract_maternity(text)
    nb_b, nb_q = extract_newborn(text)
    od_b, od_q = extract_organ_donor(text)
    ncb_v, ncb_q = extract_ncb(text)
    restore_v, restore_q = extract_restoration(text)
    rr_v, rr_q = extract_room_rent(text)
    copay_v, copay_q = extract_copayment(text)
    ded_v, ded_q = extract_deductible(text)
    cash_v, cash_q = extract_cashless(text)
    ptype_v, ptype_q = extract_policy_type(text, policy_id)

    # Compose JSON
    j = {
        "policy_id": policy_id,
        "policy_name": policy_name,
        "insurer_slug": insurer,
        "uin_code": {
            "value": uin_val,
            "source_pdf_path": primary_pdf,
            "source_quote": (uin_quote or "")[:240]
        },
        "min_entry_age": {
            "value": min_age,
            "unit": min_unit or "days",
            "source_pdf_path": primary_pdf,
            "source_quote": (min_q or "Not explicitly stated; per Policy Schedule")[:240]
        },
        "max_entry_age": {
            "value": max_age,
            "unit": max_unit or "years",
            "source_pdf_path": primary_pdf,
            "source_quote": (max_q or "Not explicitly stated; per Policy Schedule")[:240]
        },
        "max_renewal_age": {
            "value": renewal_age,
            "source_pdf_path": primary_pdf,
            "source_quote": (renewal_q or "Not specified")[:240]
        },
        "sum_insured_options": {
            "value": si_vals,
            "unit": "INR",
            "source_pdf_path": primary_pdf,
            "source_quote": (si_q or "Per Policy Schedule")[:240]
        },
        "initial_waiting_period_days": {
            "value": init_wait,
            "source_pdf_path": primary_pdf,
            "source_quote": (init_q or "Per IRDAI standard")[:240]
        },
        "pre_existing_disease_waiting_months": {
            "value": ped_m,
            "source_pdf_path": primary_pdf,
            "source_quote": (ped_q or "PED waiting per policy wording")[:240]
        },
        "specific_disease_waiting_months": {
            "value": sd_m,
            "source_pdf_path": primary_pdf,
            "source_quote": (sd_q or "Specific disease waiting per IRDAI standard")[:240]
        },
        "maternity_waiting_months": {
            "value": mat_m,
            "source_pdf_path": primary_pdf,
            "source_quote": (mat_q or "Maternity waiting only applies if maternity covered/opted")[:240]
        },
        "pre_hospitalization_days": {
            "value": pre_d,
            "source_pdf_path": primary_pdf,
            "source_quote": (pre_q or "Pre-hosp days per Policy Schedule")[:240]
        },
        "post_hospitalization_days": {
            "value": post_d,
            "source_pdf_path": primary_pdf,
            "source_quote": (post_q or "Post-hosp days per Policy Schedule")[:240]
        },
        "day_care_treatments_count": {
            "value": dc_n,
            "source_pdf_path": primary_pdf,
            "source_quote": (dc_q or "Day care covered per definition; count not enumerated")[:240]
        },
        "ayush_coverage": {
            "value": ayush_b,
            "source_pdf_path": primary_pdf,
            "source_quote": (ayush_q or "AYUSH cover not explicitly found")[:240]
        },
        "maternity_coverage": {
            "value": mat_b,
            "source_pdf_path": primary_pdf,
            "source_quote": (mat_bq or "Maternity status not explicitly extracted")[:240]
        },
        "newborn_coverage": {
            "value": nb_b,
            "source_pdf_path": primary_pdf,
            "source_quote": (nb_q or "Newborn cover status not extracted")[:240]
        },
        "organ_donor_expenses": {
            "value": od_b,
            "source_pdf_path": primary_pdf,
            "source_quote": (od_q or "Organ donor benefit not extracted")[:240]
        },
        "no_claim_bonus_pct": {
            "value": ncb_v,
            "source_pdf_path": primary_pdf,
            "source_quote": (ncb_q or "NCB % not extracted")[:240]
        },
        "restoration_benefit": {
            "value": restore_v,
            "source_pdf_path": primary_pdf,
            "source_quote": (restore_q or "Restoration not found in extracted text")[:240]
        },
        "room_rent_capping": {
            "value": rr_v,
            "source_pdf_path": primary_pdf,
            "source_quote": (rr_q or "Room rent capping not extracted")[:240]
        },
        "copayment_pct": {
            "value": copay_v,
            "source_pdf_path": primary_pdf,
            "source_quote": (copay_q or "No mandatory copay")[:240]
        },
        "deductible_amount": {
            "value": ded_v,
            "source_pdf_path": primary_pdf,
            "source_quote": (ded_q or "No base deductible")[:240]
        },
        "network_hospital_count": {
            "value": None,
            "source_url": None,
            "source_quote": "Insurer-level metric; not extracted in this curation pass"
        },
        "cashless_treatment_supported": {
            "value": cash_v if cash_v is not None else True,
            "source_pdf_path": primary_pdf,
            "source_quote": (cash_q or "Cashless implicit via insurer network")[:240]
        },
        "claim_settlement_ratio": {
            "value": None,
            "source_url": None,
            "source_quote": "Insurer-level metric (IRDAI Annual Report); not extracted"
        },
        "tat_cashless_authorization_hours": {
            "value": None,
            "source_pdf_path": None,
            "source_quote": "TAT not specified in policy wording; governed by IRDAI Master Circular"
        },
        "policy_type": {
            "value": ptype_v,
            "source_pdf_path": primary_pdf,
            "source_quote": (ptype_q or "Policy type inferred from product structure")[:240]
        },
    }

    # Completeness: count populated fields (value != None & not insurer-level)
    pdf_fields = [
        "uin_code", "min_entry_age", "max_entry_age", "sum_insured_options",
        "initial_waiting_period_days", "pre_existing_disease_waiting_months",
        "specific_disease_waiting_months", "pre_hospitalization_days",
        "post_hospitalization_days", "day_care_treatments_count",
        "ayush_coverage", "maternity_coverage", "newborn_coverage",
        "organ_donor_expenses", "no_claim_bonus_pct", "restoration_benefit",
        "room_rent_capping", "copayment_pct", "policy_type",
        "cashless_treatment_supported"
    ]
    filled = sum(1 for f in pdf_fields if j[f]["value"] not in (None, ""))
    pct = int(round(filled / len(pdf_fields) * 100))

    j["_meta"] = {
        "curated_at": "2026-05-14",
        "primary_source_pdf": primary_pdf,
        "supporting_source_pdfs": supporting,
        "completeness_pct": pct,
        "notes": "Pattern-based extraction from local PDF via pdfplumber. Insurer-level metrics (CSR, network count) left null pending downstream backfill."
    }

    return j, pct


def main():
    results = []
    skipped = []
    for i, entry in enumerate(MANIFEST, 1):
        policy_id = entry[0]
        out_path = os.path.join(OUT_DIR, f"{policy_id}.json")
        # Skip if already exists
        if os.path.exists(out_path):
            print(f"[{i}/{len(MANIFEST)}] {policy_id}: EXISTS — skipping")
            continue
        j, pct = curate_one(entry)
        if j is None:
            print(f"[{i}/{len(MANIFEST)}] {policy_id}: SKIP — {pct}")
            skipped.append((policy_id, pct))
            continue
        if pct < 50:
            print(f"[{i}/{len(MANIFEST)}] {policy_id}: LOW {pct}% — skipping (below threshold)")
            skipped.append((policy_id, f"low completeness {pct}%"))
            continue
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(j, f, indent=2, ensure_ascii=False)
        print(f"[{i}/{len(MANIFEST)}] {policy_id}: {pct}%")
        results.append((policy_id, pct))

    print()
    print(f"Wrote {len(results)} JSONs.")
    print(f"Skipped {len(skipped)}.")
    if results:
        avg = sum(p for _, p in results) / len(results)
        print(f"Average completeness: {avg:.1f}%")
    return results, skipped


if __name__ == "__main__":
    main()
