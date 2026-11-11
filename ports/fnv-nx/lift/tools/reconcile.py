#!/usr/bin/env python3
"""Entry-set comparison with the L1 contract team's facts (private repo /home/user/fnv-nx-build, branch l1-contract/mp4: facts.pkl, eh.pkl).
Their kinds are derived here from their own facts (priority: decomp, call, tail, eh, vtable slot, other data pointer, code-stored pointer).
Used by universe.py (run(U) -> report lines); the pickles are the owner's own artifacts, loaded only from that checkout."""
import os, sys, json, subprocess, bisect
from collections import Counter
REPO = os.environ.get('FNVNX_THEIRS', '/home/user/fnv-nx-build'); REF = 'FETCH_HEAD'
def show(f): return subprocess.run(['git', '-C', REPO, 'show', '%s:%s' % (REF, f)], capture_output=True, check=True).stdout
CHILD = """import sys, pickle, json
F = pickle.loads(open(sys.argv[1], 'rb').read()); E = pickle.loads(open(sys.argv[2], 'rb').read()); fn = F['fn']
sk = {}
for s, t in F['ptrs']: sk.setdefault(t, set()).add(E['kind'].get(s, 'other'))
last = lambda c: c if isinstance(c, int) else c[-1]
print(json.dumps({'fn': [[a, int(bool(v['decomp'])), v['end'], v['size']] for a, v in sorted(fn.items())],
  'called': sorted({c[1] for v in fn.values() for c in v['calls']}), 'tails': sorted({last(t) for v in fn.values() for t in v['tails']}),
  'cp': sorted({last(c) for v in fn.values() for c in v['codeptrs']}), 'sk': [[t, sorted(k)] for t, k in sorted(sk.items())]}))
"""
def load():
    d = os.environ.get('FNVNX_SCRATCH', '/home/user/l3work'); os.makedirs(d, exist_ok=True)
    for f in ('facts.pkl', 'eh.pkl'): open(os.path.join(d, 'theirs_' + f), 'wb').write(show(f))
    r = subprocess.run([sys.executable, '-I', '-c', CHILD, os.path.join(d, 'theirs_facts.pkl'), os.path.join(d, 'theirs_eh.pkl')], capture_output=True, check=True)
    return json.loads(r.stdout)
def run(U):
    try: J = load()
    except Exception as ex: return ['reconcile: skipped (%s)' % str(ex)[:60]]
    fn = {a: {'decomp': dc, 'end': e, 'size': sz} for a, dc, e, sz in J['fn']}; TH, OU = set(fn), set(U.entries); called = set(J['called']); tails = set(J['tails']); cp = set(J['cp'])
    sk = {t: set(k) for t, k in J['sk']}
    def tk(a):
        if fn[a]['decomp']: return 'decomp'
        if a in called: return 'call'
        if a in tails: return 'tail'
        k = sk.get(a, set())
        for x in ('eh_unwind', 'eh_catch', 'xc', 'slot', 'other'):
            if x in k: return 'eh' if x.startswith(('eh', 'xc')) else 'vtable-slot' if x == 'slot' else 'data-ptr'
        return 'code-stored-ptr' if a in cp else 'other'
    pk = lambda a: next(k for k in U.PRI if k in U.kinds[a])
    dts = sorted(a for a in TH if fn[a]['decomp']); nts = sorted(a for a in TH if not fn[a]['decomp'])
    def inside(a, ts):
        i = bisect.bisect_right(ts, a) - 1; return i >= 0 and ts[i] != a and a < fn[ts[i]]['end']
    def where(a): return 'decomp' if inside(a, dts) else 'non-decomp' if inside(a, nts) else 'none'
    os_, to = sorted(OU - TH), sorted(TH - OU); L = []
    L.append('reconcile vs l1-contract/mp4: theirs %d (decomp %d), ours %d (decomp %d), common %d, only-ours %d, only-theirs %d' % (
        len(TH), sum(1 for a in TH if fn[a]['decomp']), len(OU), len(U.funcs), len(OU & TH), len(os_), len(to)))
    why = {'inner': 'label inside a decomp function (ptr/eh/tail/cont target); they keep the function as one unit',
           'eh-cont': 'catch-continuation label (mov eax,imm; ret target) inside a decomp function; they list only the funclets',
           'ptr': 'target of a relocated slot stored in .text (dispatch table); they take data-section pointers only',
           'call': 'call target from code they do not scan (CRT / non-decomp tails)', 'entry': 'PE entry point (CRT startup)',
           'tail': 'jmp/jcc target leaving the entry extent, inside CRT regions they treat as one function body (not in their tail lists)'}
    c = Counter(pk(a) for a in os_)
    for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
        xs = [a for a in os_ if pk(a) == k]; w = Counter(where(a) for a in xs)
        L.append('  only-ours %-7s %5d (inside their decomp fn %d, non-decomp fn %d, none %d): %s | %s' % (k, n, w['decomp'], w['non-decomp'], w['none'], ' '.join('%x' % a for a in xs[:3]), why.get(k, '?')))
    c = Counter(tk(a) for a in to)
    for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
        xs = [a for a in to if tk(a) == k]
        L.append('  only-theirs %-8s %4d (size 0 in theirs: %d; insn start in our extents: %d): %s | %s' % (k, n, sum(1 for a in xs if fn[a]['size'] == 0), sum(1 for a in xs if U.cov[a - U.T0] == 1) if all(U.intext(a) for a in xs) else -1,
            ' '.join('%x' % a for a in xs[:3]), {'tail': 'jmp target: our non-decomp walk follows an unconditional jmp into the callee as the same extent, they list it as a function', 'data-ptr': 'relocated rgodf pointers of DIDATAFORMAT records: data (class 3) by consumer rule, they take them as functions'}.get(k, '?')))
    cs = Counter(tk(a) for a in TH & OU)
    L.append('  common by their kind: %s' % dict(sorted(cs.items(), key=lambda kv: (-kv[1], kv[0]))))
    return L
