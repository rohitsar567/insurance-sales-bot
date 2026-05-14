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

import json
import logging
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.needs_finder import Profile

_PROFILES_DIR = settings.CORPUS_DIR.parent.parent / "40-data" / "profiles"


def _normalise_name(name: str) -> str:
    """Lowercase + strip to alphanumerics. 'Rohit Sharma' → 'rohit-sharma'."""
    if not name:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return cleaned[:60]  # cap filename length


def _path_for(name: str) -> Optional[Path]:
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


def load_profile(name: str) -> Optional[Profile]:
    """Return the stored Profile for `name`, or None if no record exists.

    Future-proofs against Profile schema drift — any persisted fields that no
    longer exist on the Profile dataclass are silently dropped.
    """
    p = _path_for(name)
    if not p or not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except Exception as e:
        logging.warning("profile_store load failed name=%s: %s", name, e)
        return None
    prof_dict = raw.get("profile") or {}
    valid_fields = set(Profile.__dataclass_fields__.keys())
    prof_dict = {k: v for k, v in prof_dict.items() if k in valid_fields}
    try:
        return Profile(**prof_dict)
    except Exception as e:
        logging.warning("profile_store reconstruct failed name=%s: %s", name, e)
        return None


def save_profile(name: str, profile: Profile, *, session_id: Optional[str] = None) -> bool:
    """Persist `profile` keyed by `name`. Returns True on success."""
    p = _path_for(name)
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
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sessions = list(existing.get("sessions") or [])
        if session_id and session_id not in sessions:
            sessions.append(session_id)
            sessions = sessions[-20:]  # keep last 20 only
        payload = {
            "name_display": (profile.name or name).strip(),
            "name_slug": _normalise_name(name),
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
