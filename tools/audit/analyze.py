"""Analyze audit transcripts → produce a markdown report.

Looks across all transcripts in 80-audit/<run_id>/transcripts/ for:
  - completion rates (turns reached / 30)
  - error / timeout rates (HTTP 5xx, network failures)
  - refusal rate (blocked=true or faithfulness_passed=false)
  - profile-completeness progression (which fact-find fields landed)
  - brain routing distribution (V4-Pro / V4-Flash / Maverick cross-check)
  - intent classification distribution
  - citation density (avg citations per non-blocked reply)
  - latency p50 / p95
  - failure-pattern clusters (recurring failure modes by archetype / style)
  - stuck-in-fact-find: where the bot kept re-asking the same question

Output: 80-audit/<run_id>/report.md
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
from pathlib import Path
from typing import Any

RESULTS_ROOT = Path(__file__).resolve().parent.parent.parent / "audit_results"


def _load_transcripts(run_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted((run_dir / "transcripts").glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception as e:
            print(f"  warn: failed to read {f.name}: {e}")
    return out


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = int(round((p / 100) * (len(xs) - 1)))
    return xs[k]


def _summarize_brain(turns: list[dict]) -> dict[str, int]:
    out: dict[str, int] = collections.Counter()
    for t in turns:
        b = t.get("brain_used")
        if b:
            # Coarse-grain: just the prefix before :: or +
            key = re.split(r"[:+]", b, maxsplit=1)[0]
            out[key] += 1
    return dict(out)


def _intent_dist(turns: list[dict]) -> dict[str, int]:
    return dict(collections.Counter(t.get("intent") for t in turns if t.get("intent")))


def _profile_progress(turns: list[dict]) -> dict[str, Any]:
    """How many distinct profile fields ended up captured across all turns?
    Returns a dict with the final set + the last turn at which each appeared
    so we can see if updates flowed through `profile_updates`.
    """
    captured: dict[str, int] = {}  # field → first turn it appeared
    for t in turns:
        pu = t.get("profile_updates") or {}
        for k in pu:
            captured.setdefault(k, t.get("turn", 0))
    return {"captured_fields": captured, "fields_captured": len(captured)}


def _stuck_in_factfind(turns: list[dict]) -> int:
    """Count turns where the brain stayed in needs_finder::* across N+ turns."""
    nf_turns = [t for t in turns if (t.get("brain_used") or "").startswith("needs_finder")]
    return len(nf_turns)


def _refusals(turns: list[dict]) -> int:
    return sum(1 for t in turns if t.get("blocked"))


def _faithfulness_fails(turns: list[dict]) -> int:
    return sum(1 for t in turns if t.get("faithfulness_passed") is False)


def _reask_clarify_count(turns: list[dict]) -> int:
    return sum(1 for t in turns if "reask_clarify" in (t.get("brain_used") or ""))


def _citation_density(turns: list[dict]) -> float | None:
    counts = [len(t.get("citations") or []) for t in turns if not t.get("blocked") and t.get("citations") is not None]
    if not counts:
        return None
    return round(sum(counts) / len(counts), 2)


def _errors(turns: list[dict]) -> int:
    return sum(1 for t in turns if t.get("error"))


def analyze_one(transcript: dict[str, Any]) -> dict[str, Any]:
    persona = transcript["persona"]
    turns = transcript.get("turns", [])
    latencies = [t.get("latency_ms") for t in turns if isinstance(t.get("latency_ms"), int)]
    return {
        "persona_id": persona["persona_id"],
        "archetype": persona["archetype"],
        "style": persona["style"],
        "name": persona["name"],
        "completed_turns": len(turns),
        "errors": _errors(turns),
        "refusals": _refusals(turns),
        "faithfulness_fails": _faithfulness_fails(turns),
        "reask_clarify": _reask_clarify_count(turns),
        "stuck_in_factfind": _stuck_in_factfind(turns),
        "brain_dist": _summarize_brain(turns),
        "intent_dist": _intent_dist(turns),
        "profile_progress": _profile_progress(turns),
        "citation_density": _citation_density(turns),
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
    }


def build_report(run_dir: Path) -> Path:
    transcripts = _load_transcripts(run_dir)
    if not transcripts:
        print(f"no transcripts in {run_dir}/transcripts/")
        return run_dir / "report.md"

    per_persona = [analyze_one(t) for t in transcripts]

    n = len(per_persona)
    total_turns = sum(p["completed_turns"] for p in per_persona)
    total_errors = sum(p["errors"] for p in per_persona)
    total_refusals = sum(p["refusals"] for p in per_persona)
    total_ffails = sum(p["faithfulness_fails"] for p in per_persona)
    total_reask = sum(p["reask_clarify"] for p in per_persona)
    avg_completed = round(total_turns / n, 1) if n else 0

    all_latencies = []
    for t in transcripts:
        for turn in t.get("turns", []):
            if isinstance(turn.get("latency_ms"), int):
                all_latencies.append(turn["latency_ms"])

    # Aggregate brain + intent
    agg_brain: dict[str, int] = collections.Counter()
    agg_intent: dict[str, int] = collections.Counter()
    for p in per_persona:
        for k, v in p["brain_dist"].items():
            agg_brain[k] += v
        for k, v in p["intent_dist"].items():
            agg_intent[k] += v

    # By archetype
    by_arch: dict[str, list[dict]] = collections.defaultdict(list)
    for p in per_persona:
        by_arch[p["archetype"]].append(p)

    # By style
    by_style: dict[str, list[dict]] = collections.defaultdict(list)
    for p in per_persona:
        by_style[p["style"]].append(p)

    # Profile-field coverage across all personas
    field_counts: dict[str, int] = collections.Counter()
    for p in per_persona:
        for k in p["profile_progress"]["captured_fields"]:
            field_counts[k] += 1

    # Identify worst performers
    worst_refusers = sorted(per_persona, key=lambda p: p["refusals"], reverse=True)[:10]
    worst_errors = sorted(per_persona, key=lambda p: p["errors"], reverse=True)[:10]

    lines: list[str] = []
    lines.append(f"# Bot Audit Report")
    lines.append(f"")
    lines.append(f"_Run directory: `{run_dir.name}`_")
    lines.append(f"_Generated automatically by `tools/audit/analyze.py`_")
    lines.append(f"")
    lines.append(f"## 1. Run summary")
    lines.append(f"")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Personas completed | **{n}** of 100 |")
    lines.append(f"| Total turns executed | **{total_turns}** of {n*30} expected |")
    lines.append(f"| Avg completed turns / persona | {avg_completed} |")
    lines.append(f"| Errors (HTTP / timeout / network) | {total_errors} ({total_errors/max(total_turns,1)*100:.1f}%) |")
    lines.append(f"| Refusals (blocked=true) | {total_refusals} ({total_refusals/max(total_turns,1)*100:.1f}%) |")
    lines.append(f"| Faithfulness gate fails | {total_ffails} |")
    lines.append(f"| Fact-find re-ask events | {total_reask} |")
    if all_latencies:
        lines.append(f"| Latency p50 | {_percentile(all_latencies, 50):.0f} ms |")
        lines.append(f"| Latency p95 | {_percentile(all_latencies, 95):.0f} ms |")
        lines.append(f"| Latency p99 | {_percentile(all_latencies, 99):.0f} ms |")
    lines.append(f"")

    lines.append(f"## 2. Brain routing")
    lines.append(f"")
    lines.append(f"| Brain | Turns |")
    lines.append(f"|---|---:|")
    for k, v in sorted(agg_brain.items(), key=lambda x: -x[1]):
        lines.append(f"| `{k}` | {v} |")
    lines.append(f"")

    lines.append(f"## 3. Intent distribution")
    lines.append(f"")
    lines.append(f"| Intent | Turns |")
    lines.append(f"|---|---:|")
    for k, v in sorted(agg_intent.items(), key=lambda x: -x[1]):
        lines.append(f"| `{k}` | {v} |")
    lines.append(f"")

    lines.append(f"## 4. Profile capture (across all personas)")
    lines.append(f"")
    lines.append(f"How many personas got each field captured at least once during the audit:")
    lines.append(f"")
    lines.append(f"| Field | Personas hit |")
    lines.append(f"|---|---:|")
    for k, v in sorted(field_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| `{k}` | {v} / {n} |")
    lines.append(f"")

    lines.append(f"## 5. Refusal + faithfulness by archetype")
    lines.append(f"")
    lines.append(f"| Archetype | Personas | Avg refusals/persona | Faithfulness fails |")
    lines.append(f"|---|---:|---:|---:|")
    for arch, plist in sorted(by_arch.items()):
        if not plist:
            continue
        avg_ref = sum(p["refusals"] for p in plist) / len(plist)
        ffs = sum(p["faithfulness_fails"] for p in plist)
        lines.append(f"| `{arch}` | {len(plist)} | {avg_ref:.1f} | {ffs} |")
    lines.append(f"")

    lines.append(f"## 6. Refusal + faithfulness by conversational style")
    lines.append(f"")
    lines.append(f"| Style | Personas | Avg refusals/persona | Faithfulness fails |")
    lines.append(f"|---|---:|---:|---:|")
    for style, plist in sorted(by_style.items()):
        if not plist:
            continue
        avg_ref = sum(p["refusals"] for p in plist) / len(plist)
        ffs = sum(p["faithfulness_fails"] for p in plist)
        lines.append(f"| `{style}` | {len(plist)} | {avg_ref:.1f} | {ffs} |")
    lines.append(f"")

    lines.append(f"## 7. Worst refusers (10 personas with most refusals)")
    lines.append(f"")
    lines.append(f"| Persona | Name | Archetype | Style | Refusals | Errors |")
    lines.append(f"|---|---|---|---|---:|---:|")
    for p in worst_refusers:
        lines.append(f"| `{p['persona_id']}` | {p['name']} | {p['archetype']} | {p['style']} | {p['refusals']} | {p['errors']} |")
    lines.append(f"")

    lines.append(f"## 8. Worst error-affected (10 personas with most HTTP errors)")
    lines.append(f"")
    lines.append(f"| Persona | Name | Archetype | Style | Errors | Completed turns |")
    lines.append(f"|---|---|---|---|---:|---:|")
    for p in worst_errors:
        lines.append(f"| `{p['persona_id']}` | {p['name']} | {p['archetype']} | {p['style']} | {p['errors']} | {p['completed_turns']} |")
    lines.append(f"")

    lines.append(f"## 9. Per-persona one-liner")
    lines.append(f"")
    lines.append(f"| ID | Name | Archetype | Style | Done | Errs | Refusals | Reask | Citations/reply |")
    lines.append(f"|---|---|---|---|---:|---:|---:|---:|---:|")
    for p in per_persona:
        cd = p["citation_density"]
        cd_str = f"{cd:.1f}" if cd is not None else "—"
        lines.append(
            f"| `{p['persona_id']}` | {p['name']} | {p['archetype']} | {p['style']} | "
            f"{p['completed_turns']} | {p['errors']} | {p['refusals']} | {p['reask_clarify']} | {cd_str} |"
        )
    lines.append(f"")

    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines))
    print(f"wrote {report_path}")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None, help="audit_results subdir to analyze (default: latest)")
    args = parser.parse_args()

    if args.run_id:
        run_dir = RESULTS_ROOT / args.run_id
    else:
        runs = sorted([d for d in RESULTS_ROOT.iterdir() if d.is_dir()], key=lambda d: d.stat().st_mtime)
        if not runs:
            print("no audit runs found in 80-audit/")
            return
        run_dir = runs[-1]
    build_report(run_dir)


if __name__ == "__main__":
    main()
