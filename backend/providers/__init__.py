"""Provider clients — thin, async, behind a common interface.

After the 2026-05-14 Stack A consolidation (D-019, partially superseded by
D-022 on 2026-05-14 — NIM brain pool swap), the provider stack is:

  sarvam_stt.py        — Sarvam Saarika v2.5 (speech-to-text)
  sarvam_tts.py        — Sarvam Bulbul v2 (text-to-speech, speaker anushka)
  sarvam_llm.py        — Sarvam-M (Indic translation IN and OUT; not brain)
  local_embeddings.py  — BGE-small-en-v1.5 (local CPU embeddings)
  nvidia_nim_llm.py    — Chain pattern via NimChainLLM. Current primaries (D-022):
                           BRAIN_CHAIN[0]      = Qwen 3-Next 80B
                           FAST_BRAIN_CHAIN[0] = Nemotron Nano 30B
                           JUDGE_CHAIN[0]      = Mistral Large 3 675B
                         DeepSeek V4-Pro / V4-Flash + Llama-4 Maverick (the
                         original D-019 picks) are retained as fallback chain
                         entries that re-enter rotation when NIM's pools
                         recover from the 2026-05-14 outage.
  openrouter_llm.py    — OpenRouter cross-provider fallback (bottom of every chain).
  groq_llm.py          — Groq LPU; brain primary rotates 50/50 NIM ↔ Groq
                         per call (KI-025 / ADR-026).

See 70-docs/60-decisions/ADR-019, ADR-026, ADR-030 and 80-audit/ENTERPRISE_AUDIT.md
(D-022 row) for the full provenance.

All clients are async (use httpx.AsyncClient) so the FastAPI handlers can
parallelize provider calls without blocking the event loop.
"""
