# Error / Risk Audit Framework — Design Spec

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Date | 2026-05-17 |
| Status | Draft — awaiting user spec review |
| Goal | One runnable, exhaustive framework that mechanically catches **every class of error/risk** before commit/deploy, and **tests all functionality**, so the failure cascade of the 2026-05-16/17 recovery session cannot recur silently. |

## 1. Purpose & non-goals

**Purpose:** a single command that, run before commit / before push / after deploy,
fails loudly on any known risk class with concrete evidence — replacing the
ad-hoc, reactive checking that let symlinks, un-LFS'd blobs, dead symbols, a
CSS-comment 500, and stale docs reach (or nearly reach) production.

**Non-goals (YAGNI):** not a CI server, not a replacement for `pytest` (it
*orchestrates* it), not a linter rewrite (it *invokes* `ruff`/`tsc`), no new
test framework. It is an orchestrator + a set of project-specific risk checks
that don't exist in any off-the-shelf tool.

## 2. Risk taxonomy — the checks (this is "all risks")

Every check is grounded in a real incident this session or a standing
memory-logged silent-failure risk.

### Tier 1 — Repo integrity (fast; pre-commit)
- **T1.1 no tracked symlinks** — `git ls-files -s` mode `120000` ⇒ FAIL (the `rag/corpus`/`rag/extracted` Docker-build killer).
- **T1.2 LFS coverage** — every path matching an LFS pattern in `.gitattributes` is an LFS pointer in the index; any tracked binary >512 KB not LFS ⇒ FAIL (the insurer-logos / HF pre-receive-hook rejection).
- **T1.3 no real secrets** — no tracked file is a real `.env`/key material (allow `*.example`, docs, scripts that *read* secrets); entropy/key-shape scan of staged content.
- **T1.4 .gitignore robustness** — for each ignore intent (caches, `rag/corpus`, `rag/extracted`, `rag/vectors`) assert both file and dir forms are ignored (trailing-slash gap that committed the symlinks).
- **T1.5 no junk committed** — `tools/.pdf_text_cache/`, `.pytest_cache/`, `.DS_Store`, `frontend/out/`, `.next/`, `node_modules/`, `*.tsbuildinfo` not tracked.

### Tier 2 — Code soundness (pre-commit)
- **T2.1 AST parse** — every `*.py` parses.
- **T2.2 runtime-import** — import **every** `backend/**` + `rag/**` module in a subprocess; any `ImportError`/`NameError` ⇒ FAIL (the import-injected-into-docstring class; AST is necessary-not-sufficient).
- **T2.3 dead-symbol scan** — no code reference to deleted modules/symbols: `orchestrator`, `sales_brain`, `qa_brain`, `faithfulness`, `translator`, `profile_extractor`, `get_judge_llm`, `get_fast_brain_llm` (comments/docstrings = WARN, code = FAIL).
- **T2.4 comment footgun** — `*/` inside a CSS/JS/C block-comment body ⇒ FAIL (the app-wide 500).
- **T2.5 path-literal regression** — no `"40-data"` path *construction* in `backend/**`/`rag/**` (must use `settings.DATA_DIR`); descriptive prose = WARN.
- **T2.6 lint/typecheck** — `ruff check` (py) + `tsc --noEmit` (frontend) clean.

### Tier 3 — Build & test gates
- **T3.1 pytest** — bare `pytest` (clean-clone scoping) green; record count, FAIL on any fail/error/collection-error.
- **T3.2 next build** — `npm run build` exit 0 + static export emitted (`frontend/out/*.html`).
- **T3.3 backend boot** — `uvicorn` app imports; a local instance answers `/api/health` ok.

### Tier 4 — Functionality (BOTH sub-tiers)
- **T4-smoke (fast):** each API endpoint touched once locally — `health, version, coverage` (counts sane vs expected ≈148/20/~7.3k), `chat` (one turn returns a reply), `upload-policy` (accept a real PDF → quarantined; reject a junk PDF), `profile`, `scorecard`, `session/clear`. Each Playwright surface loaded once @ desktop+390px (no console error, no horizontal overflow).
- **T4-e2e (exhaustive):** full Playwright journeys — fact-find → recommendation with **inline cards**; marketplace browse + filter; compare modal (≤4); profile→premium live recompute; voice copy correct on touch vs desktop; PDF upload UI → in-chat ack; session reset/recall; admin panel gated. Plus the existing `pytest` unit/contract suite (security gates, scoring, premium, recall, conversation logic) is the deterministic backbone.

### Tier 5 — Deploy safety (pre-push + post-deploy)
- **T5.1 LFS pre-push validation** — simulate HF's rule: any to-be-pushed file matching an LFS pattern that isn't a pointer ⇒ FAIL *before* the push (pre-empts the pre-receive-hook rejection).
- **T5.2 Dockerfile coherence** — every `COPY <src>` exists in the tree; dataset-hydration step won't collide (no tracked `rag/corpus|extracted|vectors`).
- **T5.3 post-deploy guarded verify** — HF runtime API `runtime.sha` **actually equals the pushed commit** (never trust "RUNNING"; the LFS-quota silent-failure rule); live smoke: `/api/health` ok, frontend 200, an insurer-logo asset returns real `image/png` (LFS materialized), `/api/coverage` counts sane.
- **T5.4 standing tripwires** — ChromaDB `link_lists.bin` / `_hf_dataset_backup` bloat, disk-free, quarantine-TTL sanity; stale-doc present-state scan (docs asserting the deleted architecture as current) = WARN.

## 3. Architecture

A modular Python package `audit/` in the repo:

```
audit/
  __main__.py        # CLI: python -m audit [--static|--build|--functional|--deploy|--all] [--json]
  core.py            # Check protocol, Result(PASS/WARN/FAIL, evidence), runner, report, exit code
  tier1_repo.py      # T1.* checks
  tier2_code.py      # T2.*
  tier3_build.py     # T3.*
  tier4_functional.py# T4-smoke + T4-e2e (drives the playwright-skill scripts)
  tier5_deploy.py    # T5.* (read-only; never mutates prod)
  selftest/          # one deliberately-broken fixture per check → proves the auditor itself works
tools/audit          # thin entrypoint: `tools/audit --all`
.githooks/pre-commit # optional: runs --static
.githooks/pre-push   # optional: runs --build + T5.1/T5.2
```

**Check contract (isolation principle):** every check is a function
`def check(ctx) -> Result` — pure, independent, returns `PASS|WARN|FAIL` +
human-readable evidence + the exact remediation. No check depends on another's
side effects. New risk class = one new function; nothing else changes.

**Runner:** executes selected tiers, prints a per-check table + a final
summary, exits non-zero iff any `FAIL` (WARN never fails the gate but is always
shown). `--json` for machine use / future CI.

**Tiers map to when:** `--static` = Tier 1+2 (pre-commit, seconds);
`--build` = +Tier 3; `--functional` = +Tier 4 (needs local backend + Playwright);
`--deploy` = Tier 5; `--all` = everything (exhaustive).

**Self-verifying:** `audit/selftest/` holds a deliberately-broken fixture for
each check; `python -m audit --selftest` asserts every check *fails on the
broken fixture* — so a silently-broken auditor (the deepest risk) is itself
caught.

## 4. Error handling

- A check that errors internally ⇒ reported `FAIL` (never silently skipped — silence-is-not-success).
- Tier 4/5 needing an unavailable resource (no local backend, no network) ⇒ explicit `SKIP` with reason in the report (never a false `PASS`), and `--all` records skips prominently.
- Read-only against production: Tier 5 only does HTTP GET + `git ls-remote`; never pushes/mutates.

## 5. Testing the framework

The `--selftest` suite (broken-fixture-per-check) is the framework's own test;
it runs in `--all`. Additionally a tiny `tests/test_audit_selftest.py` so the
auditor's integrity is itself in the 215-green `pytest` gate.

## 6. Out of scope (explicit)

CI/CD server config; auto-fixing (it reports + gives the exact remediation, it
does not mutate code); replacing pytest/ruff/tsc; auditing other repos.

## 7. Open items

None — Tier-4 resolved to **both** sub-tiers per user. Ready for plan.
