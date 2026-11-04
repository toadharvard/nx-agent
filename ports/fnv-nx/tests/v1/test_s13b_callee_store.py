#!/usr/bin/env python3
"""test_s13b_callee_store.py - V1 (D14) against S13b f636994c (J-0149): "oracle-callee-store - the first differing
store is made by a callee the oracle runs (preloaded or followed) while the native calls it out of line and the harness
intercepts it: no side effects natively" (causes.POLICY: artifact, so report.py moves the function out of the hard
set).

The rule (causes._rule_effects) checks only that the first differing store's x86 pc lies outside [address, address +
size) and that the native stored nothing at that byte. It never checks that the native called that callee at all, or
that the harness intercepted the call. Records built by hand in the shapes runner.compare / effects.py write (as
tests/s13/test_causes.py does), each one a real native defect that leaves exactly that effect:
  C1  native-inlined: the native never names the callee (GCC inlined it), so the callee's code DID run natively and
      its side effects are the native's own; the native's inlined store went elsewhere (another field offset)
  C2  native-early-exit: the native makes a prefix of the oracle's calls and stops (a wrong branch); the callee that
      stores is one the native never reached
  C3  x86-only-store (mem-write, only the oracle writes): the native skipped the call (e.g. a signedness bug in its
      guard), with no call divergence recorded before the store
  C4  control: S13b's own case (the record says nothing against interception) stays oracle-callee-store
A case is a DEFECT when a real-bug shape is classified as an artifact.
  python3 -I tests/v1/test_s13b_callee_store.py   -> one line per case, exit 1 when any shows a defect
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = tempfile.mkdtemp(prefix='v1-s13b-')   # raises when it cannot: never an empty or relative path
os.environ['FNVNX_DFWORK'] = WORK
os.environ['FNVNX_DFLINK'] = os.path.join(WORK, 'nolink')   # no link: no static callee facts
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import causes  # noqa: E402

FN, SIZE = '00401000', 0x40           # the function under test: x86 0x00401000..0x00401040
CALLEE_PC = '00402010'                # a store inside a callee at 0x00402000
fails = 0


def say(case, cause, want_not_artifact, text):
    global fails
    cls = causes.POLICY.get(cause, ('?',))[0]
    bad = want_not_artifact and cls in ('artifact', 'by-design')
    fails += bad
    print('%s: %s - %s -> %s (%s)' % (case, 'DEFECT' if bad else 'ok', text, cause, cls))


def rec(div, f, cause):
    return dict(source='src/t.cpp', address=FN, symbol='?f@@YAXPAUS@@@Z', divergence=div, hint=dict(cause=cause),
                case={}, _file=f, _size=SIZE)


def effect(addr='03000010'):
    # effects.py analyse(): the oracle's final store at addr came from the callee's pc, the native stored nothing there
    return dict(first='store', store=dict(addr=addr, n=4, x86=dict(v='01', size=4, pc=CALLEE_PC), arm=None,
                                          word=dict(x86='constant 0x1', arm='zero')))


def main():
    causes.EFFECTS.clear()
    for n in ('c1.json', 'c2.json', 'c3.json', 'c4.json'):
        causes.EFFECTS[n] = effect()
    # C1: the oracle calls 0x00402000, the native never names it (inlined) and wrote the field 4 bytes higher
    c1 = rec(dict(kind='call-count', x86=1, arm=0, missing=['00402000']), 'c1.json', 'native-inlined')
    say('C1', causes.refine(c1, 'native-inlined'), True,
        'native-inlined callee (its code ran natively), its store at another offset')
    # C2: the native stops after a prefix of the oracle's calls
    c2 = rec(dict(kind='call-count', x86=2, arm=1), 'c2.json', 'native-early-exit')
    say('C2', causes.refine(c2, 'native-early-exit'), True, 'native-early-exit before the callee that stores')
    # C3: only the oracle writes the word (x86-only-store), the native never made the call
    w1, w0 = '01000000', '00000000'
    c3 = rec(dict(kind='mem-write', addr='03000010', ndiff=4, only_x86=4, only_arm=0, x86=w1, arm=w0), 'c3.json',
             'unclassified')
    say('C3', causes.refine(c3, 'unclassified'), True, 'x86-only-store: the native skipped the call')
    # C4: S13b's control, no record-level evidence either way
    c4 = rec(dict(kind='mem-write', addr='03000010', ndiff=4, only_x86=1, only_arm=1, x86=w1, arm=w0), 'c4.json',
             'unclassified')
    got = causes.refine(c4, 'unclassified')
    print('C4: %s - control: %s' % ('ok' if got == 'oracle-callee-store' else 'note (rule changed)', got))
    return 1 if fails else 0


if __name__ == '__main__':
    try:
        rc = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(rc)
