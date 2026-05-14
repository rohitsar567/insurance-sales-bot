"""Extract every PDF in rag/corpus/ (non-regulatory) that doesn't already have a .json.

Differences from rag.extract:
  - Concurrency 3 via asyncio.Semaphore (fits inside NIM's 40 req/min)
  - V4-Pro primary, V4-Flash fallback (NOT Sarvam)
  - Uses softened EXTRACT_SYSTEM prompt (already in rag/extract.py)
  - Preserves .json files that already exist (skip-if-exists)
"""
from __future__ import annotations
import asyncio, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rag.extract import extract_one, find_pdfs, load_manifest, init_db, EXTRACTED_DIR, ROOT
from rag.ingest import policy_id_for
from backend.providers.nvidia_nim_llm import get_brain_llm, get_fast_brain_llm


async def run_one(pdf, manifest, primary, fallback, sem, idx, total):
    async with sem:
        rel = str(pdf.relative_to(ROOT))
        entry = manifest.get(rel, {})
        t0 = time.time()
        print(f"[{idx:>3}/{total}] {pdf.parent.name}__{pdf.stem[:50]}", flush=True)
        try:
            result = await asyncio.wait_for(extract_one(pdf, entry, primary, fallback), timeout=240)
            return ("OK" if result else "FAIL", pdf, time.time() - t0)
        except Exception as e:
            return (f"FAIL ({type(e).__name__})", pdf, time.time() - t0)


async def main():
    init_db()
    all_pdfs = [p for p in find_pdfs() if p.parent.name != "regulatory"]
    # Skip if .json already exists
    targets = []
    for p in all_pdfs:
        pid = policy_id_for(p)
        if not (EXTRACTED_DIR / f"{pid}.json").exists():
            targets.append(p)
    manifest = load_manifest()
    primary = get_brain_llm()
    fallback = get_fast_brain_llm()
    sem = asyncio.Semaphore(3)
    print(f"Total PDFs: {len(all_pdfs)}, need extraction: {len(targets)}\n")
    t_start = time.time()
    tasks = [run_one(pdf, manifest, primary, fallback, sem, i + 1, len(targets))
             for i, pdf in enumerate(targets)]
    results = await asyncio.gather(*tasks)
    ok = sum(1 for r in results if r[0] == "OK")
    print(f"\n=== Final: {ok}/{len(targets)} OK in {time.time()-t_start:.0f}s ===")
    for status, pdf, elapsed in results:
        if not status.startswith("OK"):
            print(f"  FAIL | {policy_id_for(pdf):60s} | {status} ({elapsed:.0f}s)")

if __name__ == "__main__":
    asyncio.run(main())
