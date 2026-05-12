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

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
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
