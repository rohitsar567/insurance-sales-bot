"""Run the gold Q&A eval against the local bot.

⚠️ STATUS (2026-05-17): PENDING RE-PORT TO THE SINGLE-BRAIN ARCHITECTURE.
This harness was written for the pre-2026-05-15 `orchestrator.handle_turn`,
which is deleted. Two contracts changed and have no drop-in equivalent:
  • `orchestrator.handle_turn(user_text=, chat_history=, user_profile=,
    policy_filter_ids=)` → `single_brain.handle_turn(session, user_text,
    chat_history)`. Per-policy retrieval scoping is no longer a forced
    kwarg — the LLM chooses it as a `retrieve_policies` tool argument, so
    the old "restrict retrieval to pair.policy_id" guarantee cannot be
    reproduced without a redesign.
  • The separate LLM judge was retired in the three-chain collapse; the
    grader now reuses the brain chain (brain↔judge family separation no
    longer holds — a known eval-validity caveat to revisit).
Because `main()` overwrites eval/results.json (which the admin panel
displays), running it un-ported would publish invalid, un-scoped scores.
`main()` is therefore HARD-GUARDED until the re-port lands. Imports and the
reusable scaffolding (gold loader, regex grader, EvalRecord, IO) are kept
correct so the re-port is a focused change, not a rewrite.

Intended pipeline (once re-ported):
  1. Load eval/gold_qa.json
  2. For each pair: drive a single-brain turn with retrieval scoped to
     pair.policy_id (via the retrieve_policies tool path)
  3. Grade each reply (regex hard-facts grader + LLM grader)
  4. Aggregate and write eval/results.md + eval/results.json

Run (after re-port):
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
from backend.single_brain import handle_turn  # noqa: F401 — used post re-port (see module docstring)
from backend.providers.base import ChatMessage
from backend.providers.nvidia_nim_llm import get_brain_llm

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


_judge = None
def get_judge():
    """Returns the grader LLM. NOTE (2026-05-15 three-chain collapse): the
    separate judge accessor was removed, so the grader now reuses the brain
    chain (`get_brain_llm()`). Brain↔judge family separation no longer holds
    — a known eval-circularity caveat to address in the re-port (see module
    docstring). Kept import-correct; not exercised until the guard is lifted."""
    global _judge
    if _judge is None:
        _judge = get_brain_llm()
    return _judge


def _parse_judge_json(raw: str) -> Optional[dict]:
    """KI-022 — robust JSON parse for the Groq/NIM judge response.

    Groq Llama-3.3 occasionally returns truncated or trailing-comma JSON even
    with response_format=json_object. Try strict, then repair, then None.
    Caller falls back to the regex grader on None instead of scoring 0.
    """
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Repair pass: extract the first balanced {...} block + drop trailing commas
    try:
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not m:
            return None
        candidate = m.group(0)
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)  # trailing commas
        # Stitch a closing quote if the string ended mid-value
        if candidate.count('"') % 2 == 1:
            candidate = candidate + '"'
        # If still missing a closing brace, append one
        if candidate.count("{") > candidate.count("}"):
            candidate = candidate + "}"
        return json.loads(candidate)
    except Exception:
        return None


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
        d = _parse_judge_json(res.text)
        if d is None:
            # KI-022 (2026-05-14) — JSON-parse failure on 11/96 questions in the
            # 2026-05-14 baseline caused those questions to count as 0 factual
            # even when the bot answered correctly. Fall back to the regex
            # grader instead of dropping a 0 on the floor.
            ok, reason = _regex_factual_grade(gold["expected_answer"], bot_answer)
            return (ok, citation_present, 1.0 if ok else 0.0,
                    f"judge_json_unparseable→regex_fallback: {reason}")
        return (bool(d.get("factual_match", False)),
                citation_present,
                float(d.get("score", 0.0)),
                str(d.get("reason", ""))[:200])
    except Exception as e:
        # KI-022 — same fallback for actual exceptions (timeout, network, etc.)
        ok, reason = _regex_factual_grade(gold["expected_answer"], bot_answer)
        return (ok, citation_present, 1.0 if ok else 0.0,
                f"judge_error→regex_fallback ({type(e).__name__}): {reason}")


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
    # HARD GUARD (2026-05-17) — see module docstring. This harness targets
    # the deleted orchestrator API; running it un-ported would overwrite
    # eval/results.json (shown in the admin panel) with invalid, un-scoped
    # scores. Refuse loudly until the single-brain re-port lands.
    raise SystemExit(
        "eval/run.py is pending re-port to the single-brain architecture and "
        "is intentionally disabled — see the module docstring. It must NOT "
        "run un-ported (it would publish invalid scores to eval/results.json, "
        "which the admin panel displays)."
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--policy", default=None)
    parser.add_argument("--no-judge", action="store_true",
                        help="Use regex grader instead of Groq LLM-judge (free of rate limits; used by sweeps)")
    parser.add_argument("--workers", type=int, default=6,
                        help="Concurrent questions in flight. KI-024 — was serial, now parallel. "
                             "Cap is NIM's 40 req/min (~2 calls per question); 6 workers gives ~5× "
                             "speedup before saturating. Drop to 1 to reproduce historical serial timing.")
    parser.add_argument("--no-extract", action="store_true",
                        help="KI-053 (legacy) — skipped a now-removed pre-LLM "
                             "profile-extraction call to save ~1 request/question. "
                             "Dormant: the eval harness is pending re-port to the "
                             "single-brain architecture (see module docstring). "
                             "~25%% throughput gain; no grading impact.")
    args = parser.parse_args()

    # KI-053 — propagate the skip flag via env var so the deep-stack
    # orchestrator code path can read it without API churn.
    if args.no_extract:
        import os as _os
        _os.environ["INSURANCE_BOT_SKIP_PROFILE_EXTRACTOR"] = "1"

    if not GOLD_FILE.exists():
        print(f"Missing {GOLD_FILE} — run `python -m eval.generate_gold` first")
        return 1
    gold = json.loads(GOLD_FILE.read_text())
    if args.policy:
        gold = [g for g in gold if g["policy_id"] == args.policy]
    if args.limit:
        gold = gold[: args.limit]

    print(f"Running eval on {len(gold)} questions with {args.workers} workers...\n")
    results: list[EvalRecord] = [None] * len(gold)  # preserve order
    t0 = time.time()

    sema = asyncio.Semaphore(args.workers)
    log_lock = asyncio.Lock()
    completed_counter = {"n": 0}

    async def run_with_sema(idx: int, g: dict) -> None:
        async with sema:
            rec = await run_one(g, no_judge=args.no_judge)
        results[idx] = rec
        async with log_lock:
            completed_counter["n"] += 1
            i = completed_counter["n"]
            ok_factual = "✓" if rec.factual_match else "✗"
            ok_cite = "✓" if rec.citation_present else " "
            print(f"[{i:>3}/{len(gold)}] {ok_factual} {ok_cite} [{rec.judge_score:.2f}] "
                  f"{rec.question[:60]:<60} | {rec.judge_reason[:60]}",
                  flush=True)

    await asyncio.gather(*(run_with_sema(i, g) for i, g in enumerate(gold)))
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
