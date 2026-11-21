#!/usr/bin/env python3
"""Tests of the constant-store rule of objstub (const_cells): a cell with internal linkage, whose address does not escape and whose every store
writes its static initializer, is constant; a store to it is no effect. Toy ILP32 and LP64 objects from the assembler.
Plain script, one PASS/FAIL line per case, exit 0 on pass. Needs aarch64-linux-gnu-gcc (env.json)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx)
from fnvnx.verify import objstub  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="objstub-cells-")
FAILED, N = [], [0]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def func(name, body, glob=True):
    return ".text\n%s.type %s,%%function\n%s:\n%s\n ret\n.size %s,.-%s\n" % (".global %s\n" % name if glob else "", name, name, body, name, name)


BSS = ".bss\n.balign 4\n.type g,%object\n.size g,4\ng: .zero 4\n"
DATA7 = ".data\n.balign 4\n.type g,%object\n.size g,4\ng: .word 7\n"
GBSS = ".bss\n.balign 4\n.global g\n.type g,%object\n.size g,4\ng: .zero 4\n"
ST = " adrp x0, g\n mov w1, #%d\n str w1, [x0, :lo12:g]\n"


def kinds(abi, src):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.s" % N[0]), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run(["aarch64-linux-gnu-gcc", "-mabi=" + abi, "-c", s, "-o", o], check=True, capture_output=True)
    _, finds = objstub.run_check([(o, o)], set(), set())
    return {(n, k) for _, n, k, _ in finds}, finds


# "V" shows as const-missing here (a V body with no record). No finding at all: the body has an effect, it is an implementation.
V = {("f", "const-missing")}
for abi in ("ilp32", "lp64"):
    P = lambda n: "%s: %s" % (abi, n)   # noqa: E731
    k, f = kinds(abi, func("f", ST % 0) + BSS)
    check(P("a store of the initial value (0) to a local bss cell is no effect: V"), k == V, f)
    k, f = kinds(abi, func("f", ST % 7) + DATA7)
    check(P("a store of the initial value (7) to a local data cell is V"), k == V, f)
    k, f = kinds(abi, func("f", ST % 5) + BSS)
    check(P("a store of a non-initial value is an effect"), not k, f)
    k, f = kinds(abi, func("f", ST % 8) + DATA7)
    check(P("a store of 8 to a cell that starts at 7 is an effect"), not k, f)
    k, f = kinds(abi, func("f", ST % 0 + " add x2, x0, :lo12:g\n") + BSS)
    check(P("an ADD lo12 of the cell is an escape: the store is an effect"), not k, f)
    k, f = kinds(abi, func("f", ST % 0) + func("h", ST % 5, False) + BSS)
    check(P("a non-initial store in another function of the object makes the cell not constant"), ("f", "const-missing") not in k, f)
    k, f = kinds(abi, func("f", ST % 0) + GBSS)
    check(P("a global cell is not constant"), not k, f)
    k, f = kinds(abi, func("f", ST % 0) + BSS + ".data\n.balign 8\np: .%s g\n" % ("8byte" if abi == "lp64" else "4byte"))
    check(P("a data pointer to the cell is an escape"), not k, f)
    k, f = kinds(abi, func("f", " adrp x0, g\n str s0, [x0, :lo12:g]\n") + BSS)
    check(P("a SIMD store is not a constant store"), not k, f)
    k, f = kinds(abi, func("f", " adrp x0, g\n mov w1, #0\n strb w1, [x0, :lo12:g]\n") + BSS)
    check(P("a byte store of 0 into a zero cell is V"), k == V, f)
    k, f = kinds(abi, func("f", " adrp x0, g\n mov w1, #1\n strb w1, [x0, :lo12:g]\n") + BSS)
    check(P("a byte store of 1 into a zero cell is an effect"), not k, f)
    k, f = kinds(abi, func("f", " adrp x0, g\n str w1, [x0, :lo12:g]\n") + BSS)
    check(P("a store of an unknown register is an effect"), not k, f)
    k, f = kinds(abi, func("f", " adrp x0, g\n ldr w1, [x0, :lo12:g]\n") + BSS)
    check(P("a load of a constant cell is no effect"), k == V, f)
    k, f = kinds(abi, func("f", " adrp x0, g\n mov w1, #0\n str w1, [x0, :lo12:g]\n bl ext\n") + BSS)
    check(P("a call after the store stays an effect"), not k, f)

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
