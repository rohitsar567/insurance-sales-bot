"""Regression test for #47 — UIN net-new dedup on user uploads (2026-05-21).

When a user uploads a policy PDF whose IRDAI UIN already belongs to a
catalogue policy, it is NOT net-new: the upload route must short-circuit
and return the existing card (UploadResponse.already_in_catalogue=True)
instead of indexing a duplicate. These pin the helper logic + the
response-model contract; both symbols did not exist pre-#47.
"""
import unittest

from backend.main import (
    UploadResponse,
    _catalogue_uin_index,
    _match_catalogue_uin,
)


class TestUploadUinDedup(unittest.TestCase):
    def test_catalogue_uin_index_is_built(self):
        idx = _catalogue_uin_index()
        self.assertGreater(len(idx), 50, "catalogue UIN index looks empty")
        for uin, val in idx.items():
            self.assertRegex(uin, r"^[A-Z]{5,9}\d{5}V\d{6}$")
            self.assertEqual(len(val), 2, "value must be (policy_id, name)")

    def test_known_catalogue_uin_matches(self):
        # Every indexed (modern-format) UIN must round-trip through the
        # matcher when embedded in wording text. Order-independent — not
        # fragile to dict-iteration / module-cache state.
        idx = _catalogue_uin_index()
        self.assertTrue(idx, "catalogue UIN index is empty")
        misses = []
        for uin, val in idx.items():
            if _match_catalogue_uin(f"policy wording UIN {uin} terms") != val:
                misses.append(uin)
            if _match_catalogue_uin(uin.lower()) != val:  # case-insensitive
                misses.append(uin + " (lowercase)")
        self.assertEqual(misses, [], f"UINs that did not round-trip: {misses[:8]}")

    def test_unknown_or_fake_uin_is_not_matched(self):
        self.assertIsNone(_match_catalogue_uin("plain text, no identifier"))
        self.assertIsNone(_match_catalogue_uin(""))
        # valid UIN *shape* but not a real catalogue policy
        self.assertIsNone(_match_catalogue_uin("ZZZHLIP00000V000000"))

    def test_uploadresponse_dedup_fields(self):
        hit = UploadResponse(
            policy_id="acko__acko-health-ii", policy_name="Acko Health Ii",
            chunks_added=0, pages_indexed=10, elapsed_ms=5,
            already_in_catalogue=True,
            existing_policy_id="acko__acko-health-ii",
            existing_policy_name="Acko Health Ii")
        self.assertTrue(hit.already_in_catalogue)
        self.assertEqual(hit.existing_policy_id, "acko__acko-health-ii")
        # net-new upload — fields default to the not-a-dedup-hit state
        fresh = UploadResponse(
            policy_id="user-upload__x__y", policy_name="Y",
            chunks_added=42, pages_indexed=45, elapsed_ms=9)
        self.assertFalse(fresh.already_in_catalogue)
        self.assertIsNone(fresh.existing_policy_id)


if __name__ == "__main__":
    unittest.main()
