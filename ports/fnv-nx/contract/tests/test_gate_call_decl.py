#!/usr/bin/env python3
"""Synthetic check of the declaration-equality rule of gate n2_call (invented addresses and names, no game data)."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import gate_call

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


F_ADDR, C_ADDR, D_ADDR, X87, NOSYM, DATA = 0x400100, 0x400200, 0x400300, 0x400400, 0x400500, 0x500000
N1 = {'fn': {F_ADDR: dict(n1=True, sym='?f@@YAXH@Z'), C_ADDR: dict(n1=True, sym='_cfun'),
             X87: dict(n1=True, sym='__ftol2'), D_ADDR: dict(n1=False, sym='?notn1@@YAXXZ')},
      'sym': {(0, '?f@@YAXH@Z'): F_ADDR, (3, '?g@@3HA'): DATA, (0, '_cfun'): C_ADDR, (1, '__ftol2'): X87,
              (0, '?abs@@3HA'): 1 << 32, (2, 5, '?static@@3HA'): NOSYM}}
P = gate_call.pristine_names(N1)
check('pristine fn', P.get(F_ADDR), frozenset(['?f@@YAXH@Z']))
check('pristine data', P.get(DATA), frozenset(['?g@@3HA']))
check('pristine skips non-N1 function', D_ADDR in P, False)
check('pristine skips static and absolute pins', (NOSYM in P, 1 << 32 in P), (False, False))


def run(row_sets, calls=(F_ADDR,), data=(), other=(), extra=(), cookie=0x9000):
    """one function 0x10 in object 'a' calling `calls`; sympair rows `row_sets` (None: object has no section)."""
    fn = {0x10: dict(size=0x40, calls=[(0, t) for t in calls], tails=[])}
    obj = {('a', 0x10): ([], set(data), set(calls), set(other), [])}
    if row_sets is not None:
        obj[('a', 'sympair')] = row_sets
    obj.update(extra)
    r = gate_call.run({'fn': fn, 'secs': {}, 'xc': [], 'pristine': P, 'cookie': cookie}, obj, None)
    return r, [d for _, v, d in r['rows'] if v == 'undeclared']


REF_F = ('_Z1fi', '?f@@YAXH@Z', 'func', 'ref')

# positive: ref row byte-equal to the pristine name; also zero edges, data edge, cookie, bridge address
r, d = run([REF_F])
check('pos rows', r['rows'], [])
check('pos counts', (r['counts'].get('declared'), r['counts'].get('undeclared', 0)), (1, 0))
check('pos zero edges (no section needed)', run(None, calls=())[0]['rows'], [])
check('pos data edge', run([REF_F, ('g', '?g@@3HA', 'data', 'ref')], data=[DATA])[0]['rows'], [])
check('pos cookie dropped', run([REF_F], data=[0x9000])[0]['rows'], [])
check('pos bridge address', run([REF_F], calls=(F_ADDR,), other=['__fnvnx_br_00400100_21af1be5'])[0]['rows'], [])
check('pos def row of another symbol ignored', run([REF_F, ('h', '?h@@YAXXZ', 'func', 'def')])[0]['rows'], [])

# negatives, one per not-provable condition; the row is `undeclared`, fail lists it
r, d = run(None)
check('neg no section', len(d), 1)
check('neg no section text', 'no .fnvnx.sympair section' in d[0], True)
r, d = run([])
check('neg no ref row', ('no ref row has the pristine name' in d[0]) if d else None, True)
r, d = run([('_Z1fi', '?f@@YAXH@Z', 'func', 'def')])
check('neg def row only', len(d), 1)
r, d = run([('_Z1fi', 'unnamed:lambda', 'unnamed:lambda', 'ref')])
check('neg unnamed kind', len(d), 1)
r, d = run([('_Z1fi', '?f@@YAXH@Z', 'unnamed:lambda', 'ref')])
check('neg unnamed kind with the pristine name', ('only an unnamed' in d[0]) if d else None, True)
r, d = run([('_Z1fl', '?f@@YAXJ@Z', 'func', 'ref')])                  # (a)(c): declaration differs from the exe contract
check('neg name mismatch', ('no ref row has the pristine name' in d[0]) if d else None, True)
r, d = run([('_Z1fi', '?f@@YAXH@Z', 'func', 'ref'), ('_Z1fl', '?f@@YAXJ@Z', 'func', 'ref')])
check('pos extra mismatching row does not cover the edge', d, [])
r, d = run([('cfun', '_cfun', 'func', 'ref')], calls=(C_ADDR,))        # extern "C": undecorated pristine name
check('neg undecorated pristine', ('undecorated' in d[0]) if d else None, True)
r, d = run([('ftol', '__ftol2', 'func', 'ref'), REF_F], calls=(X87,))  # x87 CRT helper, a row exists, still no types
check('neg x87 helper', ('undecorated' in d[0]) if d else None, True)
r, d = run([REF_F], calls=(NOSYM,))
check('neg no pristine symbol', ('no pristine COFF symbol' in d[0]) if d else None, True)
r, d = run([REF_F], calls=(D_ADDR,))
check('neg non-N1 target has no pristine symbol', ('no pristine COFF symbol' in d[0]) if d else None, True)
r, d = run([REF_F], data=[NOSYM])
check('neg data edge without symbol', len(d), 1)
r, d = run([REF_F, ('g', '?g@@3HA', 'data', 'ref')], calls=(F_ADDR, C_ADDR))   # one bad edge among good ones
check('neg one bad edge among several', ('0x00400200' in d[0]) if d else None, True)
r, d = run([REF_F], calls=(F_ADDR,), extra={('b', 0x10): ([], set(), {F_ADDR}, set(), [])})
check('neg worst of several copies (object b has no section)', (len(d), d[0][:1] if d else None), (1, 'b'))
check('fail list', r['fail'], ['different', 'undeclared'])
check('row verdict', {v for _, v, _ in r['rows']}, {'undeclared'})
r, d = run(None, calls=(0x400100,))
check('row address is the exe function', [a for a, _, _ in r['rows']], [0x10])
# facts without `pristine` is a defect of the caller, not a verdict
try:
    gate_call.run({'fn': {0x10: dict(size=1, calls=[], tails=[])}, 'secs': {}, 'xc': []}, {('a', 0x10): ([], set(), set(), set(), [])}, None)
    check('missing facts[pristine]', 'no error', 'ValueError')
except ValueError:
    pass

for f in fails:
    print('FAIL', f)
print('test_gate_call_decl: %s' % ('FAIL' if fails else 'ok'))
sys.exit(1 if fails else 0)
