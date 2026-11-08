#!/usr/bin/env python3
"""W31: vtmetric.compare_slots finds the destructor pair by the exe when the renamed names hide it.

exe_vtable_copies reads the kept object's raw names (D1/D0 symbols) and starts a copy's extra slots at exe slot
nm = n - 1 (the minus-one rule), so the whole extended table is in the minus-one layout. After resolve's renames the
pair can read `__fnv_<a>` then `__fnvnx_d0del_<a>`, `__fnv_<a>_D1` then a bare `__fnv_<a>`, or two trap stubs, none
of which exevft.dtor_pos / _pair_positions know; the slot comparison then took k = j and reported a tail of -1
mismatches (697 rows, 127 extended copies on link 3f7a6c35). Hand-made names, address map and exe slots; no exe.

    python3 tools/link/test_vtmetric_pair.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft    # noqa: E402
import fnvlink   # noqa: E402
import vtmetric  # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def fn(a):
    return '__fnv_%08x' % a


EXE = [0x00401000, 0x00401100, 0x00401200, 0x00401300, 0x00401400, 0x00401500, 0x00401600]   # exe slot 0 = deleting dtor


def main():
    # V row (extended copy): D1 / D0 stub pair at native 0, 1, then exe slots 1.. (nm = n - 1 = 1, six extras)
    ext = [fn(EXE[0]), '__fnvnx_d0del_%08x' % EXE[0]] + [fn(a) for a in EXE[1:]]
    symaddr = {}
    check(exevft.dtor_pos(ext) is None, 'the renamed pair is not one dtor_pos knows (the premise)')
    agree, bad = vtmetric.compare_slots(ext, exevft.dtor_pos(ext), EXE, symaddr)
    check(bad == [] and agree == 5, 'V row, d0del pair: the exe decides, no mismatch (%s, %s)' % (agree, bad))

    # `__fnv_<a>_D1` then a bare address-bound D0
    ext2 = [fn(EXE[0]) + '_D1', fn(EXE[0])] + [fn(a) for a in EXE[1:]]
    agree, bad = vtmetric.compare_slots(ext2, exevft.dtor_pos(ext2), EXE, symaddr)
    check(bad == [] and agree == 5, '_D1 then a bare name: no mismatch (%s, %s)' % (agree, bad))

    # the pair at slot 3 (a base class with virtuals first), two trap stubs for D1 / D0
    ext3 = [fn(EXE[0]), fn(EXE[1]), fn(EXE[2]), '__fnvnx_vtrap_aaaa0001', '__fnvnx_vtrap_aaaa0002',
            fn(EXE[4]), fn(EXE[5]), fn(EXE[6])]
    agree, bad = vtmetric.compare_slots(ext3, None, EXE, symaddr)
    check(bad == [] and agree == 4, 'pair at 3 behind two virtuals: slots after it are one lower (%s, %s)' % (agree, bad))

    # D26 (c): a minus-one copy of an image vftable no native vtable stands for, its V row from vmo_vrows; the
    # flag-stub pair is a name pair _pair_positions already knows, the comparison must not move
    vrows = fnvlink.vmo_vrows({0x01010600: '__fnvnx_vmo_01010600'}, {}, {0x01010600: exevft.Vft('.?AVC@@', 0, [], EXE)})
    check(vrows[0x01010600][3].startswith('primary, minus-one copy of the exe vftable (D26 c)'), 'D26 (c) V row made')
    vmo = ['__fnvnx_vflag_%08x_0' % EXE[0], '__fnvnx_vflag_%08x_1' % EXE[0]] + [fn(a) for a in EXE[1:]]
    agree, bad = vtmetric.compare_slots(vmo, exevft.dtor_pos(vmo), EXE, symaddr)
    check(bad == [] and agree == 5, 'D26 (c) copy, flag-stub pair: no mismatch (%s, %s)' % (agree, bad))

    # a real mismatch stays one: slot 4 holds another function, whatever the alignment
    wrong = list(ext)
    wrong[4] = fn(0x00409999)
    agree, bad = vtmetric.compare_slots(wrong, exevft.dtor_pos(wrong), EXE, symaddr)
    check(len(bad) == 1 and bad[0][2] == 0x00409999 and bad[0][3] == EXE[3], 'a wrong slot is still a mismatch (%s)' % bad)

    # a table in the exe's order, no pair (k = j) is not moved
    plain = [fn(a) for a in EXE]
    agree, bad = vtmetric.compare_slots(plain, None, EXE, symaddr)
    check(bad == [] and agree == 5, 'a table in the exe\'s order stays k = j (%s, %s)' % (agree, bad))
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
