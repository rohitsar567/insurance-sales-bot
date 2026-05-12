# Research — Corpus Acquisition

_Auto-generated from `rag/corpus/_manifest.json` at 2026-05-12T23:12:11Z_

## Headline
- Total attempted: **91** URLs across 10 target insurers
- Successfully downloaded: **76** PDFs
- Failed: **15**
- Elapsed: 1008.2s

## Per-insurer breakdown

| Insurer | OK | Fail |
| --- | --- | --- |
| `aditya-birla` | 6 | 0 |
| `bajaj-allianz` | 10 | 1 |
| `care-health` | 9 | 0 |
| `hdfc-ergo` | 12 | 0 |
| `icici-lombard` | 9 | 0 |
| `manipalcigna` | 4 | 3 |
| `new-india` | 8 | 0 |
| `niva-bupa` | 10 | 0 |
| `star-health` | 0 | 11 |
| `tata-aig` | 8 | 0 |

## Failure reasons

| Reason | Count |
| --- | --- |
| `http_403` | 8 |
| `http_404` | 4 |
| `req_ConnectionError` | 3 |

## How we did it
- Dispatched a research agent to find direct PDF URLs for all health policies across 10 target insurers
- Source list saved to `data/corpus_urls.md` (75 URLs)
- `rag/download_corpus.py` downloads with PDF magic-byte verification + size floor (50KB)
- `rag/download_retry.py` retried failed downloads with browser-grade headers (rescued ICICI Lombard 9/9)
- Star Health (11 PDFs) blocked by CDN bot protection — deferred to v2 (see `docs/04-failure-modes.md` + ROADMAP)
