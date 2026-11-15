#!/usr/bin/env python3
"""Address-space plan of the emulator boot stage for the guest ELF (elf32-littleaarch64, ILP32, PIE).

Pure and stdlib-only: no unicorn, clock, environment or randomness; every iteration runs over sorted/ordered data.
The guest loader later maps the original Windows exe image (0x00400000..) and the module images through a host call, so
the stage must keep those ranges free. Library: parse_elf, normalize_reserved, plan, reloc_census. CLI:
`python3 -I verify/bootlayout.py plan -h`. A condition that cannot be met raises LayoutError (CLI: message, exit 1);
there is no alternative placement and no default.

Rules (each with its argument):
1. parse_elf: EI_CLASS=ELFCLASS32, EI_DATA=little, e_machine=EM_AARCH64 (183), e_type=ET_DYN and DT_FLAGS_1 with
   DF_1_PIE (0x08000000) in PT_DYNAMIC. Loading uses only program headers (PT_LOAD, PT_DYNAMIC, PT_TLS), never section
   headers, because the kernel/loader sees only those; the span [lo, hi) is the page-aligned (4 KiB) hull of all
   PT_LOADs, so everything the image occupies lies inside it. Any violation names the field and value.
2. reserved: (name, lo, hi) triples, from TSV `name<TAB>lo_hex<TAB>hi_hex` plus the import-key range (name "keys").
   Rows must be sorted by lo and pairwise disjoint (also after aligning outward to 4 KiB, so two ranges never share a
   page); names are unique and differ from the region names. Aligning outward only widens a range, hence the free
   claim stays sound.
3. plan: image base = the lowest 64 KiB-aligned address >= 0x05000000 whose [base+lo, base+hi) meets no reserved range
   and ends <= 0x3E000000 - n_stacks*stack_size. Argument: the scan only moves up, and moves exactly to the smallest
   64 KiB multiple that clears the overlapping range (any lower multiple still overlaps it), so no lower candidate
   is skipped; it terminates because the base grows each step and is bounded. Stacks end at 0x3E000000, host page
   (4 KiB) at 0x3F000000, heap at 0x40000000, end <= 2^32. Fixed regions are not moved: a clash is an error. Totality:
   `checks` lists every unordered pair of {regions + reserved} exactly once, each verified disjoint here, so the
   plan's disjointness is auditable without re-deriving it. `free` repeats the (aligned) reserved ranges.
4. reloc_census: the guest relocates itself at run time, so the stage applies no relocation; it only proves that the
   image is self-contained. Counts of dynamic relocations per type from DT_RELA/DT_RELASZ (Elf32_Rela), DT_REL and
   DT_JMPREL (decoded per DT_PLTREL) and the DT_RELR entry count (DT_RELRSZ / DT_RELRENT, default 4). Allowed: type
   R_AARCH64_P32_RELATIVE = 183 in DT_RELA (AAELF64 ILP32 relocations, binutils include/elf/aarch64.h, glibc elf.h)
   and any DT_RELR; anything else (other types, non-empty DT_REL / DT_JMPREL) is an error listing the counts.
   The census is part of the plan.
   crt0 header (parse_elf): at +8 of the first executable PT_LOAD: magic 0x47584E46, u32 abi, u32 link_base (+16) ==
   lowest PT_LOAD vaddr aligned down, u32 size (+20) == page-aligned span, u32 flags (+24) with bit0 (relocatable).
   Regions and reserved ranges all lie at or above 0x10000 (the guest's low pages stay unmapped).
5. CLI plan (--keys mandatory): deterministic JSON (sorted keys, indent 1, format "fnvnx-bootlayout/1").
"""
import argparse
import json
import struct
import sys

FORMAT = "fnvnx-bootlayout/1"
PAGE = 0x1000
ALIGN = 0x10000
EM_AARCH64, ET_DYN, PT_LOAD, PT_DYNAMIC, PT_TLS = 183, 3, 1, 2, 7
DF_1_PIE, DT_FLAGS_1 = 0x08000000, 0x6ffffffb
R_P32_RELATIVE = 183
MIN_ADDR, CRT0_MAGIC = 0x10000, 0x47584E46
IMAGE_MIN, STACK_END, HOST_PAGE, HEAP_BASE, TOP = 0x05000000, 0x3E000000, 0x3F000000, 0x40000000, 1 << 32
REGION_NAMES = ("image", "stacks", "host_page", "heap")


class LayoutError(Exception):
    pass


def _up(v, a):
    return (v + a - 1) // a * a


def _unpack(fmt, data, off, what):
    if off < 0 or off + struct.calcsize(fmt) > len(data):
        raise LayoutError("%s: offset 0x%x beyond the file (%d bytes)" % (what, off, len(data)))
    return struct.unpack_from(fmt, data, off)


def _file_off(loads, vaddr, size, what):
    for s in loads:
        if s["vaddr"] <= vaddr and vaddr + size <= s["vaddr"] + s["filesz"]:
            return s["offset"] + vaddr - s["vaddr"]
    raise LayoutError("%s: vaddr 0x%x size %d is not file-backed in any PT_LOAD" % (what, vaddr, size))


def _dynamic(data, elf):
    d = elf["dynamic"]
    if d is None:
        raise LayoutError("PT_DYNAMIC missing")
    tags = {}
    for i in range(d["filesz"] // 8):
        tag, val = _unpack("<iI", data, d["offset"] + 8 * i, "PT_DYNAMIC")
        if tag == 0:
            return tags
        tags.setdefault(tag, val)
    raise LayoutError("PT_DYNAMIC has no DT_NULL terminator")


def parse_elf(data):
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise LayoutError("ELF header: missing magic or shorter than 52 bytes (%d)" % len(data))
    if data[4] != 1:
        raise LayoutError("EI_CLASS=%d, need ELFCLASS32 (1)" % data[4])
    if data[5] != 1:
        raise LayoutError("EI_DATA=%d, need little endian (1)" % data[5])
    (etype, machine, _v, entry, phoff, _sh, _fl, _eh, phentsize, phnum) = _unpack("<HHIIIIIHHH", data, 16, "ELF header")
    if machine != EM_AARCH64:
        raise LayoutError("e_machine=%d, need EM_AARCH64 (183)" % machine)
    if etype != ET_DYN:
        raise LayoutError("e_type=%d, need ET_DYN (3)" % etype)
    if phentsize != 32:
        raise LayoutError("e_phentsize=%d, need 32" % phentsize)
    loads, tls, dyn = [], None, None
    for i in range(phnum):
        t, off, va, _pa, fsz, msz, fl, al = _unpack("<8I", data, phoff + 32 * i, "program header %d" % i)
        seg = {"vaddr": va, "memsz": msz, "filesz": fsz, "offset": off, "flags": fl, "align": al}
        if t == PT_LOAD:
            if fsz > msz or off + fsz > len(data):
                raise LayoutError("PT_LOAD %d: filesz=%d memsz=%d offset=0x%x file=%d" % (i, fsz, msz, off, len(data)))
            loads.append(seg)
        elif t == PT_TLS:
            tls = seg
        elif t == PT_DYNAMIC:
            dyn = seg
    if not loads:
        raise LayoutError("no PT_LOAD segment")
    lo = min(s["vaddr"] for s in loads) // PAGE * PAGE
    hi = _up(max(s["vaddr"] + s["memsz"] for s in loads), PAGE)
    elf = {"entry": entry, "loads": loads, "span": [lo, hi], "tls": tls, "dynamic": dyn}
    tags = _dynamic(data, elf)
    if DT_FLAGS_1 not in tags:
        raise LayoutError("DT_FLAGS_1 missing in PT_DYNAMIC (need DF_1_PIE 0x08000000)")
    if not tags[DT_FLAGS_1] & DF_1_PIE:
        raise LayoutError("DT_FLAGS_1=0x%x lacks DF_1_PIE (0x08000000)" % tags[DT_FLAGS_1])
    elf["flags_1"] = tags[DT_FLAGS_1]
    first_x = next((s for s in loads if s["flags"] & 1), None)
    if first_x is None:
        raise LayoutError("no executable PT_LOAD (PF_X) for the crt0 header")
    magic, abi, link_base, size, flags = _unpack("<IIIII", data, first_x["offset"] + 8, "crt0 header")
    if magic != CRT0_MAGIC:
        raise LayoutError("crt0 magic=0x%08x, need 0x%08x" % (magic, CRT0_MAGIC))
    if link_base != lo:
        raise LayoutError("crt0 link_base=0x%x, need lowest PT_LOAD vaddr 0x%x" % (link_base, lo))
    if size != hi - lo:
        raise LayoutError("crt0 size=0x%x, need page-aligned span 0x%x" % (size, hi - lo))
    if not flags & 1:
        raise LayoutError("crt0 flags=0x%x lacks bit0 (relocatable)" % flags)
    elf["abi"] = abi
    return elf


def normalize_reserved(rows):
    """rows: (name, lo, hi) in ascending lo order; returns the page-aligned-outward rows as tuples."""
    out, names, prev = [], set(), None   # prev = the previous raw row
    for name, lo, hi in rows:
        if not lo < hi:
            raise LayoutError("reserved %s: need lo < hi, got 0x%x..0x%x" % (name, lo, hi))
        if name in names or name in REGION_NAMES:
            raise LayoutError("reserved name %s is duplicated or names a region" % name)
        names.add(name)
        if prev is not None and lo < prev[1]:
            raise LayoutError("reserved rows not sorted by lo: %s 0x%x after %s 0x%x" % (name, lo, prev[0], prev[1]))
        if prev is not None and lo < prev[2]:
            raise LayoutError("reserved %s overlaps %s" % (name, prev[0]))
        if lo < MIN_ADDR:
            raise LayoutError("reserved %s: lo 0x%x is below 0x%x" % (name, lo, MIN_ADDR))
        row = (name, lo // PAGE * PAGE, _up(hi, PAGE))
        if out and row[1] < out[-1][2]:
            raise LayoutError("reserved %s overlaps %s after page alignment" % (name, out[-1][0]))
        out.append(row)
        prev = (name, lo, hi)
    return out


def plan(elf, reserved, heap_size, n_stacks=32, stack_size=0x40000):
    if n_stacks < 1 or stack_size < ALIGN or stack_size % ALIGN:
        raise LayoutError("n_stacks=%d stack_size=0x%x: need n >= 1 and a 64 KiB multiple" % (n_stacks, stack_size))
    if heap_size <= 0 or heap_size % PAGE:
        raise LayoutError("heap_size=%d must be a positive multiple of 4096" % heap_size)
    if HEAP_BASE + heap_size > TOP:
        raise LayoutError("heap: 0x%x + 0x%x ends beyond 4 GiB" % (HEAP_BASE, heap_size))
    res = normalize_reserved(reserved)
    lo, hi = elf["span"]
    limit = STACK_END - n_stacks * stack_size
    base = IMAGE_MIN
    while True:
        hit = next((r for r in res if r[1] < base + hi and base + lo < r[2]), None)
        if hit is None:
            break
        base = _up(max(base + 1, hit[2] - lo), ALIGN)
    if base + hi > limit:
        raise LayoutError("image: span 0x%x..0x%x does not fit below 0x%x (lowest free base 0x%x)" % (lo, hi, limit, base))
    regions = {"image": (base + lo, base + hi), "stacks": (limit, STACK_END), "host_page": (HOST_PAGE, HOST_PAGE + PAGE),
               "heap": (HEAP_BASE, HEAP_BASE + heap_size)}
    items = [(n, regions[n][0], regions[n][1]) for n in REGION_NAMES] + res
    checks = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a[1] < b[2] and b[1] < a[2]:
                raise LayoutError("%s 0x%x..0x%x overlaps %s 0x%x..0x%x" % (a[0], a[1], a[2], b[0], b[1], b[2]))
            checks.append([a[0], b[0], "disjoint"])
    for n, (a, b) in regions.items():
        if a % ALIGN or b > TOP:
            raise LayoutError("region %s 0x%x..0x%x is not 64 KiB aligned or exceeds 4 GiB" % (n, a, b))
    return {"format": FORMAT, "image": {"base": base, "lo": lo, "hi": hi, "entry": base + elf["entry"]},
            "regions": {n: {"lo": a, "hi": b} for n, (a, b) in regions.items()},
            "free": [{"name": n, "lo": a, "hi": b} for n, a, b in res], "checks": checks}


def _count_table(data, loads, addr, size, ent, what):
    off = _file_off(loads, addr, size, what)
    counts = {}
    for i in range(size // ent):
        info = _unpack("<II", data, off + ent * i, what)[1]
        counts[info & 0xFF] = counts.get(info & 0xFF, 0) + 1
    return {str(t): c for t, c in sorted(counts.items())}


def reloc_census(data):
    elf = parse_elf(data)
    tags = _dynamic(data, elf)
    loads, cen = elf["loads"], {"rela": {}, "rel": {}, "jmprel": {}, "relr": 0}
    size = tags.get(8, 0)
    if tags.get(9, 12) != 12 or size % 12:
        raise LayoutError("DT_RELAENT=%d DT_RELASZ=%d: need entries of 12 bytes" % (tags.get(9, 12), size))
    if size:
        cen["rela"] = _count_table(data, loads, tags[7], size, 12, "DT_RELA")
    if tags.get(18, 0):
        cen["rel"] = _count_table(data, loads, tags[17], tags[18] // 8 * 8, 8, "DT_REL")
    if tags.get(2, 0):
        if tags.get(20) not in (17, 23):
            raise LayoutError("DT_JMPREL non-empty but DT_PLTREL=%s, need 17 or 23" % tags.get(20))
        ent = 12 if tags[20] == 17 else 8
        cen["jmprel"] = _count_table(data, loads, tags[23], tags[2] // ent * ent, ent, "DT_JMPREL")
    if tags.get(35, 0):
        if tags.get(37, 4) != 4:
            raise LayoutError("DT_RELRENT=%d, need 4" % tags.get(37, 4))
        cen["relr"] = tags[35] // 4
    bad = [(k, t, c) for k in ("rela", "rel", "jmprel") for t, c in cen[k].items()
           if not (k == "rela" and t == str(R_P32_RELATIVE))]
    if bad:
        raise LayoutError("relocations the stage does not accept (table type: count): " +
                          ", ".join("%s %s: %d" % b for b in bad))
    return cen


def read_tsv(path):
    rows = []
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            f = line.split("\t")
            if len(f) != 3:
                raise LayoutError("%s:%d: need name<TAB>lo_hex<TAB>hi_hex, got %d fields" % (path, n, len(f)))
            try:
                rows.append((f[0], int(f[1], 16), int(f[2], 16)))
            except ValueError:
                raise LayoutError("%s:%d: not hexadecimal: %r" % (path, n, line))
    return rows


def _cmd_plan(a):
    with open(a.elf, "rb") as fh:
        raw = fh.read()
    elf = parse_elf(raw)
    rows = normalize_reserved(read_tsv(a.reserved))
    try:
        lo, hi = (int(x, 16) for x in a.keys.split(":"))
    except ValueError:
        raise LayoutError("--keys %r: need LO:HI in hex" % a.keys)
    if not lo < hi or lo < MIN_ADDR:
        raise LayoutError("--keys: need %x <= lo < hi, got 0x%x..0x%x" % (MIN_ADDR, lo, hi))
    rows.append(("keys", lo // PAGE * PAGE, _up(hi, PAGE)))
    doc = plan(elf, sorted(rows, key=lambda r: r[1]), a.heap_size)
    doc["census"] = reloc_census(raw)
    with open(a.o, "w") as fh:
        fh.write(json.dumps(doc, sort_keys=True, indent=1) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan", help="write PLAN.json (format %s) for the boot stage" % FORMAT)
    sp.add_argument("--elf", required=True)
    sp.add_argument("--reserved", required=True, metavar="T.tsv", help="name<TAB>lo_hex<TAB>hi_hex rows, sorted")
    sp.add_argument("--keys", required=True, metavar="LO:HI", help="import-slot key range (hex), never mapped")
    sp.add_argument("--heap-size", required=True, type=lambda s: int(s, 0))
    sp.add_argument("-o", required=True)
    a = ap.parse_args(argv)
    try:
        _cmd_plan(a)
    except (LayoutError, OSError) as e:
        print("bootlayout plan: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
