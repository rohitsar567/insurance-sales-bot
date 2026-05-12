"""Ingest pipeline: PDF -> chunks -> embeddings -> Chroma vector store.

For each PDF in rag/corpus/<insurer>/:
  1. Read with pdfplumber, keep per-page text + page numbers
  2. Chunk into 800-token windows with 120-token overlap (page-aware:
     a chunk records the page range it spans)
  3. Embed via Voyage (input_type=document)
  4. Store in Chroma with metadata: policy_id, insurer_slug, doc_type,
     policy_name, page_start, page_end, chunk_idx

Run from project root:
  python -m rag.ingest

Idempotent: a chunk is keyed by (policy_id, chunk_idx); re-running won't dup.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Iterator

import chromadb
import pdfplumber
from chromadb.config import Settings as ChromaSettings

from backend.config import settings
from backend.providers.voyage_embeddings import VoyageEmbeddings

ROOT = settings.CORPUS_DIR.parent.parent  # project root


# ---------- chunking ----------

# Rough token estimate: 1 token ~= 4 chars (English/legal text)
CHARS_PER_TOKEN = 4
CHUNK_CHARS = settings.CHUNK_TOKENS * CHARS_PER_TOKEN          # ~3200
OVERLAP_CHARS = settings.CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN  # ~480


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return re.sub(r"-+", "-", s)


def policy_id_for(pdf_path: Path) -> str:
    """Derive a stable policy_id from path: <insurer-slug>__<filename-stem>"""
    insurer = pdf_path.parent.name
    stem = pdf_path.stem  # e.g. family-health-optima__wordings
    return f"{insurer}__{stem}"


def read_pdf_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, page_text), ...]. Page numbers are 1-indexed."""
    out: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            # Normalize whitespace lightly
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            out.append((i, text))
    return out


def chunk_pages(
    pages: list[tuple[int, str]],
    target_chars: int = CHUNK_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> Iterator[dict]:
    """Yield chunks with page-range metadata.

    Strategy:
      - Concatenate all page texts with markers so we can map char positions
        back to pages
      - Slide a window of target_chars across the joined text with overlap
    """
    # Build the joined text with page boundaries we can recover later
    page_markers: list[tuple[int, int]] = []  # (start_char, page_no)
    joined = []
    pos = 0
    for page_no, text in pages:
        page_markers.append((pos, page_no))
        joined.append(text)
        joined.append("\n\n")
        pos += len(text) + 2
    full_text = "".join(joined)

    def page_at(char_pos: int) -> int:
        # Binary search would be faster; tiny lists so linear is fine
        last = page_markers[0][1]
        for start, p in page_markers:
            if start > char_pos:
                return last
            last = p
        return last

    if not full_text.strip():
        return

    start = 0
    chunk_idx = 0
    n = len(full_text)
    while start < n:
        end = min(start + target_chars, n)

        # Prefer to end on a sentence boundary if one is within ~200 chars of end
        if end < n:
            window = full_text[end - 200 : end + 200]
            local_dot = window.rfind(". ")
            if local_dot != -1:
                # Shift end to that boundary
                end = (end - 200) + local_dot + 2

        text = full_text[start:end].strip()
        if text:
            yield {
                "chunk_idx": chunk_idx,
                "text": text,
                "page_start": page_at(start),
                "page_end": page_at(min(end - 1, n - 1)),
                "char_start": start,
                "char_end": end,
            }
            chunk_idx += 1

        if end >= n:
            break
        start = max(end - overlap_chars, start + 1)


# ---------- Chroma persistence ----------

def get_chroma_collection():
    client = chromadb.PersistentClient(
        path=str(settings.VECTORS_DIR),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name="policies",
        metadata={"hnsw:space": "cosine"},
    )


# ---------- pipeline ----------

def discover_pdfs() -> list[Path]:
    """All PDFs under rag/corpus/*/*.pdf, in deterministic order."""
    pdfs: list[Path] = []
    for insurer_dir in sorted(settings.CORPUS_DIR.iterdir()):
        if not insurer_dir.is_dir():
            continue
        for pdf in sorted(insurer_dir.glob("*.pdf")):
            pdfs.append(pdf)
    return pdfs


def load_manifest() -> dict:
    """Map URL -> insurer_name + policy_name + doc_type from _manifest.json."""
    mf = settings.CORPUS_DIR / "_manifest.json"
    if not mf.exists():
        return {}
    data = json.loads(mf.read_text())
    out = {}
    for r in data.get("results", []):
        if not r.get("ok"):
            continue
        # local_path is relative to project root
        out[r["local_path"]] = r
    return out


async def ingest_one(
    pdf_path: Path,
    manifest_entry: dict,
    embedder: VoyageEmbeddings,
    collection,
):
    policy_id = policy_id_for(pdf_path)
    insurer_slug = pdf_path.parent.name
    policy_name = manifest_entry.get("policy_name", pdf_path.stem)
    doc_type = manifest_entry.get("doc_type", "unknown")
    source_url = manifest_entry.get("url", "")

    # Skip if already ingested
    existing = collection.get(where={"policy_id": policy_id}, limit=1)
    if existing and existing.get("ids"):
        print(f"  SKIP (already ingested): {policy_id}")
        return 0

    try:
        pages = read_pdf_pages(pdf_path)
    except Exception as e:
        print(f"  FAIL pdfplumber: {policy_id} | {type(e).__name__}: {e}")
        return 0

    chunks = list(chunk_pages(pages))
    if not chunks:
        print(f"  EMPTY: {policy_id} (no text extracted)")
        return 0

    texts = [c["text"] for c in chunks]
    try:
        vectors = await embedder.embed(texts, input_type="document")
    except Exception as e:
        print(f"  FAIL embed: {policy_id} | {type(e).__name__}: {e}")
        return 0

    ids = [f"{policy_id}::chunk{c['chunk_idx']}" for c in chunks]
    metadatas = [
        {
            "policy_id": policy_id,
            "insurer_slug": insurer_slug,
            "policy_name": policy_name,
            "doc_type": doc_type,
            "source_url": source_url,
            "page_start": c["page_start"],
            "page_end": c["page_end"],
            "chunk_idx": c["chunk_idx"],
            "local_path": str(pdf_path.relative_to(ROOT)),
        }
        for c in chunks
    ]

    collection.add(
        ids=ids,
        documents=texts,
        embeddings=vectors,
        metadatas=metadatas,
    )
    return len(chunks)


async def main():
    settings.VECTORS_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = discover_pdfs()
    manifest = load_manifest()
    collection = get_chroma_collection()
    embedder = VoyageEmbeddings()

    print(f"Ingesting {len(pdfs)} PDFs into Chroma at {settings.VECTORS_DIR}\n")

    total_chunks = 0
    t0 = time.time()
    for i, pdf in enumerate(pdfs, 1):
        rel = str(pdf.relative_to(ROOT))
        entry = manifest.get(rel, {})
        print(f"[{i}/{len(pdfs)}] {pdf.parent.name} | {pdf.stem[:50]}")
        n = await ingest_one(pdf, entry, embedder, collection)
        total_chunks += n
        if n:
            print(f"  -> {n} chunks")

    elapsed = time.time() - t0
    final_count = collection.count()
    print(f"\nDone in {elapsed:.1f}s. {total_chunks} new chunks added. Collection now has {final_count} chunks total.")


if __name__ == "__main__":
    asyncio.run(main())
