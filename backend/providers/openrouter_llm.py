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


# A4 (2026-05-15) — OpenRouter free-pool helpers.
#
# `:free` suffix is OpenRouter's documented marker for zero-cost models.
# We use this both to:
#   (a) order the free pool deterministically (smallest-id-first as a
#       stable, cheapest-first proxy — small models are cheaper to serve
#       and complete faster); and
#   (b) reject any non-`:free` model that accidentally lands in the free
#       pool, so a paid model can't get silently invoked under a "free"
#       call path and burn the user's prepaid balance.
_FREE_SUFFIX = ":free"


def is_free_model(model_id: str) -> bool:
    """True if `model_id` is in OpenRouter's free pool (`:free` suffix).
    Used by `enforce_free_pool` + the chat-time cost guard.
    """
    return bool(model_id) and model_id.endswith(_FREE_SUFFIX)


def order_free_pool(models: list[str]) -> list[str]:
    """A4 (2026-05-15) — Return `models` in a STABLE, deterministic order
    suitable for OpenRouter's `models=[...]` server-side fallback list.

    Sort key: (length-of-id ascending, id ascending). Shorter ids tend to
    correlate with smaller / cheaper models on OpenRouter's catalogue
    (e.g. `gemma-4-26b-a4b-it:free` < `gemma-4-31b-it:free` < ...).
    Stable alphabetical secondary key removes the randomness that an
    unsorted dict iteration could introduce across Python runs.

    Inputs that contain non-free entries are NOT silently re-ordered —
    use `enforce_free_pool` first if you need the cost guard.
    """
    return sorted(models, key=lambda m: (len(m), m))


def enforce_free_pool(models: list[str]) -> list[str]:
    """A4 (2026-05-15) — Cost guard: reject paid models in a free-pool call.

    Raises ValueError listing any non-`:free` entries. Returns the
    deterministically-ordered free pool on success.
    """
    paid = [m for m in models if not is_free_model(m)]
    if paid:
        raise ValueError(
            f"OpenRouter free-pool call rejected: non-free models {paid}. "
            "All entries must end in ':free'. See get_openrouter_llm() docs."
        )
    return order_free_pool(models)


class OpenRouterLLM(LLMProvider):
    name = "openrouter"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        chain_name: str = "unknown",
    ):
        self.api_key = api_key or getattr(settings, "OPENROUTER_API_KEY", "")
        self.model = model
        self.timeout = timeout
        # KI-085 — chain_name plumbs through to update_credits_*.
        self.chain_name = chain_name
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
        models: Optional[list[str]] = None,
        free_only: bool = False,
    ) -> LLMResult:
        """Send a chat completion to OpenRouter.

        KI-176 — `models` (optional list of model ids) activates OpenRouter's
        native server-side fallback: the server tries each id in order and
        returns the first that succeeds. When provided, `models[0]` is also
        set as the primary `model` field (OpenRouter requires both for
        routing; if `models` is omitted the lone `model` field is used).
        Reference: https://openrouter.ai/docs/features/model-routing

        A4 (2026-05-15) — `free_only=True` activates the cost guard:
          - rejects any non-`:free` model in `models` (raises ValueError)
          - reorders the survivors via `order_free_pool` for stable, smaller-
            first preference. The single-model `self.model` is checked too
            when `models` is omitted, so a paid default can't sneak in via
            the lone-model path.
        """
        # A4 cost guard — enforce BEFORE constructing the request body so
        # a paid model never reaches the wire.
        if free_only:
            if models:
                models = enforce_free_pool(list(models))
            elif not is_free_model(self.model):
                raise ValueError(
                    f"OpenRouter free-pool call rejected: default model "
                    f"{self.model!r} is not in the :free pool. Pass "
                    f"`models=[...]` with :free suffix or unset free_only."
                )

        primary_model = models[0] if models else self.model
        body: dict = {
            "model": primary_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if models:
            body["models"] = list(models)
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
            # KI-085 (2026-05-15) — opportunistic between-poll signal from
            # OpenRouter response headers. The authoritative truth (usd_balance)
            # comes from poll_openrouter_credits() every 10 min; this is a
            # finer-grained per-call backup that catches per-minute bucket
            # exhaustion before the next poll tick.
            try:
                from backend import llm_health
                chain_for_credits = f"openrouter:{self.model}"
                llm_health.update_credits_from_openrouter_headers(
                    self.chain_name, chain_for_credits, dict(resp.headers)
                )
            except Exception:
                pass
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


# ----------------------------------------------------------------------------
# KI-176 — Factory for the OpenRouter free-tier frontier pool.
#
# User loaded $10 on OpenRouter; that unlocks 1000 req/day across all `:free`
# suffix models. This factory returns an OpenRouterLLM whose `model` field is
# a sane single-model default; callers that want server-side fallback should
# pass `models=[...]` to `chat()`.
#
# Model IDs verified against the LIVE OpenRouter catalog
# (GET /api/v1/models, 2026-05-15). The model IDs from the KI-176 brief
# (gemini-2.0-flash-exp:free, llama-3.3-70b-instruct:free,
# hermes-3-llama-3.1-405b:free) were checked:
#   - gemini-2.0-flash-exp:free                 — NOT in catalog
#   - llama-3.3-70b-instruct:free               — PRESENT but no response_format
#   - hermes-3-llama-3.1-405b:free              — PRESENT but no response_format
# So those would silently lose JSON mode. The IDs below are the actual
# free-tier set that declare `response_format` (and most also
# `structured_outputs`) in their `supported_parameters`:
#   - nvidia/nemotron-3-super-120b-a12b:free    (120B, structured_outputs)
#   - qwen/qwen3-next-80b-a3b-instruct:free     (80B,  structured_outputs)
#   - google/gemma-4-31b-it:free                (31B,  response_format)
#   - minimax/minimax-m2.5:free                 (?,    response_format)
#   - google/gemma-4-26b-a4b-it:free            (26B,  response_format)
# ----------------------------------------------------------------------------

# Default JSON-mode-capable free-tier model. Callers usually override this by
# passing `models=[...]` to chat() — this is just the lone-model fallback.
DEFAULT_FREE_BRAIN_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


def get_openrouter_llm(
    chain_name: str = "sales_brain",
    model: str = DEFAULT_FREE_BRAIN_MODEL,
    timeout: float = 60.0,
) -> "OpenRouterLLM":
    """Return a fresh OpenRouterLLM ready for sales_brain.

    The returned client is single-use-shaped (one chat() call per turn) and
    intended to be passed `models=[primary, fallback1, fallback2]` so the
    OpenRouter server handles in-pool failover before the caller falls back
    to the NIM chain.
    """
    return OpenRouterLLM(
        model=model,
        timeout=timeout,
        chain_name=chain_name,
    )
