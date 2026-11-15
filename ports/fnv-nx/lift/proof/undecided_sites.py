#!/usr/bin/env python3
"""undecided_sites: the sites of a lifted pack whose outcome reads a row that the declared-machine record (platform/declared/x86sys.json) marks `undecided`.

    undecided_sites.py --x86sys x86sys.json --pack PACKDIR --gamedata DIR [--out FILE]

Universe (one rule): every instruction site of the pack, i.e. every marker line `L_<va 8 hex>: ;` of PACK/u/*.cpp, one site per marker (an instruction inside
several entries counts once per entry: codeuniverse.MARK, the pack's own definition of a site). The instruction at a marker is decoded from the image that owns
the va; the images are the pack's (the exe, then the static modules in lower-case name order after the exe, each at the base contract/modules.py assign_bases
gives), taken from the owner's gamedata.

Dependent-site rule (nothing typed per instruction; the row set is the record's own, the instruction set is derived from it):
  * the undecided rows are found by walking the record for objects with status "undecided" (path = the row);
  * `cr4.<flag>`: the instructions of the SDM-derived rows (sdm_priv_rows.tsv, class cond) whose sentence names that CR4 flag ("CR4.UMIP", "PCE flag in the CR4 register");
  * a row whose first path token (before `_`) is the mnemonic of a store row (sgdt, sldt) or the register that such a row stores (`idtr`: the register named by the
    initials of the heading "Store Interrupt Descriptor Table Register", `sidt_base`: mnemonic sidt) depends the instructions of that row;
  * `int_ring3.*`: `int imm8` (opcode cd): vector in `vectors` decided; vector in `excluded` -> that excluded row; any other vector -> `other_vectors`; the IDTR limit
    row is read by every `int imm8` (SDM INT operation: the gate is inside the IDT limit) and by sidt;
  * `selectors.<sreg>`: an instruction that reads the segment register: a register operand read (mov r, sreg; push sreg) or a memory operand with that segment override.
Output: a per-row table (sites, shapes, distinct addresses) on stdout, the full list `row<TAB>module:va<TAB>shape<TAB>mnemonic<TAB>detail` in --out."""
import argparse
import collections
import glob
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, LIFT)
from proof import privset, shape  # noqa: E402
import fnvlift  # noqa: E402

MARK = re.compile(rb"^L_([0-9a-f]{8}): ;$", re.M)
ALIGN, PAGE = 0x10000, 0x1000


def undecided(rec):
    out = []

    def walk(o, p):
        if isinstance(o, dict):
            if o.get("status") == "undecided":
                out.append(p); return
            for k, v in o.items():
                walk(v, p + (k,))
    walk(rec, ())
    return out


def register_of(heading):
    """`SIDT—Store Interrupt Descriptor Table Register` -> `idtr`: the initials of the words of the stored register's name"""
    name = heading.split("—", 1)[1]
    name = re.sub(r"^Store\s+", "", name)
    return "".join(w[0] for w in name.split()).lower()


def row_map(rec):
    """-> {row: predicate(ins) -> bool or None}; built only from the record and privset rows"""
    cond = [r for r in privset.rows() if r["class"] == "cond"]
    rows = {}
    for path in undecided(rec):
        row = ".".join(path)
        tok = path[0] if path[0] != "int_ring3" else "int"
        base = tok.split("_")[0]
        mn = set()
        if path[0] == "cr4":
            flag = path[1].upper()
            for r in cond:
                if re.search(r"CR4\.%s\b|\b%s flag in (?:the )?CR4" % (flag, flag), r["sentence"]):
                    mn |= set(r["mnemonics"])
        elif path[0] == "selectors":
            mn = None
        else:
            for r in cond:
                ms = r["mnemonics"]
                if base in ms or base == register_of(r["heading"]):
                    mn |= set(ms)
        rows[row] = (path, mn)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x86sys", required=True); ap.add_argument("--pack", required=True); ap.add_argument("--gamedata", required=True); ap.add_argument("--out")
    a = ap.parse_args()
    rec = json.load(open(a.x86sys))
    rows = row_map(rec)
    int_rec = rec["int_ring3"]
    decided_vec = set(int_rec["vectors"])
    excluded = {int(k): "int_ring3.excluded.%s" % k for k in int_rec.get("excluded", {})}
    # images: the exe at its preferred base; each module at the base the loader assigned it. The module table (modules.tsv) is not an input of the pack, so the base is
    # derived from the pack's own evidence: encs.tsv holds one real encoding (bytes, first site va) per shape key, and the base of a module is the 64 KiB-aligned
    # address above the exe at which every encs row that lands inside the image has exactly the image's bytes (relocated dwords masked), the most rows landing there.
    gd = a.gamedata
    mods = ["binkw32.dll", "d3dx9_38.dll", "libvorbis.dll", "libvorbisfile.dll"]
    exe = fnvlift.Image(os.path.join(gd, "FalloutNV.exe"))
    pe = struct.unpack_from("<I", exe.d, 0x3c)[0]
    sizeimg = struct.unpack_from("<I", exe.d, pe + 80)[0]
    lo_base = (exe.base + sizeimg + ALIGN - 1) & ~(ALIGN - 1)
    encs = sorted((int(r[2], 16), bytes.fromhex(r[1])) for r in (ln.rstrip("\n").split("\t") for ln in open(os.path.join(a.pack, "encs.tsv"))))
    encs_hi = [e for e in encs if e[0] >= lo_base]
    images = {"FalloutNV.exe": exe}
    explained = 0
    for m in mods:
        raw = fnvlift.Image(os.path.join(gd, m))
        roff = set()
        for r in raw.relocs:
            o = raw.off(r)
            if o is not None:
                roff.update(range(o, o + 4))
        mp = struct.unpack_from("<I", raw.d, 0x3c)[0]
        si = struct.unpack_from("<I", raw.d, mp + 80)[0]
        best = None
        for b in range(lo_base, 0xffff0000, ALIGN):
            inr = [e for e in encs_hi if b <= e[0] < b + si]
            if not inr:
                if b > encs_hi[-1][0]:
                    break
                continue
            good = 0
            for va, bs in inr:
                o = raw.off(raw.base + va - b)
                if o is None:
                    break
                if all(raw.d[o + k] == bs[k] or (o + k) in roff for k in range(len(bs))):
                    good += 1
                else:
                    break
            else:
                if best is None or good > best[1]:
                    best = (b, good)
        if best is None:
            sys.exit("no base of %s explains any encs row" % m)
        images[m] = fnvlift.Image(os.path.join(gd, m), best[0]); explained += best[1]
        print("module %s base %08x (explains %d encs rows)" % (m, best[0], best[1]))
    print("encs rows above the exe: %d, explained by the module bases: %d" % (len(encs_hi), explained))
    if explained != len(encs_hi):
        sys.exit("the module bases do not explain every encs row above the exe")
    spans = sorted((a_, a_ + vs, n) for n, img in images.items() for _, a_, vs, _, _ in img.secs)

    def owner(va):
        for lo, hi, n in spans:
            if lo <= va < hi:
                return n
        return None
    cnt = collections.Counter()
    for p in sorted(glob.glob(os.path.join(a.pack, "u", "*.cpp"))):
        with open(p, "rb") as fh:
            for m in MARK.findall(fh.read()):
                cnt[int(m, 16)] += 1
    total = sum(cnt.values())
    md = shape.decoder()
    import capstone
    from capstone import x86
    GS = x86.X86_REG_GS
    out = collections.defaultdict(list)       # row -> [(module, va, shape, mn, detail, mult)]
    noowner = 0; baddec = 0
    for va in sorted(cnt):
        o = owner(va)
        if o is None:
            noowner += cnt[va]; continue
        if images[o].off(va) is None:
            baddec += cnt[va]; continue
        raw = images[o].bytes(va, 15)
        ins = list(md.disasm(raw, va, 1))
        if not ins:
            baddec += cnt[va]; continue
        i = ins[0]; mn = i.mnemonic
        hit = []
        for row, (path, mns) in rows.items():
            if path[0] == "selectors":
                sreg = {"gs": GS}.get(path[1])
                if sreg is None:
                    continue
                rd = False
                rr, _ = i.regs_access()
                if sreg in rr:
                    rd = True
                if any(op.type == x86.X86_OP_MEM and op.mem.segment == sreg for op in i.operands):
                    rd = True
                if rd:
                    hit.append((row, "reads %s" % path[1]))
            elif path[0] == "int_ring3":
                if mn == "int" and raw[0] == 0xcd:
                    v = raw[1]
                    if row == "int_ring3.other_vectors" and v not in decided_vec and v not in excluded:
                        hit.append((row, "vector %d" % v))
                    elif row.startswith("int_ring3.excluded.") and row.endswith(".%d" % v):
                        hit.append((row, "vector %d" % v))
            elif path[0] == "idtr_limit":
                if mn in mns or (mn == "int" and raw[0] == 0xcd):
                    hit.append((row, "reads the IDT limit" + (" (vector %d)" % raw[1] if mn == "int" else "")))
            elif mns and mn in mns:
                hit.append((row, mn))
        if hit:
            sk = shape.shape_key(i)
            for row, detail in hit:
                out[row].append((o, va, sk, mn, detail, cnt[va]))
    lines = ["# fnvnx-undecided-sites/1 pack-sites=%d no-owner=%d undecodable=%d" % (total, noowner, baddec),
             "# row\tmodule:va\tshape\tmnemonic\tdetail\tmultiplicity"]
    print("pack sites %d (distinct addresses %d); no owning image %d; undecodable %d" % (total, len(cnt), noowner, baddec))
    print("%-28s %10s %8s %10s" % ("row", "sites", "shapes", "addresses"))
    for row in sorted(rows):
        L = out.get(row, [])
        print("%-28s %10d %8d %10d" % (row, sum(x[5] for x in L), len({x[2] for x in L}), len(L)))
        for o, va, sk, mn, detail, m in sorted(L, key=lambda x: (x[0], x[1])):
            lines.append("%s\t%s:%08x\t%s\t%s\t%s\t%d" % (row, o, va, sk, mn, detail, m))
    if a.out:
        open(a.out, "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
