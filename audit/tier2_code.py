"""Tier 2 — code soundness (pre-commit)."""
from __future__ import annotations
import ast, re
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
                      "remove/replace the dead reference (e.g. get_judge_llm -> get_brain_llm)")
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
        tail = (ruff.stdout or ruff.stderr).strip().splitlines()
        probs.append("ruff: " + (tail[-1][:200] if tail else "error"))
    if tsc.returncode not in (0, 127):
        tail = (tsc.stdout or tsc.stderr).strip().splitlines()
        probs.append("tsc: " + (tail[-1][:200] if tail else "error"))
    if 127 in (ruff.returncode, tsc.returncode) and not probs:
        return Result("T2.6", Status.SKIP, "ruff/tsc not installed", "pip install ruff / npm i")
    return (Result("T2.6", Status.FAIL, " | ".join(probs), "fix lint/type errors")
            if probs else Result("T2.6", Status.PASS, "ruff + tsc clean"))
