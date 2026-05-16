import argparse, sys
from audit import core

def main() -> int:
    p = argparse.ArgumentParser(prog="python -m audit")
    g = p.add_mutually_exclusive_group()
    for t in ("static","build","functional","deploy","all"):
        g.add_argument(f"--{t}", action="store_const", const=t, dest="tier")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    if a.selftest: return core.selftest()
    return core.run(core.TIER_SETS[a.tier or "all"], as_json=a.json)

if __name__ == "__main__":
    sys.exit(main())
