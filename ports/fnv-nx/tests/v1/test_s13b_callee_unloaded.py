#!/usr/bin/env python3
"""test_s13b_callee_unloaded.py - V1 (D14) against S13b a93f5e6 `oracle-callee-unloaded` (artifact): "the oracle
intercepts X (canned result) while the native object defines X and runs its body: X is not in the oracle's code
set".

The rule (causes._rule_callflow) fires when X, the first call the native does not make, is defined in the native
object and named by the function. That is static evidence. It never checks that the native ran X on this input,
the same gap as oracle-callee-store C1-C3 (test_s13b_callee_store.py). Records in the shapes runner.compare writes,
with the native object facts faked as tests/s13/test_causes.py test_callflow does:
  U1  call-count, x86 [X], native []: the native returned before X (a wrong branch, a real bug). X's native body
      makes no traced call, so the record cannot tell this from "the native ran X natively": no evidence either way
  U2  as U1, but X's native body calls Y, a callee outside the unit (traced when made), and the native trace has
      no Y: the native never ran X. A real native-early-exit, with evidence against the artifact in the record
  U3  control: call-target, x86 X, native Y (X's body ran natively and made its call Y): oracle-callee-unloaded
A case is a DEFECT when a real-bug shape (U1, U2) is classified as an artifact or by-design.
  python3 -I tests/v1/test_s13b_callee_unloaded.py   -> one line per case, exit 1 when any shows a defect
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    WORK = tempfile.mkdtemp(prefix='v1-s13bu-')
except OSError as e:
    print('mktemp failed: %s' % e)
    sys.exit(2)
if not WORK or not os.path.isdir(WORK):
    print('mktemp failed')
    sys.exit(2)
os.environ['FNVNX_DFWORK'] = WORK
os.environ['FNVNX_DFLINK'] = os.path.join(WORK, 'nolink')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import causes  # noqa: E402

RET = bytes.fromhex('c0035fd6')                 # ret
BL_RET = bytes.fromhex('00000094') + RET        # bl ...; ret
FN, X, Y = '00401000', '00401100', '00401300'
fails = 0


def cf(div):
    r = dict(source='src/t.cpp', address=FN, symbol='?f@@YAXXZ', conv='this:P/4 -> void', divergence=div,
             hint=dict(cause='call-flow'), case=dict(args={}))
    return causes.refine(r, 'call-flow')


def say(case, got, real_bug, text):
    global fails
    cls = causes.POLICY.get(got, ('?',))[0]
    bad = real_bug and cls in ('artifact', 'by-design')
    fails += bad
    print('%s: %s - %s -> %s (%s)' % (case, 'DEFECT' if bad else 'ok', text, got, cls))


def main():
    if 'oracle-callee-unloaded' not in causes.POLICY:
        print('skip: no oracle-callee-unloaded in causes.POLICY')
        return 0
    names = ('native_body', 'native_defines', 'native_callees', 'native_reaches', 'crt_entries', 'unbound_callees')
    saved = {n: getattr(causes, n) for n in names if hasattr(causes, n)}
    bodies = {'__fnv_' + FN: (BL_RET, {'__fnv_' + X})}
    reaches = {}
    try:
        causes.native_body = lambda src, n: bodies.get(n)
        causes.native_defines = lambda src, n: n in bodies
        causes.native_callees = lambda src, a: {'__fnv_' + X}
        causes.native_reaches = lambda src, n: reaches.get(n, {n})
        causes.crt_entries = lambda: set()
        causes.unbound_callees = lambda src, a: []
        causes._VFT, causes._LINKSYM = set(), {}
        early = dict(kind='call-count', x86=1, arm=0, x86_trace=[X], arm_trace=[])
        # U1: X defined in the unit, its body makes no call
        bodies['__fnv_' + X] = (RET, set())
        say('U1', cf(early), True, 'native returned before the unit-local X (no traced call in X): no evidence the '
            'native ran X')
        # U2: X's body calls Y (outside the unit, traced); the native trace has no Y
        bodies['__fnv_' + X] = (BL_RET, {'__fnv_' + Y})
        reaches['__fnv_' + X] = {'__fnv_' + X, '__fnv_' + Y}
        say('U2', cf(early), True, 'X\'s native body calls Y, the native trace has no Y: the native never ran X')
        # U3: control
        got = cf(dict(kind='call-target', x86=X, arm=Y, x86_trace=[X], arm_trace=[Y]))
        print('U3: %s - control: x86 X (canned), native ran X and made its call Y -> %s' % (
            'ok' if got == 'oracle-callee-unloaded' else 'note (rule changed)', got))
    finally:
        for n, f in saved.items():
            setattr(causes, n, f)
        causes._VFT = causes._LINKSYM = None
    return 1 if fails else 0


if __name__ == '__main__':
    try:
        rc = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(rc)
