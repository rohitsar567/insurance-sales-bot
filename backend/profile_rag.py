"""Customer-profile-as-RAG layer.

When a user saves their profile (POST /api/profile), the profile dict is
serialised into a natural-language paragraph and ingested into the same
Chroma collection that holds policy + regulatory chunks. Metadata fields
`doc_type='profile'` and `policy_id='profile_<session_id>'` distinguish it.

At retrieval time, `rag/retrieve.py::retrieve(..., session_id=...)` can
preferentially boost the matching profile chunk so the LLM sees the user's
context inline with the retrieved policy/regulatory text — answers become
personalised at the BRAIN level, not just at scorecard re-weighting.

This is what the user meant by "we need an architecture to store customer
profiles for RAG, complementing the brain alongside policy + regulation".

Public API:
    profile_to_chunk_text(profile_dict) -> str
        Render the structured profile as a single English paragraph.
    upsert_profile_chunk(session_id, profile_dict, embedder) -> None
        Ingest / update the chunk for this session in Chroma.
    remove_profile_chunk(session_id) -> None
        Optional cleanup on session expiry.

Storage model — one chunk per session_id. Replaced on each profile update.
Profile chunks live in the SAME collection as policies so retrieval can
naturally surface them when scoring policies for the user.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from backend.config import settings

_log = logging.getLogger(__name__)


def profile_to_chunk_text(profile: dict) -> str:
    """Render the profile dict as a natural-language paragraph for the LLM.

    The shape matches what build_messages() in the orchestrator expects so
    when this chunk is retrieved alongside policy chunks, the LLM sees a
    coherent "USER CONTEXT" block.
    """
    parts: list[str] = ["USER CONTEXT — facts about the person asking this question:"]

    age = profile.get("age")
    if isinstance(age, int):
        parts.append(f"- Age: {age} years.")

    deps = profile.get("dependents")
    if isinstance(deps, str) and deps:
        parts.append(f"- Covering: {deps.replace('_', ' ').replace('+', ' + ')}.")

    parents_age = profile.get("parents_age_max")
    parents_ped = profile.get("parents_has_ped")
    if parents_age:
        parents_line = f"- Older parent's age: {parents_age}."
        if parents_ped is True:
            parents_line += " Parents have pre-existing conditions (diabetes / BP / heart etc.)."
        elif parents_ped is False:
            parents_line += " Parents are healthy with no flagged conditions."
        parts.append(parents_line)

    conditions = profile.get("health_conditions")
    if isinstance(conditions, list) and conditions:
        cstr = ", ".join(str(c) for c in conditions)
        parts.append(f"- User's own pre-existing conditions: {cstr}.")
    elif conditions == []:
        parts.append("- User has no pre-existing conditions disclosed.")

    existing = profile.get("existing_cover_inr")
    if existing == 0:
        parts.append("- First-time buyer; no existing health insurance.")
    elif isinstance(existing, int) and existing > 0:
        if existing >= 100000:
            parts.append(f"- Already has ₹{existing // 100000}L of existing health cover.")
        else:
            parts.append(f"- Already has ₹{existing} of existing health cover.")

    goal = profile.get("primary_goal")
    if isinstance(goal, str) and goal:
        goal_str = goal.replace("_", " ")
        parts.append(f"- Goal today: {goal_str}.")

    loc = profile.get("location_tier")
    if isinstance(loc, str) and loc:
        parts.append(f"- City tier: {loc}.")

    budget = profile.get("budget_band")
    if isinstance(budget, str) and budget:
        parts.append(f"- Annual premium budget: {budget.replace('_', '-').replace('-', ' - ')}.")

    income = profile.get("income_band")
    if isinstance(income, str) and income:
        parts.append(f"- Annual income band: {income}.")

    if len(parts) == 1:
        return "USER CONTEXT — no profile info collected yet."
    parts.append(
        "Use these facts when scoring or recommending. The user has consented to share them; "
        "honesty about conditions protects their later claim, so weight disclosed conditions "
        "explicitly in the recommendation rationale."
    )
    return "\n".join(parts)


def _get_collection():
    """Lazy-import Chroma to keep startup time low when not needed."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    client = chromadb.PersistentClient(
        path=str(settings.VECTORS_DIR),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name="policies",
        metadata={"hnsw:space": "cosine"},
    )


async def upsert_profile_chunk(session_id: str, profile_dict: dict) -> None:
    """Embed the profile paragraph and store as a single chunk in Chroma.

    Idempotent — calling this on every profile update is safe; existing
    chunks for the same session_id get replaced.
    """
    from backend.providers.local_embeddings import LocalEmbeddings

    text = profile_to_chunk_text(profile_dict)
    if not text or len(text) < 30:
        return

    # KI-112 (2026-05-15) — input guard 1: session_id must be a non-empty str.
    # Pre-fix, a missing/empty session_id caused upsert under id "profile_"
    # with `policy_id` colliding across all anonymous sessions — and the
    # initial KI-102 deploy wrote a `profile_anonymous` chunk WITHOUT a
    # session_id metadata field. That legacy chunk poisoned every subsequent
    # query whose `where` clause referenced session_id ($ne / $eq) — Chroma's
    # HNSW + metadata-filter plan executor raised "Error finding id" against
    # the dangling row. Reject early with a noisy log so the bad write never
    # reaches Chroma.
    if not isinstance(session_id, str) or not session_id.strip():
        _log.warning(
            "profile_rag.upsert_profile_chunk: refusing to write — session_id "
            "must be a non-empty str, got %r. Profile not persisted.",
            session_id,
        )
        return

    embedder = LocalEmbeddings()
    [vec] = await embedder.embed([text], input_type="document")

    # KI-112 (2026-05-15) — input guard 2: embedding must be a list of finite
    # floats whose length matches the embedder's declared dimension. Pre-fix,
    # an empty / None / mis-shaped embedding could be added to Chroma where it
    # would silently corrupt HNSW (dangling pointer or shape mismatch). The
    # corpus uses 384-dim BAAI/bge-small-en-v1.5; any other shape is a bug.
    expected_dim = getattr(embedder, "dimension", None) or 384
    if (
        not isinstance(vec, (list, tuple))
        or len(vec) != expected_dim
        or any((v is None) for v in vec)
    ):
        _log.warning(
            "profile_rag.upsert_profile_chunk: refusing to write — embedding "
            "shape invalid for session_id=%s (expected %d-dim list of floats, "
            "got type=%s len=%s). Profile not persisted.",
            session_id, expected_dim, type(vec).__name__,
            (len(vec) if hasattr(vec, "__len__") else "?"),
        )
        return

    coll = _get_collection()
    chunk_id = f"profile_{session_id}"

    # Replace any existing chunk for this session
    try:
        coll.delete(where={"policy_id": chunk_id})
    except Exception as e:
        # Non-fatal: chunk may not exist yet. KI-107 — at least log so
        # silent corruption of the profile store is observable.
        _log.debug(
            "profile_rag.upsert_profile_chunk: delete(where=policy_id=%s) "
            "non-fatal failure: %s: %s",
            chunk_id, type(e).__name__, str(e)[:200],
        )

    # KI-107 (2026-05-15) — wrap coll.add() in try/except. C5 port-in saw
    # 3× HTTP 500 "Error finding id" cascade; one possible vector is a
    # transient Chroma sqlite lock during HNSW compaction when add()
    # interleaves with the retrieve path's get(). Make upsert non-fatal so
    # the chat reply still returns even if the profile-chunk write fails.
    # On next upsert (next profile field change), the retry will succeed.
    try:
        coll.add(
            ids=[chunk_id],
            documents=[text],
            embeddings=[vec],
            metadatas=[{
                "policy_id": chunk_id,
                "insurer_slug": "profile",
                "policy_name": f"User profile (session {session_id[:8]})",
                "doc_type": "profile",
                # KI-102 (2026-05-15) — privacy P0. Stamp the owning session_id so
                # the retrieve path can hard-exclude any OTHER session's profile
                # chunk via where={"session_id": current}. Pre-fix, profile chunks
                # had no session_id metadata and the main retrieval pass had no
                # doc_type filter, so chunks from sessions smokeA_1, ki100_ve, etc.
                # surfaced as cosine matches in session smokeB_B4's context —
                # leaking one user's profile facts into another user's reply.
                "session_id": session_id,
                "source_url": "",
                "page_start": 0,
                "page_end": 0,
                "chunk_idx": 0,
                "local_path": "in-memory session profile",
            }],
        )
    except Exception as e:
        _log.warning(
            "profile_rag.upsert_profile_chunk: add(id=%s) failed: %s: %s — "
            "user reply will proceed without per-session profile context; "
            "next profile change will retry.",
            chunk_id, type(e).__name__, str(e)[:200],
        )


def remove_profile_chunk(session_id: str) -> None:
    """Optional cleanup. Called on session expiry (1h TTL in session_state)."""
    try:
        coll = _get_collection()
        coll.delete(where={"policy_id": f"profile_{session_id}"})
    except Exception:
        pass


def upsert_profile_chunk_sync(session_id: str, profile_dict: dict) -> None:
    """Sync wrapper for callers that aren't async — schedules + waits."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already inside an async context — schedule on the loop
            asyncio.ensure_future(upsert_profile_chunk(session_id, profile_dict))
            return
    except RuntimeError:
        pass
    asyncio.run(upsert_profile_chunk(session_id, profile_dict))
