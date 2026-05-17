# `audit/` — the repo's self-verifying error/risk gate

One runnable command that asserts the repo is sound. **24 checks across 5 tiers.**
Every check traces to a real incident this project hit (LFS quota silent
failures, deleted-module import breakage, ChromaDB disk bloat, etc.) — the
auditor exists so those classes of failure cannot ship silently again.

The auditor itself is verified: `--selftest` reconstructs the broken state of
each incident in a temp fixture and asserts the matching check still flags it.
A check that no longer detects its incident is reported as "not self-verifying".

## CLI

```
tools/audit.sh --static|--build|--functional|--deploy|--all|--selftest [--json]
```

`tools/audit.sh` just execs `.venv/bin/python -m audit "$@"` from the repo root.
(Note: `tools/audit.sh` is the entrypoint — unrelated to the pre-existing
`tools/audit/` tool directory.)

`--json` emits the per-check results as a JSON array instead of the text
report (id / status / evidence / remediation).

## Tiers

| Flag | Runs | Checks | When to run |
|------|------|--------|-------------|
| `--static` | static | T1.* + T2.* (11) | **Pre-commit.** Seconds. Pure repo/AST/import/lint — no build, no network, no servers. |
| `--build` | static + build | + T3.* (14) | Before push. Runs the full `pytest` gate, `next build` (production static export), and boots the backend for `/api/health`. Minutes. |
| `--functional` | static + build + functional | + T4.* (20) | Needs a **local backend on :8000 and frontend** up. API smoke (health/coverage/chat/upload/profile) + Playwright E2E journeys. |
| `--deploy` | deploy only | T5.* (4) | **Read-only** prod/deploy safety: LFS pre-push simulation, Dockerfile coherence, deployed-SHA vs local, standing tripwires. |
| `--all` | static + build + functional + deploy | all 24 | Everything. Run with backend (and ideally frontend) up. |

## Exit codes

- Exit is **non-zero iff any check is `FAIL`**.
- `WARN` **never fails the gate** (it surfaces a soft / deferred condition,
  e.g. T5.3 deferred-deploy SHA mismatch or T2.3 a stale-doc note).
- `SKIP` (e.g. a prerequisite service is down) also does not fail the gate.

## `--selftest`

```
.venv/bin/python -m audit --selftest        # full: 24 checks
```

For every check, a fixture stands up the broken state from the original
incident; the check must return its `selftest_expect` status on that fixture
(`FAIL` for most; `WARN` for T5.3 / T5.4 which are advisory by design). Output:
`24 checks · 0 not self-verifying`. A non-zero "not self-verifying" count means
the auditor has silently rotted and is no longer catching its own incident.

The pytest gate (`tests/test_audit_selftest.py`) runs the selftest **scoped to
the static tier only** — `core.selftest(only_tiers={"static"})`. The
build/functional/deploy tiers are deliberately excluded from the unit gate:
T3.1's fixture shells `pytest`, which would recurse into that very test; T3.2
runs a multi-minute `next build`; Tier 4 needs a live backend. The full
24-check selftest is run via `tools/audit.sh --selftest` (manual / pre-push).

## Opt-in git hooks

`.githooks/` ships a `pre-commit` (`--static`) and a `pre-push`
(`--build` then `--deploy`). They are **opt-in** — enable per clone with:

```
git config core.hooksPath .githooks
```

This repo does not set that automatically; nothing changes your git config
until you run the command above.

## Tier 5 is read-only against prod

Every Tier 5 check is strictly read-only with respect to deployed
infrastructure: it inspects local artifacts (Dockerfile, LFS attributes,
tripwire state) and at most *reads* the deployed SHA for comparison. It never
pushes, deploys, mutates remote state, or touches the HF Space — so `--deploy`
and `--all` are safe to run at any time.
