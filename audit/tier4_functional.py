"""Tier 4 — functionality: API smoke (deterministic) + Playwright E2E.
Infra-dependent: SKIPs (never false-PASS, never crash) when the local
backend / frontend / playwright runner is unavailable."""
from __future__ import annotations
import json, os, time, urllib.request, urllib.error
from audit.core import register, Result, Status, REPO, sh

BK = "http://127.0.0.1:8000"
FE = "http://localhost:3000"
PW_RUN = "/Users/rohitsar/.claude/skills/playwright-skill/run.js"


def _get(path, timeout=20):
    with urllib.request.urlopen(BK + path, timeout=timeout) as f:
        return f.status, f.read().decode("utf-8", "replace")


def _post(path, payload, timeout=90):
    req = urllib.request.Request(
        BK + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.status, f.read().decode("utf-8", "replace")


def _backend_up():
    try:
        s, b = _get("/api/health", timeout=6)
        return s == 200 and '"status":"ok"' in b
    except Exception:
        return False


@register("T4.1", "functional", "API: health + version")
def t4_1():
    if not _backend_up():
        return Result("T4.1", Status.SKIP, "no local backend on :8000", "start uvicorn backend.main:app --port 8000")
    try:
        _, hb = _get("/api/health")
        _, vb = _get("/api/version")
        ok = '"status":"ok"' in hb and json.loads(vb) is not None
        return Result("T4.1", Status.PASS if ok else Status.FAIL,
                      "health ok + version json" if ok else f"health={hb[:80]} version={vb[:80]}",
                      "" if ok else "investigate /api/health or /api/version")
    except Exception as e:
        return Result("T4.1", Status.FAIL, f"{type(e).__name__}: {e}", "endpoint error")


@register("T4.2", "functional", "API: coverage counts sane")
def t4_2():
    if not _backend_up():
        return Result("T4.2", Status.SKIP, "no local backend", "start backend")
    try:
        _, b = _get("/api/coverage", timeout=40)
        d = json.loads(b)
        p, i, c = d.get("total_policies"), d.get("total_insurers"), d.get("total_chunks")
        ok = isinstance(p, int) and 130 <= p <= 170 and i == 20 and isinstance(c, int) and c > 5000
        return Result("T4.2", Status.PASS if ok else Status.FAIL,
                      f"policies={p} insurers={i} chunks={c}",
                      "" if ok else "coverage outside expected (~148 policies / 20 insurers / >5000 chunks)")
    except Exception as e:
        return Result("T4.2", Status.FAIL, f"{type(e).__name__}: {e}", "coverage endpoint error")


@register("T4.3", "functional", "API: chat returns a grounded reply")
def t4_3():
    if not _backend_up():
        return Result("T4.3", Status.SKIP, "no local backend", "start backend")
    try:
        _, b = _post("/api/chat",
                     {"user_text": "What is a waiting period in health insurance?",
                      "session_id": f"audit-smoke-{int(time.time())}"}, timeout=90)
        d = json.loads(b)
        reply = (d.get("reply_text") or "").strip()
        brain = d.get("brain_used", "")
        if not reply:
            return Result("T4.3", Status.FAIL, f"empty reply (brain={brain})", "chat returned no text")
        if "error_fallback" in brain:
            return Result("T4.3", Status.WARN, f"reply via {brain}", "LLM chain degraded (env/keys) — verify live")
        return Result("T4.3", Status.PASS, f"reply ok ({len(reply)} chars, brain={brain})")
    except Exception as e:
        return Result("T4.3", Status.WARN, f"{type(e).__name__}: {e}",
                      "chat slow/unavailable (LLM dependency); not a code FAIL on its own")


@register("T4.4", "functional", "API: upload-policy rejects junk")
def t4_4():
    if not _backend_up():
        return Result("T4.4", Status.SKIP, "no local backend", "start backend")
    boundary = "----auditST"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"junk.pdf\"\r\nContent-Type: application/pdf\r\n\r\n"
            + "not a real pdf " * 10 + f"\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(BK + "/api/upload-policy", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        urllib.request.urlopen(req, timeout=30)
        return Result("T4.4", Status.FAIL, "junk PDF was accepted", "upload security gates not rejecting")
    except urllib.error.HTTPError as e:
        return (Result("T4.4", Status.PASS, f"junk rejected (HTTP {e.code})")
                if e.code in (400, 413, 415, 422)
                else Result("T4.4", Status.FAIL, f"unexpected HTTP {e.code}", "check upload gate"))
    except Exception as e:
        return Result("T4.4", Status.WARN, f"{type(e).__name__}: {e}", "upload endpoint unreachable")


@register("T4.5", "functional", "API: profile + session endpoints")
def t4_5():
    if not _backend_up():
        return Result("T4.5", Status.SKIP, "no local backend", "start backend")
    sid = f"audit-smoke-{int(time.time())}"
    try:
        s1, _ = _get(f"/api/profile/completeness?session_id={sid}", timeout=20)
        s2, _ = _post("/api/session/clear", {"session_id": sid}, timeout=20)
        ok = s1 == 200 and s2 == 200
        return Result("T4.5", Status.PASS if ok else Status.FAIL,
                      f"profile/completeness={s1} session/clear={s2}",
                      "" if ok else "profile/session endpoint non-200")
    except Exception as e:
        return Result("T4.5", Status.FAIL, f"{type(e).__name__}: {e}", "profile/session error")


@register("T4.E2E", "functional", "Frontend E2E journeys (Playwright)")
def t4_e2e():
    if not os.path.exists(PW_RUN):
        return Result("T4.E2E", Status.SKIP, "playwright-skill runner not found",
                      "install/locate the playwright-skill to enable E2E")
    try:
        with urllib.request.urlopen(FE, timeout=6) as f:
            if f.status != 200:
                raise RuntimeError("frontend not 200")
    except Exception:
        return Result("T4.E2E", Status.SKIP, "no frontend on :3000",
                      "run `cd frontend && npm run dev` to enable E2E")
    r = sh(["node", PW_RUN, str(REPO / "audit/e2e/insurancebot_e2e.js")], timeout=300)
    out = r.stdout + r.stderr
    import re
    m = re.search(r"RJSON (\{.*\})", out)
    if not m:
        return Result("T4.E2E", Status.WARN, "no RJSON from e2e run (infra/flake)",
                      "inspect audit/e2e/insurancebot_e2e.js output; not a code FAIL alone")
    d = json.loads(m.group(1))
    fails = [k for k, v in d.items() if v is False]
    if fails:
        return Result("T4.E2E", Status.FAIL, f"E2E journey failures: {fails}",
                      "investigate the failing UI journey")
    return Result("T4.E2E", Status.PASS, f"E2E ok: {sorted(d)}")
