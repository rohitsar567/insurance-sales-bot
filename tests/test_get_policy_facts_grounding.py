"""FIX #23 (2026-05-19) — faithfulness guard false "could not verify UIN"
on get_policy_facts answers (REGRESSION from the get_policy_facts tool).

SYMPTOM: a correct contextual comparison / recommendation that used
`get_policy_facts` ended with:

    "⚠️ One or more policy identifiers above could not be verified against
     our records — please confirm the UIN with the insurer before relying
     on it."

…even though the answer was grounded.

ROOT: `single_brain._verify_prose_grounding(reply_text,
retrieved_chunks_all)` only treated `retrieve_policies` chunks as grounding.
`get_policy_facts` returns
`{"ok": True, "policies": [{policy_id, policy_name, insurer_slug, ...}]}`
but those policies were NEVER appended to `retrieved_chunks_all`, so a
policy NAME / UIN the model legitimately stated FROM get_policy_facts
tripped the no-invented-numbers / UIN guard.

FIX: in `handle_turn`'s `_execute_tool` loop, when the executed tool is
`get_policy_facts` and `ok` is True, append each returned policy as a
synthetic grounding entry into `retrieved_chunks_all`, mirroring the
retrieve_policies chunk shape ({policy_id, policy_name, insurer_slug,
uin_code, source_url, chunk_text}). The guard is NOT weakened for
genuinely ungrounded names.

These tests stub the two network seams (single_brain._gemini_call and
brain_tools.get_policy_facts) and drive the real handle_turn so the
grounding verifier runs for real.

Run:
    .venv/bin/python -m pytest -q tests/test_get_policy_facts_grounding.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import brain_tools, single_brain  # noqa: E402

# The exact transparency caveat handle_turn appends when
# _verify_prose_grounding fails — the string this regression must NOT see
# on a grounded get_policy_facts answer.
_CAVEAT = "could not be verified against our records"

# A UIN that matches single_brain._verify_prose_grounding's regex
# (\b[A-Z]{3,}[A-Z0-9]{2,}V\d{5,7}\b) — i.e. the exact fabrication-class
# string the guard flags when it is NOT present in any grounding entry.
_UIN = "MAXHLIP21177V032122"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fc_part(name, args):
    return {"functionCall": {"name": name, "args": args}}


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _tool_payload(parts):
    return {"candidates": [{"content": {"parts": parts}}]}


class _Harness(unittest.TestCase):
    """Drives single_brain.handle_turn with _gemini_call scripted and
    brain_tools.get_policy_facts stubbed to a deterministic payload."""

    def setUp(self):
        self._env = mock.patch.dict(
            os.environ, {"GOOGLE_API_KEY": "test-key"}
        )
        self._env.start()
        self._gemini_script: list = []
        self._gpf_result: dict = {}

        async def _fake_gemini(*_a, **_k):
            if not self._gemini_script:
                return _text_payload("(no more scripted turns)")
            return self._gemini_script.pop(0)

        def _fake_gpf(_session, policy_ids=None):
            return self._gpf_result

        self._gp = mock.patch.object(
            single_brain, "_gemini_call", _fake_gemini
        )
        self._gpf = mock.patch.object(
            brain_tools, "get_policy_facts", _fake_gpf
        )
        self._gp.start()
        self._gpf.start()

    def tearDown(self):
        self._gpf.stop()
        self._gp.stop()
        self._env.stop()

    def _session(self):
        from backend.session_state import SessionState

        return SessionState(session_id=f"t_{uuid.uuid4().hex[:8]}")


class TestGetPolicyFactsGrounding(_Harness):
    def test_named_policy_from_get_policy_facts_is_not_falsely_flagged(self):
        """The model calls get_policy_facts, then names that policy AND
        states its UIN in prose. Pre-FIX #23 the UIN was ungrounded (the
        get_policy_facts policy was never in retrieved_chunks_all) → the
        false ⚠️ caveat. Post-fix the policy is registered as grounding →
        NO caveat."""
        sess = self._session()
        self._gpf_result = {
            "ok": True,
            "count": 1,
            "policies": [
                {
                    "policy_id": "niva-bupa__reassure-3",
                    "policy_name": "Niva Bupa ReAssure 3.0",
                    "insurer_slug": "niva-bupa",
                    "insurer_name": "Niva Bupa",
                    "uin_code": _UIN,
                    "claim_settlement_ratio_pct": 91.4,
                    "complaints_per_10k_policies": 5.2,
                    "scorecard_grade": "A",
                    "claim_data_source_url": "https://irdai.gov.in/x",
                }
            ],
        }
        self._gemini_script = [
            _tool_payload(
                [
                    _fc_part(
                        "get_policy_facts",
                        {"policy_ids": ["niva-bupa__reassure-3"]},
                    )
                ]
            ),
            _text_payload(
                "Niva Bupa ReAssure 3.0 (UIN " + _UIN + ") has a strong "
                "91.4% claim-settlement ratio and low complaints — a solid "
                "pick on claim record."
            ),
        ]

        r = _run(
            single_brain.handle_turn(
                sess, "How good is the ReAssure plan's claim record?"
            )
        )

        self.assertNotIn(
            _CAVEAT,
            r.reply_text,
            "grounded get_policy_facts answer must NOT get the false "
            "'could not be verified' caveat",
        )
        self.assertTrue(
            r.faithfulness_passed,
            "faithfulness_passed must be True for a grounded "
            "get_policy_facts answer",
        )
        self.assertIn("ReAssure 3.0", r.reply_text)

    def test_guard_still_flags_a_genuinely_ungrounded_uin(self):
        """Control: FIX #23 must NOT weaken the guard. A UIN that
        get_policy_facts did NOT return (and no retrieve_policies chunk
        supports) must STILL trip the caveat."""
        sess = self._session()
        # get_policy_facts returns policy A with its own UIN…
        self._gpf_result = {
            "ok": True,
            "count": 1,
            "policies": [
                {
                    "policy_id": "care__supreme",
                    "policy_name": "Care Supreme",
                    "insurer_slug": "care",
                    "uin_code": "CARHLIP21001V010001",
                    "claim_settlement_ratio_pct": 88.0,
                }
            ],
        }
        # …but the model invents a DIFFERENT, unreturned UIN in prose.
        self._gemini_script = [
            _tool_payload(
                [
                    _fc_part(
                        "get_policy_facts",
                        {"policy_ids": ["care__supreme"]},
                    )
                ]
            ),
            _text_payload(
                "Care Supreme is great; also see plan UIN " + _UIN + " "
                "which I recommend."
            ),
        ]

        r = _run(
            single_brain.handle_turn(sess, "Tell me about Care Supreme.")
        )

        self.assertIn(
            _CAVEAT,
            r.reply_text,
            "a UIN that get_policy_facts did NOT return must still be "
            "flagged — the guard must not be weakened",
        )
        self.assertFalse(r.faithfulness_passed)


if __name__ == "__main__":
    unittest.main()
