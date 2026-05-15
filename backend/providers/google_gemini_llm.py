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
import hashlib
import logging
import os
import threading
import time
from typing import Optional

import httpx

from backend.providers.base import ChatMessage, LLMProvider, LLMResult


GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_CACHE_URL = "https://generativelanguage.googleapis.com/v1beta/cachedContents"
DEFAULT_MODEL = "gemini-2.5-flash-lite"  # KI-183 — gemini-2.0-flash retired for new accounts

# ----------------------------------------------------------------------------
# KI-199 — module-level cachedContents registry.
#
# Keyed by `(model, sha256(system_text))` so the same base preamble shared
# across sales_brain calls deduplicates onto one cache. Each value is a small
# dict with the cache `name` (the server-side resource id used in subsequent
# generateContent bodies as `cachedContent`) and the local-clock `expires_at`
# wall-time so we can self-evict before issuing a guaranteed-miss request.
#
# A threading.Lock guards entries against the rare interleave where two
# coroutines on different event loops race to create the same cache. In
# practice asyncio gives us implicit single-task ordering on one loop, but
# this is cheap insurance and keeps the contract honest if the module is ever
# pulled into a thread pool.
# ----------------------------------------------------------------------------
_CACHE_REGISTRY: dict[tuple[str, str], dict] = {}
_CACHE_REGISTRY_LOCK = threading.Lock()


def _cache_key(model: str, system_text: str) -> tuple[str, str]:
    """Build the registry key for a (model, system_text) pair.

    Hashing the system text rather than storing the raw string keeps the
    registry footprint tiny even when the preamble is multi-KB.
    """
    return (model, hashlib.sha256(system_text.encode("utf-8")).hexdigest())


def invalidate_cache(model: str, system_text: str) -> None:
    """Drop a cache registry entry — called by upstream after a 4xx response
    that names a stale `cachedContent`. The server-side cache may still be
    alive (it will lapse on TTL), but our reference is gone so the next
    chat() call provisions a fresh one.
    """
    key = _cache_key(model, system_text)
    with _CACHE_REGISTRY_LOCK:
        _CACHE_REGISTRY.pop(key, None)


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

    async def create_cache(
        self,
        system_text: str,
        ttl_seconds: int = 300,
    ) -> Optional[str]:
        """Create (or reuse) a Gemini `cachedContents` resource for `system_text`.

        Returns the cache resource name (e.g. `"cachedContents/<UUID>"`)
        that downstream `chat()` calls should pass as `cached_content_name`.
        Returns None on ANY failure (missing key, too-small payload, 4xx, network
        error) — the caller is expected to proceed without caching when this is
        the case.

        Re-uses an existing live cache from the module registry when the
        (model, system_text) pair matches AND the local `expires_at` is still
        in the future. Cache misses + creation failures are silent (logged at
        INFO) so a caching outage never breaks the main path — KI-199 brief
        requires fail-safe behaviour.
        """
        if not self.api_key or not system_text:
            return None

        key = _cache_key(self.model, system_text)
        now = time.time()
        with _CACHE_REGISTRY_LOCK:
            entry = _CACHE_REGISTRY.get(key)
            # Refresh ~10s before expiry so an in-flight request never lands
            # on a server-side cache that just rolled past its TTL.
            if entry and entry.get("expires_at", 0) > now + 10:
                return entry.get("name")

        # `model` must be the fully-qualified Gemini path "models/<id>".
        body: dict = {
            "model": f"models/{self.model}",
            "systemInstruction": {"parts": [{"text": system_text}]},
            "ttl": f"{int(ttl_seconds)}s",
        }
        url = f"{GEMINI_CACHE_URL}?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        client_timeout = httpx.Timeout(
            connect=2.0, read=self.timeout, write=2.0, pool=2.0
        )

        try:
            async with httpx.AsyncClient(timeout=client_timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                # Most common 4xx is "the request must contain at least N
                # tokens of cached content" — below the Gemini minimum the
                # cache simply isn't allowed. Log + return None so the caller
                # falls through to the uncached path.
                detail = ""
                try:
                    detail = resp.text[:300]
                except Exception:
                    pass
                logging.info(
                    "gemini.create_cache %s (model=%s): %s",
                    resp.status_code, self.model, detail,
                )
                return None
            payload = resp.json()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # noqa: BLE001 — fail-safe: any error = no cache
            logging.info(
                "gemini.create_cache raised %s (model=%s): %s",
                type(e).__name__, self.model, str(e)[:200],
            )
            return None

        cache_name = payload.get("name") or ""
        if not cache_name:
            return None

        with _CACHE_REGISTRY_LOCK:
            _CACHE_REGISTRY[key] = {
                "name": cache_name,
                # Store local expiry; the registered TTL is server-side
                # truth, but we shadow it locally so we self-evict before
                # the inevitable 4xx on an expired reference.
                "expires_at": time.time() + ttl_seconds,
            }
        logging.info(
            "gemini.create_cache OK (model=%s, name=%s, ttl=%ss)",
            self.model, cache_name, ttl_seconds,
        )
        return cache_name

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.6,
        max_tokens: int = 700,
        response_format: Optional[dict] = None,
        cached_content_name: Optional[str] = None,
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
        # When a cachedContents resource is in play, the entire systemInstruction
        # already lives inside it server-side; including it again in the request
        # body causes a 400 INVALID_ARGUMENT ("cached prompt and inline prompt
        # mutually exclusive"). Only attach systemInstruction on the uncached
        # path.
        if cached_content_name:
            body["cachedContent"] = cached_content_name
        elif system_instruction:
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
            # KI-199 — graceful fallback when a cache reference is stale.
            # Symptoms: 400/404 with body mentioning "cachedContent" /
            # "cache" / "not found". Strip the reference, re-add the inline
            # systemInstruction, drop the registry entry, retry once.
            if (
                cached_content_name
                and resp.status_code in (400, 403, 404)
                and any(
                    tok in (resp.text or "").lower()
                    for tok in ("cache", "cachedcontent")
                )
            ):
                logging.info(
                    "gemini.chat cache miss/invalid (%s) — retrying uncached (model=%s)",
                    resp.status_code, self.model,
                )
                # Best-effort invalidate by direct name match in the registry.
                with _CACHE_REGISTRY_LOCK:
                    for k, v in list(_CACHE_REGISTRY.items()):
                        if v.get("name") == cached_content_name:
                            _CACHE_REGISTRY.pop(k, None)
                body.pop("cachedContent", None)
                if system_instruction:
                    body["systemInstruction"] = {
                        "parts": [{"text": system_instruction}]
                    }
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


__all__ = [
    "GoogleGeminiLLM",
    "get_gemini_llm",
    "DEFAULT_MODEL",
    "invalidate_cache",
]
