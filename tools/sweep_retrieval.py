"""Stage 2 — retrieval hyperparameter sweep.

Run AFTER Stage 1 (chunk_sweep.py) so we use the winning chunk_size/overlap.

Sweeps top_k × MIN_TOP_SCORE on the same gold Q&A. We hold the chunking
config and the embedder constant — the variables here are how AGGRESSIVELY
we filter retrieval (Gate 1 floor) and how MANY chunks we feed the LLM.

Output:
  kb/calculations/retrieval_sweep_results.md — leaderboard
  eval/retrieval_sweep_results.json — raw

Run:
  python tools/sweep_retrieval.py
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
RESULTS_JSON = ROOT / "eval" / "retrieval_sweep_results.json"
RESULTS_MD = ROOT / "kb" / "calculations" / "retrieval_sweep_results.md"

# Read chunk-sweep winner if present; else use defaults.
def winning_chunk_params() -> tuple[int, int]:
    p = ROOT / "eval" / "chunk_sweep_results.json"
    if p.exists():
        try:
            d = json.load(open(p))
            results = [c for c in d.get("results", []) if c.get("factual_accuracy") is not None]
            if results:
                winner = max(results, key=lambda c: c["factual_accuracy"] * 0.7 + (c.get("citation_accuracy") or 0) * 0.3)
                return winner["chunk_size"], winner["overlap"]
        except Exception:
            pass
    return 800, 120  # fallback


GRID_TOP_K = [3, 5, 7, 10]
GRID_FLOOR = [0.25, 0.30, 0.35]
EVAL_LIMIT = 25


def run(cmd: list[str], env: dict = None, label: str = "") -> tuple[int, str, float]:
    full_env = {**os.environ, **(env or {})}
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=full_env, cwd=str(ROOT))
    elapsed = time.time() - t0
    if label:
        print(f"  {label} → exit={proc.returncode} ({elapsed:.1f}s)")
    return proc.returncode, proc.stdout + proc.stderr, elapsed


def patch_min_top_score(value: float):
    """Edit backend/faithfulness.py MIN_TOP_SCORE in place. Reversed at the end."""
    import re
    p = ROOT / "backend" / "faithfulness.py"
    txt = p.read_text()
    txt2 = re.sub(r"MIN_TOP_SCORE\s*=\s*[\d.]+", f"MIN_TOP_SCORE = {value}", txt)
    p.write_text(txt2)


def main():
    venv_py = ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable

    cs, ov = winning_chunk_params()
    print(f"Using winning chunk params from Stage 1: chunk_size={cs}, overlap={ov}")

    # Need to ingest ONCE with the winning chunk params, then sweep top_k+floor on top
    print("\n=== Ingesting once with winning chunk params ===")
    shutil.rmtree(ROOT / "rag" / "vectors", ignore_errors=True)
    (ROOT / "rag" / "vectors").mkdir(parents=True, exist_ok=True)
    env = {"CHUNK_TOKENS": str(cs), "CHUNK_OVERLAP_TOKENS": str(ov)}
    rc, _, _ = run([py, "-m", "rag.ingest"], env=env, label="ingest")
    if rc != 0:
        print("ingest failed; aborting")
        return 1

    # Backup faithfulness file
    orig_faith = (ROOT / "backend" / "faithfulness.py").read_text()

    results = []
    try:
        for top_k in GRID_TOP_K:
            for floor in GRID_FLOOR:
                print(f"\n=== Cell — top_k={top_k}, MIN_TOP_SCORE={floor} ===")
                patch_min_top_score(floor)
                env2 = {**env, "RAG_TOP_K": str(top_k)}
                rc, log, eval_s = run([py, "-m", "eval.run", "--limit", str(EVAL_LIMIT)], env=env2, label="eval")
                try:
                    r = json.load(open(ROOT / "eval" / "results.json"))
                    s = r.get("summary", {})
                    factual = s.get("factual_accuracy", 0)
                    citation = s.get("citation_accuracy", 0)
                    refusal = s.get("refusal_precision", 0)
                    latencies = sorted(rec.get("latency_ms", 0) for rec in r.get("results", []))
                    p50 = latencies[len(latencies) // 2] if latencies else None
                    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
                except Exception:
                    factual = citation = refusal = None
                    p50 = p95 = None

                cell = {
                    "top_k": top_k,
                    "min_top_score": floor,
                    "factual_accuracy": factual,
                    "citation_accuracy": citation,
                    "refusal_precision": refusal,
                    "p50_latency_ms": p50,
                    "p95_latency_ms": p95,
                    "eval_seconds": round(eval_s, 1),
                }
                results.append(cell)
                RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
                RESULTS_JSON.write_text(json.dumps({"chunk_params": {"chunk_size": cs, "overlap": ov}, "results": results}, indent=2))
                print(f"  factual={factual} citation={citation} p95={p95}ms")
    finally:
        # Always restore the original faithfulness.py
        (ROOT / "backend" / "faithfulness.py").write_text(orig_faith)

    # Winner
    valid = [c for c in results if c.get("factual_accuracy") is not None]
    if valid:
        winner = max(valid, key=lambda c: c["factual_accuracy"] * 0.7 + (c.get("citation_accuracy") or 0) * 0.3)
    else:
        winner = None

    rows = []
    rows.append("# Stage 2 — Retrieval Hyperparameter Sweep")
    rows.append("")
    rows.append(f"_Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} on top of Stage 1 winner (chunk_size={cs}, overlap={ov})._")
    rows.append("")
    if winner:
        rows.append(f"**Winner:** `top_k={winner['top_k']}`, `MIN_TOP_SCORE={winner['min_top_score']}` — factual {winner['factual_accuracy']*100:.1f}%, citation {winner['citation_accuracy']*100:.1f}%")
    rows.append("")
    rows.append("## All cells")
    rows.append("")
    rows.append("| top_k | MIN_TOP_SCORE | factual | citation | refusal | p50 | p95 |")
    rows.append("| --- | --- | --- | --- | --- | --- | --- |")
    for c in results:
        rows.append(
            f"| {c['top_k']} | {c['min_top_score']} | "
            f"{(c['factual_accuracy'] or 0)*100:.1f}% | {(c['citation_accuracy'] or 0)*100:.1f}% | "
            f"{(c['refusal_precision'] or 0)*100:.1f}% | {c.get('p50_latency_ms')}ms | {c.get('p95_latency_ms')}ms |"
        )
    rows.append("")
    rows.append("## Recommendation")
    rows.append("")
    if winner:
        rows.append(f"Set `RAG_TOP_K = {winner['top_k']}` in `backend/config.py` and `MIN_TOP_SCORE = {winner['min_top_score']}` in `backend/faithfulness.py`.")
    RESULTS_MD.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_MD.write_text("\n".join(rows))
    print(f"\nDone — winner saved to {RESULTS_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
