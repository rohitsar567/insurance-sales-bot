"""Full re-extraction over all 86 insurer PDFs.

Differences vs the existing rag.extract:
  1. DELETES the existing .json + ._raw.txt for each policy before running, so
     the skip-if-exists check in extract_one() does NOT short-circuit. Backs
     up the prior .json to .json.old in case the new pass is somehow worse.
  2. Concurrency 3 via asyncio.Semaphore — fits inside NIM's 40 req/min.
  3. Uses the post-D-019 + post-prompt-softening EXTRACT_SYSTEM prompt.
  4. Falls back to V4-Flash (not Sarvam-M) — Sarvam-M's <think> reasoning
     truncated ~20 prior runs by eating the 2048 output cap.

Run:
  .venv/bin/python -u tools/reextract_all.py
"""
from __future__ import annotations
import asyncio
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.extract import (
    extract_one, find_pdfs, load_manifest, init_db,
    EXTRACTED_DIR, ROOT,
)
from rag.ingest import policy_id_for
from backend.providers.nvidia_nim_llm import get_brain_llm


async def run_one(pdf: Path, manifest: dict, primary, fallback, sem, idx: int, total: int) -> tuple[str, Path, float]:
    async with sem:
        rel = str(pdf.relative_to(ROOT))
        entry = manifest.get(rel, {})
        pid = policy_id_for(pdf)
        json_path = EXTRACTED_DIR / f"{pid}.json"
        raw_path = EXTRACTED_DIR / f"{pid}._raw.txt"
        # Backup the prior good JSON in case the rerun produces worse output
        if json_path.exists():
            shutil.copy2(json_path, json_path.with_suffix(".json.old"))
            json_path.unlink()
        if raw_path.exists():
            raw_path.unlink()
        t0 = time.time()
        print(f"[{idx:>3}/{total}] {pdf.parent.name}__{pdf.stem[:50]}")
        try:
            result = await asyncio.wait_for(
                extract_one(pdf, entry, primary, fallback),
                timeout=240,
            )
            elapsed = time.time() - t0
            return ("OK" if result else "FAIL", pdf, elapsed)
        except Exception as e:
            return (f"FAIL ({type(e).__name__})", pdf, time.time() - t0)


async def main():
    init_db()
    pdfs = [p for p in find_pdfs() if p.parent.name != "regulatory"]
    manifest = load_manifest()

    primary = get_brain_llm()
    fallback = get_brain_llm()
    sem = asyncio.Semaphore(3)

    print(f"Re-extracting {len(pdfs)} insurer PDFs. via the brain chain (internal fallback), concurrency=3.\n")
    t_start = time.time()
    tasks = [
        run_one(pdf, manifest, primary, fallback, sem, i + 1, len(pdfs))
        for i, pdf in enumerate(pdfs)
    ]
    results = await asyncio.gather(*tasks)

    ok = sum(1 for r in results if r[0] == "OK")
    avg_time = sum(r[2] for r in results) / max(1, len(results))
    print(f"\n=== Final: {ok}/{len(pdfs)} OK in {time.time()-t_start:.0f}s (avg {avg_time:.1f}s/pdf) ===")
    for status, pdf, elapsed in results:
        if not status.startswith("OK"):
            print(f"  FAIL | {policy_id_for(pdf):60s} | {status} ({elapsed:.0f}s)")


if __name__ == "__main__":
    asyncio.run(main())
