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
DEFAULT_MODEL = "gemini-2.5-flash"  # KI-183 retired gemini-2.0-flash → 2.5-flash (free-tier, brain-grade — see header). NOT the -lite tier: it silently broke single-brain tool-calling (save_profile_field) and caused the live fact-find regression.

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
_CACHE_REGISTRY: dict[tuple[str, str, str], dict] = {}
_CACHE_REGISTRY_LOCK = threading.Lock()

# A4 (2026-05-15) — Gemini cachedContents server-side TTL ceiling. Google's
# `cachedContents` resources live up to ~60min on free tier; we refresh
# before that ceiling so an in-flight call never lands on an expired cache.
# `CACHE_REFRESH_AGE_SEC` is the wall-clock age at which we proactively
# re-create even if local `expires_at` has not yet elapsed — keeps us safely
# below the server-side TTL drift window observed in production.
CACHE_REFRESH_AGE_SEC = 50 * 60  # 50min — refresh BEFORE 60min server ceiling


def _cache_key(model: str, system_text: str, dynamic_prefix: str = "") -> tuple[str, str, str]:
    """Build the registry key for a (model, system_text, dynamic_prefix) tuple.

    A4 (2026-05-15) — Cache key collision fix: SHA256 of preamble alone is
    insufficient when `_dynamic_profile_block` varies per persona. The key
    now partitions on a separate `dynamic_prefix` hash so per-persona
    caches don't collide on the same static preamble hash. `dynamic_prefix`
    defaults to "" so existing callers retain prior behaviour.

    Hashing inputs rather than storing the raw string keeps the registry
    footprint tiny even when the preamble is multi-KB.
    """
    static_h = hashlib.sha256(system_text.encode("utf-8")).hexdigest()
    dyn_h = hashlib.sha256(dynamic_prefix.encode("utf-8")).hexdigest() if dynamic_prefix else ""
    return (model, static_h, dyn_h)


# ---------------------------------------------------------------------------
# A4 (2026-05-15) — Normalized provider error class.
#
# Gemini's REST surface returns different error shapes for different failure
# modes (429 rate-limit, 400 BlockedReason / SafetyRating, 404 cache not
# found, 500/503 server errors). The tier wrapper needs a stable contract:
# `BrainProviderError(retryable=bool)` where `retryable=True` signals the
# tier should fall through to the next provider, and `retryable=False`
# signals a hard error (auth / content blocked) that should surface to the
# caller without burning fallback budget.
# ---------------------------------------------------------------------------
class BrainProviderError(RuntimeError):
    """Stable provider-error envelope consumed by TieredBrainLLM.

    Attributes:
      provider:   short name ("gemini" / "nim" / ...)
      retryable:  True if the tier wrapper should try the next provider.
                  False for auth / content-block / non-recoverable errors.
      status:     HTTP-like status code if applicable, else None.
      raw:        the original exception (kept as __cause__ via `raise from`).
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "gemini",
        retryable: bool = True,
        status: Optional[int] = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status = status


def _classify_gemini_error(status_code: int, detail: str) -> BrainProviderError:
    """Map a Gemini REST response into BrainProviderError(retryable=bool).

    Routing rules (matches TieredBrainLLM expectations):
      - 429 (rate-limit / quota)              → retryable (next tier)
      - 5xx (server errors)                   → retryable (next tier)
      - 408 / 504 (timeout)                   → retryable
      - 401 / 403 (auth, key revoked)         → NOT retryable (surface)
      - 400 with BlockedReason / SafetyRating → NOT retryable (content block)
      - 400 other (malformed request)         → NOT retryable (caller bug)
      - 404 cachedContent                     → retryable (cache lapsed,
                                                  uncached retry already wired
                                                  in chat() but tier-level
                                                  fallback is still safe).
    """
    detail_l = (detail or "").lower()
    retryable = False
    if status_code == 429:
        retryable = True
    elif 500 <= status_code < 600:
        retryable = True
    elif status_code in (408, 504):
        retryable = True
    elif status_code == 404 and ("cache" in detail_l or "cachedcontent" in detail_l):
        retryable = True
    elif status_code == 400 and (
        "blocked" in detail_l or "safety" in detail_l or "blockreason" in detail_l
    ):
        retryable = False
    elif status_code in (401, 403):
        retryable = False
    return BrainProviderError(
        f"Gemini API {status_code}: {detail[:300]}",
        provider="gemini",
        retryable=retryable,
        status=status_code,
    )


def invalidate_cache(model: str, system_text: str, dynamic_prefix: str = "") -> None:
    """Drop a cache registry entry — called by upstream after a 4xx response
    that names a stale `cachedContent`. The server-side cache may still be
    alive (it will lapse on TTL), but our reference is gone so the next
    chat() call provisions a fresh one.

    A4 (2026-05-15) — `dynamic_prefix` is an optional partition arg; defaults
    to "" so existing callers retain prior behaviour. When supplied, only the
    matching (model, system_text, dynamic_prefix) entry is dropped.
    """
    key = _cache_key(model, system_text, dynamic_prefix)
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
        dynamic_prefix: str = "",
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

        key = _cache_key(self.model, system_text, dynamic_prefix)
        now = time.time()
        with _CACHE_REGISTRY_LOCK:
            entry = _CACHE_REGISTRY.get(key)
            # A4 (2026-05-15) — TWO-stage refresh:
            #   (a) self-evict ~10s before LOCAL expires_at (was already here).
            #   (b) PROACTIVELY refresh if the entry is older than
            #       CACHE_REFRESH_AGE_SEC (50min) regardless of expires_at —
            #       guards against server-side TTL drift on long-lived
            #       caches and keeps every entry well below the ~60min
            #       cachedContents ceiling.
            if entry:
                created_at = entry.get("created_at", 0)
                age = now - created_at if created_at else 0
                if (
                    entry.get("expires_at", 0) > now + 10
                    and age < CACHE_REFRESH_AGE_SEC
                ):
                    return entry.get("name")
                # else: fall through and recreate

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
            now_create = time.time()
            _CACHE_REGISTRY[key] = {
                "name": cache_name,
                # Store local expiry; the registered TTL is server-side
                # truth, but we shadow it locally so we self-evict before
                # the inevitable 4xx on an expired reference.
                "expires_at": now_create + ttl_seconds,
                # A4 (2026-05-15) — created_at lets us proactively refresh
                # entries that have lived past CACHE_REFRESH_AGE_SEC even
                # when caller set a longer TTL than Google honours.
                "created_at": now_create,
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
        #
        # A4 (2026-05-15) — Timeout binding: bind the SDK/httpx read timeout
        # to `self.timeout - 2.0` so the underlying connection times out
        # ~2s BEFORE the tier-wrapper's outer wait_for cancellation. This
        # surfaces a clean BrainProviderError(retryable=True) instead of
        # asyncio.CancelledError leaking up through the cancellation chain
        # (which the tier wrapper has historically misclassified).
        read_timeout = max(2.0, self.timeout - 2.0)
        client_timeout = httpx.Timeout(
            connect=2.0,
            read=read_timeout,
            write=2.0,
            pool=2.0,
        )

        async with httpx.AsyncClient(timeout=client_timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=body)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except httpx.TimeoutException as e:
                # A4 (2026-05-15) — TimeoutException → retryable
                # BrainProviderError so the tier wrapper falls through
                # cleanly instead of seeing asyncio.CancelledError.
                raise BrainProviderError(
                    f"Gemini timeout after {read_timeout:.1f}s (model={self.model})",
                    provider="gemini", retryable=True, status=None,
                ) from e
            except httpx.HTTPError as e:
                # Network / DNS / connection-refused etc. — retryable.
                raise BrainProviderError(
                    f"Gemini transport error ({type(e).__name__}): {str(e)[:200]}",
                    provider="gemini", retryable=True, status=None,
                ) from e
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
                try:
                    resp = await client.post(url, headers=headers, json=body)
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except httpx.TimeoutException as e:
                    raise BrainProviderError(
                        f"Gemini retry timeout after {read_timeout:.1f}s (model={self.model})",
                        provider="gemini", retryable=True, status=None,
                    ) from e
                except httpx.HTTPError as e:
                    raise BrainProviderError(
                        f"Gemini retry transport error ({type(e).__name__}): {str(e)[:200]}",
                        provider="gemini", retryable=True, status=None,
                    ) from e
            if resp.status_code >= 400:
                # A4 (2026-05-15) — Normalize the error into BrainProviderError
                # so the tier wrapper sees a stable contract:
                #   retryable=True  → fall through to next tier
                #   retryable=False → surface to caller (auth/content-block)
                # The original httpx.HTTPStatusError is preserved as __cause__
                # so logs still carry the full upstream trail.
                detail = ""
                try:
                    detail = resp.text[:500]
                except Exception:
                    pass
                upstream_err = httpx.HTTPStatusError(
                    f"Gemini API {resp.status_code}: {detail}",
                    request=resp.request,
                    response=resp,
                )
                normalized = _classify_gemini_error(resp.status_code, detail)
                raise normalized from upstream_err
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
    "BrainProviderError",
    "CACHE_REFRESH_AGE_SEC",
]
