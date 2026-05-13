"""Run the gold Q&A eval against the local bot.

Pipeline:
  1. Load eval/gold_qa.json
  2. For each pair: call backend.orchestrator.handle_turn (in-process — fast)
     with policy_filter_ids=[pair.policy_id] to restrict retrieval to that policy
  3. Grade each reply using Groq Llama as the LLM-judge (different family ->
     non-circular eval; regex hard-facts grader runs alongside)
  4. Aggregate and write eval/results.md + eval/results.json

Run:
  python -m eval.run                  # full eval
  python -m eval.run --limit 30       # smoke test on first 30
  python -m eval.run --policy <pid>   # just one policy
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.orchestrator import handle_turn
from backend.providers.base import ChatMessage
from backend.providers.groq_llm import GroqLLM

ROOT = settings.CORPUS_DIR.parent.parent
GOLD_FILE = ROOT / "eval" / "gold_qa.json"
RESULTS_JSON = ROOT / "eval" / "results.json"
RESULTS_MD = ROOT / "eval" / "results.md"


JUDGE_SYSTEM = """You grade an insurance bot's answer against a gold answer.

You will be given:
  GOLD: the expected answer
  BOT: the bot's actual reply
  REFUSAL_EXPECTED: whether the bot should have refused

Output strict JSON:
{
  "factual_match": bool,         // does BOT semantically convey what GOLD says (or correctly refuse if REFUSAL_EXPECTED is true)?
  "citation_present": bool,       // does BOT contain a [Source: ...] tag (irrelevant for correct refusals)
  "score": 0-1.0,
  "reason": "one short sentence"
}

Be strict on factual_match — partial answers that miss the key number/value should be false.
For REFUSAL_EXPECTED=true, factual_match=true ONLY if BOT explicitly refuses or says the info isn't in the document.
"""


@dataclass
class EvalRecord:
    id: str
    policy_id: str
    question: str
    expected_answer: str
    bot_answer: str
    factual_match: bool
    citation_present: bool
    judge_score: float
    judge_reason: str
    expected_refusal: bool
    question_type: str
    difficulty: str
    blocked: bool
    faithfulness_passed: bool
    faithfulness_reasons: list[str] = field(default_factory=list)
    brain_used: str = ""
    latency_ms: int = 0


_judge: Optional[GroqLLM] = None
def get_judge() -> GroqLLM:
    global _judge
    if _judge is None:
        _judge = GroqLLM()
    return _judge


def _regex_factual_grade(gold_answer: str, bot_answer: str) -> tuple[bool, str]:
    """Deterministic factual grader for sweep runs (no LLM judge).

    Extracts numeric tokens + key noun phrases from GOLD; checks whether BOT
    contains them. Decent for our gold set which is dominated by specific
    numbers (24 months, ₹5L, etc.). Less precise than the LLM judge but
    consistent + free of rate limits.
    """
    gold_lower = gold_answer.lower()
    bot_lower = (bot_answer or "").lower()

    # Pull numeric tokens (with optional unit) from gold
    nums = re.findall(r"\b(\d+(?:[.,]\d+)?)(?:\s*(?:%|months?|days?|years?|lakh|crore|inr|₹|rs))?", gold_lower)
    # Strip the unit suffix to normalize comparison
    nums = list({n for n in nums if n and not (n.isdigit() and int(n) > 9999999)})  # drop UIN-like

    if not nums:
        # No numeric anchor — fall back to keyword overlap
        gold_words = set(re.findall(r"[a-z]{4,}", gold_lower))
        bot_words = set(re.findall(r"[a-z]{4,}", bot_lower))
        # Require at least 2 content-word overlap to mark "factual_match"
        overlap = gold_words & bot_words - {"policy", "insurance", "plan", "cover", "covered", "this", "that", "with", "from", "have", "after"}
        if len(overlap) >= 2:
            return True, f"keyword_overlap={sorted(overlap)[:5]}"
        return False, f"no_overlap (gold_words={list(gold_words)[:5]})"

    matched = [n for n in nums if n in bot_lower]
    if matched:
        return True, f"matched_nums={matched}"
    return False, f"missing_nums={nums[:5]}"


async def grade_one(gold: dict, bot_answer: str, blocked: bool, *, no_judge: bool = False) -> tuple[bool, bool, float, str]:
    """Returns (factual_match, citation_present, score, reason).

    When `no_judge=True`, skips the LLM-judge call and uses a regex-based
    grader instead — much faster + free of rate limits, suitable for sweeps.
    """
    citation_present = bool(re.search(r"\[(?:Source|Regulation):", bot_answer or "", flags=re.IGNORECASE))

    # Refusal handling
    refuse_kw = ("i don't see", "i don't have", "i'd rather not", "not in the document", "no information about", "not mentioned")
    is_refusal = any(kw in (bot_answer or "").lower() for kw in refuse_kw) or blocked
    if gold["expected_refusal"]:
        return (is_refusal, citation_present, 1.0 if is_refusal else 0.0,
                "correctly refused" if is_refusal else "did not refuse when expected")

    # If bot refused but the answer WAS expected, that's a miss
    if is_refusal:
        return (False, citation_present, 0.0, "bot refused on a question with a known answer")

    # Regex-grader path (sweep mode)
    if no_judge:
        ok, reason = _regex_factual_grade(gold["expected_answer"], bot_answer)
        return (ok, citation_present, 1.0 if ok else 0.0, f"regex: {reason}")

    # LLM-judge for factual content
    user = f"""GOLD: {gold['expected_answer']}
BOT: {bot_answer}
REFUSAL_EXPECTED: {gold['expected_refusal']}

Grade now."""
    try:
        res = await get_judge().chat(
            messages=[ChatMessage(role="system", content=JUDGE_SYSTEM),
                      ChatMessage(role="user", content=user)],
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        d = json.loads(res.text)
        return (bool(d.get("factual_match", False)),
                citation_present,
                float(d.get("score", 0.0)),
                str(d.get("reason", ""))[:200])
    except Exception as e:
        return (False, citation_present, 0.0, f"judge_error: {type(e).__name__}: {e}")


async def run_one(gold: dict, *, no_judge: bool = False) -> EvalRecord:
    """Single gold-question evaluation. Guarded so transient API errors (Groq
    rate limit, network timeout) don't kill the whole sweep — the question
    is recorded as failed and we move on."""
    try:
        turn = await handle_turn(
            user_text=gold["question"],
            chat_history=[],
            user_profile={},
            policy_filter_ids=[gold["policy_id"]],
        )
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        return EvalRecord(
            id=gold["id"],
            policy_id=gold["policy_id"],
            question=gold["question"],
            expected_answer=gold["expected_answer"],
            bot_answer=f"[ORCHESTRATOR ERROR] {msg}",
            factual_match=False,
            citation_present=False,
            judge_score=0.0,
            judge_reason=f"orchestrator_error: {msg}",
            expected_refusal=gold["expected_refusal"],
            question_type=gold["question_type"],
            difficulty=gold["difficulty"],
            blocked=False,
            faithfulness_passed=False,
            faithfulness_reasons=[f"orchestrator_error: {msg}"],
            brain_used="error",
            latency_ms=0,
        )
    try:
        factual, citation, score, reason = await grade_one(gold, turn.reply_text, turn.blocked, no_judge=no_judge)
    except Exception as e:  # noqa: BLE001
        factual = False
        citation = bool(turn.citations) if hasattr(turn, "citations") else False
        score = 0.0
        reason = f"grader_error: {type(e).__name__}: {str(e)[:160]}"
    return EvalRecord(
        id=gold["id"],
        policy_id=gold["policy_id"],
        question=gold["question"],
        expected_answer=gold["expected_answer"],
        bot_answer=turn.reply_text,
        factual_match=factual,
        citation_present=citation,
        judge_score=score,
        judge_reason=reason,
        expected_refusal=gold["expected_refusal"],
        question_type=gold["question_type"],
        difficulty=gold["difficulty"],
        blocked=turn.blocked,
        faithfulness_passed=turn.faithfulness_passed,
        faithfulness_reasons=turn.faithfulness_reasons,
        brain_used=turn.brain_used,
        latency_ms=turn.latency_ms,
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--policy", default=None)
    parser.add_argument("--no-judge", action="store_true",
                        help="Use regex grader instead of Groq LLM-judge (free of rate limits; used by sweeps)")
    args = parser.parse_args()

    if not GOLD_FILE.exists():
        print(f"Missing {GOLD_FILE} — run `python -m eval.generate_gold` first")
        return 1
    gold = json.loads(GOLD_FILE.read_text())
    if args.policy:
        gold = [g for g in gold if g["policy_id"] == args.policy]
    if args.limit:
        gold = gold[: args.limit]

    print(f"Running eval on {len(gold)} questions...\n")
    results: list[EvalRecord] = []
    t0 = time.time()
    for i, g in enumerate(gold, 1):
        rec = await run_one(g, no_judge=args.no_judge)
        results.append(rec)
        ok_factual = "✓" if rec.factual_match else "✗"
        ok_cite = "✓" if rec.citation_present else " "
        print(f"[{i:>3}/{len(gold)}] {ok_factual} {ok_cite} [{rec.judge_score:.2f}] {rec.question[:60]:<60} | {rec.judge_reason[:60]}")

    elapsed = time.time() - t0

    # Aggregate
    n = len(results)
    factual_acc = sum(1 for r in results if r.factual_match) / max(1, n)
    citation_acc = sum(1 for r in results if r.citation_present and not r.expected_refusal) / max(1, sum(1 for r in results if not r.expected_refusal))
    refusal_n = sum(1 for r in results if r.expected_refusal)
    refusal_correct = sum(1 for r in results if r.expected_refusal and r.factual_match)
    refusal_precision = refusal_correct / max(1, refusal_n)

    by_type_factual: dict[str, list[bool]] = defaultdict(list)
    by_brain: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_type_factual[r.question_type].append(r.factual_match)
        by_brain[r.brain_used.split("::")[0]].append(r.factual_match)

    summary = {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 1),
        "n_questions": n,
        "factual_accuracy": round(factual_acc, 3),
        "citation_accuracy": round(citation_acc, 3),
        "refusal_precision": round(refusal_precision, 3),
        "by_type": {t: round(sum(vs) / len(vs), 3) for t, vs in by_type_factual.items()},
        "by_brain": {b: round(sum(vs) / len(vs), 3) for b, vs in by_brain.items()},
        "blocked_count": sum(1 for r in results if r.blocked),
    }

    RESULTS_JSON.write_text(json.dumps({"summary": summary, "results": [asdict(r) for r in results]}, indent=2))

    md = render_md(summary, results)
    RESULTS_MD.write_text(md)

    print(f"\n========== SUMMARY ==========")
    print(f"  Factual accuracy:    {factual_acc*100:.1f}%")
    print(f"  Citation accuracy:   {citation_acc*100:.1f}%")
    print(f"  Refusal precision:   {refusal_precision*100:.1f}%")
    print(f"  Blocked:             {summary['blocked_count']}/{n}")
    print(f"  By brain: {summary['by_brain']}")
    print(f"  Elapsed:             {elapsed:.1f}s")
    print(f"  Results:             {RESULTS_MD.relative_to(ROOT)}")
    return 0


def render_md(summary: dict, results: list[EvalRecord]) -> str:
    by_type = summary["by_type"]
    by_brain = summary["by_brain"]
    md_type = "\n".join(f"| {t} | {pct*100:.1f}% |" for t, pct in sorted(by_type.items(), key=lambda kv: -kv[1]))
    md_brain = "\n".join(f"| {b} | {pct*100:.1f}% |" for b, pct in sorted(by_brain.items(), key=lambda kv: -kv[1]))

    misses = [r for r in results if not r.factual_match][:15]
    miss_table = "\n".join(
        f"| {r.id[:60]} | {r.question[:60]} | {r.bot_answer[:80]} | {r.judge_reason[:60]} |"
        for r in misses
    )

    return f"""# Eval Results — {summary['ran_at']}

## Headline

| Metric | Value |
| --- | --- |
| Questions run | {summary['n_questions']} |
| **Factual accuracy** | **{summary['factual_accuracy']*100:.1f}%** |
| **Citation accuracy** | **{summary['citation_accuracy']*100:.1f}%** |
| **Refusal precision** | **{summary['refusal_precision']*100:.1f}%** |
| Blocked by faithfulness | {summary['blocked_count']} |
| Elapsed | {summary['elapsed_seconds']} s |

## By question type

| Type | Accuracy |
| --- | --- |
{md_type}

## By brain (router winners)

| Brain | Accuracy |
| --- | --- |
{md_brain}

## Sample misses (up to 15)

| id | question | bot_answer | reason |
| --- | --- | --- | --- |
{miss_table}

---

*Grader: Groq Llama-3.3-70B-versatile (different model family from Sarvam-M to avoid circular eval).*
*Full per-question results: `eval/results.json`.*
"""


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
