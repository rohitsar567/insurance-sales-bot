"""Audit framework core: check contract, registry, runner, selftest."""
from __future__ import annotations
import dataclasses, enum, json, pathlib, subprocess
from typing import Callable

REPO = pathlib.Path(__file__).resolve().parent.parent

class Status(enum.Enum):
    PASS = "PASS"; WARN = "WARN"; FAIL = "FAIL"; SKIP = "SKIP"

@dataclasses.dataclass
class Result:
    check_id: str; status: "Status"; evidence: str; remediation: str = ""

@dataclasses.dataclass
class Check:
    id: str; tier: str; title: str; fn: Callable[[], "Result"]
    selftest_expect: "Status" = Status.FAIL

CHECKS: list[Check] = []

def register(id: str, tier: str, title: str, selftest_expect: "Status" = Status.FAIL):
    def deco(fn):
        CHECKS.append(Check(id, tier, title, fn, selftest_expect)); return fn
    return deco

TIER_SETS = {
    "static": {"static"}, "build": {"static","build"},
    "functional": {"static","build","functional"}, "deploy": {"deploy"},
    "all": {"static","build","functional","deploy"},
}

def sh(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)

def git(*args: str) -> str:
    return sh(["git", *args]).stdout.strip()

def _load_all_checks() -> None:
    from audit import (tier1_repo, tier2_code, tier3_build,  # noqa: F401
                        tier4_functional, tier5_deploy)

def run(selected: set[str], as_json: bool = False) -> int:
    if not CHECKS:
        _load_all_checks()
    rows = []
    for c in sorted(CHECKS, key=lambda c: c.id):
        if c.tier not in selected: continue
        try:
            r = c.fn()
        except Exception as e:
            r = Result(c.id, Status.FAIL, f"check raised {type(e).__name__}: {e}",
                       "fix the check or the underlying issue")
        rows.append((c, r))
    fails = [r for _, r in rows if r.status is Status.FAIL]
    if as_json:
        print(json.dumps([{"id": c.id, "status": r.status.value,
                            "evidence": r.evidence, "remediation": r.remediation}
                           for c, r in rows], indent=2))
    else:
        for c, r in rows:
            mark = {"PASS":"OK","WARN":"WARN","FAIL":"FAIL","SKIP":"SKIP"}[r.status.value]
            print(f"  [{mark}] {c.id} {c.title}")
            if r.status in (Status.FAIL, Status.WARN):
                print(f"      {r.evidence}")
                if r.remediation: print(f"      fix: {r.remediation}")
        print(f"\n  {len(rows)} checks · "
              f"{sum(1 for _,r in rows if r.status is Status.PASS)} pass · "
              f"{sum(1 for _,r in rows if r.status is Status.WARN)} warn · "
              f"{len(fails)} fail · "
              f"{sum(1 for _,r in rows if r.status is Status.SKIP)} skip")
    return 1 if fails else 0

def selftest(only_tiers: set[str] | None = None) -> int:
    from audit.selftest_fixtures import FIXTURES
    if not CHECKS:
        _load_all_checks()
    checks = CHECKS if only_tiers is None else [c for c in CHECKS if c.tier in only_tiers]
    bad = []
    for c in checks:
        fx = FIXTURES.get(c.id)
        if fx is None:
            bad.append(f"{c.id}: NO selftest fixture")
            continue
        try:
            with fx():
                r = c.fn()
        except Exception as e:
            r = Result(c.id, Status.FAIL, f"raised {type(e).__name__}: {e}")
        if r.status is not c.selftest_expect:
            bad.append(f"{c.id}: expected {c.selftest_expect.value} on broken fixture, got {r.status.value}")
    for b in bad: print(f"  FAIL {b}")
    print(f"\n  selftest: {len(checks)} checks · {len(bad)} not self-verifying")
    return 1 if bad else 0
