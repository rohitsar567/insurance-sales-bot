"""Re-extract just the still-failing PDFs (those with _raw.txt but no .json).

Uses asyncio + Semaphore(3) — 3 concurrent NIM calls at a time. NIM's
40 req/min budget = ~6 concurrent in-flight comfortably; 3 leaves headroom
for the eval/sweep that might run alongside.

Reuses extract_one from rag.extract so behaviour matches the canonical
extraction path. Logs OK/FAIL per PDF + final tally.
"""
import asyncio, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.extract import (
    extract_one, find_pdfs, load_manifest, init_db,
    EXTRACTED_DIR, ROOT,
)
from rag.ingest import policy_id_for
from backend.providers.nvidia_nim_llm import get_brain_llm
from backend.providers.sarvam_llm import SarvamLLM


async def run_one(pdf, manifest, primary, fallback, sem, idx, total):
    async with sem:
        rel = str(pdf.relative_to(ROOT))
        entry = manifest.get(rel, {})
        t0 = time.time()
        print(f"[{idx}/{total}] {pdf.parent.name}__{pdf.stem[:50]}")
        try:
            result = await asyncio.wait_for(
                extract_one(pdf, entry, primary, fallback),
                timeout=180,  # 3 min hard cap per PDF
            )
            elapsed = time.time() - t0
            return ("OK" if result else "FAIL", pdf, elapsed)
        except Exception as e:
            return (f"FAIL ({type(e).__name__})", pdf, time.time() - t0)


async def main():
    init_db()
    pdfs = find_pdfs()
    manifest = load_manifest()

    # Filter to those with _raw.txt but no .json (the failing set)
    targets = []
    for pdf in pdfs:
        pid = policy_id_for(pdf)
        json_path = EXTRACTED_DIR / f"{pid}.json"
        raw_path = EXTRACTED_DIR / f"{pid}._raw.txt"
        if not json_path.exists() and raw_path.exists():
            targets.append(pdf)

    print(f"Re-extracting {len(targets)} failed PDFs (concurrency=3)...\n")

    primary = get_brain_llm()
    fallback = SarvamLLM()
    sem = asyncio.Semaphore(3)

    tasks = [
        run_one(pdf, manifest, primary, fallback, sem, i+1, len(targets))
        for i, pdf in enumerate(targets)
    ]
    results = await asyncio.gather(*tasks)

    ok = sum(1 for r in results if r[0] == "OK")
    print(f"\n=== Final: {ok}/{len(targets)} OK ===")
    for status, pdf, elapsed in results:
        if not status.startswith("OK"):
            print(f"  FAIL | {pdf.parent.name}__{pdf.stem[:60]} | {status} ({elapsed:.0f}s)")


if __name__ == "__main__":
    asyncio.run(main())
