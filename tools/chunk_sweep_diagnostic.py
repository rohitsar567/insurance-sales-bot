"""Diagnostic — explain why chunk_sweep results are identical.

Two cells: chunk=400/60 (very small) vs chunk=1500/300 (very large).
For each cell, ingest + run eval --limit 8, capture per-question bot_answer
+ blocked + retrieved-chunk-count. Then diff.

This tells us whether the constant accuracy is because:
 (A) chunk size doesn't change retrieval enough → byte-identical bot_answers
 (B) chunk size changes retrieval → answers differ but eval score happens to round identical
 (C) faithfulness gate blocks the same questions → blocked-set is the constant
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAG_OUT = ROOT / "eval" / "chunk_diagnostic.json"

CELLS = [(400, 60), (1500, 300)]
LIMIT = 8


def run(cmd: list[str], env: dict) -> tuple[int, str]:
    full_env = {**os.environ, **env}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=full_env, cwd=str(ROOT))
    return proc.returncode, proc.stdout + proc.stderr


def main():
    py = str(ROOT / ".venv" / "bin" / "python")
    cells_out = []
    for chunk_size, overlap in CELLS:
        print(f"\n=== chunk_size={chunk_size}  overlap={overlap} ===")
        shutil.rmtree(ROOT / "rag" / "vectors", ignore_errors=True)
        (ROOT / "rag" / "vectors").mkdir(parents=True, exist_ok=True)

        env = {"CHUNK_TOKENS": str(chunk_size), "CHUNK_OVERLAP_TOKENS": str(overlap)}
        print("  ingest...", flush=True)
        t0 = time.time()
        rc, log = run([py, "-m", "rag.ingest"], env=env)
        print(f"  ingest exit={rc} ({time.time()-t0:.0f}s)")
        if rc != 0:
            print(f"  log tail:\n{log[-600:]}")
            return 1

        # Chunk count
        try:
            import chromadb
            from chromadb.config import Settings as CS
            cli = chromadb.PersistentClient(path=str(ROOT / "rag" / "vectors"), settings=CS(anonymized_telemetry=False))
            coll = cli.get_or_create_collection(name="policies", metadata={"hnsw:space": "cosine"})
            chunk_count = coll.count()
        except Exception as e:
            chunk_count = None
            print(f"  chunk count error: {e}")

        print(f"  chunks indexed: {chunk_count}")
        print("  eval...", flush=True)
        t0 = time.time()
        rc, log = run([py, "-m", "eval.run", "--limit", str(LIMIT)], env=env)
        print(f"  eval exit={rc} ({time.time()-t0:.0f}s)")

        # Snapshot per-question results
        results = json.loads((ROOT / "eval" / "results.json").read_text())
        rows = []
        for rec in results["results"]:
            rows.append({
                "id": rec["id"],
                "question": rec["question"],
                "blocked": rec["blocked"],
                "factual_match": rec["factual_match"],
                "brain": rec["brain_used"],
                "bot_answer": rec["bot_answer"],
                "judge_reason": rec["judge_reason"],
                "faithfulness_reasons": rec.get("faithfulness_reasons", []),
            })
        cells_out.append({
            "chunk_size": chunk_size,
            "overlap": overlap,
            "chunk_count": chunk_count,
            "summary": results["summary"],
            "rows": rows,
        })

    DIAG_OUT.write_text(json.dumps(cells_out, indent=2))

    # Diff
    a, b = cells_out
    print("\n========= DIFF =========")
    print(f"Cell A: chunk={a['chunk_size']}/{a['overlap']}  chunks={a['chunk_count']}  factual={a['summary']['factual_accuracy']}  blocked={a['summary']['blocked_count']}")
    print(f"Cell B: chunk={b['chunk_size']}/{b['overlap']}  chunks={b['chunk_count']}  factual={b['summary']['factual_accuracy']}  blocked={b['summary']['blocked_count']}")

    same_blocked = 0
    same_answer = 0
    different_answer_same_factual = 0
    different_factual = 0
    by_id_b = {r["id"]: r for r in b["rows"]}
    for ra in a["rows"]:
        rb = by_id_b.get(ra["id"])
        if not rb:
            continue
        if ra["blocked"] == rb["blocked"]:
            same_blocked += 1
        if ra["bot_answer"] == rb["bot_answer"]:
            same_answer += 1
        elif ra["factual_match"] == rb["factual_match"]:
            different_answer_same_factual += 1
        if ra["factual_match"] != rb["factual_match"]:
            different_factual += 1

    n = min(len(a["rows"]), len(b["rows"]))
    print(f"  {same_blocked}/{n} same blocked-state")
    print(f"  {same_answer}/{n} byte-identical bot_answer")
    print(f"  {different_answer_same_factual}/{n} different bot_answer but same factual verdict")
    print(f"  {different_factual}/{n} different factual verdict")

    print(f"\nDiagnostic written to {DIAG_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
