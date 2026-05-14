"""OpenRouter — cross-provider fallback brain/judge for the reasoning stack.

OpenRouter aggregates dozens of OSS + proprietary models behind one
OpenAI-compatible endpoint at
POST https://openrouter.ai/api/v1/chat/completions.

Why have this in the chain at all when NIM is primary?
  - NIM has had regional pool outages (full-tier brownouts on the DeepSeek-V4
    and Meta Llama pools, multi-hour). If every brain candidate inside NIM
    sits behind the same regional ingress, a NIM-side incident takes the
    whole reasoning stack down.
  - OpenRouter is a fully different provider (different DNS, different
    region, different upstream pools) so its inclusion as the last entries
    of BRAIN_CHAIN + JUDGE_CHAIN keeps the brain + judge alive through a
    complete NIM outage.

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


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default model: openai/gpt-oss-120b — OpenAI's open-weights GPT-OSS 120B.
# Free on OpenRouter (no cost tier), MIT licensed, strong on reasoning +
# instruction following — a sane cross-provider fallback for both brain and
# judge roles when NIM is fully down.
DEFAULT_MODEL = "openai/gpt-oss-120b"


class OpenRouterLLM(LLMProvider):
    name = "openrouter"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key or getattr(settings, "OPENROUTER_API_KEY", "")
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set. Get a key at https://openrouter.ai/keys "
                "and add OPENROUTER_API_KEY=sk-or-... to .env"
            )
        self.name = f"openrouter::{model.split('/')[-1]}"

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
            # OpenRouter requires HTTP-Referer + X-Title for free-tier rate-limit
            # categorization + analytics. Without these the key still works but
            # is treated as anonymous traffic (lower priority).
            "HTTP-Referer": "https://huggingface.co/spaces/rohitsar567/InsuranceBot",
            "X-Title": "Insurance Bot",
        }

        # KI-084 — per-phase httpx timeouts (connect / read / write / pool)
        # so a stuck OpenRouter connection releases its slot on its own
        # deadline, independent of the outer wait_for cancellation.
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
                resp = await client.post(OPENROUTER_URL, headers=headers, json=body)
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
