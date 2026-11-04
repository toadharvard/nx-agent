#!/usr/bin/env python3
"""tests/s13/test_audit.py - the probe-cause audit (S13b s13b-002, V2 L1): a probe-assigned artifact / by-design
cause needs evidence in the record, and compare does not stop at differences that are by design."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13audit'))

import arm64emu   # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class T:
    local_statics = set(range(0x011d0000, 0x011d0010))
    conv = conv.Conv('?f@@YAXXZ', 'test')
    source = None


def main():
    iw = classify.indefinite_word
    check(iw('00000080', 'ffffff7f') and not iw('80000000', 'ffffff7f') and not iw('00000008aaaaaaaa', '0') and
          not iw('00000080', '00000080'), 'float->int: an aligned 0x80000000 word that differs, not a substring')
    check(classify.is_dtor_name('??1Foo@@QAE@XZ') and classify.is_dtor_name('??_GFoo@@UAEPAXI@Z') and
          classify.is_dtor_name('_ZN3FooD1Ev') and not classify.is_dtor_name('?InitFromSource@Foo@@QAEXXZ'),
          'destructor names (vptr-store only there)')
    fn = dict(code=b'\xc3', address=0x00401000)
    st = dict(kind='mem-write', only_x86=4, ndiff=4, addrs=['011d0000', '011d0001', '011d0002', '011d0003'],
              x86='01000000', arm='00000000', arm_local_static=True)
    h = classify.hint(None, T(), None, st, fn, 0)
    check(h.get('cause') == 'local-static', 'oracle-only store to a local static: local-static (%s)' % h)
    ct = dict(kind='call-target', x86='00453a70', arm='0084e3a0', index=0, arm_local_static=True)
    h = classify.hint(None, T(), world.Case(seed=1, bg='rand'), ct, fn, 0)
    check(h.get('cause') != 'local-static', 'a swapped call pair with a local static read is not local-static '
                                              '(V2 L1: %s)' % h.get('cause'))
    # compare: the same garbage word on both machines, the native's in its stub range
    cv = conv.Conv('?f@@YAXXZ', 'test')
    w = world.World(world.Case(seed=1), {}, {})
    rx, ra = x86emu.Result(), arm64emu.Result()
    rx.calls = [x86emu.Call(0x08a01234, [0, 0], 0)]
    c = arm64emu.Call(0x00a01234, [0] * 8, [0] * 8, 0)
    c.addr = 0x08a01234
    ra.calls = [c]
    rx.ret = ra.ret = None
    d = runner.compare(cv, rx, ra, w, vcalls=({0x00401000}, set()))
    check(d is None, 'vcall-stub-alias: the same raw word on both machines is the same call (%s)' % d)
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
