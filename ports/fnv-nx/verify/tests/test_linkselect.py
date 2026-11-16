#!/usr/bin/env python3
"""The link-member rule (RECIPE.md "Link members"): every candidate root is offered as an archive (recipe.ar_bytes) and the pinned ld picks the objects, with the
seed own-undefined + the symbols the script references (found by ld itself, recipe.script_refs_loop) + the entry. Run here with the host aarch64 binutils on toy
objects: the rule is ld's archive semantics, the code under test is the archive writer, the script-reference loop and the map reading."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


AS, LD = "aarch64-linux-gnu-as", "aarch64-linux-gnu-ld"
if not all(subprocess.run(["which", t], capture_output=True).returncode == 0 for t in (AS, LD)):
    print("SKIP: no host aarch64 binutils")
    sys.exit(0)
SCRIPT = 'SECTIONS { . = 0x1000; .text : { *(.text*) } /DISCARD/ : { *(.note*) *(.comment) } }\nASSERT(hdr == start + 4, "hdr")\nASSERT(mod0 == hdr + 8, "mod0")\n'
SRC = {   # object -> assembler: .globl definitions, `bl` references
    "crt0": "start: b image_entry\nhdr: .word 0\n.globl start, hdr, mod0\nmod0 = hdr + 8\n",
    "runtime": ".globl guest_entry\nguest_entry: bl main\nbl dispatch\nret\n",
    "main": ".globl main\nmain: bl loader\nret\n",
    "loader": ".globl loader\nloader: ret\n",
    "disp": ".globl dispatch\ndispatch: bl pack_a\nret\n",
    "pack_a": ".globl pack_a\npack_a: ret\n",
    "pack_b": ".globl pack_b\npack_b: ret\n",
    "tent": ".globl tent\ntent: bl helper\nret\n",
    "helper": ".globl helper\nhelper: ret\n",
}
with tempfile.TemporaryDirectory() as td:
    obj = {}
    for n, src in SRC.items():
        open(os.path.join(td, n + ".s"), "w").write(".text\n" + src)
        subprocess.run([AS, os.path.join(td, n + ".s"), "-o", os.path.join(td, n + ".o")], check=True)
        obj[n] = open(os.path.join(td, n + ".o"), "rb").read()
    open(os.path.join(td, "s.ld"), "w").write(SCRIPT)
    open(os.path.join(td, "empty.o"), "wb").write(recipe.empty_object(2, 183))

    def run(defined):    # the script alone, as the stage does it
        r = subprocess.run([LD, "-m", "aarch64linux", "-T", "s.ld", "--unresolved-symbols=report-all", "-o", "x.elf"] + ["--defsym=%s=0" % n for n in defined] + ["empty.o"],
                           cwd=td, capture_output=True, text=True)
        return None if r.returncode == 0 else r.stderr.splitlines()
    refs = recipe.script_refs_loop(run)
    check("the script's references are what ld reports, over passes (all three are named in expressions; ENTRY is a reference ld resolves itself)", refs == ["hdr", "mod0", "start"], refs)
    check("a script that links alone has none", recipe.script_refs_loop(lambda d: None) == [])
    check("a failing script link that names no undefined symbol has none (the real link reports it)", recipe.script_refs_loop(lambda d: ["boom"]) == [])

    roots = [("01-rt", ["crt0", "runtime"]), ("02-main", ["main", "loader"]), ("03-pack", ["disp", "pack_a", "pack_b"]), ("04-aux", ["helper"])]
    for label, names in roots:
        open(os.path.join(td, label + ".a"), "wb").write(recipe.ar_bytes([("%05d.o" % i, obj[n]) for i, n in enumerate(names)]))
    check("the archive is a pure function of its members", recipe.ar_bytes([("00000.o", obj["main"])]) == recipe.ar_bytes([("00000.o", obj["main"])]))
    r = subprocess.run(["aarch64-linux-gnu-ar", "t", "01-rt.a"], cwd=td, capture_output=True, text=True)
    check("the archive is a valid ar (the pinned binutils lists its members)", r.stdout.split() == ["00000.o", "00001.o"], (r.stdout, r.stderr))

    def select(own, entry, seed):
        for n in own:
            open(os.path.join(td, "own_%s.o" % n), "wb").write(obj[n])
        argv = [LD, "-m", "aarch64linux", "-T", "s.ld", "--unresolved-symbols=ignore-all", "--defsym=image_entry=" + entry] + [x for r_ in seed for x in ("-u", r_)] + [
            "-Map=m.map", "-o", "sel.elf", "--whole-archive"] + ["own_%s.o" % n for n in own] + ["--no-whole-archive", "--start-group"] + [l + ".a" for l, _ in roots] + ["--end-group"]
        r = subprocess.run(argv, cwd=td, capture_output=True, text=True)
        assert r.returncode == 0 or seed == ["tent"], r.stderr   # (the script alone fails on its own references when nothing seeds them)
        got = {}
        for ar, mem in recipe.map_members(open(os.path.join(td, "m.map")).read()):
            lab = ar[:-2]
            got.setdefault(lab, []).append(names_of[lab][mem])
        return {k: sorted(v) for k, v in got.items()}
    names_of = {l: {"%05d.o" % i: n for i, n in enumerate(ns)} for l, ns in roots}
    # a test image: its own entry (tent -> helper), the script references; no runtime, no pack
    got = select(["tent"], "tent", refs + ["tent"])
    check("a test image gets crt0 and its entry's references only: no runtime, no loader, no pack", got == {"01-rt": ["crt0"], "04-aux": ["helper"]}, got)
    # an image with no undefined symbol of its own still gets crt0 (the script needs it)
    got = select(["helper"], "helper", refs + ["helper"])
    check("an image with no undefined symbols still gets crt0", got == {"01-rt": ["crt0"]}, got)
    # the script-reference seed is what pulls crt0: without it ld pulls nothing
    got = select(["tent"], "tent", ["tent"])
    check("without the script's references crt0 is not pulled (the seed rule is needed)", "01-rt" not in got, got)
    # the product image: entry = the runtime's entry: the full closure over objects, and not pack_b, which nothing references
    got = select([], "guest_entry", refs + ["guest_entry"])
    check("the product image gets the closure of its entry, per object", got == {"01-rt": ["crt0", "runtime"], "02-main": ["loader", "main"], "03-pack": ["disp", "pack_a"]}, got)
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
