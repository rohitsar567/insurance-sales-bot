"""Per-session state for multi-turn fact-find continuity (in-memory only).

The orchestrator was originally stateless — each user turn re-classified
intent from scratch. That broke fact-find: after the bot asked "what's
your age?", the user's "39 years old" wasn't matched by intent_classifier
and got routed to RAG retrieval (which then refused). This module fixes that.

Persistence model (KI-118, 2026-05-15):
  - In-memory dict ONLY. No disk persistence.
  - Sessions are evicted from memory after `_TTL_SECONDS = 60 * 60` idle.
  - Cross-session memory is name-based: when the user provides a name,
    `rehydrate_by_name(session, name)` pulls the named profile from
    `backend.profile_store.load_profile(name)` (canonical JSON at
    `40-data/profiles/<persona_id>.json`).
  - Anonymous sessions live only in-memory and never leave a trace on disk.

Rationale: insurance shoppers don't multi-session within a browsing window.
Cross-session memory is name-based. The previous disk-write side
(`40-data/sessions/<session_id>.json`) was the root of the Chroma
corruption fought 2026-05-14/15 (profile_anonymous dangling row).

Public API:
    get_session(session_id) -> SessionState
    rehydrate_by_name(session, name) -> bool   # KI-118 cross-session re-entry
    SessionState.profile, .asked, .awaiting (question id pending answer)
    SessionState.set_awaiting(qid)
    SessionState.record_answer(qid, raw_answer) → also clears awaiting
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from typing import Any, Dict

from backend.needs_finder import Profile, record_answer


# 2026-05-27 — best-effort identity-fact extractors for the same-turn
# match-before-merge guard inside `apply_pending_recall` AND for the
# two-fact gate inside `rehydrate_by_name`. The LLM is the canonical
# extractor (save_profile_field), but it runs AFTER both call sites in
# the turn pipeline — so a "Yes I'm 35" / "Hi I'm Rohit, I'm in
# Mumbai" reply would otherwise either merge the wrong profile or fail
# to stage a legitimate same-turn recall. Mis-parses return None and
# cost nothing.
#
# Coverage:
#   age            — _extract_age_from_text
#   dependents     — _extract_dependents_from_text  (delegates to
#                    brain_tools._normalize_dependents_inline so the
#                    canonical bucket set is honored)
#   location_tier  — _extract_location_tier_from_text  (Indian-city
#                    name → metro / tier1 / tier2 / tier3 lookup)
#   income_band    — _extract_income_band_from_text  (delegates to
#                    needs_finder._parse_income_band)

_AGE_HINT_RE = re.compile(
    r"\b(?:age\s*[:=]?\s*|i'?m\s+|i\s+am\s+|aged\s+|aged\s*[:=]?\s*)?(\d{2})"
    r"\s*(?:years?\s*old|yrs?|y/?o)?\b",
    re.IGNORECASE,
)


def _extract_age_from_text(text: str) -> Optional[int]:
    """Best-effort age extraction from a free-form user reply.
    Returns int in [18, 99] or None. Picks the FIRST plausible match —
    in practice the user states their age once, near the start.
    """
    if not text:
        return None
    for m in _AGE_HINT_RE.finditer(text):
        try:
            v = int(m.group(1))
            if 18 <= v <= 99:
                return v
        except Exception:
            continue
    return None


def _extract_dependents_from_text(text: str) -> Optional[str]:
    """Best-effort dependents bucket from free-form text.
    Delegates to brain_tools._normalize_dependents_inline so the
    canonical bucket set ("self", "self+spouse", "self+spouse+kids",
    "self+parents", "self+spouse+parents", "self+spouse+kids+parents",
    "self+kids") is honored. Returns None on no match.
    """
    if not text:
        return None
    try:
        # Lazy import to avoid load-order issues at module init.
        from backend.brain_tools import _normalize_dependents_inline
    except Exception:
        return None
    try:
        return _normalize_dependents_inline(text)
    except Exception:
        return None


# Indian-city → tier lookup. Tier-1 list per the IRDAI / RBI city
# classification (Mumbai/Delhi/Bangalore/Chennai/Kolkata/Hyderabad =
# Tier-1 "metro" in insurance vernacular). Bot's pricing uses "metro"
# as the high-tier loading bucket — keep that as the alias here so a
# user typing "Mumbai" maps to the same bucket the LLM would store.
_LOCATION_TIER_MAP: dict[str, str] = {
    # Metro / Tier-1
    "mumbai": "metro", "bombay": "metro",
    "delhi": "metro", "new delhi": "metro",
    "bangalore": "metro", "bengaluru": "metro",
    "kolkata": "metro", "calcutta": "metro",
    "chennai": "metro", "madras": "metro",
    "hyderabad": "metro",
    "metro": "metro", "tier 1": "tier1", "tier1": "tier1", "tier-1": "tier1",
    # Tier-2
    "pune": "tier2", "ahmedabad": "tier2", "jaipur": "tier2",
    "lucknow": "tier2", "kanpur": "tier2", "nagpur": "tier2",
    "indore": "tier2", "thane": "tier2", "bhopal": "tier2",
    "visakhapatnam": "tier2", "vizag": "tier2",
    "patna": "tier2", "vadodara": "tier2", "baroda": "tier2",
    "ghaziabad": "tier2", "ludhiana": "tier2", "agra": "tier2",
    "nashik": "tier2", "faridabad": "tier2", "meerut": "tier2",
    "rajkot": "tier2", "varanasi": "tier2", "srinagar": "tier2",
    "aurangabad": "tier2", "amritsar": "tier2", "navi mumbai": "tier2",
    "allahabad": "tier2", "prayagraj": "tier2", "ranchi": "tier2",
    "howrah": "tier2", "coimbatore": "tier2", "jabalpur": "tier2",
    "gwalior": "tier2", "vijayawada": "tier2", "jodhpur": "tier2",
    "raipur": "tier2", "kota": "tier2", "chandigarh": "tier2",
    "guwahati": "tier2", "solapur": "tier2", "hubli": "tier2",
    "mysore": "tier2", "mysuru": "tier2",
    "tier 2": "tier2", "tier2": "tier2", "tier-2": "tier2",
    # Tier-3 buckets (rest)
    "tier 3": "tier3", "tier3": "tier3", "tier-3": "tier3",
    "small town": "tier3", "village": "tier3", "rural": "tier3",
}


def _extract_location_tier_from_text(text: str) -> Optional[str]:
    """Best-effort location_tier from a free-form user reply.
    Returns "metro" / "tier1" / "tier2" / "tier3" or None.
    Matches whole tokens / phrases against _LOCATION_TIER_MAP.
    """
    if not text:
        return None
    s = text.lower()
    # Longest-key-first so "navi mumbai" beats "mumbai".
    for key in sorted(_LOCATION_TIER_MAP.keys(), key=len, reverse=True):
        # Word-boundary match for single-word keys; substring for
        # multi-word keys (which already have natural boundaries).
        if " " in key:
            if key in s:
                return _LOCATION_TIER_MAP[key]
        else:
            if re.search(rf"\b{re.escape(key)}\b", s):
                return _LOCATION_TIER_MAP[key]
    return None


def _extract_income_band_from_text(text: str) -> Optional[str]:
    """Best-effort income_band from free-form text.
    Delegates to needs_finder._parse_income_band so the canonical
    bucket set (under_5L / 5L-10L / 10L-25L / 25L+) is honored.
    """
    if not text:
        return None
    try:
        from backend.needs_finder import _parse_income_band
    except Exception:
        return None
    try:
        return _parse_income_band(text)
    except Exception:
        return None


def _parse_user_text_facts(user_text: str) -> Dict[str, Any]:
    """Run all four identity-fact extractors against user_text. Returns
    a dict of only the fields that successfully parsed. Used by both
    the two-fact recall gate (rehydrate_by_name) and the same-turn
    match-before-merge guard (apply_pending_recall).
    """
    if not user_text:
        return {}
    out: Dict[str, Any] = {}
    for fld, fn in (
        ("age", _extract_age_from_text),
        ("dependents", _extract_dependents_from_text),
        ("location_tier", _extract_location_tier_from_text),
        ("income_band", _extract_income_band_from_text),
    ):
        v = fn(user_text)
        if v not in (None, "", []):
            out[fld] = v
    return out

_log = logging.getLogger(__name__)


@dataclass
class SessionState:
    session_id: str
    profile: Profile = field(default_factory=Profile)
    awaiting_question_id: Optional[str] = None  # if set, next user message answers this
    free_form_session: bool = False              # user explicitly opted out of fact-find
    last_touched: float = field(default_factory=time.time)
    # KI-196 (ADR-041) — confirmation-gated profile recall. When a fresh
    # session captures a name that matches an on-disk profile, the recall
    # is staged here (NOT auto-merged) and surfaced to the sales_brain as a
    # one-shot "welcome back" prompt. Affirm → merge stored fields into
    # `profile`. Negate → discard. Shape:
    #   {
    #     "name": "Rohit Sarma",
    #     "summary": {age, dependents, location_tier, primary_goal, ...},
    #     "captured_this_turn": {<field>: <value>, ...},  # don't re-extract
    #     "staged_at": <epoch-seconds>,
    #   }
    pending_profile_recall: Optional[Dict[str, Any]] = None
    # KI-224 — most-recent recommendation policy_ids the brain cited on the
    # last user-visible recommendation/comparison turn. Populated by the
    # orchestrator after a clean closer reply. Lets the NEXT turn route
    # follow-ups like "tell me more about #2" without re-retrieving from
    # scratch. Empty list = no active shortlist on this session.
    last_recommendation_ids: list = field(default_factory=list)
    # X7 (admin Recommendation History — conversation_turn column).
    # Monotonically incremented at the START of every orchestrator.handle_turn
    # and single_brain.handle_turn call so the policy-event writer can stamp
    # `turn_idx` on each event dict. Frontend renders this as the
    # "Conversation turn" column in the admin Recommendation History panel
    # (previously showed "—" because no caller populated the field).
    turn_idx: int = 0
    # Set True after the first successful single_brain turn; a later
    # SingleBrainError on the same session then emits a graceful retry
    # prompt instead of switching handlers, so the session stays on
    # single_brain.
    single_brain_sticky: bool = False
    # Post-recap pricing & family-history bundle re-ask gate
    # (brain_tools.retrieve_policies):
    #   pricing_bundle_reasked — one-shot guard; set True the first time
    #     the gate re-asks an unresolved bundle slot so the next
    #     recommendation retrieve proceeds even if the user skips.
    #   pricing_bundle_skipped — set True by single_brain when the user
    #     explicitly declines the pricing inputs; bypasses the re-ask.
    pricing_bundle_reasked: bool = False
    pricing_bundle_skipped: bool = False
    # Bug #25 (2026-05-19) — one-shot guard for returning-user recall.
    # The old wiring only probed on turn 1, but the fact-find asks the
    # name in the bot's FIRST reply, so the name lands on turn >=2 and
    # recall never fired. The probe now runs whenever the name is first
    # known (any turn); this flag stops it re-staging every subsequent
    # turn and stops a declined recall from being re-offered.
    recall_probe_done: bool = False
    # ADR-042 follow-up #1 (2026-05-27) — two-fact recall gate. When
    # rehydrate_by_name finds a stored profile under the captured name
    # but the live session has NO identity-fact match (and user_text
    # carried no parseable fact either), it sets this flag instead of
    # staging. The caller (single_brain.py) sees this and DOES NOT set
    # recall_probe_done=True, so the probe retries on subsequent turns
    # as more facts come in via save_profile_field. Reset to False by
    # the caller every retry. Prevents the slug-collision Welcome-Back
    # leak from ever firing on a bare-name intro.
    recall_match_deferred: bool = False

    def _flush(self) -> None:
        """No-op. Session state lives only in the in-memory dict; the
        method is kept so callers' write paths don't have to change.
        """
        return None

    def set_awaiting(self, question_id: Optional[str]) -> None:
        self.awaiting_question_id = question_id
        self.last_touched = time.time()

    def record_user_answer(self, raw_answer: str) -> Optional[str]:
        """If we're awaiting an answer, parse + store it. Returns the answered question_id."""
        if not self.awaiting_question_id:
            return None
        qid = self.awaiting_question_id
        record_answer(self.profile, qid, raw_answer)
        self.awaiting_question_id = None
        self.last_touched = time.time()
        return qid

    def update_profile_field(self, name: str, value) -> None:
        """Set a Profile attribute. Used by /api/profile."""
        if hasattr(self.profile, name):
            setattr(self.profile, name, value)
            self.last_touched = time.time()


_sessions: dict[str, SessionState] = {}
_lock = Lock()
_TTL_SECONDS = 60 * 60         # 1h idle → evict from in-memory cache


def get_session(session_id: str) -> SessionState:
    with _lock:
        now = time.time()
        # Evict idle entries from the hot cache
        to_kill = [k for k, v in _sessions.items() if now - v.last_touched > _TTL_SECONDS]
        for k in to_kill:
            del _sessions[k]
        if session_id in _sessions:
            return _sessions[session_id]
        # KI-118 — no disk lookup; fresh sessions start blank. Cross-session
        # rehydration happens via rehydrate_by_name() when the user provides
        # their name to the fact_find brain.
        _sessions[session_id] = SessionState(session_id=session_id)
        return _sessions[session_id]


# Identity-summary fields surfaced in the "are you <name>?" confirm prompt.
# Enough to let the real owner recognise their own profile, but it is NOT
# applied to the live session until the user explicitly confirms.
_RECALL_SUMMARY_FIELDS: tuple[str, ...] = (
    "age", "dependents", "income_band", "location_tier",
    "primary_goal", "parents_age_max",
)


def rehydrate_by_name(
    session: SessionState,
    name: str,
    *,
    user_text: str = "",
) -> bool:
    """Cross-session re-entry point — STAGE a name match for confirmation.

    PRIVACY FIX (2026-05-16, audit). Previously this AUTO-MERGED the stored
    named profile into the live session on the very first turn. Because the
    lookup key was the user-stated NAME (not the session / no cookie), a
    second real user on a shared browser/IP — or anyone who simply states a
    common first name — was silently served a stranger's captured profile
    and greeted "Welcome back, <name>!". A fresh, no-cookie session must
    NEVER inherit another session's profile from a weak/shared key.

    PRIVACY HARDENING v4 (2026-05-27, ADR-042 follow-up #1) — TWO-FACT
    GATE. Even with the explicit confirm gate in apply_pending_recall, a
    bare-name "Hi I'm Rohit" would still LEAK staged attrs in the
    Welcome Back prompt (the brain's recall_block was redacted in v1/v2,
    but the very *existence* of a Welcome Back prompt telegraphs that
    SOMEONE under this name has used the bot before). The two-fact gate
    blocks the staging entirely unless at least ONE identity fact —
    drawn from live `session.profile` (prior-turn captures) OR parsed
    from `user_text` (same-turn, via _parse_user_text_facts) — MATCHES
    the stored profile. If a parsed fact CONTRADICTS, staging is also
    refused (no leak to a different person sharing the name slug). If
    no identity facts are available yet (bare name), staging is
    DEFERRED — `session.recall_match_deferred=True` signals the caller
    to retry on a later turn as more facts come in.

    Safe design (KI-196 / ADR-041, specced via `pending_profile_recall` but
    previously never wired): a name match is STAGED on
    `session.pending_profile_recall`, NOT merged. `session.profile` is left
    untouched, so `is_returning_user` / RULE-4 "Welcome back" does NOT fire
    on a fresh session. The brain asks the user to confirm ("are you
    <name>?"); only an explicit affirmation calls `apply_pending_recall(
    session, confirmed=True)` to merge the stored fields. An explicit deny
    discards the staged profile.

    Returns:
        False  — always. The stored profile is NEVER auto-applied here, so
                 callers must treat False as "do not flag a returning user
                 / do not greet Welcome back". Whether a match was *staged*
                 is observable via `session.pending_profile_recall`; the
                 deferred-retry signal is `session.recall_match_deferred`.

    Failures are logged but never raise — a fresh chat must always proceed.
    """
    if not name or not name.strip():
        return False
    try:
        from backend.profile_store import load_profile
        stored = load_profile(name)
        if stored is None:
            # Bug #25 (2026-05-19): a multi-token capture ("Rohit Sar")
            # slugs to "rohit-sar" and misses the stored first-name file
            # ("rohit.json"). Fall back to the first name token. Still
            # privacy-safe — this only STAGES a match; the user must
            # explicitly confirm the identity summary before any merge.
            _stripped = (name or "").strip()
            _first = _stripped.split()[0] if _stripped else ""
            if _first and _first.lower() != _stripped.lower():
                stored = load_profile(_first)
            if stored is None:
                # No stored profile under this name. Done — no recall
                # opportunity, no deferral needed.
                session.recall_match_deferred = False
                return False

        # ─── TWO-FACT GATE (v4, 2026-05-27) ───────────────────────────
        # Require at least ONE non-name identity fact to match the
        # stored profile before staging. Sources for the fact:
        #   (1) session.profile — already-captured live facts (any
        #       prior turn of this same session)
        #   (2) user_text — same-turn parse via _parse_user_text_facts
        # Any CONTRADICTION fails closed (no stage). No fact available
        # ⇒ defer (set session.recall_match_deferred=True so the
        # single_brain caller does NOT mark recall_probe_done, and the
        # probe retries next turn as more facts come in).
        same_turn_facts = _parse_user_text_facts(user_text)
        matched_fact = False
        contradicted_fact = False
        for fld in ("age", "dependents", "location_tier", "income_band"):
            stored_v = getattr(stored, fld, None)
            if stored_v in (None, "", []):
                continue
            # Source 1: prior-turn live capture
            live_v = getattr(session.profile, fld, None)
            if live_v not in (None, "", []):
                if fld == "age":
                    try:
                        if int(live_v) == int(stored_v):
                            matched_fact = True
                        else:
                            contradicted_fact = True
                    except Exception:
                        pass
                else:
                    if str(live_v).strip().lower() == str(stored_v).strip().lower():
                        matched_fact = True
                    else:
                        contradicted_fact = True
                continue
            # Source 2: same-turn parse from user_text
            user_v = same_turn_facts.get(fld)
            if user_v not in (None, "", []):
                if fld == "age":
                    try:
                        if int(user_v) == int(stored_v):
                            matched_fact = True
                        else:
                            contradicted_fact = True
                    except Exception:
                        pass
                else:
                    if str(user_v).strip().lower() == str(stored_v).strip().lower():
                        matched_fact = True
                    else:
                        contradicted_fact = True

        if contradicted_fact:
            # Any contradicting identity fact ⇒ fail-closed. No stage.
            # Mark probe done so we don't try again — the stored
            # profile is for a DIFFERENT person sharing this name slug.
            session.recall_match_deferred = False
            _log.info(
                "rehydrate_by_name: identity-fact contradiction for "
                "name=%r — fail-closed, no stage", name,
            )
            return False
        if not matched_fact:
            # No fact to confirm a match yet. Defer staging until a
            # later turn has captured a fact via save_profile_field.
            session.recall_match_deferred = True
            session.last_touched = time.time()
            return False

        # ─── At this point: stored exists AND ≥1 fact matches. Stage. ─

        # Build a non-PII-leaking identity summary so the brain can ask
        # "are you <name>?" without putting anything on the live profile.
        summary: Dict[str, Any] = {}
        for fld in _RECALL_SUMMARY_FIELDS:
            v = getattr(stored, fld, None)
            if v not in (None, "", []):
                summary[fld] = v

        # Snapshot the full stored union so apply_pending_recall can merge
        # WITHOUT a second disk read (and without the staged copy being
        # mutated by anything between stage and confirm).
        staged_fields: Dict[str, Any] = {}
        for fld in Profile.__dataclass_fields__.keys():
            v = getattr(stored, fld, None)
            if v not in (None, "", []):
                staged_fields[fld] = v

        session.pending_profile_recall = {
            "name": (getattr(stored, "name", None) or name).strip(),
            "summary": summary,
            "stored_fields": staged_fields,
            "staged_at": time.time(),
        }
        session.recall_match_deferred = False
        session.last_touched = time.time()
        # Deliberately False: nothing merged, no Welcome-back greeting.
        return False
    except Exception as e:
        _log.warning(
            "rehydrate_by_name failed (name=%r): %s: %s",
            name, type(e).__name__, str(e)[:200],
        )
        return False


def apply_pending_recall(
    session: SessionState,
    *,
    confirmed: bool,
    user_text: str = "",
) -> bool:
    """Resolve a staged cross-session profile recall.

    PRIVACY FIX (2026-05-16). The ONLY path that merges a stored, name-keyed
    profile into a live session. Called after the user explicitly answers
    the "are you <name>?" confirm prompt.

    PRIVACY HARDENING (2026-05-27). The brain prompt no longer discloses
    staged attrs (single_brain.py recall_block). To compensate for the
    accidental / mistaken "yes" path, we now run a **match-before-merge**
    contradiction check: if the live profile has captured any identity
    fact in this conversation that CONTRADICTS the staged recall (e.g.
    user said age=29 but staged.age=34), we discard the staged recall
    entirely — no partial merge. Empty live slots (no contradiction
    possible) are still filled from staged on confirmed=True.

    confirmed=True  — the user affirmed it's them. Stored fields fill any
                      EMPTY slot on the live profile — UNLESS any live slot
                      contradicts the staged value (then the whole staged
                      recall is dropped). Returns True iff a profile was
                      applied.
    confirmed=False — the user denied / it isn't them. The staged recall is
                      discarded. The live session stays blank. Returns False.

    Idempotent: clears `pending_profile_recall` either way. A no-op (returns
    False) when there is nothing staged.
    """
    pending = getattr(session, "pending_profile_recall", None)
    if not pending:
        return False
    # Resolve the staging regardless of outcome.
    session.pending_profile_recall = None
    # Bug #25 (2026-05-19) — a confirmed OR denied recall is final for
    # this session; never auto-re-stage / re-offer it on a later turn.
    session.recall_probe_done = True
    if not confirmed:
        return False
    stored_fields = pending.get("stored_fields") or {}
    if not stored_fields:
        return False
    # PRIVACY HARDENING (2026-05-27) — match-before-merge guard.
    # Fields chosen here are decision-critical identity facts where a
    # mismatch unambiguously means "different person" (vs. e.g.
    # health_conditions which evolves between visits).
    _GUARD_FIELDS = (
        "age", "dependents", "income_band", "location_tier",
        "primary_goal", "parents_age_max",
    )
    for fld in _GUARD_FIELDS:
        live_v = getattr(session.profile, fld, None)
        staged_v = stored_fields.get(fld)
        if live_v in (None, "", []) or staged_v in (None, "", []):
            continue
        # Normalise to compare. Int and string forms of age both common.
        if str(live_v).strip().lower() != str(staged_v).strip().lower():
            _log.info(
                "apply_pending_recall: prior-turn contradiction on %s "
                "(live=%r, staged=%r) — discarding staged recall, "
                "no merge",
                fld, live_v, staged_v,
            )
            return False
    # PRIVACY HARDENING v2 (2026-05-27) — same-turn contradiction guard.
    # _affirm_or_deny + apply_pending_recall fire BEFORE the LLM iteration
    # that runs save_profile_field, so a user reply like "Yes I'm 35"
    # against a staged Rohit at age=29 would otherwise slip through the
    # prior-turn guard (live.<fld>=None at this point) and merge the
    # wrong profile.
    #
    # PRIVACY HARDENING v3 (2026-05-27, ADR-042 follow-up #2): extended
    # from age-only to all four decision-critical identity facts
    # (age / dependents / location_tier / income_band) via
    # _parse_user_text_facts. Mis-parses return None and cost nothing.
    if user_text:
        same_turn_facts = _parse_user_text_facts(user_text)
        for fld, user_v in same_turn_facts.items():
            staged_v = stored_fields.get(fld)
            if staged_v in (None, "", []):
                continue
            # Normalise. Age is the only numeric; the rest are strings.
            if fld == "age":
                try:
                    staged_norm = int(staged_v)
                    user_norm = int(user_v)
                except Exception:
                    continue
                if staged_norm != user_norm:
                    _log.info(
                        "apply_pending_recall: same-turn age contradiction "
                        "(user_text age=%d, staged.age=%s) — discarding "
                        "staged recall, no merge",
                        user_norm, staged_v,
                    )
                    return False
            else:
                if str(user_v).strip().lower() != str(staged_v).strip().lower():
                    _log.info(
                        "apply_pending_recall: same-turn %s contradiction "
                        "(user_text=%r, staged=%r) — discarding staged "
                        "recall, no merge",
                        fld, user_v, staged_v,
                    )
                    return False
    for fld, new in stored_fields.items():
        try:
            if fld not in Profile.__dataclass_fields__:
                continue
            cur = getattr(session.profile, fld, None)
            if cur in (None, "", []) and new not in (None, "", []):
                setattr(session.profile, fld, new)
        except Exception:
            continue
    session.last_touched = time.time()
    return True


def set_free_form(session_id: str, free_form: bool = True) -> None:
    s = get_session(session_id)
    s.free_form_session = free_form
    s.awaiting_question_id = None
    s.last_touched = time.time()


def reset_session(session_id: str) -> bool:
    """Delete a session — evict from in-memory cache.
    Returns True if anything was actually deleted.

    KI-020 (2026-05-14) — backs the user-facing "Clear chat / start fresh" toggle.
    KI-118 (2026-05-15) — no disk file to remove anymore; in-memory eviction
    is the only side effect. Returns True iff the session id was live.
    """
    with _lock:
        if session_id in _sessions:
            del _sessions[session_id]
            return True
    return False


def clear_session(session_id: str) -> bool:
    """KI-196 (ADR-041) — Wipe in-memory state for one session_id WITHOUT
    touching any on-disk profile JSON under `40-data/profiles/`.

    Semantically identical to `reset_session` today (both just evict the
    in-memory entry; the disk profile has always been independent and lives
    by persona_id / name slug, not session_id). Kept as a distinct symbol so
    the call-site intent at `POST /api/session/clear` is self-documenting and
    so future divergence (e.g. partial-state wipes) doesn't require touching
    the legacy KI-020 caller.

    Returns True iff a live in-memory session was evicted.
    """
    with _lock:
        if session_id in _sessions:
            del _sessions[session_id]
            return True
    return False


def purge_old_files() -> int:
    """KI-118 (2026-05-15) — no-op. Disk persistence was removed; there are
    no files to purge. Kept as a stub so any existing scheduled-task caller
    (cron / startup hook) doesn't crash on attribute miss.
    """
    return 0
