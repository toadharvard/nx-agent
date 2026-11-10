#!/usr/bin/env python3
"""Find position-dependent code in a guest link: PC-relative relocations (adrp/adr/ldr literal/b/bl) whose
symbol is absolute in the linked ELF. S2's aliases.ld binds image data to absolute __fnv_<addr> symbols. An object
that addresses one of them PC-relatively (adrp + add, as GCC does for data it thinks the unit defines) is
correct only while the guest runs at its link address, because a relocated PIE moves the PC and not the
target.

    pcrel_abs.py <linked elf> <readelf> <nm> <response file of objects | object...> [--out list.tsv]

Prints "pc-relative references to absolute symbols: N in M objects (K symbols)" and, with --out, one line per
site (object, section offset, type, symbol). A second stdout line, "pc-relative relocations examined: N", counts
every relocation of those kinds in all objects, whether or not its symbol is absolute. Exit status 0 either way; build.sh pins the guest when N > 0."""
import argparse
import collections
import subprocess
import sys

PCREL = ("ADR_PREL_PG_HI21", "ADR_PREL_PG_HI21_NC", "ADR_PREL_LO21", "LD_PREL_LO19", "CALL26", "JUMP26",
         "CONDBR19", "TSTBR14")


def absolute_symbols(nm, elf):
    out = subprocess.run([nm, elf], capture_output=True, text=True, check=True).stdout
    return {f[2] for f in (l.split() for l in out.splitlines()) if len(f) == 3 and f[1] in "Aa"}


def objects(args):
    for a in args:
        if a.startswith("@"):
            for l in open(a[1:]):
                l = l.strip()
                if l:
                    yield l.strip('"')
        else:
            yield a


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("elf")
    ap.add_argument("readelf")
    ap.add_argument("nm")
    ap.add_argument("objects", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    absyms = absolute_symbols(a.nm, a.elf)
    objs = list(objects(a.objects))
    sites, per_obj, syms, examined = [], collections.Counter(), set(), 0
    for i in range(0, len(objs), 200):
        r = subprocess.run([a.readelf, "-rW"] + objs[i:i + 200], capture_output=True, text=True)
        cur = objs[i] if len(objs[i:i + 200]) == 1 else None
        sec = ""
        for l in r.stdout.splitlines():
            if l.startswith("File: "):
                cur = l[6:]
                continue
            if l.startswith("Relocation section"):
                sec = l.split("'")[1] if "'" in l else l
                continue
            f = l.split()
            if len(f) >= 5 and f[2].startswith("R_AARCH64_"):
                t = f[2][10:]
                t = t[4:] if t.startswith("P32_") else t
                if t in PCREL:
                    examined += 1
                if t in PCREL and f[4] in absyms:
                    sites.append((cur, sec, f[0], t, f[4]))
                    per_obj[cur] += 1
                    syms.add(f[4])
    print(f"pc-relative references to absolute symbols: {len(sites)} in {len(per_obj)} objects ({len(syms)} symbols)")
    print(f"pc-relative relocations examined: {examined}")
    if a.out:
        with open(a.out, "w") as f:
            f.write("# object\tsection\toffset\ttype\tsymbol\n")
            for s in sites:
                f.write("\t".join(str(x) for x in s) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
