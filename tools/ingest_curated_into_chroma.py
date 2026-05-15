"""Ingest curated-fact JSONs into the Chroma `policies` collection.

Problem: ~21 policies appear in the marketplace (/api/policies/all) but have
ZERO Chroma chunks, because their canonical PDFs are image-only / not in the
corpus / or the curated JSON is the only data source we have. The chat bot
cannot retrieve or cite these policies — RAG returns no hits.

Fix: render each curated policy_facts JSON into a flat text representation
that mimics what a wordings PDF chunk would look like, embed it with the
SAME LocalEmbeddings (BGE-small, 384-dim) used by rag/ingest.py, and add it
to the SAME `policies` collection with metadata.doc_type='curated' so the
retriever can find these the same way it finds PDF chunks.

Run:
  /Users/rohitsar/.cache/uv-venvs/insurance-sales-bot/bin/python3 \
    tools/ingest_curated_into_chroma.py

Idempotent — re-running skips any policy_id already present in Chroma.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

# Make backend importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.main import _load_curated_facts  # noqa: E402
from backend.providers.local_embeddings import LocalEmbeddings  # noqa: E402
from rag.ingest import (  # noqa: E402
    CHUNK_CHARS,
    OVERLAP_CHARS,
    chunk_pages,
    get_chroma_collection,
)

CURATED_DIR = ROOT / "40-data" / "policy_facts"
CORPUS_URLS_MD = ROOT / "40-data" / "corpus_urls.md"


# ---------- corpus_urls.md → policy_id → source_url ----------

def _load_corpus_urls() -> dict[str, str]:
    """Parse 40-data/corpus_urls.md table rows into {insurer_slug+policy_name_slug: url}.

    Used as a best-effort source_url backfill for curated policies that have
    no PDF in rag/corpus/. The slug match is fuzzy — we slugify the
    policy_name and check if it overlaps with the curated policy_id stem.
    """
    if not CORPUS_URLS_MD.exists():
        return {}
    text = CORPUS_URLS_MD.read_text()
    rows: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("| ") or "---" in line or "insurer_slug" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        insurer_slug, _insurer_name, policy_name, _doc_type, url = cells[:5]
        if not url.startswith("http"):
            continue
        name_slug = re.sub(r"[^a-z0-9]+", "-", policy_name.lower()).strip("-")
        rows.setdefault(f"{insurer_slug}__{name_slug}", url)
        # Also index a shorter token-overlap key for fuzzier matching
        rows.setdefault(name_slug, url)
    return rows


def _best_source_url(policy_id: str, curated_data: dict, url_index: dict[str, str]) -> str:
    """Pick a source URL for a curated policy. Preference order:
    1. Any non-null `source_url` field in the curated JSON
    2. corpus_urls.md row whose insurer_slug__name_slug matches policy_id
    3. Empty string (downstream code accepts "" gracefully)
    """
    # 1. Look inside the curated JSON for any source_url
    for k, v in curated_data.items():
        if isinstance(v, dict) and v.get("source_url"):
            return v["source_url"]
    # 2. corpus_urls.md fuzzy match
    if policy_id in url_index:
        return url_index[policy_id]
    # Try the stem (everything after `insurer__`)
    parts = policy_id.split("__", 1)
    if len(parts) == 2:
        stem = parts[1]
        if stem in url_index:
            return url_index[stem]
        # Loose match: check any url-index key that contains the stem
        for key, url in url_index.items():
            if stem in key or key in stem:
                return url
    return ""


# ---------- curated JSON → flat text representation ----------

_HUMAN_LABEL = {
    "uin_code": "UIN Code",
    "min_entry_age": "Minimum Entry Age",
    "max_entry_age": "Maximum Entry Age",
    "max_renewal_age": "Maximum Renewal Age",
    "sum_insured_options": "Sum Insured Options",
    "initial_waiting_period_days": "Initial Waiting Period (days)",
    "pre_existing_disease_waiting_months": "Pre-Existing Disease Waiting Period (months)",
    "specific_disease_waiting_months": "Specific Disease Waiting Period (months)",
    "maternity_waiting_months": "Maternity Waiting Period (months)",
    "pre_hospitalization_days": "Pre-Hospitalization Coverage (days)",
    "post_hospitalization_days": "Post-Hospitalization Coverage (days)",
    "day_care_treatments_count": "Number of Day-Care Treatments Covered",
    "ayush_coverage": "AYUSH Coverage",
    "maternity_coverage": "Maternity Coverage",
    "newborn_coverage": "Newborn Baby Coverage",
    "organ_donor_expenses": "Organ Donor Expenses",
    "no_claim_bonus_pct": "No Claim Bonus (%)",
    "restoration_benefit": "Restoration / Reload Benefit",
    "room_rent_capping": "Room Rent Capping",
    "copayment_pct": "Co-Payment (%)",
    "deductible_amount": "Deductible Amount",
    "network_hospital_count": "Network Hospital Count",
    "cashless_treatment_supported": "Cashless Treatment Supported",
    "claim_settlement_ratio": "Claim Settlement Ratio (%)",
    "tat_cashless_authorization_hours": "Cashless Authorization Turnaround Time (hours)",
    "policy_type": "Policy Type",
}


def render_curated_to_text(curated: dict) -> str:
    """Render a curated_facts dict into a flat text document the embedder
    can ingest. Includes both value and source_quote so the LLM sees the
    full evidence the curator used.
    """
    policy_name = curated.get("policy_name") or curated.get("policy_id", "")
    insurer_slug = curated.get("insurer_slug", "")
    out: list[str] = []
    out.append(f"Policy Name: {policy_name}")
    out.append(f"Insurer: {insurer_slug}")
    out.append(f"Policy ID: {curated.get('policy_id', '')}")
    out.append("")
    out.append("Structured Policy Facts (curated from official wordings/CIS/brochure):")
    out.append("")
    for key, val in curated.items():
        if key.startswith("_") or key in ("policy_id", "policy_name", "insurer_slug"):
            continue
        label = _HUMAN_LABEL.get(key, key.replace("_", " ").title())
        if isinstance(val, dict):
            value = val.get("value")
            quote = val.get("source_quote") or ""
            unit = val.get("unit", "")
            if value is None or value == "":
                # Skip null fields but keep the source_quote if it gives context
                if quote:
                    out.append(f"- {label}: not specified. Source note: {quote}")
                continue
            display = f"{value} {unit}".strip() if unit else str(value)
            line = f"- {label}: {display}."
            if quote:
                line += f" Source quote: \"{quote}\""
            out.append(line)
        else:
            if val is None or val == "" or val == []:
                continue
            out.append(f"- {label}: {val}")

    # Meta block — curation context + primary source PDF
    meta = curated.get("_meta") or {}
    if meta:
        out.append("")
        out.append("Curation metadata:")
        if meta.get("curated_at"):
            out.append(f"- Curated on: {meta['curated_at']}")
        if meta.get("primary_source_pdf"):
            out.append(f"- Primary source PDF: {meta['primary_source_pdf']}")
        if meta.get("completeness_pct") is not None:
            out.append(f"- Curation completeness: {meta['completeness_pct']}%")
        if meta.get("notes"):
            out.append(f"- Notes: {meta['notes']}")

    return "\n".join(out)


# ---------- main pipeline ----------

async def main():
    # 1. Identify missing curated policies
    curated_all = _load_curated_facts()
    # _load_curated_facts adds __wordings/__brochure/__cis duplicate keys;
    # collapse to the actual policy_id from inside each entry.
    base_curated: dict[str, dict] = {}
    for _key, data in curated_all.items():
        real_pid = data.get("policy_id")
        if real_pid and real_pid not in base_curated:
            base_curated[real_pid] = data
    print(f"Distinct curated policy_ids: {len(base_curated)}")

    coll = get_chroma_collection()
    print(f"Chroma `policies` chunks before ingest: {coll.count()}")

    existing = coll.get(include=["metadatas"])
    chroma_pids = set(md.get("policy_id") for md in existing["metadatas"])
    print(f"Chroma unique policy_ids before ingest: {len(chroma_pids)}")

    # Missing = curated pid AND no chroma pid that exactly matches or matches
    # `<curated_pid>__<docvariant>` (Chroma stores e.g. `...__wordings`).
    missing: list[str] = []
    for pid in sorted(base_curated):
        if pid in chroma_pids:
            continue
        if any(c.startswith(pid + "__") for c in chroma_pids):
            continue
        missing.append(pid)
    print(f"\nMissing curated policies: {len(missing)}")
    for m in missing:
        print(f"  - {m}")

    if not missing:
        print("\nNothing to ingest. Exiting.")
        return

    # 2. Render + embed + add
    url_index = _load_corpus_urls()
    embedder = LocalEmbeddings()
    print(f"\nEmbedder: {embedder.name} ({embedder.model_name}, dim={embedder.dimension}, device={embedder.device})")

    total_chunks = 0
    per_policy: list[tuple[str, int]] = []

    for pid in missing:
        data = base_curated[pid]
        text = render_curated_to_text(data)
        n_chars = len(text)

        # Single chunk if under target, else slide window via rag.ingest.chunk_pages
        # (chunk_pages expects [(page_no, text)]; we feed one virtual page).
        if n_chars <= CHUNK_CHARS:
            chunks = [{
                "chunk_idx": 0,
                "text": text,
                "page_start": 0,
                "page_end": 0,
                "char_start": 0,
                "char_end": n_chars,
            }]
        else:
            chunks = list(chunk_pages(
                pages=[(0, text)],
                target_chars=CHUNK_CHARS,
                overlap_chars=OVERLAP_CHARS,
            ))

        if not chunks:
            print(f"  WARN empty render for {pid}, skipping")
            continue

        texts = [c["text"] for c in chunks]
        vectors = await embedder.embed(texts, input_type="document")

        insurer_slug = data.get("insurer_slug") or pid.split("__", 1)[0]
        policy_name = data.get("policy_name") or pid
        source_url = _best_source_url(pid, data, url_index)

        ids = [f"{pid}::curated::chunk{c['chunk_idx']}" for c in chunks]
        metadatas = [
            {
                "policy_id": pid,
                "insurer_slug": insurer_slug,
                "policy_name": policy_name,
                "doc_type": "curated",
                "source_url": source_url,
                "page_start": 0,
                "page_end": 0,
                "chunk_idx": c["chunk_idx"],
                "local_path": f"curated-facts:{pid}",
            }
            for c in chunks
        ]

        coll.add(ids=ids, documents=texts, embeddings=vectors, metadatas=metadatas)

        per_policy.append((pid, len(chunks)))
        total_chunks += len(chunks)
        print(f"  + {pid}: {len(chunks)} chunk(s), {n_chars} chars, url={'yes' if source_url else 'no'}")

    # 3. Verify by re-querying
    print("\n--- Verification ---")
    final_count = coll.count()
    print(f"Chroma `policies` chunks after ingest: {final_count}")
    for pid, n in per_policy:
        got = coll.get(where={"policy_id": pid})
        print(f"  verify {pid}: chunks_now={len(got.get('ids') or [])}")

    # 4. Sample retrieval test — embed a query and pull top-5
    print("\n--- Sample retrieval test ---")
    sample_query = "What are the waiting periods and AYUSH coverage for Aditya Birla Activ One?"
    qv = await embedder.embed([sample_query], input_type="query")
    res = coll.query(query_embeddings=qv, n_results=5)
    for i, (doc_id, md, dist) in enumerate(zip(res["ids"][0], res["metadatas"][0], res["distances"][0])):
        snippet = (res["documents"][0][i] or "")[:120].replace("\n", " ")
        print(f"  rank {i+1}: {md.get('policy_id')} (dist={dist:.4f}) doc_type={md.get('doc_type')}")
        print(f"           {snippet}...")

    # 5. Summary
    print("\n--- Summary ---")
    print(f"Policies ingested: {len(per_policy)}")
    print(f"Total chunks added: {total_chunks}")


if __name__ == "__main__":
    asyncio.run(main())
