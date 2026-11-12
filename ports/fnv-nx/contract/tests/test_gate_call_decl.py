#!/usr/bin/env python3
"""Synthetic check of the declaration-equality rule of gate n2_call (invented addresses and names, no game data)."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import gate_call
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

fails = testcase.FAILED
F_ADDR, C_ADDR, D_ADDR, X87, NOSYM, DATA = 0x400100, 0x400200, 0x400300, 0x400400, 0x400500, 0x500000
N1 = {'fn': {F_ADDR: dict(n1=True, sym='?f@@YAXH@Z'), C_ADDR: dict(n1=True, sym='_cfun'),
             X87: dict(n1=True, sym='__ftol2'), D_ADDR: dict(n1=False, sym='?notn1@@YAXXZ')},
      'sym': {(0, '?f@@YAXH@Z'): F_ADDR, (3, '?g@@3HA'): DATA, (0, '_cfun'): C_ADDR, (1, '__ftol2'): X87,
              (0, '?abs@@3HA'): 1 << 32, (2, 5, '?static@@3HA'): NOSYM}}
P = gate_call.pristine_names(N1)
case('pristine fn', got=P.get(F_ADDR), want=frozenset(['?f@@YAXH@Z']))
case('pristine data', got=P.get(DATA), want=frozenset(['?g@@3HA']))
case('pristine skips non-N1 function', got=D_ADDR in P, want=False)
case('pristine skips static and absolute pins', got=(NOSYM in P, 1 << 32 in P), want=(False, False))


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
case('pos rows', got=r['rows'], want=[])
case('pos counts', got=(r['counts'].get('declared'), r['counts'].get('undeclared', 0)), want=(1, 0))
case('pos zero edges (no section needed)', got=run(None, calls=())[0]['rows'], want=[])
case('pos data edge', got=run([REF_F, ('g', '?g@@3HA', 'data', 'ref')], data=[DATA])[0]['rows'], want=[])
case('pos cookie dropped', got=run([REF_F], data=[0x9000])[0]['rows'], want=[])
case('pos bridge address', got=run([REF_F], calls=(F_ADDR,), other=['__fnvnx_br_00400100_21af1be5'])[0]['rows'], want=[])
case('pos def row of another symbol ignored', got=run([REF_F, ('h', '?h@@YAXXZ', 'func', 'def')])[0]['rows'], want=[])

# negatives, one per not-provable condition; the row is `undeclared`, fail lists it
r, d = run(None)
case('neg no section', got=len(d), want=1)
case('neg no section text', got='no .fnvnx.sympair section' in d[0], want=True)
r, d = run([])
case('neg no ref row', got=('no ref row has the pristine name' in d[0]) if d else None, want=True)
r, d = run([('_Z1fi', '?f@@YAXH@Z', 'func', 'def')])
case('neg def row only', got=len(d), want=1)
r, d = run([('_Z1fi', 'unnamed:lambda', 'unnamed:lambda', 'ref')])
case('neg unnamed kind', got=len(d), want=1)
r, d = run([('_Z1fi', '?f@@YAXH@Z', 'unnamed:lambda', 'ref')])
case('neg unnamed kind with the pristine name', got=('only an unnamed' in d[0]) if d else None, want=True)
r, d = run([('_Z1fl', '?f@@YAXJ@Z', 'func', 'ref')])                  # (a)(c): declaration differs from the exe contract
case('neg name mismatch', got=('no ref row has the pristine name' in d[0]) if d else None, want=True)
r, d = run([('_Z1fi', '?f@@YAXH@Z', 'func', 'ref'), ('_Z1fl', '?f@@YAXJ@Z', 'func', 'ref')])
case('pos extra mismatching row does not cover the edge', got=d, want=[])
r, d = run([('cfun', '_cfun', 'func', 'ref')], calls=(C_ADDR,))        # extern "C": undecorated pristine name
case('neg undecorated pristine', got=('undecorated' in d[0]) if d else None, want=True)
r, d = run([('ftol', '__ftol2', 'func', 'ref'), REF_F], calls=(X87,))  # x87 CRT helper, a row exists, still no types
case('neg x87 helper', got=('undecorated' in d[0]) if d else None, want=True)
r, d = run([REF_F], calls=(NOSYM,))
case('neg no pristine symbol', got=('no pristine COFF symbol' in d[0]) if d else None, want=True)
r, d = run([REF_F], calls=(D_ADDR,))
case('neg non-N1 target has no pristine symbol', got=('no pristine COFF symbol' in d[0]) if d else None, want=True)
r, d = run([REF_F], data=[NOSYM])
case('neg data edge without symbol', got=len(d), want=1)
r, d = run([REF_F, ('g', '?g@@3HA', 'data', 'ref')], calls=(F_ADDR, C_ADDR))   # one bad edge among good ones
case('neg one bad edge among several', got=('0x00400200' in d[0]) if d else None, want=True)
r, d = run([REF_F], calls=(F_ADDR,), extra={('b', 0x10): ([], set(), {F_ADDR}, set(), [])})
case('neg worst of several copies (object b has no section)', got=(len(d), d[0][:1] if d else None), want=(1, 'b'))
case('fail list', got=r['fail'], want=['different', 'undeclared'])
case('row verdict', got={v for _, v, _ in r['rows']}, want={'undeclared'})
r, d = run(None, calls=(0x400100,))
case('row address is the exe function', got=[a for a, _, _ in r['rows']], want=[0x10])
# facts without `pristine` is a defect of the caller, not a verdict
try:
    gate_call.run({'fn': {0x10: dict(size=1, calls=[], tails=[])}, 'secs': {}, 'xc': []}, {('a', 0x10): ([], set(), set(), set(), [])}, None)
    case('missing facts[pristine]', got='no error', want='ValueError')
except ValueError:
    pass

for f in fails:
    print('FAIL', f)
print('test_gate_call_decl: %s' % ('FAIL' if fails else 'ok'))
sys.exit(1 if fails else 0)
