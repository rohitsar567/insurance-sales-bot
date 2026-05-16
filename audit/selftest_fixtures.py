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


FIXTURES.update({
    "T1.1": _f_t1_1,
    "T1.2": _f_t1_2,
    "T1.3": _f_t1_3,
    "T1.4": _f_t1_4,
    "T1.5": _f_t1_5,
})
