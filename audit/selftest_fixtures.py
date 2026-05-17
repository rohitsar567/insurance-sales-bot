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
    """Track backend/_audit_st_syntax.py with a SyntaxError so T2.1 FAILs."""
    rel = "backend/_audit_st_syntax.py"
    f = REPO / rel
    f.write_text("def (:\n    pass\n", encoding="utf-8")
    sh(["git", "add", "-f", rel])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", rel])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t2_2():
    """Track backend/_audit_st_import.py that raises ImportError at import time."""
    rel = "backend/_audit_st_import.py"
    f = REPO / rel
    f.write_text('raise ImportError("audit selftest")\n', encoding="utf-8")
    sh(["git", "add", "-f", rel])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", rel])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t2_3():
    """Track backend/_audit_st_dead.py with a CODE ref to a deleted module.

    The line is a real import statement (not a comment/docstring) so T2.3 must
    classify it as a code_hit and return FAIL, not WARN.
    """
    rel = "backend/_audit_st_dead.py"
    f = REPO / rel
    f.write_text("from backend.orchestrator import handle_turn\n", encoding="utf-8")
    sh(["git", "add", "-f", rel])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", rel])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t2_4():
    """Track frontend/_audit_st.css with an orphan */ so T2.4 FAILs.

    `/* a */ b */` — the first `*/` legitimately closes the comment; the
    trailing ` */` is then an orphan terminator outside any comment, which is
    exactly the comment-terminator footgun T2.4's state machine flags.
    """
    rel = "frontend/_audit_st.css"
    f = REPO / rel
    f.write_text("/* a */ b */\n", encoding="utf-8")
    sh(["git", "add", "-f", rel])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", rel])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t2_5():
    """Track backend/_audit_st_path.py with a hardcoded 40-data path so T2.5 FAILs."""
    rel = "backend/_audit_st_path.py"
    f = REPO / rel
    f.write_text(
        'x = settings.CORPUS_DIR.parent.parent / "40-data" / "y.json"\n',
        encoding="utf-8",
    )
    sh(["git", "add", "-f", rel])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", rel])
        if f.exists():
            f.unlink()


def _ruff_available() -> bool:
    return (REPO / ".venv" / "bin" / "ruff").exists()


def _tsc_available() -> bool:
    return (REPO / "frontend" / "node_modules" / ".bin" / "tsc").exists()


@contextlib.contextmanager
def _f_t2_6():
    """Force a lint/type failure so T2.6 FAILs.

    Prefer a tsc type error (a .ts under frontend/src/ that fails strict type
    checking). If tsc is unavailable, fall back to a ruff-only fixture (a
    flagrant unused-import + bare-except .py under audit/). If NEITHER ruff nor
    tsc is available this single fixture cannot legitimately force a FAIL —
    raise a clear RuntimeError; the hardened core.selftest treats that raise as
    the check failing on the broken fixture, which is the acceptable outcome.
    """
    have_ruff = _ruff_available()
    have_tsc = _tsc_available()
    if not have_ruff and not have_tsc:
        raise RuntimeError("T2.6 selftest needs ruff or tsc")

    created = []
    try:
        if have_tsc:
            rel = "frontend/src/_audit_st_bad.ts"
            f = REPO / rel
            f.write_text('const x: number = "str";\n', encoding="utf-8")
            sh(["git", "add", "-f", rel])
            created.append(rel)
        else:
            rel = "audit/_audit_st_lint.py"
            f = REPO / rel
            f.write_text("import os\ntry:\n    pass\nexcept:\n    pass\n",
                         encoding="utf-8")
            sh(["git", "add", "-f", rel])
            created.append(rel)
        yield
    finally:
        for rel in created:
            sh(["git", "rm", "--cached", "-q", rel])
            fp = REPO / rel
            if fp.exists():
                fp.unlink()


@contextlib.contextmanager
def _f_t3_1():
    """Track a temp test that fails so `pytest -q` exits non-zero -> T3.1 FAIL.

    The file lives under tests/ (testpaths=tests in pytest.ini) AND is named
    `test_*.py` so pytest's default `python_files` glob actually collects it
    — a bare `_audit_st_fail.py` is silently skipped from collection, leaving
    the suite green and T3.1 a false PASS. Restored via `git rm --cached` +
    unlink so the suite is green again and the repo is byte-identical after.
    """
    rel = "tests/test__audit_st_fail.py"
    f = REPO / rel
    f.write_text(
        "def test_audit_selftest_intentional_fail():\n    assert False\n",
        encoding="utf-8",
    )
    sh(["git", "add", "-f", rel])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", rel])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t3_2():
    """Track a temp .tsx with a hard syntax error so `next build` fails.

    The unbalanced/invalid TSX makes Next's compilation step error out fast,
    so T3.2 FAILs without needing a green full build. Restored so the repo is
    byte-identical after (this fixture's selftest is inherently a ~minute
    real build attempt — that is expected).
    """
    rel = "frontend/src/app/_audit_st_bad.tsx"
    f = REPO / rel
    f.write_text(
        "export default function(){ return <div> }\nconst x: = ;\n",
        encoding="utf-8",
    )
    sh(["git", "add", "-f", rel])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", rel])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t3_3():
    """Make `import backend.main` raise so T3.3 FAILs deterministically.

    T3.3 PASSes only if `import backend.main` succeeds AND :8000 is healthy;
    it SKIPs when no backend is up. To force a deterministic FAIL regardless
    of whether a local backend is running, the only sound lever is to make
    the `import backend.main` subprocess raise. We do that WITHOUT corrupting
    real source: create a temp tracked module that raises on import, and
    append a single import line to the END of backend/main.py.

    The original backend/main.py bytes are captured verbatim BEFORE any
    mutation and written back EXACTLY in `finally` — even if the check raises
    mid-way — so backend/main.py is byte-for-byte identical afterwards. A
    corrupted main.py would be a disaster, so the restore is unconditional
    and uses the captured raw bytes (not a re-render).
    """
    helper_rel = "backend/_audit_st_importbreak.py"
    helper = REPO / helper_rel
    main_py = REPO / "backend" / "main.py"

    original_bytes = main_py.read_bytes()  # capture EXACT bytes first
    helper.write_text('raise SyntaxError("audit selftest import break")\n',
                       encoding="utf-8")
    sh(["git", "add", "-f", helper_rel])
    try:
        main_py.write_bytes(
            original_bytes
            + b"\nimport backend._audit_st_importbreak  # AUDIT-ST\n"
        )
        yield
    finally:
        # Restore backend/main.py byte-for-byte, unconditionally.
        main_py.write_bytes(original_bytes)
        sh(["git", "rm", "--cached", "-q", helper_rel])
        if helper.exists():
            helper.unlink()


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
    "T3.1": _f_t3_1,
    "T3.2": _f_t3_2,
    "T3.3": _f_t3_3,
})
