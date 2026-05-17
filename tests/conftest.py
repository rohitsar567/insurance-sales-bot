"""Shared pytest fixtures.

The recommendation-card EXTRACTION GATE is a new, orthogonal invariant: a
policy with no extracted corpus file renders as a broken card (raw
policy_id title, grade "N/A", "No extraction available for this policy.",
"Data not indexed"), so it must never be cited or quality-seeded.

In production every RETRIEVED policy is renderable. The synthetic
policy_ids the logic tests build (e.g. "rs-multiplier", "p0",
"test__policy-a") have no extracted file on disk, so without this fixture
the gate would empty every synthetic citation set and mask the behaviour
each test actually checks (selection, ordering, dedup, fit-floor,
transparency).

So: for every test EXCEPT the dedicated extraction-gate guard
(tests/test_extraction_gate.py — which restores the real predicate via
its own monkeypatch), treat test policies as renderable. Forcing
_has_extraction -> True reproduces the exact pre-gate behaviour these
tests were written against.
"""

from __future__ import annotations

import pytest

from backend import brain_tools


@pytest.fixture(autouse=True)
def _treat_test_policies_as_renderable(monkeypatch):
    monkeypatch.setattr(brain_tools, "_has_extraction", lambda pid: True)
