"""Customer-profile-as-RAG layer.

Profile chunks are keyed by `name_slug` (the canonicalised user name),
NOT by session_id. Only NAMED users ever get embedded; anonymous sessions
never write to Chroma. Keying by name_slug avoids the corruption surface
of session_id-keyed chunks (an anonymous row missing a session_id
metadata field could poison session_id-scoped retrieval queries).

At retrieval time, `rag/retrieve.py::retrieve(..., profile_name_slug=...)`
boosts the user's profile chunk so the LLM sees the user's context inline
with the retrieved policy/regulatory text — answers become personalised at
the BRAIN level, not just at scorecard re-weighting.

Public API:
    profile_to_chunk_text(profile_dict) -> str
        Render the structured profile as a single English paragraph.
    upsert_profile_chunk(name_slug, profile_dict, embedder) -> None
        Ingest / update the chunk for this named user in Chroma.
    remove_profile_chunk(name_slug) -> None
        Optional cleanup.

Storage model — one chunk per name_slug. Replaced on each profile update.
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

    Rendered as a coherent "USER CONTEXT" block so that when this chunk is
    retrieved alongside policy chunks, the LLM sees the user's facts inline.
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


async def upsert_profile_chunk(name_slug: str, profile_dict: dict) -> None:
    """Embed the profile paragraph and store as a single chunk in Chroma.

    Keyed by `name_slug` (canonical user name), not `session_id`. Only
    NAMED users ever get embedded — anonymous chats never write to Chroma.

    Idempotent — calling this on every profile update is safe; existing
    chunks for the same name_slug get replaced.
    """
    from backend.providers.local_embeddings import LocalEmbeddings

    text = profile_to_chunk_text(profile_dict)
    if not text or len(text) < 30:
        return

    # Input guard 1: name_slug must be a non-empty str. An empty key would
    # upsert under id "profile_" and collide across anonymous sessions.
    # Callers gate on `session.profile.name` before calling, so anonymous
    # users do not reach this function; this guard is belt-and-braces.
    if not isinstance(name_slug, str) or not name_slug.strip():
        _log.warning(
            "profile_rag.upsert_profile_chunk: refusing to write — name_slug "
            "must be a non-empty str, got %r. Profile not persisted.",
            name_slug,
        )
        return

    embedder = LocalEmbeddings()
    [vec] = await embedder.embed([text], input_type="document")

    # Input guard 2: embedding must be a list of finite floats whose length
    # matches the embedder's declared dimension. An empty / None / mis-shaped
    # embedding added to Chroma would silently corrupt HNSW (dangling
    # pointer or shape mismatch). The corpus uses 384-dim
    # BAAI/bge-small-en-v1.5; any other shape is rejected.
    expected_dim = getattr(embedder, "dimension", None) or 384
    if (
        not isinstance(vec, (list, tuple))
        or len(vec) != expected_dim
        or any((v is None) for v in vec)
    ):
        _log.warning(
            "profile_rag.upsert_profile_chunk: refusing to write — embedding "
            "shape invalid for name_slug=%s (expected %d-dim list of floats, "
            "got type=%s len=%s). Profile not persisted.",
            name_slug, expected_dim, type(vec).__name__,
            (len(vec) if hasattr(vec, "__len__") else "?"),
        )
        return

    coll = _get_collection()
    chunk_id = f"profile_{name_slug}"

    # Replace any existing chunk for this name
    try:
        coll.delete(where={"policy_id": chunk_id})
    except Exception as e:
        # Non-fatal: chunk may not exist yet. Log so a silent failure of
        # the profile store is observable.
        _log.debug(
            "profile_rag.upsert_profile_chunk: delete(where=policy_id=%s) "
            "non-fatal failure: %s: %s",
            chunk_id, type(e).__name__, str(e)[:200],
        )

    # Wrap coll.add() in try/except so upsert is non-fatal: a transient
    # Chroma sqlite lock during HNSW compaction (add() interleaving with
    # the retrieve path's get()) must not break the chat reply. The next
    # upsert (next profile field change) retries the write.
    try:
        coll.add(
            ids=[chunk_id],
            documents=[text],
            embeddings=[vec],
            metadatas=[{
                "policy_id": chunk_id,
                "insurer_slug": "profile",
                "policy_name": f"User profile ({name_slug[:16]})",
                "doc_type": "profile",
                # Stamp name_slug; the retrieve path filters profile
                # chunks via this field.
                "name_slug": name_slug,
                "source_url": "",
                "page_start": 0,
                "page_end": 0,
                "chunk_idx": 0,
                "local_path": "in-memory named-profile chunk",
            }],
        )
    except Exception as e:
        _log.warning(
            "profile_rag.upsert_profile_chunk: add(id=%s) failed: %s: %s — "
            "user reply will proceed without per-user profile context; "
            "next profile change will retry.",
            chunk_id, type(e).__name__, str(e)[:200],
        )


def remove_profile_chunk(name_slug: str) -> None:
    """Optional cleanup. Keyed by name_slug."""
    if not name_slug:
        return
    try:
        coll = _get_collection()
        coll.delete(where={"policy_id": f"profile_{name_slug}"})
    except Exception:
        pass


def upsert_profile_chunk_sync(name_slug: str, profile_dict: dict) -> None:
    """Sync wrapper for callers that aren't async — schedules + waits."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already inside an async context — schedule on the loop
            asyncio.ensure_future(upsert_profile_chunk(name_slug, profile_dict))
            return
    except RuntimeError:
        pass
    asyncio.run(upsert_profile_chunk(name_slug, profile_dict))
