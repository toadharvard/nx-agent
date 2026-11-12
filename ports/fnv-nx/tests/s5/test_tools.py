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
import types
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
        self.assertNotIn("0x00aa3e40", [n for n, _ in stubs])   # MemoryManager::Allocate runs on the game's heaps
        self.assertNotIn("0x00a8a5f0", [f for f, _ in pre])   # NiMainSDM: S1's INIT1 static calls it now
        names = [n for n, _ in stubs]
        self.assertEqual(len(names), len(set(names)), "a function listed twice")
        for n in names:
            self.assertRegex(n, r"^(0x[0-9a-f]{8}|[A-Za-z_]\w*)$")

    def test_standins_defined(self):
        """every 'standin' line names a function tests/s5/standins.c defines (by its asm label)"""
        sys.path.insert(0, HERE)
        import emu_guest
        stubs, _ = emu_guest.parse_fault_table(os.path.join(HERE, "known_faults.txt"))
        src = open(emu_guest.STANDINS).read()
        names = [n for n, a in stubs if a == "standin"]
        self.assertTrue(names)
        for n in names:
            self.assertRegex(src, r'\b\w+\(void\) __asm__\("%s"\);|__asm__\("%s"\)' % (re.escape(n), re.escape(n)))

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
        self.assertEqual(names, ["winmain", "ini-read", "archive-list", "bsa-opened", "renderer-init", "esm-header",
                                 "masters-loaded", "main-menu", "first-frame", "first-cell"])   # S18's order
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
            for bad in ("x call\n", "x jump 0x1\n", "x store 0x1+\n", "X call 0x1\n", "x call re:(\n", "x all\n",
                        "x all jump 0x1\n", "x any call 0x1\n"):
                with open(p, "w") as f:
                    f.write(bad)
                with self.assertRaises((ValueError, re.error)):
                    emu_guest.parse_milestones(p)

    def test_callok_needs_success(self):
        """'callok F' is reached when F returns 0 to its caller, not when F starts: the native-render hook fails
        CreateDevice without a GPU backend (D3DERR_NOTAVAILABLE), and that boot has no renderer"""
        sys.path.insert(0, HERE)
        import emu_guest
        from unicorn import UC_ARCH_ARM64, UC_MODE_ARM, Uc
        from unicorn.arm64_const import UC_ARM64_REG_SP
        rows = emu_guest.parse_milestones(emu_guest.MILESTONES)
        self.assertEqual({k for n, k, _ in rows if n == "renderer-init"}, {"callok", "calltrue", "ret", "call"})
        for ret, reached in ((0, True), (0x8876086A, False)):
            e = emu_guest.Emu.__new__(emu_guest.Emu)
            e.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
            e.calls, e.cur, e.miles = 0, None, {}
            e.code_syms = {"_ZN15Null_IDirect3D912CreateDeviceEj": 0x1100}
            e.code_addr = lambda n, e=e: e.code_syms.get(n)
            e.sym = lambda a: "x %#x" % a
            uc.mem_map(0x1000, 0x1000)
            uc.mem_map(0x8000, 0x1000)   # stack
            # 0x1000: bl 0x1100; nop    0x1100: movz w0,#lo; movk w0,#hi,lsl 16; ret
            uc.mem_write(0x1000, struct.pack("<2I", 0x94000040, 0xD503201F))
            uc.mem_write(0x1100, struct.pack("<3I", 0x52800000 | ((ret & 0xFFFF) << 5),
                                             0x72A00000 | ((ret >> 16) << 5), 0xD65F03C0))
            uc.reg_write(UC_ARM64_REG_SP, 0x8800)
            with tempfile.TemporaryDirectory() as t:
                p = os.path.join(t, "m.txt")
                with open(p, "w") as f:
                    f.write("renderer-init callok re:CreateDevice\n")
                e.install_milestones(p)
            uc.emu_start(0x1000, 0x1008)
            self.assertEqual("renderer-init" in e.miles, reached, hex(ret))

    # -- prerequisites and compound markers (small Unicorn snippets: functions that only `ret`)
    FN = {"0x00000001": 0x1100, "0x00000002": 0x1200, "0x00000003": 0x1300}

    def mile_emu(self, table, syms=None, fns=None):
        """an Emu with `table` (milestones.txt text) installed; every address of fns (original address -> where) and
        every symbol of syms is a `ret` stub; call(addr) runs `bl addr` as the thread tid"""
        sys.path.insert(0, HERE)
        import emu_guest
        from unicorn import UC_ARCH_ARM64, UC_MODE_ARM, Uc
        from unicorn.arm64_const import UC_ARM64_REG_SP
        fns, syms = dict(fns or self.FN), dict(syms or {})
        e = emu_guest.Emu.__new__(emu_guest.Emu)
        e.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        e.calls, e.cur, e.miles, e.code_syms = 0, None, {}, syms
        e.code_addr = lambda n: fns.get(n, syms.get(n))
        e.sym = lambda a: "x %#x" % a
        uc.mem_map(0x1000, 0x2000)
        uc.mem_map(0x8000, 0x1000)
        uc.mem_map(0x01100000, 0x1000)   # data window
        uc.mem_map(0x20000000, 0x1000)   # heap
        for a in list(fns.values()) + list(syms.values()):
            uc.mem_write(a, struct.pack("<I", 0xD65F03C0))
        uc.reg_write(UC_ARM64_REG_SP, 0x8800)
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "m.txt")
            with open(p, "w") as f:
                f.write(table)
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                e.install_milestones(p)

        stub = [0x2000]

        def call(addr, tid=1):
            e.cur = None if tid == 1 else types.SimpleNamespace(tid=tid)
            at = stub[0]   # a stub of its own each time: Unicorn keeps translated blocks of code written before
            stub[0] += 0x10
            uc.mem_write(at, struct.pack("<3I", 0x94000000 | (((addr - at) >> 2) & 0x3FFFFFF), 0xD503201F,
                                         0xD503201F))
            uc.emu_start(at, at + 8)
        e.call = call
        return e

    def test_out_of_order_is_recorded_not_counted(self):
        e = self.mile_emu("a call 0x00000001\nb call 0x00000002\nc call 0x00000003\n")
        e.call(0x1200)   # b before a
        self.assertEqual(list(e.miles), [])
        self.assertEqual(list(e.mile_ooo), ["b"])
        self.assertEqual(e.mile_ooo["b"]["missing"], ["a"])
        self.assertEqual(e.milestone_lines()[1].split("\t")[:3], ["milestone", "b", "out-of-order"])
        self.assertIn("missing: a", e.milestone_lines()[1])
        s = e.milestone_summary()
        self.assertEqual((s["reached"], s["of"], s["milestone"]), (0, 3, None))
        self.assertEqual([(o["name"], o["missing"]) for o in s["out_of_order"]], [("b", ["a"])])
        self.assertEqual([m["reached"] for m in s["milestones"]], [False, False, False])
        e.call(0x1300)   # c: missing a and b
        self.assertEqual(e.mile_ooo["c"]["missing"], ["a", "b"])
        e.call(0x1100)   # a arrives: b does not count retroactively
        self.assertEqual(list(e.miles), ["a"])
        self.assertNotIn("b", e.miles)
        s = e.milestone_summary()
        self.assertEqual((s["reached"], s["milestone"]), (1, "a"))
        self.assertEqual([o["name"] for o in s["out_of_order"]], ["b", "c"])
        e.call(0x1200)   # a new hit of b does
        self.assertEqual(list(e.miles), ["a", "b"])
        self.assertTrue(e.miles["b"]["was_out_of_order"])
        self.assertEqual(list(e.mile_ooo), ["c"])
        self.assertEqual(e.milestone_summary()["milestone"], "b")
        self.assertEqual(e.milestone_lines()[2].split("\t")[:3], ["milestone", "c", "out-of-order"])

    def test_all_compound_needs_every_part(self):
        e = self.mile_emu("m all call 0x00000001\nm all call 0x00000002\nm all ret 0x00000003\n")
        e.call(0x1100)
        e.call(0x1100)
        self.assertEqual(list(e.miles), [])
        e.call(0x1200)
        self.assertEqual(list(e.miles), [])   # the ret part is still missing
        e.call(0x1300)
        self.assertEqual(list(e.miles), ["m"])
        # a plain line next to a compound is another way; groups with different ids are separate
        e = self.mile_emu("m all:x call 0x00000001\nm all:x call 0x00000002\nm all:y call 0x00000003\n")
        e.call(0x1100)
        e.call(0x1300)
        self.assertEqual(list(e.miles), ["m"])   # the group y alone
        e = self.mile_emu("m all call 0x00000001\nm all call 0x00000002\nm call 0x00000003\n")
        e.call(0x1300)
        self.assertEqual(list(e.miles), ["m"])

    def test_seq_compound_keeps_the_order(self):
        e = self.mile_emu("m seq call 0x00000001\nm seq call 0x00000002\n")
        e.call(0x1200)   # second part first: does not count
        e.call(0x1100)
        self.assertEqual(list(e.miles), [])
        e.call(0x1200)
        self.assertEqual(list(e.miles), ["m"])

    def table_part(self, *names):
        """the lines of milestones.txt of the given milestones, as a table of their own (no prerequisites)"""
        sys.path.insert(0, HERE)
        import emu_guest
        keep = []
        for line in _read(emu_guest.MILESTONES).decode().splitlines():
            f = line.split("#", 1)[0].split()
            if f and f[0] in names:
                keep.append(line)
        return "\n".join(keep) + "\n"

    def test_first_frame_needs_the_loop_entered_on_the_main_thread(self):
        present = "_ZN21Null_IDirect3DDevice97PresentEPK7tagRECT"
        e = self.mile_emu(self.table_part("first-frame"), syms={present: 0x1400},
                          fns={"0x00851d90": 0x1100})
        e.call(0x1400)                  # a Present before the main loop was entered (the loading screen thread)
        self.assertEqual(list(e.miles), [])
        e.call(0x1100, tid=2)           # the loop head on another thread: no
        e.call(0x1400)
        self.assertEqual(list(e.miles), [])
        e.call(0x1100)                  # ProcessQueuedSaveLoad entered on the main thread ...
        e.call(0x1400, tid=5)           # ... a Present of another thread
        self.assertEqual(list(e.miles), [])
        e.call(0x1400)                  # ... then the main thread's Present
        self.assertEqual(list(e.miles), ["first-frame"])
        # the native renderer's end of frame counts the same way
        e = self.mile_emu(self.table_part("first-frame"), syms={"_ZN2nx8Renderer11Do_EndFrameEv": 0x1400},
                          fns={"0x00851d90": 0x1100})
        e.call(0x1400)
        self.assertEqual(list(e.miles), [])
        e.call(0x1100)
        e.call(0x1400)
        self.assertEqual(list(e.miles), ["first-frame"])

    def test_first_cell_needs_the_store_and_a_later_frame(self):
        fns = {"0x00852d20": 0x1100, "0x0086ff70": 0x1200, "0x00950290": 0x1300}
        e = self.mile_emu(self.table_part("first-cell"), fns=fns)
        uc = e.uc
        from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3
        uc.mem_write(0x1900, struct.pack("<3I", 0xB9000001, 0xB9004043, 0xD503201F))   # str w1,[x0]; str w3,[x2,#64]
        player = 0x011dea3c
        uc.mem_map(0x011de000, 0x1000)

        def store(ptr=0x20000000, cell=0x1234):
            for r, v in ((UC_ARM64_REG_X0, player), (UC_ARM64_REG_X1, ptr), (UC_ARM64_REG_X2, ptr),
                         (UC_ARM64_REG_X3, cell)):
                uc.reg_write(r, v)
            uc.emu_start(0x1900, 0x1908)
        # the pointer stored with the cell already set (the old 'already set' shortcut): not enough
        uc.mem_write(0x20000040, struct.pack("<I", 0x1234))
        e.call(0x1100)                  # LoadCellReferences
        uc.reg_write(UC_ARM64_REG_X0, player)
        uc.reg_write(UC_ARM64_REG_X1, 0x20000000)
        uc.emu_start(0x1900, 0x1904)
        e.call(0x1200)
        e.call(0x1300)
        self.assertEqual(list(e.miles), [])
        store()                         # the parentCell store
        e.call(0x1300)                  # UpdateCameraMode in the same frame: no
        self.assertEqual(list(e.miles), [])
        e.call(0x1200)                  # the next frame counted ...
        self.assertEqual(list(e.miles), [])
        e.call(0x1300)                  # ... and UpdateCameraMode in it
        self.assertEqual(list(e.miles), ["first-cell"])

    def test_discover_unwinds_frame(self):
        """--discover: a fault inside a function with a GCC frame returns 0 to its caller with the caller's
        callee-saved registers restored (x19), and a call through a null pointer is skipped at its call site"""
        sys.path.insert(0, HERE)
        import emu_guest
        from unicorn import UC_ARCH_ARM64, UC_MODE_ARM, Uc, UcError
        from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X19
        e = emu_guest.Emu.__new__(emu_guest.Emu)
        e.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        e.cur, e.miles, e.discovered, e.discover_max = None, {}, [], 10
        e.base = e.link_base = 0x1000
        e.size, e.relocatable = 0x1000, False
        e.sym_addrs, e.sym_names, e.code_syms = [0x1000, 0x1100], [["caller"], ["callee"]], {}
        import bisect
        e._bisect = bisect.bisect_right
        uc.mem_map(0x1000, 0x1000)
        uc.mem_map(0x8000, 0x1000)
        # caller: mov x19,#7; mov x0,#99; bl callee; mov x1,x19; nop ...  (0x1010: after the call)
        uc.mem_write(0x1000, struct.pack("<5I", 0xD28000F3, 0xD2800C60, 0x9400003E, 0xAA1303E1, 0xD503201F))
        # callee: stp x29,x30,[sp,#-32]!; mov x29,sp; stp x19,x20,[sp,#16]; mov x19,#5; ldr w0,[xzr? -> x9=0]
        uc.mem_write(0x1100, struct.pack("<6I", 0xA9BE7BFD, 0x910003FD, 0xA90153F3, 0xD28000B3, 0xD2800009,
                                         0xB9400120))
        uc.reg_write(UC_ARM64_REG_SP, 0x8800)
        try:
            uc.emu_start(0x1000, 0x1014)
            self.fail("no fault")
        except UcError as err:
            self.assertTrue(e.discover_fault(1, str(err)))
        self.assertEqual(uc.reg_read(UC_ARM64_REG_PC), 0x100C)
        self.assertEqual((uc.reg_read(UC_ARM64_REG_X19), uc.reg_read(UC_ARM64_REG_X0), uc.reg_read(UC_ARM64_REG_SP)),
                         (7, 0, 0x8800))
        self.assertEqual(e.discovered[0][0], "callee")

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


class SanitizeTest(unittest.TestCase):
    """emu_sanitize.py (emu_guest.py --alloc-check, --image-check): small Unicorn code tests"""
    PTR = 0x40000010
    CODE, MARK, STK = 0x10000, 0x11000, 0x20000
    # movz w0,#0x4000,lsl 16 ; movk w0,#0x10 ; ret   /   ret
    ALLOC = [0x52A80000, 0x72800200, 0xD65F03C0]
    RET = [0xD65F03C0]

    def emu(self, syms):
        sys.path.insert(0, HERE)
        import emu_guest
        from unicorn import UC_ARCH_ARM64, UC_MODE_ARM, Uc
        e = emu_guest.Emu.__new__(emu_guest.Emu)
        e.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(self.CODE, 0x2000)
        uc.mem_map(self.STK, 0x1000)
        e.cur, e.relocatable, e.base, e.link_base, e.heap = None, False, 0, 0, 0x40000000
        e.sym = lambda a: "%#x f_%x+0x0" % (a, a)
        e.code_syms = {}
        for i, (name, words) in enumerate(syms.items()):
            at = self.CODE + 0x100 * i
            uc.mem_write(at, b"".join(struct.pack("<I", w) for w in words))
            e.code_syms[name] = at
        uc.mem_write(self.MARK, struct.pack("<I", 0xD503201F) * 4)   # nop; the call returns here
        return e

    def call(self, e, name, x0=0):
        from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X29
        uc = e.uc
        uc.reg_write(UC_ARM64_REG_X0, x0)
        uc.reg_write(UC_ARM64_REG_X29, 0)
        uc.reg_write(UC_ARM64_REG_SP, self.STK + 0x800)
        uc.reg_write(UC_ARM64_REG_LR, self.MARK)
        uc.emu_start(e.code_syms[name], self.MARK + 4)
        return uc.reg_read(UC_ARM64_REG_X0)

    def alloc_emu(self):
        e = self.emu({"malloc": self.ALLOC, "free": self.RET, "_ZdlPv": self.RET})
        e.install_alloc_check()
        self.assertEqual(self.call(e, "malloc", 16), self.PTR)
        return e

    def test_mismatched_free(self):
        e = self.alloc_emu()
        self.call(e, "_ZdlPv", self.PTR)   # malloc'd, freed by operator delete
        row = e.alloc_lines()[0].split("\t")
        self.assertEqual(row[:5], ["alloc-check", "mismatch", "new:_ZdlPv", "libc:malloc", "0x40000010"])
        self.assertEqual(e.alloc_lines()[1].split("\t")[:3], ["alloc-check-summary", "1", "mismatch"])
        self.call(e, "free", 0)   # a null free is fine
        self.assertEqual(len([l for l in e.alloc_lines() if l.startswith("alloc-check\t")]), 1)

    def test_double_free_and_unknown(self):
        e = self.alloc_emu()
        self.call(e, "free", self.PTR)
        self.assertEqual([l for l in e.alloc_lines() if l.startswith("alloc-check\t")], [])
        self.call(e, "free", self.PTR)
        self.call(e, "free", 0x40000999)   # in the heap, never allocated
        self.call(e, "free", 0x1234)       # not in the heap
        kinds = [l.split("\t")[1] for l in e.alloc_lines() if l.startswith("alloc-check\t")]
        self.assertEqual(kinds, ["double-free", "unknown-block", "non-heap"])
        self.assertIn("first freed by libc:free", e.alloc_lines()[0])

    def test_nested_alloc_keeps_outer_family(self):
        # operator new calls malloc by bl: the block belongs to new
        from unicorn.arm64_const import UC_ARM64_REG_LR
        e = self.emu({"_Znwm": [0xA9BF7BFD, 0x94000000 | 0x3FC0, 0xA8C17BFD, 0xD65F03C0],   # stp x29,x30,[sp,-16]!; bl +0xFF00 (patched); ldp; ret
                      "malloc": self.ALLOC, "_ZdlPv": self.RET})
        disp = (e.code_syms["malloc"] - (e.code_syms["_Znwm"] + 4)) >> 2
        e.uc.mem_write(e.code_syms["_Znwm"] + 4, struct.pack("<I", 0x94000000 | disp & 0x3FFFFFF))
        e.install_alloc_check()
        self.assertEqual(self.call(e, "_Znwm", 8), self.PTR)
        self.call(e, "_ZdlPv", self.PTR)
        self.assertEqual([l for l in e.alloc_lines() if l.startswith("alloc-check\t")], [])

    def image_emu(self):
        e = self.emu({})
        e.set_image_ranges(0x400000, 0x1200000, [(".text", 0x401000, 0x900000, True), (".rdata", 0x900000, 0xA00000, False),
                                                  (".data", 0xA00000, 0x1200000, False)], {0x401234: "_Z3foov"})
        e.uc.hook_add(__import__("unicorn").UC_HOOK_MEM_INVALID, e.on_bad)
        return e

    def fault(self, e, word, x1):
        from unicorn import UcError
        from unicorn.arm64_const import UC_ARM64_REG_X1
        e.uc.mem_write(self.CODE, struct.pack("<I", word))
        e.uc.reg_write(UC_ARM64_REG_X1, x1)
        with self.assertRaises(UcError):
            e.uc.emu_start(self.CODE, self.CODE + 4)

    def test_image_check_classifies(self):
        e = self.image_emu()
        self.fault(e, 0xD63F0020, 0x401234)   # blr x1: an unrelocated code pointer
        self.fault(e, 0xD63F0020, 0x3000)     # blr x1: an RVA (0x3000 + 0x400000 is .text)
        self.fault(e, 0xB9400022, 0x500000)   # ldr w2,[x1]: code read as data
        self.fault(e, 0xB9000022, 0x500000)   # str w2,[x1]
        self.fault(e, 0xB9400022, 0x400800)   # the headers: no section
        self.fault(e, 0xB9400022, 0x7000000)  # beyond the image: not the image's
        rows = [l.split("\t") for l in e.image_lines() if l.startswith("image-check\t")]
        self.assertEqual([r[1] for r in rows], ["fetch-text", "rva-as-address", "code-read", "code-write", "no-section"])
        self.assertEqual(rows[0][2:4], ["0x00401234", ".text"])
        self.assertEqual(rows[0][-1], "native=_Z3foov")
        self.assertEqual(rows[1][2], "0x00003000")


if __name__ == "__main__":
    unittest.main()
