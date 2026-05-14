#!/usr/bin/env python3
"""Extract text from all PDFs in the batch list. Write each to /tmp/pdfraw/<policy_id>.txt."""
import os
import sys
import re
import pdfplumber

BATCH_FILE = "/tmp/extract_batch_4.txt"
OUT_DIR = "/tmp/pdfraw"
os.makedirs(OUT_DIR, exist_ok=True)

MAX_CHARS = 28000

PATTERNS = [
    (re.compile(r"waiting period", re.I), 3),
    (re.compile(r"pre[- ]?existing", re.I), 3),
    (re.compile(r"sum insured", re.I), 2),
    (re.compile(r"entry age|age limit|renewal", re.I), 2),
    (re.compile(r"grace period|free look", re.I), 3),
    (re.compile(r"room rent|icu", re.I), 3),
    (re.compile(r"co[- ]?pay", re.I), 3),
    (re.compile(r"deductible", re.I), 2),
    (re.compile(r"day care", re.I), 2),
    (re.compile(r"domiciliary|ayush|maternity|new\s*born|organ donor|ambulance", re.I), 2),
    (re.compile(r"cumulative bonus|no claim|recharge|reload|restoration", re.I), 2),
    (re.compile(r"network|hospitals across", re.I), 1),
    (re.compile(r"critical illness", re.I), 1),
    (re.compile(r"exclusion|excluded", re.I), 2),
    (re.compile(r"sub[- ]?limit|cataract|knee|joint replacement", re.I), 2),
    (re.compile(r"UIN", re.I), 2),
    (re.compile(r"family floater|self.*spouse|dependent", re.I), 1),
    (re.compile(r"₹|Rs\.|INR|lakh|crore", re.I), 1),
]


def score(text):
    s = 0
    for pat, w in PATTERNS:
        s += len(pat.findall(text)) * w
    defs = len(re.findall(r"Def\.\s*\d+", text))
    s -= defs * 2
    return s


def slug_for(rel_path):
    parts = rel_path.split("/")
    # rag/corpus/<insurer-slug>/<file-stem>.pdf
    insurer = parts[2]
    stem = parts[3].replace(".pdf", "")
    return f"{insurer}__{stem}"


with open(BATCH_FILE) as f:
    paths = [ln.strip() for ln in f if ln.strip()]

base = "/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot"

for rel in paths:
    pid = slug_for(rel)
    out = os.path.join(OUT_DIR, pid + ".txt")
    if os.path.exists(out):
        continue
    full = os.path.join(base, rel)
    try:
        with pdfplumber.open(full) as pdf:
            pages_data = []
            for i, p in enumerate(pdf.pages):
                t = p.extract_text() or ""
                pages_data.append((i, t, score(t)))
        # First 3 + top-score
        selected_idx = set([0, 1, 2])
        remaining = sorted([(i, t, s) for i, t, s in pages_data[3:]], key=lambda x: -x[2])
        total = sum(len(pages_data[i][1]) for i in selected_idx if i < len(pages_data))
        for i, t, sc in remaining:
            if total >= MAX_CHARS:
                break
            if sc <= 0:
                continue
            selected_idx.add(i)
            total += len(t)
        out_text = []
        for i, t, sc in pages_data:
            if i in selected_idx:
                out_text.append(f"=== PAGE {i+1} (score={sc}) ===\n{t}")
        result = ("\n".join(out_text))[:MAX_CHARS]
        with open(out, "w") as fo:
            fo.write(result)
        print(f"OK {pid} pages={len(pages_data)} chars={len(result)}")
    except Exception as e:
        print(f"ERR {pid} {e}")
