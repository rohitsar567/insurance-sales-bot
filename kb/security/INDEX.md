# Security — Upload Gates + Hallucination Defense

_Auto-generated. Source modules: `backend/security.py` + `backend/faithfulness.py`._

## Upload security — 8 gates

Every PDF uploaded via `/api/upload-policy` runs through these gates before
indexing. Pipeline lives in `backend/uploaded_docs.py` + `backend/security.py`,
governed by ADR-044 (2026-05-27). Failure logs to `logs/upload_blocks.jsonl`.

| # | Gate | Check |
| --- | --- | --- |
| 1 | File mechanics | Magic bytes `%PDF`; size 5KB-25MB; `%%EOF` present; dangerous PDF features (`/JavaScript`, `/Launch`, `/OpenAction`, `/EmbeddedFile`, `/SubmitForm`, `/AA`, `/RichMedia`, `/Movie`, `/Sound`, `/GoToR`); embedded executable signatures (Windows PE, Linux ELF, Mach-O, Java class, shell, HTML/JS, PHP) |
| 2 | Content quality | ≥1,500 chars text; ≥3 pages; ≥1 insurance keyword match (catches "garbage PDF" uploads) |
| 3 | Prompt injection | Regex sweep for "ignore previous instructions", "system prompt reveal", jailbreak markers, role-takeover patterns, im_start/im_end tokens |
| 4 | Per-session rate limit | 5 uploads/hour/session; 200 chunks/session lifetime |
| 5 | Per-IP rate limit | 10 uploads/hour/IP (per X-Forwarded-For or peer IP) |
| 6 | Encrypted / locked PDF reject | Refuse any PDF that is password-protected or has restrictive permissions blocking text extraction |
| 7 | Page-count ceiling | Reject PDFs with >200 pages |
| 8 | Hash dedupe + reject-cache | Re-uploads of an already-accepted PDF are deduped; re-uploads of a previously-rejected hash are short-circuited |

Beyond the 8, a **UIN net-new check** + **PDF-text fuzzy match** against the
catalogued 148 also run — uploads that match an existing catalogued policy
short-circuit to the catalogued card.

All gates run for EVERY upload. Block on any failure; the audit trail captures the reason set. See README §2.8 and `70-docs/60-decisions/ADR-044-uploaded-pdf-parity.md` for the dual-write model and the heuristic-floor / Gemini extraction chain.

## Hallucination defense — structural grounding (post-KI-225, 2026-05-15)

The single brain (`backend/single_brain.py`) quotes only what its tools returned:

| Tool | What it returns | Where the brain reads it |
|---|---|---|
| `retrieve_policies` | top-k policy-wording chunks from Chroma | `backend/brain_tools.py::retrieve_policies` |
| `get_policy_facts` | curated structured facts + verbatim `source_quote` | `backend/brain_tools.py::get_policy_facts` |

The brain's system prompt enforces "cite only what the tools returned" as a structural invariant. The pre-KI-225 architecture had a separate `backend/faithfulness.py` 4-gate post-hoc verifier — that module was removed in the single-brain consolidation because the single LLM's tool-grounded output flow makes the post-hoc verification structurally unnecessary. Source: ADR-040 + KI-225.

## What WE can't (yet) check

- LLM determinism (DeepSeek-V3 / Sarvam-M can produce slightly different
  output at `temperature=0`).
- Insurer-side PDF tampering — we trust the source PDF was real at download.
- Embedding model drift — pinned to BGE-small-en-v1.5.

These are explicit limits documented in `kb/AUDIT_TRAIL.md` §5.
