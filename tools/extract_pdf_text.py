#!/usr/bin/env python3
"""Extract first 25K chars of text from a PDF for policy extraction.

Usage: python extract_pdf_text.py <pdf_path>
"""
import sys

import pdfplumber

if len(sys.argv) < 2:
    print("usage: extract_pdf_text.py <path>", file=sys.stderr)
    sys.exit(2)

path = sys.argv[1]
pages = []
with pdfplumber.open(path) as pdf:
    for p in pdf.pages[:30]:
        pages.append(p.extract_text() or "")
text = "\n".join(pages)
print(text[:25000])
