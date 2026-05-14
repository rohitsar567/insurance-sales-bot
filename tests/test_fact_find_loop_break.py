"""Regression tests for the fact-find canonical-fallback loop bug (KI-103).

Pre-fix bug
-----------
Live 15-persona smoke test on 2026-05-15 caught the brain repeating the
SAME slot question for 4-8 consecutive turns even though the user explicitly
answered on the first turn. Concrete evidence:
    A3 (Priya): age 42 stated T2, bot asked "First, your age?" 7 times
    A5 (Sanjay): employer-cover answered T4, bot asked the same Q 4 times
    B3 (Mehul): age 45 stated T2, bot asked age 8 times despite the answer

Root cause
----------
When the LLM brain returned a reply WITHOUT the `<FF>{...}</FF>` JSON tail
(reason=`no_trailer`), `_canonical_fallback` ran. Its greedy multi-slot
capture only appends to `profile.asked` when a value is successfully
captured. When greedy fails (user_text doesn't match any slot's strict
regex), `profile.asked` stays unchanged, so `next_question(profile)`
returns the SAME slot every turn. Loop.

Fix
---
`_canonical_fallback` now tracks per-slot consecutive failed-fallback
surfaces on the SessionState (`_ff_failed_attempts`). After
`_MAX_FAILED_ATTEMPTS` (=2) consecutive surfaces of slot S with no
capture for S, the slot is marked asked + appended to
`_ff_skipped_slots` and the fallback advances to the next unfilled slot.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_fact_find_loop_break.py -v
"""

from __future__ import annotations

import unittest
import uuid
from dataclasses import dataclass, field
from typing import Optional

# Bootstrap import path so this file runs from either pytest or unittest.
import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _fresh_session_id() -> str:
    return f"test_ff_loop_break_{uuid.uuid4().hex[:10]}"


def _cleanup_session_file(session_id: str) -> None:
    target = _REPO_ROOT / "40-data" / "sessions" / f"{session_id}.json"
    if target.exists():
        try:
            target.unlink()
        except OSError:
            pass


class TestCanonicalFallbackBreaksLoop(unittest.TestCase):
    """KI-103 — three consecutive `_canonical_fallback` calls with user
    text that captures nothing must NOT return the same slot on call 3
    as on call 1. The session-level failure counter must advance past
    the stuck slot after two failed surfaces.
    """

    def setUp(self) -> None:
        from backend.session_state import _sessions, get_session
        _sessions.clear()
        self.session_id = _fresh_session_id()
        # Build a fresh session with name + age unset (everything unset
        # actually). No awaiting_question_id, no asked slots.
        self.session = get_session(self.session_id)

    def tearDown(self) -> None:
        _cleanup_session_file(self.session_id)

    def test_three_failed_attempts_advance_slot(self) -> None:
        """Drive `_canonical_fallback` 3 times with non-extracting input.
        Assert the slot driven on call 3 differs from call 1 — the
        loop-breaker has skipped the stuck slot and advanced.
        """
        from backend.fact_find_brain import _canonical_fallback

        # user_text that doesn't match ANY slot's strict capture regex:
        # - "name" strict mode needs intro phrase ("I'm X", "my name is X")
        # - "age" needs age trigger or bare number
        # - dependents / income_band / etc. need family / amount / etc. keywords
        non_extracting_text = "hello there how are you"

        # Call 1
        out1 = _canonical_fallback(
            self.session, non_extracting_text, reason="no_trailer",
        )
        # Call 2
        out2 = _canonical_fallback(
            self.session, non_extracting_text, reason="no_trailer",
        )
        # Call 3
        out3 = _canonical_fallback(
            self.session, non_extracting_text, reason="no_trailer",
        )

        # Sanity: all three are ambiguous canonical fallbacks.
        self.assertTrue(out1.ambiguous, "Call 1 must be a canonical fallback")
        self.assertTrue(out2.ambiguous, "Call 2 must be a canonical fallback")
        self.assertTrue(out3.ambiguous, "Call 3 must be a canonical fallback")

        # Headline assertion: by call 3 the loop-breaker has advanced past
        # the stuck slot — the slot returned on call 3 MUST differ from call 1.
        self.assertIsNotNone(out1.slot_driving, "Call 1 must surface a slot")
        self.assertIsNotNone(out3.slot_driving, "Call 3 must surface a slot")
        self.assertNotEqual(
            out3.slot_driving, out1.slot_driving,
            "REGRESSION (KI-103): canonical fallback re-asked the same slot "
            f"on call 3 ({out3.slot_driving!r}) as on call 1 "
            f"({out1.slot_driving!r}) — loop-breaker is broken. After "
            "2 failed attempts the stuck slot must be marked asked and the "
            "fallback must advance to the next unfilled slot.",
        )

        # The stuck slot from call 1 must now be in _ff_skipped_slots
        # so the orchestrator/scorecard knows it's intentionally unanswered.
        # `slot_driving` on the outcome is the FIELD name, not the question
        # id; map back to the question id for the assertion.
        from backend.fact_find_brain import FIELD_TO_QUESTION_ID
        stuck_field = out1.slot_driving
        stuck_qid = FIELD_TO_QUESTION_ID.get(stuck_field, stuck_field)
        skipped = getattr(self.session, "_ff_skipped_slots", [])
        self.assertIn(
            stuck_qid, skipped,
            f"REGRESSION (KI-103): stuck slot {stuck_qid!r} (field "
            f"{stuck_field!r}) must be recorded on session._ff_skipped_slots "
            f"so the scorecard knows it was intentionally skipped after "
            f"hitting the failed-attempt threshold. Got skipped={skipped!r}.",
        )

    def test_successful_capture_resets_counter(self) -> None:
        """A successful greedy capture for a slot must reset its failure
        counter — so a temporary LLM outage that recovers doesn't
        permanently ghost that slot for the session.
        """
        from backend.fact_find_brain import _canonical_fallback

        # Call 1: non-extracting input → name surfaces, counter[name]=1.
        _canonical_fallback(self.session, "hello there", reason="no_trailer")
        attempts = getattr(self.session, "_ff_failed_attempts", {})
        # Note: the surfaced slot id is determined by GREEDY_ORDER + GRAPH
        # iteration; we don't hard-code it — just confirm a counter advanced.
        self.assertTrue(
            any(v >= 1 for v in attempts.values()),
            "Failure counter must record the first surface as a failed attempt.",
        )

        # Call 2: user supplies a clear name in lenient form. The strict
        # name parser requires an intro phrase, so use that explicitly.
        out2 = _canonical_fallback(
            self.session, "my name is Rohit", reason="no_trailer",
        )
        # Name should now be captured.
        self.assertEqual(
            self.session.profile.name, "Rohit",
            "Greedy capture in canonical fallback should have extracted "
            "the name from 'my name is Rohit'.",
        )
        # The name slot's counter must be reset (popped) after capture.
        attempts_after = getattr(self.session, "_ff_failed_attempts", {})
        self.assertNotIn(
            "name", attempts_after,
            "REGRESSION (KI-103): successful name capture must reset/pop "
            "the failed-attempt counter for 'name' so the slot can be "
            "re-introduced cleanly if the brain recovers.",
        )

    def test_failed_attempt_threshold_constant(self) -> None:
        """Pin the threshold constant. If anyone tunes it back to 5+ they
        have to update this test (and re-prove the loop is still broken).
        """
        from backend.fact_find_brain import _MAX_FAILED_ATTEMPTS
        self.assertEqual(
            _MAX_FAILED_ATTEMPTS, 2,
            "KI-103 threshold is 2 (skip on the 3rd surface). Bumping this "
            "above 2 re-enables the live-smoke-test loop bug where users "
            "saw the same slot question 4-8 times in a row.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
