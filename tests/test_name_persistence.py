"""Regression tests for the fact-find name-loop bug (KI-091 + KI-094 + KI-095).

Pre-fix bug:
    1. User says "Rohit" → bot captures name + advances to next slot.
    2. Mid-session, on a later turn, the LLM extractor (running in the
       free-form / QA branch) returns {"name": null, ...} for an utterance
       that has nothing to do with the name.
    3. Orchestrator's merge loop wrote that None back via
       session.update_profile_field("name", None), wiping the captured value.
    4. next_question(profile) then re-asks the name slot → infinite loop.

Three fixes pin the bug shut:
    KI-091 — skip the LLM extractor + faithfulness judge entirely on fact-find
             turns (intent=='fact_find' OR in_fact_find_continuation).
    KI-094 — defensive guard inside the extractor merge: if new_value is in
             (None, "", []), skip the field — never clobber a filled slot.
    KI-095 — the /api/profile REST endpoint applies the same guard: empty
             strings from the client cannot wipe a filled field.

These tests run WITHOUT touching any LLM API. retrieve / brain / judge /
extractor are all monkeypatched. The only thing exercised is the
orchestrator merge logic + the /api/profile endpoint logic.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    .venv/bin/python -m pytest tests/test_name_persistence.py -v
    # or:
    .venv/bin/python -m unittest tests.test_name_persistence -v
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from dataclasses import dataclass
from typing import Optional
from unittest import mock

# Bootstrap import path so this file runs from either pytest or unittest.
import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Lightweight stubs that stand in for the heavy I/O dependencies of
# `handle_turn`. Together they let us drive the orchestrator end-to-end
# without spinning up Chroma, an LLM provider, or the faithfulness judge.
# ---------------------------------------------------------------------------

@dataclass
class _StubLLMResult:
    text: str = "Got it."
    model: str = "stub-model"


class _StubProvider:
    """Stands in for whatever `pick_brain(...).provider` returns."""
    name = "stub-provider"
    model = "stub-model"

    async def chat(self, messages, temperature=0.2, max_tokens=1500):
        return _StubLLMResult(text="Got it.", model="stub-model")


def _stub_brain_pick(intent: str, language: str):
    # Match the BrainPick shape (.provider, .tag) the orchestrator uses.
    from backend.orchestrator import BrainPick
    return BrainPick(_StubProvider(), f"stub::{intent}")


async def _stub_retrieve(query, top_k=5, policy_ids=None, profile_name_slug=None, session_id=None):
    return []  # empty context — perfectly valid for these tests


def _stub_format_for_llm_context(chunks):
    return ""


async def _stub_upsert(*args, **kwargs):
    return None


def _fresh_session_id() -> str:
    """Use a unique session id per test so the on-disk JSON cache doesn't
    cross-contaminate. We also clean up the file in tearDown."""
    return f"test_name_persistence_{uuid.uuid4().hex[:10]}"


def _cleanup_session_file(session_id: str) -> None:
    target = _REPO_ROOT / "40-data" / "sessions" / f"{session_id}.json"
    if target.exists():
        try:
            target.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CASE A — extractor returning {"name": null} cannot wipe captured name.
# ---------------------------------------------------------------------------

class TestCaseA_ExtractorNullCannotWipeName(unittest.TestCase):
    """KI-094 regression. The merge loop in orchestrator.handle_turn must
    treat extractor-returned None / "" / [] as a no-op for that field —
    NEVER overwrite a filled slot.
    """

    def setUp(self) -> None:
        from backend.session_state import _sessions  # in-memory cache
        _sessions.clear()
        self.session_id = _fresh_session_id()

    def tearDown(self) -> None:
        _cleanup_session_file(self.session_id)

    def test_null_extraction_preserves_filled_name(self) -> None:
        from backend.session_state import get_session
        from backend import orchestrator as orch

        # Seed: a session that already has name="Rohit" + name slot asked.
        # Mark free_form_session so the extractor branch is reached (and not
        # short-circuited by KI-091's fact-find gate).
        session = get_session(self.session_id)
        session.profile.name = "Rohit"
        session.profile.age = 30
        session.profile.asked = ["name"]
        session.free_form_session = True
        session.awaiting_question_id = None
        session._flush()

        # Mock extractor: returns null name + a valid new age update.
        # Per KI-094, the null name must be skipped; the valid age update
        # should still be applied.
        async def _mock_extractor(user_text, current_profile):
            return {"name": None, "age": 31, "income_band": "", "health_conditions": []}

        # Patch all the heavy collaborators. We patch the names AS REFERENCED
        # by orchestrator (which imports them inside the function body, so
        # we patch at their source module).
        with mock.patch("backend.profile_extractor.extract_profile_updates",
                        new=_mock_extractor), \
             mock.patch("backend.orchestrator.retrieve", new=_stub_retrieve), \
             mock.patch("backend.orchestrator.format_for_llm_context",
                        new=_stub_format_for_llm_context), \
             mock.patch("backend.orchestrator.pick_brain", new=_stub_brain_pick), \
             mock.patch("backend.profile_rag.upsert_profile_chunk",
                        new=_stub_upsert), \
             mock.patch("backend.orchestrator.check_faithfulness",
                        new=mock.AsyncMock(return_value=mock.Mock(
                            passed=True, reasons=[]))):

            # Drive a QA turn — an explicit policy-fact question.
            # Intent should classify as "qa", profile is not empty (has age),
            # so should_route_to_fact_find returns False → flow falls through
            # to the extractor branch.
            asyncio.run(orch.handle_turn(
                user_text="What is the waiting period for PED under Activ Assure?",
                chat_history=[],
                user_profile=None,
                session_id=self.session_id,
            ))

        # Re-read the session and assert.
        session_after = get_session(self.session_id)
        self.assertEqual(
            session_after.profile.name, "Rohit",
            "REGRESSION (KI-094): extractor returning {'name': null} wiped "
            "the captured name. The merge guard "
            "`if new_value in (None, '', []): continue` is missing or "
            "broken in backend/orchestrator.py.",
        )
        self.assertEqual(
            session_after.profile.age, 31,
            "Valid non-null extractor updates should still be applied; "
            "guard must skip ONLY null/empty values, not block all updates.",
        )


# ---------------------------------------------------------------------------
# CASE B — fact-find branch does not invoke extract_profile_updates.
# ---------------------------------------------------------------------------

class TestCaseB_FactFindBranchDoesNotInvokeExtractor(unittest.TestCase):
    """KI-091 regression. On a fact-find turn the orchestrator MUST NOT call
    extract_profile_updates — the fact_find brain extracts slots natively
    from its <FF> JSON tail, and the heavy extractor LLM both (a) duplicates
    work and (b) periodically returns nulls that wipe filled slots.

    We pin this by patching `extract_profile_updates` to raise. If the
    orchestrator wrongly calls it on a fact-find turn, the call raises and
    the test fails. If KI-091's gate is in place, the call never happens
    and the turn completes cleanly.
    """

    def setUp(self) -> None:
        from backend.session_state import _sessions
        _sessions.clear()
        self.session_id = _fresh_session_id()

    def tearDown(self) -> None:
        _cleanup_session_file(self.session_id)

    def test_fact_find_turn_does_not_call_extractor(self) -> None:
        from backend.session_state import get_session
        from backend import orchestrator as orch
        from backend.fact_find_brain import FactFindOutcome

        # Seed a session that's in fact-find continuation (awaiting an answer).
        session = get_session(self.session_id)
        session.profile.name = "Rohit"
        session.profile.age = 30
        session.profile.asked = ["name", "age"]
        session.free_form_session = False
        session.awaiting_question_id = "dependents"  # mid-fact-find
        session._flush()

        # Mock extractor: if called, fail loudly.
        async def _explode(user_text, current_profile):
            raise AssertionError(
                "extract_profile_updates was called on a fact-find turn — "
                "KI-091 gate is broken. The fact_find brain already extracts "
                "slots from its <FF> trailer; the heavy extractor LLM here "
                "is both wasteful AND the source of the name-wipe bug."
            )

        # Mock the fact_find brain to return a benign outcome.
        async def _stub_drive(user_text, session, chat_history, session_id):
            return FactFindOutcome(
                reply_text="And who else do you want covered?",
                captured_updates={},
                slot_driving="dependents",
                fact_find_complete=False,
                ambiguous=False,
            )

        # The orchestrator wraps the extractor call in try/except, swallows
        # AssertionError, and just logs a warning — meaning a broken KI-091
        # gate would silently regress without our raise propagating. To pin
        # this properly, we monitor the patched extractor with a Mock and
        # assert call_count == 0 instead of relying on the raise reaching us.
        extractor_spy = mock.AsyncMock(side_effect=_explode)

        with mock.patch("backend.profile_extractor.extract_profile_updates",
                        new=extractor_spy), \
             mock.patch("backend.fact_find_brain.drive_fact_find",
                        new=_stub_drive), \
             mock.patch("backend.profile_rag.upsert_profile_chunk",
                        new=_stub_upsert), \
             mock.patch("backend.profile_store.save_profile", new=mock.Mock()):

            # Drive a turn — the user's message is a fact-find answer.
            # in_fact_find_continuation is True (awaiting_question_id set,
            # not free-form), so the orchestrator should route to the
            # fact-find brain and return BEFORE reaching the extractor.
            result = asyncio.run(orch.handle_turn(
                user_text="self + spouse",
                chat_history=[],
                user_profile=None,
                session_id=self.session_id,
            ))

        # The headline assertion: extractor was never invoked.
        self.assertEqual(
            extractor_spy.call_count, 0,
            "REGRESSION (KI-091): extract_profile_updates was called on a "
            "fact-find turn (call_count=%d). The intent='fact_find' / "
            "in_fact_find_continuation gate in backend/orchestrator.py is "
            "broken — fact-find turns must skip the heavy extractor LLM."
            % extractor_spy.call_count,
        )

        # Sanity: the fact-find branch did run (we got its reply back).
        self.assertEqual(result.intent, "fact_find")
        self.assertEqual(
            result.reply_text, "And who else do you want covered?",
            "Fact-find branch did not run — test setup is wrong.",
        )

        # And the captured name is still there.
        session_after = get_session(self.session_id)
        self.assertEqual(session_after.profile.name, "Rohit")


# ---------------------------------------------------------------------------
# CASE C — /api/profile endpoint rejects empty-string overwrite.
# ---------------------------------------------------------------------------

class TestCaseC_ProfileApiRejectsEmptyOverwrite(unittest.TestCase):
    """KI-095 regression. The POST /api/profile endpoint must NOT overwrite
    a filled field with an empty string / empty list from the client.

    Pre-fix: the loop did `if v is not None: setattr(...)` — an empty
    string passed through and clobbered the existing name.

    Post-fix (KI-095): `if v in (None, "", []): continue` — empty values
    from the client are now no-ops.
    """

    def setUp(self) -> None:
        from backend.session_state import _sessions
        _sessions.clear()
        self.session_id = _fresh_session_id()

    def tearDown(self) -> None:
        _cleanup_session_file(self.session_id)

    def test_empty_string_name_does_not_clobber_filled_name(self) -> None:
        from backend.session_state import get_session
        from backend.main import profile_update, ProfileUpdateRequest

        # Seed a session with name="Rohit".
        session = get_session(self.session_id)
        session.profile.name = "Rohit"
        session.profile.age = 30
        session._flush()

        # Build a request with name="" — the bad payload that used to wipe.
        # Other fields stay None so they're skipped by the existing
        # `if v is not None` guard regardless.
        req = ProfileUpdateRequest(session_id=self.session_id, name="")

        # Patch the downstream RAG upsert + named-profile store so the
        # endpoint doesn't hit disk/Chroma.
        with mock.patch("backend.profile_rag.upsert_profile_chunk",
                        new=_stub_upsert), \
             mock.patch("backend.profile_store.save_profile", new=mock.Mock()):
            asyncio.run(profile_update(req))

        # Re-read and assert.
        session_after = get_session(self.session_id)
        self.assertEqual(
            session_after.profile.name, "Rohit",
            "REGRESSION (KI-095): /api/profile let an empty string clobber "
            "a filled name. The guard `if v in (None, '', []): continue` "
            "in backend/main.py:profile_update is missing or broken.",
        )

    def test_empty_list_health_conditions_does_not_clobber_filled_list(self) -> None:
        """KI-095 also protects list fields — an empty health_conditions
        list from the client must not wipe a populated list."""
        from backend.session_state import get_session
        from backend.main import profile_update, ProfileUpdateRequest

        session = get_session(self.session_id)
        session.profile.name = "Rohit"
        session.profile.health_conditions = ["diabetes", "hypertension"]
        session._flush()

        req = ProfileUpdateRequest(
            session_id=self.session_id,
            health_conditions=[],
        )

        with mock.patch("backend.profile_rag.upsert_profile_chunk",
                        new=_stub_upsert), \
             mock.patch("backend.profile_store.save_profile", new=mock.Mock()):
            asyncio.run(profile_update(req))

        session_after = get_session(self.session_id)
        self.assertEqual(
            session_after.profile.health_conditions,
            ["diabetes", "hypertension"],
            "REGRESSION (KI-095): empty list from client clobbered a "
            "filled health_conditions list.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
