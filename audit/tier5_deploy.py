"""Tier 5 — deploy safety. READ-ONLY vs production (HTTP GET + git ls-remote
+ local git only). NEVER pushes or mutates anything."""
from __future__ import annotations
import fnmatch, json, urllib.request
from audit.core import register, Result, Status, REPO, git, sh

SPACE_API = "https://huggingface.co/api/spaces/rohitsar567/InsuranceBot"
LIVE = "https://rohitsar567-insurancebot.hf.space"


@register("T5.1", "deploy", "LFS pre-push validation (HF hook simulation)")
def t5_1() -> Result:
    ga = (REPO / ".gitattributes").read_text(encoding="utf-8", errors="replace")
    globs = [l.split()[0] for l in ga.splitlines()
             if "filter=lfs" in l and l.strip() and not l.startswith("#")]
    lfs = set(sh(["git", "lfs", "ls-files", "-n"]).stdout.split())
    bad = [p for p in git("ls-files").splitlines()
           if any(fnmatch.fnmatch(p, g) for g in globs) and p not in lfs]
    return (Result("T5.1", Status.FAIL, f"non-LFS but LFS-globbed (HF would reject): {bad[:6]}",
                   "git rm --cached + re-add so they store as LFS pointers")
            if bad else Result("T5.1", Status.PASS, "all LFS-pattern files are pointers"))


@register("T5.2", "deploy", "Dockerfile coherence")
def t5_2() -> Result:
    df = (REPO / "Dockerfile").read_text(encoding="utf-8", errors="replace")
    missing = []
    for ln in df.splitlines():
        s = ln.strip()
        if s.startswith("COPY ") and not s.startswith("COPY --from"):
            parts = s.split()
            if len(parts) >= 3:
                src = parts[1]
                if src not in (".",) and "*" not in src and not (REPO / src).exists():
                    missing.append(src)
    collide = [p for p in git("ls-files").splitlines()
               if p in ("rag/corpus", "rag/extracted", "rag/vectors")]
    if missing or collide:
        return Result("T5.2", Status.FAIL,
                      f"COPY src missing: {missing}; dataset-hydration collide: {collide}",
                      "fix the COPY source path / untrack rag/corpus|extracted|vectors")
    return Result("T5.2", Status.PASS, "COPY paths exist; no hydration collision")


@register("T5.3", "deploy", "deployed sha matches local (guarded)", selftest_expect=Status.WARN)
def t5_3() -> Result:
    head = git("rev-parse", "HEAD")[:12]
    o = sh(["git", "ls-remote", "origin", "-h", "refs/heads/main"]).stdout.split()
    origin_sha = o[0][:12] if o else ""
    try:
        rt = json.load(urllib.request.urlopen(SPACE_API, timeout=20)).get("runtime", {})
    except Exception as e:
        return Result("T5.3", Status.SKIP, f"no network/HF API: {e}", "run with network to verify deploy")
    stage = rt.get("stage")
    live_sha = (rt.get("sha") or "")[:12]
    if stage != "RUNNING":
        return Result("T5.3", Status.WARN, f"Space stage={stage} sha={live_sha}",
                      "wait for build / inspect HF build log")
    if origin_sha and live_sha and live_sha != origin_sha:
        return Result("T5.3", Status.WARN,
                      f"live sha {live_sha} != origin/main {origin_sha} (Space lagging build / LFS silent-fail?)",
                      "verify the Space actually rebuilt; never trust 'RUNNING' alone")
    if origin_sha and origin_sha != head:
        return Result("T5.3", Status.WARN,
                      f"local HEAD {head} not pushed (origin/main={origin_sha} is live)",
                      "expected if deploy is deliberately deferred; push when ready")
    try:
        h = urllib.request.urlopen(f"{LIVE}/api/health", timeout=20).read(200).decode()
        logo = urllib.request.urlopen(f"{LIVE}/insurer-logos/oriental-insurance.png", timeout=20)
        ok = '"status":"ok"' in h and logo.headers.get_content_type() == "image/png"
        return (Result("T5.3", Status.PASS, f"sha {live_sha} live; health ok; LFS logo image/png")
                if ok else Result("T5.3", Status.FAIL, f"live unhealthy: {h[:80]}", "investigate live deploy"))
    except Exception as e:
        return Result("T5.3", Status.WARN, f"live smoke unreachable: {e}", "retry with network")


@register("T5.4", "deploy", "standing tripwires (bloat/disk/stale-docs)", selftest_expect=Status.WARN)
def t5_4() -> Result:
    warns = []
    bloat = sh(["bash", "-lc",
                "find rag -name link_lists.bin -size +200M 2>/dev/null | head -3; "
                "du -sm rag/_hf_dataset_backup 2>/dev/null | awk '$1>20000{print $0\" MB\"}'"]).stdout.strip()
    if bloat:
        warns.append(f"chroma/backup bloat: {bloat[:160]}")
    free = sh(["bash", "-lc", "df -m . | tail -1 | awk '{print $4}'"]).stdout.strip()
    if free.isdigit() and int(free) < 2000:
        warns.append(f"low disk: {free} MB free")
    stale = sh(["bash", "-lc",
                "grep -rl 'Status | Live' 70-docs 2>/dev/null | xargs -r grep -l 'orchestrator.py' 2>/dev/null | head -3"]).stdout.strip()
    if stale:
        warns.append(f"stale present-state docs: {stale.splitlines()[:3]}")
    return (Result("T5.4", Status.WARN, " | ".join(warns), "address the flagged tripwire")
            if warns else Result("T5.4", Status.PASS, "no bloat/disk/stale-doc tripwire"))
