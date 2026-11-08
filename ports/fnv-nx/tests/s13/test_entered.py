#!/usr/bin/env python3
"""test_entered.py - oracle-callee-unloaded from runner's arm_entered (EQ, farm baseline 588863, 0x007d1bb0).

The oracle traces a call of X, a function the native's unit defines; the native runs X untraced, so its next traced
call is the oracle's next one. When X is a leaf (no traced call of its own) native_ran has no evidence; runner records
which __fnv_<X> entry blocks the native executed (arm_entered), which is the evidence.
  E1  call-target x86 X, arm Y, x86_next Y, X in arm_entered: oracle-callee-unloaded
  E2  as E1 without arm_entered: stays call-flow (no evidence)
  E3  X in arm_entered but the native's call is not the oracle's next one: stays call-flow
  E4  call-count, the native stops before X (never entered): not oracle-callee-unloaded (native-early-exit)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = tempfile.mkdtemp(prefix='s13-entered-')
os.environ['FNVNX_DFWORK'] = WORK
os.environ['FNVNX_DFLINK'] = os.path.join(WORK, 'nolink')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import causes  # noqa: E402

RET = bytes.fromhex('c0035fd6')
BL_RET = bytes.fromhex('00000094') + RET
FN, X, Y = '00401000', '00401100', '00401300'


def cf(div):
    r = dict(source='src/t.cpp', address=FN, symbol='?f@@YAXXZ', conv='this:P/4 -> void', divergence=div,
             hint=dict(cause='call-flow'), case=dict(args={}))
    return causes.refine(r, 'call-flow')


def main():
    bodies = {'__fnv_' + FN: (BL_RET, {'__fnv_' + X}), '__fnv_' + X: (RET, set())}
    causes.native_body = lambda src, n: bodies.get(n)
    causes.native_defines = lambda src, n: n in bodies
    causes.native_callees = lambda src, a: {'__fnv_' + X, '__fnv_' + Y}
    causes.native_reaches = lambda src, n: {n}
    causes.crt_entries = lambda: set()
    causes.unbound_callees = lambda src, a: []
    causes._VFT, causes._LINKSYM = set(), {}
    base = dict(kind='call-target', index=0, x86=X, arm=Y, x86_next=Y, x86_trace=[X], arm_trace=[Y])
    cases = [('E1', dict(base, arm_entered=[FN, X]), 'oracle-callee-unloaded'),
             ('E2', dict(base), 'call-flow'),
             ('E3', dict(base, arm_entered=[FN, X], x86_next='00401500'), 'call-flow'),
             ('E4', dict(kind='call-count', x86=1, arm=0, x86_trace=[X], arm_trace=[], arm_entered=[FN]),
              'native-early-exit')]
    fails = 0
    for name, div, want in cases:
        got = cf(div)
        ok = got == want
        fails += not ok
        print('%s: %s (%s, want %s)' % (name, 'ok' if ok else 'FAIL', got, want))
    print('test_entered: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
