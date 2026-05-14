"""KI-104 (2026-05-15) — chain-of-thought / instruction-echo strip tests.

Live smoke caught the brain LLM leaking internal reasoning into the
user-visible reply_text. Examples seen verbatim in production:
  - "We need to respond to user question..."
  - "We must ground every factual claim..."
  - "We need to follow instructions. The user asks... According to
     conversation rules..."

The faithfulness judge passed these through. Users would see them as
broken/embarrassing output.

These tests lock in:
  1. `strip_cot_preamble` removes CoT preamble before substantive content.
  2. `<think>...</think>` blocks are removed (defense-in-depth — the
     primary strip is in persona.strip_think_tags).
  3. Labelled reasoning blocks (`**Reasoning:**`, `[INTERNAL]`) are removed.
  4. Substantive content starting with a CoT-like phrase ("Sure, here are
     3 plans...") is NOT a false positive.
  5. tts_preprocess invokes the strip end-to-end.
  6. persona.strip_think_tags invokes the strip end-to-end.

Run as a script (no pytest dep):
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    .venv/bin/python -m unittest tests.test_voice_format -v
"""

from __future__ import annotations

import unittest

from backend.voice_format import (
    strip_cot_preamble,
    tts_preprocess,
    _EMERGENCY_REPLY,
)
from backend.persona import strip_think_tags


class StripCotPreambleTests(unittest.TestCase):
    """Direct unit tests on strip_cot_preamble."""

    # ---------- core production-bug repros (verbatim from smoke test) ----------

    def test_we_need_to_respond_preamble_dropped(self):
        """The headline production bug — kill the 'We need to respond' line."""
        inp = "We need to respond to user question. Here's the actual answer."
        out = strip_cot_preamble(inp)
        self.assertTrue(
            out.startswith("Here's the actual answer"),
            f"Expected actual answer at start, got: {out!r}",
        )
        self.assertNotIn("We need to respond", out)

    def test_we_must_ground_preamble_dropped(self):
        """Second observed leak — 'We must ground every factual claim'."""
        inp = (
            "We must ground every factual claim in retrieved chunks.\n"
            "HDFC ERGO Optima Secure has a 36-month waiting period for "
            "pre-existing diseases."
        )
        out = strip_cot_preamble(inp)
        self.assertNotIn("We must ground", out)
        self.assertIn("HDFC ERGO", out)

    def test_we_need_follow_instructions_multi_line_preamble(self):
        """Third observed leak — multiple CoT lines stacked."""
        inp = (
            "We need to follow instructions.\n"
            "The user asks about pre-existing disease waiting periods.\n"
            "According to conversation rules, we cite policy text.\n"
            "The waiting period is 36 months under Optima Secure."
        )
        out = strip_cot_preamble(inp)
        self.assertNotIn("We need to follow", out)
        self.assertNotIn("The user asks", out)
        self.assertNotIn("According to conversation rules", out)
        self.assertIn("36 months", out)

    # ---------- <think> tag handling (defense in depth) ----------

    def test_stray_close_think_tag_dropped(self):
        """If a stray </think> appears with no opening tag, drop everything before it."""
        inp = "foo bar baz</think>The answer is X."
        out = strip_cot_preamble(inp)
        self.assertEqual(out, "The answer is X.")

    # ---------- labelled reasoning blocks ----------

    def test_reasoning_label_block_dropped(self):
        inp = (
            "**Reasoning:** I need to check the retrieved chunks.\n"
            "The deductible is ₹5,000 per claim."
        )
        out = strip_cot_preamble(inp)
        self.assertNotIn("Reasoning:", out)
        self.assertIn("deductible", out)

    def test_bracket_internal_block_dropped(self):
        inp = (
            "[INTERNAL]Let me check the chunks first[/INTERNAL]\n"
            "Yes, dental is covered up to ₹10,000."
        )
        out = strip_cot_preamble(inp)
        self.assertNotIn("INTERNAL", out)
        self.assertNotIn("Let me check the chunks", out)
        self.assertIn("dental is covered", out)

    def test_plan_label_block_dropped(self):
        inp = (
            "**Plan:** Answer briefly, cite policy clause 3.2.\n"
            "The maximum sum insured is ₹1 crore."
        )
        out = strip_cot_preamble(inp)
        self.assertNotIn("Plan:", out)
        self.assertIn("1 crore", out)

    # ---------- starter-phrase preamble (Step 1, To answer this, etc.) ----------

    def test_step_numbered_preamble_dropped(self):
        inp = (
            "Step 1: Identify the policy.\n"
            "Step 2: Find the clause.\n"
            "Maternity waiting period is 24 months."
        )
        out = strip_cot_preamble(inp)
        self.assertNotIn("Step 1:", out)
        self.assertNotIn("Step 2:", out)
        self.assertIn("Maternity", out)

    def test_to_answer_this_preamble_dropped(self):
        inp = "To answer this question, I'll check the chunks.\nDental is covered."
        out = strip_cot_preamble(inp)
        self.assertTrue(out.startswith("Dental"))

    def test_first_ill_preamble_dropped(self):
        inp = "First, I'll review the retrieved policy text.\nThe waiting period is 36 months."
        out = strip_cot_preamble(inp)
        self.assertNotIn("First, I'll", out)
        self.assertIn("36 months", out)

    def test_let_me_think_preamble_dropped(self):
        inp = "Let me think about this carefully.\nThe answer is yes — OPD is included."
        out = strip_cot_preamble(inp)
        self.assertNotIn("Let me think about", out)
        self.assertIn("OPD is included", out)

    def test_following_instructions_dropped(self):
        inp = "Following the instructions, I will cite each claim.\nCoverage is comprehensive."
        out = strip_cot_preamble(inp)
        self.assertNotIn("Following the instructions", out)
        self.assertIn("Coverage is comprehensive", out)

    def test_as_per_guidelines_dropped(self):
        inp = "As per the guidelines, citations are required.\nThe premium is ₹15,000."
        out = strip_cot_preamble(inp)
        self.assertNotIn("As per the guidelines", out)
        self.assertIn("premium", out)

    # ---------- false-positive guards ----------

    def test_legit_reply_unchanged_sure_here_are_3_plans(self):
        """Don't false-positive a substantive opener that starts with 'Sure'."""
        inp = "Sure, here are 3 plans to consider:\n1. HDFC ERGO Optima Secure\n2. Star Comprehensive\n3. Niva Bupa ReAssure"
        out = strip_cot_preamble(inp)
        self.assertEqual(out, inp)

    def test_legit_reply_unchanged_yes_dental_is_covered(self):
        inp = "Yes, dental treatment is covered under Optima Secure subject to a sub-limit of ₹10,000 per year."
        out = strip_cot_preamble(inp)
        self.assertEqual(out, inp)

    def test_legit_reply_unchanged_we_have_three_options_midreply(self):
        """The phrase 'We have three options:' is substantive content, not CoT."""
        inp = "Based on your needs profile, we have three options: A, B, and C."
        out = strip_cot_preamble(inp)
        self.assertEqual(out, inp)

    def test_legit_reply_starting_with_the_user(self):
        """A reply that legitimately begins 'The user manual says...' must survive."""
        # Note: 'The user manual' does NOT match the starter regex (which
        # requires 'The user asks/is asking/wants/needs').
        inp = "The user manual for Optima Secure is available at hdfcergo.com."
        out = strip_cot_preamble(inp)
        self.assertEqual(out, inp)

    def test_legit_reply_with_inline_let_me_think(self):
        """'Let me think' beyond the scan window must not be stripped."""
        inp = (
            "Optima Secure offers a sum insured of ₹1 crore with "
            "unlimited restore. It covers daycare, road ambulance, and "
            "ayurveda. Let me think about which plan suits you best — "
            "I'll need your age and city to recommend."
        )
        out = strip_cot_preamble(inp)
        # The 'Let me think' is mid-reply, beyond line 1, so it survives.
        self.assertIn("Let me think", out)
        self.assertIn("Optima Secure", out)

    # ---------- empty / edge cases ----------

    def test_empty_input_returns_emergency_reply(self):
        self.assertEqual(strip_cot_preamble(""), _EMERGENCY_REPLY)
        self.assertEqual(strip_cot_preamble("   \n  "), _EMERGENCY_REPLY)

    def test_all_cot_returns_emergency_reply(self):
        """If the WHOLE reply is CoT and nothing substantive remains."""
        inp = (
            "We need to respond to user question.\n"
            "We must check the chunks.\n"
            "Let me think.\n"
            "Step 1: identify policy."
        )
        out = strip_cot_preamble(inp)
        self.assertEqual(out, _EMERGENCY_REPLY)

    def test_none_input_safe(self):
        """Conservative: None / falsy inputs should not crash."""
        self.assertEqual(strip_cot_preamble(None), _EMERGENCY_REPLY)  # type: ignore[arg-type]


class TtsPreprocessIntegrationTests(unittest.TestCase):
    """End-to-end: tts_preprocess invokes strip_cot_preamble."""

    def test_tts_strips_cot_before_markdown(self):
        inp = "We need to respond to user question.\n**Yes**, OPD is covered."
        out = tts_preprocess(inp, language="en")
        self.assertNotIn("We need to respond", out)
        self.assertIn("Yes", out)
        # Markdown bold was also stripped:
        self.assertNotIn("**", out)


class PersonaStripThinkTagsIntegrationTests(unittest.TestCase):
    """End-to-end: persona.strip_think_tags invokes strip_cot_preamble too."""

    def test_think_block_plus_cot_preamble_both_stripped(self):
        inp = (
            "<think>I need to check the chunks first.</think>\n"
            "We need to respond carefully.\n"
            "The waiting period is 36 months."
        )
        out = strip_think_tags(inp)
        self.assertNotIn("<think>", out)
        self.assertNotIn("We need to respond", out)
        self.assertIn("36 months", out)

    def test_clean_reply_passes_through_unchanged(self):
        inp = "Dental treatment is covered up to ₹10,000 per year under Optima Secure."
        out = strip_think_tags(inp)
        self.assertEqual(out, inp)


if __name__ == "__main__":
    unittest.main()
