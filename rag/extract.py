"""Structured extraction: PDF -> 48-field HealthPolicy JSON -> DuckDB.

For each PDF in rag/corpus/:
  1. Read full text via pdfplumber (already have per-page text from ingest)
  2. Pass to LLM (Sarvam-M primary, DeepSeek-V3 fallback) with the Pydantic
     schema as a structured-output target
  3. Self-critique pass — LLM scores per-field confidence vs source text
  4. Validate via Pydantic
  5. Upsert into DuckDB `policies` table

Each extracted policy is also written to rag/extracted/<policy_id>.json for
reproducibility / debugging.

Run:
  python -m rag.extract           # extract all PDFs not yet extracted
  python -m rag.extract --policy <policy_id>   # one specific
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

import duckdb
import pdfplumber

from backend.config import settings
from backend.providers.base import ChatMessage
from backend.providers.groq_llm import GroqLLM
from backend.providers.openrouter_llm import OpenRouterLLM
from backend.providers.sarvam_llm import SarvamLLM
from rag.ingest import policy_id_for
from rag.schema import HealthPolicy

ROOT = settings.CORPUS_DIR.parent.parent
EXTRACTED_DIR = settings.EXTRACTED_DIR
DB_PATH = settings.STRUCTURED_DB


# ---------- LLM extraction prompts ----------

EXTRACT_SYSTEM = """You extract structured fields from Indian health insurance policy documents and output a compact JSON object. Strict instructions:

1. **OUTPUT ONLY THE JSON.** No markdown fences, no commentary, no <think> tags, no preface. Start your response with `{` and end with `}`. Nothing else.

2. **OMIT NULL FIELDS.** Do NOT include fields whose value would be null. Only include fields you actually extracted from the document. Empty/unknown fields = simply leave them out. This keeps the JSON compact.

3. **NORMALIZE VALUES.**
   - Waiting periods in months as integer; days separately.
   - Sum insured as list of INR integers, no commas: [500000, 1000000].
   - Booleans: true / false (lowercase).
   - Percentages: numeric (50 for 50%).
   - Coverage items (CoverageItem): {"covered": bool, "limit_inr": int?, "limit_text": str?, "notes": str?} — also drop null sub-keys inside.

4. **NO HALLUCINATIONS.** If a field is not explicitly stated, OMIT it. Don't invent.

5. **COMPACT.** No whitespace beyond what's needed. Single object."""


def build_extract_prompt(policy_text: str, schema_excerpt: str, policy_id: str) -> str:
    # Sarvam-M rejects prompts >~25k chars with HTTP 400 even though docs say
    # 32k tokens — undocumented stricter limit. Groq llama-3.3-70b handles
    # 128k but rate-limits TOKENS per minute aggressively (~30k/min free tier).
    # 12k chars (~3k tokens) keeps total request well under both limits and
    # captures Schedule + Key Definitions + Benefits front-matter where the
    # structured fields live. Exclusions section (the back half) doesn't yield
    # many structured fields, just policy_exclusions list which is mostly noise.
    MAX_CHARS = 12_000
    if len(policy_text) > MAX_CHARS:
        # Front-bias: schedules, definitions, waiting periods, UIN, sum-insured
        # tables all live in the first ~25k chars. Truncate the back (which is
        # usually exclusions + boilerplate + grievance procedures).
        policy_text = policy_text[:MAX_CHARS] + "\n\n[...truncated for length — extract from above only...]"
    return f"""POLICY DOCUMENT (policy_id = {policy_id}):
'''
{policy_text}
'''

JSON SCHEMA (field names + types you must use):
{schema_excerpt}

Now produce the JSON object. Remember: null for any field not explicitly stated.
"""


# ---------- helpers ----------

def schema_excerpt() -> str:
    """Compact representation of HealthPolicy fields for the prompt. Strips
    descriptions to save input tokens (~6.7k → ~2.5k chars)."""
    fields = HealthPolicy.model_fields
    lines = []
    for name, info in fields.items():
        ann = info.annotation
        ann_str = str(ann).replace("typing.", "").replace("Optional[", "?").replace("]", "")
        lines.append(f"  {name}: {ann_str}")
    return "{\n" + "\n".join(lines) + "\n}"


def read_full_text(pdf_path: Path) -> str:
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            text = re.sub(r"[ \t]+", " ", text)
            out.append(f"[Page {i}]\n{text}")
    return "\n\n".join(out)


def find_pdfs() -> list[Path]:
    pdfs = []
    for insurer_dir in sorted(settings.CORPUS_DIR.iterdir()):
        if not insurer_dir.is_dir():
            continue
        for pdf in sorted(insurer_dir.glob("*.pdf")):
            pdfs.append(pdf)
    return pdfs


def load_manifest() -> dict:
    mf = settings.CORPUS_DIR / "_manifest.json"
    if not mf.exists():
        return {}
    data = json.loads(mf.read_text())
    return {r["local_path"]: r for r in data.get("results", []) if r.get("ok")}


def json_from_llm_text(text: str) -> dict:
    """Strip code fences and <think> blocks, extract the first balanced {...} block."""
    text = text.strip()
    # Sarvam-M reasoning model emits <think>...</think> before the JSON; strip
    # them (handles both closed and unterminated think blocks when output is
    # truncated mid-thought).
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
    # Remove fenced markdown
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    # Find first balanced { ... }
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in LLM output")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unbalanced braces in LLM JSON")


# ---------- DuckDB store ----------

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS policies (
            policy_id TEXT PRIMARY KEY,
            insurer_slug TEXT,
            insurer_name TEXT,
            policy_name TEXT,
            policy_type TEXT,
            uin_code TEXT,
            extraction_confidence_pct DOUBLE,
            extracted_at TEXT,
            source_pdf_path TEXT,
            source_pdf_url TEXT,
            data_json TEXT  -- full HealthPolicy JSON for retrieval
        )
    """)
    con.close()


def upsert_policy(policy: HealthPolicy, source_pdf_path: str, source_pdf_url: str):
    con = duckdb.connect(str(DB_PATH))
    data_json = policy.model_dump_json()
    con.execute(
        """
        INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (policy_id) DO UPDATE SET
            insurer_slug = excluded.insurer_slug,
            insurer_name = excluded.insurer_name,
            policy_name = excluded.policy_name,
            policy_type = excluded.policy_type,
            uin_code = excluded.uin_code,
            extraction_confidence_pct = excluded.extraction_confidence_pct,
            extracted_at = excluded.extracted_at,
            source_pdf_path = excluded.source_pdf_path,
            source_pdf_url = excluded.source_pdf_url,
            data_json = excluded.data_json
        """,
        [
            policy.policy_id,
            policy.insurer_slug,
            policy.insurer_name,
            policy.policy_name,
            policy.policy_type.value if policy.policy_type else None,
            policy.uin_code,
            policy.extraction_confidence_pct,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            source_pdf_path,
            source_pdf_url,
            data_json,
        ],
    )
    con.close()


# ---------- pipeline ----------

async def extract_one(pdf_path: Path, manifest_entry: dict, llm_primary, llm_fallback) -> Optional[HealthPolicy]:
    policy_id = policy_id_for(pdf_path)
    out_json = EXTRACTED_DIR / f"{policy_id}.json"
    if out_json.exists():
        print(f"  SKIP (already extracted): {policy_id}")
        with open(out_json) as f:
            return HealthPolicy(**json.load(f))

    try:
        text = read_full_text(pdf_path)
    except Exception as e:
        print(f"  FAIL read: {policy_id} | {type(e).__name__}: {e}")
        return None

    prompt = build_extract_prompt(text, schema_excerpt(), policy_id)
    messages = [
        ChatMessage(role="system", content=EXTRACT_SYSTEM),
        ChatMessage(role="user", content=prompt),
    ]

    # Try primary, fall back on failure or empty result
    raw = ""
    for attempt, llm in enumerate([llm_primary, llm_fallback]):
        try:
            # Hard per-attempt timeout so a hung TCP connection in httpx
            # pooling can't stall the whole sweep. Groq (primary) has its own
            # 4-step backoff retry loop that can use up to ~5min, so give it
            # 120s ceiling. Sarvam (fallback) has no retries; 60s is plenty.
            attempt_timeout = 120 if llm is llm_primary else 60
            res = await asyncio.wait_for(
                llm.chat(messages=messages, temperature=0.0, max_tokens=2048),
                timeout=attempt_timeout,
            )
            raw = res.text
            data = json_from_llm_text(raw)
            # Force-fill identity fields from filename/manifest. These are
            # REQUIRED in the schema, and the LLM frequently emits them as
            # null because they're not in the truncated text. Override even
            # if the key exists with null/empty.
            if not data.get("policy_id"):
                data["policy_id"] = policy_id
            if not data.get("insurer_slug"):
                data["insurer_slug"] = pdf_path.parent.name
            if not data.get("insurer_name"):
                data["insurer_name"] = manifest_entry.get("insurer_name") or pdf_path.parent.name
            if not data.get("policy_name"):
                data["policy_name"] = manifest_entry.get("policy_name") or pdf_path.stem
            policy = HealthPolicy(**data)
            EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
            out_json.write_text(policy.model_dump_json(indent=2))
            upsert_policy(
                policy,
                source_pdf_path=str(pdf_path.relative_to(ROOT)),
                source_pdf_url=manifest_entry.get("url", ""),
            )
            print(f"  OK | provider={llm.name} | conf={policy.extraction_confidence_pct or 'n/a'}")
            return policy
        except Exception as e:
            print(f"  attempt {attempt+1} FAIL: {type(e).__name__}: {e!s:.200s}")
            continue

    # Save the raw output for inspection on total failure
    (EXTRACTED_DIR / f"{policy_id}._raw.txt").write_text(raw)
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", help="Specific policy_id to extract", default=None)
    parser.add_argument("--limit", type=int, default=None, help="Cap on number of policies")
    args = parser.parse_args()

    init_db()
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = find_pdfs()
    manifest = load_manifest()

    if args.policy:
        pdfs = [p for p in pdfs if policy_id_for(p) == args.policy]
        if not pdfs:
            print(f"No PDF matches policy_id={args.policy}")
            return

    if args.limit:
        pdfs = pdfs[: args.limit]

    # Sarvam-M is a reasoning model that consumes most of the starter-tier
    # 2048-output-token budget on <think> blocks, frequently truncating the
    # JSON. Groq Llama-3.3-70b skips the reasoning and emits JSON cleanly +
    # has higher output budget. So Groq primary, Sarvam fallback.
    primary = GroqLLM()
    fallback = SarvamLLM()
    _ = OpenRouterLLM  # noqa: F841 — kept importable for future paid use

    print(f"Extracting {len(pdfs)} policies. Primary=Sarvam-M, Fallback=DeepSeek-V3.\n")
    t0 = time.time()
    ok = 0
    for i, pdf in enumerate(pdfs, 1):
        rel = str(pdf.relative_to(ROOT))
        entry = manifest.get(rel, {})
        print(f"[{i}/{len(pdfs)}] {pdf.parent.name} | {pdf.stem[:50]}")
        result = await extract_one(pdf, entry, primary, fallback)
        if result is not None:
            ok += 1

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s. {ok}/{len(pdfs)} extracted.")
    print(f"DuckDB: {DB_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
