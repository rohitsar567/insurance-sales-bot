"""OpenRouter — DeepSeek-V3 (strongest fallback brain).

OpenRouter exposes an OpenAI-compatible chat completions endpoint at
POST https://openrouter.ai/api/v1/chat/completions.

Used as the strongest fallback brain in the router for complex
multi-policy reasoning / recommendation queries.
"""

from __future__ import annotations

from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import ChatMessage, LLMProvider, LLMResult


class OpenRouterLLM(LLMProvider):
    name = "openrouter-deepseek"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = settings.OPENROUTER_BRAIN_MODEL,
        timeout: float = 90.0,
    ):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set in .env")

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
        url = f"{settings.OPENROUTER_BASE_URL}/chat/completions"
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
            "HTTP-Referer": "https://github.com/rohitsar567/insurance-sales-bot",
            "X-Title": "Insurance Sales Portfolio Expert",
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
