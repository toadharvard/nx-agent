#!/usr/bin/env python3
"""Self-test of NX1 check 1: the packer against switch-tools, and the validator against broken NROs.

    python3 -I tests/nx1/test_check_nro.py [--ref-bin <dir with switch-tools elf2nro and nacptool>]

Builds the synthetic fixture (tests/nx1/fixture, system aarch64-linux-gnu binutils), packs it with
toolchain/tools/elf2nro.py and the committed icon, checks that it passes, then that each mutation fails with the
expected check id. Without --ref-bin (or NX1_REF_BIN) the round-trip checks are skipped, not passed.
"""
import argparse
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "toolchain", "tools"))
import check_nro  # noqa: E402
import elf2nro  # noqa: E402
import nxelf  # noqa: E402

ICON = os.path.join(ROOT, "toolchain", "nro", "icon.jpg")


def fails(c, cid):
    return any(r[0] == cid and not r[1] for r in c.rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-bin", default=os.environ.get("NX1_REF_BIN"))
    a = ap.parse_args()
    if not shutil.which("aarch64-linux-gnu-as"):
        print("SKIP: aarch64-linux-gnu binutils not installed")
        return 0
    t = tempfile.mkdtemp(prefix="nx1-nro-")
    try:
        elf = os.path.join(t, "fx.elf")
        subprocess.run([os.path.join(HERE, "fixture", "build.sh"), elf], check=True)
        nacp = elf2nro.build_nacp("fnv-nx", "fnv-nx project", "0.1.0")
        icon = open(ICON, "rb").read()
        good = elf2nro.build_nro(nxelf.read(elf), icon, nacp)
        nro = os.path.join(t, "fx.nro")
        refs = {}
        if a.ref_bin:
            refs = dict(ref_elf2nro=os.path.join(a.ref_bin, "elf2nro"), ref_nacptool=os.path.join(a.ref_bin, "nacptool"))
        open(nro, "wb").write(good)
        c = check_nro.check(nro, elf, refs.get("ref_elf2nro"), refs.get("ref_nacptool"), True)
        failures = 0

        def expect(name, cond):
            nonlocal failures
            print(("ok   " if cond else "FAIL ") + name)
            failures += not cond

        expect("fixture passes", c.passed)
        expect("round trips ran" if refs else "round trips skipped (no --ref-bin)",
               not refs or {"roundtrip.elf2nro", "roundtrip.nacptool"} <= {r[0] for r in c.rows})

        size = struct.unpack_from("<I", good, 0x18)[0]

        def mutated(name, cid, fn, use_elf=False):
            b = bytearray(good)
            fn(b)
            open(nro, "wb").write(b)
            r = check_nro.check(nro, elf if use_elf else None, None, None, True)
            expect(f"{name} -> {cid}", fails(r, cid))

        mutated("bad magic", "header.magic", lambda b: b.__setitem__(slice(0x10, 0x14), b"NSO0"))
        mutated("version 1", "header.version", lambda b: struct.pack_into("<I", b, 0x14, 1))
        mutated("unaligned rodata", "seg.rodata.aligned", lambda b: struct.pack_into("<I", b, 0x28, 0x1004))
        mutated("data past size", "seg.data.hbloader_bounds", lambda b: struct.pack_into("<I", b, 0x34, 0x2000))
        mutated("text not at 0", "seg.text_at_0", lambda b: struct.pack_into("<I", b, 0x20, 0x1000))
        mutated("bss unaligned", "seg.bss_aligned", lambda b: struct.pack_into("<I", b, 0x38, 0x10))
        mutated("no MOD0", "mod0.magic", lambda b: b.__setitem__(slice(0x80, 0x84), b"XXXX"))
        mutated("MOD0 bss outside", "mod0.bss", lambda b: struct.pack_into("<i", b, 0x88, 0x100000))
        mutated("no ASET", "aset.present", lambda b: b.__setitem__(slice(size, size + 4), b"XXXX"))
        mutated("icon cut", "aset.icon.jpeg", lambda b: b.__setitem__(slice(size + 0x38 + len(icon) - 2,
                                                                             size + 0x38 + len(icon)), b"\0\0"))
        mutated("nacp name not terminated", "nacp.name",
                lambda b: b.__setitem__(slice(size + 0x38 + len(icon), size + 0x38 + len(icon) + 0x200), b"A" * 0x200))
        mutated("text bytes changed", "elf.text.bytes", lambda b: b.__setitem__(0xA0, b[0xA0] ^ 0xFF), use_elf=True)

        # dynamic: an R_AARCH64_ABS64 relocation, and a DT_NEEDED
        e = nxelf.read(elf)
        dyn = next(p for p in e.phdrs if p.type == nxelf.PT_DYNAMIC).vaddr

        def add_dyn(tag, val):
            def f(b):
                i = dyn
                while struct.unpack_from("<q", b, i)[0] != 0:
                    i += 16
                struct.pack_into("<qQqQ", b, i, tag, val, 0, 0)
            return f
        mutated("DT_NEEDED", "dyn.no_needed", add_dyn(1, 1))

        def bad_rela(b):   # a 1-entry RELA table in rodata padding with type ABS64 (257)
            ra = 0x1F00
            struct.pack_into("<QQq", b, ra, 0x2000, 257, 0)
            add_dyn(7, ra)(b)
            add_dyn(8, 24)(b)
        mutated("ABS64 relocation", "dyn.rela_types", bad_rela)

        def rela_into_rodata(b):   # RELATIVE (1027) targeting rodata, which hbloader maps R-- (V1 NX1-NRO-N2)
            ra = 0x1F00
            struct.pack_into("<QQq", b, ra, 0x1000, 1027, 0)
            add_dyn(7, ra)(b)
            add_dyn(8, 24)(b)
        mutated("RELATIVE into rodata", "dyn.rela_targets", rela_into_rodata)

        def relr_into_text(b):     # one RELR address entry pointing at text
            ra = 0x1F00
            struct.pack_into("<Q", b, ra, 0x100)
            add_dyn(36, ra)(b)
            add_dyn(35, 8)(b)
        mutated("RELR into text", "dyn.relr_targets", relr_into_text)

        def relr_bitmap_into_rodata(b):   # address entry in data, then a bitmap entry reaching past it is fine;
            ra = 0x1F00                  # an address entry in rodata is not
            struct.pack_into("<QQ", b, ra, 0x2000, 0x1000)
            add_dyn(36, ra)(b)
            add_dyn(35, 16)(b)
        mutated("RELR address in rodata", "dyn.relr_targets", relr_bitmap_into_rodata)

        # icon variants
        from_pil = None
        try:
            from PIL import Image
            from_pil = Image
        except ImportError:
            print("skip icon variants: Pillow not installed")
        if from_pil:
            for name, cid, kw, sz in (("progressive icon", "aset.icon.baseline", dict(progressive=True), 256),
                                      ("128x128 icon", "aset.icon.256", {}, 128)):
                im = from_pil.open(ICON).resize((sz, sz))
                bio = io.BytesIO()
                im.save(bio, "JPEG", **kw)
                open(nro, "wb").write(elf2nro.build_nro(nxelf.read(elf), bio.getvalue(), nacp))
                expect(f"{name} -> {cid}", fails(check_nro.check(nro, None, None, None, True), cid))

        # the packer refuses a gap between segments (elf2nro would write a wrong header)
        b = bytearray(open(elf, "rb").read())
        ph = struct.unpack_from("<Q", b, 32)[0]
        struct.pack_into("<Q", b, ph + 56 + 16, 0x5000)   # rodata vaddr 0x1000 -> 0x5000
        try:
            elf2nro.build_nro(nxelf.Elf(bytes(b)), icon, nacp)
            expect("packer refuses a segment gap", False)
        except ValueError:
            expect("packer refuses a segment gap", True)
        # the packer refuses a relocation outside the RW segment too: a RELA table in a copy of the ELF
        b = bytearray(open(elf, "rb").read())
        ph = struct.unpack_from("<Q", b, 32)[0]
        struct.pack_into("<Q", b, ph + 56 + 32, 0x1000)   # rodata filesz: the table at 0x1F00 is file-backed
        e2 = nxelf.Elf(bytes(b))
        dynp = next(p for p in e2.phdrs if p.type == nxelf.PT_DYNAMIC)
        rel_va = 0x1F00
        b[e2.vaddr_to_offset(0x1000) + 0xF00:e2.vaddr_to_offset(0x1000) + 0xF18] = struct.pack("<QQq", 0x1000, 1027, 0)
        o = dynp.offset
        while struct.unpack_from("<q", b, o)[0] != 0:
            o += 16
        struct.pack_into("<qQqQqQ", b, o, 7, rel_va, 8, 24, 0, 0)
        try:
            e3 = nxelf.Elf(bytes(b))
            refused = False
            try:
                elf2nro.build_nro(e3, icon, nacp)
            except ValueError as ex:
                refused = "RW segment" in str(ex)
            expect("packer refuses a relocation into rodata", refused)
        except nxelf.ElfError as ex:
            expect(f"packer refusal test set up ({ex})", False)
        print(f"{failures} failed")
        return 1 if failures else 0
    finally:
        shutil.rmtree(t, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
