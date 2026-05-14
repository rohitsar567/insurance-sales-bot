"""Translation helpers — Sarvam-M as the Indic specialist for query/answer
translation in the cascade routing pattern.

The cascade pattern for Hindi/Hinglish queries:
  1. Sarvam-M translates the user's Hinglish query → clean English
  2. DeepSeek-V3 reasons over the retrieved policy chunks → English answer
     (with full citation grammar preserved)
  3. Sarvam-M translates the English answer → natural Hinglish

Why this is better than either model alone:
  - Sarvam-M has the best Indic comprehension + cultural context but mid-tier
    English reasoning. Don't rely on it for the reasoning step.
  - DeepSeek-V3 has SOTA open-source reasoning but is English-trained.
    Don't rely on it for the Indic understanding/generation step.
  - The cascade gets the best of both — at a +3s latency cost.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from backend.providers.base import ChatMessage
from backend.providers.sarvam_llm import SarvamLLM


# KI-099 — outer per-call wait_for. Even with KI-099's split httpx.Timeout
# in sarvam_llm.py, an outer asyncio.wait_for guarantees the orchestrator
# slot can't be held by a wedged Sarvam call past this ceiling. 20s
# matches the inner read timeout — if httpx hasn't returned by then,
# something is genuinely wedged.
_SARVAM_CALL_TIMEOUT = 20.0


# KI-110 (2026-05-15) — translator contract: NEVER raise on a Sarvam failure.
# Callers (orchestrator inbound translate, cascade outbound translate,
# translation_check back-translate) all degrade gracefully when passthrough
# text is returned, but any exception escaping translate_to_english /
# translate_to_indic is a P0: live re-smoke verified that EVERY Hindi /
# Devanagari turn surfaced `brain_used=error_fallback` from KI-106's catch-all
# because the translator could raise non-TimeoutError exceptions and the
# catch only handled `asyncio.TimeoutError`.
#
# Specifically, post-KI-099 the inner httpx.Timeout(read=20.0) races with the
# outer asyncio.wait_for(timeout=20.0). When httpx fires first it raises
# `httpx.ReadTimeout` (NOT a subclass of asyncio.TimeoutError); on Sarvam 4xx
# or 5xx (rate-limit on Indic-heavy load, auth issues, server hiccups) it
# raises `httpx.HTTPStatusError`; on malformed payload it raises KeyError on
# `payload["choices"][0]`. None were caught.
#
# Fix: catch every Sarvam-originated exception type explicitly. We do NOT
# catch `asyncio.CancelledError` (would break task cancellation), and we do
# NOT use a bare `except Exception` (would hide real coding bugs).
_TRANSLATOR_FAILURE_TYPES: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    httpx.TimeoutException,  # ConnectTimeout / ReadTimeout / WriteTimeout / PoolTimeout
    httpx.HTTPStatusError,   # 4xx/5xx from Sarvam
    httpx.RequestError,      # network / DNS / SSL / connect errors
    KeyError,                # malformed Sarvam payload (missing 'choices')
    ValueError,              # malformed JSON / dataclass construction
)

_log = logging.getLogger(__name__)


_TRANSLATE_TO_EN_SYSTEM = """You are a precise translator from Hindi / Hinglish / code-switched Indian English to clean standard English.

RULES:
1. Output ONLY the translated English sentence. No preamble, no quotes, no explanation.
2. Preserve insurance/finance/medical terms exactly as in the source.
3. Names of insurers (Star Health, HDFC ERGO, Niva Bupa, Care, ICICI Lombard, Bajaj Allianz, Aditya Birla, etc.) and policy product names must remain unchanged.
4. Numbers, currencies (₹, lakh, crore), durations (din/months/years) stay numeric — convert "do saal" → "2 years"; "tees din" → "30 days".
5. If the source is already English, return it as-is unchanged."""


_TRANSLATE_TO_INDIC_SYSTEM = """You are a precise translator from English to natural conversational Hindi / Hinglish (code-switched Indian English) — the way a buyer in urban India actually speaks.

RULES:
1. Output ONLY the translated text. No preamble, no quotes.
2. Use Devanagari for Hindi words, English for English words that are commonly kept in English in spoken Hindi (insurance, policy, coverage, premium, hospital, claim, network, copay, etc.).
3. Names of insurers and policies stay unchanged.
4. Numbers stay numeric.
5. Keep the tone conversational + warm, like an experienced advisor speaking to a friend.
6. Maximum 60 words; if the source is longer, condense to the key facts. Do not invent details.

Example:
  English: "Yes, HDFC ERGO Optima Secure covers Ayurveda treatment at recognized AYUSH hospitals. The waiting period is 30 days from policy start."
  Hinglish: "Haan, HDFC ERGO Optima Secure mein Ayurveda treatment cover hai — but sirf recognized AYUSH hospitals mein. Policy shuru hone se 30 din ka waiting period rahega."
"""


async def translate_to_english(text: str, sarvam: SarvamLLM | None = None) -> str:
    """Translate a Hinglish/Hindi query into clean English for the reasoning brain."""
    if not text.strip():
        return text
    sarvam = sarvam or SarvamLLM()
    try:
        # KI-099 — outer wait_for caps end-to-end Sarvam latency at 20s even
        # if httpx's inner read-timeout fails to fire. On timeout we
        # passthrough the original text — callers in orchestrator.py /
        # translation_check.py already tolerate the original text (they
        # log + degrade gracefully, see KI-004 path).
        res = await asyncio.wait_for(
            sarvam.chat(
                messages=[
                    ChatMessage(role="system", content=_TRANSLATE_TO_EN_SYSTEM),
                    ChatMessage(role="user", content=text),
                ],
                temperature=0.0,
                max_tokens=400,
            ),
            timeout=_SARVAM_CALL_TIMEOUT,
        )
    except _TRANSLATOR_FAILURE_TYPES as e:
        # KI-110 — passthrough on ANY Sarvam failure (timeout, http error,
        # network error, malformed payload). The translator contract is
        # "never raise" so callers don't see this as a chat-killing
        # exception. Log with type-name so we can still observe Sarvam health.
        _log.warning(
            "sarvam translate_to_english passthrough (%s): %s",
            type(e).__name__, str(e)[:200],
        )
        return text
    out = res.text.strip()
    # Strip <think> tags if Sarvam-M went into reasoning mode
    from backend.persona import strip_think_tags
    return strip_think_tags(out) or text


async def translate_to_indic(
    english: str,
    target_lang: str = "hi-IN",
    sarvam: SarvamLLM | None = None,
) -> str:
    """Translate an English answer back into natural Hinglish for the user.

    Preserves any [Source: ...] citation tags so the faithfulness gate can
    still verify the citation chain after translation.
    """
    if not english.strip():
        return english
    sarvam = sarvam or SarvamLLM()
    try:
        # KI-099 — see translate_to_english for rationale. On timeout we
        # passthrough the English; orchestrator.py treats an empty/equal
        # Indic reply as "no cascade translation" and falls back to the
        # English reply unchanged (existing behaviour for empty reply_indic).
        res = await asyncio.wait_for(
            sarvam.chat(
                messages=[
                    ChatMessage(role="system", content=_TRANSLATE_TO_INDIC_SYSTEM),
                    ChatMessage(role="user", content=english),
                ],
                temperature=0.2,
                max_tokens=600,
            ),
            timeout=_SARVAM_CALL_TIMEOUT,
        )
    except _TRANSLATOR_FAILURE_TYPES as e:
        # KI-110 — see translate_to_english for full rationale. Passthrough
        # the English on any Sarvam failure; orchestrator's indic cascade
        # treats English-back == English-input as "no cascade translation"
        # and serves the English reply unchanged.
        _log.warning(
            "sarvam translate_to_indic passthrough (%s): %s",
            type(e).__name__, str(e)[:200],
        )
        return english
    out = res.text.strip()
    from backend.persona import strip_think_tags
    return strip_think_tags(out) or english
