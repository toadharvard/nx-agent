#!/usr/bin/env python3
"""modlift.run (R3 / reloc-consistency fixed point): a cycle of (X, R) states is red (RuntimeError), never a result; a run that converges returns. _run is replaced by a scripted producer
ONLY to feed the loop (the loop under test is modlift.run itself; no product symbol is defined here)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import modlift
F = []
def check(n, c):
    print(('ok   ' if c else 'FAIL ') + n); (c or F.append(n))
def script(seq):
    it = iter(seq)
    def _run(arg, cfg, X, R):
        return next(it)
    return _run
# (code, Xo, Ro, PMF, nE, nC, nh)
modlift._run = script([(0, {1}, {}, [], 1, 0, 0), (0, set(), {}, [], 1, 0, 0), (0, {1}, {}, [], 1, 0, 0), (0, set(), {}, [], 1, 0, 0)])
try: modlift.run('x', None); ok = False
except RuntimeError as e: ok = 'cycle at round' in str(e)
check('an oscillating X (state seen twice) is a RuntimeError naming the cycle', ok)
sys.exit(1 if F else 0)
