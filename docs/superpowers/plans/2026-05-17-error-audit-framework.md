# Error / Risk Audit Framework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single runnable, self-verifying `audit/` framework that mechanically catches every known error/risk class and tests all functionality before commit/deploy.

**Architecture:** Modular Python package `audit/`. One pure function per check, decorated with `@register(id, tier, title)`, returning a `Result(status, evidence, remediation)`. A runner selects tiers, prints a table, exits non-zero on any FAIL. A `--selftest` mode proves every check fails on a deliberately-broken fixture (so the auditor can't be silently broken). Read-only against production.

**Tech Stack:** Python 3.11 stdlib only (subprocess/pathlib/ast/json/urllib), the repo's `.venv` pytest, `ruff`, `npm`, the existing playwright-skill for E2E.

**Spec:** `docs/superpowers/specs/2026-05-17-error-audit-framework-design.md`

---

## File Structure

| File | Responsibility |
| --- | --- |
| `audit/__init__.py` | empty package marker |
| `audit/core.py` | `Status`, `Result`, `Check`, `register`, `CHECKS`, `git()`/`sh()` helpers, `run()`, `selftest()`, tier map, `REPO` |
| `audit/__main__.py` | CLI: arg parse → `run()`/`selftest()`; exit code |
| `audit/tier1_repo.py` | T1.1–T1.5 (repo integrity) |
| `audit/tier2_code.py` | T2.1–T2.6 (code soundness) |
| `audit/tier3_build.py` | T3.1–T3.3 (build & test gates) |
| `audit/tier4_functional.py` | T4-smoke + T4-e2e |
| `audit/tier5_deploy.py` | T5.1–T5.4 (deploy safety, read-only) |
| `audit/selftest_fixtures.py` | one broken-input factory per check id |
| `tools/audit` | thin executable entrypoint (`exec .venv/bin/python -m audit "$@"`) |
| `.githooks/pre-commit` | optional: `tools/audit --static` |
| `.githooks/pre-push` | optional: `tools/audit --build` + T5.1/T5.2 |
| `tests/test_audit_selftest.py` | runs `audit.selftest()` so the auditor is in the 215-green pytest gate |
| `audit/README.md` | usage |

Checks are grouped one module per tier (files that change together live together). Each check is independently testable via its selftest fixture.

---

## Task 1: Core contract + runner + CLI

**Files:**
- Create: `audit/__init__.py`, `audit/core.py`, `audit/__main__.py`, `tools/audit`
- Test: `tests/test_audit_selftest.py` (created here, expanded in Task 7)

- [ ] **Step 1: Write the failing test** — `tests/test_audit_selftest.py`

```python
import subprocess, sys, pathlib
REPO = pathlib.Path(__file__).resolve().parent.parent

def test_core_runner_passes_a_trivial_pass_check():
    # audit.core.run with only a dummy PASS check exits 0
    code = subprocess.run(
        [sys.executable, "-c",
         "import audit.core as c; "
         "c.CHECKS.clear(); "
         "c.register('X.1','static','dummy')(lambda: c.Result('X.1', c.Status.PASS, 'ok')); "
         "import sys; sys.exit(c.run({'static'}))"],
        cwd=REPO).returncode
    assert code == 0

def test_core_runner_fails_on_a_fail_check():
    code = subprocess.run(
        [sys.executable, "-c",
         "import audit.core as c; "
         "c.CHECKS.clear(); "
         "c.register('X.2','static','dummy')(lambda: c.Result('X.2', c.Status.FAIL, 'bad')); "
         "import sys; sys.exit(c.run({'static'}))"],
        cwd=REPO).returncode
    assert code == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_audit_selftest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'audit'`

- [ ] **Step 3: Write `audit/__init__.py`** — empty file.

- [ ] **Step 4: Write `audit/core.py`**

```python
"""Audit framework core: check contract, registry, runner, selftest."""
from __future__ import annotations
import dataclasses, enum, json, pathlib, subprocess, sys
from typing import Callable

REPO = pathlib.Path(__file__).resolve().parent.parent


class Status(enum.Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclasses.dataclass
class Result:
    check_id: str
    status: Status
    evidence: str
    remediation: str = ""


@dataclasses.dataclass
class Check:
    id: str
    tier: str            # static | build | functional | deploy
    title: str
    fn: Callable[[], Result]


CHECKS: list[Check] = []


def register(id: str, tier: str, title: str):
    def deco(fn: Callable[[], Result]) -> Callable[[], Result]:
        CHECKS.append(Check(id, tier, title, fn))
        return fn
    return deco


TIER_SETS = {
    "static": {"static"},
    "build": {"static", "build"},
    "functional": {"static", "build", "functional"},
    "deploy": {"deploy"},
    "all": {"static", "build", "functional", "deploy"},
}


def sh(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)


def git(*args: str) -> str:
    return sh(["git", *args]).stdout.strip()


def _load_all_checks() -> None:
    from audit import (tier1_repo, tier2_code, tier3_build,  # noqa: F401
                        tier4_functional, tier5_deploy)


def run(selected: set[str], as_json: bool = False) -> int:
    if not CHECKS:
        _load_all_checks()
    rows = []
    for c in sorted(CHECKS, key=lambda c: c.id):
        if c.tier not in selected:
            continue
        try:
            r = c.fn()
        except Exception as e:  # a broken check is a FAIL, never silent
            r = Result(c.id, Status.FAIL, f"check raised {type(e).__name__}: {e}",
                       "fix the check or the underlying issue")
        rows.append((c, r))
    fails = [r for _, r in rows if r.status is Status.FAIL]
    if as_json:
        print(json.dumps([{"id": c.id, "status": r.status.value,
                            "evidence": r.evidence, "remediation": r.remediation}
                           for c, r in rows], indent=2))
    else:
        for c, r in rows:
            mark = {"PASS": "✓", "WARN": "▲", "FAIL": "✗", "SKIP": "·"}[r.status.value]
            print(f"  {mark} [{c.id}] {c.title}: {r.status.value}")
            if r.status in (Status.FAIL, Status.WARN):
                print(f"      {r.evidence}")
                if r.remediation:
                    print(f"      fix: {r.remediation}")
        n = len(rows)
        print(f"\n  {n} checks · "
              f"{sum(1 for _,r in rows if r.status is Status.PASS)} pass · "
              f"{sum(1 for _,r in rows if r.status is Status.WARN)} warn · "
              f"{len(fails)} fail · "
              f"{sum(1 for _,r in rows if r.status is Status.SKIP)} skip")
    return 1 if fails else 0


def selftest() -> int:
    """Every check must FAIL on its deliberately-broken fixture."""
    from audit.selftest_fixtures import FIXTURES
    if not CHECKS:
        _load_all_checks()
    bad = []
    for c in CHECKS:
        fx = FIXTURES.get(c.id)
        if fx is None:
            bad.append(f"{c.id}: NO selftest fixture")
            continue
        with fx() as broken_ctx:
            r = c.fn()
        if r.status is not Status.FAIL:
            bad.append(f"{c.id}: expected FAIL on broken fixture, got {r.status.value}")
    for b in bad:
        print(f"  ✗ {b}")
    print(f"\n  selftest: {len(CHECKS)} checks · {len(bad)} not self-verifying")
    return 1 if bad else 0
```

- [ ] **Step 5: Write `audit/__main__.py`**

```python
import argparse, sys
from audit import core

def main() -> int:
    p = argparse.ArgumentParser(prog="python -m audit")
    g = p.add_mutually_exclusive_group()
    for t in ("static", "build", "functional", "deploy", "all"):
        g.add_argument(f"--{t}", action="store_const", const=t, dest="tier")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    if a.selftest:
        return core.selftest()
    return core.run(core.TIER_SETS[a.tier or "all"], as_json=a.json)

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Write `tools/audit`**

```bash
#!/usr/bin/env bash
# Entrypoint for the audit framework. Usage: tools/audit [--static|--build|--functional|--deploy|--all|--selftest] [--json]
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m audit "$@"
```

Then: `chmod +x tools/audit`

- [ ] **Step 7: Create empty stub modules so `_load_all_checks` imports cleanly**

Create `audit/tier1_repo.py`, `audit/tier2_code.py`, `audit/tier3_build.py`, `audit/tier4_functional.py`, `audit/tier5_deploy.py`, `audit/selftest_fixtures.py` each containing only:

```python
# filled in a later task
```

And in `audit/selftest_fixtures.py`:

```python
FIXTURES: dict = {}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_audit_selftest.py -q`
Expected: PASS (2 passed)

- [ ] **Step 9: Commit**

```bash
git add audit/ tools/audit tests/test_audit_selftest.py
git commit -m "feat(audit): core contract + runner + CLI"
```

---

## Task 2: Tier 1 — Repo integrity (T1.1–T1.5)

**Files:**
- Modify: `audit/tier1_repo.py`, `audit/selftest_fixtures.py`

- [ ] **Step 1: Write `audit/tier1_repo.py`**

```python
"""Tier 1 — repo integrity (pre-commit, fast)."""
from __future__ import annotations
import re
from audit.core import register, Result, Status, REPO, git, sh


@register("T1.1", "static", "no tracked symlinks")
def t1_1() -> Result:
    out = git("ls-files", "-s")
    syms = [ln.split("\t", 1)[1] for ln in out.splitlines() if ln.startswith("120000")]
    if syms:
        return Result("T1.1", Status.FAIL, f"tracked symlinks: {syms}",
                       "git rm --cached <path>; add to .gitignore (no trailing slash)")
    return Result("T1.1", Status.PASS, "no tracked symlinks")


@register("T1.2", "static", "LFS coverage for binary/large files")
def t1_2() -> Result:
    ga = (REPO / ".gitattributes").read_text(encoding="utf-8", errors="replace")
    lfs_globs = [ln.split()[0] for ln in ga.splitlines()
                 if "filter=lfs" in ln and ln.strip() and not ln.startswith("#")]
    lfs_files = set(sh(["git", "lfs", "ls-files", "-n"]).stdout.split())
    bad = []
    for ln in git("ls-files", "-s").splitlines():
        mode, _, _, path = ln.replace("\t", " ").split(maxsplit=3)
        if mode == "120000":
            continue
        blob_sz = sh(["git", "cat-file", "-s", ln.split()[1]]).stdout.strip()
        is_lfs = path in lfs_files
        big = blob_sz.isdigit() and int(blob_sz) > 512 * 1024
        if big and not is_lfs:
            bad.append(f"{path} ({int(blob_sz)//1024} KB) not LFS")
    if bad:
        return Result("T1.2", Status.FAIL, "; ".join(bad[:8]),
                       "add a filter=lfs rule to .gitattributes; git rm --cached + re-add the files")
    return Result("T1.2", Status.PASS, f"{len(lfs_files)} LFS files; no oversized non-LFS blobs")


@register("T1.3", "static", "no real secrets tracked")
def t1_3() -> Result:
    KEYISH = re.compile(r"(hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_\-]{20,}|xox[bp]-[A-Za-z0-9-]{20,})")
    suspects = []
    for path in git("ls-files").splitlines():
        base = path.rsplit("/", 1)[-1]
        if base == ".env" or (base.startswith(".env") and not base.endswith((".example", ".sample"))):
            suspects.append(f"{path} (real dotenv tracked)")
            continue
        if path.endswith((".png", ".jpg", ".pdf", ".duckdb", ".bin", ".ico", ".woff", ".woff2", ".ttf")):
            continue
        try:
            txt = (REPO / path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if KEYISH.search(txt) and "example" not in path and "ADR-010" not in path:
            suspects.append(f"{path} (key-shaped string)")
    if suspects:
        return Result("T1.3", Status.FAIL, "; ".join(suspects[:8]),
                       "remove from index + history; rotate the key; gitignore the file")
    return Result("T1.3", Status.PASS, "no real .env / key material tracked")


@register("T1.4", "static", ".gitignore robust for file AND dir")
def t1_4() -> Result:
    intents = ["tools/.pdf_text_cache", "rag/corpus", "rag/extracted", "rag/vectors"]
    gi = (REPO / ".gitignore").read_text(encoding="utf-8", errors="replace").splitlines()
    missing = []
    for it in intents:
        # a slash-less line ignores BOTH the file and the dir form
        if it not in gi:
            missing.append(it)
    if missing:
        return Result("T1.4", Status.FAIL, f"only dir-form (or absent) ignore for: {missing}",
                       "add a slash-less line per intent so a symlink/file of that name is also ignored")
    return Result("T1.4", Status.PASS, "file+dir ignore intents present")


@register("T1.5", "static", "no junk/build artifacts tracked")
def t1_5() -> Result:
    JUNK = ("tools/.pdf_text_cache/", ".pytest_cache/", ".DS_Store",
            "frontend/out/", "frontend/.next/", "node_modules/", ".tsbuildinfo")
    tracked = git("ls-files").splitlines()
    hits = [p for p in tracked if any(j in p for j in JUNK)]
    if hits:
        return Result("T1.5", Status.FAIL, f"{len(hits)} junk paths e.g. {hits[:5]}",
                       "git rm -r --cached <path> and gitignore it")
    return Result("T1.5", Status.PASS, "no caches/build artifacts tracked")
```

- [ ] **Step 2: Add the 5 selftest fixtures** — append to `audit/selftest_fixtures.py`

```python
import contextlib, pathlib, subprocess
from audit.core import REPO

@contextlib.contextmanager
def _staged_symlink():
    p = REPO / "_audit_selftest_link"
    p.symlink_to("/nonexistent")
    subprocess.run(["git", "add", "-f", str(p)], cwd=REPO)
    try:
        yield
    finally:
        subprocess.run(["git", "rm", "-f", "--cached", "-q", str(p)], cwd=REPO)
        p.unlink(missing_ok=True)

# (Analogous tiny context managers for T1.2..T1.5: stage an oversized non-LFS
# blob; a file containing 'hf_' + 40 chars; remove the rag/corpus gitignore
# line in a temp copy; stage a .DS_Store. Each yields a broken repo state and
# restores it. Full code written in this step — no placeholders.)
FIXTURES.update({
    "T1.1": _staged_symlink,
    # "T1.2": _staged_big_blob, "T1.3": _staged_fake_key,
    # "T1.4": _broken_gitignore, "T1.5": _staged_dsstore
})
```

> Implementer note: write the four remaining context managers in full here using the same `@contextlib.contextmanager` + git add/rm + restore pattern as `_staged_symlink`. They are 6–10 lines each; do not abbreviate in the actual code.

- [ ] **Step 3: Verify Tier 1 runs**

Run: `.venv/bin/python -m audit --static`
Expected: T1.1–T1.5 all `✓ PASS` on the current clean tree.

- [ ] **Step 4: Verify selftest catches breakage**

Run: `.venv/bin/python -m audit --selftest 2>&1 | grep -E 'T1\.'`
Expected: no `T1.x: ...not self-verifying` lines (each FAILs on its broken fixture).

- [ ] **Step 5: Commit**

```bash
git add audit/tier1_repo.py audit/selftest_fixtures.py
git commit -m "feat(audit): Tier 1 repo-integrity checks + selftest fixtures"
```

---

## Task 3: Tier 2 — Code soundness (T2.1–T2.6)

**Files:** Modify `audit/tier2_code.py`, `audit/selftest_fixtures.py`

- [ ] **Step 1: Write `audit/tier2_code.py`** (full code)

```python
"""Tier 2 — code soundness (pre-commit)."""
from __future__ import annotations
import ast, re, subprocess, sys
from audit.core import register, Result, Status, REPO, git, sh

PY = [p for p in git("ls-files").splitlines() if p.endswith(".py")]
DEAD = ("backend.orchestrator", "import sales_brain", "qa_brain", "faithfulness",
        "backend.translator", "profile_extractor", "get_judge_llm", "get_fast_brain_llm")


@register("T2.1", "static", "all .py parse (AST)")
def t2_1() -> Result:
    bad = []
    for p in PY:
        try:
            ast.parse((REPO / p).read_text(encoding="utf-8", errors="replace"), p)
        except SyntaxError as e:
            bad.append(f"{p}: {e}")
    return (Result("T2.1", Status.FAIL, "; ".join(bad[:5]), "fix the syntax error")
            if bad else Result("T2.1", Status.PASS, f"{len(PY)} files parse"))


@register("T2.2", "static", "runtime-import every backend/rag module")
def t2_2() -> Result:
    mods = []
    for p in PY:
        if (p.startswith("backend/") or p.startswith("rag/")) and not p.endswith("__init__.py") \
           and "_smoke_test" not in p and "/tests/" not in p:
            mods.append(p[:-3].replace("/", "."))
    code = "import importlib,sys\nbad=[]\n" + \
           "".join(f"try:\n importlib.import_module({m!r})\nexcept Exception as e:\n bad.append(({m!r},repr(e)))\n"
                   for m in mods) + "print(bad)\nsys.exit(1 if bad else 0)"
    r = sh([".venv/bin/python", "-c", code], timeout=300)
    if r.returncode != 0:
        return Result("T2.2", Status.FAIL, r.stdout.strip()[:600],
                      "fix the import (often: import wrongly placed inside a docstring)")
    return Result("T2.2", Status.PASS, f"{len(mods)} modules import clean")


@register("T2.3", "static", "no refs to deleted modules/symbols")
def t2_3() -> Result:
    code_hits, doc_hits = [], []
    for p in PY:
        for i, ln in enumerate(open(REPO / p, encoding="utf-8", errors="replace"), 1):
            for d in DEAD:
                if d in ln:
                    s = ln.strip()
                    (doc_hits if s.startswith("#") or s.startswith(('"', "'", "*")) else code_hits
                     ).append(f"{p}:{i} {d}")
    if code_hits:
        return Result("T2.3", Status.FAIL, "; ".join(code_hits[:6]),
                      "remove/replace the dead reference (e.g. get_judge_llm → get_brain_llm)")
    if doc_hits:
        return Result("T2.3", Status.WARN, f"{len(doc_hits)} stale comment refs e.g. {doc_hits[:3]}",
                      "tidy the stale comment")
    return Result("T2.3", Status.PASS, "no dead-symbol references")


@register("T2.4", "static", "no */ inside CSS/JS block-comment body")
def t2_4() -> Result:
    bad = []
    for p in git("ls-files").splitlines():
        if not p.endswith((".css", ".scss")):
            continue
        txt = (REPO / p).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"/\*.*?\*/", txt, re.S):
            body = m.group(0)[2:-2]
            if "*/" in body:
                bad.append(f"{p}: nested */ in comment")
    return (Result("T2.4", Status.FAIL, "; ".join(bad), "space the token: '* /' or reword")
            if bad else Result("T2.4", Status.PASS, "no comment-terminator footgun"))


@register("T2.5", "static", "no hardcoded 40-data path construction")
def t2_5() -> Result:
    pat = re.compile(r'/\s*["\']40-data["\']')
    bad = []
    for p in PY:
        if not (p.startswith("backend/") or p.startswith("rag/")):
            continue
        if p.endswith("config.py"):
            continue
        for i, ln in enumerate(open(REPO / p, encoding="utf-8", errors="replace"), 1):
            if pat.search(ln) and not ln.strip().startswith("#"):
                bad.append(f"{p}:{i}")
    return (Result("T2.5", Status.FAIL, "; ".join(bad[:8]), "use settings.DATA_DIR")
            if bad else Result("T2.5", Status.PASS, "DATA_DIR centralized"))


@register("T2.6", "static", "ruff + tsc clean")
def t2_6() -> Result:
    ruff = sh([".venv/bin/ruff", "check", "backend", "rag", "audit"], timeout=120)
    tsc = sh(["npx", "--prefix", "frontend", "--no-install", "tsc", "-p", "frontend", "--noEmit"], timeout=240)
    probs = []
    if ruff.returncode not in (0, 127):
        probs.append("ruff: " + (ruff.stdout or ruff.stderr).strip().splitlines()[-1][:200])
    if tsc.returncode not in (0, 127):
        probs.append("tsc: " + (tsc.stdout or tsc.stderr).strip().splitlines()[-1][:200])
    if 127 in (ruff.returncode, tsc.returncode):
        return Result("T2.6", Status.SKIP, "ruff/tsc not installed", "pip install ruff / npm i")
    return (Result("T2.6", Status.FAIL, " | ".join(probs), "fix lint/type errors")
            if probs else Result("T2.6", Status.PASS, "ruff + tsc clean"))
```

- [ ] **Step 2: Add T2.1–T2.6 selftest fixtures** to `audit/selftest_fixtures.py` — each a context manager that creates the broken condition (a temp `.py` with a SyntaxError; a temp module with an import inside its docstring; a temp file referencing `get_judge_llm`; a temp `.css` with `/* a */ b */`; a temp backend file with `/ "40-data" /`; monkeypatch ruff to fail). Full code, same pattern as Task 2; register all six in `FIXTURES`.

- [ ] **Step 3: Verify** — `.venv/bin/python -m audit --static` → T2.* PASS on clean tree.
- [ ] **Step 4: Selftest** — `.venv/bin/python -m audit --selftest` → no T2 self-verify gaps.
- [ ] **Step 5: Commit** — `git add audit/tier2_code.py audit/selftest_fixtures.py && git commit -m "feat(audit): Tier 2 code-soundness checks"`

---

## Task 4: Tier 3 — Build & test gates (T3.1–T3.3)

**Files:** Modify `audit/tier3_build.py`, `audit/selftest_fixtures.py`

- [ ] **Step 1: Write `audit/tier3_build.py`** (full code)

```python
"""Tier 3 — build & test gates."""
from __future__ import annotations
import re, urllib.request
from audit.core import register, Result, Status, sh


@register("T3.1", "build", "pytest green (clean-clone scoping)")
def t3_1() -> Result:
    r = sh([".venv/bin/python", "-m", "pytest", "-q"], timeout=600)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else ""
    if r.returncode != 0 or "error" in tail.lower() or "failed" in tail.lower():
        return Result("T3.1", Status.FAIL, tail[:200], "fix failing tests / collection error")
    m = re.search(r"(\d+) passed", tail)
    return Result("T3.1", Status.PASS, f"{m.group(0) if m else 'green'}")


@register("T3.2", "build", "next build (production static export)")
def t3_2() -> Result:
    r = sh(["npm", "--prefix", "frontend", "run", "build"], timeout=900)
    htmls = sh(["bash", "-lc", "ls frontend/out/*.html 2>/dev/null | wc -l"]).stdout.strip()
    if r.returncode != 0:
        return Result("T3.2", Status.FAIL,
                      (r.stdout + r.stderr).strip().splitlines()[-1][:200],
                      "fix the production build error")
    if htmls in ("", "0"):
        return Result("T3.2", Status.FAIL, "no static export emitted",
                      "ensure next.config has output:'export'")
    return Result("T3.2", Status.PASS, f"build ok, {htmls} html exported")


@register("T3.3", "build", "backend boots + /api/health (local)")
def t3_3() -> Result:
    # backend already runs locally on :8000 during dev; verify it imports + health
    imp = sh([".venv/bin/python", "-c", "import backend.main"], timeout=120)
    if imp.returncode != 0:
        return Result("T3.3", Status.FAIL, imp.stderr.strip()[:200], "fix backend import")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=8) as f:
            body = f.read(200).decode()
        ok = '"status":"ok"' in body
        return (Result("T3.3", Status.PASS, "backend imports + /api/health ok") if ok
                else Result("T3.3", Status.FAIL, body[:120], "backend unhealthy"))
    except Exception:
        return Result("T3.3", Status.SKIP, "no local backend on :8000",
                      "start uvicorn backend.main:app --port 8000 to run this check")
```

- [ ] **Step 2: Fixtures** for T3.1–T3.3 (a temp failing test file; force `next build` failure via a temp broken `.tsx` import; monkeypatch the health URL to an unreachable port → must FAIL not SKIP when import itself broken). Full code; register in `FIXTURES`.
- [ ] **Step 3: Verify** — `.venv/bin/python -m audit --build` (T3 may take minutes).
- [ ] **Step 4: Selftest** — no T3 self-verify gaps.
- [ ] **Step 5: Commit** — `git add audit/tier3_build.py audit/selftest_fixtures.py && git commit -m "feat(audit): Tier 3 build/test gates"`

---

## Task 5: Tier 4 — Functionality, both sub-tiers (T4-smoke, T4-e2e)

**Files:** Modify `audit/tier4_functional.py`, `audit/selftest_fixtures.py`
Create: `audit/e2e/insurancebot_e2e.js` (Playwright journeys, driven via the playwright-skill runner)

- [ ] **Step 1: Write `audit/tier4_functional.py`** — `T4.SMOKE` (one Result per endpoint group; each does a single local HTTP call to :8000 and asserts shape/sane counts: health, version, coverage≈148/20/~7300±5%, chat one turn returns non-error reply, upload-policy accepts a real corpus PDF → chunks_added>0 and rejects 100 bytes of junk, profile, scorecard, session/clear) and `T4.E2E` (shells the playwright-skill runner on `audit/e2e/insurancebot_e2e.js`, parses its `RJSON`, asserts: chat→inline cards, marketplace renders, compare modal opens, profile→premium recompute, voice copy touch-vs-desktop, PDF-upload UI ack, no console errors, no 390px overflow). Both return `SKIP` (not PASS) when the local backend / Playwright is unavailable. Full code following the Tier-3 pattern.

- [ ] **Step 2: Write `audit/e2e/insurancebot_e2e.js`** — a single Playwright script (headless) running the enumerated journeys, emitting one `RJSON {...}` line of booleans, modelled on the verified `/tmp/pw-3-multiturn.js` from the recovery session (reuse its multi-turn fact-find→recommendation logic).
- [ ] **Step 3: Fixtures** — T4.SMOKE fixture monkeypatches the base URL to a stub returning wrong coverage counts (must FAIL); T4.E2E fixture feeds a canned `RJSON` with `cardsRenderInlineOnMobile:false` (must FAIL). Full code; register.
- [ ] **Step 4: Verify** — with local backend up: `.venv/bin/python -m audit --functional`.
- [ ] **Step 5: Selftest + Commit** — `git add audit/tier4_functional.py audit/e2e/ audit/selftest_fixtures.py && git commit -m "feat(audit): Tier 4 functionality (smoke + exhaustive E2E)"`

---

## Task 6: Tier 5 — Deploy safety, read-only (T5.1–T5.4)

**Files:** Modify `audit/tier5_deploy.py`, `audit/selftest_fixtures.py`

- [ ] **Step 1: Write `audit/tier5_deploy.py`** (full code)

```python
"""Tier 5 — deploy safety. READ-ONLY against production (GET + git ls-remote only)."""
from __future__ import annotations
import json, urllib.request
from audit.core import register, Result, Status, REPO, git, sh

SPACE_API = "https://huggingface.co/api/spaces/rohitsar567/InsuranceBot"
LIVE = "https://rohitsar567-insurancebot.hf.space"


@register("T5.1", "deploy", "LFS pre-push validation (HF hook simulation)")
def t5_1() -> Result:
    ga = (REPO / ".gitattributes").read_text("utf-8", "replace")
    globs = [l.split()[0] for l in ga.splitlines() if "filter=lfs" in l and not l.startswith("#")]
    lfs = set(sh(["git", "lfs", "ls-files", "-n"]).stdout.split())
    import fnmatch
    bad = [p for p in git("ls-files").splitlines()
           if any(fnmatch.fnmatch(p, g) for g in globs) and p not in lfs]
    return (Result("T5.1", Status.FAIL, f"would be HF-rejected: {bad[:6]}",
                   "git rm --cached + re-add so they store as LFS pointers")
            if bad else Result("T5.1", Status.PASS, "all LFS-pattern files are pointers"))


@register("T5.2", "deploy", "Dockerfile coherence")
def t5_2() -> Result:
    df = (REPO / "Dockerfile").read_text("utf-8", "replace")
    missing = [m.split()[1] for m in df.splitlines()
               if m.strip().startswith("COPY ")
               and not (REPO / m.split()[1]).exists()]
    collide = [p for p in git("ls-files").splitlines()
               if p in ("rag/corpus", "rag/extracted", "rag/vectors")]
    if missing or collide:
        return Result("T5.2", Status.FAIL,
                      f"COPY missing: {missing}; hydration-collide: {collide}",
                      "fix COPY source / untrack rag/corpus|extracted|vectors")
    return Result("T5.2", Status.PASS, "COPY paths exist; no hydration collision")


@register("T5.3", "deploy", "post-deploy guarded sha + live smoke")
def t5_3() -> Result:
    head = git("rev-parse", "HEAD")[:12]
    try:
        rt = json.load(urllib.request.urlopen(SPACE_API, timeout=20))["runtime"]
        sha = (rt.get("sha") or "")[:12]
        if rt.get("stage") != "RUNNING":
            return Result("T5.3", Status.WARN, f"Space stage={rt.get('stage')} sha={sha}",
                          "wait for build / check HF build log")
        if sha != head:
            return Result("T5.3", Status.WARN,
                          f"live sha {sha} != local HEAD {head} (not yet deployed?)",
                          "push + verify; never trust 'RUNNING' alone (LFS silent-failure)")
        h = urllib.request.urlopen(f"{LIVE}/api/health", timeout=20).read(200).decode()
        logo = urllib.request.urlopen(f"{LIVE}/insurer-logos/oriental-insurance.png", timeout=20)
        ok = '"status":"ok"' in h and logo.headers.get_content_type() == "image/png"
        return (Result("T5.3", Status.PASS, f"sha {sha} live, health ok, LFS logo image/png")
                if ok else Result("T5.3", Status.FAIL, f"health/logo bad: {h[:80]}",
                                  "investigate live deploy"))
    except Exception as e:
        return Result("T5.3", Status.SKIP, f"no network/API: {e}", "run with network to verify deploy")


@register("T5.4", "deploy", "standing tripwires (bloat/disk/stale-docs)")
def t5_4() -> Result:
    warns = []
    bloat = sh(["bash", "-lc",
                "find rag -name link_lists.bin -size +200M 2>/dev/null; "
                "du -sm rag/_hf_dataset_backup 2>/dev/null | awk '$1>20000{print}'"]).stdout.strip()
    if bloat:
        warns.append(f"chroma/backup bloat: {bloat}")
    free = sh(["bash", "-lc", "df -m . | tail -1 | awk '{print $4}'"]).stdout.strip()
    if free.isdigit() and int(free) < 2000:
        warns.append(f"low disk: {free} MB free")
    stale = sh(["bash", "-lc",
                "grep -rl 'Status | Live' 70-docs 2>/dev/null | xargs -r grep -l 'orchestrator.py' 2>/dev/null"]).stdout.strip()
    if stale:
        warns.append(f"stale present-state docs: {stale.splitlines()[:3]}")
    return (Result("T5.4", Status.WARN, " | ".join(warns), "address the flagged tripwire")
            if warns else Result("T5.4", Status.PASS, "no bloat/disk/stale-doc tripwire"))
```

- [ ] **Step 2: Fixtures** — T5.1 stage a non-LFS file matching an LFS glob; T5.2 add a bogus `COPY nonexistent ./x` to a temp Dockerfile copy; T5.3 monkeypatch SPACE_API to a stub with mismatched sha (must WARN→treat as FAIL in selftest by asserting status≠PASS — fixture asserts not-PASS); T5.4 create an oversized dummy file. Full code; register. (Note: for WARN-only checks, the selftest asserts `status is not PASS` rather than strictly FAIL — add a `selftest_expect` field to `Check`/`register` defaulting to `FAIL`, set `WARN` for T2.3/T5.4/T5.3; update `core.selftest()` to honor it. Make this small core change here.)
- [ ] **Step 3: Verify** — `.venv/bin/python -m audit --deploy`.
- [ ] **Step 4: Selftest + Commit** — `git add audit/tier5_deploy.py audit/core.py audit/selftest_fixtures.py && git commit -m "feat(audit): Tier 5 deploy-safety (read-only) + WARN selftest expectation"`

---

## Task 7: Self-test wiring, git hooks, README, full run

**Files:** Modify `tests/test_audit_selftest.py`; Create `.githooks/pre-commit`, `.githooks/pre-push`, `audit/README.md`

- [ ] **Step 1: Extend `tests/test_audit_selftest.py`**

```python
def test_every_check_is_self_verifying():
    import audit.core as c
    assert c.selftest() == 0, "some checks are not self-verifying (see stdout)"
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_audit_selftest.py -q` → PASS (now in the 215-gate; count becomes 216).
- [ ] **Step 3: Write `.githooks/pre-commit`**

```bash
#!/usr/bin/env bash
exec "$(git rev-parse --show-toplevel)/tools/audit" --static
```

`.githooks/pre-push`:

```bash
#!/usr/bin/env bash
root="$(git rev-parse --show-toplevel)"
"$root/tools/audit" --build || exit 1
"$root/.venv/bin/python" -m audit --deploy || exit 1
```

`chmod +x .githooks/*`. Document opt-in: `git config core.hooksPath .githooks`.

- [ ] **Step 4: Write `audit/README.md`** — usage table (tiers, when to run, exit codes, `--selftest`, hook opt-in).
- [ ] **Step 5: Full exhaustive run** — `tools/audit --all` (backend up). Capture the report; every check PASS or justified WARN/SKIP.
- [ ] **Step 6: Commit** — `git add tests/test_audit_selftest.py .githooks audit/README.md && git commit -m "feat(audit): selftest in pytest gate + git hooks + docs"`

---

## Self-Review

**Spec coverage:** T1.1–T1.5 → Task 2 · T2.1–T2.6 → Task 3 · T3.1–T3.3 → Task 4 · T4-smoke/T4-e2e → Task 5 · T5.1–T5.4 → Task 6 · self-verifying → Task 1 (`selftest`) + Task 7 (pytest wiring) · CLI/tiers/hooks → Task 1 + Task 7 · read-only-prod → Task 6 (GET/ls-remote only) · "test all functionalities" both sub-tiers → Task 5. No spec section unmapped.

**Placeholder scan:** Core/Tier1/Tier2/Tier3/Tier5 checks given as complete code. Tier-1 fixtures: `_staged_symlink` complete; the other four + Tier2/3/4/5 fixtures specified with exact pattern + explicit "write in full, do not abbreviate" instruction (they are mechanical 6–10-line repeats of the shown context-manager pattern — acceptable as a per-fixture instruction, not a logic placeholder). Tier-4 bodies are specified behaviourally with the exact endpoints/assertions + reuse of the verified `/tmp/pw-3-multiturn.js`; flagged as the one area to expand to full code during implementation.

**Type consistency:** `Result(check_id,status,evidence,remediation)`, `Status.{PASS,WARN,FAIL,SKIP}`, `register(id,tier,title)`, `CHECKS`, `TIER_SETS`, `git()/sh()`, `FIXTURES` dict — names identical across all tasks. Task 6 adds `selftest_expect` to `register`/`Check`/`selftest()` consistently in one place.

**Scope:** one cohesive framework, one repo, single plan. Good.

---

## Execution Handoff

(Provided after user reviews this plan.)
