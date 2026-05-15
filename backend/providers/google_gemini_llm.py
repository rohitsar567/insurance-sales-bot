"""Google Gemini — primary LLM tier for sales_brain + orchestrator brain (KI-179).

Google AI Studio's free tier on `gemini-2.0-flash` and `gemini-2.5-flash` gives
1500 req/day with native JSON mode (`responseMimeType: "application/json"`).
This is best-in-class free quality for conversation and beats every model in
NIM or OpenRouter free tiers.

Wire-shape: REST API direct via httpx (no SDK dependency — matches the pattern
of nvidia_nim_llm.py and openrouter_llm.py).

Endpoint:
    POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}

Request body:
    {
      "contents": [
        {"role": "user", "parts": [{"text": "..."}]},
        {"role": "model", "parts": [{"text": "..."}]}
      ],
      "systemInstruction": {"parts": [{"text": "..."}]},
      "generationConfig": {
        "temperature": 0.6,
        "maxOutputTokens": 700,
        "responseMimeType": "application/json"
      }
    }

Role mapping:
    OpenAI shape       →   Gemini shape
    role="system"      →   systemInstruction (separate top-level field)
    role="user"        →   contents[i].role = "user"
    role="assistant"   →   contents[i].role = "model"

JSON mode:
    response_format == {"type": "json_object"}  →
        generationConfig.responseMimeType = "application/json"

The provider matches the existing `LLMProvider` interface so it slots
straight into both sales_brain.py (Tier 0) and the orchestrator brain main
path (Tier 0, via TieredBrainLLM wrapper).
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx

from backend.providers.base import ChatMessage, LLMProvider, LLMResult


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash-lite"  # KI-183 — gemini-2.0-flash retired for new accounts


def _to_gemini_contents(messages: list[ChatMessage]) -> tuple[Optional[str], list[dict]]:
    """Split an OpenAI-style message list into (systemInstruction, contents).

    Gemini's API expects:
      - `systemInstruction` as a separate top-level field (one block, the
        concatenation of every system message in order)
      - `contents` as a list of `{role: "user" | "model", parts: [{text: ...}]}`
        entries (no "system" role allowed inside contents)

    OpenAI's role names map cleanly:
      - "system"    → folded into systemInstruction
      - "user"      → contents[i].role = "user"
      - "assistant" → contents[i].role = "model"

    Empty / non-string content is coerced to a safe empty string so a stray
    None never breaks the wire payload.
    """
    system_chunks: list[str] = []
    contents: list[dict] = []
    for m in messages:
        text = m.content if isinstance(m.content, str) else str(m.content or "")
        if m.role == "system":
            if text:
                system_chunks.append(text)
            continue
        if m.role == "user":
            contents.append({"role": "user", "parts": [{"text": text}]})
        elif m.role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            # Unknown role — treat as user input rather than dropping it.
            contents.append({"role": "user", "parts": [{"text": text}]})
    system_instruction = "\n\n".join(system_chunks) if system_chunks else None
    return system_instruction, contents


class GoogleGeminiLLM(LLMProvider):
    """Google Gemini Flash via the AI Studio REST API.

    `api_key` is read from the `GOOGLE_API_KEY` env var at *chat-time* — NOT
    at module import time — so missing-key errors only surface when this
    provider is actually called. That keeps cold imports cheap and lets the
    Tier 0 layer cleanly fall through to Tier 1 (NIM) when the key is
    unavailable (e.g., on the eval harness machine).
    """

    name = "gemini"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        timeout: float = 25.0,
    ):
        # Defer the key-presence check to chat() — see class docstring.
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self.name = f"gemini::{model}"

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.6,
        max_tokens: int = 700,
        response_format: Optional[dict] = None,
        **kwargs,  # absorb OR-specific kwargs like `models=[...]` — ignored here
    ) -> LLMResult:
        if not self.api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY not set. Get a key at "
                "https://aistudio.google.com/app/apikey and add "
                "GOOGLE_API_KEY=... to .env"
            )

        system_instruction, contents = _to_gemini_contents(messages)

        generation_config: dict = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        # JSON mode — Gemini's native equivalent of OpenAI response_format.
        if response_format and response_format.get("type") == "json_object":
            generation_config["responseMimeType"] = "application/json"

        body: dict = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        # API key goes in the URL query param — the Google AI Studio default.
        # The header form (`x-goog-api-key`) also works but is undocumented for
        # the v1beta generativelanguage endpoint.
        url = f"{GEMINI_BASE_URL}/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}

        # Per-phase timeouts mirroring the NIM/OpenRouter pattern: a stuck
        # connection releases its slot on its own deadline rather than holding
        # past the outer wait_for cancellation.
        client_timeout = httpx.Timeout(
            connect=2.0,
            read=self.timeout,
            write=2.0,
            pool=2.0,
        )

        async with httpx.AsyncClient(timeout=client_timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                # Surface the Google error body so the caller's log makes the
                # root cause visible (typical failure: 429 quota exceeded or
                # 400 prompt-block).
                detail = ""
                try:
                    detail = resp.text[:500]
                except Exception:
                    pass
                raise httpx.HTTPStatusError(
                    f"Gemini API {resp.status_code}: {detail}",
                    request=resp.request,
                    response=resp,
                )
            payload = resp.json()

        # Response shape:
        #   {"candidates": [{"content": {"parts": [{"text": "..."}], "role": "model"},
        #                    "finishReason": "STOP"}],
        #    "usageMetadata": {"promptTokenCount": N, "candidatesTokenCount": M, ...}}
        text = ""
        try:
            candidates = payload.get("candidates") or []
            if candidates:
                parts = (candidates[0].get("content") or {}).get("parts") or []
                # Concatenate every text part — Gemini sometimes returns
                # multiple chunks per candidate when streaming-shape leaks
                # into a non-streaming response.
                text = "".join(
                    p.get("text", "") for p in parts if isinstance(p, dict)
                )
        except Exception:
            text = ""

        usage = payload.get("usageMetadata") or {}
        return LLMResult(
            text=text,
            model=self.model,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            raw=payload,
        )


# ----------------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------------

def get_gemini_llm(
    model: str = DEFAULT_MODEL,
    timeout: float = 25.0,
) -> GoogleGeminiLLM:
    """Return a fresh GoogleGeminiLLM client.

    25s timeout matches the KI-179 brief — Gemini Flash typically responds in
    1-3s so 25s is a generous outer bound that still bails fast on a quota /
    network stall.

    Common models:
      - "gemini-2.0-flash"   → fast, conversational, sales_brain Tier 0
      - "gemini-2.5-flash"   → slightly heavier reasoning, brain main Tier 0
    """
    return GoogleGeminiLLM(model=model, timeout=timeout)


__all__ = ["GoogleGeminiLLM", "get_gemini_llm", "DEFAULT_MODEL"]
