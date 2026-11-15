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

# ---- host images: the declared loader (link.loader) --------------------------------------------------------------------------------------
import argparse  # noqa: E402
import contextlib  # noqa: E402
import io  # noqa: E402
import tempfile  # noqa: E402


def elf32(symtab, dynsym, etype=3):
    """An ELF32 little-endian AArch64 image with .symtab and .dynsym; each [(name, bind, defined)]."""
    def table(syms):
        st, ent = b"\0", [b"\0" * 16]
        for n, bind, defined in syms:
            ent.append(struct.pack("<IIIBBH", len(st), 0, 0, bind << 4, 0, 1 if defined else 0))
            st += n.encode() + b"\0"
        return b"".join(ent), st
    (sy, sstr), (dy, dstr) = table(symtab), table(dynsym)
    parts, off = [], 52
    for blob in (sy, sstr, dy, dstr):
        parts.append((off, blob))
        off += len(blob)
    sh = struct.pack("<10I", *([0] * 10))
    for (o, b), typ, link, ent in zip(parts, (2, 3, 11, 3), (2, 0, 4, 0), (16, 0, 16, 0)):
        sh += struct.pack("<10I", 0, typ, 0, 0, o, len(b), link, 0, 4, ent)
    hdr = b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack("<HHIIIIIHHHHHH", etype, 183, 1, 0, 0, off, 0, 52, 0, 0, 40, 5, 0)
    return hdr + b"".join(b for _, b in parts) + sh


def run_host(inputs, image_dyn, loader=None, libdyn=None):
    """ldcheck.host_main on a shared image: INPUTS {object: symbols}, IMAGE_DYN the image's dynamic symbols; -> (exit code, ids)."""
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "o"))
        for n, syms in inputs.items():
            open(os.path.join(d, "o", n + ".o"), "wb").write(elf32(syms, [], 1))
        img = os.path.join(d, "x.so")
        open(img, "wb").write(elf32(image_dyn, image_dyn))
        vs = os.path.join(d, "v.map")
        open(vs, "w").write("{ global: ex; local: *; };\n")
        lib = []
        if libdyn is not None:
            open(os.path.join(d, "lib.so"), "wb").write(elf32([], libdyn))
            lib = [os.path.join(d, "lib.so")]
        ld = None
        if loader is not None:
            ld = os.path.join(d, "defs.txt")
            open(ld, "w").write("".join(n + "\n" for n in loader))
        a = argparse.Namespace(elf=img, objs=os.path.join(d, "o"), out=os.path.join(d, "out"), name="x.so", kind="shared", version_script=vs, lib=lib, loader_defs=ld)
        rc = 0
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                L.host_main(a)
            except SystemExit as x:
                rc = x.code or 0
        return rc, sorted(ln[len("ldcheck: "):] for ln in err.getvalue().splitlines() if ln.startswith("ldcheck: ") and ":" in ln[9:] and "image" not in ln)


EX = [("ex", G, True)]
REF = [("nxgl4_guest_ptr", G, False)]
rc, ids = run_host({"a": EX + REF}, EX + REF)
check("a shared image whose undefined name has no definition anywhere is red", rc == 1 and "output-undefined:nxgl4_guest_ptr" in ids, (rc, ids))
rc, ids = run_host({"a": EX + REF}, EX + REF, loader=["nxgl4_guest_ptr", "nxgl4_on_present"])
check("the same image with the name in the defined set of its declared loader is green", rc == 0 and not ids, (rc, ids))
rc, ids = run_host({"a": EX + REF + [("other", G, False)]}, EX + REF + [("other", G, False)], loader=["nxgl4_guest_ptr"])
check("a loader defining one name does not cover another undefined name: red, naming the other", rc == 1 and "output-undefined:other" in ids
      and "output-undefined:nxgl4_guest_ptr" not in ids, (rc, ids))
rc, ids = run_host({"a": EX + [("memcpy", G, False)]}, EX + [("memcpy", G, False)], libdyn=[("memcpy", G, True)])
check("a name a pinned system library exports stays undefined (libraries, no loader)", rc == 0 and not ids, (rc, ids))
rc, ids = run_host({"a": EX + REF + [("memcpy", G, False)]}, EX + REF + [("memcpy", G, False)], loader=["nxgl4_guest_ptr"], libdyn=[("memcpy", G, True)])
check("undefined = system exports + loader's defined set: both together are green", rc == 0 and not ids, (rc, ids))
rc, ids = run_host({"a": EX + REF}, EX + REF, loader=[])
check("a declared loader that defines nothing covers nothing", rc == 1 and "output-undefined:nxgl4_guest_ptr" in ids, (rc, ids))
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
