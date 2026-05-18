"""BUG #24 — user-facing policy_name cleanup for the HDFC ERGO Optima family.

SYMPTOM
-------
A policy card / chat prose showed the product name
`my:Optima Secure (older variant)` (insurer "HDFC ERGO"). The lowercase
`my:` prefix reads as a typo / formatting error to users. The official
product is "Optima Secure"; the "(older variant)" suffix is an INTENTIONAL
KI-145 dedup disambiguator (1 IRDAI UIN = 1 marketplace card) that MUST be
preserved.

FIX
---
`backend.policy_identity.clean_display_policy_name()` — a small, central,
display-only normaliser applied at every user-facing surface that emits a
policy_name (marketplace card pass-1/pass-2 + aliases, /api/coverage
sample names, chat citations, retrieve_policies chunks, get_policy_facts,
build_scorecard → compare / single & bulk scorecard).

INVARIANTS THIS LOCKS DOWN
--------------------------
1. The cleaned display name for the older-variant product is
   "Optima Secure (older variant)" — no "my:", no duplicated "HDFC ERGO "
   insurer label (the insurer is shown separately on the card), and the
   "(older variant)" disambiguator is kept verbatim.
2. TARGETED, NOT a blanket `my:` strip — `my:health Suraksha` and the
   rest of HDFC ERGO's legitimate `my:health …` brand family are
   returned UNCHANGED.
3. Display-only — `policy_id`, normalised UIN, `product_key` and
   `canonical_key` (KI-145 dedup identity) are NEVER derived from the
   cleaned name, so policy_id resolution + 1-UIN-1-card dedup still work
   exactly as before and the newer vs older Optima Secure siblings stay
   two distinct cards.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.policy_identity import (  # noqa: E402
    canonical_key,
    clean_display_policy_name,
    normalize_uin,
    product_key,
)

# The product at the centre of BUG #24.
_OLD_VARIANT_PID = "hdfc-ergo__my-optima-secure-older-variant__wordings"
_OLD_VARIANT_UIN = "HDFHLIP21016V012122"
_NEW_PID = "hdfc-ergo__optima-secure"
_NEW_UIN = "HDFHLIP25041V062425"


class TestDisplayNameCleanup(unittest.TestCase):
    # ---- the BUG is fixed -------------------------------------------------
    def test_older_variant_my_prefix_removed_disambiguator_kept(self):
        self.assertEqual(
            clean_display_policy_name("my:Optima Secure (older variant)"),
            "Optima Secure (older variant)",
        )

    def test_plain_my_optima_secure_cleaned(self):
        self.assertEqual(
            clean_display_policy_name("my:Optima Secure"),
            "Optima Secure",
        )

    def test_duplicated_insurer_label_stripped(self):
        # The card shows the insurer ("HDFC ERGO") separately, so the name
        # must not duplicate it.
        self.assertEqual(
            clean_display_policy_name("HDFC ERGO my:Optima Secure"),
            "Optima Secure",
        )
        self.assertEqual(
            clean_display_policy_name(
                "HDFC ERGO Optima Secure (Older / Legacy Variant)"
            ),
            "Optima Secure (Older / Legacy Variant)",
        )

    def test_no_lowercase_my_colon_remains_for_optima_family(self):
        for raw in (
            "my:Optima Secure (older variant)",
            "my:Optima Secure",
            "HDFC ERGO my:Optima Secure",
        ):
            self.assertNotIn("my:", clean_display_policy_name(raw))

    # ---- TARGETED: my:health family is LEGITIMATE and untouched ----------
    def test_my_health_family_is_NOT_altered(self):
        legit = [
            "my:health Suraksha",
            "my:health Medisure Prime",
            "my:health Sampoorna Suraksha",
            "my:health Women Suraksha",
            "my:health Medisure Prime Insurance",
            "HDFC ERGO my:health Suraksha",  # not Optima → prefix stays
        ]
        for name in legit:
            self.assertEqual(
                clean_display_policy_name(name),
                name,
                f"legitimate HDFC ERGO brand name altered: {name!r}",
            )

    # ---- conservative / idempotent --------------------------------------
    def test_already_clean_and_other_products_unchanged(self):
        for name in (
            "Optima Secure (older variant)",  # already clean
            "Optima Restore",
            "Optima Plus",
            "Optima Enhance",
            "Star Family Health Optima",
            "Activ Secure - Cancer Secure",
        ):
            self.assertEqual(clean_display_policy_name(name), name)

    def test_idempotent(self):
        once = clean_display_policy_name("HDFC ERGO my:Optima Secure")
        self.assertEqual(clean_display_policy_name(once), once)

    def test_non_string_safe(self):
        self.assertEqual(clean_display_policy_name(None), "")
        self.assertEqual(clean_display_policy_name(123), 123)

    # ---- HARD CONSTRAINT: identity / dedup UNCHANGED --------------------
    def test_policy_id_uin_dedup_unchanged_by_display_cleanup(self):
        chunk = {
            "policy_id": _OLD_VARIANT_PID,
            "policy_name": "my:Optima Secure (older variant)",
            "uin_code": _OLD_VARIANT_UIN,
        }
        cleaned = dict(chunk)
        cleaned["policy_name"] = clean_display_policy_name(
            chunk["policy_name"]
        )

        # Dedup key is derived from UIN / policy_id, never from the name.
        self.assertEqual(canonical_key(chunk), canonical_key(cleaned))
        self.assertEqual(
            canonical_key(cleaned), f"uin:{_OLD_VARIANT_UIN}"
        )
        self.assertEqual(
            product_key(chunk["policy_id"]),
            product_key(cleaned["policy_id"]),
        )
        self.assertEqual(
            normalize_uin(chunk["uin_code"]), _OLD_VARIANT_UIN
        )

    def test_ki145_two_cards_preserved(self):
        # The newer "Optima Secure" and the "(older variant)" have
        # DIFFERENT UINs → KI-145 must keep them as two distinct cards.
        newer = {"policy_id": _NEW_PID, "uin_code": _NEW_UIN}
        older = {"policy_id": _OLD_VARIANT_PID, "uin_code": _OLD_VARIANT_UIN}
        self.assertNotEqual(canonical_key(newer), canonical_key(older))


if __name__ == "__main__":
    unittest.main()
