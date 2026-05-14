# `tools/audit/` — Multi-persona conversational audit

End-to-end conversational stress test: walks 100 distinct personas through 30-turn flows against the live API, captures every turn's reply / latency / faithfulness verdict / blocked status, and rolls up to a defect-counting report.

This is the framework that surfaced the headline KI-018 (QA→fact-find misrouting) and KI-021 (latency p95 blow-out) defects in the readiness audit.

## Files

| File | Role |
| --- | --- |
| `run_audit.py` | Entry point. Walks each persona × 30 turns against the live `/api/chat`. Resumable (per-persona transcripts land as they finish). Concurrent (`--workers W`) but rate-aware — global NIM 40 req/min cap enforced via per-request sleep. Retries 5xx with exponential backoff. |
| `personas.py` | Generator: 10 archetypes × 10 demographic profiles × 1 deterministic style = **100 unique personas**. Stable order = stable persona IDs across runs, so diffs are regressions not shuffle noise. Run as a script to (re)generate `personas.json`. |
| `personas.json` | Materialised 100-persona list. Stable input to `run_audit.py`. |
| `flows.py` | Generator: per persona produces a 30-turn user-text sequence in 5 phases — opening (1) · fact-find answers (9) · free-form Qs (10) · edge-case probes (5) · adversarial + close (5). |
| `flows.json` | Materialised flows. `dict[persona_id, list[str]]` of the 30 turns each persona sends. |
| `analyze.py` | Post-run aggregator: reads `80-audit/<run_id>/transcripts/*.json`, computes per-archetype / per-language / per-style breakdowns of faithfulness, blocked rate, p95 latency. Emits `report.md` + `summary.json` into the run dir. |

## Output layout

```
80-audit/<run_id>/
├── transcripts/
│   ├── P001.json          (complete persona)
│   ├── P002.json
│   ├── P003.partial.json  (in-flight or interrupted)
│   └── …
├── report.md              (analyze.py output — defect breakdown)
└── summary.json           (machine-readable rollup)
```

`<run_id>` convention is `full_YYYYMMDD_HHMMSS` for the full 100-persona pass and `postfix_YYYYMMDD_HHMMSS` for a post-fix re-run targeting a specific defect.

## Typical run

```bash
# Full audit against the live HF Space
python tools/audit/run_audit.py --workers 4

# Smoke (5 personas) for a config change
python tools/audit/run_audit.py --max-personas 5 --base http://localhost:8000

# Aggregate after
python tools/audit/analyze.py 80-audit/full_20260514_145243/
```

## Watch-outs

- **HF Space rebuild is 5-8 min.** Don't start an audit until the desired image is stably deployed, or transcripts span multiple builds and become useless for A/B.
- **The 40 req/min NIM cap is global.** Bumping `--workers` past 4 will not help — the per-request sleep clamps dispatch rate.
- **Personas are stable by index.** If you change the `ARCHETYPES` / demographic lists, P037 is no longer the same person — call it out in the run notes.

## Related

- `80-audit/ENTERPRISE_AUDIT.md` — defect register fed by audit output
- `80-audit/README.md` — output-folder layout reference
- Root `CLAUDE.md` § Routing invariants — the KI-018 / KI-023 regressions the audit catches
