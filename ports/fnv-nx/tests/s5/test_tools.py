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
import re
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
        self.assertEqual(d["main_thread_stack_size"], 0x400000)   # NX1: 4x the PC exe's 1 MiB reserve, AArch64 frames are larger
        # 8 syscall descriptors (0xC0 SVCs / 24 per descriptor) + 6 others
        self.assertEqual(len(d["kernel_caps"]), 14)
        app_type = [c for c in d["kernel_caps"] if c & 0x3FFF == 0x1FFF]
        self.assertEqual([(c >> 14) & 7 for c in app_type], [1])
        # kernel flags: cores 0..3 (core 3 for the placement opt-in, toolchain/host/placement.h), default core 0
        kf = [c for c in d["kernel_caps"] if c & 0xF == 0x7]
        self.assertEqual(len(kf), 1)
        self.assertEqual(((kf[0] >> 16) & 0xFF, (kf[0] >> 24) & 0xFF), (0, 3))
        self.assertEqual(self.app_json()["default_cpu_id"], 0)

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
        self.assertEqual(rows[1][1:], (0, 0x1, 0, 0))       # main: core 0 alone, never core 3
        self.assertEqual(rows[4][1:], (1, 0x2, 0x2B, 0))    # render worker
        self.assertEqual(emu_guest.place(0x102, 0, 0)[1:], (2, 0x6, 0x2C))
        self.assertEqual(emu_guest.place((1 << 64) - 1, 1, 0)[1:], (2, 0x6, 0x2C))
        # core 3 opt-in: IO, audio, tasklets and the default row may use cores 1-3; main, render, AI, loading not
        for role in (0, 5, 6, 7):
            self.assertEqual(emu_guest.place(0x100 | role, 2, 0, core3=True)[1:], (3, 0xE, 0x2C))
            self.assertEqual(emu_guest.place(0x100 | role, 2, 0)[1:3], (1, 0x6))   # seq 2 of cores 1-2
        for role, ideal, mask in ((1, 0, 0x1), (2, 2, 0x6), (3, 1, 0x6), (4, 1, 0x2), (8, 1, 0x6)):
            self.assertEqual(emu_guest.place(0x100 | role, 0, 0, core3=True)[1:3], (ideal, mask))


class FaultTableTest(unittest.TestCase):
    """tests/s5/known_faults.txt, the emulator's --stub-faults table, parses; every entry has a known action."""

    def test_known_faults(self):
        sys.path.insert(0, HERE)
        import emu_guest
        stubs, pre = emu_guest.parse_fault_table(os.path.join(HERE, "known_faults.txt"))
        self.assertIn(("0x00aa3e40", "use-malloc:x1"), stubs)
        self.assertNotIn("0x00a8a5f0", [f for f, _ in pre])   # NiMainSDM: S1's INIT1 static calls it now
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


class ExeSlotPlanTest(unittest.TestCase):
    """emu_guest.exe_slot_plan, the exe-vtables stand-in for S2c's link-time slot fill: which trap slots of a native
    vtable take the native of the exe vftable's slot."""

    @staticmethod
    def plan(slots, exe, natives):
        sys.path.insert(0, HERE)
        import emu_guest
        return emu_guest.exe_slot_plan(slots, exe, natives)

    # exe functions 0x401000 + 0x10 * i; their natives 0x7000 + 0x10 * i
    NAT = {0x401000 + 0x10 * i: 0x7000 + 0x10 * i for i in range(12)}

    def exe(self, *i):
        return [0x401000 + 0x10 * k for k in i]

    def nat(self, i):
        return ("native", f"_ZN1A2f{i}Ev", 0x7000 + 0x10 * i)

    def trap(self, i):
        return ("trap", f"_ZN1A2v{i}Ev", 0x9000 + 4 * i)

    def test_plain(self):
        slots = [self.nat(0), self.trap(1), self.nat(2), self.trap(3)]
        self.assertEqual(self.plan(slots, self.exe(0, 1, 2, 3), self.NAT), ([(1, 0x7010), (3, 0x7030)], "ok"))

    def test_trap_without_native_stays(self):
        slots = [self.nat(0), self.trap(1)]
        exe = self.exe(0) + [0x00500000]   # no F row for the exe's slot 1
        self.assertEqual(self.plan(slots, exe, self.NAT), ([], "ok"))

    def test_past_exe_end_stays(self):
        self.assertEqual(self.plan([self.nat(0), self.trap(1)], self.exe(0), self.NAT), ([], "ok"))

    def test_destructor_pair(self):
        # GCC: f0, D2, D0, then native k = exe k - 1; MSVC: f0, scalar deleting dtor, f2, f3
        slots = [self.nat(0), ("native", "_ZN1AD2Ev", 0x8000), ("native", "_ZN1AD0Ev", 0x8010),
                 self.nat(2), self.trap(4)]
        fills, status = self.plan(slots, self.exe(0, 1, 2, 3), self.NAT)
        self.assertEqual((fills, status), ([(4, 0x7030)], "dtor"))
        # the pair itself is never filled, even as traps
        slots = [self.nat(0), ("trap", "_ZN1AD1Ev", 0x9100), ("trap", "_ZN1AD0Ev", 0x9104), self.trap(3)]
        self.assertEqual(self.plan(slots, self.exe(0, 1, 2), self.NAT), ([(3, 0x7020)], "dtor"))

    def test_shifted_is_misaligned(self):
        # every native sits one exe slot later than its own index: the mapping is off, nothing is filled
        slots = [self.nat(1), self.nat(2), self.trap(9), self.nat(4)]
        self.assertEqual(self.plan(slots, self.exe(0, 1, 2, 3, 4), self.NAT), ([], "misaligned"))

    def test_folded_exe_function_is_ok(self):
        # the exe folds one empty body into many slots: a different function in a native's own slot is no evidence
        empty = 0x401000 + 0x10 * 11
        slots = [self.nat(0), ("native", "_ZN1A5emptyEv", 0x8000), self.trap(2), self.nat(3)]
        exe = [self.exe(0)[0], empty, self.exe(2)[0], self.exe(3)[0]]
        self.assertEqual(self.plan(slots, exe, self.NAT), ([(2, 0x7020)], "ok"))
        # ... nor is the folded function in a nearby slot, when the natives agree more often than not
        exe = [self.exe(0)[0], empty, empty, self.exe(3)[0]]
        self.assertEqual(self.plan(slots, exe, self.NAT), ([(2, 0x70b0)], "ok"))

    def test_known_faults_switches_it_on(self):
        sys.path.insert(0, HERE)
        import emu_guest
        self.assertIn("exe-vtables", emu_guest.fault_table_options(os.path.join(HERE, "known_faults.txt")))


class MilestoneTest(unittest.TestCase):
    """tests/s5/milestones.txt (emu_guest.py --milestones) parses into the five boot milestones in boot order, the
    store watch reads the right word of a wide store, and the farm report shows the summary of a boot that ended by
    itself (no STOPPED line)."""

    def test_table(self):
        sys.path.insert(0, HERE)
        import emu_guest
        rows = emu_guest.parse_milestones(emu_guest.MILESTONES)
        names = list(dict.fromkeys(n for n, _, _ in rows))
        self.assertEqual(names, ["winmain", "renderer-init", "main-menu", "first-frame", "first-cell"])
        self.assertIn(("winmain", "call", "0x0086a850"), rows)
        self.assertIn(("first-cell", "store", "*0x011dea3c+0x40"), rows)
        rxs = {}
        for n, k, t in rows:
            if t.startswith("re:"):
                rxs.setdefault(n, []).append(re.compile(t[3:]))

        class Any:   # a milestone is reached by any of its lines
            def __init__(self, l):
                self.l = l

            def search(self, s):
                return any(r.search(s) for r in self.l)
        rx = {n: Any(l) for n, l in rxs.items()}
        self.assertTrue(rx["renderer-init"].search("_ZN15Null_IDirect3D912CreateDeviceEj10_D3DDEVTYPE"))
        self.assertTrue(rx["renderer-init"].search("_ZN11PxDirect3D912CreateDeviceEj10_D3DDEVTYPE"))
        self.assertTrue(rx["first-frame"].search("_ZN21Null_IDirect3DDevice97PresentEPK7tagRECT"))
        self.assertTrue(rx["first-frame"].search("_ZN24Null_IDirect3DSwapChain97PresentEPK7tagRECT"))
        self.assertFalse(rx["first-frame"].search("_ZN14E7CCD0Renderer12PresentFrameEv"))
        # the native-render build (tests/s5/native_render.py): nx::Renderer::Init, nx::Renderer::Do_EndFrame
        self.assertTrue(rx["renderer-init"].search("_ZN2nx8Renderer4InitEPNS_7BackendEjjjj"))
        self.assertTrue(rx["first-frame"].search("_ZN2nx8Renderer11Do_EndFrameEv"))
        self.assertFalse(rx["first-frame"].search("_ZN2nx6engine13NiDX9Renderer11Do_EndFrameEv"))
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "m.txt")
            for bad in ("x call\n", "x jump 0x1\n", "x store 0x1+\n", "X call 0x1\n", "x call re:(\n"):
                with open(p, "w") as f:
                    f.write(bad)
                with self.assertRaises((ValueError, re.error)):
                    emu_guest.parse_milestones(p)

    def test_stored_word(self):
        sys.path.insert(0, HERE)
        import emu_guest
        sw = emu_guest.Emu.stored_word
        self.assertEqual(sw(0x100, 4, 0x1234, 0x100), 0x1234)
        self.assertEqual(sw(0x100, 8, 0x5555000000000000 | 7, 0x104), 0x55550000)   # stp w, w: the upper word
        self.assertIsNone(sw(0x100, 4, 0, 0x100))                                     # zero: not reached
        self.assertEqual(sw(0x100, 4, 0, 0x100, nonzero=False), 0)
        self.assertIsNone(sw(0x102, 2, 0xFFFF, 0x100))                                # partial store
        self.assertIsNone(sw(0x0FC, 8, 1, 0x100 + 4))                                 # does not cover it

    def test_store_watch_follows_pointer(self):
        """'store *A+N' under Unicorn: the pointer stored at A is followed, then the store to it +N reaches it"""
        sys.path.insert(0, HERE)
        import emu_guest
        from unicorn import UC_ARCH_ARM64, UC_MODE_ARM, Uc
        from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3
        e = emu_guest.Emu.__new__(emu_guest.Emu)
        e.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        e.calls, e.cur, e.code_syms, e.miles = 0, None, {}, {}
        uc.mem_map(0x1000, 0x1000)       # code
        uc.mem_map(0x01100000, 0x1000)   # "data window": the pointer at 0x01100010
        uc.mem_map(0x20000000, 0x1000)   # "heap": the object
        code = struct.pack("<4I", 0xB9000001, 0xB9004043, 0xB9004043, 0xD503201F)   # str w1,[x0]; str w3,[x2,#64] x2
        uc.mem_write(0x1000, code)
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "m.txt")
            with open(p, "w") as f:
                f.write("cell store *0x01100010+0x40\nother store 0x01100020\n")
            e.install_milestones(p)
        for r, v in ((UC_ARM64_REG_X0, 0x01100010), (UC_ARM64_REG_X1, 0x20000000), (UC_ARM64_REG_X2, 0x20000000),
                     (UC_ARM64_REG_X3, 0x1234)):
            uc.reg_write(r, v)
        uc.emu_start(0x1000, 0x1000 + len(code))
        self.assertEqual(list(e.miles), ["cell"])
        self.assertEqual(e.milestone_lines()[1], "milestone\tother\t-")

    def test_r0_milestones_file(self):
        sys.path.insert(0, HERE)
        import boot_milestones
        with tempfile.TemporaryDirectory() as t:
            raw = os.path.join(t, "raw.json")
            _write(raw, json.dumps({"milestone": "winmain", "peak_heap_bytes": 4096, "milestones": [
                {"name": n, "reached": n == "winmain"} for n in boot_milestones.KEYS],
                "stop": {"reason": "STOPPED: x", "messages": []}}).encode())
            out = os.path.join(t, "milestones.json")
            boot_milestones.main([out, raw, os.path.join(t, "missing.json"), "--image", "player exe"])
            doc = json.loads(_read(out))
        self.assertEqual({k: doc[k] for k in ("winmain", "main_menu", "first_frame", "first_cell")},
                         {"winmain": True, "main_menu": False, "first_frame": False, "first_cell": False})
        self.assertEqual((doc["renderer_init"], doc["milestone"], doc["boot_peak_heap_bytes"], doc["image"]),
                         (False, "winmain", 4096, "player exe"))
        self.assertIsNone(doc["stub"]["winmain"])   # no summary: unknown, not false

    def test_report_without_stop_line(self):
        sys.path.insert(0, os.path.join(HERE, "..", "..", "tools", "farm"))
        import full_report
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "boot", "stub"))
            _write(os.path.join(t, "boot", "stub", "emu_stub.log"), b"   fnvnx boot: calling WinMain\nPASS\n")
            _write(os.path.join(t, "boot", "stub", "boot_summary_stub.json"), json.dumps({
                "milestone": "winmain", "milestones": [{"name": "winmain", "reached": True},
                                                       {"name": "renderer-init", "reached": False}],
                "stop": {"reason": "guest returned 0x0", "messages": ["[fnvnx] MessageBox [Fallout]: x"]}}).encode())
            text = "\n".join(full_report.stub_boot(t, lambda s, n=200: str(s)[:n]))
        self.assertIn("stop: guest returned 0x0 / [fnvnx] MessageBox [Fallout]: x", text)
        self.assertIn("game milestones: winmain yes, renderer-init no; last reached: winmain", text)


class NullD3d9Test(unittest.TestCase):
    """toolchain/game/null_d3d9.cpp is what gen_null_d3d9.py makes of shim/include/d3d9.h, and it registers
    d3d9.dll: the engine reaches Direct3DCreate9 only through LoadLibraryA("D3D9.DLL") + GetProcAddress, so a
    null-device guest without the module stops at "Failed to initialize renderer." (the first boot on the real exe)."""

    def test_generated_and_registers_module(self):
        game = os.path.join(TC, "game")
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "null_d3d9.cpp")
            subprocess.run([sys.executable, "-I", os.path.join(game, "gen_null_d3d9.py"),
                            os.path.join(TC, "..", "shim", "include", "d3d9.h"), out], check=True,
                           capture_output=True)
            gen = _read(out)
        committed = _read(os.path.join(game, "null_d3d9.cpp"))
        self.assertEqual(gen, committed, "null_d3d9.cpp is stale: rerun gen_null_d3d9.py")
        src = committed.decode()
        self.assertIn('extern "C" void fnvnx_d3d9_register(void) {', src)   # boot.cpp's weak hook
        self.assertIn('fnvnx_register_module("d3d9.dll", null_d3d9_export);', src)
        exports = src.split("null_d3d9_export(const char *proc) {", 1)[1].split("};", 1)[0]
        self.assertIn('{"Direct3DCreate9", (FARPROC)Direct3DCreate9}', exports)
        self.assertIn('{"D3DPERF_SetOptions", (FARPROC)D3DPERF_SetOptions}', exports)   # the exe's one IAT import
        self.assertNotIn("Direct3DCreate9Ex", exports)   # as Nine: the engine takes its Direct3DCreate9 path


class InitOrderTest(unittest.TestCase):
    """toolchain/game/init_order.py: no game unit's static constructor before the platform's, the runtime libraries'
    or the boot's (the order toolchain/guest/guest.ld and platform/os/os.h FNVNX_INIT_* give)."""

    MAP = (".text           0x30000000     0x100\n"
           ".init_array     0x3177c000       0x{size:x}\n"
           "                0x3177c000                        PROVIDE (__init_array_start = .)\n"
           " *(SORT_BY_INIT_PRIORITY(.init_array.*) SORT_BY_INIT_PRIORITY(.ctors.*))\n"
           " .init_array.00101\n"
           "                0x3177c000        0x4 /b/obj/platform_crt_heap.c.o\n"
           " .init_array.00110\n"
           "                0x3177c004        0x4 /b/obj/toolchain_game_boot.cpp.o\n"
           " *(.init_array .ctors)\n"
           "{rest}"
           "                0x3177c0f0                        PROVIDE (__init_array_end = .)\n"
           "\n.fini_array     0x3177c04c        0xc\n"
           " .fini_array    0x3177c04c        0x4 /b/obj/late_platform.cpp.o\n")

    def run_map(self, rest, size):
        sys.path.insert(0, os.path.join(HERE, "..", "..", "toolchain", "game"))
        import init_order
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "fnvnx.map")
            with open(p, "w") as f:
                f.write(self.MAP.format(size=size, rest=rest))
            lst = os.path.join(t, "game_objects.list")
            with open(lst, "w") as f:
                f.write('"/s2/fixed/src_a.cpp.o"\n"/s2/fixed/src_b.cpp.o"\n')
            return init_order.check(init_order.init_array_entries(p), init_order.read_game_list(lst))

    def test_good(self):
        rest = (" .init_array    0x3177c008        0x4 /tc/lib/crtbegin.o\n"
                " .init_array    0x3177c00c        0x4 /tc/lib/libstdc++.a(eh_alloc.o)\n"
                " .init_array    0x3177c010        0x8 /s2/fixed/src_a.cpp.o\n")
        rows, bad = self.run_map(rest, 0x18)
        self.assertEqual([r[1] for r in rows], ["own", "own", "runtime", "runtime", "game", "game"])
        self.assertEqual(rows[0][2], "00101")
        self.assertEqual(bad, [])

    def test_platform_after_game(self):
        rest = (" .init_array    0x3177c008        0x4 /s2/fixed/src_b.cpp.o\n"
                " .init_array    0x3177c00c        0x4 /b/obj/platform_win32_time.cpp.o\n"
                " .init_array    0x3177c010        0x4 /tc/lib/libc.a(libc_a-stack_protector.o)\n")
        rows, bad = self.run_map(rest, 0x14)
        self.assertEqual([(r[1], os.path.basename(r[3])) for r in bad],
                         [("own", "platform_win32_time.cpp.o"), ("runtime", "libc.a(libc_a-stack_protector.o)")])


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
