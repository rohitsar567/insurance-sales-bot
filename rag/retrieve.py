"""Retrieve relevant chunks from Chroma given a user query.

Returns a list of `RetrievedChunk` dicts with the metadata needed for the
LLM to cite sources (policy_id, page_start, page_end, doc_type, source_url).

Run a quick interactive smoke test from project root:
  python -m rag.retrieve "what is the waiting period for cataract"
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from backend.config import settings
from backend.providers.local_embeddings import LocalEmbeddings as VoyageEmbeddings  # alias kept

_log = logging.getLogger(__name__)


def _safe_collection_get(
    collection: Any,
    *,
    ids: Optional[list[str]] = None,
    where: Optional[dict] = None,
    include: Optional[list[str]] = None,
) -> Optional[dict]:
    """Defensive wrapper around `collection.get(...)`.

    Chroma's `collection.get(ids=[...])` is documented to return empty
    lists when ids miss, but the Rust-backed client can still raise on
    certain edge cases (legacy schema rows, where-clause + missing-id
    combinations, transient sqlite lock contention during HNSW
    compaction).

    This helper:
      - Returns None on ANY exception (caller MUST treat None as "miss").
      - Returns the raw dict (may have empty lists) on success.
      - Logs at WARNING level so the failure is observable instead of
        swallowed silently.
    """
    try:
        kwargs: dict = {}
        if ids is not None:
            kwargs["ids"] = ids
        if where is not None:
            kwargs["where"] = where
        if include is not None:
            kwargs["include"] = include
        return collection.get(**kwargs)
    except Exception as e:  # broad: Chroma raises bare RuntimeError, ValueError, sqlite errors
        _log.warning(
            "Chroma get(ids=%r, where=%r) failed: %s: %s",
            ids, where, type(e).__name__, str(e)[:200],
        )
        return None


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    policy_id: str
    insurer_slug: str
    policy_name: str
    doc_type: str
    source_url: str
    page_start: int
    page_end: int
    chunk_idx: int
    score: float


def get_collection():
    client = chromadb.PersistentClient(
        path=str(settings.VECTORS_DIR),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name="policies",
        metadata={"hnsw:space": "cosine"},
    )


import re as _re

# Queries containing these keywords trigger a parallel regulatory-only
# retrieval whose top results are boosted ×1.2 and merged into the final
# context. This ensures the brain sees IRDAI mandates whenever the user
# asks about compliance, legality, or what's "allowed" — even when the
# policy chunks would otherwise dominate raw cosine.
_REGULATORY_TRIGGERS = _re.compile(
    r"\b(irdai|irda|regulation|regulator|regulatory|mandate|mandatory|"
    r"allowed|prohibited|legal|illegal|unenforceable|cap|capped|ceiling|"
    r"master circular|section\s+\d+|compliance|non[- ]?compliant|"
    r"required|must|rule|statute|act|government)\b",
    flags=_re.IGNORECASE,
)
REGULATORY_BOOST = 1.2  # multiplier applied to regulatory chunk scores


def _is_regulatory_intent(query: str) -> bool:
    return bool(_REGULATORY_TRIGGERS.search(query or ""))


# Review-intent triggers: when a query is about insurer reputation /
# claim experience / customer satisfaction, surface the review chunks
# (ingested via tools/ingest_reviews.py, doc_type='review').
_REVIEW_TRIGGERS = _re.compile(
    r"\b(review|reviews|rating|ratings|reputation|complaint|complaints|"
    r"trustpilot|policybazaar|insurancedekho|joinditto|claim experience|"
    r"claim settlement|claim ratio|complaint ratio|customer service|"
    r"good service|bad service|user experience|reddit|youtube|"
    r"feedback|testimonial|sentiment|trust|reliable|reliability)\b",
    flags=_re.IGNORECASE,
)
REVIEW_BOOST = 1.15  # slight boost so reviews appear in reputation Qs


def _is_review_intent(query: str) -> bool:
    return bool(_REVIEW_TRIGGERS.search(query or ""))


def _build_chunk(cid: str, doc: str, meta: dict, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        text=doc,
        policy_id=meta.get("policy_id", ""),
        insurer_slug=meta.get("insurer_slug", ""),
        policy_name=meta.get("policy_name", ""),
        doc_type=meta.get("doc_type", ""),
        source_url=meta.get("source_url", ""),
        page_start=int(meta.get("page_start", 0)),
        page_end=int(meta.get("page_end", 0)),
        chunk_idx=int(meta.get("chunk_idx", 0)),
        score=score,
    )


# In-process retrieval cache. Keyed by
# (query_text, top_k, sorted policy_ids, sorted insurer_slugs). Caps at 256
# entries with FIFO eviction so memory stays bounded across long sessions.
# Within a single chat session, users frequently rephrase or follow up on the
# same topic ("what's the waiting period?" → "and the pre-existing diseases
# waiting period?" → "and the diabetes-specific one?"). When the cache key
# matches, we skip the Voyage embed call AND the Chroma collection.query() —
# saving the round-trip + the Voyage 3 RPM tax. Cache invalidates implicitly
# at process restart (HF Space deploy / reload).
from collections import OrderedDict as _OrderedDict
_RETRIEVAL_CACHE: "_OrderedDict[tuple, list]" = _OrderedDict()
_RETRIEVAL_CACHE_MAX = 256


def _cache_key(
    query: str,
    top_k: int,
    policy_ids: Optional[list[str]],
    insurer_slugs: Optional[list[str]],
    session_id: Optional[str] = None,
) -> tuple:
    # session_id MUST be part of the cache key (quarantine isolation).
    # The result set is session-dependent: when a session_id is
    # supplied the quarantine boost pass prepends that session's uploaded
    # PDF chunks. Without session_id in the key, a result computed for
    # session A (carrying A's private uploaded chunks) would be served
    # verbatim to session B for the same query string — a cross-session
    # data leak — and a session query could be served a stale non-session
    # result that silently drops the quarantine boost. Keying on session_id
    # keeps each session's cache slice strictly its own.
    return (
        (query or "").strip().lower(),
        int(top_k),
        tuple(sorted(policy_ids)) if policy_ids else None,
        tuple(sorted(insurer_slugs)) if insurer_slugs else None,
        (session_id or None),
    )


def _cache_get(key: tuple) -> Optional[list]:
    if key not in _RETRIEVAL_CACHE:
        return None
    # LRU bump
    val = _RETRIEVAL_CACHE.pop(key)
    _RETRIEVAL_CACHE[key] = val
    return val


def _cache_set(key: tuple, value: list) -> None:
    _RETRIEVAL_CACHE[key] = value
    while len(_RETRIEVAL_CACHE) > _RETRIEVAL_CACHE_MAX:
        _RETRIEVAL_CACHE.popitem(last=False)  # evict oldest


async def retrieve(
    query: str,
    top_k: int = settings.RAG_TOP_K,
    policy_ids: Optional[list[str]] = None,
    insurer_slugs: Optional[list[str]] = None,
    embedder: Optional[VoyageEmbeddings] = None,
    profile_name_slug: Optional[str] = None,
    session_id: Optional[str] = None,
) -> list[RetrievedChunk]:
    """Embed the query and return top-k most similar chunks.

    Optional filters narrow retrieval to specific policies/insurers (used
    by comparison and per-policy Q&A flows).

    For queries with regulatory intent (IRDAI / mandate / allowed / etc.),
    runs a SECOND retrieval restricted to doc_type='regulatory' chunks and
    merges the top 3 of those (score-boosted ×1.2) into the result set.
    This ensures the brain sees regulatory ceilings even when policy
    chunks dominate raw cosine.

    Profile chunks are keyed by `profile_name_slug` (canonical user name);
    only named users have a profile chunk to boost. `session_id` is used
    for the per-session quarantine (user-uploaded PDF) lookup, not for
    profile boosting.
    """
    # Short-circuit identical-query re-asks via the LRU cache.
    # session_id is part of the key so one session's cached result (which
    # may carry that session's private quarantine chunks) is never served
    # to another session.
    cache_key = _cache_key(query, top_k, policy_ids, insurer_slugs, session_id)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Wider net for table-cell questions. The policy table holding the
    # cap structure (room-rent / sub-limit / room category) is one chunk
    # in Chroma and at top_k=5 it can lose to other relevant prose
    # chunks. For queries clearly asking about a structured cap, retrieve
    # more candidates so the table chunk has a higher chance of landing
    # in the LLM's context window.
    _TABLE_CELL_TRIGGERS = _re.compile(
        r"\b(room\s*rent|sub[\s\-]?limit|sub[\s\-]?limits|copay|co[\s\-]?pay|"
        r"cap\s+on|capped\s+at|sum\s+insured\s+limit|"
        r"single\s+private|twin\s+sharing|general\s+ward|"
        r"no[\s\-]?claim\s+bonus|ncb)\b",
        flags=_re.IGNORECASE,
    )
    effective_top_k = top_k
    if _TABLE_CELL_TRIGGERS.search(query or ""):
        effective_top_k = max(top_k, 10)

    embedder = embedder or VoyageEmbeddings()
    [query_vec] = await embedder.embed([query], input_type="query")

    # Privacy invariant: the main retrieval pass MUST NEVER return chunks
    # of doc_type='profile' because those chunks are scoped to a specific
    # user. Profile chunks are exclusively surfaced via the explicit
    # per-session collection.get(ids=[f"profile_{session_id}"]) lookup
    # below; allowing them through the cosine pass would leak another
    # user's profile into the current session.
    #
    # We translate this to Chroma's `where` DSL using $ne (not-equals) on
    # doc_type. Combined with optional policy_id / insurer_slug filters via
    # $and so the existing comparison + per-policy Q&A flows still work.
    _filter_clauses: list[dict] = [{"doc_type": {"$ne": "profile"}}]
    if policy_ids:
        # #61 — chunks are stored under the EXTRACTION stem (often
        # doctype-suffixed, e.g. `niva-bupa__health-companion-v2022__brochure`)
        # but the recommendation cites the marketplace card's canonical id
        # (e.g. `niva-bupa__health-companion-v2022`). An exact `$in` on
        # policy_id then matched ZERO chunks → the bot apologised it "was
        # unable to retrieve specific details" about a policy it had just
        # recommended. Expand each requested id to its canonical family:
        # the id itself, its doctype-stripped product_key, and every
        # doctype-suffixed sibling — a strict superset, so this can only add
        # correct matches, never drop or cross-contaminate a policy.
        try:
            from backend.policy_identity import product_key as _pk
        except Exception:  # noqa: BLE001 — never break retrieval on import
            _pk = None
        _expanded: set[str] = set()
        for _pid in policy_ids:
            if not _pid:
                continue
            _expanded.add(_pid)
            _base = _pk(_pid) if _pk else _pid
            if _base:
                _expanded.add(_base)
                for _suf in ("__wordings", "__brochure", "__cis", "__prospectus"):
                    _expanded.add(_base + _suf)
        _filter_clauses.append(
            {"policy_id": {"$in": sorted(_expanded) if _expanded else policy_ids}}
        )
    if insurer_slugs:
        _filter_clauses.append({"insurer_slug": {"$in": insurer_slugs}})
    if len(_filter_clauses) == 1:
        where: dict = _filter_clauses[0]
    else:
        where = {"$and": _filter_clauses}

    collection = get_collection()

    # Standard retrieval — collection.query() is wrapped with the same
    # defensive pattern as _safe_collection_get because .query() can raise
    # `chromadb.errors.InternalError: Error executing plan` when an HNSW
    # index entry points to a missing doc. On error we degrade to empty
    # retrieval: the brain still answers (acknowledging it can't access
    # the catalog) instead of the user seeing a generic "something went
    # wrong" fallback for every message.
    out: list[RetrievedChunk] = []
    try:
        res = collection.query(
            query_embeddings=[query_vec],
            n_results=effective_top_k,
            where=where,
        )
        if res["ids"] and res["ids"][0]:
            for cid, doc, meta, dist in zip(
                res["ids"][0], res["documents"][0],
                res["metadatas"][0], res["distances"][0],
            ):
                out.append(_build_chunk(cid, doc, meta, 1.0 - dist))
    except Exception as e:
        import logging
        logging.warning(
            "Chroma collection.query() failed (top_k=%d, where=%s); "
            "degrading to empty retrieval. %s: %s",
            effective_top_k, where, type(e).__name__, str(e)[:300],
        )

    # Profile boost pass. Profile chunks are keyed by `profile_name_slug`
    # (canonical user name), not session_id. The caller passes the slug
    # only when the live session has a known name; anonymous chats skip
    # this branch entirely.
    if profile_name_slug:
        profile_chunk_id = f"profile_{profile_name_slug}"
        # Filter by BOTH id AND name_slug metadata so any ID collision
        # (or migration-era chunk written under a shared id) can't leak
        # the wrong profile into this user's context.
        #
        # Route through _safe_collection_get because first-time named
        # users (no profile saved yet) and certain transient Chroma
        # sqlite states cause collection.get(ids=[missing_id]) to raise
        # instead of returning empty lists.
        prof_res = _safe_collection_get(
            collection,
            ids=[profile_chunk_id],
            where={"name_slug": profile_name_slug},
            include=["documents", "metadatas"],
        )
        # prof_res is None on exception, a dict (possibly with empty lists) on success.
        if prof_res and prof_res.get("ids"):
            documents = prof_res.get("documents") or []
            metadatas = prof_res.get("metadatas") or []
            p_doc = documents[0] if documents else ""
            p_meta = metadatas[0] if metadatas else {}
            # Triple-check: refuse the row unless its metadata.name_slug
            # matches the caller's slug. Belt + suspenders + parachute.
            if p_doc and p_meta.get("name_slug") == profile_name_slug:
                # Profile gets max score (1.0) so it always tops the context
                profile_chunk = _build_chunk(profile_chunk_id, p_doc, p_meta, 1.0)
                # Prepend; trim to top_k so we keep budget
                out = [profile_chunk] + [c for c in out if c.chunk_id != profile_chunk_id]
                out = out[:top_k]

    # Quarantine boost pass — when the caller passes a session_id, also
    # query the SEPARATE quarantine collection (user-uploaded PDFs) scoped to
    # this session. Results get a ×1.1 score boost so the user's own uploaded
    # doc ranks above generic policies when relevant. Quarantine lives in a
    # different on-disk Chroma collection ("user_uploads_quarantine") so it
    # is physically isolated from the main `policies` collection — no exclude
    # clause is needed in the main pass.
    if session_id:
        try:
            from rag.ingest import get_quarantine_collection
            q_coll = get_quarantine_collection()
            q_res = q_coll.query(
                query_embeddings=[query_vec],
                n_results=3,
                where={"session_id": session_id},
            )
            if q_res["ids"] and q_res["ids"][0]:
                seen = {c.chunk_id for c in out}
                quarantine_chunks: list[RetrievedChunk] = []
                for cid, doc, meta, dist in zip(
                    q_res["ids"][0], q_res["documents"][0],
                    q_res["metadatas"][0], q_res["distances"][0],
                ):
                    if cid in seen:
                        continue
                    boosted = (1.0 - dist) * 1.1
                    quarantine_chunks.append(_build_chunk(cid, doc, meta, boosted))
                # Prepend so user's own upload appears first, then trim to top_k
                out = quarantine_chunks + out
                out = out[:top_k]
        except Exception:
            # Quarantine boost is additive; failure must never break main retrieval
            pass

    # Regulatory boost pass — only when the query is about IRDAI / regulations,
    # and only when not already filtered to specific policies (otherwise the
    # caller is asking about a specific policy, not regulations).
    if _is_regulatory_intent(query) and not policy_ids and not insurer_slugs:
        try:
            reg_res = collection.query(
                query_embeddings=[query_vec],
                n_results=3,
                where={"doc_type": "regulatory"},
            )
            if reg_res["ids"] and reg_res["ids"][0]:
                seen = {c.chunk_id for c in out}
                reg_chunks: list[RetrievedChunk] = []
                for cid, doc, meta, dist in zip(
                    reg_res["ids"][0], reg_res["documents"][0],
                    reg_res["metadatas"][0], reg_res["distances"][0],
                ):
                    if cid in seen:
                        continue
                    boosted = (1.0 - dist) * REGULATORY_BOOST
                    reg_chunks.append(_build_chunk(cid, doc, meta, boosted))
                # Merge and re-sort by score, then trim back to top_k
                merged = sorted(out + reg_chunks, key=lambda c: c.score, reverse=True)
                out = merged[:top_k]
        except Exception:
            # Regulatory boost is additive; failure shouldn't kill the main result
            pass

    # Review boost pass — when query asks about insurer reputation / claim
    # experience / service quality. Filters to the relevant insurer if
    # insurer_slugs is set; otherwise queries across all reviews.
    if _is_review_intent(query):
        try:
            where_review: dict = {"doc_type": "review"}
            if insurer_slugs:
                # Combine doc_type + insurer filter
                where_review = {
                    "$and": [
                        {"doc_type": "review"},
                        {"insurer_slug": {"$in": insurer_slugs}},
                    ]
                }
            rev_res = collection.query(
                query_embeddings=[query_vec],
                n_results=3,
                where=where_review,
            )
            if rev_res["ids"] and rev_res["ids"][0]:
                seen = {c.chunk_id for c in out}
                rev_chunks: list[RetrievedChunk] = []
                for cid, doc, meta, dist in zip(
                    rev_res["ids"][0], rev_res["documents"][0],
                    rev_res["metadatas"][0], rev_res["distances"][0],
                ):
                    if cid in seen:
                        continue
                    boosted = (1.0 - dist) * REVIEW_BOOST
                    rev_chunks.append(_build_chunk(cid, doc, meta, boosted))
                merged = sorted(out + rev_chunks, key=lambda c: c.score, reverse=True)
                out = merged[:top_k]
        except Exception:
            # Review boost is additive; failure shouldn't kill the main result
            pass

    # #52 — uploaded-doc global merge pass. A user-uploaded policy that was
    # added to THE marketplace (doc_type='user_upload', NO session_id — it
    # is globally visible by design) is usually a 1–few-chunk document, so
    # it loses the raw-cosine race against the 140+ multi-chunk corpus
    # policies and never enters the top-k even when it's the best answer to
    # a question literally about that document. Mirror the regulatory /
    # review boost passes: run a SECOND query restricted to
    # doc_type='user_upload', score-boost the hits, and merge. Skipped when
    # the caller already scoped to specific policies/insurers (then they're
    # asking about a known corpus policy, not browsing uploaded docs). The
    # session-scoped quarantine pass above is unaffected — that path serves
    # the uploader's OWN still-private upload; this serves docs already
    # promoted to the public marketplace.
    if not policy_ids and not insurer_slugs:
        try:
            up_res = collection.query(
                query_embeddings=[query_vec],
                n_results=3,
                where={"doc_type": "user_upload"},
            )
            if up_res["ids"] and up_res["ids"][0]:
                seen = {c.chunk_id for c in out}
                up_chunks: list[RetrievedChunk] = []
                for cid, doc, meta, dist in zip(
                    up_res["ids"][0], up_res["documents"][0],
                    up_res["metadatas"][0], up_res["distances"][0],
                ):
                    if cid in seen:
                        continue
                    boosted = (1.0 - dist) * 1.1
                    up_chunks.append(_build_chunk(cid, doc, meta, boosted))
                merged = sorted(out + up_chunks, key=lambda c: c.score, reverse=True)
                out = merged[:top_k]
        except Exception:
            # Uploaded-doc boost is additive; failure must not break retrieval
            pass

    # KI-034 — populate cache with the FINAL merged result so subsequent
    # identical queries skip Voyage embed + Chroma query + boost passes.
    _cache_set(cache_key, out)
    return out


def format_for_llm_context(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks for inclusion in an LLM prompt.

    Each chunk is tagged with an inline source so the LLM can cite it back.
    """
    blocks = []
    for ch in chunks:
        page_ref = f"p.{ch.page_start}" if ch.page_start == ch.page_end else f"pp.{ch.page_start}-{ch.page_end}"
        header = f"[Source: {ch.policy_name} ({ch.insurer_slug}), {page_ref}]"
        blocks.append(f"{header}\n{ch.text.strip()}")
    return "\n\n---\n\n".join(blocks)


async def _smoke(query: str):
    print(f"Query: {query!r}\n")
    chunks = await retrieve(query, top_k=5)
    if not chunks:
        print("(no chunks — has rag/ingest been run?)")
        return
    for c in chunks:
        page_ref = f"p.{c.page_start}" if c.page_start == c.page_end else f"pp.{c.page_start}-{c.page_end}"
        print(f"  [{c.score:.3f}] {c.policy_name} ({c.insurer_slug}) {page_ref}")
        snippet = c.text[:160].replace("\n", " ")
        print(f"     {snippet}...")
        print()


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "what is the waiting period for cataract surgery"
    asyncio.run(_smoke(q))
