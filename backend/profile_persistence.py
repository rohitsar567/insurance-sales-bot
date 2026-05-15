"""Post-turn auto-persistence + returning-user recall helpers.

Coupled features (KI-Z7, 2026-05-15):

  Feature A — auto_persist_session(session)
    Called from /api/chat AFTER single_brain.handle_turn returns and BEFORE
    the ChatResponse is built. Reads the union of all 13 slot_union fields
    from session.profile and pushes them to:
      - profile_store.save_profile(...)      — canonical JSON on disk
      - profile_rag.upsert_profile_chunk(...) — Chroma vector chunk
    Gated on session.profile.name being non-empty (anonymous turns never
    persist — KI-118).

    Wrapped in try/except internally; persistence failure NEVER raises and
    NEVER affects the reply path.

  Feature B — extract_potential_name(text)
    Cheap regex heuristic that recovers a first name from a turn-1 user
    utterance ("Hi I'm Priya", "My name is Rajesh", "Anjali here"). Returns
    None when no clear name is present (e.g. "I'm 34 years old"). Used by
    single_brain.handle_turn so a returning user is recognised BEFORE
    Gemini's first tool-call iteration.

  Feature B — try_recall_by_name(session, name)
    Loads the named profile JSON via profile_store.load_profile and hydrates
    every empty slot on session.profile. Returns True on a successful merge
    so single_brain can stamp `is_returning_user=True` for the RULE 4
    "welcome back" greeting.

  Feature B — recall_by_name_payload(name, session_id)
    Builds the response payload for the POST /api/profile/recall-by-name
    endpoint: {found, profile, predicted_band, session_id}. predicted_band
    is computed via premium_calculator.estimate_premium_band against the
    just-hydrated profile.

Design notes:
  - This module sits between profile_store + profile_rag and is the SINGLE
    write path the /api/chat hot loop reaches; brain_tools.save_profile_field
    still only mutates the in-memory Profile (B6 owns that file).
  - No async required for the JSON write; profile_rag.upsert_profile_chunk
    is async (embedder), so auto_persist_session is async too.
  - All callers MUST await it and swallow exceptions defensively. We do the
    defensive swallow internally as belt-and-suspenders so callers can
    simply `await auto_persist_session(session)` without try/except.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature A — post-turn persistence
# ---------------------------------------------------------------------------


# The 13 fields that make up the "slot_union" — what we persist on every
# auto_persist_session call. Kept in sync with the schemas in
# needs_finder.Profile and brain_tools.save_profile_field's accepted fields.
_UNION_FIELDS: tuple[str, ...] = (
    "name",
    "age",
    "dependents",
    "income_band",
    "existing_cover_inr",
    "primary_goal",
    "location_tier",
    "parents_to_insure",
    "parents_age_max",
    "parents_has_ped",
    "health_conditions",
    "budget_band",
    "desired_sum_insured_inr",
)


def _build_union_dict(profile) -> dict[str, Any]:
    """Build the persistence payload — dict of all 13 union fields."""
    return {f: getattr(profile, f, None) for f in _UNION_FIELDS}


async def auto_persist_session(session) -> bool:
    """Persist the live session profile to disk + Chroma.

    No-op (returns False) when:
      - session is None
      - session.profile.name is empty / None
      - either underlying write raises (logged, then swallowed)

    Returns True iff BOTH save_profile() AND upsert_profile_chunk() ran
    without raising. Persistence failure NEVER bubbles out — the chat
    reply must not be blocked by a stuck disk or a Chroma hiccup.
    """
    if session is None:
        return False
    profile = getattr(session, "profile", None)
    if profile is None:
        return False
    name = (getattr(profile, "name", None) or "").strip()
    if not name:
        # Anonymous turn — never write to disk, never embed (KI-118).
        return False

    union = _build_union_dict(profile)
    saved_json = False
    saved_chunk = False

    # 1) Canonical JSON on disk.
    try:
        from backend.profile_store import save_profile

        saved_json = save_profile(
            name,
            profile,
            session_id=getattr(session, "session_id", None),
        )
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "auto_persist_session: save_profile failed (name=%r): %s: %s",
            name, type(e).__name__, str(e)[:200],
        )

    # 2) Vector chunk (gated on a derivable name_slug — same gate as the
    #    POST /api/profile path).
    try:
        from backend.profile_store import _normalise_name
        from backend.profile_rag import upsert_profile_chunk

        name_slug = _normalise_name(name)
        if name_slug:
            await upsert_profile_chunk(name_slug, union)
            saved_chunk = True
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "auto_persist_session: upsert_profile_chunk failed (name=%r): %s: %s",
            name, type(e).__name__, str(e)[:200],
        )

    return saved_json and saved_chunk


# ---------------------------------------------------------------------------
# Feature B — turn-1 name heuristic + returning-user recall
# ---------------------------------------------------------------------------


# Match an explicit self-introduction. Three forms cover the common cases:
#   1. "I'm <name>" / "I am <name>"
#   2. "My name is <name>" / "this is <name>"
#   3. "<name> here"
# We deliberately require a verb-ish anchor — bare "Priya." with no
# surrounding context is too noisy on turn 1 (it could be a question subject)
# and the existing brain_tools.save_profile_field path will pick it up
# safely if the user repeats it.
_NAME_RE = re.compile(
    r"""
    (?:
        \b(?:i\s*am|i'?m|my\s+name\s+is|this\s+is|name'?s)\s+
        (?P<n1>[A-Z][a-zA-Z]{1,30}(?:\s+[A-Z][a-zA-Z]{1,30})?)
        \b
      |
        ^\s*(?P<n2>[A-Z][a-zA-Z]{1,30}(?:\s+[A-Z][a-zA-Z]{1,30})?)\s+here\b
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Words that look name-like to the regex (capitalised at sentence start)
# but are NOT names. Filter post-match so "I'm 34" and "I am okay" never
# resolve to "okay" / "34" as a name.
_NAME_STOPWORDS: set[str] = {
    "ok", "okay", "fine", "good", "great", "well", "yes", "no", "yeah",
    "nope", "sure", "looking", "trying", "thinking", "interested",
    "here", "back", "ready", "done", "free", "busy", "tired", "young",
    "old", "married", "single", "alone", "happy", "sad",
    "from", "in", "at", "on", "with", "for", "to",
    "the", "a", "an",
    # Time + age
    "twenty", "thirty", "forty", "fifty", "sixty",
}


def extract_potential_name(text: str) -> Optional[str]:
    """Return a probable first-name capture from a turn-1 user utterance.

    Examples:
        "Hi I'm Priya"            -> "Priya"
        "Hello, my name is Rajesh" -> "Rajesh"
        "Anjali here"             -> "Anjali"
        "I'm 34 years old"        -> None
        "I am okay, thanks"       -> None
        ""                        -> None

    Caller should slug + try_recall_by_name; no DB or LLM cost.
    """
    if not text or not text.strip():
        return None
    # Normalize whitespace, keep original casing for the regex.
    s = text.strip()
    m = _NAME_RE.search(s)
    if not m:
        return None
    raw = (m.group("n1") or m.group("n2") or "").strip()
    if not raw:
        return None
    # Reject digit-laden captures ("I'm 34") and stop-words ("I'm okay").
    first_token = raw.split()[0]
    if first_token.lower() in _NAME_STOPWORDS:
        return None
    if not first_token.isalpha():
        return None
    if len(first_token) < 2:
        return None
    return raw


def try_recall_by_name(session, name: str) -> bool:
    """Look up a stored profile by name and hydrate `session.profile`.

    Wraps session_state.rehydrate_by_name so the call-site (single_brain
    handle_turn entry) doesn't need to know which helper is canonical.

    Returns True iff a stored profile was found AND at least one slot was
    merged into the live session. False on no-match or any error.
    """
    if not name or not name.strip():
        return False
    try:
        from backend.session_state import rehydrate_by_name

        return bool(rehydrate_by_name(session, name))
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "try_recall_by_name failed (name=%r): %s: %s",
            name, type(e).__name__, str(e)[:200],
        )
        return False


async def recall_by_name_payload(
    name: str,
    session_id: str,
) -> dict[str, Any]:
    """Build the response payload for POST /api/profile/recall-by-name.

    Returns:
        {
          "found": bool,
          "profile": dict | None,         # the hydrated union dict
          "predicted_band": dict | None,  # {min_inr, median_inr, max_inr, ...}
          "session_id": str,
        }

    Side effect: when a match is found, the live in-memory session for
    `session_id` is hydrated (so the next /api/chat turn sees the recalled
    slots in session.profile WITHOUT requiring a separate /api/profile POST).
    """
    out: dict[str, Any] = {
        "found": False,
        "profile": None,
        "predicted_band": None,
        "session_id": session_id,
    }
    if not name or not name.strip() or not session_id:
        return out

    try:
        from backend.session_state import get_session

        sess = get_session(session_id)
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "recall_by_name_payload: get_session failed (session_id=%r): %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )
        return out

    found = try_recall_by_name(sess, name)
    if not found:
        return out

    # Build the union dict snapshot post-hydration.
    union = _build_union_dict(sess.profile)
    out["found"] = True
    out["profile"] = union

    # Predicted-premium band — same path /api/profile/predicted-premium-band
    # uses, so the banner number matches the chip number exactly.
    try:
        from backend.premium_calculator import estimate_premium_band

        out["predicted_band"] = estimate_premium_band(union)
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "recall_by_name_payload: estimate_premium_band failed (name=%r): %s: %s",
            name, type(e).__name__, str(e)[:200],
        )
        out["predicted_band"] = None

    return out


__all__ = [
    "auto_persist_session",
    "extract_potential_name",
    "try_recall_by_name",
    "recall_by_name_payload",
]
