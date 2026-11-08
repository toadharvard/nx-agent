#!/usr/bin/env python3
"""tests/s13/test_sgshift.py - d25-sg: the oracle object's $SG literals are numbered 43 higher than units.json's;
coffobj finds the shift (by the exe's bytes, else by names) and maps object $SG<n> to units.json's $SG<n-43>
(no decomp content)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'difffuzz'))
import coffobj  # noqa: E402
import fnkeys   # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class FakeCoff:
    def __init__(self, lits):
        data = b''.join(lits.values())
        self.sections = {1: {'data': data, 'flags': 0}}
        self.symbols, off = {}, 0
        for i, (n, b) in enumerate(lits.items()):
            self.symbols[i] = {'name': '$SG%d' % n, 'section': 1, 'value': off}
            off += len(b)


class FakeExe:
    def __init__(self, mem):
        self.mem = mem

    def read(self, a, n):
        for base, b in self.mem.items():
            if base <= a and a + n <= base + len(b):
                return b[a - base:a - base + n]
        return b'\xff' * n


lits = {143: b'alpha\0', 144: b'beta\0', 145: b'gamma\0'}
unit = {'symbols': {'$SG100': '0x01010000', '$SG101': '0x01010008', '$SG102': '0x01010010', '?x@@3HA': '0x01020000'}}
exe = FakeExe({0x01010000: b'alpha\0\0\0beta\0\0\0\0gamma\0\0\0'})
check(coffobj.sg_shift(FakeCoff(lits), unit, exe) == 43, 'shift 43 found by the exe bytes')
check(coffobj.sg_shift(FakeCoff({100: b'alpha\0', 101: b'beta\0', 102: b'gamma\0'}), unit, exe) == 0,
      'an unshifted object: 0')
coffobj.EXE, coffobj._EXE_TRIED = None, True          # no exe: the name delta, at least 90 % of the names
check(coffobj.sg_shift(FakeCoff(lits), unit) == 43, 'shift 43 by names without the exe')
check(coffobj.sg_shift(FakeCoff({143: b'a\0', 500: b'b\0', 900: b'c\0'}), unit) == 0, 'names do not agree: 0')
# read-only tables carry their relocations: a mapped target, else the exe's word (EQ reports/eq-sg: 0 vs 0x00722b50)
ui = object.__new__(coffobj.UnitImage)
ui.coff = type('C', (), {'symbols': {0: {'name': '?f@@YAXXZ', 'section': 2, 'value': 0},
                                     1: {'name': '??_EX@@UAEPAXI@Z', 'section': 3, 'value': 0}}})()
ui.mappings, ui.placements = {'?f@@YAXXZ': 0x00401230}, {}
sec = {'relocs': [(0, 0, coffobj.DIR32), (4, 1, coffobj.DIR32)]}
coffobj.EXE = FakeExe({0x01070000: bytes.fromhex('30124000' '502b7200')})
check(ui._relocated(sec, 0, bytes(8), 0x01070000) == bytes.fromhex('30124000' '502b7200'),
      'a table: the mapped target, and the exe word for a target the unit cannot place')
coffobj.EXE, coffobj._EXE_TRIED = None, True
check(ui._relocated(sec, 0, bytes(8), 0x01070000) == bytes.fromhex('30124000' '00000000'),
      'without the exe the unplaceable word keeps the object bytes')
ch = [c for c in fnkeys.CHANGES if c.tag == 'd25-sg']
check(len(ch) == 1 and fnkeys.SCOPED == 'd25' and ch[0].selects(fnkeys.Facts('f', osg=True)) and not ch[0].selects(fnkeys.Facts('f')), 'CHANGES d25-sg selects osg functions, SCOPED d25')
print('%d failures' % len(FAIL))
sys.exit(1 if FAIL else 0)
