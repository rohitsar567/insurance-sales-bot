"""Sarvam-M — chat / text generation (primary brain).

Sarvam exposes an OpenAI-compatible chat completions endpoint at
POST https://api.sarvam.ai/v1/chat/completions.

Auth: header `api-subscription-key: <SARVAM_API_KEY>` (Sarvam) or
      Bearer token; we use `Authorization: Bearer ...` style which is
      OpenAI-compatible and Sarvam supports.
"""

from __future__ import annotations

from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import ChatMessage, LLMProvider, LLMResult


class SarvamLLM(LLMProvider):
    name = "sarvam-m"
    model = settings.SARVAM_LLM_MODEL

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = settings.SARVAM_LLM_MODEL,
        timeout: float = 60.0,
    ):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("SARVAM_API_KEY not set in .env")

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
        url = f"{settings.SARVAM_BASE_URL}{settings.SARVAM_CHAT_PATH}"
        body: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        headers = {
            "api-subscription-key": self.api_key,
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
