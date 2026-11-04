#!/usr/bin/env python3
"""tests/s13/test_vtpairs.py - vtmodel.pairs: a kind-V pair is short only when the native vtable has fewer slots than
the exe's vftable; as many, or one more (the Itanium D1/D0 pair beside MSVC's ??_G), is exact. The harness pads
every copy with spare slots past the exact count, which is not a pairing gap (J-0145 counted those as short)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..', '..', 'tools', 'difffuzz')))
import vtmodel  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class Exe:
    vft = {0x1000: dict(off=0, cls='A', slots=[1, 2, 3]), 0x2000: dict(off=0, cls='B', slots=[1, 2, 3]),
           0x3000: dict(off=0, cls='C', slots=[1, 2, 3]), 0x4000: dict(off=0, cls='D', slots=[1, 2]),
           0x5000: dict(off=4, cls='A', slots=[1]), 0x6000: dict(off=0, cls='E', slots=[1])}


class M:
    exe = Exe()
    rows = {0x1000: ('_ZTV1A', 'a.o', 8), 0x2000: ('_ZTV1B', 'a.o', 8), 0x3000: ('_ZTV1C', 'a.o', 8),
            0x4000: ('_ZTV1D', 'a.o', 8), 0x5000: ('_ZTV1A', 'a.o', 32), 0x6000: ('_ZTV1E', 'gone.o', 8)}
    groups = {'_ZTV1A': [0, None, 'f1', 'f2', 'f3', 'f4', -4, None, 'g'],   # A: 4 slots of 3 (+1), its secondary 1
              '_ZTV1B': [0, None, 'f1', 'f2', 'f3'],                         # exact
              '_ZTV1C': [0, None, 'f1'],                                     # 1 of 3: short
              '_ZTV1D': [0, None, 'f1', 'f2', 'f3', 'f4', 'f5']}             # longer

    def group(self, sym, obj):
        return None if obj == 'gone.o' else (self.groups[sym], 4 * len(self.groups[sym]))


def main():
    c, ex = vtmodel.pairs(M())
    check(c[(True, 'exact')] == 2, 'equal and one more are exact (%s)' % dict(c))
    check(c[(True, 'short')] == 1 and ex and ex[0].startswith('00003000 C: 1 native slots of 3'),
          'one fewer slot is short (%s)' % ex)
    check(c[(True, 'longer')] == 1, 'two or more extra slots are longer')
    check(c[(False, 'exact')] == 1, 'a secondary vftable at its own address point')
    check(c[(True, 'no-group')] == 1, 'a row whose vtable is not in fixed/')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    raise SystemExit(main())
