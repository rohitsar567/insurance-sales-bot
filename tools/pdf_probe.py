import time, pdfplumber
t0 = time.time()
with pdfplumber.open('rag/corpus/star-health/star-hospital-cash__brochure.pdf') as p:
    nP = len(p.pages)
    print(f'pages: {nP}')
    sample = ''
    for i, page in enumerate(p.pages[:5]):
        t = page.extract_text() or ''
        sample += t
    print(f'first 5 pages chars: {len(sample)}')
    print(f'elapsed: {time.time()-t0:.1f}s')
