"""Groq — cross-provider fallback brain/judge running on LPU hardware.

Groq exposes an OpenAI-compatible chat completions endpoint at
POST https://api.groq.com/openai/v1/chat/completions.

Why include this as the last fallback?
  - Groq runs on custom LPU silicon (Language Processing Unit) — bone-fast
    inference (often the lowest TTFT of any free-tier provider). On
    llama-3.3-70b-versatile we typically see sub-second token streaming.
  - Different provider, different DNS, different upstream pool from NIM
    and OpenRouter — so its presence at the bottom of BRAIN_CHAIN +
    JUDGE_CHAIN guarantees the reasoning stack survives a simultaneous
    NIM + OpenRouter regional outage.

Drop-in behaviour matches `NvidiaNimLLM`:
  - Bearer auth + OpenAI request shape
  - 4 attempts with exponential backoff on 429 / 5xx
  - Returns `LLMResult` with `text` + `model` populated
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import ChatMessage, LLMProvider, LLMResult


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Default model: llama-3.3-70b-versatile — Meta's Llama 3.3 70B Instruct,
# served on Groq's LPU at the lowest TTFT of any free-tier 70B option.
# Sane choice as a last-resort cross-provider fallback.
DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqLLM(LLMProvider):
    name = "groq"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        chain_name: str = "unknown",
    ):
        self.api_key = api_key or getattr(settings, "GROQ_API_KEY", "")
        self.model = model
        self.timeout = timeout
        # KI-085 — chain_name plumbs through so update_credits_from_groq
        # can route the response-header signal to the right chain state.
        self.chain_name = chain_name
        if not self.api_key:
            raise RuntimeError(
                "GROQ_API_KEY not set. Get a key at https://console.groq.com/keys "
                "and add GROQ_API_KEY=gsk_... to .env"
            )
        self.name = f"groq::{model.split('/')[-1]}"

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
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

        # KI-084 — per-phase httpx timeouts so a stuck Groq connection
        # frees its slot independently of the connection-level deadline.
        client_timeout = httpx.Timeout(
            connect=2.0,
            read=self.timeout,
            write=2.0,
            pool=2.0,
        )
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            attempts = 4
            delay = 1.0
            for attempt in range(attempts):
                resp = await client.post(GROQ_URL, headers=headers, json=body)
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
            # Stamp credits_remaining from Groq's x-ratelimit-* headers
            # BEFORE we drop the response. Groq is
            # the highest-fidelity provider on this front: every successful
            # call returns the daily-tokens remaining, so we get a
            # continuously-updated election signal at zero extra cost.
            try:
                from backend import llm_health
                chain_for_credits = (
                    f"groq:{self.model}"  # chain entries are prefixed
                )
                llm_health.update_credits_from_groq(
                    self.chain_name, chain_for_credits, dict(resp.headers)
                )
            except Exception:
                pass  # credit tracking must never break the chat path
            payload = resp.json()

        choice = payload["choices"][0]
        msg = choice.get("message", {}) or {}
        text = msg.get("content") or msg.get("reasoning_content") or ""
        usage = payload.get("usage", {})
        return LLMResult(
            text=text,
            model=payload.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw=payload,
        )
