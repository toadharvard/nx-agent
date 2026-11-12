#!/usr/bin/env python3
"""Rule TAB-GUARD (fnvlift/tabguard.py, core.tab_tails): the slots of a `jmp [reloc disp + idx*4]` that a function can hand to the dispatcher are the
slots of the index values the function's own guards prove, never a run read off the data and never a default bias.

Each case builds a one-function PE32 image (the real fnvlift.image.Image over real bytes: sections, base relocations), explores the function with
core.explore and calls core.tab_tails.  Positive: an unsigned guard (the table slots are exactly the guarded ones, a following relocated dword is not
taken), a signed guard pair that starts above 0 (displacement = table - 4*k), a byte-table index (min..max of the bytes), a test/jne fact through
mov+and (memcpy tail tables: slot 0 is not a code pointer).  Negative (each must raise core.Unsupported): no guard, a guard on another register,
a join with an unguarded path, a call between guard and jmp (no calling-convention fact is used), a signed upper guard only, a byte table in a
writable section, a proved slot that is not a relocated dword.
    python3 -I tabguard_test.py"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fnvlift.image import Image
from fnvlift import core

BASE = 0x400000
TEXT, TEXT_RVA = 0x1000, 0x1000        # code at BASE+0x1000, tables at BASE+0x1100
DATA_RVA = 0x2000                      # a read-only data section
WDATA_RVA = 0x3000                     # a writable data section
FN = BASE + TEXT_RVA                   # function start
TAB = FN + 0x100


def u32(v):
    return struct.pack('<I', v & 0xffffffff)


def pe(text, rdata=b'', wdata=b'', relocs=()):
    """a PE32 image: .text (R X) with `text` at its start, .rdata (R), .data (RW), .reloc with HIGHLOW entries at the virtual addresses `relocs`"""
    page = {}
    for r in relocs:
        page.setdefault((r - BASE) & ~0xfff, []).append((r - BASE) & 0xfff)
    rel = b''
    for pg, offs in sorted(page.items()):
        ent = b''.join(struct.pack('<H', 0x3000 | o) for o in sorted(offs))
        if len(offs) % 2:
            ent += b'\0\0'
        rel += struct.pack('<II', pg, 8 + len(ent)) + ent
    secs = [(b'.text', 0x1000, text.ljust(0x200, b'\xcc'), 0x60000020), (b'.rdata', 0x1000, rdata.ljust(0x200, b'\0'), 0x40000040),
            (b'.data', 0x1000, wdata.ljust(0x200, b'\0'), 0xc0000040), (b'.reloc', 0x1000, rel.ljust(0x200, b'\0'), 0x42000040)]
    rvas = [TEXT_RVA, DATA_RVA, WDATA_RVA, 0x4000]
    hdr = bytearray(0x400)
    hdr[0:2] = b'MZ'
    struct.pack_into('<I', hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', hdr, 0x44, 0x14c, len(secs), 0, 0, 0, 224, 0x102)
    o = 0x58
    struct.pack_into('<H', hdr, o, 0x10b)
    struct.pack_into('<I', hdr, 0x74, BASE)
    struct.pack_into('<II', hdr, 0xe0, 0x4000, len(rel))                       # data directory 5: base relocations
    raw = 0x400
    pos = 0x138
    for (nm, vs, body, ch), rva in zip(secs, rvas):
        struct.pack_into('<8sIIIIIIHHI', hdr, pos, nm, vs, rva, len(body), raw, 0, 0, 0, 0, ch)
        raw += len(body)
        pos += 40
    return bytes(hdr) + b''.join(b for _, _, b, _ in secs)


def tails(code, table=b'', rdata=b'', wdata=b'', relocs=(), hi=FN + 0x40):
    """core.tab_tails of the function at FN (range [FN, hi)); `table` is placed at TAB in .text"""
    text = code.ljust(0x100, b'\xcc') + table
    img = Image.from_bytes(pe(text, rdata, wdata, relocs))
    c = img.bytes(FN, hi - FN + 16)
    insns, tables, taken, bad = core.explore(img, FN, hi, c, None, (), hi)
    return core.tab_tails(img, insns, tables, {FN} | set(taken), FN, hi)


OUT = [0x500000 + 0x10 * i for i in range(8)]       # code pointers outside the function


def tab(*v):
    return b''.join(u32(x) for x in v)


class Case:
    """the three assertions the cases use"""

    def assertEqual(self, a, b):
        assert a == b, '%r != %r' % (a, b)

    def assertIn(self, a, b):
        assert a in b, '%r not in %r' % (a, b)

    def red(self, code, table, **kw):
        try:
            tails(code, table, **kw)
        except core.Unsupported as e:
            return str(e)
        raise AssertionError('core.Unsupported not raised')


def jmp_tab(disp, reg=0x85):
    return b'\xff\x24' + bytes([reg]) + u32(disp)


class Positive(Case):
    def test_unsigned_guard_exact(self):
        # cmp eax,3 / ja def / jmp [eax*4+TAB]; the table has 4 slots and one more relocated dword after it, which no index reaches
        code = b'\x83\xf8\x03\x77\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        site = FN + 5
        rel = [site + 3] + [TAB + 4 * i for i in range(5)]
        got = tails(code, tab(*OUT[:5]), relocs=rel)
        self.assertEqual(got, sorted(OUT[:4]))

    def test_signed_pair_lower_bound_above_zero(self):
        # cmp eax,2 / jl def / cmp eax,4 / jg def / jmp [eax*4 + TAB-8]: indices 2..4 are the three slots; the dword at the displacement is not relocated
        code = b'\x83\xf8\x02\x7c\x0c\x83\xf8\x04\x7f\x07' + jmp_tab(TAB - 8) + b'\xc3'
        site = FN + 10
        rel = [site + 3] + [TAB + 4 * i for i in range(3)]
        got = tails(code, tab(*OUT[:3]), relocs=rel)
        self.assertEqual(got, sorted(OUT[:3]))

    def test_byte_table_index(self):
        # cmp eax,3 / ja def / movzx ecx, byte [eax+IDX] / jmp [ecx*4+TAB]; IDX (read-only) = 1,2,2,1: slots 1 and 2 only, slot 0 is not a code pointer
        idx = BASE + DATA_RVA
        code = b'\x83\xf8\x03\x77\x0e' + b'\x0f\xb6\x88' + u32(idx) + jmp_tab(TAB, 0x8d) + b'\xc3'
        site = FN + 5 + 7
        rel = [FN + 5 + 3, site + 3, TAB + 4, TAB + 8]
        got = tails(code, b'\x90' * 4 + tab(OUT[0], OUT[1]), rdata=bytes([1, 2, 2, 1]), relocs=rel)
        self.assertEqual(got, sorted(OUT[:2]))

    def test_test_jne_fact_through_mov_and(self):
        # test edi,3 / jne L / ret / nop / L: mov eax,edi / and eax,3 / jmp [eax*4+TAB]: edi&3 != 0 gives eax in 1..3 (slot 0 is not a code pointer)
        code = b'\xf7\xc7\x03\x00\x00\x00\x75\x02\xc3\x90\x89\xf8\x83\xe0\x03' + jmp_tab(TAB)
        site = FN + 15
        rel = [site + 3] + [TAB + 4 * i for i in (1, 2, 3)]
        got = tails(code, b'\x90' * 4 + tab(*OUT[:3]), relocs=rel)
        self.assertEqual(got, sorted(OUT[:3]))


    def test_frame_cell_guard(self):
        # debug-build switch: mov [ebp-0x14],edx / cmp dword [ebp-0x14],4 / ja def / mov eax,[ebp-0x14] / jmp [eax*4+TAB]
        code = b'\x89\x55\xec\x83\x7d\xec\x04\x77\x0a\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        rel = [FN + 12 + 3] + [TAB + 4 * i for i in range(5)]
        self.assertEqual(tails(code, tab(*OUT[:5]), relocs=rel), sorted(OUT[:5]))

    def test_other_register_holds_the_constant(self):
        # mov edx,3 / cmp eax,edx / ja def / jmp [eax*4+TAB]
        code = b'\xba\x03\x00\x00\x00\x39\xd0\x77\x07' + jmp_tab(TAB) + b'\xc3'
        rel = [FN + 9 + 3] + [TAB + 4 * i for i in range(4)]
        self.assertEqual(tails(code, tab(*OUT[:4]), relocs=rel), sorted(OUT[:4]))


class Negative(Case):
    def test_no_guard(self):
        code = jmp_tab(TAB)
        msg = self.red(code, tab(*OUT[:4]), relocs=[FN + 3] + [TAB + 4 * i for i in range(4)])
        self.assertIn('no guard bounds eax', msg)

    def test_guard_on_other_register(self):
        code = b'\x83\xf9\x03\x77\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        msg = self.red(code, tab(*OUT[:4]), relocs=[FN + 5 + 3] + [TAB + 4 * i for i in range(4)])
        self.assertIn('no guard bounds eax', msg)

    def test_join_with_unguarded_path(self):
        # test ecx,ecx / je L / cmp eax,3 / ja def / L: jmp [eax*4+TAB] -- L is also entered without the guard
        code = b'\x85\xc9\x74\x05\x83\xf8\x03\x77\x07' + jmp_tab(TAB) + b'\xc3'
        msg = self.red(code, tab(*OUT[:4]), relocs=[FN + 9 + 3] + [TAB + 4 * i for i in range(4)])
        self.assertIn('no guard bounds eax', msg)

    def test_call_between_guard_and_jmp(self):
        # cmp eax,3 / ja def / call X / jmp [eax*4+TAB]: no register survives a call
        code = b'\x83\xf8\x03\x77\x0c\xe8' + u32(0x600000 - (FN + 10)) + jmp_tab(TAB) + b'\xc3'
        msg = self.red(code, tab(*OUT[:4]), relocs=[FN + 10 + 3] + [TAB + 4 * i for i in range(4)])
        self.assertIn('no guard bounds eax', msg)

    def test_signed_upper_guard_only(self):
        # cmp eax,3 / jg def / jmp [eax*4+TAB]: a negative index passes the guard
        code = b'\x83\xf8\x03\x7f\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        msg = self.red(code, tab(*OUT[:4]), relocs=[FN + 5 + 3] + [TAB + 4 * i for i in range(4)])
        self.assertIn('no guard bounds eax', msg)

    def test_byte_table_in_writable_section(self):
        idx = BASE + WDATA_RVA
        code = b'\x83\xf8\x03\x77\x0e' + b'\x0f\xb6\x88' + u32(idx) + jmp_tab(TAB, 0x8d) + b'\xc3'
        rel = [FN + 5 + 3, FN + 5 + 7 + 3, TAB + 4, TAB + 8]
        msg = self.red(code, b'\x90' * 4 + tab(OUT[0], OUT[1]), wdata=bytes([1, 2, 2, 1]), relocs=rel)
        self.assertIn('is not a relocated dword', msg)

    def test_proved_slot_not_relocated(self):
        code = b'\x83\xf8\x03\x77\x0a' + jmp_tab(TAB) + b'\xcc\xcc\xcc\xc3'
        msg = self.red(code, tab(*OUT[:4]), relocs=[FN + 5 + 3] + [TAB + 4 * i for i in range(3)])
        self.assertIn('slot 3', msg)
        self.assertIn('is not a relocated dword', msg)


    def test_store_through_pointer_drops_the_cell(self):
        # as test_frame_cell_guard, with `mov dword [ecx],0` between the guard and the reload: [ecx] may be the cell
        code = b'\x89\x55\xec\x83\x7d\xec\x04\x77\x10\xc7\x01\x00\x00\x00\x00\x8b\x45\xec' + jmp_tab(TAB) + b'\xc3'
        msg = self.red(code, tab(*OUT[:5]), relocs=[FN + 18 + 3] + [TAB + 4 * i for i in range(5)])
        self.assertIn('no guard bounds eax', msg)


if __name__ == '__main__':
    CASES = [(Positive, Positive.test_unsigned_guard_exact), (Positive, Positive.test_signed_pair_lower_bound_above_zero), (Positive, Positive.test_byte_table_index), (Positive, Positive.test_test_jne_fact_through_mov_and), (Positive, Positive.test_frame_cell_guard), (Positive, Positive.test_other_register_holds_the_constant), (Negative, Negative.test_no_guard), (Negative, Negative.test_guard_on_other_register), (Negative, Negative.test_join_with_unguarded_path), (Negative, Negative.test_call_between_guard_and_jmp), (Negative, Negative.test_signed_upper_guard_only), (Negative, Negative.test_byte_table_in_writable_section), (Negative, Negative.test_proved_slot_not_relocated), (Negative, Negative.test_store_through_pointer_drops_the_cell)]
    for cls, f in CASES:
        f(cls())
        print('ok', f.__qualname__)
    print('tabguard: %d cases passed' % len(CASES))
