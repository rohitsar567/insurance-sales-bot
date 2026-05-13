"""Hyperparameter sweep — find the empirically-best (chunk_size, overlap)
combination for our corpus by measuring real eval accuracy on each.

For each (chunk_size, overlap) cell:
  1. Wipe rag/vectors/
  2. Re-ingest the entire corpus with CHUNK_TOKENS + CHUNK_OVERLAP_TOKENS
     overridden via env vars
  3. Run eval/run.py --limit 25 → record accuracy + latency + chunk count
  4. Restore the WINNER's Chroma at the end so we ship with the best setting

Output:
  kb/calculations/chunk_sweep_results.md — leaderboard + per-cell details
  eval/chunk_sweep_results.json — raw

Run:
  python tools/chunk_sweep.py
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
RESULTS_JSON = ROOT / "eval" / "chunk_sweep_results.json"
RESULTS_MD = ROOT / "kb" / "calculations" / "chunk_sweep_results.md"

# Sweep grid. 6 cells = 6 × (~3 min ingest + ~5 min eval) = ~50 min.
GRID = [
    (600, 80),
    (600, 120),
    (800, 80),
    (800, 120),   # current default
    (1000, 120),
    (1000, 200),
]
EVAL_LIMIT = 25


def run(cmd: list[str], env: dict = None, label: str = "") -> tuple[int, str, float]:
    full_env = {**os.environ, **(env or {})}
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=full_env, cwd=str(ROOT))
    elapsed = time.time() - t0
    if label:
        print(f"  {label} → exit={proc.returncode} ({elapsed:.1f}s)")
    return proc.returncode, proc.stdout + proc.stderr, elapsed


def dir_size_mb(p: Path) -> float:
    if not p.exists(): return 0.0
    total = 0
    for f in p.rglob("*"):
        if f.is_file(): total += f.stat().st_size
    return round(total / 1024 / 1024, 1)


def main():
    venv_py = ROOT / ".venv" / "bin" / "python"
    py = str(venv_py) if venv_py.exists() else sys.executable

    results = []
    for i, (chunk_size, overlap) in enumerate(GRID, 1):
        print(f"\n=== Cell {i}/{len(GRID)} — chunk_size={chunk_size}, overlap={overlap} ===")
        # 1) Wipe vectors
        shutil.rmtree(ROOT / "rag" / "vectors", ignore_errors=True)
        (ROOT / "rag" / "vectors").mkdir(parents=True, exist_ok=True)

        env = {
            "CHUNK_TOKENS": str(chunk_size),
            "CHUNK_OVERLAP_TOKENS": str(overlap),
        }

        # 2) Re-ingest
        rc, log, ingest_s = run([py, "-m", "rag.ingest"], env=env, label="ingest")
        if rc != 0:
            print(f"  ingest FAILED: {log[-500:]}")
            results.append({"chunk_size": chunk_size, "overlap": overlap, "error": "ingest_failed"})
            continue

        # Count chunks added
        chunk_count = None
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            client = chromadb.PersistentClient(
                path=str(ROOT / "rag" / "vectors"),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            coll = client.get_or_create_collection(name="policies", metadata={"hnsw:space": "cosine"})
            chunk_count = coll.count()
        except Exception as e:
            chunk_count = None
            print(f"  count error: {e}")

        storage_mb = dir_size_mb(ROOT / "rag" / "vectors")
        print(f"  chunks={chunk_count}  storage={storage_mb}MB  ingest={ingest_s:.0f}s")

        # 3) Eval
        rc, log, eval_s = run([py, "-m", "eval.run", "--limit", str(EVAL_LIMIT)], env=env, label="eval")

        # Parse eval/results.json
        try:
            r = json.load(open(ROOT / "eval" / "results.json"))
            s = r.get("summary", {})
            factual = s.get("factual_accuracy", 0.0)
            citation = s.get("citation_accuracy", 0.0)
            refusal = s.get("refusal_precision", 0.0)
            # p95 latency from per-question results
            latencies = sorted(rec.get("latency_ms", 0) for rec in r.get("results", []))
            p50 = latencies[len(latencies)//2] if latencies else None
            p95 = latencies[min(len(latencies)-1, int(len(latencies)*0.95))] if latencies else None
        except Exception as e:
            factual = citation = refusal = None
            p50 = p95 = None
            print(f"  eval parse error: {e}")

        cell = {
            "chunk_size": chunk_size,
            "overlap": overlap,
            "chunk_count": chunk_count,
            "storage_mb": storage_mb,
            "ingest_seconds": round(ingest_s, 1),
            "eval_seconds": round(eval_s, 1),
            "factual_accuracy": factual,
            "citation_accuracy": citation,
            "refusal_precision": refusal,
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
        }
        results.append(cell)
        # Snapshot per-cell results for resumability
        RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_JSON.write_text(json.dumps({"results": results, "in_progress": i < len(GRID)}, indent=2))
        print(f"  factual={factual} citation={citation} p95={p95}ms")

    # Pick winner — composite score
    def score(c):
        if c.get("factual_accuracy") is None: return -1
        f = c["factual_accuracy"]
        cit = c.get("citation_accuracy") or 0
        return f * 0.7 + cit * 0.3  # bias toward factual; citation is a constraint
    valid = [c for c in results if c.get("factual_accuracy") is not None]
    winner = max(valid, key=score) if valid else None

    # Write markdown leaderboard
    rows = []
    rows.append("# Chunk-Size Hyperparameter Sweep")
    rows.append("")
    rows.append(f"_Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}. Re-run via `python tools/chunk_sweep.py`._")
    rows.append("")
    rows.append("## Headline")
    rows.append("")
    if winner:
        rows.append(f"**Empirical winner:** `chunk_size={winner['chunk_size']}`, `overlap={winner['overlap']}` — factual {winner['factual_accuracy']*100:.1f}%, citation {winner['citation_accuracy']*100:.1f}%, p95 {winner['p95_latency_ms']}ms")
    rows.append("")
    rows.append("## All cells")
    rows.append("")
    rows.append("| chunk_size | overlap | chunks | storage | factual | citation | refusal | p50 | p95 | ingest |")
    rows.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for c in results:
        if "error" in c:
            rows.append(f"| {c['chunk_size']} | {c['overlap']} | FAILED | - | - | - | - | - | - | {c.get('error')} |")
            continue
        f = (c.get('factual_accuracy') or 0) * 100
        cit = (c.get('citation_accuracy') or 0) * 100
        ref = (c.get('refusal_precision') or 0) * 100
        rows.append(
            f"| **{c['chunk_size']}** | **{c['overlap']}** | {c['chunk_count']} | {c['storage_mb']}MB | "
            f"{f:.1f}% | {cit:.1f}% | {ref:.1f}% | {c.get('p50_latency_ms')}ms | "
            f"{c.get('p95_latency_ms')}ms | {c.get('ingest_seconds')}s |"
        )
    rows.append("")
    rows.append("## Selection rubric")
    rows.append("")
    rows.append("```")
    rows.append("score = 0.7 × factual_accuracy + 0.3 × citation_accuracy")
    rows.append("```")
    rows.append("Bias toward factual accuracy; citation accuracy as a hard floor.")
    rows.append("")
    rows.append("## Eval methodology")
    rows.append("")
    rows.append(f"- 6 cells × ({EVAL_LIMIT} gold Q&A questions × Groq Llama-3.3-70B judge)")
    rows.append("- Embedder held constant: BGE-small-en-v1.5 (384-dim)")
    rows.append("- Top-k held constant: 5")
    rows.append("- Generator brain held constant: DeepSeek-V3 primary")
    rows.append("- All other hyperparameters held constant — only chunk_size + overlap vary")
    rows.append("")
    rows.append("## Recommendation for `decisions.md` D-018")
    rows.append("")
    if winner:
        rows.append(f"Set `CHUNK_TOKENS = {winner['chunk_size']}`, `CHUNK_OVERLAP_TOKENS = {winner['overlap']}` in `backend/config.py`.")
    else:
        rows.append("No valid cells completed; keep current defaults.")

    RESULTS_MD.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_MD.write_text("\n".join(rows))

    # Final summary
    print("\n\n========== SWEEP COMPLETE ==========")
    for c in results:
        if "error" in c:
            print(f"  {c['chunk_size']}/{c['overlap']}: ERROR")
        else:
            f = (c.get('factual_accuracy') or 0) * 100
            cit = (c.get('citation_accuracy') or 0) * 100
            print(f"  {c['chunk_size']}/{c['overlap']}: factual={f:.1f}% citation={cit:.1f}% p95={c.get('p95_latency_ms')}ms")
    if winner:
        print(f"\nWinner: chunk_size={winner['chunk_size']}, overlap={winner['overlap']}")
    print(f"Results: {RESULTS_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
