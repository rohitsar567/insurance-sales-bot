import subprocess, sys, pathlib
REPO = pathlib.Path(__file__).resolve().parent.parent

def test_core_runner_passes_a_trivial_pass_check():
    code = subprocess.run(
        [sys.executable, "-c",
         "import audit.core as c; c.CHECKS.clear(); "
         "c.register('X.1','static','dummy')(lambda: c.Result('X.1', c.Status.PASS, 'ok')); "
         "import sys; sys.exit(c.run({'static'}))"],
        cwd=REPO).returncode
    assert code == 0

def test_core_runner_fails_on_a_fail_check():
    code = subprocess.run(
        [sys.executable, "-c",
         "import audit.core as c; c.CHECKS.clear(); "
         "c.register('X.2','static','dummy')(lambda: c.Result('X.2', c.Status.FAIL, 'bad')); "
         "import sys; sys.exit(c.run({'static'}))"],
        cwd=REPO).returncode
    assert code == 1


def test_static_checks_are_self_verifying():
    """The auditor's own integrity is in the pytest gate — but ONLY the
    'static' tier (Tier 1+2). The build/functional/deploy tiers are excluded
    here on purpose: T3.1's fixture shells `pytest`, which would recurse into
    this very test; T3.2 runs a multi-minute `next build`; Tier 4 needs a
    live backend. Those are exercised by `tools/audit.sh --selftest` (manual
    / pre-push hook), not the unit gate."""
    import audit.core as c
    assert c.selftest(only_tiers={"static"}) == 0, "a static-tier check is not self-verifying (see stdout)"
