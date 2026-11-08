#!/usr/bin/env python3
"""test_basector.py - oracle-callee-unloaded when X's native body reaches a secondary-base entry (EQ, farm baseline
588863, the global-object initializers 0x00f382d0 ... 0x00f4c610).

A dynamic initializer calls the unit-local constructor X (exe 0x0045cfe0). The oracle traces that one call; the
native runs X untraced, and X's body calls the exe's first callee Y (exe 0x0045d013 call 0x404920) through the
entry `__fnv_<Y>_C2` (the base-object constructor), which the runner traces as <Y>. native_ran only knew the plain
`__fnv_<Y>` spelling, so the evidence was missed and the record stayed call-flow.
  B1  X reaches __fnv_Y_C2 (not defined in the unit), the native trace holds Y: oracle-callee-unloaded
  B2  as B1 with plain __fnv_Y: oracle-callee-unloaded (unchanged)
  B3  as B1, the native trace holds no Y: stays call-flow (no evidence the native ran X)
Second shape (0x00905330 calls BSSimpleList::IsEmpty 0x008256d0, a leaf a provider of the unit image defines): the
oracle's only traced call is X, the native object references `__fnv_<X>` without defining it, the run entered it:
  L1  call-count, x86 [X], native [], X in arm_entered: oracle-callee-unloaded
  L2  as L1 without arm_entered: native-early-exit (no evidence)
  L3  as L1 with a second oracle call after X: native-early-exit (the native lacks that call too)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = tempfile.mkdtemp(prefix='s13-basector-')
os.environ['FNVNX_DFWORK'] = WORK
os.environ['FNVNX_DFLINK'] = os.path.join(WORK, 'nolink')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import causes  # noqa: E402

RET = bytes.fromhex('c0035fd6')
BL_RET = bytes.fromhex('00000094') + RET
FN, X, Y, Z = '00401000', '00401100', '00401300', '00401500'


def cf(div):
    r = dict(source='src/t.cpp', address=FN, symbol='?f@@YAXXZ', conv='this:P/4 -> void', divergence=div,
             hint=dict(cause='call-flow'), case=dict(args={}))
    return causes.refine(r, 'call-flow')


SECOND = [False]


def main():
    bodies = {'__fnv_' + FN: (BL_RET, {'__fnv_' + X}), '__fnv_' + X: (BL_RET, set())}
    reach = {}
    causes.native_body = lambda src, n: bodies.get(n)
    causes.native_defines = lambda src, n: n in bodies and n != '__fnv_' + X if SECOND[0] else n in bodies
    causes.native_callees = lambda src, a: {'__fnv_' + X}
    causes.native_reaches = lambda src, n: reach.get(n, {n})
    causes.crt_entries = lambda: set()
    causes.unbound_callees = lambda src, a: []
    causes._VFT, causes._LINKSYM = set(), {}
    div = dict(kind='call-target', index=0, x86=X, arm=Y, x86_next=Z, x86_trace=[X], arm_trace=[Y])
    fails = 0
    for name, r, d, want in [('B1', {'__fnv_' + Y + '_C2'}, div, 'oracle-callee-unloaded'),
                             ('B2', {'__fnv_' + Y}, div, 'oracle-callee-unloaded'),
                             ('B3', {'__fnv_' + Y + '_C2'}, dict(div, arm=Z, arm_trace=[Z]), 'call-flow')]:
        reach['__fnv_' + X] = r
        got = cf(d)
        ok = got == want
        fails += not ok
        print('%s: %s (%s, want %s)' % (name, 'ok' if ok else 'FAIL', got, want))
    SECOND[0] = True
    reach['__fnv_' + X] = {'__fnv_' + X}
    lc = dict(kind='call-count', x86=2, arm=0, x86_trace=[X], arm_trace=[])
    for name, d, want in [('L1', dict(lc, arm_entered=[FN, X]), 'oracle-callee-unloaded'),
                          ('L2', dict(lc), 'native-early-exit'),
                          ('L3', dict(lc, x86_trace=[X, Z], arm_entered=[FN, X]), 'native-early-exit')]:
        got = cf(d)
        ok = got == want
        fails += not ok
        print('%s: %s (%s, want %s)' % (name, 'ok' if ok else 'FAIL', got, want))
    print('test_basector: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
