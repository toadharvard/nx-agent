#!/usr/bin/env python3
"""test_slot_class.py - tests/s5/slot_class.py on synthetic symbol tables, vtable words and a made-up log. No game data.

    python3 -I tests/s5/test_slot_class.py
"""
from __future__ import annotations

import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import slot_class as sc  # noqa: E402

LDR_W3_X4 = 0xB9400083      # ldr w3, [x4]
LDR_W3_X3_168 = 0xB940A863  # ldr w3, [x3, #168]   (slot 42)
BLR_X3 = 0xD63F0060
MOV_W5_W4 = 0x2A0403E5      # mov w5, w4
NOP = 0xD503201F
BL_0 = 0x94000000


class Mem:
    def __init__(self, words, text=(0x30000000, 0x30100000)):
        self.w, self.text = dict(words), text

    def read32(self, a):
        return self.w.get(a)

    def is_text(self, a):
        return self.text[0] <= a < self.text[1]

    def read(self, a, n):
        out = b""
        for o in range(a, a + n, 4):
            out += struct.pack("<I", self.w.get(o, NOP))
        return out


F = 0x30001000
SYMS = sc.Syms([
    (0x30002000, 0x40, "T", "_ZN1AD1Ev"), (0x30002040, 0x40, "T", "_ZN1AD0Ev"),
    (0x30002100, 0x10, "T", "_ZN1A3fooEv"), (0x30002200, 0x10, "T", "_ZN1A3barEv"),
    (0x30002300, 0x10, "T", "__fnvnx_vtrap_00401234"),
    (0x30002400, 0x10, "T", "_Z12FUN_00e8b490Pii"),
    (0x30002500, 0x10, "T", "_ZN1BD1Ev"),
    (0x30002600, 0x10, "T", "_ZN1B3bazEv"),
    (0x30003000, 0x20, "D", "_ZTV1A"),   # offset-to-top, typeinfo, D1, D0, foo, bar
    (0x30003100, 0x14, "D", "_ZTV1B"),   # no pair: slot0 = ~B (D1 only), baz
    (0x30003200, 0x14, "D", "_ZTV1T"),   # slots: trap, thisless
    (0x30003300, 0x10, "D", "_ZTV1S"),   # short: 2 slots
    (F, 0x40, "T", "_Z6callerv"),
])
WORDS = {
    0x30003000: 0, 0x30003004: 0x30009000, 0x30003008: 0x30002000, 0x3000300C: 0x30002040,
    0x30003010: 0x30002100, 0x30003014: 0x30002200,
    0x30003100: 0, 0x30003104: 0x30009000, 0x30003108: 0x30002500, 0x3000310C: 0x30002600,
    0x30003200: 0, 0x30003204: 0x30009000, 0x30003208: 0x30002300, 0x3000320C: 0x30002400,
    0x30003300: 0, 0x30003304: 0x30009000, 0x30003308: 0x30002100, 0x3000330C: 0x30002200,
    0x30003310: 0xFFFFFFF8,   # the next group's offset-to-top is past _ZTV1S's size
}
AM = {0x00500100: ("F", "__fnv_00500100", "_ZN1A3fooEv"), 0x00500200: ("F", "__fnv_00500200", "_ZN1A3barEv")}


class FakeExe:
    def __init__(self, slots, col=True):
        self.s, self.c = slots, col

    def slots(self, va):
        return self.s

    def rtti(self, va):
        return "locator 0x1, type .?AVFake@@" if self.c else None


class Classes(unittest.TestCase):
    def cls(self, slot, vptr, **kw):
        return sc.classify(slot, vptr, SYMS, Mem(WORDS), **kw)

    def test_d26b(self):
        r = self.cls(2, 0x30003008, am=AM)
        self.assertEqual(r.cls, "D26b call-only")
        self.assertEqual(r.owner, "S1-rules / S2-bodies-2")
        txt = "\n".join(r.evidence)
        self.assertIn("exe slot 1", txt)
        self.assertIn("0x00500100", txt)    # landed function's exe address
        self.assertIn("0x00500200", txt)    # the function meant

    def test_runtime_vptr_is_converted(self):
        r = self.cls(2, 0x30003008 - 0x30000000 + 0x05000000)
        self.assertEqual(r.cls, "D26b call-only")

    def test_no_pair_is_other(self):
        self.assertEqual(self.cls(1, 0x30003108).cls, "other")

    def test_short_past_end(self):
        r = self.cls(5, 0x30003308)
        self.assertEqual(r.cls, "short vtable")
        self.assertEqual(r.owner, "S2-bodies-2")

    def test_short_word_not_code(self):
        self.assertEqual(self.cls(2, 0x30003308).cls, "short vtable")   # 0xfffffff8 inside the symbol? size 0x10 ends at 3310
        w = dict(WORDS)
        w[0x30003310] = 0
        self.assertEqual(sc.classify(2, 0x30003308, SYMS, Mem(w)).cls, "short vtable")

    def test_trap_and_thisless(self):
        r = self.cls(0, 0x30003208)
        self.assertEqual((r.cls, r.owner), ("trap", "bodies"))
        r = self.cls(1, 0x30003208)
        self.assertEqual((r.cls, r.owner), ("this-less slot", "S2-bodies-2"))

    def test_image(self):
        exe = FakeExe([0x00401000, 0x00401010, 0x00401020, 0x00401030])
        r = self.cls(3, 0x010F0F50, exe=exe)
        self.assertEqual(r.cls, "D26c image vptr")
        txt = "\n".join(r.evidence)
        self.assertIn("exe slot 3", txt)
        self.assertIn("0x00401020", txt)    # slot 2, meant by minus-one
        self.assertIn("RTTI locator", txt)
        r = self.cls(3, 0x010F0F50, exe=FakeExe([1, 2, 3, 4], col=False))
        self.assertIn("no RTTI", "\n".join(r.evidence))

    def test_not_in_vtable_and_no_vptr(self):
        self.assertEqual(self.cls(1, 0x31000000).cls, "other")
        self.assertEqual(self.cls(1, None).cls, "other")
        self.assertEqual(sc.classify(1, None, SYMS, Mem(WORDS), target_hint=0x30002300).cls, "trap")

    def test_norm_vptr(self):
        self.assertEqual(sc.norm_vptr(0x010F0F50), ("image", 0x010F0F50))
        self.assertEqual(sc.norm_vptr(0x05001234), ("link", 0x30001234))
        self.assertEqual(sc.norm_vptr(0x3199CC68), ("link", 0x3199CC68))


class Site(unittest.TestCase):
    def code(self, *ins):
        w = dict(WORDS)
        for i, x in enumerate(ins):
            w[F + 4 * i] = x
        return Mem(w), F + 4 * (len(ins) - 1)

    def test_vcall(self):
        mem, blr = self.code(LDR_W3_X4, LDR_W3_X3_168, BLR_X3)
        vc = sc.find_vcall(mem.read, blr, F)
        self.assertEqual((vc["slot"], vc["off"], vc["recv_reg"]), (42, 168, 4))

    def test_mov_followed(self):
        mem, blr = self.code(LDR_W3_X4, LDR_W3_X3_168, 0x2A0303E2, 0xD63F0040)   # mov w2,w3 ; blr x2
        self.assertEqual(sc.find_vcall(mem.read, blr, F)["slot"], 42)

    def test_not_vcall(self):
        mem, blr = self.code(NOP, BL_0, BLR_X3)           # blr of a value from nowhere
        self.assertIsNone(sc.find_vcall(mem.read, blr, F))
        mem, blr = self.code(LDR_W3_X4, BL_0, LDR_W3_X3_168, BLR_X3)   # vptr register clobbered by a call
        self.assertIsNone(sc.find_vcall(mem.read, blr, F))
        mem, blr = self.code(NOP, BL_0)                   # a bl, not a blr
        self.assertIsNone(sc.find_vcall(mem.read, blr, F))


LOG = """junk
  invalid memory access type=19 addr=0x4 size=4 pc=0x5000000 thread=1
== trace (fault: Invalid memory read (UC_ERR_READ_UNMAPPED)), thread 1
   S10 initializer (running or last run): orig 0x00fc7a50
   pc  0x30001014 _Z6callerv [orig 00401000]+0x14
   lr  0x30001010 _Z6callerv [orig 00401000]+0x10
   x0 0x0 x1 0x1 x2 0x5002100 x3 0x0
   x19 0x3dfff130 x20 0x50d56044
   #0  0x30001018 _Z6callerv [orig 00401000]+0x18
   #1  0x3f000ff0
   fnvnx boot: early init
"""


class Log(unittest.TestCase):
    def test_parse(self):
        frames, regs, order = sc.parse_log(LOG)
        self.assertEqual(frames["pc"], (0x30001014, "_Z6callerv", 0x14))
        self.assertEqual(frames["lr"][2], 0x10)
        self.assertEqual(order, ["pc", "lr", "#0"])
        self.assertEqual(regs["x2"], 0x5002100)
        self.assertEqual(regs["x20"], 0x50D56044)

    def test_resolve_by_name_when_rebuilt(self):
        a, why = sc.resolve_frame((0x30009990, "_Z6callerv", 0x10), SYMS)
        self.assertEqual(a, F + 0x10)
        self.assertIn("by name", why)
        a, why = sc.resolve_frame((F + 0x10, "_Z6callerv", 0x10), SYMS)
        self.assertEqual((a, why), (F + 0x10, ""))

    def test_locate_skips_non_vcall_frames(self):
        w = dict(WORDS)
        # lr (F+0x10) follows a bl; #0 (F+0x18) follows the vcall at F+0x14
        for o, x in {0: NOP, 4: NOP, 8: NOP, 0xC: BL_0, 0x10: LDR_W3_X4, 0x14: LDR_W3_X3_168, 0x18: BLR_X3}.items():
            w[F + o] = x
        frames = {"lr": (F + 0x10, "_Z6callerv", 0x10), "#0": (F + 0x1C, "_Z6callerv", 0x1C)}
        blr, vc, tried, _ = sc.locate_site(frames, ["pc", "lr", "#0"], SYMS, Mem(w))
        self.assertEqual((blr, vc["slot"]), (F + 0x18, 42))
        self.assertEqual(len(tried), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
