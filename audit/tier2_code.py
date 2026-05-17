"""Tier 2 — code soundness (pre-commit)."""
from __future__ import annotations
import ast, re
from audit.core import register, Result, Status, REPO, git, sh

# DEAD = deleted modules/accessors, matched in MODULE/IMPORT/CALL form so we
# do NOT false-match live field-name substrings (e.g. faithfulness_passed is
# an active log field; the backend.faithfulness *module* is gone).
DEAD = (
    "backend.orchestrator", "import orchestrator",
    "import sales_brain", "from backend import sales_brain", "backend.sales_brain",
    "import qa_brain", "backend.qa_brain",
    "import faithfulness", "from backend import faithfulness", "backend.faithfulness",
    "import translator", "backend.translator",
    "import profile_extractor", "backend.profile_extractor",
    "get_judge_llm", "get_fast_brain_llm",
)


def _py() -> list[str]:
    """Re-queried per call (NOT a frozen import-time snapshot) so selftest
    fixtures that add a file are actually seen by the checks."""
    return [p for p in git("ls-files").splitlines() if p.endswith(".py")]


def _audit_self(p: str) -> bool:
    # the framework's own files contain the DEAD strings as detection DATA
    return p.startswith("audit/") or p == "tests/test_audit_selftest.py"


@register("T2.1", "static", "all .py parse (AST)")
def t2_1() -> Result:
    bad = []
    for p in _py():
        try:
            ast.parse((REPO / p).read_text(encoding="utf-8", errors="replace"), p)
        except SyntaxError as e:
            bad.append(f"{p}: {e}")
    return (Result("T2.1", Status.FAIL, "; ".join(bad[:5]), "fix the syntax error")
            if bad else Result("T2.1", Status.PASS, f"{len(_py())} files parse"))


@register("T2.2", "static", "runtime-import every backend/rag module")
def t2_2() -> Result:
    mods = []
    for p in _py():
        if (p.startswith("backend/") or p.startswith("rag/")) and not p.endswith("__init__.py") \
           and "_smoke_test" not in p and "/tests/" not in p:
            mods.append(p[:-3].replace("/", "."))
    code = "import importlib,sys\nbad=[]\n" + \
           "".join(f"try:\n importlib.import_module({m!r})\nexcept Exception as e:\n bad.append(({m!r},repr(e)))\n"
                   for m in mods) + "print(bad)\nsys.exit(1 if bad else 0)"
    r = sh([".venv/bin/python", "-c", code], timeout=300)
    if r.returncode != 0:
        return Result("T2.2", Status.FAIL, r.stdout.strip()[:600],
                      "fix the import (often: import wrongly placed inside a docstring, or a deleted symbol)")
    return Result("T2.2", Status.PASS, f"{len(mods)} modules import clean")


@register("T2.3", "static", "no refs to deleted modules/symbols")
def t2_3() -> Result:
    code_hits, doc_hits = [], []
    for p in _py():
        if _audit_self(p):
            continue
        for i, ln in enumerate((REPO / p).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for d in DEAD:
                if d in ln:
                    s = ln.strip()
                    (doc_hits if s.startswith(("#", '"', "'", "*")) else code_hits
                     ).append(f"{p}:{i} {d}")
                    break
    if code_hits:
        return Result("T2.3", Status.FAIL, "; ".join(code_hits[:6]),
                      "remove/replace the dead reference (e.g. get_fast_brain_llm -> get_brain_llm)")
    if doc_hits:
        return Result("T2.3", Status.WARN, f"{len(doc_hits)} stale comment refs e.g. {doc_hits[:3]}",
                      "tidy the stale comment")
    return Result("T2.3", Status.PASS, "no dead-symbol references")


@register("T2.4", "static", "no orphan */ (CSS comment-terminator footgun)")
def t2_4() -> Result:
    bad = []
    for p in git("ls-files").splitlines():
        if not p.endswith((".css", ".scss")):
            continue
        s = (REPO / p).read_text(encoding="utf-8", errors="replace")
        i, n, line = 0, len(s), 1
        in_comment = False
        in_str = ""  # "" | "'" | '"'
        while i < n:
            ch = s[i]
            nx = s[i + 1] if i + 1 < n else ""
            if ch == "\n":
                line += 1
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = ""
                i += 1
                continue
            if in_comment:
                if ch == "*" and nx == "/":
                    in_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if ch in ("'", '"'):
                in_str = ch
                i += 1
                continue
            if ch == "/" and nx == "*":
                in_comment = True
                i += 2
                continue
            if ch == "*" and nx == "/":
                # a */ outside any comment/string: an earlier stray */ closed
                # a comment prematurely (the exact app-wide-500 footgun).
                bad.append(f"{p}:{line} orphan '*/' (a stray '*/' earlier closed a comment early)")
                i += 2
                continue
            i += 1
    return (Result("T2.4", Status.FAIL, "; ".join(bad[:5]),
                   "a comment body contains '*/' (e.g. .snap-*/.rev-*) — space it '* /' or reword")
            if bad else Result("T2.4", Status.PASS, "no comment-terminator footgun"))


@register("T2.5", "static", "no hardcoded 40-data path construction")
def t2_5() -> Result:
    pat = re.compile(r'/\s*["\']40-data["\']')
    bad = []
    for p in _py():
        if _audit_self(p) or not (p.startswith("backend/") or p.startswith("rag/")):
            continue
        if p.endswith("config.py"):
            continue
        for i, ln in enumerate((REPO / p).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if pat.search(ln) and not ln.strip().startswith("#"):
                bad.append(f"{p}:{i}")
    return (Result("T2.5", Status.FAIL, "; ".join(bad[:8]), "use settings.DATA_DIR")
            if bad else Result("T2.5", Status.PASS, "DATA_DIR centralized"))


@register("T2.6", "static", "ruff + tsc clean")
def t2_6() -> Result:
    def _try(cmd, timeout):
        try:
            return sh(cmd, timeout=timeout)
        except FileNotFoundError:
            return None
    ruff = _try([".venv/bin/ruff", "check", "backend", "rag", "audit"], 120)
    tsc = _try(["npx", "--prefix", "frontend", "--no-install", "tsc", "-p", "frontend", "--noEmit"], 240)
    probs = []
    if ruff is not None and ruff.returncode not in (0, 127):
        t = (ruff.stdout or ruff.stderr).strip().splitlines()
        probs.append("ruff: " + (t[-1][:200] if t else "error"))
    if tsc is not None and tsc.returncode not in (0, 127):
        t = (tsc.stdout or tsc.stderr).strip().splitlines()
        probs.append("tsc: " + (t[-1][:200] if t else "error"))
    avail = [x for x in (ruff, tsc) if x is not None and x.returncode != 127]
    if not avail and not probs:
        return Result("T2.6", Status.SKIP, "ruff and tsc both unavailable",
                      "pip install ruff / npm i in frontend to enable this gate")
    return (Result("T2.6", Status.FAIL, " | ".join(probs), "fix lint/type errors")
            if probs else Result("T2.6", Status.PASS, "ruff/tsc clean (available tools)"))
