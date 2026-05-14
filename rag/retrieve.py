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
        n_results=top_k,
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
