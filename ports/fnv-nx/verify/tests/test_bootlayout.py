#!/usr/bin/env python3
"""Tests of verify/bootlayout.py (address-space plan of the boot stage). Plain script: exit 0 on pass, one line per case.
ELF32 AArch64 images are built here with struct (no cross compiler needed)."""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import bootlayout  # noqa: E402

FAILED = []
N = [0]
MB = 1 << 20


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


ENTRY = 0x1040   # the entry of the synthetic image (the e_entry make_elf writes; the plan takes it as an argument, as boot.py does from the link record)


def make_elf(ei_class=1, ei_data=1, machine=183, etype=3, flags1=0x08000000, relocs=((0x12200, 183, 0x1234),),
             magic=0x47584E46, link_base=0, size=0x14000, cflags=1, relr=0, rel=0, jmprel=0):
    """ELF header + PT_LOAD R (0..0x1000) + PT_LOAD RX (0x1000, crt0 header) + PT_LOAD RW (0x12000..0x14000) + PT_DYNAMIC.
    DT_RELA at 0x12100; optional DT_RELR (relr words), DT_REL (rel entries), DT_JMPREL (jmprel entries) at 0x12100 too."""
    rela = b"".join(struct.pack("<IIi", o, t, a) for o, t, a in relocs)
    dyn = [(7, 0x12100), (8, len(rela)), (9, 12)]
    if relr:
        dyn += [(36, 0x12100), (35, 4 * relr), (37, 4)]
    if rel:
        dyn += [(17, 0x12100), (18, 8 * rel), (19, 8)]
    if jmprel:
        dyn += [(23, 0x12100), (2, 12 * jmprel), (20, 17)]
    if flags1 is not None:
        dyn.append((0x6ffffffb, flags1))
    dyn.append((0, 0))
    dyn_b = b"".join(struct.pack("<iI", t, v) for t, v in dyn)
    ident = b"\x7fELF" + bytes([ei_class, ei_data, 1]) + bytes(9)
    ehdr = ident + struct.pack("<HHIIIIIHHHHHH", etype, machine, 1, ENTRY, 52, 0, 0, 52, 32, 4, 0, 0, 0)
    ph = struct.pack("<8I", 1, 0, 0, 0, 0x1000, 0x1000, 4, 0x1000)
    ph += struct.pack("<8I", 1, 0x1000, 0x1000, 0x1000, 0x100, 0x1000, 5, 0x1000)
    ph += struct.pack("<8I", 1, 0x2000, 0x12000, 0x12000, 0x300, 0x2000, 6, 0x1000)
    ph += struct.pack("<8I", 2, 0x2000, 0x12000, 0x12000, len(dyn_b), len(dyn_b), 6, 4)
    text = struct.pack("<8I", 0, 0, magic, 1, link_base, size, cflags, 0).ljust(0x1000, b"\0")
    blob = dyn_b.ljust(0x100, b"\0") + rela
    return (ehdr + ph).ljust(0x1000, b"\0") + text + blob.ljust(0x300, b"\0")


def raises(fn, *needles):
    try:
        fn()
    except bootlayout.LayoutError as e:
        return all(n in str(e) for n in needles), str(e)
    return False, "no error"


def main():
    good = make_elf()
    e = bootlayout.parse_elf(good)
    case("good image: span, entry, loads", e["span"] == [0, 0x14000] and len(e["loads"]) == 3
         and e["loads"][2]["vaddr"] == 0x12000 and e["tls"] is None, e)
    for name, kw, needles in (
            ("class 64", dict(ei_class=2), ("EI_CLASS", "2")),
            ("big endian", dict(ei_data=2), ("EI_DATA", "2")),
            ("wrong machine", dict(machine=62), ("e_machine", "62")),
            ("ET_EXEC", dict(etype=2), ("e_type", "2")),
            ("no DF_1_PIE", dict(flags1=0), ("DF_1_PIE",)),
            ("no DT_FLAGS_1", dict(flags1=None), ("DT_FLAGS_1",)),
            ("crt0 magic", dict(magic=0x12345678), ("magic", "0x12345678")),
            ("crt0 link_base", dict(link_base=0x1000), ("link_base", "0x1000")),
            ("crt0 size", dict(size=0x13000), ("size", "0x13000")),
            ("crt0 not relocatable", dict(cflags=0), ("flags", "0x0"))):
        ok, msg = raises(lambda kw=kw: bootlayout.parse_elf(make_elf(**kw)), *needles)
        case("reject " + name, ok, msg)
    ok, msg = raises(lambda: bootlayout.parse_elf(good[:40]), "ELF header")
    case("reject truncated", ok, msg)

    rsv = [("exe", 0x00400000, 0x00C00000), ("res", 0x05000000, 0x05200000)]
    p = bootlayout.plan(e, rsv, 16 * MB, entry=ENTRY)
    case("base skips reserved to next 64 KiB", p["image"]["base"] == 0x05200000, p["image"])
    p0 = bootlayout.plan(e, rsv[:1], 16 * MB, entry=ENTRY)
    case("base is the lowest candidate", p0["image"]["base"] == 0x05000000)
    case("fixed regions", p["regions"]["host_page"]["lo"] == 0x3F000000 and p["regions"]["heap"]["lo"] == 0x40000000
         and p["regions"]["stacks"]["hi"] == 0x3E000000 and p["regions"]["stacks"]["lo"] == 0x3E000000 - 32 * 0x40000)
    n = 4 + len(rsv)
    case("checks total", len(p["checks"]) == n * (n - 1) // 2
         and len({tuple(sorted(c[:2])) for c in p["checks"]}) == len(p["checks"]), len(p["checks"]))
    case("free verbatim", [(f["name"], f["lo"], f["hi"]) for f in p["free"]] == [tuple(r) for r in rsv], p["free"])
    ok, msg = raises(lambda: bootlayout.plan(e, [("big", 0x05000000, 0x3D7F0000)], MB, entry=ENTRY), "image")
    case("image too large for gap", ok, msg)
    ok, msg = raises(lambda: bootlayout.plan(e, [("h", 0x40000000, 0x40010000)], MB, entry=ENTRY), "heap", "h")
    case("heap onto reserved is an error", ok, msg)
    ok, msg = raises(lambda: bootlayout.plan(e, rsv, 0xC1000000, entry=ENTRY), "heap")
    case("heap beyond 4 GiB is an error", ok, msg)
    ok, msg = raises(lambda: bootlayout.normalize_reserved([("a", 0x11000, 0x13000), ("b", 0x12000, 0x14000)]), "overlap")
    case("overlapping reserved rows", ok, msg)
    ok, msg = raises(lambda: bootlayout.normalize_reserved([("b", 0x14000, 0x15000), ("a", 0x11000, 0x12000)]), "sorted")
    case("unsorted reserved rows", ok, msg)
    r = bootlayout.normalize_reserved([("a", 0x11100, 0x11200)])
    case("aligned outward", r == [("a", 0x11000, 0x12000)], r)

    ok, msg = raises(lambda: bootlayout.normalize_reserved([("low", 0x8000, 0x20000)]), "low", "0x10000")
    case("reserved below 0x10000", ok, msg)
    c = bootlayout.reloc_census(good)
    case("census: 183 only", c == {"rela": {"183": 1}, "rel": {}, "jmprel": {}, "relr": 0}, c)
    c = bootlayout.reloc_census(make_elf(relr=5, relocs=()))
    case("census: RELR ok", c["relr"] == 5 and c["rela"] == {}, c)
    ok, msg = raises(lambda: bootlayout.reloc_census(make_elf(relocs=((0x12200, 183, 0), (0x12204, 181, 0),
                                                                      (0x12208, 181, 0)))), "181", "2")
    case("census: foreign type", ok, msg)
    ok, msg = raises(lambda: bootlayout.reloc_census(make_elf(jmprel=1)), "jmprel")
    case("census: non-empty JMPREL", ok, msg)
    ok, msg = raises(lambda: bootlayout.reloc_census(make_elf(rel=1)), "rel")
    case("census: non-empty REL", ok, msg)

    case("plan: image.entry is base + entry", p["image"]["entry"] == p["image"]["base"] + ENTRY, p["image"])
    for what, ent in (("past the span", 0x14000), ("below it", -1)):
        ok, msg = raises(lambda ent=ent: bootlayout.plan(e, rsv, 16 * MB, entry=ent), "entry", "span")
        case("plan: an entry %s is refused" % what, ok, msg)
    print("%d failed" % len(FAILED) if FAILED else "all %d passed" % N[0])
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
