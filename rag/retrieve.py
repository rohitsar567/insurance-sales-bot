"""Retrieve relevant chunks from Chroma given a user query.

Returns a list of `RetrievedChunk` dicts with the metadata needed for the
LLM to cite sources (policy_id, page_start, page_end, doc_type, source_url).

Run a quick interactive smoke test from project root:
  python -m rag.retrieve "what is the waiting period for cataract"
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from backend.config import settings
from backend.providers.local_embeddings import LocalEmbeddings as VoyageEmbeddings  # alias kept


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


# KI-034 (2026-05-14) — in-process retrieval cache. Keyed by
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
) -> tuple:
    return (
        (query or "").strip().lower(),
        int(top_k),
        tuple(sorted(policy_ids)) if policy_ids else None,
        tuple(sorted(insurer_slugs)) if insurer_slugs else None,
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
    """
    # KI-034 — short-circuit identical-query re-asks via the LRU cache.
    cache_key = _cache_key(query, top_k, policy_ids, insurer_slugs)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # KI-049 (2026-05-14) — wider net for table-cell questions. The
    # post-fix 96-Q eval surfaced 8 failures on room-rent / sub-limit /
    # cap questions where the bot's reply omitted the room category
    # ("Single Private", "Twin Sharing", "General Ward"). Root cause:
    # the policy table with the cap structure is one chunk in Chroma,
    # but at top_k=5 it can lose to other relevant prose chunks. For
    # queries clearly asking about a structured cap, retrieve more
    # candidates so the table chunk has a higher chance of landing in
    # the LLM's context window.
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

    where: dict = {}
    if policy_ids:
        where["policy_id"] = {"$in": policy_ids}
    if insurer_slugs:
        where["insurer_slug"] = {"$in": insurer_slugs}

    collection = get_collection()

    # Standard retrieval
    res = collection.query(
        query_embeddings=[query_vec],
        n_results=effective_top_k,
        where=where if where else None,
    )

    out: list[RetrievedChunk] = []
    if res["ids"] and res["ids"][0]:
        for cid, doc, meta, dist in zip(
            res["ids"][0], res["documents"][0],
            res["metadatas"][0], res["distances"][0],
        ):
            out.append(_build_chunk(cid, doc, meta, 1.0 - dist))

    # Profile boost pass — when the orchestrator passes a session_id, look
    # up THAT user's profile chunk in Chroma. Inject it at the top of the
    # context (always, not just on high cosine) so the LLM sees the user
    # context block before any policy text. Mirrors the regulatory-boost
    # pattern below.
    if session_id:
        try:
            profile_chunk_id = f"profile_{session_id}"
            prof_res = collection.get(
                ids=[profile_chunk_id],
                include=["documents", "metadatas"],
            )
            if prof_res.get("ids"):
                p_doc = prof_res["documents"][0] if prof_res.get("documents") else ""
                p_meta = prof_res["metadatas"][0] if prof_res.get("metadatas") else {}
                if p_doc:
                    # Profile gets max score (1.0) so it always tops the context
                    profile_chunk = _build_chunk(profile_chunk_id, p_doc, p_meta, 1.0)
                    # Prepend; trim to top_k so we keep budget
                    out = [profile_chunk] + [c for c in out if c.chunk_id != profile_chunk_id]
                    out = out[:top_k]
        except Exception:
            pass

    # Quarantine boost pass — when the orchestrator passes a session_id, also
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
