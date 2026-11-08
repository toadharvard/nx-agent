#!/usr/bin/env python3
"""test_flowthrow.py - throw-temp-dtor (EQ, flow rows: 0x00b2ceb0, 0x00b2cbc0, 0x00b05420, 0x00b092a0, ... 7 rows).

The oracle ends the run at its call of _CxxThrowException (0x00ec7894); the native makes one more call first, the
destructor of the temporary the exception was built from (std::string::_Tidy 0x0044a730). GCC destroys the temporaries
of a throw expression before __cxa_throw; MSVC emits the same call right after the throw call (dead code: the unwind
map runs it). The exe has the call there (exeempty.calls_after_noreturn).
  H1  call-target x86 throw, arm Y, arm_next = throw, Y called right after the exe's throw: throw-temp-dtor
  H2  the exe does not call Y after its throw: no such cause
  H3  the native's next call is not the throw: no such cause
  H4  the oracle's call is not the throw: no such cause
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = tempfile.mkdtemp(prefix='s13-flowthrow-')
os.environ['FNVNX_DFWORK'] = WORK
os.environ['FNVNX_DFLINK'] = os.path.join(WORK, 'nolink')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import causes   # noqa: E402
import exeempty  # noqa: E402

THROW, Y = '00ec7894', '0044a730'
FN = '00401000'


def cf(div):
    r = dict(source='src/t.cpp', address=FN, symbol='?f@@YAXXZ', conv='this:P/4 -> void', divergence=div,
             hint=dict(cause='call-flow'), case=dict(args={}))
    return causes.refine(r, 'call-flow')


def main():
    causes.native_callees = lambda src, a: {'__fnv_00401200'}
    causes.native_body = lambda src, n: None
    causes.native_defines = lambda src, n: False
    causes.native_reaches = lambda src, n: {n}
    causes.crt_entries = lambda: set()
    causes.unbound_callees = lambda src, a: []
    causes._VFT, causes._LINKSYM = set(), {}
    exeempty.calls_after_noreturn = lambda fn, nr, n=8: {int(Y, 16)} if nr == 0x00ec7894 else set()
    base = dict(kind='call-target', index=1, x86=THROW, arm=Y, x86_next=None, arm_next=THROW,
                x86_trace=['00401100', THROW], arm_trace=['00401100', Y])
    cases = [('H1', dict(base), 'throw-temp-dtor'),
             ('H2', dict(base, arm='00401300'), None),
             ('H3', dict(base, arm_next='00401500'), None),
             ('H4', dict(base, x86='00401600', arm_next='00401600'), None)]
    fails = 0
    for name, div, want in cases:
        got = cf(div)
        ok = (got == want) if want else (got != 'throw-temp-dtor')
        fails += not ok
        print('%s: %s (%s, want %s)' % (name, 'ok' if ok else 'FAIL', got, want or 'not throw-temp-dtor'))
    print('test_flowthrow: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
