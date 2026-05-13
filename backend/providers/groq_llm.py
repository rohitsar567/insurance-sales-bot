"""Groq — Llama-3.3-70B (grader + medium fallback brain).

Groq exposes an OpenAI-compatible chat completions endpoint at
POST https://api.groq.com/openai/v1/chat/completions.

Used in two roles:
  1. Grader for eval harness (response: scoring)
  2. Fallback brain in the router for medium-complexity queries
"""

from __future__ import annotations

from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import ChatMessage, LLMProvider, LLMResult


class GroqLLM(LLMProvider):
    name = "groq-llama"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = settings.GROQ_BRAIN_MODEL,
        timeout: float = 60.0,
    ):
        self.api_key = api_key or settings.GROQ_API_KEY
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set in .env")

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
        url = f"{settings.GROQ_BASE_URL}/chat/completions"
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

        import asyncio
        # Groq free tier rate-limits aggressively (~30 req/min). Retry on 429
        # with exponential backoff; also retry transient 5xx.
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            attempts = 4
            delay = 1.5
            for attempt in range(attempts):
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 429 or (500 <= resp.status_code < 600):
                    if attempt == attempts - 1:
                        resp.raise_for_status()
                    # Honor Retry-After if Groq sends one; else exponential
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
