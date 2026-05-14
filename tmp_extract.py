#!/usr/bin/env python3
"""Extract relevant policy text from a PDF for schema extraction.

Strategy:
1. First 3 pages (preamble + ToC + insurer/UIN).
2. Pages with high keyword density for benefits / waiting / exclusions / sub-limits.
"""
import sys
import pdfplumber
import re

path = sys.argv[1]
maxchars = int(sys.argv[2]) if len(sys.argv) > 2 else 25000

# Keyword groups with weights
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
    # Penalty for "Def." dense definition pages
    defs = len(re.findall(r"Def\.\s*\d+", text))
    s -= defs * 2
    return s

with pdfplumber.open(path) as pdf:
    pages_data = []
    for i, p in enumerate(pdf.pages):
        t = p.extract_text() or ""
        pages_data.append((i, t, score(t)))

# Always include first 3 pages
selected_idx = set([0, 1, 2])
# Sort remaining pages by score
remaining = sorted(pages_data[3:], key=lambda x: -x[2])
total = sum(len(pages_data[i][1]) for i in selected_idx)
for i, t, sc in remaining:
    if total >= maxchars:
        break
    if sc <= 0:
        continue
    selected_idx.add(i)
    total += len(t)

# Output in page order
out = []
for i, t, sc in pages_data:
    if i in selected_idx:
        out.append(f"=== PAGE {i+1} (score={sc}) ===\n{t}")

print(("\n".join(out))[:maxchars])
