"""Provider clients — thin, async, behind a common interface.

Each provider lives in its own module:
  sarvam_stt.py        — Sarvam Saarika v2.5 (speech-to-text)
  sarvam_tts.py        — Sarvam Bulbul (text-to-speech)
  sarvam_llm.py        — Sarvam-M (chat / generation, primary brain)
  voyage_embeddings.py — Voyage voyage-3 (embeddings)
  groq_llm.py          — Llama-3.3-70B on Groq (grader + medium fallback brain)
  openrouter_llm.py    — DeepSeek-V3 via OpenRouter (strongest fallback brain)

All clients are async (use httpx.AsyncClient) so the FastAPI handlers can
parallelize provider calls without blocking the event loop.
"""
