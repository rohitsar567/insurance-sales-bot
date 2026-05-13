"""Provider clients — thin, async, behind a common interface.

After the 2026-05-14 Stack A consolidation (D-019), the provider stack is:

  sarvam_stt.py        — Sarvam Saarika v2.5 (speech-to-text)
  sarvam_tts.py        — Sarvam Bulbul v2 (text-to-speech)
  sarvam_llm.py        — Sarvam-M (Indic translation IN and OUT; not brain anymore)
  local_embeddings.py  — BGE-small-en-v1.5 (local CPU embeddings)
  nvidia_nim_llm.py    — NIM Llama-3.3-70B-Instruct (BRAIN) + Llama-4 Maverick (JUDGE)

Four legacy providers were retired in the same change: openrouter_llm.py,
deepseek_llm.py, cerebras_llm.py, groq_llm.py. Single NIM key replaces all
four. See docs/decisions.md D-019.

All clients are async (use httpx.AsyncClient) so the FastAPI handlers can
parallelize provider calls without blocking the event loop.
"""
