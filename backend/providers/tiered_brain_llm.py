"""TieredBrainLLM — 3-tier wrapper for the orchestrator's brain main path (KI-179).

Architecture:

    Tier 0 — Google Gemini Flash       (best free quality, 1500 req/day)
    Tier 1 — NIM brain chain           (NimChainLLM, multi-candidate election)
    Tier 2 — OpenRouter free pool      (cross-provider safety net)

Why a wrapper class instead of inlining the tiers in orchestrator.py?

The orchestrator's brain main path is one of the busiest call-sites in the
system — faithfulness gates, cross-check retry, drift recovery, eval logging
all hang off it. Inlining a 3-tier try/except cascade there would explode
the cyclomatic complexity of an already-long function. Wrapping the
3-tier dance behind the SAME `LLMProvider.chat()` signature as `NimChainLLM`
lets us swap in the wrapper at the construction site with zero downstream
changes — the existing `await pick.provider.chat(...)` call shape is
preserved.

Match the existing `LLMProvider` interface so this drops into the same
slot the orchestrator currently fills with `get_brain_llm()` /
`get_fast_brain_llm()`. `self.model` and `self.name` are updated after a
successful call to reflect which tier actually served — preserves the
KI-080 audit trail.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from backend.providers.base import ChatMessage, LLMProvider, LLMResult
from backend.providers.google_gemini_llm import get_gemini_llm
from backend.providers.nvidia_nim_llm import (
    get_brain_llm,
    get_fast_brain_llm,
)
from backend.providers.openrouter_llm import get_openrouter_llm


# Mirrors sales_brain.py's free-tier pool: every model declares
# response_format in its supported_parameters so the OR server's native
# fallback walks an JSON-mode-aware list.
_OR_MODELS: list[str] = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-4-31b-it:free",
]


class TieredBrainLLM(LLMProvider):
    """3-tier brain wrapper: Gemini → NIM → OpenRouter.

    `role` is one of "brain" | "fast_brain" — controls which NIM chain
    factory is used for Tier 1:
      - "brain"      → get_brain_llm()      (BRAIN_CHAIN, 20s per-link)
      - "fast_brain" → get_fast_brain_llm() (FAST_BRAIN_CHAIN, 6s per-link)

    `gemini_model` defaults to "gemini-2.5-flash" for the heavy brain (slightly
    higher quality on complex synthesis) and can be overridden to
    "gemini-2.0-flash" for the fast brain (lower latency).

    `per_tier_timeout` is the outer asyncio.wait_for cap applied to EACH
    tier individually. Mirrors the 30s budget the orchestrator's existing
    brain call uses (see orchestrator.py KI-098).
    """

    name = "tiered-brain"

    def __init__(
        self,
        role: str = "brain",
        gemini_model: str = "gemini-2.5-flash",
        per_tier_timeout: float = 30.0,
    ):
        if role not in ("brain", "fast_brain"):
            raise ValueError(f"role must be 'brain' or 'fast_brain', got {role!r}")
        self.role = role
        self.gemini_model = gemini_model
        self.per_tier_timeout = per_tier_timeout
        # Start with the Tier 0 model as the "current" model — `chat()`
        # rewrites this to whichever tier actually served the request.
        self.model = gemini_model
        self.name = f"tiered-brain::{role}::gemini:{gemini_model}"

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
        cached_content_name: Optional[str] = None,
        **kwargs,  # absorb provider-specific kwargs (e.g. OR's `models=[...]`)
    ) -> LLMResult:
        """KI-199 — `cached_content_name`, when supplied by the caller, is
        forwarded to the Gemini tier ONLY. NIM and OpenRouter don't speak
        Gemini's cachedContents protocol so we silently drop the reference on
        those tiers — they receive the full messages list as before, which
        keeps the wire shape correct on fall-through.
        """
        gemini_exc: Optional[BaseException] = None
        nim_exc: Optional[BaseException] = None
        or_exc: Optional[BaseException] = None

        # ---- Tier 0 — Google Gemini ----
        try:
            gemini_llm = get_gemini_llm(model=self.gemini_model, timeout=25.0)
            result = await asyncio.wait_for(
                gemini_llm.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    cached_content_name=cached_content_name,
                ),
                timeout=self.per_tier_timeout,
            )
            text = (result.text or "").strip()
            if text:
                # Stamp the actually-served model so faithfulness can exclude it
                # from the judge chain (cross-family-grading invariant).
                self.model = result.model or self.gemini_model
                self.name = f"tiered-brain::{self.role}::gemini:{self.model}"
                return result
            # Empty text from Gemini → treat as failure, fall through.
            logging.info(
                "TieredBrainLLM[%s]: Gemini returned empty text, falling to NIM",
                self.role,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except asyncio.TimeoutError as e:
            gemini_exc = e
            logging.info(
                "TieredBrainLLM[%s]: Gemini timed out, falling to NIM",
                self.role,
            )
        except Exception as e:  # noqa: BLE001 — fall through on ANY provider error
            gemini_exc = e
            logging.info(
                "TieredBrainLLM[%s]: Gemini raised %s, falling to NIM: %s",
                self.role, type(e).__name__, str(e)[:200],
            )

        # ---- Tier 1 — NIM chain ----
        try:
            nim_llm = (
                get_brain_llm() if self.role == "brain" else get_fast_brain_llm()
            )
            result = await asyncio.wait_for(
                nim_llm.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                ),
                timeout=self.per_tier_timeout,
            )
            text = (result.text or "").strip()
            if text:
                self.model = result.model or getattr(nim_llm, "model", "nim")
                self.name = f"tiered-brain::{self.role}::nim:{self.model}"
                return result
            logging.info(
                "TieredBrainLLM[%s]: NIM returned empty text, falling to OpenRouter",
                self.role,
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except asyncio.TimeoutError as e:
            nim_exc = e
            logging.info(
                "TieredBrainLLM[%s]: NIM timed out, falling to OpenRouter",
                self.role,
            )
        except Exception as e:  # noqa: BLE001
            nim_exc = e
            logging.info(
                "TieredBrainLLM[%s]: NIM raised %s, falling to OpenRouter: %s",
                self.role, type(e).__name__, str(e)[:200],
            )

        # ---- Tier 2 — OpenRouter free pool ----
        try:
            or_llm = get_openrouter_llm(chain_name=f"orchestrator_{self.role}")
            result = await asyncio.wait_for(
                or_llm.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    models=_OR_MODELS,
                ),
                timeout=self.per_tier_timeout,
            )
            self.model = result.model or "openrouter"
            self.name = f"tiered-brain::{self.role}::or:{self.model}"
            return result
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # noqa: BLE001
            or_exc = e

        # All three tiers failed — raise a single combined RuntimeError so
        # the orchestrator's existing outer-error handling treats it as a
        # standard brain failure (it already handles `pick.provider.chat`
        # raising — see the wait_for cap at orchestrator.py KI-098).
        raise RuntimeError(
            f"TieredBrainLLM[{self.role}] all tiers failed: "
            f"gemini={type(gemini_exc).__name__ if gemini_exc else 'n/a'}, "
            f"nim={type(nim_exc).__name__ if nim_exc else 'n/a'}, "
            f"or={type(or_exc).__name__ if or_exc else 'n/a'}"
        ) from or_exc


def get_tiered_brain_llm(
    role: str = "brain",
    gemini_model: Optional[str] = None,
) -> TieredBrainLLM:
    """Factory for the orchestrator's brain main path.

    Defaults:
      role="brain"        → gemini-2.5-flash + BRAIN_CHAIN  (heavy synthesis)
      role="fast_brain"   → gemini-2.5-flash-lite + FAST_BRAIN_CHAIN (voice latency)

    KI-183 (2026-05-15) — gemini-2.0-flash retired for new accounts;
    gemini-2.5-flash-lite is the replacement (faster, cleaner JSON).
    """
    if gemini_model is None:
        gemini_model = (
            "gemini-2.5-flash" if role == "brain" else "gemini-2.5-flash-lite"
        )
    return TieredBrainLLM(role=role, gemini_model=gemini_model)


__all__ = ["TieredBrainLLM", "get_tiered_brain_llm"]
