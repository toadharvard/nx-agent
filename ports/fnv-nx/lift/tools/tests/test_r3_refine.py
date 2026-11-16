"""R3 refinement termination (modlift.run): the exclusion-set sequence of an antitone map G, driven by a model _run.
G(X) = the sites that stay displacement-only when X is excluded. Cases: a fixed point; a 2-cycle where the sequence reaches L first; a 2-cycle where U is reached first (one extra round with L);
a 3-cycle is an error. The answer X must satisfy X <= Xo of its own round."""
import sys, os, tempfile, io, contextlib
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')); sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import modlift


def drive(G):
    calls = []
    def fake_run(arg, cfg, X, _):
        Xo = frozenset(G(X)); calls.append((X, Xo)); return 0, Xo, None, None, 0, 0, 0
    d = tempfile.mkdtemp(); os.makedirs(d + '/exe')
    orig = (modlift._run, modlift.residue.entries_on_data, modlift.residue.data_proof)
    modlift._run = fake_run; modlift.residue.entries_on_data = lambda od: []; modlift.residue.data_proof = lambda od, a: ''
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            modlift.run('exe', SimpleNamespace(exe='x', facts_out=d))
    finally:
        modlift._run, modlift.residue.entries_on_data, modlift.residue.data_proof = orig
    return calls


F = frozenset
# fixed point: G(X) = {1} regardless of X
c = drive(lambda X: {1}); assert c[-1][0] <= c[-1][1] and len(c) == 2, c
# antitone 2-cycle: G(empty) = {1,2} (= U), G({1,2}) = {1} (= L'), G({1}) = {1,2}, ... L={1}, U={1,2}: G(L)=U, G(U)=L
def g2(X): return {1, 2} if X == F() or X == F({1}) else {1}
c = drive(g2); last = c[-1]; assert last[0] == F({1}) and last[0] <= last[1], c
# sequence reaches L first: G(empty) = {1}, G({1}) = {1,2}, G({1,2}) = {1}: S = {}, {1}, {1,2}, {1}...
def g3(X): return {1} if X in (F(), F({1, 2})) else {1, 2}
c = drive(g3); last = c[-1]; assert last[0] == F({1}) and last[0] <= last[1], c
# a cycle of length 3 is an error
seq = {F(): F({1}), F({1}): F({2}), F({2}): F({1, 2}), F({1, 2}): F()}
try: drive(lambda X: seq[F(X)]); raise SystemExit('3-cycle accepted')
except RuntimeError: pass
print('ok')
