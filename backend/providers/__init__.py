"""Provider clients — thin, async, behind a common interface.

The provider stack:

  sarvam_stt.py        — Sarvam Saarika v2.5 (speech-to-text)
  sarvam_tts.py        — Sarvam Bulbul v2 (text-to-speech, speaker anushka)
  sarvam_llm.py        — Sarvam-M (Indic translation IN and OUT; not brain)
  local_embeddings.py  — BGE-small-en-v1.5 (local CPU embeddings)
  nvidia_nim_llm.py    — Chain pattern via NimChainLLM. BRAIN_CHAIN is the
                         only chain: Qwen 3-Next 80B → Mistral Large 3
                         675B → Llama-4 Maverick 17B → Nemotron-Super 49B
                         (last resort).
  openrouter_llm.py    — OpenRouter client (cross-provider option).
  groq_llm.py          — Groq LPU client.

All clients are async (use httpx.AsyncClient) so the FastAPI handlers can
parallelize provider calls without blocking the event loop.
"""
