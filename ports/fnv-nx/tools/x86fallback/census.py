#!/usr/bin/env python3
"""X1-fex step 3: static census of what an x86-mode function needs (docs/X86_FALLBACK.md 4.4, 9).

For each function (extent from the decomp's build/units.json, bytes from the player's FalloutNV.exe):
  vcall      provable vcall sites: `call/jmp dword ptr [r+d]` whose base r was last loaded from `[x]` (disp 0,
             the vptr load) in the same function, linear order; the load-time pass can rewrite these
  ind_other  other memory-indirect calls `call [r+d]` / `call [abs]` that are not IAT slots (function-pointer fields,
             tables; unproven: the pass must disqualify or prove them)
  ind_reg    `call r` (register-indirect; vcall when r came from [vptr+d], else unproven)
  iat        calls through IAT slots (exit thunks, fine)
  eh         fs:[0] accessed (SEH / C++ EH frame registration)
  vft_imm    immediates equal to a vftable address (??_7 symbols): need the Itanium address point
  vbt_imm    immediates equal to a vbtable address (??_8): virtual inheritance, not eligible
  x87        x87 instructions (cost, section 5)
Eligible now = no eh, no vbt_imm, no unproven indirect call. With the vcall pass = the same, vcalls allowed.

Inputs are read at run time and nothing derived is written to the repository; --out goes to a local path.
Usage: census.py --exe FalloutNV.exe --units build/units.json [--set NAME=addrfile ...] [--out rows.tsv]
addrfile: one hex address per line, or a TSV whose 2nd column is the address (EQ row tables).
"""
import argparse, collections, hashlib, json, re, sys
import capstone, pefile
from capstone import x86 as X

EXE_SHA = "89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe"
COLS = ("vcall", "ind_other", "ind_reg", "iat", "eh", "vft_imm", "vbt_imm", "x87")


def load_units(path):
    j = json.load(open(path))
    funcs, vft, vbt = {}, set(), set()
    for u in j["units"]:
        for f in u["functions"]:
            funcs[int(f["address"], 16)] = (f["size"], f["symbol"])
        for name, a in (u.get("symbols") or {}).items():
            if name.startswith("??_7"): vft.add(int(a, 16))
            elif name.startswith("??_8"): vbt.add(int(a, 16))
    return funcs, vft, vbt


def census(md, code, va, vft, vbt, iat_lo, iat_hi):
    r = dict.fromkeys(COLS, 0)
    vptr_regs, vslot_regs = set(), set()     # regs holding a vptr ([x] load) / a slot value ([vptr+d] load)
    for i in md.disasm(code, va):
        ops = i.operands
        if i.mnemonic.startswith("f") and i.mnemonic not in ("femms",): r["x87"] += 1
        for op in ops:
            if op.type == X.X86_OP_MEM and op.mem.segment == X.X86_REG_FS and op.mem.disp == 0 \
                    and op.mem.base == 0: r["eh"] = 1
            if op.type == X.X86_OP_IMM or (op.type == X.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0):
                v = (op.imm if op.type == X.X86_OP_IMM else op.mem.disp) & 0xffffffff
                if v in vft: r["vft_imm"] += 1
                if v in vbt: r["vbt_imm"] += 1
        if i.mnemonic in ("call", "jmp") and ops and ops[0].type == X.X86_OP_MEM:
            m = ops[0].mem
            if m.base == 0 and m.index == 0:
                r["iat" if iat_lo <= (m.disp & 0xffffffff) < iat_hi else "ind_other"] += 1
            elif m.base in vptr_regs and m.index == 0: r["vcall"] += 1
            else: r["ind_other"] += 1
            continue
        if i.mnemonic in ("call", "jmp") and ops and ops[0].type == X.X86_OP_REG:
            if ops[0].reg in vslot_regs: r["vcall"] += 1
            else: r["ind_reg"] += 1
            continue
        # register def tracking (linear, conservative: any write ends the fact)
        try: _, written = i.regs_access()
        except capstone.CsError: written = ()
        if i.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_MEM \
                and ops[1].mem.segment == 0 and ops[1].mem.index == 0 and ops[1].mem.base != 0:
            dst, mem = ops[0].reg, ops[1].mem
            src_vptr = mem.base in vptr_regs
            vptr_regs.discard(dst); vslot_regs.discard(dst)
            if mem.disp == 0 and mem.base not in (X.X86_REG_ESP, X.X86_REG_EBP): vptr_regs.add(dst)
            if src_vptr: vslot_regs.add(dst)
            continue
        for reg in written: vptr_regs.discard(reg); vslot_regs.discard(reg)
    return r


def read_set(path):
    out = set()
    for line in open(path):
        if line.startswith("#") or not line.strip(): continue
        f = line.rstrip("\n").split("\t")
        tok = f[1] if len(f) > 1 else f[0]
        if re.fullmatch(r"(0x)?[0-9a-fA-F]{6,8}", tok.strip()): out.add(int(tok, 16))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True); ap.add_argument("--units", required=True)
    ap.add_argument("--set", action="append", default=[], help="NAME=file (several files: NAME=a,b)")
    ap.add_argument("--out"); ap.add_argument("--all", action="store_true", help="also every decomp function")
    o = ap.parse_args()
    data = open(o.exe, "rb").read()
    if hashlib.sha256(data).hexdigest() != EXE_SHA: sys.exit("not the pinned FalloutNV.exe")
    pe = pefile.PE(data=data, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    idd = pe.OPTIONAL_HEADER.DATA_DIRECTORY[12]          # IAT directory
    iat_lo, iat_hi = base + idd.VirtualAddress, base + idd.VirtualAddress + idd.Size
    funcs, vft, vbt = load_units(o.units)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    sets = {}
    for s in o.set:
        name, files = s.split("=", 1)
        sets[name] = set().union(*(read_set(f) for f in files.split(",")))
    if o.all: sets["all-decomp"] = set(funcs)
    want = set().union(*sets.values()) if sets else set()
    rows = {}
    for a in sorted(want):
        if a not in funcs: continue
        size, sym = funcs[a]
        rows[a] = census(md, pe.get_data(a - base, size), a, vft, vbt, iat_lo, iat_hi)
    if o.out:
        with open(o.out, "w") as f:
            f.write("# address\t" + "\t".join(COLS) + "\tsymbol\n")
            for a, r in rows.items(): f.write(f"{a:08x}\t" + "\t".join(str(r[c]) for c in COLS) + f"\t{funcs[a][1]}\n")
    print(f"vftables {len(vft)}, vbtables {len(vbt)}, IAT {iat_lo:#x}..{iat_hi:#x}")
    print("| set | functions (with extent) | vcall fns / sites | other indirect fns | EH fns | vbase fns | vftable-imm fns "
          "| x87 fns | eligible now | eligible with vcall pass |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, s in sets.items():
        rs = [rows[a] for a in s if a in rows]
        n = lambda k: sum(1 for r in rs if r[k])
        unproven = lambda r: r["ind_other"] or r["ind_reg"]
        bad = lambda r: r["eh"] or r["vbt_imm"] or unproven(r)
        now = sum(1 for r in rs if not bad(r) and not r["vcall"])
        withpass = sum(1 for r in rs if not bad(r))
        print(f"| {name} | {len(s)} ({len(rs)}) | {n('vcall')} / {sum(r['vcall'] for r in rs)} | "
              f"{sum(1 for r in rs if unproven(r))} | {n('eh')} | {n('vbt_imm')} | {n('vft_imm')} | {n('x87')} | "
              f"{now} ({100 * now / max(1, len(rs)):.0f} %) | {withpass} ({100 * withpass / max(1, len(rs)):.0f} %) |")


if __name__ == "__main__":
    main()
