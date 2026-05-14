# `80-audit/` — Audit output + production-readiness register

Two artefact classes coexist here:

1. **`ENTERPRISE_AUDIT.md`** — the **master defect log**. A living, hand-curated, severity-tagged register of every P0/P1/P2/P3 issue discovered during the readiness sprint, with evidence + fix status. Start here before any deploy decision.
2. **`<run_id>/` subdirectories** — per-run output of the multi-persona audit framework (`tools/audit/`). Each is a complete, immutable snapshot of one full or partial 100-persona pass.

## `ENTERPRISE_AUDIT.md`

| Section | What's there |
| --- | --- |
| Executive scorecard | One-line status per domain: disk stability, data pipeline, observability, accuracy, latency, profile capture, language fairness, code hygiene, test coverage, voice UX, fact-find tone, secrets. |
| Defect Register | D-001 … D-NNN. Each row: severity (P0–P3) · title · symptom · root cause · impact · fix status. |

Severity legend: **P0** blocks production deployment · **P1** blocks enterprise procurement · **P2** quality / hygiene · **P3** nice-to-have.

Status emoji: ✅ fixed · ⚠️ partial · 🟡 improving · 🔴 open.

## Run-directory layout

```
80-audit/
├── ENTERPRISE_AUDIT.md
└── <run_id>/
    ├── transcripts/
    │   ├── P001.json
    │   ├── P002.json
    │   ├── P003.partial.json     (in-flight or interrupted persona)
    │   └── …
    ├── report.md                 (analyze.py rollup)
    └── summary.json              (machine-readable rollup)
```

| Run-ID prefix | Meaning |
| --- | --- |
| `full_YYYYMMDD_HHMMSS` | Full 100-persona pass against the live HF Space. |
| `postfix_YYYYMMDD_HHMMSS` | Targeted re-run after shipping a specific fix, used to confirm the defect is gone. |

## Each persona transcript

`transcripts/P###.json` captures the 30-turn flow for one persona. Per turn:

| Field | Source |
| --- | --- |
| `user_text` | `tools/audit/flows.py` |
| `reply_text` | live API |
| `intent`, `brain_used` | `backend/orchestrator.py` |
| `citations` | `backend/main.py` response |
| `profile_updates` | `backend/profile_extractor.py` |
| `faithfulness_passed`, `blocked` | `backend/faithfulness.py` |
| `latency_ms` | wall-clock around the HTTP call |

`.partial.json` files indicate the audit was interrupted (rate-limit pile-up, deploy mid-run, etc.); the runner is resumable and will pick up from the next persona on restart.

## How runs flow into the register

1. `python tools/audit/run_audit.py` writes per-persona transcripts.
2. `python tools/audit/analyze.py 80-audit/<run_id>/` rolls up `report.md` + `summary.json`.
3. New defects → new `D-NNN` row in `ENTERPRISE_AUDIT.md`.
4. Fixes → status flipped, run re-executed as `postfix_…`, evidence linked.

## Related

- `tools/audit/README.md` — framework that produces the run dirs
- `tools/audit/personas.json`, `tools/audit/flows.json` — the deterministic inputs each run replays
- Root `CLAUDE.md` — high-level project state; defers detail to this file for change history
- [`kb/AUDIT_TRAIL.md`](../kb/AUDIT_TRAIL.md) — data-lineage doc that pairs with the behaviour-audit log
