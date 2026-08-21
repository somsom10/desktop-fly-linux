"""Measure the unseeded pass rate of the behaviour suite, per check.

Upstream's suites are stochastic by design. This quantifies which checks are
inherently flaky so a real regression is distinguishable from bad luck.
"""
import sys, os, io, contextlib, collections, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from desktopfly.tests import behaviortest as bt

N = int(os.environ.get("RUNS", "60"))
fails = collections.Counter()
clean = 0
for i in range(N):
    random.seed()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = bt.run(seed=None)
    if rc == 0:
        clean += 1
    for line in buf.getvalue().splitlines():
        if line.startswith("FAIL"):
            fails[line.split(":")[0].replace("FAIL  ", "").strip()] += 1
print(f"runs: {N}  all-17-pass: {clean} ({100*clean/N:.0f}%)")
for name, c in fails.most_common():
    print(f"  {100*c/N:5.1f}%  {name}")
if not fails:
    print("  (no failures observed)")
