"""Generate the Information Source Map — a detailed catalog of what knowledge
the corpus contains, used for both reviewer-facing explainability and
faithfulness verification at runtime.

Outputs two artifacts after ingestion + extraction have run:

  1. docs/information_source_map.md
     Human-readable per-policy catalog: insurer, policy, doc type, chunk count,
     pages covered, extracted-field summary, source URL. The "what does the bot
     know" reference.

  2. rag/source_map.json
     Machine-readable per-chunk index: {chunk_id, policy_id, page_range,
     extracted_terms, primary_topics}. Used by faithfulness verifier to look up
     whether a claim could plausibly trace to a chunk.

Run:
  python -m rag.source_map
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

import chromadb
import duckdb
from chromadb.config import Settings as ChromaSettings

from backend.config import settings

ROOT = settings.CORPUS_DIR.parent.parent
MD_OUTPUT = ROOT / "docs" / "information_source_map.md"
JSON_OUTPUT = settings.VECTORS_DIR.parent / "source_map.json"


# Topic keywords used to tag chunks with the high-level concepts they cover.
# Used for the JSON catalog + chunk-routing in retrieval.
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "waiting_period": ["waiting period", "pre-existing", "PED", "specific waiting", "initial waiting"],
    "coverage_scope": ["covered", "covers", "inpatient", "outpatient", "OPD", "domiciliary"],
    "exclusions": ["exclusion", "excluded", "not covered", "shall not pay", "permanent exclusion"],
    "claim_process": ["claim", "settlement", "TAT", "turnaround time", "reimbursement", "cashless"],
    "sum_insured": ["sum insured", "sum assured", "policy limit", "annual limit"],
    "room_rent": ["room rent", "ICU", "private room", "single room"],
    "copayment": ["co-payment", "copay", "deductible", "patient share"],
    "maternity": ["maternity", "pregnancy", "delivery", "newborn"],
    "ayush": ["AYUSH", "Ayurveda", "Yoga", "Unani", "Siddha", "Homeopathy"],
    "critical_illness": ["critical illness", "cancer", "stroke", "heart attack", "kidney failure"],
    "network": ["network hospital", "cashless", "network of hospitals", "empanelled"],
    "ncb": ["no claim bonus", "NCB", "cumulative bonus", "renewal bonus"],
    "restoration": ["restoration", "refill", "recharge"],
    "geography": ["pan-india", "worldwide", "overseas", "geographic"],
    "tax_section_80d": ["80D", "tax benefit", "tax deduction", "income tax"],
    "renewal": ["renewal", "renewability", "lifelong", "guaranteed renewal"],
}


def chroma_collection():
    client = chromadb.PersistentClient(
        path=str(settings.VECTORS_DIR),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name="policies",
        metadata={"hnsw:space": "cosine"},
    )


def load_extracted_policies() -> dict[str, dict]:
    """Map policy_id -> extracted JSON from DuckDB."""
    out: dict[str, dict] = {}
    db = settings.STRUCTURED_DB
    if not db.exists():
        return out
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute("SELECT policy_id, data_json FROM policies").fetchall()
        for pid, data in rows:
            try:
                out[pid] = json.loads(data)
            except Exception:
                pass
    finally:
        con.close()
    return out


def tag_topics(text: str) -> list[str]:
    """Return the topics this chunk text covers."""
    t = text.lower()
    return [topic for topic, kws in TOPIC_KEYWORDS.items() if any(kw.lower() in t for kw in kws)]


def summarize_fields(p: dict) -> dict:
    """Pick high-leverage fields for the per-policy summary in the markdown."""
    def get(k, default="—"):
        v = p.get(k, default)
        if v is None or v == "" or v == []:
            return default
        return v
    return {
        "policy_name": get("policy_name"),
        "insurer_name": get("insurer_name"),
        "policy_type": get("policy_type"),
        "min_entry_age": get("min_entry_age"),
        "max_entry_age": get("max_entry_age"),
        "sum_insured_options": get("sum_insured_options"),
        "pre_existing_disease_waiting_months": get("pre_existing_disease_waiting_months"),
        "maternity_waiting_months": get("maternity_waiting_months"),
        "ayush_coverage": get("ayush_coverage"),
        "room_rent_capping": get("room_rent_capping"),
        "copayment_pct": get("copayment_pct"),
        "no_claim_bonus_pct": get("no_claim_bonus_pct"),
        "network_hospital_count": get("network_hospital_count"),
        "extraction_confidence_pct": get("extraction_confidence_pct"),
    }


def build_machine_index() -> dict:
    """Per-chunk index used by faithfulness verifier."""
    coll = chroma_collection()
    total = coll.count()
    if total == 0:
        return {"total_chunks": 0, "chunks": []}

    PAGE = 500
    chunks_out: list[dict] = []
    for offset in range(0, total, PAGE):
        res = coll.get(limit=PAGE, offset=offset, include=["documents", "metadatas"])
        for cid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"]):
            chunks_out.append({
                "chunk_id": cid,
                "policy_id": meta.get("policy_id", ""),
                "insurer_slug": meta.get("insurer_slug", ""),
                "policy_name": meta.get("policy_name", ""),
                "doc_type": meta.get("doc_type", ""),
                "page_start": meta.get("page_start"),
                "page_end": meta.get("page_end"),
                "topics": tag_topics(doc),
            })
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_chunks": total,
        "chunks": chunks_out,
    }


def build_markdown(machine: dict, extracted: dict[str, dict]) -> str:
    """Human-readable per-policy + per-topic catalog."""
    # Group chunks by policy
    by_policy: dict[str, list[dict]] = defaultdict(list)
    for c in machine.get("chunks", []):
        by_policy[c["policy_id"]].append(c)

    # Per-policy summary
    policies_md = []
    for pid in sorted(by_policy.keys()):
        chunks = by_policy[pid]
        meta = chunks[0]
        pages = sorted(set(c["page_start"] for c in chunks if c["page_start"]))
        topic_counts: dict[str, int] = defaultdict(int)
        for c in chunks:
            for t in c.get("topics", []):
                topic_counts[t] += 1
        topic_summary = ", ".join(f"{t}({n})" for t, n in sorted(topic_counts.items(), key=lambda kv: -kv[1])[:8])

        # Extracted-field summary
        ext = extracted.get(pid, {})
        f = summarize_fields(ext) if ext else {}
        field_lines = []
        for k, v in f.items():
            if v not in ("—", None, ""):
                field_lines.append(f"  - **{k}**: {v}")
        field_block = "\n".join(field_lines) if field_lines else "  - (extraction not yet run for this policy)"

        policies_md.append(
            f"### {meta['policy_name']}  \n"
            f"_{meta['insurer_slug']} · {meta['doc_type']} · {len(chunks)} chunks · pages {min(pages) if pages else '?'}-{max(pages) if pages else '?'}_\n\n"
            f"**Topics covered:** {topic_summary or '(none auto-tagged)'}\n\n"
            f"**Extracted fields:**\n{field_block}\n\n"
            f"`policy_id`: `{pid}`\n"
        )

    # Per-topic inverted index
    topic_to_policies: dict[str, set[str]] = defaultdict(set)
    for c in machine.get("chunks", []):
        for t in c.get("topics", []):
            topic_to_policies[t].add(c["policy_id"])
    topic_md = []
    for topic in sorted(topic_to_policies.keys()):
        pols = sorted(topic_to_policies[topic])
        topic_md.append(f"- **{topic}** — covered in {len(pols)} policies: {', '.join(pols[:8])}{', …' if len(pols) > 8 else ''}")

    md = f"""# Information Source Map

| Field | Value |
| --- | --- |
| Generated | {machine.get('generated_at', 'never')} |
| Total chunks in vector store | {machine.get('total_chunks', 0)} |
| Policies indexed | {len(by_policy)} |
| Topics auto-tagged | {len(TOPIC_KEYWORDS)} |

## 0. Purpose

This document is the **authoritative catalog of what the bot can answer**. Every chunk in the Chroma vector store is summarized here, grouped by policy. For each policy, the high-value extracted fields are listed alongside.

A reviewer can use this file to answer two questions:

1. **"Could the bot know this?"** → look up the policy + topic.
2. **"Is the bot's answer plausibly grounded?"** → cross-reference the policy_id and field in the runtime audit log.

This artifact is regenerated after every ingestion or extraction run via `python -m rag.source_map`.

## 1. Topic inverted index — what is covered, where

{chr(10).join(topic_md) if topic_md else '_(no topics indexed yet — has ingestion run?)_'}

## 2. Per-policy catalog

{(chr(10) + chr(10)).join(policies_md) if policies_md else '_(no policies indexed yet)_'}

---

## 3. Machine-readable index

A JSON form of this catalog is at `rag/source_map.json` — used by the faithfulness verifier to look up whether a claim could plausibly trace to a chunk before allowing it through.

## 4. Coverage gaps (transparent)

These are areas where the corpus is thin. Bot questions on these should refuse:

- **Regulatory documents (IRDAI):** Deferred — see `decisions.md` D-017. The bot's faithfulness Gate 1 (retrieval floor) refuses these correctly.
- **Premium pricing:** Out of scope (advisor, not broker). See `decisions.md` D-007.
- **Categories beyond Health (Life, Motor, Travel):** Out of scope v1.
- **Star Health policies (11 PDFs):** Star Health's CDN actively blocks scripted downloads. Mitigation pending in v2.
"""
    return md


def main():
    extracted = load_extracted_policies()
    machine = build_machine_index()

    JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(json.dumps(machine, indent=2))

    md = build_markdown(machine, extracted)
    MD_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    MD_OUTPUT.write_text(md)

    print(f"Wrote:")
    print(f"  {MD_OUTPUT.relative_to(ROOT)}  ({len(md)} bytes)")
    print(f"  {JSON_OUTPUT.relative_to(ROOT)}  ({machine.get('total_chunks', 0)} chunks)")
    print(f"Policies indexed: {len({c['policy_id'] for c in machine.get('chunks', [])})}")


if __name__ == "__main__":
    main()
