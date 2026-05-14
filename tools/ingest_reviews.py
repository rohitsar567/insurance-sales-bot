"""Ingest insurer reviews into the main Chroma `policies` collection.

For each insurer review JSON in `data/reviews/`:
  1. Render the structured review into a natural-language paragraph that
     captures the gist of an insurer's reputation: claim settlement %,
     complaint rate, aggregator ratings, sentiment summary, news flags.
  2. Embed via LocalEmbeddings (BGE-small).
  3. Write to the same Chroma `policies` collection with
       insurer_slug = <slug>
       policy_id    = "review_<slug>"
       doc_type     = "review"
       source_url   = first verified review URL we have
  4. Idempotent: re-running replaces the existing chunk for that insurer.

After ingest, `retrieve()` will surface these chunks for queries like
"is HDFC ERGO's claim experience good?" or "what do users say about
Care Health?" — semantic recall over reviews, citable back to source.

Run AFTER the main rag.ingest finishes (avoids Chroma write contention).
  .venv/bin/python tools/ingest_reviews.py
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import chromadb
from chromadb.config import Settings

from backend.config import settings
from backend.providers.local_embeddings import LocalEmbeddings


ROOT = Path(__file__).resolve().parent.parent
REVIEWS_DIR = ROOT / "data" / "reviews"


def review_to_paragraph(d: dict) -> str:
    """Render a structured review JSON into a single English paragraph
    suitable for embedding + retrieval."""
    parts: list[str] = []
    name = d.get("insurer_name") or d.get("insurer_slug")
    parts.append(f"USER REVIEWS AND REPUTATION — {name}.")

    # Hard claim metrics first — most-cited numbers
    cm = d.get("claim_metrics") or {}
    if cm.get("claim_settlement_ratio_pct") is not None:
        parts.append(
            f"Claim Settlement Ratio: {cm['claim_settlement_ratio_pct']}% "
            f"({cm.get('claim_settlement_ratio_year','recent')}, per IRDAI)."
        )
    if cm.get("complaints_per_10k_policies") is not None:
        parts.append(
            f"Complaints per 10,000 policies: {cm['complaints_per_10k_policies']} "
            f"({cm.get('complaints_year','recent')})."
        )
    if cm.get("incurred_claim_ratio_pct") is not None:
        parts.append(f"Incurred Claim Ratio: {cm['incurred_claim_ratio_pct']}%.")

    # Aggregator star ratings (Policybazaar, InsuranceDekho, Ditto, etc.)
    agg = d.get("aggregator_ratings") or {}
    for site, info in agg.items():
        star = (info or {}).get("avg_star")
        if star is not None:
            count = info.get("review_count")
            count_part = f" ({count} reviews)" if count else ""
            parts.append(f"{site.replace('_',' ').title()} rating: {star}/5{count_part}.")

    # Trustpilot
    tp = d.get("trustpilot") or {}
    if tp.get("score") is not None:
        parts.append(f"Trustpilot: {tp['score']}/5 over {tp.get('review_count','few')} reviews.")

    # Reddit / Youtube sentiment summaries (text fields)
    for key, label in [
        ("reddit_sentiment", "Reddit user sentiment"),
        ("youtube_coverage", "YouTube coverage"),
        ("in_news", "Recent news"),
    ]:
        v = d.get(key)
        if isinstance(v, dict):
            summary = v.get("summary") or v.get("note")
            if summary:
                parts.append(f"{label}: {summary}")
        elif isinstance(v, str) and v.strip():
            parts.append(f"{label}: {v.strip()}")

    # Aggregate score the bot has computed
    agg_score = d.get("aggregate_score")
    if isinstance(agg_score, dict):
        s = agg_score.get("score")
        rationale = agg_score.get("rationale") or ""
        if s is not None:
            parts.append(f"Overall trust score (internal): {s}. {rationale}")

    parts.append(
        "Use these reviews when a user asks about claim experience, "
        "service quality, or general reputation. Reviews are dated; "
        f"last updated {d.get('last_updated','recent')}."
    )
    return "\n".join(parts)


def first_verified_url(d: dict) -> str:
    """Pick a single canonical URL to attach as source_url on the chunk.
    Prefer IRDAI claim-stats page, then Policybazaar, then company."""
    cm = d.get("claim_metrics") or {}
    for k in ("source_irdai_url", "source_secondary_url", "source_company_url"):
        if cm.get(k):
            return cm[k]
    agg = d.get("aggregator_ratings") or {}
    for site_info in agg.values():
        if isinstance(site_info, dict) and site_info.get("url"):
            return site_info["url"]
    return ""


async def main():
    files = sorted(REVIEWS_DIR.glob("*.json"))
    if not files:
        print(f"No review JSONs found in {REVIEWS_DIR}")
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

    ok, skipped = 0, 0
    for f in files:
        try:
            d = json.load(open(f))
        except Exception as e:
            print(f"  SKIP {f.name}: {type(e).__name__}: {e}")
            skipped += 1
            continue
        slug = d.get("insurer_slug") or f.stem
        chunk_id = f"review_{slug}"
        text = review_to_paragraph(d)
        if len(text) < 100:
            print(f"  SKIP {slug}: rendered text too short ({len(text)} chars)")
            skipped += 1
            continue
        [vec] = await embedder.embed([text], input_type="document")

        # Replace any prior chunk for this insurer (idempotent)
        try:
            coll.delete(where={"policy_id": chunk_id})
        except Exception:
            pass

        coll.add(
            ids=[chunk_id],
            documents=[text],
            embeddings=[vec],
            metadatas=[{
                "policy_id":    chunk_id,
                "insurer_slug": slug,
                "policy_name":  f"{d.get('insurer_name', slug)} reviews",
                "doc_type":     "review",
                "source_url":   first_verified_url(d),
                "page_start":   0,
                "page_end":     0,
                "chunk_idx":    0,
                "local_path":   str(f),
            }],
        )
        print(f"  OK   {slug:22s}  {len(text):4d} chars  -> {chunk_id}")
        ok += 1

    print()
    print(f"Done. Embedded: {ok}, skipped: {skipped}, total: {len(files)}")


if __name__ == "__main__":
    asyncio.run(main())
