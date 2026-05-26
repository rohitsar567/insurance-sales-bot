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
    PRIVACY FIX (2026-05-16, audit). Previously this hydrated every empty
    slot on session.profile and returned True so single_brain stamped
    `is_returning_user=True` / a "Welcome back" greeting — on a FRESH,
    no-cookie session, keyed only on the user-stated name. A second user
    on a shared browser/IP, or anyone stating a common first name, was
    served a stranger's captured profile. Now it delegates to
    session_state.rehydrate_by_name which STAGES the match on
    `session.pending_profile_recall` (no merge, no greeting) and ALWAYS
    returns False. The stored profile is applied ONLY after an explicit
    user confirmation via session_state.apply_pending_recall(session,
    confirmed=True). Same-session continuity is unaffected — slots captured
    within the live conversation never travel this path.

  Feature B — recall_by_name_payload(name, session_id)
    Builds the response payload for the POST /api/profile/recall-by-name
    endpoint. PRIVACY FIX (2026-05-16): this NO LONGER auto-hydrates the
    live session. A name match is STAGED; the payload reports
    {found, requires_confirmation, name, summary, session_id} so the UI
    renders an explicit "are you <name>?" confirm rather than silently
    adopting a stranger's stored profile. The stored fields are applied to
    the session only after the user confirms (the chat affirmation path
    calls session_state.apply_pending_recall).

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


def try_recall_by_name(session, name: str, *, user_text: str = "") -> bool:
    """Look up a stored profile by name and STAGE it for confirmation.

    PRIVACY FIX (2026-05-16). Wraps session_state.rehydrate_by_name, which
    now STAGES a name match on `session.pending_profile_recall` instead of
    auto-merging it into `session.profile`. Nothing is applied to the live
    session here.

    PRIVACY HARDENING v4 (2026-05-27, ADR-042 follow-up #1) — `user_text`
    is threaded through so the two-fact gate inside rehydrate_by_name can
    parse same-turn identity facts (age/dependents/location/income) to
    decide whether to stage / defer / fail-closed. Callers that don't have
    user_text on hand (rare) still get the prior-turn live-profile path.

    Returns:
        Always False — the stored profile is NEVER auto-applied. The
        single_brain caller therefore never stamps `is_returning_user` /
        a "Welcome back" greeting off a bare name on a fresh session.
        Whether a match was *staged* is observable on
        `session.pending_profile_recall`; whether the probe was *deferred*
        (no facts to disambiguate yet) is on `session.recall_match_deferred`.
        The brain surfaces an "are you <name>?" confirm and calls
        session_state.apply_pending_recall on the user's explicit answer.
    """
    if not name or not name.strip():
        return False
    try:
        from backend.session_state import rehydrate_by_name

        return bool(rehydrate_by_name(session, name, user_text=user_text))
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

    PRIVACY FIX (2026-05-16, audit). This NO LONGER hydrates the live
    session. A bare name is a weak, shared, guessable key; auto-applying a
    stored profile to a fresh, no-cookie session leaked stranger PII. A
    match is now STAGED on `session.pending_profile_recall`; the payload
    asks the UI to confirm identity before anything is applied.

    Returns:
        {
          "found": bool,                  # True iff a stored match exists
          "requires_confirmation": bool,  # True when found (never auto-apply)
          "name": str | None,             # stored display name (for the prompt)
          "summary": dict | None,         # non-PII identity hints for the prompt
          "profile": None,                # NEVER returned pre-confirmation
          "predicted_band": None,         # NEVER returned pre-confirmation
          "session_id": str,
        }

    No side effect on `session.profile`. The staged match is applied only
    after the user explicitly confirms, via the chat affirmation path
    (session_state.apply_pending_recall).
    """
    out: dict[str, Any] = {
        "found": False,
        "requires_confirmation": False,
        "name": None,
        "summary": None,
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

    # Stages on sess.pending_profile_recall; ALWAYS returns False (no merge).
    try_recall_by_name(sess, name)
    pending = getattr(sess, "pending_profile_recall", None)
    if not pending:
        return out

    out["found"] = True
    out["requires_confirmation"] = True
    out["name"] = pending.get("name")
    out["summary"] = pending.get("summary")
    return out


__all__ = [
    "auto_persist_session",
    "extract_potential_name",
    "try_recall_by_name",
    "recall_by_name_payload",
]
