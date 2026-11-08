#!/usr/bin/env python3
"""test_d27.py - d27-eqbase (orchestrator's decision (b), farm baseline 588863): HARNESS stays difffuzz-20 and only
the cached hard verdicts (real-bug / triage by causes.policy) are fuzzed again once.
  E1  a soft record is not rechecked
  E2  a record whose cause is by design (nan-bits) is not rechecked
  E3  an unexplained record (call-args, triage) is rechecked
  E4  the row is the newest tag (SCOPED d27)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13d27'))
os.environ.setdefault('FNVNX_DFLINK', os.path.join(os.environ['FNVNX_DFWORK'], 'nolink'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import fnkeys  # noqa: E402

FAIL = 0


def check(name, cond, got):
    global FAIL
    FAIL += not cond
    print('%s: %s (%s)' % (name, 'ok' if cond else 'FAIL', got))


def main():
    f = getattr(fnkeys, '_hard_cached', None)
    if f is None:
        check('E0', False, 'no _hard_cached')
        print('test_d27: %d failed' % FAIL)
        return 1
    base = dict(source='src/t.cpp', address='00401000', symbol='?f@@YAXXZ', conv='this:P/4 -> void', case={})
    soft = dict(base, divergence=dict(kind='mem-write', soft=True), hint=dict(cause='x87-precision'))
    check('E1', f(soft) is False, f(soft))
    nan = dict(base, divergence=dict(kind='mem-write', x86='0000c0ff', arm='0000c07f', addrs=['03000000'], ndiff=1,
                                     only_x86=0, only_arm=0), hint=dict(cause='nan-bits'))
    check('E2', f(nan) is False, f(nan))
    args = dict(base, divergence=dict(kind='call-args', arg='p0', cls='I', x86='01000000', arm='02000000', index=0,
                                      target='00402000'), hint=dict(cause='unclassified'))
    check('E3', f(args) is True, f(args))
    check('E4', fnkeys.SCOPED == 'd27' and fnkeys.CHANGES[-1].tag == 'd27-eqbase', (fnkeys.SCOPED, fnkeys.CHANGES[-1].tag))
    print('test_d27: %d failed' % FAIL)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
