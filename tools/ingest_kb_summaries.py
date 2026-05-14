"""Ingest kb/policies/*.md natural-language summaries into Chroma (KI-019).

Why this exists:
  The current retrieval index has 5,401 chunks from raw PDF wordings —
  dense legalese that's hard to match against natural-language user
  questions like "what does Care Supreme cover?". Meanwhile, every
  policy has a hand-written kb/policies/<policy_id>.md summary
  organized by schema field (Eligibility / Waiting Periods / Coverage /
  etc.) — perfect retrieval targets, but never embedded.

Strategy:
  For each kb/policies/*.md:
    1. Parse the YAML frontmatter for canonical (policy_id, insurer_slug,
       policy_name, uin_code).
    2. Split the body at H2 boundaries (one section per schema "family"
       — Identity, Eligibility, Waiting periods, Sub-limits, etc.).
    3. SKIP sections where every field's Value is "_not specified_"
       (no information content, dead weight in the index).
    4. Embed each surviving section via BGE-small.
    5. Insert into Chroma with `doc_type="summary"`, `kb_section=<H2 title>`
       so retrieval can prefer summary chunks when intent is natural-Q&A.

Output: ~6-10 chunks per policy × 224 policies ≈ 1,500-2,000 summary
chunks added to the same `policies` Chroma collection.

Run AFTER tools/ingest_reviews.py:
  PYTHONPATH=. python tools/ingest_kb_summaries.py
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from backend.config import settings
from backend.providers.local_embeddings import LocalEmbeddings


ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "kb" / "policies"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_after_frontmatter)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 4:].lstrip()
    meta: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def split_h2_sections(body: str) -> list[dict]:
    """Return list of {title, content} for each `## ...` section.
    Drops sections where every H3 field is `_not specified_`."""
    sections = re.split(r"\n##\s+", body)
    out: list[dict] = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if "\n" in sec:
            title, _, content = sec.partition("\n")
        else:
            title, content = sec, ""
        title = title.strip()
        # Skip the very first header-only block (insurer/policy header)
        if title.startswith("#") or "Insurer:" in title or "Policy ID:" in content[:200]:
            continue
        # Skip sections with no real information (all `_not specified_`)
        if re.search(r"\*\*Value:\*\*\s+_?[A-Za-z0-9]", content):
            out.append({"title": title, "content": content.strip()})
    return out


def chunk_text(title: str, content: str, policy_meta: dict) -> str:
    """Build the embedded text for one section."""
    header = (
        f"POLICY SUMMARY — {policy_meta.get('policy_name','?')} "
        f"({policy_meta.get('insurer_name', policy_meta.get('insurer_slug','?'))})\n"
        f"Section: {title}\n"
    )
    return header + content


async def main() -> None:
    files = sorted(KB_DIR.glob("*.md"))
    if not files:
        print(f"No markdown files in {KB_DIR}")
        return

    client = chromadb.PersistentClient(
        path=str(settings.VECTORS_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    coll = client.get_or_create_collection(
        name="policies",
        metadata={"hnsw:space": "cosine"},
    )
    embedder = LocalEmbeddings()

    total_policies = 0
    total_chunks = 0
    skipped = 0

    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  SKIP {f.name}: {type(e).__name__}: {e}")
            skipped += 1
            continue

        meta, body = parse_frontmatter(text)
        policy_id = meta.get("policy_id") or f.stem
        sections = split_h2_sections(body)
        if not sections:
            skipped += 1
            continue

        # Delete any prior summary chunks for this policy (idempotent across re-runs)
        parent_id = f"summary_{policy_id}"
        try:
            coll.delete(where={"policy_id": parent_id})
        except Exception:
            pass

        texts = [chunk_text(s["title"], s["content"], meta) for s in sections]
        try:
            vecs = await embedder.embed(texts, input_type="document")
        except Exception as e:
            print(f"  ERR  {policy_id}: embed failed: {type(e).__name__}: {e}")
            skipped += 1
            continue

        ids = []
        metadatas = []
        for i, s in enumerate(sections):
            section_slug = re.sub(r"[^a-z0-9]+", "_", s["title"].lower()).strip("_")
            ids.append(f"{parent_id}_{section_slug}_{i}")
            metadatas.append({
                "policy_id":    parent_id,
                "insurer_slug": meta.get("insurer_slug", ""),
                "policy_name":  meta.get("policy_name", policy_id),
                "doc_type":     "summary",
                "kb_section":   s["title"],
                "source_url":   "",
                "page_start":   0,
                "page_end":     0,
                "chunk_idx":    i,
                "local_path":   str(f.relative_to(ROOT)),
            })
        coll.add(ids=ids, documents=texts, embeddings=vecs, metadatas=metadatas)
        from rag.ingest import _abort_if_hnsw_bloated
        _abort_if_hnsw_bloated()
        total_policies += 1
        total_chunks += len(sections)
        if total_policies % 25 == 0:
            print(f"  ... {total_policies:>3d} policies done | {total_chunks:>4d} chunks")

    print()
    print(f"Done. Policies indexed: {total_policies}, chunks added: {total_chunks}, skipped: {skipped}, total files: {len(files)}")


if __name__ == "__main__":
    asyncio.run(main())
