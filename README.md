---
title: Insurance Sales Portfolio Expert
emoji: 🏥
colorFrom: teal
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Voice-first AI advisor for Indian health insurance · Sarvam AI
---

# Insurance Sales Portfolio Expert

A voice-first AI advisor for Indian health insurance buyers, over a curated corpus of policies from 10 leading insurers. Built as a take-home assignment for Sarvam AI.

**Live demo:** *(deployed URL — added at submission)*

**Docs (read in order):**

1. [`docs/01-requirements.md`](docs/01-requirements.md) — product vision, personas, success criteria, non-goals
2. [`docs/02-architecture.md`](docs/02-architecture.md) — stack picks, schema, system design *(in progress)*
3. [`docs/03-eval-plan.md`](docs/03-eval-plan.md) — gold Q&A pairs, automated grader, accuracy targets *(in progress)*
4. [`docs/04-failure-modes.md`](docs/04-failure-modes.md) — known failure modes + mitigations *(in progress)*
5. [`docs/05-needs-analysis-flow.md`](docs/05-needs-analysis-flow.md) — fact-find question graph *(in progress)*
6. [`docs/decisions.md`](docs/decisions.md) — every meaningful decision logged with alternatives + reasoning
7. [`docs/ROADMAP.md`](docs/ROADMAP.md) — how this scales from v1 vertical slice to full platform

## Quick start

```bash
git clone https://github.com/rohitsar567/insurance-sales-bot.git
cd insurance-sales-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in SARVAM_API_KEY
streamlit run streamlit_app.py
```

## Architecture at a glance

```
User voice → Sarvam Saaras (STT) → Orchestrator → Sarvam-M (LLM)
                                          ↓
                                  Hybrid retrieval:
                                  ├─ Structured (DuckDB) — filters, comparison
                                  └─ Unstructured (Chroma) — chunked PDFs, RAG
                                          ↓
                                  Cited response → Sarvam Bulbul (TTS) → User
```

## What's in scope for v1 (the vertical slice)

- 10 insurers × all their health policies (target: 40–80 policies)
- 40–50 structured fields per policy (premium, sum insured, waiting periods, PED, sub-limits, network, claim ratio, geography, etc.)
- Voice advisor with Hindi/English code-switch
- Adaptive needs analysis
- Granular filter + side-by-side comparison
- Illustrative pricing bands

## What's out of scope (v2 roadmap)

See [`docs/01-requirements.md` §7](docs/01-requirements.md).

## Built with

- **STT**: Sarvam Saaras
- **TTS**: Sarvam Bulbul
- **LLM**: Sarvam-M
- **Vector DB**: Chroma
- **Structured DB**: DuckDB
- **UI**: Streamlit

Each pick is justified in [`docs/decisions.md`](docs/decisions.md).
