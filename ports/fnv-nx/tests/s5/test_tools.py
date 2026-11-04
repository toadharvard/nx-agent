#!/usr/bin/env python3
"""Unit tests for the S5 packers (toolchain/tools).

    python3 -I tests/s5/test_tools.py [-v]

Optional references: set FNVNX_REFTOOLS to a directory holding switch-tools' npdmtool, elf2nso and
build_pfs0 built for the host (gcc npdmtool.c cJSON.c; gcc elf2nso.c sha256.c -llz4; gcc build_pfs0.c);
the outputs are then compared byte for byte (NSO: header fields and decompressed segments, since LZ4
encoders may differ). FNVNX_TEST_ELF64 / FNVNX_TEST_ELF32 name linked images to convert (host.elf, guest.elf).
"""
from __future__ import annotations

import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TC = os.path.normpath(os.path.join(HERE, "..", "..", "toolchain"))
sys.path.insert(0, os.path.join(TC, "tools"))
import build_pfs0  # noqa: E402
import elf2nso  # noqa: E402
import lz4block  # noqa: E402
import npdmtool  # noqa: E402
import nxelf  # noqa: E402

REF = os.environ.get("FNVNX_REFTOOLS")
HBL_JSON = os.environ.get("FNVNX_HBL_JSON")  # nx-hbloader/hbl.json, classic npdmtool schema


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def ref(tool):
    p = os.path.join(REF or "", tool)
    return p if REF and os.access(p, os.X_OK) else None


class Lz4Test(unittest.TestCase):
    def test_roundtrip(self):
        rnd = random.Random(5)
        cases = [b"", b"a", b"abcd" * 3, bytes(100000), bytes(rnd.randrange(256) for _ in range(5000)),
                 b"".join(rnd.choice([b"MOD0", b"\0\0\0\0", b"hello world", bytes([rnd.randrange(256)])])
                          for _ in range(20000))]
        for c in cases:
            comp = lz4block._compress_py(c)
            self.assertEqual(_py_decompress(comp, len(c)), c)

    def test_compresses(self):
        self.assertLess(len(lz4block._compress_py(bytes(65536))), 400)


def _py_decompress(src, size):
    saved = lz4block._lz4
    lz4block._lz4 = None
    try:
        return lz4block.decompress(src, size)
    finally:
        lz4block._lz4 = saved


class NpdmTest(unittest.TestCase):
    def app_json(self):
        with open(os.path.join(TC, "npdm", "fnvnx-app.json")) as f:
            return json.load(f)

    def test_app_npdm(self):
        d = npdmtool.dump_npdm(npdmtool.build_npdm(self.app_json()))
        self.assertTrue(d["is_64_bit"])
        self.assertEqual(d["address_space"], "32BitNoReserved")
        self.assertEqual(d["mmu_flags"], 0b101)
        self.assertEqual(d["main_thread_stack_size"], 0x100000)
        # 8 syscall descriptors (0xC0 SVCs / 24 per descriptor) + 6 others
        self.assertEqual(len(d["kernel_caps"]), 14)
        app_type = [c for c in d["kernel_caps"] if c & 0x3FFF == 0x1FFF]
        self.assertEqual([(c >> 14) & 7 for c in app_type], [1])

    def test_address_space_names(self):
        for name, val in (("32bit", 0), ("64bit_old", 1), ("32bit_no_reserved", 2), ("64bit", 3), (2, 2)):
            j = self.app_json()
            j["address_space_type"] = name
            self.assertEqual((npdmtool.build_npdm(j)[0xC] >> 1) & 3, val)

    def test_syscalls_all_matches_list(self):
        j1, j2 = self.app_json(), self.app_json()
        j2["kernel_capabilities"][1]["value"] = {f"s{n}": f"0x{n:02X}" for n in range(0xC0)}
        self.assertEqual(npdmtool.build_npdm(j1), npdmtool.build_npdm(j2))

    @unittest.skipUnless(ref("npdmtool"), "FNVNX_REFTOOLS not set")
    def test_matches_switch_tools(self):
        js = []
        if HBL_JSON:
            with open(HBL_JSON) as f:
                js.append(json.load(f))
        j = self.app_json()
        j["address_space_type"] = 2
        j["kernel_capabilities"][1]["value"] = {f"svc{n:02X}": f"0x{n:02X}" for n in range(0xC0)}
        js.append(j)
        for j in js:
            with tempfile.TemporaryDirectory() as td:
                jp, op = os.path.join(td, "in.json"), os.path.join(td, "out.npdm")
                with open(jp, "w") as f:
                    json.dump(j, f)
                subprocess.run([ref("npdmtool"), jp, op], check=True, capture_output=True)
                self.assertEqual(npdmtool.build_npdm(j), _read(op), j["name"])


class Pfs0Test(unittest.TestCase):
    def test_roundtrip(self):
        files = [("main", b"A" * 1000), ("subsdk0", b"B" * 77), ("main.npdm", b"C" * 5)]
        blob = build_pfs0.build_pfs0(files)
        parsed = build_pfs0.parse_pfs0(blob)
        self.assertEqual([p[0] for p in parsed], [f[0] for f in files])
        for (name, off, size), (_, data) in zip(parsed, files):
            self.assertEqual(blob[off:off + size], data)
        self.assertEqual(struct.unpack_from("<I", blob, 8)[0] % 0x20, 0)  # string table padded to 0x20

    @unittest.skipUnless(ref("build_pfs0"), "FNVNX_REFTOOLS not set")
    def test_matches_switch_tools(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "exefs") + "/"
            os.mkdir(d)
            files = {"main": os.urandom(3000), "main.npdm": os.urandom(700)}
            for k, v in files.items():
                _write(d + k, v)
            out = os.path.join(td, "ref.nsp")
            subprocess.run([ref("build_pfs0"), d, out], check=True, capture_output=True)
            r = _read(out)
            order = [n for n, _, _ in build_pfs0.parse_pfs0(r)]  # readdir order
            self.assertEqual(build_pfs0.build_pfs0([(n, files[n]) for n in order]), r)


def tiny_elf(is64: bool, base: int = 0) -> bytes:
    """A synthetic AArch64 ELF with text/rodata/data PT_LOADs and a build-id note."""
    segs = [(base, b"\x1f\x20\x03\xd5" * 64, 5, 0), (base + 0x1000, b"rodata!" * 30, 4, 0), (base + 0x2000, b"data" * 40, 6, 0x300)]
    note = struct.pack("<III", 4, 20, 3) + b"GNU\0" + bytes(range(20))
    ehsize, phsize = (64, 56) if is64 else (52, 32)
    off = ehsize + phsize * 3
    body, phdrs = b"", b""
    for va, data, fl, bss in segs:
        if is64:
            phdrs += struct.pack("<IIQQQQQQ", 1, fl, off + len(body), va, va, len(data), len(data) + bss, 0x1000)
        else:
            phdrs += struct.pack("<IIIIIIII", 1, off + len(body), va, va, len(data), len(data) + bss, fl, 0x1000)
        body += data
    note_off = off + len(body)
    body += note
    shstr = b"\0.note.gnu.build-id\0.shstrtab\0"
    shstr_off = off + len(body)
    body += shstr
    shoff = off + len(body)
    if is64:
        sh = bytes(64) + struct.pack("<IIQQQQIIQQ", 1, 7, 2, 0, note_off, len(note), 0, 0, 4, 0)
        sh += struct.pack("<IIQQQQIIQQ", 20, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0)
        eh = b"\x7fELF\x02\x01\x01" + bytes(9) + struct.pack("<HHIQQQIHHHHHH", 2 if base else 3, 183, 1, 0, ehsize, shoff, 0,
                                                              ehsize, phsize, 3, 64, 3, 2)
    else:
        sh = bytes(40) + struct.pack("<IIIIIIIIII", 1, 7, 2, 0, note_off, len(note), 0, 0, 4, 0)
        sh += struct.pack("<IIIIIIIIII", 20, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0)
        eh = b"\x7fELF\x01\x01\x01" + bytes(9) + struct.pack("<HHIIIIIHHHHHH", 2 if base else 3, 183, 1, 0, ehsize, shoff, 0,
                                                              ehsize, phsize, 3, 40, 3, 2)
    return eh + phdrs + body + sh


class NsoTest(unittest.TestCase):
    def check(self, elf_bytes):
        e = nxelf.Elf(elf_bytes)
        for compress in (True, False):
            nso = elf2nso.build_nso(e, compress=compress)
            info = elf2nso.parse_nso(nso)
            self.assertEqual(info["flags"], 0x3F if compress else 0x38)
            self.assertTrue(all(s["hash_ok"] for s in info["segments"]))
            for s, p in zip(info["segments"], e.loads()):
                self.assertEqual(s["bytes"], e.segment_bytes(p))
                self.assertEqual(s["dst"], p.vaddr - e.loads()[0].vaddr)
            self.assertEqual(info["segments"][2]["bss"], e.loads()[2].memsz - e.loads()[2].filesz)
            self.assertEqual(bytes.fromhex(info["build_id"])[:len(e.build_id())], e.build_id())

    def test_elf32(self):
        self.check(tiny_elf(False))

    def test_elf64(self):
        self.check(tiny_elf(True))

    def test_elf32_fixed_address(self):
        self.check(tiny_elf(False, base=0x10000000))   # ET_EXEC guest: offsets relative to its base

    def test_guest_header_patch(self):
        # an fnv-nx guest (FNXG at text+8) gets link base, page-aligned size and the relocatable flag at +16..+24
        # (bit 0), and --pin sets bit 1 (the host must use the link base)
        for etype, pinned, want in ((2, False, 0), (3, False, 1), (3, True, 3)):
            b = bytearray(tiny_elf(False, base=0x30000000))
            struct.pack_into("<H", b, 16, etype)
            text_off = struct.unpack_from("<I", b, 52 + 4)[0]
            struct.pack_into("<I", b, text_off + 8, elf2nso.GUEST_MAGIC)
            info = elf2nso.parse_nso(elf2nso.build_nso(nxelf.Elf(bytes(b)), pinned=pinned))
            self.assertTrue(all(s["hash_ok"] for s in info["segments"]))
            base, size, flags = struct.unpack_from("<III", info["segments"][0]["bytes"], 16)
            self.assertEqual((base, size, flags), (0x30000000, 0x3000, want))

    def test_rejects_bad_layout(self):
        b = bytearray(tiny_elf(False))
        struct.pack_into("<I", b, 52 + 32 + 8, 0x1800)  # rodata vaddr not page aligned
        with self.assertRaises(nxelf.ElfError):
            elf2nso.build_nso(nxelf.Elf(bytes(b)))

    def compare_ref(self, path):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "ref.nso")
            subprocess.run([ref("elf2nso"), path, out], check=True, capture_output=True)
            r, m = _read(out), elf2nso.build_nso(nxelf.read(path))
            # identical header except LZ4 sizes / file offsets; identical decompressed segments
            for o in (0x0, 0x4, 0x8, 0xC, 0x40, 0xA0):
                n = 0x60 if o == 0xA0 else (0x20 if o == 0x40 else 4)
                self.assertEqual(r[o:o + n], m[o:o + n], hex(o))
            for i in range(3):
                self.assertEqual(r[0x14 + 0x10 * i:0x20 + 0x10 * i], m[0x14 + 0x10 * i:0x20 + 0x10 * i])
            ri, mi = elf2nso.parse_nso(r), elf2nso.parse_nso(m)
            for a, b in zip(ri["segments"], mi["segments"]):
                self.assertEqual(a["bytes"], b["bytes"])

    @unittest.skipUnless(ref("elf2nso"), "FNVNX_REFTOOLS not set")
    def test_matches_switch_tools_elf64(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.elf")
            _write(p, tiny_elf(True))
            self.compare_ref(p)
        if os.environ.get("FNVNX_TEST_ELF64"):
            self.compare_ref(os.environ["FNVNX_TEST_ELF64"])

    @unittest.skipUnless(os.environ.get("FNVNX_TEST_ELF32"), "FNVNX_TEST_ELF32 not set")
    def test_linked_guest(self):
        e = nxelf.read(os.environ["FNVNX_TEST_ELF32"])
        self.assertFalse(e.is64)
        rels = e.relocations()
        self.assertTrue(rels)
        self.assertTrue(all(t == nxelf.R_AARCH64_P32_RELATIVE for _, t, _, _ in rels))
        data = e.loads()[2]
        self.assertTrue(all(data.vaddr <= o < data.vaddr + data.memsz for o, _, _, _ in rels),
                        "relocation outside the data segment")
        self.check(e.data)


class PlacementTest(unittest.TestCase):
    """toolchain/host/placement.h natively (tests/s5/test_placement.c), and the emulator's copy of the table."""

    @unittest.skipUnless(__import__("shutil").which("cc"), "no native C compiler")
    def test_native(self):
        root = os.path.join(HERE, "..", "..")
        with tempfile.TemporaryDirectory() as t:
            exe = os.path.join(t, "test_placement")
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Werror", "-I", os.path.join(root, "toolchain", "include"),
                            os.path.join(HERE, "test_placement.c"), "-o", exe], check=True)
            r = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)

    def test_emulator_table(self):
        sys.path.insert(0, HERE)
        import emu_guest
        rows = emu_guest.placement_table()
        self.assertEqual(rows[1][1:], (0, 0x1, 0))          # main: core 0 alone
        self.assertEqual(rows[4][1:], (1, 0x2, 0x2B))       # render worker
        self.assertEqual(emu_guest.place(0x102, 0, 0)[1:], (2, 0x6, 0x2C))
        self.assertEqual(emu_guest.place((1 << 64) - 1, 1, 0)[1:], (2, 0x6, 0x2C))


class FaultTableTest(unittest.TestCase):
    """tests/s5/known_faults.txt, the emulator's --stub-faults table, parses; every entry has a known action."""

    def test_known_faults(self):
        sys.path.insert(0, HERE)
        import emu_guest
        stubs, pre = emu_guest.parse_fault_table(os.path.join(HERE, "known_faults.txt"))
        self.assertIn(("0x00aa3e40", "use-malloc:x1"), stubs)
        self.assertIn(("0x00a8a5f0", 0), pre)
        names = [n for n, _ in stubs]
        self.assertEqual(len(names), len(set(names)), "a function listed twice")
        for n in names:
            self.assertRegex(n, r"^(0x[0-9a-f]{8}|[A-Za-z_]\w*)$")

    def test_bad_lines(self):
        sys.path.insert(0, HERE)
        import emu_guest
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "f.txt")
            for bad in ("0x00401000 return1\n", "0x00401000 malloc:x1 extra\n", "before-winmain\n"):
                with open(p, "w") as f:
                    f.write(bad)
                with self.assertRaises(ValueError):
                    emu_guest.parse_fault_table(p)
            with open(p, "w") as f:
                f.write("# c\nInitHolder  # why\n0x00aa1070 use-malloc:x0\nbefore-winmain 0x00a8a5f0 0x10\n")
            self.assertEqual(emu_guest.parse_fault_table(p),
                             ([("InitHolder", "return0"), ("0x00aa1070", "use-malloc:x0")], [("0x00a8a5f0", 16)]))


class HandVptrsTest(unittest.TestCase):
    """tests/s5/hand_vptrs.py on a two-unit tree: store forms, binding and rule (no ELF: no native-vtable rules)."""

    def test_rules(self):
        src = (
            "// @fnv 0x00401000\n"
            "FNVNX_KEEP A::A() { *(int*)this = (int)&DAT_01011000; }\n"          # image-bound, class A -> S1:virtuals
            "struct Q { int* vtable; Q() { vtable = &DAT_01011100; } };\n"       # inline ctor member store, bound -> ok
            "void Free(void* p) { *(void**)p = (void*)&DAT_01011200; }\n"       # free function, deref -> S1:class
            "void B::B() { *(const char**)this = \"@K@\"; m_x = &DAT_01011300; }\n"   # literal; m_x not a vptr
            "void C::C() { *(int*)((char*)this + 8) = (int)&DAT_01011400; }\n"  # secondary, V entry -> S2:mapped
            "void D::f() { *(int*)this = (int)&DAT_00401234; }\n")              # .text address: not a vptr
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "tree", "build"))
            os.makedirs(os.path.join(t, "tree", "src"))
            os.makedirs(os.path.join(t, "link", "out"))
            with open(os.path.join(t, "tree", "src", "u.cpp"), "w") as f:
                f.write(src)
            with open(os.path.join(t, "tree", "build", "units.json"), "w") as f:
                json.dump({"units": [{"source": "src/u.cpp", "symbols": {}}]}, f)
            with open(os.path.join(t, "link", "out", "aliases.ld"), "w") as f:
                f.write("__fnv_01011000 = 0x01011000;\n__fnv_01011100 = _ZTV1Q + 8;\n__fnv_01011200 = 0x01011200;\n"
                        "__fnv_01011400 = 0x01011400;\n")
            with open(os.path.join(t, "link", "out", "addrmap.tsv"), "w") as f:
                f.write("01011400\tV\t_ZTV1C\t_ZTV1C\tu.o\tsecondary\t8\t\t\n")
            r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "hand_vptrs.py"), "--tree", os.path.join(t, "tree"),
                                "--link", os.path.join(t, "link"), "--out", os.path.join(t, "hv")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(t, "hv.tsv")) as f:
                rows = {l.split("\t")[0]: l.rstrip("\n").split("\t") for l in f if not l.startswith("#")}
            self.assertEqual(rows["01011000"][2], "S1:virtuals")
            self.assertEqual(rows["01011000"][7], "A")
            self.assertEqual(rows["01011100"][2], "ok")
            self.assertEqual(rows["01011100"][7], "Q")
            self.assertEqual(rows["01011200"][2:4], ["S1:class", "deref"])
            self.assertEqual(rows["01011400"][2:4], ["S2:mapped", "this+N"])
            self.assertEqual(rows[""][2], "literal")
            self.assertNotIn("01011300", rows)
            self.assertNotIn("00401234", rows)


if __name__ == "__main__":
    unittest.main()
