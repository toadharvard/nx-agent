#!/usr/bin/env python3
"""undecided_sites: the sites of the lift facts whose outcome reads a row that the declared-machine record (platform/declared/x86sys.json) marks `undecided`.

    undecided_sites.py --x86sys x86sys.json --sites SITES.tsv --exe FalloutNV.exe --modules modules.tsv --dir DIR [--dir DIR ...] [--out FILE]

Universe (one rule): every instruction that the lift facts decode, SITES.tsv of factsites.py (L3a's code universe; no pack, no marker file, no sweep of ours). The instruction at a site is
decoded from the image that owns it; the image set and each module's base are the loader's module table (imageset.py, contract/modules.py modules.tsv), the images are the owner's gamedata.

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
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, LIFT); sys.path.insert(0, HERE)
from proof import imageset, privset, shape  # noqa: E402
import fnvlift  # noqa: E402



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
    ap.add_argument("--x86sys", required=True); ap.add_argument("--sites", required=True, help="factsites.py output"); ap.add_argument("--exe", required=True)
    ap.add_argument("--modules", required=True); ap.add_argument("--dir", action="append", required=True); ap.add_argument("--out")
    a = ap.parse_args()
    rec = json.load(open(a.x86sys))
    rows = row_map(rec)
    int_rec = rec["int_ring3"]
    decided_vec = set(int_rec["vectors"])
    excluded = {int(k): "int_ring3.excluded.%s" % k for k in int_rec.get("excluded", {})}
    # images: the exe at its preferred base, each module at the assigned base of the loader's module table (imageset.py); the sites are the facts' decoded instructions (factsites.py)
    ims = imageset.images(a.exe, a.modules, a.dir)
    base = {n: ab for n, _, _, ab in imageset.load(a.modules)}
    images = {n: (fnvlift.Image(path) if n not in base else fnvlift.Image(path, base[n])) for n, path, _ in ims}
    cnt = collections.Counter()
    for ln in open(a.sites):
        if ln[0] == "#" or not ln.strip():
            continue
        m, va = ln.split("\t")[0].rsplit(":", 1)
        cnt[(m, int(va, 16))] += 1
    total = sum(cnt.values())
    md = shape.decoder()
    import capstone
    from capstone import x86
    GS = x86.X86_REG_GS
    out = collections.defaultdict(list)       # row -> [(module, va, shape, mn, detail, mult)]
    noowner = 0; baddec = 0
    for (o, va) in sorted(cnt):
        if o not in images:
            noowner += cnt[(o, va)]; continue
        if images[o].off(va) is None:
            baddec += cnt[(o, va)]; continue
        raw = images[o].bytes(va, 15)
        ins = list(md.disasm(raw, va, 1))
        if not ins:
            baddec += cnt[(o, va)]; continue
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
                out[row].append((o, va, sk, mn, detail, cnt[(o, va)]))
    lines = ["# fnvnx-undecided-sites/1 sites=%d no-owner=%d undecodable=%d" % (total, noowner, baddec),
             "# row\tmodule:va\tshape\tmnemonic\tdetail\tmultiplicity"]
    print("sites %d (distinct addresses %d); no owning image %d; undecodable %d" % (total, len(cnt), noowner, baddec))
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
