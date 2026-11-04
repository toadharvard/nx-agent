#!/usr/bin/env python3
"""V2 adversarial case for S2c-abi 73d8499 (thisadj): "__fnv_<addr> stays the primary entry for native callers".

    python3 tests/v2/test_s2c_thisadj.py   # needs what tools/link/test_bindings.py needs; hand-written units only

MSVC enters an override of a secondary base's virtual with `this` at that base. A decomp unit that calls the address
directly through a shell class declared at the subobject passes the subobject pointer, as the x86 caller did:
  T1  src/sub.cpp: `struct Sub { int fb(int); int b; }; int call_sub(Sub *s) { return s->fb(1); }`, Sub::fb named
      at D::fb's address 0x401580 (MSVC entry this = D + 8). The reference must bind to the MSVC entry
      (__fnvnx_msvc_00401580, the _ZThn8_ thunk), not to the primary entry, which reads `d` 8 bytes off.
  T0  control: test_bindings' own D::fb check (the map names the thunk, a reference on D keeps __fnv_).
At link-head 06857911 four addresses with a thunk have such callers (7 reference rows), e.g. 0x008805f0
CVShell::FUN_008805f0 (d=164) called by Holder::FUN_008d4870 (0x008d4870: `this` is the +0xa4 subobject), and
0x00de2980 S_00de2980_D::FUN_00de2980 (d=32) called as hkpPhysicsContextWorldListener::FUN_00de2980.
"""
import os
import shutil
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
import test_bindings as tb  # noqa: E402

WORK = tempfile.mkdtemp(prefix='v2-thisadj-')   # raises when it cannot create the directory


class Keep:
    """tempfile.TemporaryDirectory stand-in for test_bindings: the tree stays until this test has read it."""
    def __init__(self, *a, **k):
        self.name = WORK

    def __enter__(self):
        return WORK

    def __exit__(self, *a):
        return False


def main():
    tb.SRC['src/sub.cpp'] = ('struct Sub { int fb(int); int b; };\n'
                             'int call_sub(Sub *s) { return s->fb(1); }\n')
    tb.UNITS['units'].append({'source': 'src/sub.cpp', 'compiler': 'sp1', 'flags': [],
                              'functions': [tb.F(0x4015a0, '?call_sub@@YAHPAUSub@@@Z')],
                              'symbols': {'?fb@Sub@@QAEHH@Z': '0x00401580'}})
    tb.tempfile.TemporaryDirectory = Keep
    fails = 0
    try:
        try:
            tb.main()
        except SystemExit as e:
            print('T0 test_bindings exit %s' % e.code)
        out = os.path.join(WORK, 'out')
        refs = {(r[0], r[1]): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'refs.tsv'))
                                          if not l.startswith('#'))}
        r = refs.get(('src/sub.cpp', '_ZN3Sub2fbEi'), [''] * 6)
        ok = r[5] == '__fnvnx_msvc_00401580'
        print('%s T1: reference on the subobject shell Sub binds to %r (want the MSVC entry __fnvnx_msvc_00401580)'
              % ('ok' if ok else 'FAIL', r[5]))
        fails += not ok
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
