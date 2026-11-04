#!/usr/bin/env python3
"""collect_traps.py - the trap list of a collect-all boot (tests/s15/mtqemu/boot_game.sh with COLLECT=1), grouped by
cause and owner, as one TSV (published on fnv-nx-build as reports/s5-traps/<c12>.tsv; derived from the decomp, so
never committed here).

    tests/s5/collect_traps.py <boot.log> <fnvnx.elf> [--addrmap out/addrmap.tsv] [--out traps.tsv]

Sources in boot.log, in boot order:
  linhost: collect <TAB> vslot|special <TAB> caller <TAB> text   virtual / constructor-destructor defined nowhere
  fnvnx boot: trap #N: unresolved NAME (caller C), returning 0     a symbol the link left undefined (mktraps.py)
  fnvnx boot: trap: image code pointer to / IAT slot ADDR ...      an image code pointer or import without a native
  linhost: fault: signal S ... pc P lr L                            the first real fault, which ends the run

Columns: seq, phase (init: before WinMain / winmain), kind, cause, owner, key (the symbol, slot text or address),
caller (symbolized), orig (the caller's exe address, from the __fnv_ alias), note.

Cause -> owner (the orchestrator's split):
  unbound vtable slot (vslot trap)                         -> S2c-vt
  constructor/destructor defined nowhere (special trap)    -> S1-refs (the decomp lacks the body)
  unresolved decomp function (__fnv_<addr>, C++ name)      -> S1-refs
  unresolved Win32/CRT/platform name, image import         -> S4 (missing runtime service)
  Direct3D / renderer name                                 -> render
  image code pointer without native                        -> S2c-vt (bridge/address map)
  real fault                                               -> triage (listed last; ends the run)
"""
from __future__ import annotations

import argparse
import bisect
import collections
import re
import subprocess
import sys

RENDER = re.compile(r"(?i)d3d|direct3d|nine|IDirect3D|D3DX|_Px|shader", re.I)
WIN32 = re.compile(r"^[A-Z][A-Za-z0-9]+[AW]?$|^_?_?(str|mem|wcs|f|v|s)[a-z_]*$|^Reg|^Shell|^Get|^Set|^Create|^Wsa|^Xinput",
                   re.I)


def symbols(elf):
    out = subprocess.run(["aarch64-linux-gnu-nm", "-n", "--defined-only", elf], capture_output=True, text=True).stdout
    by = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 3 and f[1] in "tTwW":
            by.setdefault(int(f[0], 16), []).append(f[2])
    addrs = sorted(by)
    return addrs, [by[a] for a in addrs]


def namer(elf):
    addrs, names = symbols(elf)

    lo = next((x for x in addrs if x >= 0x10000000), 0)   # the guest's link base region

    def sym(a):
        if a < lo and 0x05000000 <= a < 0x10000000:   # a runtime address of a relocated guest (emu_guest.py places
            a = a - 0x05000000 + (lo & ~0xFFFFFF)       # it at 0x05000000): back to the link address
        i = bisect.bisect_right(addrs, a) - 1
        if i < 0 or a - addrs[i] > 0x100000:
            return f"{a:#x}", ""
        ns = names[i]
        native = next((n for n in ns if not n.startswith("__fnv_")), ns[0])
        orig = next((n[6:14] for n in ns if re.fullmatch(r"__fnv_[0-9a-f]{8}", n)), "")
        return f"{native}+{a - addrs[i]:#x}", orig
    return sym


def classify_symbol(name):
    if re.fullmatch(r"__fnv_[0-9a-f]{8}", name) or name.startswith("_Z") and "FUN_" in name:
        return "unresolved decomp function", "S1-refs"
    if RENDER.search(name):
        return "renderer", "render"
    if name.startswith("_Z"):
        return "unresolved decomp function", "S1-refs"
    if WIN32.match(name):
        return "missing runtime service", "S4"
    return "unresolved decomp function", "S1-refs"


def parse(log, sym):
    rows, phase, seq = [], "init", 0
    for line in open(log, errors="replace"):
        line = line.rstrip("\n")
        if "fnvnx boot: calling WinMain" in line:
            phase = "winmain"
        m = re.match(r"linhost: collect\t(vslot|special)\t(0x[0-9a-f]+)\t(.*?)\t#\d+$", line)
        if m:
            kind, caller, text = m.group(1), int(m.group(2), 16), m.group(3)
            c, orig = sym(caller)
            if kind == "vslot":
                cause, owner = "unbound vtable slot", "S2c-vt"
                k = re.match(r"virtual function defined nowhere: (\S+)(.*)", text)
                key, note = (k.group(1), k.group(2).strip()) if k else (text, "")
            else:
                cause, owner = "constructor/destructor defined nowhere", "S1-refs"
                k = re.match(r"constructor/destructor defined nowhere: (\S+)(.*)", text)
                key, note = (k.group(1), k.group(2).strip()) if k else (text, "")
            seq += 1
            rows.append((seq, phase, kind, cause, owner, key, c, orig, note))
            continue
        m = re.search(r"skipped initializer (0x[0-9a-f]+)\S* ?(.*?): (.*?) at (0x[0-9a-f]+)", line)
        if m:   # an initializer that faulted (--skip-faulting-init): the fault, at its pc
            c, orig = sym(int(m.group(4), 16))
            seq += 1
            rows.append((seq, phase, "init fault", "initializer fault (skipped)", "triage", m.group(3)[:120], c, orig,
                         f"initializer {m.group(1)} {m.group(2)}".strip()))
            continue
        m = re.search(r"fnvnx boot: trap #\d+: unresolved (\S+) \(caller (0x[0-9a-f]+)\)", line)
        if m:
            name, caller = m.group(1), int(m.group(2), 16)
            c, orig = sym(caller)
            cause, owner = classify_symbol(name)
            seq += 1
            rows.append((seq, phase, "undefined", cause, owner, name, c, orig, ""))
            continue
        m = re.search(r"fnvnx boot: trap: (IAT slot|image code pointer to) (0x[0-9a-f]+|[0-9a-f]+)", line)
        if m:
            seq += 1
            iat = m.group(1) == "IAT slot"
            rows.append((seq, phase, "image pointer", "missing runtime service" if iat else "image code pointer without native",
                         "S4" if iat else "S2c-vt", m.group(2), "", "", line.split("trap: ", 1)[1]))
            continue
        m = re.match(r"linhost: fault: signal (\d+) thread \d+ pc (0x[0-9a-f]+) lr (0x[0-9a-f]+) addr (0x[0-9a-f]+)", line)
        if m:
            pc, lr = int(m.group(2), 16), int(m.group(3), 16)
            c, orig = sym(lr)
            seq += 1
            rows.append((seq, phase, "fault", "real fault (ends the run)", "triage",
                         f"signal {m.group(1)} pc {sym(pc)[0]} addr {m.group(4)}", c, orig, ""))
    return rows


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("elf")
    ap.add_argument("--out", help="the TSV (default: stdout)")
    a = ap.parse_args(argv)
    rows = parse(a.log, namer(a.elf))
    head = "seq\tphase\tkind\tcause\towner\tkey\tcaller\torig\tnote"
    text = "\n".join([head] + ["\t".join(str(v) for v in r) for r in rows]) + "\n"
    if a.out:
        open(a.out, "w").write(text)
    else:
        sys.stdout.write(text)
    by = collections.Counter((r[4], r[3]) for r in rows)
    print(f"collect_traps: {len(rows)} traps", file=sys.stderr)
    for (owner, cause), n in sorted(by.items()):
        print(f"  {owner:8} {n:5}  {cause}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
