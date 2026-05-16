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
        parts = ln.split()
        mode, blob, path = parts[0], parts[1], ln.split("\t", 1)[1] if "\t" in ln else parts[3]
        if mode == "120000":
            continue
        szout = sh(["git", "cat-file", "-s", blob]).stdout.strip()
        is_lfs = path in lfs_files
        big = szout.isdigit() and int(szout) > 512 * 1024
        if big and not is_lfs:
            bad.append(f"{path} ({int(szout)//1024} KB) not LFS")
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
        if path.endswith((".png", ".jpg", ".jpeg", ".pdf", ".duckdb", ".bin", ".ico", ".woff", ".woff2", ".ttf")):
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
    missing = [it for it in intents if it not in gi]
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
