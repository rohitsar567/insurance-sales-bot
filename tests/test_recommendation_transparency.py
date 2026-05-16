"""Recommendation-transparency (deploy-#2 follow-up, 2026-05-16).

CONTEXT (owner Image#8 diagnosis, CONFIRMED): the recommendation-fit gate
is CORRECT — when a new hard constraint appears (e.g. user says "zero
co-pay, individual only" after "Royal Sundaram Multiplier" was shown), the
gate rightly drops Multiplier because it carries a co-pay. The BUG is purely
conversational: the assistant SILENTLY swaps the recommendation set with no
explanation, so it feels "random / dropped a policy" to the user.

These tests pin the FIX (single_brain.py only — the gate logic is NOT
touched):

  1. Pure-derivation layer (`_recommendation_change_note` /
     `_constraint_reason_clause`):
       • constraint added → previously-cited policy dropped → returns a
         one-line note that NAMES the real dropped policy and ties the
         removal to the REAL constraint the user stated (from this turn's
         save_profile_field updates) — nothing invented.
       • set unchanged / no new constraint / no prior snapshot / empty
         current set → returns "" (NO spurious explanation).
       • canonical identity: a doctype-sibling re-id of a still-cited
         policy is NOT mis-reported as dropped.

  2. End-to-end `handle_turn` integration (Gemini + retrieve_policies
     mocked at their network seams):
       • Turn 1: Multiplier (has co-pay) is in the cited set, snapshot
         persisted, NO drop note.
       • Turn 2: user states "zero co-pay" → fit gate drops Multiplier →
         reply is PREPENDED with a transparent line naming Multiplier and
         citing the zero-co-pay constraint.
       • Turn where the set is unchanged → NO note prepended.

Run:
    .venv/bin/python -m pytest -q tests/test_recommendation_transparency.py
"""

from __future__ import annotations

import asyncio
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import brain_tools, single_brain  # noqa: E402
from backend.single_brain import (  # noqa: E402
    _constraint_reason_clause,
    _recommendation_change_note,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _cite(pid, name):
    """A cited-card dict shaped like _build_recommendation_citations output."""
    return {
        "chunk_id": f"{pid}#c",
        "policy_id": pid,
        "policy_name": name,
        "insurer_slug": pid.split("__")[0],
        "doc_type": "policy",
        "source_url": f"https://example.com/{pid}.pdf",
        "score": 0.5,
    }


# ════════════════════════════════════════════════════════════════════════════
# LAYER 1 — pure derivation: _constraint_reason_clause
# ════════════════════════════════════════════════════════════════════════════
class TestConstraintReasonClause(unittest.TestCase):
    def test_zero_copay_is_the_canonical_reason(self):
        # The Image#8 scenario: user said "zero co-pay" → LLM persisted
        # copay_pct=0 this turn.
        self.assertEqual(
            _constraint_reason_clause({"copay_pct": "0"}),
            "you want zero co-pay",
        )
        self.assertEqual(
            _constraint_reason_clause({"copay_pct": 0}),
            "you want zero co-pay",
        )

    def test_nonzero_copay_does_not_claim_zero(self):
        # User accepted SOME co-pay — must NOT say "you want zero co-pay".
        clause = _constraint_reason_clause({"copay_pct": "20"})
        self.assertNotIn("zero", clause)
        self.assertIn("co-pay", clause)

    def test_other_known_fields_map_to_their_phrase(self):
        self.assertEqual(
            _constraint_reason_clause({"budget_band": "under_15k"}),
            "you gave a budget",
        )
        self.assertEqual(
            _constraint_reason_clause({"parents_to_insure": True}),
            "you're now insuring parents",
        )

    def test_unknown_field_falls_back_to_generic_not_invented(self):
        # An unrecognised field must NOT fabricate a specific reason.
        clause = _constraint_reason_clause({"some_future_field": "x"})
        self.assertEqual(clause, "based on the preference you just shared")

    def test_no_updates_is_generic(self):
        self.assertEqual(
            _constraint_reason_clause({}),
            "based on the preference you just shared",
        )


# ════════════════════════════════════════════════════════════════════════════
# LAYER 1 — pure derivation: _recommendation_change_note
# ════════════════════════════════════════════════════════════════════════════
class TestRecommendationChangeNote(unittest.TestCase):
    def test_drop_due_to_zero_copay_is_explained_and_named(self):
        # Royal Sundaram Multiplier (has a co-pay) WAS shown last turn;
        # this turn it's gone and the user just set copay_pct=0.
        prev = {
            "royal-sundaram__multiplier": "Royal Sundaram Multiplier",
            "niva-bupa__reassure-3": "ReAssure 3.0",
        }
        current = [_cite("niva-bupa__reassure-3", "ReAssure 3.0")]
        note = _recommendation_change_note(
            prev_snapshot=prev,
            current_citations=current,
            profile_updates={"copay_pct": "0"},
        )
        self.assertTrue(note, "a drop+constraint must produce a note")
        # Names the REAL dropped policy (from the snapshot, not invented).
        self.assertIn("Royal Sundaram Multiplier", note)
        # Ties it to the REAL constraint the user stated this turn.
        self.assertIn("zero co-pay", note)
        # Does NOT wrongly name a policy that is still cited.
        self.assertNotIn("ReAssure", note)
        self.assertTrue(note.rstrip().endswith(":"),
                        "note should lead INTO the new shortlist")

    def test_no_note_when_set_unchanged(self):
        prev = {"niva-bupa__reassure-3": "ReAssure 3.0"}
        current = [_cite("niva-bupa__reassure-3", "ReAssure 3.0")]
        # Even WITH a constraint update, nothing was dropped → no note.
        self.assertEqual(
            _recommendation_change_note(
                prev_snapshot=prev,
                current_citations=current,
                profile_updates={"copay_pct": "0"},
            ),
            "",
        )

    def test_no_note_when_set_only_grew(self):
        prev = {"niva-bupa__reassure-3": "ReAssure 3.0"}
        current = [
            _cite("niva-bupa__reassure-3", "ReAssure 3.0"),
            _cite("care__supreme", "Care Supreme"),
        ]
        self.assertEqual(
            _recommendation_change_note(
                prev_snapshot=prev,
                current_citations=current,
                profile_updates={"copay_pct": "0"},
            ),
            "",
        )

    def test_no_note_without_a_new_constraint(self):
        # A set change with NO constraint persisted this turn is a normal
        # refinement, not a silent constraint-driven drop → stay quiet.
        prev = {"royal-sundaram__multiplier": "Royal Sundaram Multiplier"}
        current = [_cite("niva-bupa__reassure-3", "ReAssure 3.0")]
        self.assertEqual(
            _recommendation_change_note(
                prev_snapshot=prev,
                current_citations=current,
                profile_updates={},
            ),
            "",
        )

    def test_no_note_without_prior_snapshot(self):
        # First recommendation ever — nothing to diff against.
        self.assertEqual(
            _recommendation_change_note(
                prev_snapshot={},
                current_citations=[_cite("niva-bupa__reassure-3",
                                         "ReAssure 3.0")],
                profile_updates={"copay_pct": "0"},
            ),
            "",
        )

    def test_no_note_when_current_set_empty(self):
        # Empty cited set is a separate "no plan fits" path — there is no
        # "and these now fit better" to lead into.
        self.assertEqual(
            _recommendation_change_note(
                prev_snapshot={"royal-sundaram__multiplier":
                               "Royal Sundaram Multiplier"},
                current_citations=[],
                profile_updates={"copay_pct": "0"},
            ),
            "",
        )

    def test_doctype_sibling_reid_not_misreported_as_dropped(self):
        # Same product, different doctype-sibling id this turn. canonical
        # identity must treat it as STILL cited → no false "removed" note.
        prev = {
            "national-insurance__new-national-parivar-mediclaim__brochure":
                "New National Parivar Mediclaim",
        }
        current = [_cite(
            "national-insurance__new-national-parivar-mediclaim__wordings",
            "New National Parivar Mediclaim")]
        self.assertEqual(
            _recommendation_change_note(
                prev_snapshot=prev,
                current_citations=current,
                profile_updates={"copay_pct": "0"},
            ),
            "",
            "a re-id of a STILL-cited product must not be reported dropped",
        )

    def test_multiple_dropped_policies_all_named(self):
        prev = {
            "royal-sundaram__multiplier": "Royal Sundaram Multiplier",
            "star__assure": "Star Assure",
            "niva-bupa__reassure-3": "ReAssure 3.0",
        }
        current = [_cite("niva-bupa__reassure-3", "ReAssure 3.0")]
        note = _recommendation_change_note(
            prev_snapshot=prev,
            current_citations=current,
            profile_updates={"copay_pct": "0"},
        )
        self.assertIn("Royal Sundaram Multiplier", note)
        self.assertIn("Star Assure", note)
        self.assertIn("they don't", note)


# ════════════════════════════════════════════════════════════════════════════
# LAYER 2 — end-to-end handle_turn (Gemini + retrieve_policies mocked)
# ════════════════════════════════════════════════════════════════════════════
def _fc_part(name, args):
    return {"functionCall": {"name": name, "args": args}}


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _tool_payload(parts):
    return {"candidates": [{"content": {"parts": parts}}]}


# Catalog the fake retrieve_policies serves. Multiplier carries a co-pay;
# ReAssure 3.0 / Care Supreme are zero-co-pay.
_MULTIPLIER = {
    "chunk_id": "rs1", "policy_id": "royal-sundaram__multiplier",
    "policy_name": "Royal Sundaram Multiplier",
    "insurer_slug": "royal-sundaram", "doc_type": "policy",
    "source_url": "https://example.com/multiplier.pdf", "score": 0.61,
    "uin_code": "RSAHLIP21001V010001",
}
_REASSURE = {
    "chunk_id": "nb1", "policy_id": "niva-bupa__reassure-3",
    "policy_name": "ReAssure 3.0",
    "insurer_slug": "niva-bupa", "doc_type": "policy",
    "source_url": "https://example.com/reassure.pdf", "score": 0.55,
    "uin_code": "MAXHLIP21177V032122",
}
_CARE = {
    "chunk_id": "ch1", "policy_id": "care__supreme",
    "policy_name": "Care Supreme",
    "insurer_slug": "care", "doc_type": "policy",
    "source_url": "https://example.com/caresupreme.pdf", "score": 0.50,
    "uin_code": "CARHLIP21001V010001",
}


class _HandleTurnHarness(unittest.TestCase):
    """Drives single_brain.handle_turn with the two network seams stubbed:
    _gemini_call (scripted per-turn payloads) and brain_tools.retrieve_policies
    (scripted chunk lists). The fit gate, citation builder and transparency
    layer all run for real."""

    def setUp(self):
        import os
        self._env = mock.patch.dict(os.environ,
                                    {"GOOGLE_API_KEY": "test-key"})
        self._env.start()
        self._gemini_script: list = []
        self._retrieve_chunks: list = []

        async def _fake_gemini(*_a, **_k):
            if not self._gemini_script:
                return _text_payload("(no more scripted turns)")
            return self._gemini_script.pop(0)

        async def _fake_retrieve(*_a, **_k):
            chunks = list(self._retrieve_chunks)
            # mark_recommendation gates on session.last_retrieved_chunks —
            # mirror what the real retrieve_policies stamps.
            sess = _k.get("session")
            if sess is not None:
                sess.last_retrieved_chunks = list(chunks)
                sess.slug_to_insurer = {
                    c["policy_id"]: c["insurer_slug"] for c in chunks
                }
            return {"chunks": chunks, "count": len(chunks)}

        self._gp = mock.patch.object(single_brain, "_gemini_call",
                                     _fake_gemini)
        self._rp = mock.patch.object(brain_tools, "retrieve_policies",
                                     _fake_retrieve)
        self._gp.start()
        self._rp.start()

    def tearDown(self):
        self._rp.stop()
        self._gp.stop()
        self._env.stop()

    def _fresh_session(self):
        from backend.session_state import SessionState
        return SessionState(session_id=f"t_{uuid.uuid4().hex[:8]}")

    def _ready_session(self):
        """Session with the 7 required slots filled + the post-recap pricing
        bundle marked skipped — the realistic precondition for a
        RECOMMENDATION turn. Bug #107 only attaches policy citations once
        brain_tools._profile_complete is satisfied, and Bug #108's one-shot
        bundle re-ask gate is bypassed when the user skipped the pricing
        inputs; recommendation-transparency assertions (snapshot persists,
        drop note fires) must reflect that real flow."""
        sess = self._fresh_session()
        sess.profile.name = "Asha"
        sess.profile.age = 35
        sess.profile.dependents = "self+spouse"
        sess.profile.location_tier = "metro"
        sess.profile.income_band = "10L-25L"
        sess.profile.primary_goal = "first_buy"
        sess.profile.health_conditions = ["none"]
        sess.pricing_bundle_skipped = True
        return sess


class TestHandleTurnTransparency(_HandleTurnHarness):
    def test_silent_swap_becomes_explicit_on_new_constraint(self):
        sess = self._ready_session()

        # ---- Turn 1: recommend Multiplier + ReAssure (no constraint yet).
        self._retrieve_chunks = [_MULTIPLIER, _REASSURE]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "comprehensive health cover"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["royal-sundaram__multiplier",
                               "niva-bupa__reassure-3"]})]),
            _text_payload(
                "I recommend Royal Sundaram Multiplier and ReAssure 3.0 "
                "for comprehensive cover."),
        ]
        r1 = _run(single_brain.handle_turn(sess, "I want health insurance"))

        self.assertNotIn("I've removed", r1.reply_text,
                          "no prior shortlist → no drop note on turn 1")
        cited1 = {c["policy_id"] for c in r1.citations}
        self.assertIn("royal-sundaram__multiplier", cited1)
        # Snapshot of THIS turn's cited set must persist for the next diff.
        snap = getattr(sess, "last_recommendation_snapshot", {})
        self.assertEqual(
            snap.get("royal-sundaram__multiplier"),
            "Royal Sundaram Multiplier")

        # ---- Turn 2: user demands zero co-pay. The gate drops Multiplier
        # (it has a co-pay); retrieval now only surfaces zero-co-pay plans.
        self._retrieve_chunks = [_REASSURE, _CARE]
        self._gemini_script = [
            _tool_payload([_fc_part("save_profile_field",
                                    {"field": "copay_pct", "value": "0"})]),
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "zero co-pay comprehensive"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["niva-bupa__reassure-3", "care__supreme"]})]),
            _text_payload(
                "ReAssure 3.0 and Care Supreme are strong zero-co-pay "
                "options."),
        ]
        r2 = _run(single_brain.handle_turn(
            sess, "I want zero co-pay, individual only"))

        # The swap is now EXPLAINED, naming the real dropped policy and the
        # real stated constraint — and prepended ahead of the rec prose.
        self.assertIn("Royal Sundaram Multiplier", r2.reply_text)
        self.assertIn("zero co-pay", r2.reply_text)
        self.assertIn("I've removed", r2.reply_text)
        self.assertLess(
            r2.reply_text.index("Royal Sundaram Multiplier"),
            r2.reply_text.index("ReAssure 3.0"),
            "transparency line must be PREPENDED before the rec prose")
        # Gate behaviour unchanged: Multiplier is no longer cited.
        cited2 = {c["policy_id"] for c in r2.citations}
        self.assertNotIn("royal-sundaram__multiplier", cited2)
        self.assertIn("niva-bupa__reassure-3", cited2)

    def test_no_spurious_note_when_set_unchanged(self):
        sess = self._ready_session()

        self._retrieve_chunks = [_REASSURE, _CARE]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies", {"query": "cover"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["niva-bupa__reassure-3", "care__supreme"]})]),
            _text_payload("ReAssure 3.0 and Care Supreme look good."),
        ]
        _run(single_brain.handle_turn(sess, "show me plans"))

        # Turn 2: a constraint is stated BUT the same set still fits → no
        # policy dropped → must NOT fabricate a removal note.
        self._retrieve_chunks = [_REASSURE, _CARE]
        self._gemini_script = [
            _tool_payload([_fc_part("save_profile_field",
                                    {"field": "copay_pct", "value": "0"})]),
            _tool_payload([_fc_part("retrieve_policies", {"query": "cover"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["niva-bupa__reassure-3", "care__supreme"]})]),
            _text_payload("Both still fit with zero co-pay."),
        ]
        r2 = _run(single_brain.handle_turn(sess, "I want zero co-pay"))

        self.assertNotIn("I've removed", r2.reply_text)
        self.assertEqual(r2.reply_text, "Both still fit with zero co-pay.")

    def test_no_note_on_pure_qa_turn_after_recommendation(self):
        sess = self._ready_session()

        self._retrieve_chunks = [_MULTIPLIER, _REASSURE]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies", {"query": "cover"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["royal-sundaram__multiplier",
                               "niva-bupa__reassure-3"]})]),
            _text_payload("Royal Sundaram Multiplier and ReAssure 3.0."),
        ]
        _run(single_brain.handle_turn(sess, "recommend plans"))
        snap_before = dict(
            getattr(sess, "last_recommendation_snapshot", {}))

        # Pure QA follow-up: no shortlist named, is_recommendation False.
        self._gemini_script = [
            _text_payload("A waiting period is the time before a benefit "
                          "becomes claimable."),
        ]
        r2 = _run(single_brain.handle_turn(
            sess, "what does waiting period mean?"))

        self.assertNotIn("I've removed", r2.reply_text)
        # Active shortlist identity must NOT be wiped by a QA turn.
        self.assertEqual(
            getattr(sess, "last_recommendation_snapshot", {}),
            snap_before,
            "QA turn must not erase the active shortlist snapshot")


if __name__ == "__main__":
    unittest.main()
