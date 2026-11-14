#!/usr/bin/env python3
"""Tests for link/ldcheck.py symbol_ids: the rules on the symbol tables of the inputs and of the linked output, among them the weak-undefined rule
(a WEAK undefined reference of a final guest image is red, naming the symbol and the objects that reference it; no exception list). Pure: ELF32
AArch64 images with a hand-written symbol table, no build."""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "link"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "contract"))
import ldcheck as L  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


def elf(symbols):
    """An ELF32 little-endian AArch64 image with sections null, .symtab and .strtab; SYMBOLS [(name, bind, defined)] (bind 1 global, 2 weak)."""
    strtab, syms = b"\0", [b"\0" * 16]
    for n, bind, defined in symbols:
        syms.append(struct.pack("<IIIBBH", len(strtab), 0, 0, bind << 4 | 0, 0, 1 if defined else 0))   # st_name value size info other shndx
        strtab += n.encode() + b"\0"
    symtab = b"".join(syms)
    off_sym, off_str = 52, 52 + len(symtab)
    shoff = off_str + len(strtab)
    sh = struct.pack("<IIIIIIIIII", *([0] * 10))
    sh += struct.pack("<IIIIIIIIII", 0, 2, 0, 0, off_sym, len(symtab), 2, 0, 4, 16)        # .symtab: link = 2 (.strtab), entsize 16
    sh += struct.pack("<IIIIIIIIII", 0, 3, 0, 0, off_str, len(strtab), 0, 0, 1, 0)
    hdr = b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack("<HHIIIIIHHHHHH", 3, 183, 1, 0, 0, shoff, 0, 52, 0, 0, 40, 3, 0)
    return hdr + symtab + strtab + sh


def objs(**per):
    out = {}
    for o, syms in per.items():
        d, st, wk, _, _ = L.symtab_info(elf(syms))
        out[o + ".o"] = (d, st, wk)
    return out


G, W = 1, 2
# a weak undefined reference nobody defines (the linker kept it in the output's symbol table)
ids = L.symbol_ids(objs(a=[("f", G, True), ("wf", W, False)], b=[("wf", W, False)]), {"f"}, elf([("f", G, True), ("wf", W, False)]))
check("a weak undefined reference with no definition is red, naming the symbol and every referencing object",
      ids == ["weak-undefined:wf <- a.o, b.o"], ids)
# the linker dropped it from the output (resolved to 0): the inputs' tables still show it
ids = L.symbol_ids(objs(a=[("f", G, True), ("wf", W, False)]), {"f"}, elf([("f", G, True)]))
check("a weak reference the linker dropped from the output is red too", ids == ["weak-undefined:wf <- a.o"], ids)
# the output holds it undefined, but no input object names it (a library member): the symbol is reported, the objects are none
ids = L.symbol_ids(objs(a=[("f", G, True)]), {"f"}, elf([("f", G, True), ("lw", W, False)]))
check("a weak undefined symbol only in the output's table is red, with no referencing input object",
      len(ids) == 1 and ids[0].startswith("weak-undefined:lw <- (none"), ids)
# a weak reference that the output defines (another object defines it strongly)
ids = L.symbol_ids(objs(a=[("wf", W, False)], b=[("wf", G, True)]), {"wf"}, elf([("wf", G, True)]))
check("a weak reference that is defined is green", ids == [], ids)
# a weak definition is a definition
ids = L.symbol_ids(objs(a=[("wd", W, True)]), {"wd"}, elf([("wd", W, True)]))
check("a weak definition is green", ids == [], ids)
# a strong undefined reference is not a weak one: its own ids
ids = L.symbol_ids(objs(a=[("s", G, False)]), set(), elf([("s", G, False)]))
check("a strong undefined reference is cover and output-undefined, not weak-undefined",
      ids == ["cover:s", "output-undefined:s"], ids)
# a name referenced weakly by one object and strongly by another is a strong reference
ids = L.symbol_ids(objs(a=[("x", W, False)], b=[("x", G, False)]), set(), elf([("x", G, False)]))
check("a symbol referenced strongly anywhere is not weak-only", all(not i.startswith("weak-undefined") for i in ids), ids)
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
