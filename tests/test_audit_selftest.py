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
