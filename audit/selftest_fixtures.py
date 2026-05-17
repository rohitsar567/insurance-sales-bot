"""Selftest fixtures: each yields a context where the matching check FAILs.

Every fixture creates an obviously-temporary broken state under REPO, yields,
then fully restores in a `finally` so the repo is not left dirty.
"""
from __future__ import annotations
import contextlib
import os
from audit.core import REPO, sh

FIXTURES: dict = {}


@contextlib.contextmanager
def _f_t1_1():
    """Track a symlink (mode 120000) so T1.1 FAILs."""
    link = REPO / "_audit_selftest_symlink"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink("README.md", link)
    sh(["git", "add", "-f", "_audit_selftest_symlink"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "_audit_selftest_symlink"])
        if link.exists() or link.is_symlink():
            link.unlink()


@contextlib.contextmanager
def _f_t1_2():
    """Track a >512KB file with an extension not covered by any LFS glob."""
    big = REPO / "_audit_selftest_big.dat"
    big.write_bytes(b"A" * (768 * 1024))
    sh(["git", "add", "-f", "_audit_selftest_big.dat"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "_audit_selftest_big.dat"])
        if big.exists():
            big.unlink()


@contextlib.contextmanager
def _f_t1_3():
    """Track a file containing a key-shaped string so T1.3 FAILs."""
    secret = REPO / "_audit_selftest_secret.txt"
    secret.write_text("token = hf_" + "a1B2c3D4e5F6g7H8i9J0kLmNoP\n", encoding="utf-8")
    sh(["git", "add", "-f", "_audit_selftest_secret.txt"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "_audit_selftest_secret.txt"])
        if secret.exists():
            secret.unlink()


@contextlib.contextmanager
def _f_t1_4():
    """Remove the slash-less `rag/corpus` line from .gitignore so T1.4 FAILs."""
    gi = REPO / ".gitignore"
    original = gi.read_text(encoding="utf-8")
    patched = "\n".join(
        ln for ln in original.split("\n") if ln != "rag/corpus"
    )
    gi.write_text(patched, encoding="utf-8")
    try:
        yield
    finally:
        gi.write_text(original, encoding="utf-8")


@contextlib.contextmanager
def _f_t1_5():
    """Track a path containing .DS_Store so T1.5 FAILs."""
    d = REPO / "_audit_selftest_dir"
    d.mkdir(exist_ok=True)
    junk = d / ".DS_Store"
    junk.write_bytes(b"\x00junk\x00")
    sh(["git", "add", "-f", "_audit_selftest_dir/.DS_Store"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "_audit_selftest_dir/.DS_Store"])
        if junk.exists():
            junk.unlink()
        if d.exists():
            d.rmdir()


@contextlib.contextmanager
def _f_t2_1():
    """Track a .py with a syntax error so T2.1 FAILs."""
    f = REPO / "_audit_selftest_syntax.py"
    f.write_text("def broken(:\n    pass\n", encoding="utf-8")
    sh(["git", "add", "-f", "_audit_selftest_syntax.py"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "_audit_selftest_syntax.py"])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t2_2():
    """Track backend/_audit_selftest_mod.py that raises NameError at import."""
    f = REPO / "backend" / "_audit_selftest_mod.py"
    f.write_text("x = undefined_thing\n", encoding="utf-8")
    sh(["git", "add", "-f", "backend/_audit_selftest_mod.py"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "backend/_audit_selftest_mod.py"])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t2_3():
    """Track backend/_audit_selftest_dead.py with a code ref to a deleted module."""
    f = REPO / "backend" / "_audit_selftest_dead.py"
    f.write_text("from backend.orchestrator import handle_turn\n", encoding="utf-8")
    sh(["git", "add", "-f", "backend/_audit_selftest_dead.py"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "backend/_audit_selftest_dead.py"])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t2_4():
    """Track a .css file with a nested */ inside a block comment so T2.4 FAILs."""
    f = REPO / "_audit_selftest.css"
    f.write_text("/* a */ b */\n.x { color: red; }\n", encoding="utf-8")
    sh(["git", "add", "-f", "_audit_selftest.css"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "_audit_selftest.css"])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t2_5():
    """Track backend/_audit_selftest_path.py with a hardcoded 40-data path."""
    f = REPO / "backend" / "_audit_selftest_path.py"
    f.write_text('x = something / "40-data" / "y"\n', encoding="utf-8")
    sh(["git", "add", "-f", "backend/_audit_selftest_path.py"])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", "backend/_audit_selftest_path.py"])
        if f.exists():
            f.unlink()


def _ruff_available() -> bool:
    return (REPO / ".venv" / "bin" / "ruff").exists()


def _tsc_available() -> bool:
    return (REPO / "frontend" / "node_modules" / ".bin" / "tsc").exists()


@contextlib.contextmanager
def _f_t2_6():
    """Force a lint/type failure so T2.6 FAILs.

    Prefer ruff (a flagrant unused-import + bare-except .py under audit/). If
    ruff is unavailable in this env T2.6 would SKIP (not FAIL) on a ruff-only
    fixture, so fall back to forcing a tsc error via a broken .ts under
    frontend/src/. If NEITHER ruff nor tsc is available this single fixture
    cannot legitimately force a FAIL — raise a clear RuntimeError so selftest
    surfaces it rather than reporting a false pass.
    """
    have_ruff = _ruff_available()
    have_tsc = _tsc_available()
    if not have_ruff and not have_tsc:
        raise RuntimeError("T2.6 selftest needs ruff or tsc")

    created = []
    try:
        if have_ruff:
            f = REPO / "audit" / "_audit_selftest_lint.py"
            f.write_text("import os\ntry:\n    pass\nexcept:\n    pass\n",
                         encoding="utf-8")
            sh(["git", "add", "-f", "audit/_audit_selftest_lint.py"])
            created.append("audit/_audit_selftest_lint.py")
        else:
            f = REPO / "frontend" / "src" / "_audit_selftest_bad.ts"
            f.write_text("export const x: number = ;\n", encoding="utf-8")
            sh(["git", "add", "-f", "frontend/src/_audit_selftest_bad.ts"])
            created.append("frontend/src/_audit_selftest_bad.ts")
        yield
    finally:
        for rel in created:
            sh(["git", "rm", "--cached", "-q", rel])
            fp = REPO / rel
            if fp.exists():
                fp.unlink()


FIXTURES.update({
    "T1.1": _f_t1_1,
    "T1.2": _f_t1_2,
    "T1.3": _f_t1_3,
    "T1.4": _f_t1_4,
    "T1.5": _f_t1_5,
    "T2.1": _f_t2_1,
    "T2.2": _f_t2_2,
    "T2.3": _f_t2_3,
    "T2.4": _f_t2_4,
    "T2.5": _f_t2_5,
    "T2.6": _f_t2_6,
})
