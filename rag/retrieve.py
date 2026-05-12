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
from backend.providers.voyage_embeddings import VoyageEmbeddings


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


async def retrieve(
    query: str,
    top_k: int = settings.RAG_TOP_K,
    policy_ids: Optional[list[str]] = None,
    insurer_slugs: Optional[list[str]] = None,
    embedder: Optional[VoyageEmbeddings] = None,
) -> list[RetrievedChunk]:
    """Embed the query and return top-k most similar chunks.

    Optional filters narrow retrieval to specific policies/insurers (used
    by comparison and per-policy Q&A flows).
    """
    embedder = embedder or VoyageEmbeddings()
    [query_vec] = await embedder.embed([query], input_type="query")

    where: dict = {}
    if policy_ids:
        where["policy_id"] = {"$in": policy_ids}
    if insurer_slugs:
        where["insurer_slug"] = {"$in": insurer_slugs}

    collection = get_collection()
    res = collection.query(
        query_embeddings=[query_vec],
        n_results=top_k,
        where=where if where else None,
    )

    out: list[RetrievedChunk] = []
    if not res["ids"] or not res["ids"][0]:
        return out

    for cid, doc, meta, dist in zip(
        res["ids"][0],
        res["documents"][0],
        res["metadatas"][0],
        res["distances"][0],
    ):
        # Chroma returns cosine *distance*; convert to similarity score
        score = 1.0 - dist
        out.append(
            RetrievedChunk(
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
        )
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
