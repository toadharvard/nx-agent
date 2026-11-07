#!/usr/bin/env python3
"""tests for boot_trace.py (synthetic logs; no game data)"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import boot_trace as bt  # noqa: E402


def guest_line(seq, depth, name, orig, regs, caller="WinMain [orig 0086a850]"):
    r = "\t".join(hex(v) for v in (regs + [0] * 8)[:8])
    return f"call\t{seq}\t1\t{depth}\t{name}\t{orig}\t{r}\t0x3dfff000\t0x5000000\t{caller}\n"


def wine_line(seq, depth, name, orig, ecx, stack):
    r = "\t".join(hex(v) for v in [ecx, 0] + (stack + [0] * 8)[:8])
    return f"call\t{seq}\t204\t{depth}\t{name}\t{orig}\t{r}\t0x31fe00\t0x401013\n"


class Conv(unittest.TestCase):
    def test_conv(self):
        self.assertEqual(bt.msvc_conv("?Allocate@MemoryHeap@@QAEPAXII@Z"), "thiscall")
        self.assertEqual(bt.msvc_conv("?FUN_00e8b490@@YGXPAHHH@Z"), "stdcall")
        self.assertEqual(bt.msvc_conv("?f@@YAHH@Z"), "cdecl")
        self.assertEqual(bt.msvc_conv("?f@@YIHH@Z"), "fastcall")
        self.assertEqual(bt.msvc_conv("?g@C@@SAHXZ"), "cdecl")
        self.assertIsNone(bt.msvc_conv("_Z3foov"))

    def test_void(self):
        self.assertTrue(bt.msvc_void("?FUN_00e8b490@@YGXPAHHH@Z"))
        self.assertTrue(bt.msvc_void("?Deallocate@MemoryHeap@@UAEXPAXI@Z"))
        self.assertFalse(bt.msvc_void("?Allocate@MemoryHeap@@QAEPAXII@Z"))


class Lockstep(unittest.TestCase):
    def logs(self, g, w):
        d = tempfile.mkdtemp()
        gp, wp = os.path.join(d, "g.tsv"), os.path.join(d, "w.tsv")
        open(gp, "w").write("".join(g))
        open(wp, "w").write("".join(w))
        return bt.main_thread(bt.read_log(gp, "guest")[1]), bt.main_thread(bt.read_log(wp, "wine")[1])

    def test_inlined_and_runtime_calls_are_skipped(self):
        g = [guest_line(1, 0, "A", "00401000", [0x11f6238, 5]),
             guest_line(2, 1, "Dealloc", "00aa4060", [0x11f6238, 0x40001000], caller="_ZdlPv"),   # runtime
             guest_line(3, 1, "C", "00403000", [0x11f6238, 7])]
        w = [wine_line(1, 0, "A", "00401000", 0x11f6238, [5]),
             wine_line(2, 1, "B", "00402000", 0x11f6238, [1]),   # inlined by GCC
             wine_line(3, 1, "C", "00403000", 0x11f6238, [7])]
        gg, ww = self.logs(g, w)
        r = bt.compare(gg, ww, {})
        self.assertEqual(r["matched"], 2)
        self.assertIsNone(r["first"])
        self.assertEqual(r["inlined"][("wine-only", "B")], 1)

    def test_image_argument_divergence(self):
        g = [guest_line(1, 0, "A", "00401000", [0x01082504, 5])]
        w = [wine_line(1, 0, "A", "00401000", 0x010824fc, [5])]
        gg, ww = self.logs(g, w)
        r = bt.compare(gg, ww, {0x00401000: "?A@C@@QAEXH@Z"})
        self.assertEqual(r["first"]["kind"], "arg this")

    def test_heap_pointers_are_not_compared(self):
        g = [guest_line(1, 0, "A", "00401000", [0x40031fe0, 0x14])]
        w = [wine_line(1, 0, "A", "00401000", 0x02f57b80, [0x14])]
        gg, ww = self.logs(g, w)
        r = bt.compare(gg, ww, {0x00401000: "?A@C@@QAEXI@Z"})
        self.assertIsNone(r["first"])

    def test_control_divergence(self):
        g = [guest_line(1, 0, "A", "00401000", [1]), guest_line(2, 0, "X", "00409000", [1]),
             guest_line(3, 0, "C", "00403000", [1])]
        w = [wine_line(1, 0, "A", "00401000", 0, [1]), wine_line(2, 0, "Y", "00408000", 0, [1]),
             wine_line(3, 0, "C", "00403000", 0, [1])]
        gg, ww = self.logs(g, w)
        r = bt.compare(gg, ww, {})
        self.assertEqual(r["first"]["kind"], "control")
        self.assertEqual(r["matched"], 2)


if __name__ == "__main__":
    unittest.main()
