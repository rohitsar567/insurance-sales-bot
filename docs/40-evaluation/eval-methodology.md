# 03 — Evaluation Plan

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Version | 0.1 |
| Date | 2026-05-13 |
| Depends on | `01-requirements.md` §6 (success criteria), `02-architecture.md` (system under test) |
| Status | Pipeline implemented; full run pending corpus completion |

## 0. Purpose

Evaluation is **how we prove the bot works**. Three artifacts:

1. **Gold Q&A set** — ground-truth question-answer-source triples
2. **Automated grader** — measures factual accuracy + citation accuracy + refusal precision per turn
3. **Results table** — versioned, audit-grade record of every eval run

These directly back the success criteria in Doc 01 §6: C2 (≥95% factual), C3 (≥95% citation), C4 (≥90% refusal precision).

## 1. Gold Q&A construction — three pipelines

### Pipeline A — auto-generated from structured extraction (the bulk)

For every successfully extracted policy in DuckDB, generate templated Q&A where the answer comes directly from the 62-field schema. Per `eval/generate_gold.py`:

- ~15 question templates × ~80 policies = ~1,100 candidate pairs
- Each pair is **fully reproducible** — the answer traces to a specific field which traces to a specific clause
- Auto-tagged: `question_type` ∈ {waiting_period, coverage_scope, exclusions, sub_limit, eligibility, claim, network, bonus}

**Why this scales:** adding the 11th, 12th, 80th policy adds zero new template work. Same generator, more rows.

### Pipeline B — LLM-drafted nuanced questions (curated)

For top-priority policies (top 10–20), run an LLM (Sarvam-M, with DeepSeek-V3 fallback) on the policy text with prompt: *"generate 5 buyer-style questions whose answers are explicitly in this document; include the source clause."* Each generated pair is **human spot-checked** before commit.

Target: 5 × 20 = 100 questions covering multi-clause and edge-case reasoning that Pipeline A can't generate.

### Pipeline C — hand-crafted adversarial set (refusal tests)

~30–40 hand-written questions across these classes:

- **Out-of-corpus** ("Does this cover space tourism?") → expect refusal
- **Out-of-policy-type** ("What is the IRDAI mandate on dental coverage?") → expect refusal (D-017)
- **Multi-policy compare** ("Compare cancer coverage in Star vs HDFC ERGO") → expect cited comparison
- **Hinglish** ("Cataract ke liye waiting period kya hai?") → expect Hindi answer, same factual accuracy
- **Code-switched** ("policy mein maternity cover hai kya?") → expect Hinglish answer

Each pair marked with `expected_refusal: bool`. Refusal cases test that the bot doesn't hallucinate when grounding fails.

## 2. Grader design

**Single grading endpoint:** `eval/run.py` calls `backend.orchestrator.handle_turn` in-process, takes the reply, scores against gold with three signals:

### Signal 1 — Regex hard-checks (deterministic)

Numbers, dates, currency, durations, percentages are extracted via regex from both gold and bot reply. Exact match (after normalization) is the strongest signal. Catches "the premium is ₹15,000" hallucinations without an LLM.

### Signal 2 — LLM-judge faithfulness (Groq Llama-3.3-70B)

Different model family from Sarvam-M (the brain) → **non-circular evaluation**. Judge prompt:

> Given GOLD and BOT answers, output strict JSON: `{factual_match: bool, citation_present: bool, score: 0-1, reason: str}`. Be strict on partial matches.

### Signal 3 — Citation regex check

`[Source: ...]` tag must be present in BOT for non-refusal questions. Caught by `re.search(r'\[Source:'` pattern.

**Final per-question score:** `factual_match AND (citation_present OR expected_refusal)`.

## 3. Metrics computed

| Metric | Doc 01 target | How |
| --- | --- | --- |
| Factual accuracy | C2 ≥ 95% | n_correct / n_total |
| Citation accuracy | C3 ≥ 95% | n_correct_citations / n_non_refusal |
| Refusal precision | C4 ≥ 90% | n_correct_refusals / n_expected_refusals |
| Hindi parity | C8 within 5pp | factual_acc(hi) vs factual_acc(en) |
| Brain winners | (router config) | factual_acc grouped by brain_used |

## 4. Output artifacts per run

- `eval/results.md` — human-readable summary with per-type, per-brain accuracy + sample misses
- `eval/results.json` — machine-readable full per-question record
- `logs/hallucinations.jsonl` — every blocked reply with its reason (audit log)

## 5. Run cadence

| Stage | Cadence | Implementation |
| --- | --- | --- |
| Development | Manual, after meaningful changes | `python -m eval.run` |
| Pre-deploy | Every PR | GitHub Actions runs eval, blocks merge if accuracy regresses |
| Production | Nightly synthetic + spot-grading | Cron via Render scheduled job; live-traffic sampling via Playwright |
| Post-deploy verification | Per deploy | `tests/live_verify.py` runs full eval against live Vercel URL |

## 6. Bilingual eval

Bilingual sub-set: 20 questions translated to Hindi + Hinglish via Sarvam-M with manual spot-check. Run separately. Hindi factual accuracy compared to English baseline — must be within 5pp per C8.

## 7. Known limitations (transparent)

- **Sample bias:** Pipeline A questions are templated, so accuracy on Pipeline A is upper-bound real-world performance. Pipeline B + C provide the harder signal.
- **Single judge model:** Groq Llama is one judge. Risk of judge-specific bias. v2: 3-judge consensus.
- **No human evaluation:** Cost-prohibitive for 1,100 questions. We rely on the grader; manual spot-check 5%.
- **No latency budget enforcement in eval:** Latency is captured per record but doesn't gate the score. Doc 01 C1 (p50 ≤ 4s) is monitored separately.
