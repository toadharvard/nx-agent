#!/usr/bin/env python3
"""Synthetic check of every verdict of the four contract gates (invented addresses, no game data)."""
import os, sys, shutil, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import gate_data, gate_call, gate_vcall, gate_xc

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def fn(size=0x40, calls=(), tails=(), icalls=(), data=(), decomp=True):
    return dict(size=size, calls=[(0, t) for t in calls], tails=[(0, t) for t in tails], icalls=list(icalls),
                data=[(0, d) for d in data], imports=[], decomp=decomp)


def ob(data=(), calls=(), other=(), vcall=()):
    return ([], set(data), set(calls), set(other), list(vcall))


def verdicts(r):
    return dict((a, v) for a, v, _ in r['rows']), r['counts']


tmp = tempfile.mkdtemp()
os.makedirs(tmp + '/out')
with open(tmp + '/out/aliases.ld', 'w') as fh:
    for a in (0x1000, 0x1100, 0x1500):
        fh.write('__fnv_%08x = 0x%08x;\n' % (a, a))
secs = {'.data': (0x1000, 0x1000, 0, 0), '.text': (0x400000, 0x1000, 0, 0)}

# ---- data
F = {'fn': {0x10: fn(), 0x20: fn(data=[0x1000]), 0x30: fn(data=[0x1000, 0x1104, 0x1500]), 0x40: fn(data=[0x1000]),
            0x50: fn(data=[0x1000]), 0x60: fn(data=[0x1000, 0x5000]), 0x70: fn(data=[0x1000])},
     'secs': secs, 'xc': [], 'cookie': 0x1500}
O = {('a', 0x10): ob(), ('a', 0x20): ob(data=[0x1000, 0x1500]), ('a', 0x30): ob(data=[0x1000]),
     ('a', 0x40): ob(data=[0x1000, 0x1100]), ('a', 0x50): ob(data=[0x1100]), ('a', 0x60): ob(data=[0x1000]),
     ('a', 0x70): ob(data=[0x1000]), ('b', 0x70): ob(data=[0x1100]), ('a', 0x99): ob(data=[0x1100]),
     ('a', 'ztv'): ob()}
F['fn'][0x80] = fn(data=[0x1500]); O[('a', 0x80)] = ob()
F['fn'][0x90] = fn(data=[0x1500]); O[('a', 0x90)] = ob()
v, c = verdicts(gate_data.run(F, O, tmp))
for k, n in (('none', 3), ('equal', 2), ('subset', 1), ('superset', 1), ('different', 2)):
    check('data ' + k, c.get(k, 0), n)
check('data cookie', c.get('cookie_refs_ignored'), 3)
check('data fail 0x40', v.get(0x40), 'superset')
check('data fail 0x70 worst of copies', v.get(0x70), 'different')
check('data fail 0x50', v.get(0x50), 'different')
check('data fail set', sorted(v), [0x40, 0x50, 0x70])
check('data fail list', gate_data.run(F, O, tmp)['fail'], ['superset', 'different'])

# ---- call
F = {'fn': {0x10: fn(), 0x20: fn(calls=[0x400100]), 0x30: fn(calls=[0x400100], tails=[0x400200]),
            0x40: fn(calls=[0x400100]), 0x50: fn(calls=[0x400100]), 0x60: fn(calls=[0x400100, 0x60 + 8]),
            0x70: fn(calls=[0x400100]), 0x80: fn(calls=[0x400100])},
     'secs': secs, 'xc': [],
     'pristine': {0x400100: frozenset(['?f@@YAXXZ']), 0x400200: frozenset(['?g@@YAXXZ']), 0x400300: frozenset(['?h@@YAXXZ'])}}
SP = [('_Z1fv', '?f@@YAXXZ', 'func', 'ref'), ('_Z1gv', '?g@@YAXXZ', 'func', 'ref'), ('_Z1hv', '?h@@YAXXZ', 'func', 'ref')]
O = {('a', 'sympair'): SP, ('b', 'sympair'): SP,
     ('a', 0x10): ob(), ('a', 0x20): ob(calls=[0x400100]), ('a', 0x30): ob(calls=[0x400100]),
     ('a', 0x40): ob(calls=[0x400100, 0x400300]), ('a', 0x50): ob(calls=[0x400300]),
     ('a', 0x60): ob(other=['__fnvnx_br_00400100_21af1be5']), ('a', 0x70): ob(other=['memset']),
     ('a', 0x80): ob(calls=[0x400100]), ('b', 0x80): ob(calls=[0x400300]), ('a', 0x11): ob()}
v, c = verdicts(gate_call.run(F, O, tmp))
for k, n in (('none', 1), ('equal', 2), ('subset', 2), ('superset', 1), ('different', 2)):
    check('call ' + k, c.get(k, 0), n)
check('call fail', sorted(v.items()), [(0x50, 'different'), (0x80, 'different')])
check('call fail list', gate_call.run(F, O, tmp)['fail'], ['different', 'undeclared'])
check('call declared', (c.get('declared'), c.get('undeclared', 0)), (8, 0))

# ---- vcall
F = {'fn': {0x10: fn(icalls=[(0, 'mem', 'eax', 8), (0, 'mem', 'eax', 0x10)]),
            0x20: fn(icalls=[(0, 'mem', 'eax', 8), (0, 'reg', 'edx', 0x10), (0, 'reg', 'edx', None),
                             (0, 'abs', None, None)]),
            0x30: fn(icalls=[(0, 'mem', 'eax', 8)]), 0x40: fn(icalls=[(0, 'mem', 'eax', 8)]),
            0x50: fn(icalls=[(0, 'mem', 'eax', 8)]), 0x60: fn()},
     'secs': secs, 'xc': []}
O = {('a', 0x10): ob(vcall=[0x10, 8]), ('a', 0x20): ob(vcall=[0xc, 0x14]), ('a', 0x30): ob(vcall=[]),
     ('a', 0x40): ob(vcall=[0x20]), ('a', 0x60): ob(vcall=[4])}
v, c = verdicts(gate_vcall.run(F, O, tmp))
for k, n in (('equal', 1), ('delta_0_or_4', 1), ('count_differs', 1), ('other', 1)):
    check('vcall ' + k, c.get(k, 0), n)
check('vcall delta key', c.get('delta:4'), 2)
check('vcall fail', sorted(v.items()), [(0x40, 'other')])
check('vcall fail list', gate_vcall.run(F, O, tmp)['fail'], ['other'])

# ---- xc
F = {'fn': {0x400100: fn(), 0x400200: fn(decomp=True), 0x400300: fn(decomp=False)}, 'secs': secs,
     'xc': [0x400100, 0x400200, 0x400300]}
O = {('a', 0x400100): ob(), ('b', 0x400100): ob(), ('a', 'ztv'): ob()}
r = gate_xc.run(F, O, tmp)
check('xc counts', dict(r['counts']), {'native': 1, 'missing_decomp': 1, 'missing_other': 1})
check('xc rows', sorted((a, vv) for a, vv, _ in r['rows']), [(0x400200, 'missing_decomp'), (0x400300, 'missing_other')])
check('xc fail', r['fail'], ['missing_decomp', 'missing_other'])

for f in fails:
    print('FAIL', f)
print('test_gates: %s' % ('FAIL' if fails else 'ok'))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fails else 0)
