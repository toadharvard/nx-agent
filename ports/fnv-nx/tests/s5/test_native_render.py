#!/usr/bin/env python3
"""Tests of tests/s5/native_render.py's object loader (link_object) and its PNG writer, on small ILP32 objects we
compile here. Needs aarch64-linux-gnu-gcc (skipped without it).

    python3 -I tests/s5/test_native_render.py
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import native_render as nr  # noqa: E402

CC = shutil.which("aarch64-linux-gnu-gcc")

SRC = r"""
extern int guest_data;
extern int guest_func(int);
extern int maybe(int) __attribute__((weak));
int table[4] = {1, 2, 3, 4};
static const char msg[] = "native";
int *ptr_to_table = &table[2];
const char *ptr_to_msg = msg;
int use_got(void) { return guest_data; }
int call_guest(int x) { return guest_func(x) + 1; }
int read_table(int i) { return table[i]; }
long long wide[2] = {5, 6};
long long read_wide(int i) { return wide[i]; }
int weak_or_zero(int x) { return maybe ? maybe(x) : -1; }
int loop(int n) { int s = 0; for (int i = 0; i < n; i++) if (table[i & 3] & 1) s += i; return s; }
"""


@unittest.skipUnless(CC, "aarch64-linux-gnu-gcc not installed")
class LinkObject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="fnvnx-nr-test-")
        src = os.path.join(cls.d, "t.c")
        open(src, "w").write(SRC)
        cls.obj = os.path.join(cls.d, "t.o")
        subprocess.run([CC, "-mabi=ilp32", "-O2", "-fPIE", "-mno-outline-atomics", "-fno-asynchronous-unwind-tables",
                        "-ffunction-sections", "-fdata-sections", "-c", src, "-o", cls.obj], check=True)
        cls.data = open(cls.obj, "rb").read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def link(self, place=0x06000000, guest=None):
        g = {"guest_data": 0x05001230, "guest_func": 0x05004000} if guest is None else guest
        return nr.link_object(self.data, place, g.get)

    def word(self, blob, place, addr):
        return struct.unpack_from("<I", blob, addr - place)[0]

    def test_data_pointers(self):
        place = 0x06000000
        blob, defined, funcs, nrel = self.link(place)
        self.assertGreater(nrel, 0)
        self.assertEqual(self.word(blob, place, defined["ptr_to_table"]), defined["table"] + 8)
        msg = self.word(blob, place, defined["ptr_to_msg"])
        self.assertEqual(blob[msg - place:msg - place + 7], b"native\0")
        self.assertEqual([self.word(blob, place, defined["table"] + 4 * i) for i in range(4)], [1, 2, 3, 4])
        self.assertIn("use_got", funcs)
        self.assertNotIn("table", funcs)

    def test_got_slot_holds_guest_address(self):
        place = 0x06000000
        blob, defined, funcs, _ = self.link(place)
        words = [self.word(blob, place, a) for a in range(place, place + len(blob) - 3, 4)]
        self.assertIn(0x05001230, words)   # the GOT slot of guest_data

    def test_call_reaches_guest(self):
        place = 0x06000000
        blob, defined, funcs, _ = self.link(place)
        f = funcs["call_guest"]
        for k in range(0, 64, 4):
            w = self.word(blob, place, f + k)
            if w & 0xFC000000 == 0x94000000:   # bl
                disp = ((w & 0x3FFFFFF) ^ 0x2000000) - 0x2000000
                self.assertEqual(f + k + 4 * disp, 0x05004000)
                return
        self.fail("no bl in call_guest")

    def test_adrp_pairs_point_at_the_data(self):
        place = 0x06003FF0   # data crosses a page: the page and the low 12 bits both matter
        blob, defined, funcs, _ = self.link(place)
        f = funcs["read_table"]
        page = lo = None
        for k in range(0, 64, 4):
            a = f + k
            w = self.word(blob, place, a)
            if w & 0x9F000000 == 0x90000000:   # adrp
                imm = ((w >> 29) & 3) | (((w >> 5) & 0x7FFFF) << 2)
                imm = (imm ^ 0x100000) - 0x100000
                page = (a & ~0xFFF) + (imm << 12)
            elif page is not None and w & 0x7F800000 == 0x11000000:   # add (immediate, 32 or 64 bit)
                lo = (w >> 10) & 0xFFF
                break
        self.assertIsNotNone(lo)
        self.assertEqual(page + lo, defined["table"])

    def test_weak_undefined_is_zero_and_missing_names_are_reported(self):
        self.link()   # maybe is weak: no error
        with self.assertRaises(nr.ObjError) as e:
            self.link(guest={"guest_data": 0x05001230})
        self.assertIn("guest_func", str(e.exception))

    def test_not_ilp32(self):
        with self.assertRaises(nr.ObjError):
            nr.link_object(b"\x7fELF\x02" + b"\0" * 60, 0, {}.get)


class Png(unittest.TestCase):
    def test_roundtrip(self):
        d = tempfile.mkdtemp(prefix="fnvnx-nr-png-")
        try:
            p = os.path.join(d, "a.png")
            px = bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 9, 9, 9, 9])
            nr.png(p, 2, 2, px)
            b = open(p, "rb").read()
            self.assertEqual(b[:8], b"\x89PNG\r\n\x1a\n")
            w, h = struct.unpack(">II", b[16:24])
            self.assertEqual((w, h), (2, 2))
            i = b.index(b"IDAT")
            n = struct.unpack(">I", b[i - 4:i])[0]
            raw = zlib.decompress(b[i + 4:i + 4 + n])
            self.assertEqual(raw, b"\0" + px[:8] + b"\0" + px[8:])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
