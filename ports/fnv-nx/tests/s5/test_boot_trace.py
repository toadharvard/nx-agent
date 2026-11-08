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

    def test_missing_and_extra_calls_listed(self):
        """T1 (owner: the seams as a list): calls the exe makes and the guest does not, and the reverse, are
        divergences in the list with both caller chains; the first divergence stays the first value difference"""
        g = [guest_line(1, 0, "A", "00401000", [0x11f6238, 5]),
             guest_line(2, 1, "X", "00409000", [0x11f6238, 1]),      # extra: the exe never calls X here
             guest_line(3, 1, "C", "00403000", [0x11f6238, 7]),
             guest_line(4, 1, "D", "00404000", [0x22, 9])]            # D's receiver differs
        w = [wine_line(1, 0, "A", "00401000", 0x11f6238, [5]),
             wine_line(2, 1, "C", "00403000", 0x11f6238, [7]),
             wine_line(3, 1, "B", "00402000", 0x11f6238, [1]),       # missing: the guest never calls B
             wine_line(4, 1, "B", "00402000", 0x11f6238, [2]),
             wine_line(5, 1, "D", "00404000", 0x11f6238, [3])]
        gg, ww = self.logs(g, w)
        r = bt.compare(gg, ww, {})
        kinds = [d["kind"] for d in r["divergences"]]
        self.assertEqual(kinds[:2], ["extra", "missing"], kinds)
        ex, mi = r["divergences"][0], r["divergences"][1]
        self.assertEqual((ex["guest"].name, ex["wine"].name), ("X", "C"))
        self.assertIn("and 1 more", mi["detail"])
        self.assertEqual([c.name for c in bt.path(mi["wine"])], ["A", "B"])   # the exe's caller chain
        self.assertEqual([c.name for c in bt.path(mi["guest"])], ["A", "D"])  # where the guest goes on
        self.assertEqual(r["first"]["kind"].split()[0], "arg")
        self.assertEqual(r["first"]["guest"].name, "D")
        self.assertEqual(len(bt.compare(gg, ww, {}, limit=1)["divergences"]), 1)

    def test_wide_resync_after_control(self):
        """T1: no common call within --resync, but one further on: a control divergence, then the walk goes on
        (the later calls are compared: their argument difference is found)"""
        g = [guest_line(1, 0, "A", "00401000", [0x11f6238, 5])]
        w = [wine_line(1, 0, "A", "00401000", 0x11f6238, [5])]
        seq = 2
        for k in range(5):       # the guest's own five, then the common Z
            g.append(guest_line(seq, 1, "G%d" % k, "%08x" % (0x00500000 + k), [0x11f6238])); seq += 1
        g.append(guest_line(seq, 1, "Z", "00409000", [0x22, 1]))
        for k in range(8):       # the exe's own eight, then Z with another receiver
            w.append(wine_line(2 + k, 1, "W%d" % k, "%08x" % (0x00600000 + k), 0x11f6238, [1]))
        w.append(wine_line(10, 1, "Z", "00409000", 0x11f6238, [1]))
        gg, ww = self.logs(g, w)
        r = bt.compare(gg, ww, {}, resync=10)   # the level is too short for anchors; Z is 5 + 8 = 13 away
        kinds = [d["kind"] for d in r["divergences"]]
        self.assertEqual(kinds[0], "control")
        self.assertIn("resynced 5 guest and 8 exe calls on", r["divergences"][0]["detail"])
        self.assertTrue(any(d["guest"].name == "Z" and d["kind"].startswith("arg") for d in r["divergences"]), kinds)

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


class Diff(unittest.TestCase):
    def test_exit_codes(self):
        d = tempfile.mkdtemp()
        g, w = os.path.join(d, "g.tsv"), os.path.join(d, "w.tsv")
        open(g, "w").write(guest_line(1, 0, "A", "00401000", [0x01082504]))
        open(w, "w").write(wine_line(1, 0, "A", "00401000", 0x01082504, []))
        self.assertEqual(bt.main(["diff", w, g]), 0)
        open(w, "w").write(wine_line(1, 0, "A", "00401000", 0x010824fc, [0x010824fc]))
        self.assertEqual(bt.main(["diff", w, g, "--json", os.path.join(d, "o.json")]), 1)


if __name__ == "__main__":
    unittest.main()
