#!/usr/bin/env python3
"""test_s13b_unclassified_artifacts.py - V1 (D14) against S13b 524ab2e: the four ARTIFACT bucket rules of
causes._rule_unclassified (ctor-this, canned-this, native-wild-private, stub-normalized).

Each rule decides from the values in the record alone (static evidence, no dynamic check), the same gap as
oracle-callee-store C1-C3 and oracle-callee-unloaded U1-U2. Records below are real native bugs in the shapes
runner.compare writes; a case is a DEFECT when the rule still labels it artifact or by-design:
  A1 stub-normalized: the native forgets a flag bit 0x800 of a stored flags word (x86 01 08 00 00, native
     01 00 00 00). The rule takes any byte 0x08 -> 0x00 at any offset of any store: it never checks that the
     word is a 0x08xxxxxx code-stub pointer (byte 3 of an aligned word) nor that the address is a data word
     arm64emu rewrote
  A2 ctor-this (return): `p = alloc(); return p->Init();` where Init returns another object (a factory); the
     native drops Init's result and returns p. The rule never checks that call m got the block of call j as this,
     and for return / mem-write the later call m may be any of 64 indices, made or not
  A3 canned-this: the oracle passes GetFoo()'s pointer result (canned eax of call 0) as `this`, the native passes
     a frame temporary because its declaration of GetFoo returns the struct by value (hidden-pointer ABI
     mismatch, an S2c-abi real bug). Only "earlier canned word vs native stack address" is checked
  A4 native-wild-private: the native calls through the wrong vtable slot and reads a code pointer from the random
     background into the private range; x86 called the right slot and faulted nowhere. The rule only checks
     x86 = no error; the policy text ("x86 intercepts the same wild pointer") is not checked
  K1-K4 controls: the rules' own positive shapes from tests/s13/test_causes.py (expected artifact, not defects)
  python3 -I tests/v1/test_s13b_unclassified_artifacts.py   -> one line per case, exit 1 when any shows a defect
"""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    WORK = tempfile.mkdtemp(prefix='v1-s13bx-')
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

fails = 0


def w(*vals):
    return b''.join(struct.pack('<I', v) for v in vals).hex()


def refine(div, case):
    r = dict(source='src/t.cpp', address='00401000', symbol='?f@@YAXXZ', divergence=div,
             hint=dict(cause='unclassified'), case=case)
    return causes.refine(r, 'unclassified')


def say(case, got, real_bug, text):
    global fails
    cls = causes.POLICY.get(got, ('?',))[0]
    bad = real_bug and cls in ('artifact', 'by-design')
    fails += bad
    tag = 'DEFECT' if bad else 'ok'
    if not real_bug:
        tag = 'ok' if cls == 'artifact' else 'note (rule changed)'
    print('%s: %s - %s -> %s (%s)' % (case, tag, text, got, cls))


def main():
    if not hasattr(causes, '_rule_unclassified'):
        print('skip: no _rule_unclassified in causes.py')
        return 0
    canned = '5e1f2a77' + '00' * 12            # call 1 returns 0x772a1f5e (made up)
    case = dict(seed=12345, bg='zero', rets=['00' * 16, canned])
    blk = causes._alloc_result(0)
    # A1: a flags word whose bit 0x800 the native never sets
    say('A1', refine(dict(kind='mem-write', addr='03800040', ndiff=1, x86=w(0x00000801), arm=w(0x00000001),
                          only_x86=0, only_arm=0), case), True,
        'native forgets flag 0x800 of a stored flags word (byte 1, not a stub pointer)')
    # A2: native returns the allocator block, the oracle Init()'s (another object's) result
    say('A2', refine(dict(kind='return', x86=w(0x772a1f5e), arm=w(blk)), case), True,
        'native drops the result of p->Init() and returns p')
    # A3: by-value vs pointer declaration of the callee: native passes &tmp
    case0 = dict(seed=12345, bg='zero', rets=[canned])
    say('A3', refine(dict(kind='call-args', arg='this', cls='P', x86=w(0x772a1f5e), arm=w(0x06effff0), index=1),
                     case0), True, 'native passes a frame temporary for GetFoo()\'s pointer (by-value decl)')
    # A4: wrong vtable slot read from the random background; x86 had no error at all
    say('A4', refine(dict(kind='error', x86=None, arm='fault:x:0a123457'), dict(seed=1, bg='rand')), True,
        'native calls the wrong vtable slot into its private range; x86 made the right call, no wild call')
    # controls: the rules' own shapes
    say('K1', refine(dict(kind='mem-write', addr='03000013', ndiff=1, x86='11223308aabbccdd', arm='11223300aabbccdd',
                          only_x86=0, only_arm=0), case), False, 'stub-normalized shape of test_causes')
    say('K2', refine(dict(kind='call-args', arg='p0', cls='P', x86=w(0x772a1f5e), arm=w(blk), index=2), case), False,
        'ctor-this shape of test_causes')
    say('K3', refine(dict(kind='call-args', arg='this', cls='P', x86=w(0x772a1f5e), arm=w(0x06effff0), index=2),
                     case), False, 'canned-this shape of test_causes')
    say('K4', refine(dict(kind='error', x86=None, arm='fault:x:0a123457'), dict(seed=1, bg='rand')), False,
        'native-wild-private shape of test_causes (the same record as A4: indistinguishable)')
    return 1 if fails else 0


if __name__ == '__main__':
    try:
        rc = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(rc)
