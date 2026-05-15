"""Minimal NIM fallback for first-turn Gemini 503.

Replaces the 5,219 LOC legacy orchestrator stack. Does ONE thing: when
single_brain raises SingleBrainError on the first turn (no
single_brain_sticky yet), give the user a working reply using NIM.

No tools, no function-calling, no faithfulness gate, no normalizer —
just a single NIM chat call with a tiny system prompt. The user gets a
polite "let me think out loud while I reconnect" reply; the next turn,
single_brain (Gemini) takes over and the session sticks.

KI-155 / ADR-038: NIM-only. We elect via llm_health.get_primary("brain")
which walks BRAIN_CHAIN in priority order over election-eligible models.
If the elector has nothing eligible, we fall back to BRAIN_CHAIN[0] as a
last resort so the user still gets a reply.

Contract:
- Returns a single_brain.TurnResult so /api/chat treats the result
  identically to the happy path.
- Always returns — on NIM failure or timeout we synthesise a graceful
  reply (no exceptions escape).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from backend import single_brain
from backend.providers.base import ChatMessage
from backend.providers.nvidia_nim_llm import BRAIN_CHAIN, NvidiaNimLLM

try:
    # llm_health is the elector — its get_primary("brain") walks the chain
    # in priority order over election-eligible models. Imported lazily-safe
    # in case CLEAN3's edits transiently move the symbol.
    from backend import llm_health
except Exception:  # noqa: BLE001
    llm_health = None  # type: ignore[assignment]


_LOG = logging.getLogger(__name__)

# Devanagari unicode block (Hindi / Marathi / Sanskrit etc.). Used to tag
# `language` on the TurnResult for downstream logging — matches the
# coarse `indic` vs `en` distinction single_brain uses.
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

PROMPT = (
    "You are an Indian health-insurance advisor. The user just spoke to "
    "you and the primary system is briefly down. Reply in 1-2 sentences "
    "acknowledging what they said and asking them to repeat or rephrase "
    "so the main system can serve a proper recommendation. Keep it warm "
    "and brief. Indian context (use ₹, lakh, IRDAI). Do NOT name "
    "specific policies or insurers — you have no retrieval available."
)

# Graceful synthetic reply used when NIM itself fails / times out. Kept
# short and on-brand with the rest of the codebase's fallback strings.
_GRACEFUL_REPLY = (
    "Sorry, I'm having trouble — please try again in a moment."
)

# Outer per-call budget for the NIM round-trip. Tighter than the caller's
# 20s wait_for so we always emit our own TurnResult rather than letting
# the outer wrapper time out.
_NIM_TIMEOUT_S = 15.0


def _detect_language(text: str) -> str:
    """Coarse 2-bucket language tag matching single_brain conventions."""
    return "indic" if _DEVANAGARI_RE.search(text or "") else "en"


def _pick_model() -> str:
    """Elect the NIM brain model.

    Prefer llm_health.get_primary("brain") so this path participates in
    the same sticky-primary election as the rest of the stack. Fall
    through to BRAIN_CHAIN[0] if the elector is unavailable or has no
    eligible candidates — better to try the chain leader than to refuse
    the turn.
    """
    if llm_health is not None:
        try:
            elected = llm_health.get_primary("brain")
            if elected:
                return elected
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("nim_fallback: get_primary('brain') failed: %s", exc)
    # Last resort — chain leader.
    return BRAIN_CHAIN[0]


def _flatten_history(chat_history: Optional[list[dict]]) -> list[ChatMessage]:
    """Translate the orchestrator-style chat_history ({role, content})
    into the NIM `ChatMessage` shape. Roles other than user/assistant
    are dropped; assistant aliases collapse to `assistant`.
    """
    out: list[ChatMessage] = []
    for msg in chat_history or []:
        role = (msg.get("role") or "user").lower()
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role in ("assistant", "model", "bot"):
            out.append(ChatMessage(role="assistant", content=content))
        else:
            out.append(ChatMessage(role="user", content=content))
    return out


async def _nim_chat(
    model: str,
    chat_history: Optional[list[dict]],
    user_text: str,
) -> str:
    """Single NIM chat call. Raises on any provider error so the caller
    can fall through to the graceful synthetic reply."""
    client = NvidiaNimLLM(model=model, timeout=_NIM_TIMEOUT_S)
    messages: list[ChatMessage] = [ChatMessage(role="system", content=PROMPT)]
    messages.extend(_flatten_history(chat_history))
    messages.append(ChatMessage(role="user", content=user_text))
    result = await client.chat(
        messages,
        temperature=0.4,
        max_tokens=400,
    )
    return (result.text or "").strip()


async def handle_turn_fallback(
    session,
    user_text: str,
    chat_history: Optional[list[dict]] = None,
) -> single_brain.TurnResult:
    """First-turn fallback when single_brain (Gemini) raises.

    Returns a TurnResult with the NIM reply, or a graceful synthetic
    reply if NIM itself fails / times out. Never raises.
    """
    t0 = time.time()
    model = _pick_model()
    brain_used = f"nim_fallback::{model}"
    language = _detect_language(user_text)

    try:
        reply_text = await asyncio.wait_for(
            _nim_chat(model, chat_history, user_text),
            timeout=_NIM_TIMEOUT_S,
        )
        if not reply_text:
            # Model returned empty — treat as failure and fall through.
            raise RuntimeError("empty reply from NIM")
    except asyncio.TimeoutError:
        _LOG.warning(
            "nim_fallback: NIM call timed out after %.1fs (model=%s)",
            _NIM_TIMEOUT_S, model,
        )
        reply_text = _GRACEFUL_REPLY
        brain_used = f"nim_fallback::timeout::{model}"
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "nim_fallback: NIM call failed (model=%s): %s", model, exc,
        )
        reply_text = _GRACEFUL_REPLY
        brain_used = f"nim_fallback::error::{model}"

    latency_ms = int((time.time() - t0) * 1000)
    return single_brain.TurnResult(
        reply_text=reply_text,
        citations=[],
        retrieved_chunk_ids=[],
        brain_used=brain_used,
        intent="qa",
        language=language,
        latency_ms=latency_ms,
        raw_reply=reply_text,
        faithfulness_passed=True,
        faithfulness_reasons=[],
        blocked=False,
        profile_updates={},
        followup_policy_id=None,
    )


__all__ = ["handle_turn_fallback", "PROMPT"]
