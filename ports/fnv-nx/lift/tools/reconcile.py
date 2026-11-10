#!/usr/bin/env python3
"""Entry-set comparison with the L1 contract team's facts (private repo /home/user/fnv-nx-build, branch l1-contract/mp4: facts.pkl, eh.pkl).
Their kinds are derived here from their own facts (priority: decomp, call, tail, eh, vtable slot, other data pointer, code-stored pointer).
Used by universe.py (run(U) -> report lines); the pickles are the owner's own artifacts, loaded only from that checkout."""
import os, pickle, subprocess, bisect
from collections import Counter
REPO = os.environ.get('FNVNX_THEIRS', '/home/user/fnv-nx-build'); REF = 'FETCH_HEAD'
def show(f): return subprocess.run(['git', '-C', REPO, 'show', '%s:%s' % (REF, f)], capture_output=True, check=True).stdout
def run(U):
    try: F, E = pickle.loads(show('facts.pkl')), pickle.loads(show('eh.pkl'))
    except Exception as ex: return ['reconcile: skipped (%s)' % str(ex)[:60]]
    fn = F['fn']; TH, OU = set(fn), set(U.entries); called = {c[1] for v in fn.values() for c in v['calls']}
    tails = {t if isinstance(t, int) else t[-1] for v in fn.values() for t in v['tails']}; sk = {}
    for s, t in F['ptrs']: sk.setdefault(t, set()).add(E['kind'].get(s, 'other'))
    cp = {c if isinstance(c, int) else c[-1] for v in fn.values() for c in v['codeptrs']}
    def tk(a):
        if fn[a]['decomp']: return 'decomp'
        if a in called: return 'call'
        if a in tails: return 'tail'
        k = sk.get(a, set())
        for x in ('eh_unwind', 'eh_catch', 'xc', 'slot', 'other'):
            if x in k: return 'eh' if x.startswith(('eh', 'xc')) else 'vtable-slot' if x == 'slot' else 'data-ptr'
        return 'code-stored-ptr' if a in cp else 'other'
    pk = lambda a: next(k for k in U.PRI if k in U.kinds[a])
    ts = sorted(TH); te = {a: fn[a]['end'] for a in TH}
    def inside(a):
        i = bisect.bisect_right(ts, a) - 1; return i >= 0 and ts[i] != a and a < te[ts[i]]
    os_, to = sorted(OU - TH), sorted(TH - OU); L = []
    L.append('reconcile vs l1-contract/mp4: theirs %d (decomp %d), ours %d (decomp %d), common %d, only-ours %d, only-theirs %d' % (
        len(TH), sum(1 for a in TH if fn[a]['decomp']), len(OU), len(U.funcs), len(OU & TH), len(os_), len(to)))
    why = {'inner': 'label in the interior of a decomp function (ptr/eh/tail target); they keep the function as one unit',
           'eh-cont': 'catch-continuation label (mov eax,imm;ret) in a function body; they have no continuation entries',
           'ptr': 'pointer slot in .text/CRT region outside their function universe', 'call': 'call target in the CRT region their scan does not enter',
           'entry': 'PE entry point (CRT startup), outside their function universe'}
    c = Counter(pk(a) for a in os_)
    for k, n in c.most_common():
        xs = [a for a in os_ if pk(a) == k]
        L.append('  only-ours %-8s %5d (%d inside one of their functions): %s | %s' % (k, n, sum(1 for a in xs if inside(a)), ' '.join('%x' % a for a in xs[:3]), why.get(k, '?')))
    c = Counter(tk(a) for a in to)
    for k, n in c.most_common():
        xs = [a for a in to if tk(a) == k]
        L.append('  only-theirs %-6s %4d (size 0 in theirs: %d; insn start in our extents: %d): %s | %s' % (k, n, sum(1 for a in xs if fn[a]['size'] == 0), sum(1 for a in xs if U.cov[a - U.T0] == 1) if all(U.intext(a) for a in xs) else -1,
            ' '.join('%x' % a for a in xs[:3]), 'jmp/tail target: our non-decomp walk follows an unconditional jmp into the callee as the same extent, they list it as a function' if k == 'tail' else '?'))
    cs = Counter(tk(a) for a in TH & OU)
    L.append('  common by their kind: %s' % dict(cs.most_common()))
    return L
