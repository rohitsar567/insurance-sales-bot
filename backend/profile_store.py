"""Persistent name-keyed profile store.

KI-040 (2026-05-14). Lets returning visitors say their name and have the
bot recognise them + auto-load their stored profile, so they don't have
to walk the 9-slot fact-find again.

Architectural answer to the "embed or JSON?" question:

  • JSON (this module) — the canonical store, keyed by normalised name.
    O(1) lookup, deterministic, human-readable, manually editable.
  • Chroma vector chunk (existing backend/profile_rag.py) — re-embedded
    when the profile changes, so retrieval-time the brain sees the
    user's profile alongside policy chunks for the "what's best for me?"
    style questions. Embedding cost = once per update, not per query.

Both layers stay in sync: when `save_profile()` is called here, the
orchestrator also fires `profile_rag.upsert_profile_chunk()` so the
Chroma side reflects the new state.

Files live under `40-data/profiles/<normalised-name>.json`. Names are
normalised to lowercase + alpha-only for the filename so "Rohit" and
"rohit." both resolve to the same profile. The original (capitalised)
display name is preserved inside the JSON.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal, Optional

from backend.config import settings
from backend.needs_finder import Profile

_PROFILES_DIR = settings.CORPUS_DIR.parent.parent / "40-data" / "profiles"


def _normalise_name(name: str) -> str:
    """Lowercase + strip to alphanumerics. 'Rohit Sharma' → 'rohit-sharma'."""
    if not name:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return cleaned[:60]  # cap filename length


# KI-062 (2026-05-15) — identity-defining fields used to disambiguate two
# users with the same display name. Order matters for hash stability.
_PERSONA_ID_FIELDS: tuple[str, ...] = (
    "age", "dependents", "income_band", "location_tier", "parents_age_max",
)


def compute_persona_id(profile: Profile) -> str:
    """Return a 12-char hash blending the user's normalised name with their
    identity-defining profile fields. Two users named 'Rohit' but with
    different age/dependents/location resolve to different persona IDs.

    Returns '' if there's not enough signal (no name AND no identity
    fields). Caller falls back to name-only slug in that case.

    KI-062 (2026-05-15).
    """
    parts = [_normalise_name(profile.name or "")]
    for f in _PERSONA_ID_FIELDS:
        v = getattr(profile, f, None)
        parts.append("" if v in (None, "", []) else str(v).strip().lower())
    if not any(parts):
        return ""
    blob = "|".join(parts).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]


def _path_for(name: str, *, persona_id: Optional[str] = None) -> Optional[Path]:
    """Resolve the JSON file path. Prefers persona_id (KI-062) when given,
    falling back to the name slug for legacy lookups."""
    if persona_id:
        return _PROFILES_DIR / f"{persona_id}.json"
    slug = _normalise_name(name)
    if not slug:
        return None
    return _PROFILES_DIR / f"{slug}.json"


def is_valid_name(text: str) -> bool:
    """Heuristic name validation. Rejects empty, too long, mostly-non-alpha."""
    if not text:
        return False
    s = text.strip()
    if not (1 <= len(s) <= 50):
        return False
    # At least 60% alphabetic
    alpha = sum(1 for c in s if c.isalpha())
    return alpha / max(1, len(s)) >= 0.5


def _load_from_path(p: Path) -> Optional[Profile]:
    """Read a profile file path → Profile. Drops persisted fields that no
    longer exist on the Profile dataclass (schema-drift safety)."""
    try:
        raw = json.loads(p.read_text())
    except Exception as e:
        logging.warning("profile_store load failed path=%s: %s", p, e)
        return None
    prof_dict = raw.get("profile") or {}
    valid_fields = set(Profile.__dataclass_fields__.keys())
    prof_dict = {k: v for k, v in prof_dict.items() if k in valid_fields}
    try:
        return Profile(**prof_dict)
    except Exception as e:
        logging.warning("profile_store reconstruct failed path=%s: %s", p, e)
        return None


def load_profile(name: str, *, persona_id: Optional[str] = None) -> Optional[Profile]:
    """Return the stored Profile for `name` (and optional `persona_id`).

    Lookup order (KI-062, 2026-05-15; PRIVACY-HARDENED 2026-05-16):
      1. If `persona_id` given, try that exact file.
      2. Try the name-slug file (legacy + first-visit path before
         identity fields are known).

    PRIVACY FIX (2026-05-16, audit). A former step 3 scanned the WHOLE
    profiles directory and returned any persona-id-keyed file whose stored
    `name_display` matched the requested name slug. That was a pure
    cross-identity leak: a fresh, no-cookie visitor stating a common first
    name ("I'm Rahul") pulled a *stranger's* persona-id profile (different
    age / city / dependents) — exactly the audit's "Welcome back, Rahul"
    finding. The scan has no legitimate use: `save_profile` already writes
    a name-slug file on the first visit (before identity fields exist) and
    only graduates to a persona-id file once enough disambiguating signal
    exists, so a real returning user is resolved by step 1 (their own
    persona_id, passed by the client) or step 2 (their own slug file).
    Matching strangers by bare display name is removed entirely. Cross-name
    recall, when intended, is now gated behind explicit user confirmation
    at the session layer (`session_state.apply_pending_recall`).
    """
    # 1. Direct persona-id hit (the caller's OWN id, never inferred here).
    if persona_id:
        p = _path_for(name, persona_id=persona_id)
        if p and p.exists():
            return _load_from_path(p)
    # 2. Legacy / first-visit name-slug file (this user's own slug file).
    p = _path_for(name)
    if p and p.exists():
        return _load_from_path(p)
    # 3. (REMOVED) cross-identity display-name directory scan — leak vector.
    return None


def save_profile(name: str, profile: Profile, *, session_id: Optional[str] = None) -> bool:
    """Persist `profile`. KI-062 (2026-05-15): files are keyed by
    `compute_persona_id(profile)` when there's enough signal so two users
    named 'Rohit' with different age/location don't overwrite each other.
    Falls back to the name slug when persona_id can't be derived.
    """
    persona_id = compute_persona_id(profile)
    p = _path_for(name, persona_id=persona_id) if persona_id else _path_for(name)
    if not p:
        return False
    try:
        _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if p.exists():
            try:
                existing = json.loads(p.read_text())
            except Exception:
                existing = {}
        # KI-062 — also clean up any older same-name file that was saved
        # before we had enough identity signal to disambiguate. We move
        # its session history into the new file rather than orphaning.
        if persona_id:
            legacy = _path_for(name)
            if legacy and legacy.exists() and legacy.resolve() != p.resolve():
                try:
                    leg_raw = json.loads(legacy.read_text())
                    legacy_sessions = list(leg_raw.get("sessions") or [])
                    existing.setdefault("sessions", [])
                    for s in legacy_sessions:
                        if s not in existing["sessions"]:
                            existing["sessions"].append(s)
                    legacy.unlink()
                except Exception:
                    pass
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sessions = list(existing.get("sessions") or [])
        if session_id and session_id not in sessions:
            sessions.append(session_id)
            sessions = sessions[-20:]  # keep last 20 only
        payload = {
            "name_display": (profile.name or name).strip(),
            "name_slug": _normalise_name(name),
            "persona_id": persona_id,  # KI-062
            "profile": asdict(profile),
            "first_seen": existing.get("first_seen") or now_iso,
            "last_seen": now_iso,
            "sessions": sessions,
        }
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str))
        tmp.replace(p)
        return True
    except Exception as e:
        logging.warning("profile_store save failed name=%s: %s", name, e)
        return False


# ---------------------------------------------------------------------------
# KI-063 (2026-05-15) — per-user policy interaction tracking.
#
# Three event types are tracked on the Profile:
#   shown    — auto-logged by orchestrator when a policy is cited in a
#              recommendation / comparison turn that passed faithfulness.
#   selected — user clicked "save / shortlist" on a policy card (frontend
#              POSTs to /api/profile/select).
#   rejected — user clicked "not for me" (frontend POSTs to /api/profile/reject).
#
# Each entry persists across sessions on the JSON profile, so a returning
# visitor sees their shortlist and the bot can avoid re-pitching rejected
# policies.
# ---------------------------------------------------------------------------

_EVENT_TYPE_TO_FIELD = {
    "shown": "shown_policies",
    "selected": "selected_policies",
    "rejected": "rejected_policies",
}


def record_policy_event(
    persona_id_or_name: str,
    profile: Profile,
    event_type: Literal["shown", "selected", "rejected"],
    policy_slug: str,
    insurer: str,
    session_id: Optional[str] = None,
    reason: Optional[str] = None,
    turn_idx: Optional[int] = None,
) -> bool:
    """Append a single policy-interaction event to the profile and persist.

    Dedup: if the SAME `policy_slug` already exists in the matching list for
    this event_type, the existing entry is updated in place (event_at +
    session_id refreshed) rather than appending a duplicate. This keeps the
    list bounded and chronologically meaningful — repeated shows of the same
    policy collapse to the most recent timestamp.

    Returns True on successful save, False on any failure (missing fields,
    invalid event type, save error).
    """
    if event_type not in _EVENT_TYPE_TO_FIELD:
        return False
    if not policy_slug or not insurer:
        return False
    field_name = _EVENT_TYPE_TO_FIELD[event_type]
    entries: list[dict] = list(getattr(profile, field_name, None) or [])
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    default_reason = {
        "shown": "shown_in_recommendation",
        "selected": "user_clicked_select",
        "rejected": "user_clicked_reject",
    }[event_type]
    payload = {
        "policy_slug": policy_slug,
        "insurer": insurer,
        "event_at": now_iso,
        "session_id": session_id,
        "reason": reason or default_reason,
    }
    # X7 — stamp the conversation_turn index when the caller knows it.
    # Admin Recommendation History reads `conversation_turn` from the event
    # and falls back to "—" when the field is missing/None. Optional so
    # legacy callers (frontend /api/profile/select & /reject) stay valid.
    if turn_idx is not None:
        payload["turn_idx"] = int(turn_idx)
    # Dedup on policy_slug within this event-type list. Bump timestamp +
    # session_id; preserve original reason unless caller passed a new one.
    dedup_idx = next(
        (i for i, e in enumerate(entries) if e.get("policy_slug") == policy_slug),
        None,
    )
    if dedup_idx is not None:
        existing = dict(entries[dedup_idx])
        existing["event_at"] = now_iso
        if session_id:
            existing["session_id"] = session_id
        if reason:
            existing["reason"] = reason
        if turn_idx is not None:
            existing["turn_idx"] = int(turn_idx)
        entries[dedup_idx] = existing
    else:
        entries.append(payload)
    setattr(profile, field_name, entries)
    # Persist through the existing save path so persona-id resolution + Chroma
    # sync (if any) stay consistent.
    save_name = profile.name or persona_id_or_name
    if not save_name:
        return False
    return save_profile(save_name, profile, session_id=session_id)


def get_shortlist(profile: Profile) -> list[dict]:
    """Return the user's selected (shortlisted) policies.

    Thin convenience wrapper used by the admin panel + welcome-back greeting
    so callers don't have to remember the field name.
    """
    return list(getattr(profile, "selected_policies", None) or [])


def list_profiles() -> list[dict]:
    """Return summary of all stored profiles — used by the admin Profile +
    Visitor Log view. One entry per file."""
    if not _PROFILES_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(_PROFILES_DIR.glob("*.json")):
        try:
            raw = json.loads(p.read_text())
            out.append({
                "name_display": raw.get("name_display"),
                "name_slug": raw.get("name_slug"),
                "first_seen": raw.get("first_seen"),
                "last_seen": raw.get("last_seen"),
                "session_count": len(raw.get("sessions") or []),
                "profile_complete_fields": sum(
                    1 for v in (raw.get("profile") or {}).values()
                    if v not in (None, "", [], 0)
                ),
            })
        except Exception:
            continue
    return out
