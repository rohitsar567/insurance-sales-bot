"""Ingest insurer reviews into the main Chroma `policies` collection.

For each insurer review JSON in `40-data/reviews/`:
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
REVIEWS_DIR = ROOT / "40-data" / "reviews"


def review_to_chunks(d: dict) -> list[dict]:
    """Render a structured review JSON into 4-6 SEMANTICALLY DISTINCT chunks
    so retrieval can match the right slice to the user's intent.

    Returns a list of dicts: {sub_id, label, text}. Each will be embedded
    + indexed as a separate Chroma row, keyed by `<chunk_id>_<sub_id>`.

    Old behaviour was one paragraph per insurer (~500 chars). Result: 10
    reviews total in Chroma. Now each insurer yields 4-6 chunks so the
    `reviews` doc_type slice grows ~5x, with each chunk focused enough to
    match queries like "claim-settlement ratio for HDFC ERGO" cleanly
    against the claim-metrics chunk instead of competing with prose.
    """
    name = d.get("insurer_name") or d.get("insurer_slug")
    chunks: list[dict] = []

    # --- 1. Hard IRDAI claim metrics ---
    cm = d.get("claim_metrics") or {}
    if cm:
        parts = [f"CLAIM METRICS for {name} (IRDAI primary source data)."]
        if cm.get("claim_settlement_ratio_pct") is not None:
            parts.append(
                f"Claim Settlement Ratio: {cm['claim_settlement_ratio_pct']}% "
                f"({cm.get('claim_settlement_ratio_year','recent')})."
            )
        if cm.get("complaints_per_10k_policies") is not None:
            parts.append(
                f"Complaints per 10,000 policies: {cm['complaints_per_10k_policies']} "
                f"({cm.get('complaints_year','recent')}). "
                f"Total complaints in FY24: {cm.get('total_complaints_fy24','n/a')}."
            )
        if cm.get("incurred_claim_ratio_pct") is not None:
            parts.append(f"Incurred Claim Ratio: {cm['incurred_claim_ratio_pct']}%.")
        if cm.get("claims_rejected_fy24") is not None:
            parts.append(f"Claims rejected in FY24: {cm['claims_rejected_fy24']}.")
        if len(parts) > 1:
            chunks.append({"sub_id": "metrics", "label": "claim metrics", "text": "\n".join(parts)})

    # --- 2. Aggregator star ratings (Policybazaar, InsuranceDekho, MouthShut) ---
    agg = d.get("aggregator_ratings") or {}
    rating_parts = [f"AGGREGATOR RATINGS for {name}."]
    for site, info in agg.items():
        if not isinstance(info, dict):
            continue
        star = info.get("avg_star")
        if star is not None:
            count = info.get("review_count")
            count_part = f" from {count} reviews" if count else ""
            note = info.get("note", "")
            note_part = f" — {note}" if note else ""
            rating_parts.append(f"{site.replace('_',' ').title()}: {star}/5{count_part}.{note_part}")
    tp = d.get("trustpilot") or {}
    if tp.get("score") is not None:
        rating_parts.append(f"Trustpilot: {tp['score']}/5 over {tp.get('review_count','few')} reviews.")
    if len(rating_parts) > 1:
        chunks.append({"sub_id": "ratings", "label": "aggregator ratings", "text": "\n".join(rating_parts)})

    # --- 3. Reddit / Quora sentiment + themes ---
    rs = d.get("reddit_sentiment") or {}
    if isinstance(rs, dict) and (rs.get("notable_themes") or rs.get("sentiment_overall")):
        parts = [
            f"REDDIT AND QUORA USER SENTIMENT for {name}.",
            f"Overall sentiment: {rs.get('sentiment_overall','mixed')}.",
            f"Subreddits: {rs.get('subreddit','various')}.",
            f"Approx mentions last year: {rs.get('mentions_last_year_estimate','few')}.",
        ]
        themes = rs.get("notable_themes") or []
        if themes:
            parts.append("Notable themes from real user posts:")
            parts.extend(f"- {t}" for t in themes if isinstance(t, str))
        chunks.append({"sub_id": "reddit", "label": "reddit sentiment", "text": "\n".join(parts)})

    # --- 4. YouTube creator coverage ---
    yt = d.get("youtube_coverage") or {}
    if isinstance(yt, dict) and yt.get("top_creators_who_reviewed"):
        creators = yt["top_creators_who_reviewed"]
        parts = [
            f"YOUTUBE CREATOR REVIEWS of {name}.",
            f"Overall YouTube sentiment: {yt.get('overall_youtube_sentiment','mixed')}.",
            "Reviewed by:",
        ]
        for c in creators:
            if isinstance(c, dict):
                parts.append(f"- {c.get('creator','?')}: \"{c.get('video_title','')}\" — {c.get('video_url','')}")
        chunks.append({"sub_id": "youtube", "label": "youtube reviews", "text": "\n".join(parts)})

    # --- 5. Recent news items (each a one-liner) ---
    in_news = d.get("in_news")
    if isinstance(in_news, list) and in_news:
        parts = [f"RECENT NEWS about {name} (verified press coverage)."]
        for item in in_news[:10]:
            if isinstance(item, dict):
                hl = item.get("headline","")
                url = item.get("url","")
                date = item.get("date","")
                parts.append(f"- {hl} ({date}) — {url}")
        if len(parts) > 1:
            chunks.append({"sub_id": "news", "label": "recent news", "text": "\n".join(parts)})

    # --- 6. Aggregate score + letter grade summary ---
    agg_score = d.get("aggregate_score") or {}
    if isinstance(agg_score, dict) and agg_score.get("value_0_100") is not None:
        parts = [
            f"OVERALL REPUTATION SUMMARY for {name}.",
            f"Internal aggregate score: {agg_score.get('value_0_100')}/100 ({agg_score.get('letter_grade','?')}).",
        ]
        if agg_score.get("headline"):
            parts.append(f"Summary: {agg_score['headline']}")
        if agg_score.get("computation_notes"):
            parts.append(f"How this score was computed: {agg_score['computation_notes']}")
        chunks.append({"sub_id": "overall", "label": "overall trust score", "text": "\n".join(parts)})

    return chunks


# Backwards-compat shim — keep the old name available so any external
# callers don't break. Returns the concatenation of all chunks.
def review_to_paragraph(d: dict) -> str:
    chunks = review_to_chunks(d)
    return "\n\n---\n\n".join(c["text"] for c in chunks)


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

    ok_insurers, ok_chunks, skipped = 0, 0, 0
    for f in files:
        try:
            d = json.load(open(f))
        except Exception as e:
            print(f"  SKIP {f.name}: {type(e).__name__}: {e}")
            skipped += 1
            continue
        slug = d.get("insurer_slug") or f.stem
        parent_id = f"review_{slug}"
        chunks = review_to_chunks(d)
        if not chunks:
            print(f"  SKIP {slug}: no embeddable content")
            skipped += 1
            continue

        # Replace any prior chunks for this insurer (idempotent across re-runs)
        try:
            coll.delete(where={"policy_id": parent_id})
        except Exception:
            pass

        texts = [c["text"] for c in chunks]
        vecs = await embedder.embed(texts, input_type="document")
        url = first_verified_url(d)
        ids = []
        metadatas = []
        for i, c in enumerate(chunks):
            sub = c["sub_id"]
            ids.append(f"{parent_id}_{sub}")
            metadatas.append({
                "policy_id":    parent_id,                       # share parent — easy to delete-by-insurer
                "insurer_slug": slug,
                "policy_name":  f"{d.get('insurer_name', slug)} reviews",
                "doc_type":     "review",
                "review_facet": sub,                              # NEW — claim metrics / ratings / reddit / etc.
                "source_url":   url,
                "page_start":   0,
                "page_end":     0,
                "chunk_idx":    i,
                "local_path":   str(f),
            })
        coll.add(ids=ids, documents=texts, embeddings=vecs, metadatas=metadatas)
        from rag.ingest import _abort_if_hnsw_bloated
        _abort_if_hnsw_bloated()
        print(f"  OK   {slug:18s}  {len(chunks)} chunks  ({sum(len(t) for t in texts):>5d} chars)")
        ok_insurers += 1
        ok_chunks += len(chunks)

    print()
    print(f"Done. Insurers embedded: {ok_insurers}, total chunks: {ok_chunks}, skipped: {skipped}, total files: {len(files)}")


if __name__ == "__main__":
    asyncio.run(main())
