"""Selftest fixtures: each yields a context where the matching check FAILs.

Every fixture creates an obviously-temporary broken state under REPO, yields,
then fully restores in a `finally` so the repo is not left dirty.
"""
from __future__ import annotations
import contextlib
import json
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


# ---------------------------------------------------------------------------
# Tier 4 fixtures.
#
# T4 checks hit a LIVE local backend / frontend / playwright runner, so a
# file-on-disk fixture (the T1–T3 pattern) cannot force a deterministic FAIL.
# Instead each fixture monkeypatches the *module seams* of
# `audit.tier4_functional` (`_backend_up`, `_get`, `_post`, the module's
# `urllib.request.urlopen`, `sh`) so the check exercises its real logic against
# a controlled response and returns FAIL. Every patch is captured BEFORE the
# yield and restored UNCONDITIONALLY in `finally` — even if the check raises —
# so no module attr (and no file) is left mutated. No repo files are touched.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _f_t4_1():
    """Backend 'up' but /api/health reports a non-ok status -> T4.1 FAIL."""
    import audit.tier4_functional as m
    orig_up, orig_get = m._backend_up, m._get
    m._backend_up = lambda: True
    m._get = lambda p, timeout=20: (200, '{"status":"bad"}')
    try:
        yield
    finally:
        m._backend_up, m._get = orig_up, orig_get


@contextlib.contextmanager
def _f_t4_2():
    """Coverage counts far outside the sane window -> T4.2 FAIL."""
    import audit.tier4_functional as m
    orig_up, orig_get = m._backend_up, m._get
    m._backend_up = lambda: True
    m._get = lambda p, timeout=20: (
        200,
        '{"total_policies":1,"total_insurers":1,"total_chunks":1}',
    )
    try:
        yield
    finally:
        m._backend_up, m._get = orig_up, orig_get


@contextlib.contextmanager
def _f_t4_3():
    """Chat returns an empty reply_text -> T4.3 FAIL (empty reply)."""
    import audit.tier4_functional as m
    orig_up, orig_post = m._backend_up, m._post
    m._backend_up = lambda: True
    m._post = lambda p, payload, timeout=90: (
        200,
        '{"reply_text":"","brain_used":"x"}',
    )
    try:
        yield
    finally:
        m._backend_up, m._post = orig_up, orig_post


@contextlib.contextmanager
def _f_t4_4():
    """Make the junk-PDF upload POST 'succeed' (fake 200) -> T4.4 FAIL.

    T4.4 calls `urllib.request.urlopen` directly (not via `_post`). A real junk
    upload raises HTTPError(4xx) -> PASS. To force the FAIL branch we patch the
    `urllib.request.urlopen` *as referenced inside tier4_functional* so the
    POST returns a fake 200 response instead of raising — i.e. the app
    'accepted junk', which T4.4 must report as FAIL.
    """
    import audit.tier4_functional as m
    orig_up = m._backend_up
    orig_urlopen = m.urllib.request.urlopen

    class _FakeResp:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, *a, **k):
        return _FakeResp()

    m._backend_up = lambda: True
    m.urllib.request.urlopen = _fake_urlopen
    try:
        yield
    finally:
        m._backend_up = orig_up
        m.urllib.request.urlopen = orig_urlopen


@contextlib.contextmanager
def _f_t4_5():
    """Profile/session endpoints return HTTP 500 -> T4.5 FAIL."""
    import audit.tier4_functional as m
    orig_up, orig_get, orig_post = m._backend_up, m._get, m._post
    m._backend_up = lambda: True
    m._get = lambda p, timeout=20: (500, "err")
    m._post = lambda p, payload, timeout=90: (500, "err")
    try:
        yield
    finally:
        m._backend_up, m._get, m._post = orig_up, orig_get, orig_post


@contextlib.contextmanager
def _f_t4_e2e():
    """Frontend 'reachable' + e2e run reports a failed journey -> T4.E2E FAIL.

    PW_RUN exists on this machine, so `os.path.exists(PW_RUN)` is already True.
    We patch the module's `urllib.request.urlopen` so the FE :3000 reachability
    probe 'passes' (fake 200) regardless of whether a dev server is up, and
    patch the module's `sh` to return a fake CompletedProcess whose stdout is
    `RJSON {"loads":false}` — a failing journey, which T4.E2E must report as
    FAIL. No real browser/runner is spawned.
    """
    import subprocess
    import audit.tier4_functional as m
    orig_urlopen = m.urllib.request.urlopen
    orig_sh = m.sh

    class _FakeResp:
        status = 200

        def read(self):
            return b"<html><h1>x</h1></html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    m.urllib.request.urlopen = lambda req, *a, **k: _FakeResp()
    m.sh = lambda cmd, timeout=120: subprocess.CompletedProcess(
        cmd, 0, stdout='RJSON {"loads":false}\n', stderr=""
    )
    try:
        yield
    finally:
        m.urllib.request.urlopen = orig_urlopen
        m.sh = orig_sh


# ---------------------------------------------------------------------------
# Tier 5 fixtures.
#
# T5.1/T5.2/T5.4 are file/index-on-disk fixtures (the T1–T3 pattern): create an
# obviously-temporary broken tracked state, yield, then fully restore so the
# repo is byte-identical afterwards. T5.2 captures the Dockerfile's EXACT bytes
# BEFORE any mutation and rewrites them UNCONDITIONALLY first in `finally`
# (the T3.3/main.py critical-integrity pattern) — a corrupted Dockerfile would
# break the deploy. T5.3 hits the HF Space API, so it monkeypatches the module
# seam (`tier5_deploy.urllib.request.urlopen`) to return a controlled
# non-RUNNING runtime; the patch is restored UNCONDITIONALLY in `finally`.
# T5.3/T5.4's checks carry selftest_expect=Status.WARN, so their fixtures must
# make the check RETURN Status.WARN (not raise).
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _f_t5_1():
    """Track _audit_st.parquet as a PLAIN blob (NOT an LFS pointer).

    `.parquet` is in `.gitattributes` as `filter=lfs`, so a non-pointer
    `.parquet` blob is exactly the "LFS-globbed but stored raw" state HF's
    pre-push hook rejects -> T5.1 FAIL. We add it with the lfs clean/smudge
    filters neutralised so git stores the raw bytes instead of a pointer, then
    assert it is NOT in `git lfs ls-files`. Restored via `git rm --cached` +
    unlink so the repo is byte-identical after.
    """
    rel = "_audit_st.parquet"
    f = REPO / rel
    f.write_bytes(b"PAR1_audit_selftest_not_a_pointer_PAR1\n")
    # Empty ALL lfs filters (clean/smudge/process) and drop the `required`
    # guard so `git add` stores the raw bytes instead of running the LFS
    # clean filter (which, if only partially neutralised, aborts the add and
    # silently leaves nothing staged — a false-PASS for T5.1).
    sh(["git",
        "-c", "filter.lfs.clean=",
        "-c", "filter.lfs.smudge=",
        "-c", "filter.lfs.process=",
        "-c", "filter.lfs.required=false",
        "add", "-f", rel])
    # Sanity: it must be tracked AND NOT an LFS pointer for the fixture valid.
    tracked = sh(["git", "ls-files"]).stdout.split()
    tracked_lfs = sh(["git", "lfs", "ls-files", "-n"]).stdout.split()
    if rel not in tracked or rel in tracked_lfs:
        sh(["git", "rm", "--cached", "-qf", rel])
        if f.exists():
            f.unlink()
        raise RuntimeError(
            "T5.1 fixture: _audit_st.parquet not staged as a plain blob "
            f"(tracked={rel in tracked} lfs={rel in tracked_lfs})")
    try:
        yield
    finally:
        # staged content differs from HEAD (file is new) -> needs -f.
        sh(["git", "rm", "--cached", "-qf", rel])
        if f.exists():
            f.unlink()


@contextlib.contextmanager
def _f_t5_2():
    """Append a `COPY <nonexistent> ./x` line to Dockerfile so T5.2 FAILs.

    The Dockerfile's EXACT original bytes are captured BEFORE any mutation and
    written back UNCONDITIONALLY as the FIRST statement in `finally` (even if
    the check raises mid-way) — a corrupted Dockerfile is a deploy disaster, so
    the restore is the verbatim captured bytes, not a re-render.
    """
    df = REPO / "Dockerfile"
    original_bytes = df.read_bytes()  # capture EXACT bytes first
    try:
        df.write_bytes(
            original_bytes
            + b"\nCOPY _audit_st_nonexistent_dir ./x\n"
        )
        yield
    finally:
        # Restore Dockerfile byte-for-byte, unconditionally, FIRST.
        df.write_bytes(original_bytes)


@contextlib.contextmanager
def _f_t5_3():
    """Patch tier5_deploy.urllib.request.urlopen so SPACE_API runtime.stage is
    BUILDING (not RUNNING) -> T5.3 returns Status.WARN.

    The patched urlopen is captured BEFORE the yield and restored
    UNCONDITIONALLY in `finally`. No repo files are touched.
    """
    import io
    import audit.tier5_deploy as m
    orig_urlopen = m.urllib.request.urlopen
    payload = json.dumps({"runtime": {"stage": "BUILDING", "sha": "deadbeefcafe0000"}})

    def _fake_urlopen(url, *a, **k):
        # Only intercept the HF Space API call; anything else is unexpected
        # here (the BUILDING branch returns before any LIVE smoke fetch).
        return io.BytesIO(payload.encode())

    m.urllib.request.urlopen = _fake_urlopen
    try:
        yield
    finally:
        m.urllib.request.urlopen = orig_urlopen


@contextlib.contextmanager
def _f_t5_4():
    """Track 70-docs/_audit_st_stale.md containing both `Status | Live` and
    `orchestrator.py` so T5.4's stale-present-state-doc tripwire -> WARN.

    Restored via `git rm --cached` + unlink so the repo is byte-identical
    after.
    """
    rel = "70-docs/_audit_st_stale.md"
    f = REPO / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "| Status | Live |\n\nReferences backend/orchestrator.py here.\n",
        encoding="utf-8",
    )
    sh(["git", "add", "-f", rel])
    try:
        yield
    finally:
        sh(["git", "rm", "--cached", "-q", rel])
        if f.exists():
            f.unlink()


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
    "T4.1": _f_t4_1,
    "T4.2": _f_t4_2,
    "T4.3": _f_t4_3,
    "T4.4": _f_t4_4,
    "T4.5": _f_t4_5,
    "T4.E2E": _f_t4_e2e,
    "T5.1": _f_t5_1,
    "T5.2": _f_t5_2,
    "T5.3": _f_t5_3,
    "T5.4": _f_t5_4,
})
