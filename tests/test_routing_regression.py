"""Regression tests for the KI-018 routing fix.

The original bug (D-003 in 80-audit/ENTERPRISE_AUDIT.md): when a session
had no profile, the orchestrator force-routed EVERY turn to fact-find — even
direct QA. So when a user asked "What is the waiting period for pre-existing
diseases under Activ Assure?", the bot answered "Happy to help. First, your
age?" — and gold-QA factual accuracy was 30%.

These tests lock in:
    1. classify_intent correctly tags policy-fact questions as "qa".
    2. should_route_to_fact_find does NOT force-route qa intent to fact-find
       on empty profile — only recommendation/comparison.
    3. Greetings and advice-seeking openers DO route to fact-find regardless.

Run as a script (no pytest dep):
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    .venv/bin/python -m unittest tests.test_routing_regression -v
"""

from __future__ import annotations

import unittest

from backend.orchestrator import (
    classify_intent,
    CONTEXT_DEPENDENT_INTENTS,
    should_route_to_fact_find,
)


class TestIntentClassification(unittest.TestCase):
    def test_policy_fact_questions_classify_as_qa(self) -> None:
        # These are the questions that produced 30% factual accuracy
        # because they were misrouted to needs_finder. They MUST stay qa.
        qa_questions = [
            "What is the waiting period for pre-existing diseases under Activ Assure?",
            "Is there a cap on room rent under Care Supreme?",
            "Does Activ Assure cover AYUSH treatments?",
            "How many days of pre-hospitalization expenses does Care Supreme cover?",
            "What is the initial waiting period under Comprehensive Care Plan?",
        ]
        for q in qa_questions:
            with self.subTest(question=q):
                self.assertEqual(classify_intent(q), "qa")

    def test_advice_seekers_classify_as_fact_find(self) -> None:
        for q in [
            "I'm looking for a new health insurance policy.",
            "Help me find the best plan for my parents.",
            "Hi",
            "Hello, I need advice.",
        ]:
            with self.subTest(question=q):
                self.assertEqual(classify_intent(q), "fact_find")

    def test_comparison_keywords_classify_as_comparison(self) -> None:
        self.assertEqual(classify_intent("Compare HDFC Optima vs ICICI Elevate"), "comparison")
        self.assertEqual(classify_intent("Which is better between policy A and B?"), "comparison")

    def test_recommendation_keywords_classify_as_recommendation(self) -> None:
        self.assertEqual(classify_intent("Recommend a policy for me"), "recommendation")
        self.assertEqual(classify_intent("Best for a 35-year-old in Bangalore?"), "recommendation")
        # Known ambiguity: "Which one should I get?" matches BOTH the fact-find
        # trigger "should i get" AND the recommendation keyword "should i". The
        # current classifier prefers fact-find (it's checked first). This is OK
        # for a first-turn empty-profile session — the bot fact-finds before
        # recommending anyway. Pinned here so we notice if the order changes.
        self.assertEqual(classify_intent("Which one should I get?"), "fact_find")

    def test_ki105_closer_phrases_classify_as_recommendation(self) -> None:
        """KI-105 (2026-05-15) — explicit-closer regression.

        Live 15-persona smoke caught these phrases being routed to qa or
        fact_find instead of recommendation, so the heavy brain was never
        called + no ranked shortlist was ever produced. The explicit-closer
        override (checked BEFORE FACT_FIND_TRIGGERS) lifts them into the
        recommendation lane unambiguously.
        """
        recommendation_closers = [
            "show me the top 3 policies",
            "show me policies",
            "give me the top 3",
            "what would you recommend",
            "your top picks",
            "pitch me the top 3",
        ]
        for q in recommendation_closers:
            with self.subTest(question=q):
                self.assertEqual(
                    classify_intent(q),
                    "recommendation",
                    f"REGRESSION: {q!r} should classify as recommendation. "
                    f"See KI-105 — without this the bot doesn't produce a "
                    f"ranked shortlist on the closer turn.",
                )

    def test_ki105_closer_phrases_classify_as_comparison(self) -> None:
        """KI-105 — comparison-shaped closer phrases.

        'compare HDFC Ergo and Niva Bupa' and 'rank top 3' both explicitly
        ask for a side-by-side / ranked output across the candidate set.
        They must NOT fall through to qa or fact_find.
        """
        comparison_closers = [
            "compare HDFC Ergo and Niva Bupa",
            "compare top 3",
            "compare the top 3 policies",
            "rank top 3 for me",
            "side-by-side these policies",
        ]
        for q in comparison_closers:
            with self.subTest(question=q):
                self.assertEqual(
                    classify_intent(q),
                    "comparison",
                    f"REGRESSION: {q!r} should classify as comparison.",
                )


class TestKI105CloserPersona(unittest.TestCase):
    """KI-105 (2026-05-15) — the persona must append the CLOSER MODE
    addendum ONLY on recommendation/comparison turns AND only when a
    profile is present. Otherwise the brain reverts to its default
    60-word conservative reply that re-asks slots or refuses."""

    def test_closer_addendum_present_on_recommendation_turn(self) -> None:
        from backend.persona import build_messages
        msgs = build_messages(
            user_query="show me the top 3 policies",
            retrieved_context="[Source: Care Supreme (care-health), p.5]\nSample chunk text.",
            user_profile={"age": 34, "dependents": "self+spouse", "budget_band": "30k_60k"},
            intent="recommendation",
        )
        system = msgs[0]["content"]
        self.assertIn("CLOSER MODE", system)
        self.assertIn("ranked shortlist", system.lower() + "")  # phrase appears in addendum

    def test_closer_addendum_present_on_comparison_turn(self) -> None:
        from backend.persona import build_messages
        msgs = build_messages(
            user_query="compare HDFC Ergo and Niva Bupa",
            retrieved_context="[Source: Optima Secure (hdfc-ergo), p.5]\nSample.",
            user_profile={"age": 34, "dependents": "self+spouse"},
            intent="comparison",
        )
        self.assertIn("CLOSER MODE", msgs[0]["content"])

    def test_closer_addendum_absent_on_qa_turn(self) -> None:
        """A 'what is the PED waiting period?' turn must NOT get the
        ranked-shortlist contract appended — that would force the brain
        to invent 3 policies on a single-policy factual lookup."""
        from backend.persona import build_messages
        msgs = build_messages(
            user_query="What is the PED waiting period under Activ Assure?",
            retrieved_context="[Source: Activ Assure (aditya-birla), p.10]\nClause text.",
            user_profile={"age": 34},
            intent="qa",
        )
        self.assertNotIn("CLOSER MODE", msgs[0]["content"])

    def test_closer_addendum_absent_on_empty_profile(self) -> None:
        """KI-013 belt-and-braces: even if a closer intent slips
        through with no profile, the addendum must NOT fire — otherwise
        the brain might pitch a senior-only policy to an anonymous user."""
        from backend.persona import build_messages
        msgs = build_messages(
            user_query="show me the top 3 policies",
            retrieved_context="[Source: Care Senior (care-health), p.5]\nSample.",
            user_profile=None,
            intent="recommendation",
        )
        self.assertNotIn("CLOSER MODE", msgs[0]["content"])


class TestFactFindRouting(unittest.TestCase):
    """KI-018 regression: empty-profile sessions must NOT trap qa in fact-find."""

    def test_qa_on_empty_profile_does_not_force_fact_find(self) -> None:
        """The headline regression test. Pre-fix this returned True; post-fix False."""
        self.assertFalse(
            should_route_to_fact_find(
                "qa",
                profile_is_empty=True,
                in_fact_find_continuation=False,
                free_form_session=False,
            ),
            "REGRESSION: qa intent with empty profile is being trapped in fact-find. "
            "See D-003 in 80-audit/ENTERPRISE_AUDIT.md — this is the bug "
            "that caused the 'Happy to help. First, your age?' answer to "
            "'What is the waiting period for PED?'.",
        )

    def test_recommendation_on_empty_profile_DOES_force_fact_find(self) -> None:
        """The original KI-013 guard must still work: don't recommend to anonymous users."""
        self.assertTrue(
            should_route_to_fact_find(
                "recommendation",
                profile_is_empty=True,
                in_fact_find_continuation=False,
                free_form_session=False,
            ),
            "KI-013 regression: empty-profile recommendation must force fact-find "
            "(else bot might pitch Care Senior to a 25-year-old).",
        )

    def test_comparison_on_empty_profile_DOES_force_fact_find(self) -> None:
        self.assertTrue(
            should_route_to_fact_find(
                "comparison",
                profile_is_empty=True,
                in_fact_find_continuation=False,
                free_form_session=False,
            )
        )

    def test_fact_find_intent_always_force_fact_find_outside_free_form(self) -> None:
        self.assertTrue(
            should_route_to_fact_find(
                "fact_find",
                profile_is_empty=False,
                in_fact_find_continuation=False,
                free_form_session=False,
            )
        )

    def test_fact_find_continuation_force_fact_find(self) -> None:
        self.assertTrue(
            should_route_to_fact_find(
                "qa",  # even if user veered to a QA question mid-flow…
                profile_is_empty=False,
                in_fact_find_continuation=True,  # …continuation flag wins
                free_form_session=False,
            )
        )

    def test_free_form_session_never_force_fact_find(self) -> None:
        """Once user has opted out of fact-find, never drag them back."""
        for intent in ("fact_find", "qa", "recommendation", "comparison"):
            with self.subTest(intent=intent):
                self.assertFalse(
                    should_route_to_fact_find(
                        intent,
                        profile_is_empty=True,
                        in_fact_find_continuation=False,
                        free_form_session=True,
                    )
                )

    def test_context_dependent_intents_set_unchanged(self) -> None:
        """If someone adds 'qa' here by mistake, the headline bug returns. Pin the set."""
        self.assertEqual(CONTEXT_DEPENDENT_INTENTS, frozenset({"recommendation", "comparison"}))


class TestProviderLoadBalancing(unittest.TestCase):
    """KI-025: brain chain primary rotates 50/50 between NIM Qwen and Groq Llama
    to spread load across two independent rate-cap quotas."""

    def test_rotation_deterministic_modes(self) -> None:
        """Pin both ends of the probability dial — 0% never picks Groq, 100% always does."""
        from backend.providers.nvidia_nim_llm import _balanced_brain_chain, BRAIN_CHAIN
        never_groq = _balanced_brain_chain(BRAIN_CHAIN, groq_first_probability=0.0)
        self.assertFalse(never_groq[0].startswith("groq:"),
                         "groq_first_probability=0 must keep NIM as primary")
        always_groq = _balanced_brain_chain(BRAIN_CHAIN, groq_first_probability=1.0)
        self.assertTrue(always_groq[0].startswith("groq:"),
                        "groq_first_probability=1 must hoist Groq to primary")

    def test_rotation_preserves_chain_membership(self) -> None:
        """Whatever the primary, the FULL set of fallback candidates must still be reachable."""
        from backend.providers.nvidia_nim_llm import _balanced_brain_chain, BRAIN_CHAIN
        rotated = _balanced_brain_chain(BRAIN_CHAIN, groq_first_probability=1.0)
        self.assertEqual(sorted(rotated), sorted(BRAIN_CHAIN),
                         "Rotation must not lose or duplicate any chain candidate.")

    def test_rotation_50_50_in_aggregate(self) -> None:
        """Over many calls, ~50% should land on Groq primary (binomial; allow ±10% slack)."""
        from backend.providers.nvidia_nim_llm import _balanced_brain_chain, BRAIN_CHAIN
        import random
        random.seed(42)  # deterministic for CI
        n = 1000
        groq_first = sum(
            1 for _ in range(n)
            if _balanced_brain_chain(BRAIN_CHAIN)[0].startswith("groq:")
        )
        self.assertGreater(groq_first, 400, f"Expected ~500 groq-firsts, got {groq_first}")
        self.assertLess(groq_first, 600, f"Expected ~500 groq-firsts, got {groq_first}")

    def test_groq_present_in_brain_chain(self) -> None:
        from backend.providers.nvidia_nim_llm import BRAIN_CHAIN, FAST_BRAIN_CHAIN
        self.assertTrue(any(m.startswith("groq:") for m in BRAIN_CHAIN),
                        "BRAIN_CHAIN must have a Groq fallback for the rotation to balance against.")
        self.assertTrue(any(m.startswith("groq:") for m in FAST_BRAIN_CHAIN),
                        "FAST_BRAIN_CHAIN must have a Groq fallback for the rotation to balance against.")


class TestChatEndpointGracefulFailures(unittest.TestCase):
    """KI-106: /api/chat must NEVER return HTTP 500 to the user when
    handle_turn raises (asyncio.TimeoutError or any other exception).

    Pre-fix: C4 NRI persona saw 5× HTTP 500 with
    'Orchestrator failed: TimeoutError'. The inner asyncio.wait_for handlers
    caught their local timeouts but the outer non-fact-find brain call let
    TimeoutError propagate, and FastAPI converted it to 500.

    Post-fix: TimeoutError + any Exception are caught at the /api/chat
    layer and converted into a 200 OK ChatResponse with a graceful
    'try again' message.
    """

    def _client(self):
        from fastapi.testclient import TestClient

        from backend import main as backend_main
        return TestClient(backend_main.app), backend_main

    def test_handle_turn_timeout_returns_200_with_graceful_reply(self) -> None:
        import asyncio as _asyncio

        client, backend_main = self._client()

        async def _raise_timeout(**_kwargs):
            raise _asyncio.TimeoutError("simulated wait_for timeout")

        original = backend_main.handle_turn
        backend_main.handle_turn = _raise_timeout
        try:
            resp = client.post(
                "/api/chat",
                json={"user_text": "What is the waiting period under Activ Assure?"},
            )
        finally:
            backend_main.handle_turn = original

        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 graceful reply, got {resp.status_code}: {resp.text[:200]}",
        )
        body = resp.json()
        self.assertIn(
            "longer than expected",
            body.get("reply_text", "").lower(),
            f"Reply should mention timeout grace phrasing, got: {body.get('reply_text')!r}",
        )
        self.assertEqual(body.get("brain_used"), "timeout_fallback")
        self.assertEqual(body.get("citations"), [])

    def test_handle_turn_unhandled_exception_returns_200_with_graceful_reply(self) -> None:
        client, backend_main = self._client()

        async def _raise_runtime(**_kwargs):
            raise RuntimeError("simulated provider blow-up")

        original = backend_main.handle_turn
        backend_main.handle_turn = _raise_runtime
        try:
            resp = client.post(
                "/api/chat",
                json={"user_text": "Hi"},
            )
        finally:
            backend_main.handle_turn = original

        self.assertEqual(
            resp.status_code,
            200,
            f"Expected 200 graceful reply, got {resp.status_code}: {resp.text[:200]}",
        )
        body = resp.json()
        self.assertIn(
            "something went wrong",
            body.get("reply_text", "").lower(),
            f"Reply should mention error grace phrasing, got: {body.get('reply_text')!r}",
        )
        self.assertEqual(body.get("brain_used"), "error_fallback")


if __name__ == "__main__":
    unittest.main(verbosity=2)
