#!/usr/bin/env python3
"""Extract text from a PDF over a page range and char window.

Usage: python extract_pdf_range.py <pdf_path> <page_start> <page_end> [char_max]
"""
import sys

import pdfplumber

if len(sys.argv) < 4:
    print("usage: extract_pdf_range.py <path> <page_start> <page_end> [char_max]", file=sys.stderr)
    sys.exit(2)

path = sys.argv[1]
ps = int(sys.argv[2])
pe = int(sys.argv[3])
cmax = int(sys.argv[4]) if len(sys.argv) > 4 else 25000

pages = []
with pdfplumber.open(path) as pdf:
    end = min(pe, len(pdf.pages))
    for i in range(ps, end):
        pages.append(pdf.pages[i].extract_text() or "")
text = "\n".join(pages)
print(text[:cmax])
