"""Tier 3 — build & test gates."""
from __future__ import annotations
import re, urllib.request
from audit.core import register, Result, Status, sh


@register("T3.1", "build", "pytest green (clean-clone scoping)")
def t3_1() -> Result:
    r = sh([".venv/bin/python", "-m", "pytest", "-q"], timeout=600)
    out = (r.stdout + r.stderr).strip()
    tail = out.splitlines()[-1] if out else ""
    low = tail.lower()
    if r.returncode != 0 or "error" in low or "failed" in low:
        return Result("T3.1", Status.FAIL, tail[:200], "fix failing tests / collection error")
    m = re.search(r"\d+ passed", tail)
    return Result("T3.1", Status.PASS, m.group(0) if m else "green")


@register("T3.2", "build", "next build (production static export)")
def t3_2() -> Result:
    r = sh(["npm", "--prefix", "frontend", "run", "build"], timeout=900)
    htmls = sh(["bash", "-lc", "ls frontend/out/*.html 2>/dev/null | wc -l"]).stdout.strip()
    if r.returncode != 0:
        out = (r.stdout + r.stderr).strip().splitlines()
        return Result("T3.2", Status.FAIL, (out[-1][:200] if out else "build failed"),
                      "fix the production build error")
    if htmls in ("", "0"):
        return Result("T3.2", Status.FAIL, "no static export emitted",
                      "ensure next.config has output:'export'")
    return Result("T3.2", Status.PASS, f"build ok, {htmls} html exported")


@register("T3.3", "build", "backend boots + /api/health (local)")
def t3_3() -> Result:
    imp = sh([".venv/bin/python", "-c", "import backend.main"], timeout=120)
    if imp.returncode != 0:
        return Result("T3.3", Status.FAIL, imp.stderr.strip()[:200], "fix backend import")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=8) as f:
            body = f.read(200).decode()
        return (Result("T3.3", Status.PASS, "backend imports + /api/health ok")
                if '"status":"ok"' in body
                else Result("T3.3", Status.FAIL, body[:120], "backend unhealthy"))
    except Exception:
        return Result("T3.3", Status.SKIP, "no local backend on :8000",
                      "start uvicorn backend.main:app --port 8000 to run this check")
