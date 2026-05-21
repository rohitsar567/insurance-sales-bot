"""Regression tests for #41 + #43 (2026-05-21).

#41 — the post-recap pricing bundle re-asked family medical history even
after the user answered "no family history". Root cause: an explicit
negative answer coerces to [] and save_profile_field dropped it as
"normalized_empty" → the slot stayed unset → the gate re-asked it.
Fix: save_profile_field records an explicit-none family-history answer on
profile.asked, and _unresolved_pricing_bundle treats an answered (asked)
slot as resolved even when its value is empty.

#43 — a comparison reply citing a genuine catalogue UIN (not echoed in
this turn's retrieved chunks) was wrongly flagged "could not be verified
against our records". _verify_prose_grounding now treats a real
catalogue UIN as verified-by-definition.
"""
import unittest

from backend import single_brain
from backend.brain_tools import save_profile_field, _unresolved_pricing_bundle
from backend.session_state import SessionState
from backend.main import _catalogue_uin_index


class TestBug41FamilyHistoryNoneResolved(unittest.TestCase):
    def test_explicit_none_answer_is_recorded_as_asked(self):
        for neg in ("none", "no", "no family history",
                    "no family medical history"):
            s = SessionState(session_id="b41a_" + neg[:4])
            r = save_profile_field(s, "family_medical_history", neg)
            self.assertTrue(r.get("saved"),
                            f"#41: explicit-none {neg!r} not accepted: {r}")
            self.assertIn("family_medical_history", s.profile.asked,
                          f"#41: {neg!r} not recorded on profile.asked")

    def test_bundle_gate_does_not_reask_an_answered_none(self):
        s = SessionState(session_id="b41b")
        save_profile_field(s, "desired_sum_insured_inr", "1000000")
        save_profile_field(s, "budget_band", "15k_30k")
        save_profile_field(s, "copay_pct", "0")
        save_profile_field(s, "smoker", "no")
        # before answering family history → it IS in the re-ask set
        self.assertIn("family_medical_history",
                      _unresolved_pricing_bundle(s.profile, s))
        # user answers "no family history" → must become RESOLVED
        save_profile_field(s, "family_medical_history", "none")
        self.assertNotIn("family_medical_history",
                         _unresolved_pricing_bundle(s.profile, s),
                         "#41: family history re-asked after it was answered")

    def test_affirmative_family_history_still_saved_normally(self):
        s = SessionState(session_id="b41c")
        r = save_profile_field(s, "family_medical_history", "diabetes")
        self.assertTrue(r.get("saved"))
        self.assertTrue(s.profile.family_medical_history)  # non-empty value
        self.assertNotIn("family_medical_history",
                         _unresolved_pricing_bundle(s.profile, s))

    def test_garbage_empty_still_dropped(self):
        # a None/garbage save must still be rejected (KI-091/094 — never
        # overwrite a captured slot with an empty extraction)
        s = SessionState(session_id="b41d")
        r = save_profile_field(s, "family_medical_history", None)
        self.assertFalse(r.get("saved"))
        self.assertNotIn("family_medical_history", s.profile.asked)


class TestBug43CatalogueUinIsVerified(unittest.TestCase):
    def test_real_catalogue_uin_in_prose_is_not_flagged(self):
        uin = next(iter(_catalogue_uin_index()))  # a genuine catalogue UIN
        ok, reasons = single_brain._verify_prose_grounding(
            f"Comparison table. Policy A [Source: ..., {uin}] is strong.",
            [],  # no retrieved chunks this turn
        )
        self.assertTrue(ok, f"#43: catalogue UIN {uin} wrongly flagged: {reasons}")
        self.assertEqual(reasons, [])

    def test_fabricated_uin_is_still_flagged(self):
        ok, reasons = single_brain._verify_prose_grounding(
            "Policy X [Source: Bogus, ZZZHLIP99999V999999] is great.", [])
        self.assertFalse(ok, "#43: a fabricated UIN must still be flagged")
        self.assertTrue(reasons)

    def test_uin_grounded_in_a_retrieved_chunk_passes(self):
        ok, _ = single_brain._verify_prose_grounding(
            "Policy [Source: P, ZZZHLIP99999V999999].",
            [{"uin_code": "ZZZHLIP99999V999999", "chunk_text": ""}])
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
