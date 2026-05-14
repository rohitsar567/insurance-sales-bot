"""KI-085 (2026-05-15) — proactive credit tracking + election gate.

Tests:
  1. update_credits_from_groq parser variants (valid / threshold / missing / malformed)
  2. NIM local rate-meter increments + 60s reset
  3. _is_election_eligible gates by credits_remaining vs credits_low_water
  4. elect_primary_and_backup integration: credit-exhausted candidates skipped

Run:
    .venv/bin/python -m unittest tests.test_credits_election -v
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from backend import llm_health
from backend.llm_health import (
    GROQ_TOKENS_LOW_WATER,
    ModelHealth,
    NIM_REQ_PER_MIN_LOW_WATER,
    _has_credits,
    _is_election_eligible,
    record_nim_call,
    update_credits_from_groq,
)


def _fresh_state():
    """Wipe in-memory state + mark loaded so tests don't trigger disk reads."""
    llm_health._STATE.clear()
    llm_health._STATE_LOADED = True
    llm_health._NIM_CALL_TIMES.clear()


def _healthy_now(model: str) -> ModelHealth:
    """Build a ModelHealth that passes every check EXCEPT credits — so
    flipping credits is the only failure surface in these tests."""
    h = ModelHealth(model=model)
    h.status = "healthy"
    h.latency_ms = 200
    h.tested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return h


class TestGroqHeaderParser(unittest.TestCase):
    def setUp(self) -> None:
        _fresh_state()

    def test_546_tokens_remaining_gates_out(self) -> None:
        """546 < 5000 low water → credit-gate fails."""
        model = "groq:llama-3.3-70b-versatile"
        update_credits_from_groq("brain", model, {
            "x-ratelimit-remaining-tokens-day": "546",
            "x-ratelimit-reset-tokens-day": "1h2m",
        })
        h = llm_health._STATE[model]
        self.assertEqual(h.credits_remaining, 546.0)
        self.assertEqual(h.credits_unit, "tokens_day")
        self.assertEqual(h.credits_low_water, GROQ_TOKENS_LOW_WATER)
        self.assertFalse(_has_credits(h, time.monotonic()))

    def test_50000_tokens_remaining_is_eligible(self) -> None:
        """50K >> 5K low water → credit-gate passes."""
        model = "groq:llama-3.3-70b-versatile"
        update_credits_from_groq("brain", model, {
            "x-ratelimit-remaining-tokens-day": "50000",
            "x-ratelimit-reset-tokens-day": "5h",
        })
        h = llm_health._STATE[model]
        self.assertEqual(h.credits_remaining, 50000.0)
        self.assertTrue(_has_credits(h, time.monotonic()))

    def test_missing_header_is_noop(self) -> None:
        """No daily-tokens header → state untouched (credits_remaining stays None)."""
        model = "groq:llama-3.3-70b-versatile"
        update_credits_from_groq("brain", model, {})
        self.assertNotIn(model, llm_health._STATE)

    def test_malformed_value_is_noop(self) -> None:
        """Garbage in the header → no-op + warning logged (not raised)."""
        model = "groq:llama-3.3-70b-versatile"
        update_credits_from_groq("brain", model, {
            "x-ratelimit-remaining-tokens-day": "not-a-number",
        })
        # State remains absent because update bailed before stamping.
        self.assertNotIn(model, llm_health._STATE)

    def test_reset_string_parses_duration(self) -> None:
        """`1h2m` → ~3720s offset; `45s` → ~45s offset; `30m` → ~1800s."""
        now_mono = time.monotonic()
        from backend.llm_health import _parse_reset_seconds
        v = _parse_reset_seconds("1h2m", now_mono)
        self.assertIsNotNone(v)
        self.assertAlmostEqual(v - now_mono, 3720, delta=2)
        v = _parse_reset_seconds("45s", now_mono)
        self.assertAlmostEqual(v - now_mono, 45, delta=2)
        v = _parse_reset_seconds("30m", now_mono)
        self.assertAlmostEqual(v - now_mono, 1800, delta=2)
        # Bare seconds-from-now
        v = _parse_reset_seconds("120", now_mono)
        self.assertAlmostEqual(v - now_mono, 120, delta=2)
        # Empty / None
        self.assertIsNone(_parse_reset_seconds("", now_mono))
        self.assertIsNone(_parse_reset_seconds(None, now_mono))


class TestNimRateMeter(unittest.TestCase):
    def setUp(self) -> None:
        _fresh_state()

    def test_30_calls_in_60s_eligible(self) -> None:
        """30 successful calls → 40-30=10 remaining, > 5 low water → eligible."""
        model = "qwen/qwen3-next-80b-a3b-instruct"
        for _ in range(30):
            record_nim_call("brain", model)
        h = llm_health._STATE[model]
        self.assertEqual(h.credits_remaining, 10.0)
        self.assertEqual(h.credits_unit, "requests_min")
        self.assertTrue(_has_credits(h, time.monotonic()))

    def test_36_calls_in_60s_gated_out(self) -> None:
        """36 successful calls → 40-36=4 remaining, < 5 low water → gated."""
        model = "qwen/qwen3-next-80b-a3b-instruct"
        for _ in range(36):
            record_nim_call("brain", model)
        h = llm_health._STATE[model]
        self.assertEqual(h.credits_remaining, 4.0)
        self.assertFalse(_has_credits(h, time.monotonic()))

    def test_60s_window_resets(self) -> None:
        """After 60s elapses, _has_credits flips back to True via stale-reset."""
        model = "qwen/qwen3-next-80b-a3b-instruct"
        for _ in range(36):
            record_nim_call("brain", model)
        h = llm_health._STATE[model]
        self.assertFalse(_has_credits(h, time.monotonic()))
        # Simulate 65 seconds passing: credits_reset_at is now in the past.
        future_now = (h.credits_reset_at or time.monotonic()) + 5.0
        self.assertTrue(_has_credits(h, future_now))


class TestElectionCreditGate(unittest.TestCase):
    """Integration: elect_primary skips quota-exhausted candidates."""

    def setUp(self) -> None:
        _fresh_state()

    def test_groq_below_water_skipped_in_election(self) -> None:
        """Groq has fast probe latency but is below daily-tokens water →
        elector falls through to next candidate."""
        groq_model = "groq:llama-3.3-70b-versatile"
        nim_model = "qwen/qwen3-next-80b-a3b-instruct"
        # Both healthy + fresh probe + low latency. Groq is faster (100ms).
        gh = _healthy_now(groq_model)
        gh.latency_ms = 100
        gh.credits_remaining = 100.0  # < 5000 low_water
        gh.credits_unit = "tokens_day"
        gh.credits_low_water = GROQ_TOKENS_LOW_WATER
        gh.credits_observed_at = time.monotonic()
        llm_health._STATE[groq_model] = gh

        nh = _healthy_now(nim_model)
        nh.latency_ms = 300
        llm_health._STATE[nim_model] = nh

        with mock.patch.object(
            llm_health, "_chain_for", return_value=[groq_model, nim_model]
        ):
            primary = llm_health.get_primary("brain")
        self.assertEqual(primary, nim_model,
                         "Quota-exhausted Groq should be skipped despite faster latency.")

    def test_nim_preferred_over_faster_groq_when_eligible(self) -> None:
        """KI-087 NIM-first preference: even when both candidates are healthy
        and Groq has plenty of credits AND is 3x faster on latency, NIM must
        still be elected as PRIMARY. Groq + OpenRouter are emergency-fallback
        only, not picked-on-latency. Replaces the pre-KI-087
        `test_groq_above_water_picked_in_election` which asserted the
        opposite (Groq wins on latency)."""
        groq_model = "groq:llama-3.3-70b-versatile"
        nim_model = "qwen/qwen3-next-80b-a3b-instruct"
        gh = _healthy_now(groq_model)
        gh.latency_ms = 100  # faster
        gh.credits_remaining = 10000.0  # well above water
        gh.credits_unit = "tokens_day"
        gh.credits_low_water = GROQ_TOKENS_LOW_WATER
        gh.credits_observed_at = time.monotonic()
        llm_health._STATE[groq_model] = gh

        nh = _healthy_now(nim_model)
        nh.latency_ms = 300  # slower but NIM
        llm_health._STATE[nim_model] = nh

        with mock.patch.object(
            llm_health, "_chain_for", return_value=[groq_model, nim_model]
        ):
            primary = llm_health.get_primary("brain")
        self.assertEqual(
            primary, nim_model,
            "KI-087: NIM must beat Groq as PRIMARY even when Groq is faster + "
            "has credits. NIM is the strategic free provider; Groq + OpenRouter "
            "are emergency fallback only.",
        )

    def test_groq_picked_when_nim_pool_empty(self) -> None:
        """KI-087 fallthrough: when NO eligible NIM candidate exists (all NIM
        models down / out of credits / not in chain), election falls through
        to the best non-NIM candidate as PRIMARY. Locks in the safety net so
        a full NIM regional outage still produces a working brain call."""
        groq_model = "groq:llama-3.3-70b-versatile"
        or_model = "openrouter:openai/gpt-oss-120b"
        gh = _healthy_now(groq_model)
        gh.latency_ms = 100
        gh.credits_remaining = 10000.0
        gh.credits_unit = "tokens_day"
        gh.credits_low_water = GROQ_TOKENS_LOW_WATER
        gh.credits_observed_at = time.monotonic()
        llm_health._STATE[groq_model] = gh

        oh = _healthy_now(or_model)
        oh.latency_ms = 800  # slower
        llm_health._STATE[or_model] = oh

        # Note the chain has NO NIM candidates.
        with mock.patch.object(
            llm_health, "_chain_for", return_value=[or_model, groq_model]
        ):
            primary = llm_health.get_primary("brain")
        self.assertEqual(
            primary, groq_model,
            "KI-087 fallthrough: no NIM eligible → election picks the highest-"
            "scored non-NIM candidate (Groq's 100ms beats OpenRouter's 800ms).",
        )

    def test_none_credits_is_permissive(self) -> None:
        """Cold-start: a candidate with credits_remaining=None must be electable."""
        model = "qwen/qwen3-next-80b-a3b-instruct"
        h = _healthy_now(model)
        # credits_remaining is None by default — leave it alone.
        llm_health._STATE[model] = h
        self.assertTrue(_is_election_eligible(h, time.monotonic()),
                        "Cold-start (None credits) must NOT gate out a healthy candidate.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
