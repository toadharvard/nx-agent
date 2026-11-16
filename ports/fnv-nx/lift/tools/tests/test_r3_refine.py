"""R3 refinement termination (modlift.r3_settle): the exclusion-set sequence of an antitone map G, given to r3_settle as its round step (an argument, nothing is replaced).
G(X) = the sites that stay displacement-only when X is excluded. Cases: a fixed point; a 2-cycle where the sequence reaches L first; a 2-cycle where U is reached first (one extra round with L);
a 3-cycle is an error. The answer X must satisfy X <= Xo of its own round. The real round (modlift._run) is exercised by the facts stage of every image."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import sys, os, io, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import fnvnx.lift.tools.modlift as modlift

F = frozenset


def drive(G):
    calls = []
    def step(X):
        Xo = F(G(X)); calls.append((X, Xo)); return Xo, None
    with contextlib.redirect_stderr(io.StringIO()):
        X, _, k, fixed = modlift.r3_settle(step)
    assert X <= calls[k][1] and calls[k][0] == X, (X, calls)
    return X, fixed, calls


X, fixed, c = drive(lambda X: {1}); assert X == F({1}) and fixed and len(c) == 2, c
def g2(X): return {1, 2} if X in (F(), F({1})) else {1}      # L = {1}, U = {1,2}: G(L) = U, G(U) = L; S = {}, {1,2}, {1}, {1,2}...
X, fixed, c = drive(g2); assert X == F({1}) and not fixed, c
def g3(X): return {1} if X in (F(), F({1, 2})) else {1, 2}      # S = {}, {1}, {1,2}, {1}...
X, fixed, c = drive(g3); assert X == F({1}) and not fixed, c
seq = {F(): F({1}), F({1}): F({2}), F({2}): F({1, 2}), F({1, 2}): F()}
try: drive(lambda X: seq[F(X)]); raise SystemExit('3-cycle accepted')
except RuntimeError: pass
print('ok')
