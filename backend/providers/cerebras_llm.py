"""Cerebras — Llama-3.3-70B (primary brain when CEREBRAS_API_KEY is set).

Cerebras exposes an OpenAI-compatible chat completions endpoint at
POST https://api.cerebras.ai/v1/chat/completions.

Why Cerebras + why primary over Groq:
  - Same Llama-3.3-70B model (no quality difference)
  - Cerebras Wafer-Scale Engine outpaces Groq on Llama inference
  - Free tier has much higher rate limits than Groq (~30 req/sec vs 30 req/min)
  - Drop-in API shape — same retry/backoff pattern works

Used in three roles when CEREBRAS_API_KEY is configured:
  1. Eval grader (replaces Groq for chunk-sweep load)
  2. Orchestrator fallback brain (replaces Groq fallback)
  3. Faithfulness Gate 4 LLM judge

When CEREBRAS_API_KEY is NOT set, callers fall back to GroqLLM transparently.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import ChatMessage, LLMProvider, LLMResult


CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
# Cerebras free-tier flagship as of May 2026 — 235B params, beats Llama-3.3-70B
# on most reasoning + judging benchmarks. Larger context window, very fast on
# Cerebras's WSE-3 hardware. Verified available via /v1/models endpoint.
CEREBRAS_DEFAULT_MODEL = "qwen-3-235b-a22b-instruct-2507"


class CerebrasLLM(LLMProvider):
    name = "cerebras-llama"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = CEREBRAS_DEFAULT_MODEL,
        timeout: float = 60.0,
    ):
        # Prefer explicit api_key arg; else read from settings (which reads CEREBRAS_API_KEY env)
        self.api_key = api_key or getattr(settings, "CEREBRAS_API_KEY", "")
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError(
                "CEREBRAS_API_KEY not set. Get a key at https://cloud.cerebras.ai/ "
                "and add CEREBRAS_API_KEY=... to .env"
            )

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
        url = f"{CEREBRAS_BASE_URL}/chat/completions"
        body: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Cerebras rate limits are much more generous than Groq, but we still
        # retry on 429 / 5xx with exponential backoff — same shape as the
        # Groq client so the call site behaves identically.
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            attempts = 4
            delay = 1.0
            for attempt in range(attempts):
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 429 or (500 <= resp.status_code < 600):
                    if attempt == attempts - 1:
                        resp.raise_for_status()
                    ra = resp.headers.get("Retry-After")
                    wait = float(ra) if ra and ra.replace(".", "").isdigit() else delay
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
                resp.raise_for_status()
                break
            payload = resp.json()

        choice = payload["choices"][0]
        usage = payload.get("usage", {})
        return LLMResult(
            text=choice["message"]["content"],
            model=payload.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw=payload,
        )


def get_judge_llm(language: str = "en") -> LLMProvider:
    """Provider chain for LLM judge / fallback brain.

    LANGUAGE-AWARE: Hindi/Hinglish queries prefer Llama-3.3-70B (via Groq)
    because Llama has ~5× more Indic training data than Qwen-3-235B. English
    queries (and all judge/grader tasks, which are always English) prefer
    Cerebras Qwen-235B for higher quota + larger model.

    language: 'en' for English / pure judge tasks, 'hi' for Hindi/Hinglish.

    Resolution order:
      English / unspecified:
        Cerebras Qwen-3-235B → Groq Llama-3.3-70B (fallback if Cerebras fails)
      Hindi / Hinglish:
        Groq Llama-3.3-70B → Cerebras Qwen-3-235B (fallback)
    """
    lang = (language or "en").lower()
    is_indic = lang.startswith("hi") or lang in ("hinglish", "indic")

    from backend.providers.groq_llm import GroqLLM

    if is_indic:
        # Indic-first chain — Llama 70B has more Hindi training than Qwen
        try:
            return GroqLLM()
        except RuntimeError:
            pass
        if getattr(settings, "CEREBRAS_API_KEY", ""):
            try:
                return CerebrasLLM()
            except RuntimeError:
                pass
    else:
        # English / judge-task chain — Cerebras Qwen 235B is bigger + faster
        if getattr(settings, "CEREBRAS_API_KEY", ""):
            try:
                return CerebrasLLM()
            except RuntimeError:
                pass
        try:
            return GroqLLM()
        except RuntimeError:
            pass
    raise RuntimeError("No LLM provider available — set CEREBRAS_API_KEY or GROQ_API_KEY in .env")
