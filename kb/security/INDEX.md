# Security — Upload Gates + Hallucination Defense

_Auto-generated. Source modules: `backend/security.py` + `backend/faithfulness.py`._

## Upload security — 5 gates

Every PDF uploaded via `/api/upload-policy` runs through these gates before
indexing. Failure logs to `logs/upload_blocks.jsonl`.

| # | Gate | Check |
| --- | --- | --- |
| 1 | Mechanics | Magic bytes `%PDF`; size 5KB-25MB; `%%EOF` present; dangerous PDF features (`/JavaScript`, `/Launch`, `/OpenAction`, `/EmbeddedFile`, `/SubmitForm`, `/AA`, `/RichMedia`, `/Movie`, `/Sound`, `/GoToR`); embedded executable signatures (Windows PE, Linux ELF, Mach-O, Java class, shell, HTML/JS, PHP) |
| 2 | Content quality | ≥1,500 chars text; ≥3 pages; ≥1 insurance keyword match (catches "garbage PDF" uploads) |
| 3 | Prompt injection | 11 regex patterns scanning for "ignore previous instructions", "system prompt reveal", jailbreak markers, role-takeover patterns, im_start/im_end tokens |
| 4 | Session rate limit | 5 uploads/hour/session; 200 chunks/session lifetime |
| 5 | IP rate limit | 10 uploads/hour/IP (per X-Forwarded-For or peer IP) |

All gates run for EVERY upload. Block on any failure; the audit trail captures the reason set.

## Hallucination defense — 5 gates (runtime, per-turn)

| # | Gate | What it catches |
| --- | --- | --- |
| 1 | Retrieval floor | Top-1 cosine < 0.30 OR avg top-5 < 0.22 → refuse outright |
| 2 | Citation integrity | Any `[Source:…]` in the bot's reply must point to a real retrieved chunk's policy_name |
| 3 | Numeric grounding | Every ₹, %, day/month/year in the reply must appear in retrieved chunks (regex) |
| 4 | LLM-judge faithfulness | NIM Mistral Large 3 675B (primary judge per D-022) + Llama-4 Maverick (fallback) inspects the reply against retrieved chunks; outputs strict JSON; different family from Qwen 3-Next 80B brain (primary per D-022) + DeepSeek-V4 (fallback) → non-circular eval (D-019). |
| 5 (Indic) | Hinglish drift LLM-judge | Same idea on the Hinglish back-translation vs the English source |

Plus **regex anchors + back-translate cosine** as additional drift checks
when the bot replies in Hinglish.

All blocked replies → `logs/hallucinations.jsonl` with the reason set.

## What WE can't (yet) check

- LLM determinism (DeepSeek-V3 / Sarvam-M can produce slightly different
  output at `temperature=0`).
- Insurer-side PDF tampering — we trust the source PDF was real at download.
- Embedding model drift — pinned to BGE-small-en-v1.5.

These are explicit limits documented in `kb/AUDIT_TRAIL.md` §5.
