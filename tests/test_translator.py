"""Regression tests for backend.translator — KI-110.

Locks in the translator contract: NEVER raise on a Sarvam failure. Live
re-smoke (2026-05-15) caught every Hindi turn in Scenario S2 surfacing
`brain_used=error_fallback` because the post-KI-099 try/except only caught
`asyncio.TimeoutError` — leaving httpx.ReadTimeout, httpx.HTTPStatusError,
httpx.RequestError, and KeyError (malformed Sarvam payload) to propagate
through to KI-106's catch-all in main.py.

These tests assert: every Sarvam failure mode returns the original text
(passthrough), not raises. Mirrors the existing project convention of
asyncio.run inside unittest.TestCase (no pytest-asyncio dependency).
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock

import httpx

# Ensure SARVAM_API_KEY is set so SarvamLLM constructor doesn't reject
os.environ.setdefault("SARVAM_API_KEY", "test-key-stub")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.providers.base import LLMResult  # noqa: E402
from backend.providers.sarvam_llm import SarvamLLM  # noqa: E402
from backend.translator import translate_to_english, translate_to_indic  # noqa: E402


HINDI_QUERY = "मुझे स्वास्थ्य बीमा चाहिए"
ENGLISH_REPLY = "Yes, HDFC ERGO Optima Secure covers Ayurveda treatment."


def _make_request() -> httpx.Request:
    """httpx exceptions need a Request; construct a minimal one."""
    return httpx.Request("POST", "https://api.sarvam.ai/v1/chat/completions")


def _stub_sarvam(side_effect=None, return_value=None) -> SarvamLLM:
    fake = SarvamLLM(api_key="stub")
    if side_effect is not None:
        fake.chat = AsyncMock(side_effect=side_effect)  # type: ignore[method-assign]
    else:
        fake.chat = AsyncMock(return_value=return_value)  # type: ignore[method-assign]
    return fake


class TranslateToEnglishTests(unittest.TestCase):
    def test_happy_path(self):
        fake = _stub_sarvam(return_value=LLMResult(
            text="I want health insurance",
            model="sarvam-m",
        ))
        out = asyncio.run(translate_to_english(HINDI_QUERY, sarvam=fake))
        self.assertEqual(out, "I want health insurance")

    def test_passthrough_on_asyncio_timeout(self):
        """KI-099 baseline: outer wait_for fires → passthrough original Hindi."""
        fake = _stub_sarvam(side_effect=asyncio.TimeoutError())
        out = asyncio.run(translate_to_english(HINDI_QUERY, sarvam=fake))
        self.assertEqual(out, HINDI_QUERY)

    def test_passthrough_on_httpx_read_timeout(self):
        """KI-110 P0: httpx.ReadTimeout (NOT subclass of asyncio.TimeoutError) — passthrough."""
        fake = _stub_sarvam(side_effect=httpx.ReadTimeout(
            "Read timeout", request=_make_request(),
        ))
        out = asyncio.run(translate_to_english(HINDI_QUERY, sarvam=fake))
        self.assertEqual(out, HINDI_QUERY)

    def test_passthrough_on_httpx_connect_error(self):
        """KI-110: Sarvam unreachable — passthrough."""
        fake = _stub_sarvam(side_effect=httpx.ConnectError(
            "Connection refused", request=_make_request(),
        ))
        out = asyncio.run(translate_to_english(HINDI_QUERY, sarvam=fake))
        self.assertEqual(out, HINDI_QUERY)

    def test_passthrough_on_http_status_error(self):
        """KI-110: Sarvam 429 (rate-limit on Indic-heavy load) — passthrough."""
        req = _make_request()
        resp = httpx.Response(status_code=429, request=req)
        fake = _stub_sarvam(side_effect=httpx.HTTPStatusError(
            "Too Many Requests", request=req, response=resp,
        ))
        out = asyncio.run(translate_to_english(HINDI_QUERY, sarvam=fake))
        self.assertEqual(out, HINDI_QUERY)

    def test_passthrough_on_malformed_payload(self):
        """KI-110: Sarvam returns malformed payload (no 'choices' key) — passthrough."""
        fake = _stub_sarvam(side_effect=KeyError("choices"))
        out = asyncio.run(translate_to_english(HINDI_QUERY, sarvam=fake))
        self.assertEqual(out, HINDI_QUERY)

    def test_empty_input_no_sarvam_call(self):
        """Empty input: returns immediately, no Sarvam call."""
        self.assertEqual(asyncio.run(translate_to_english("")), "")
        self.assertEqual(asyncio.run(translate_to_english("   ")), "   ")

    def test_cancelled_error_propagates(self):
        """CRITICAL: asyncio.CancelledError MUST propagate — never swallow it,
        otherwise outer wait_for / task cancellation breaks."""
        fake = _stub_sarvam(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(translate_to_english(HINDI_QUERY, sarvam=fake))


class TranslateToIndicTests(unittest.TestCase):
    def test_happy_path(self):
        fake = _stub_sarvam(return_value=LLMResult(
            text="Haan, HDFC ERGO Optima Secure mein Ayurveda cover hai.",
            model="sarvam-m",
        ))
        out = asyncio.run(translate_to_indic(ENGLISH_REPLY, sarvam=fake))
        self.assertIn("HDFC", out)

    def test_passthrough_on_httpx_read_timeout(self):
        fake = _stub_sarvam(side_effect=httpx.ReadTimeout(
            "Read timeout", request=_make_request(),
        ))
        out = asyncio.run(translate_to_indic(ENGLISH_REPLY, sarvam=fake))
        self.assertEqual(out, ENGLISH_REPLY)

    def test_passthrough_on_http_status_error(self):
        req = _make_request()
        resp = httpx.Response(status_code=500, request=req)
        fake = _stub_sarvam(side_effect=httpx.HTTPStatusError(
            "Server Error", request=req, response=resp,
        ))
        out = asyncio.run(translate_to_indic(ENGLISH_REPLY, sarvam=fake))
        self.assertEqual(out, ENGLISH_REPLY)


if __name__ == "__main__":
    unittest.main()
