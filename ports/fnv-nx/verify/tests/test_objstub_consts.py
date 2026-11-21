#!/usr/bin/env python3
"""Tests of the declared-constant records (`.fnvnx.const`, contract/l2consts.py) as objstub judges them: toy ILP32 and LP64 objects from assembler.
Plain script, one PASS/FAIL line per case, exit 0 on pass. Needs aarch64-linux-gnu-gcc (env.json)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx)
from fnvnx.verify import objstub  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="objstub-consts-")
FAILED, N = [], [0]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def func(name, body):
    return ".text\n.global %s\n.type %s,%%function\n%s:\n%s\n ret\n.size %s,.-%s\n" % (name, name, name, body, name, name)


def rec(abi, fn, length, value=""):
    ptr = ".4byte" if abi == "ilp32" else ".8byte"
    return ".pushsection .fnvnx.const,\"\",%%progbits\n.balign 4\n%s %s\n.4byte %d\n%s.balign 4\n.popsection\n" % (ptr, fn, length, value)


def ident(abi, fn, k):
    ptr = ".4byte" if abi == "ilp32" else ".8byte"
    return ".pushsection .fnvnx.ident,\"\",%%progbits\n.balign 4\n%s %s\n.4byte %d\n.balign 4\n.popsection\n" % (ptr, fn, k)


def judge(abi, src):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.s" % N[0]), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run(["aarch64-linux-gnu-gcc", "-mabi=" + abi, "-c", s, "-o", o], check=True, capture_output=True)
    _, finds = objstub.run_check([(o, o)], set(), set())
    return {(n, k) for _, n, k, _ in finds}, finds


for abi in ("ilp32", "lp64"):
    P = lambda n: "%s: %s" % (abi, n)   # noqa: E731
    w = ".8byte" if abi == "lp64" else ".4byte"
    seven = func("f", " mov w0, #7")
    k, f = judge(abi, seven + rec(abi, "f", 4, "\t.4byte 7\n"))
    check(P("a body with one record that equals its constant is certified"), not k, f)
    k, f = judge(abi, seven + rec(abi, "f", 4, "\t.4byte 8\n"))
    check(P("a record that differs from the body's constant is const-mismatch"), k == {("f", "const-mismatch")}, f)
    k, f = judge(abi, seven)
    check(P("a V body without a record is const-missing"), k == {("f", "const-missing")}, f)
    k, f = judge(abi, seven + rec(abi, "f", 4, "\t.4byte 7\n") + rec(abi, "f", 4, "\t.4byte 7\n"))
    check(P("two records for one body are const-duplicate, even equal"), k == {("f", "const-duplicate")}, f)
    k, f = judge(abi, func("v", "") + rec(abi, "v", 0))
    check(P("a LEN 0 record certifies a void body"), not k, f)
    k, f = judge(abi, func("v", "") + rec(abi, "v", 4, "\t.4byte 0\n"))
    check(P("a value record on a void body is const-mismatch"), k == {("v", "const-mismatch")}, f)
    k, f = judge(abi, func("b", " mov w0, #0x1ff") + rec(abi, "b", 1, "\t.byte 0xff\n") + ".balign 4\n")
    check(P("a one-byte record compares the low 8 bits bit for bit"), not k, f)
    k, f = judge(abi, func("b", " mov w0, #0x1ff") + rec(abi, "b", 1, "\t.byte 0xfe\n"))
    check(P("a one-byte record that differs in the low 8 bits is const-mismatch"), k == {("b", "const-mismatch")}, f)
    k, f = judge(abi, func("g", " adrp x0, gl\n add x0, x0, :lo12:gl" if abi == "lp64" else " adrp x0, gl\n add x0, x0, :lo12:gl") + ".bss\n.global gl\ngl: .space 4\n"
                 + rec(abi, "g", 8 if abi == "lp64" else 4, "\t%s gl\n" % w))
    check(P("an address record (a relocation at the value) certifies the address of that symbol"), not k, f)
    k, f = judge(abi, seven + ".pushsection .fnvnx.const,\"\",%progbits\n.balign 4\n.4byte 0\n.4byte 4\n.4byte 7\n.popsection\n")
    check(P("a record with no relocation is const-malformed (and the body const-missing)"), ("f", "const-missing") in k and any(n == ".fnvnx.const" for n, _ in k) and any(kk == "const-malformed" for _, kk in k), f)
    k, f = judge(abi, seven + rec(abi, "nowhere", 4, "\t.4byte 7\n"))
    check(P("a record whose relocation is no function of the object is const-malformed"), any(kk == "const-malformed" for _, kk in k), f)
    k, f = judge(abi, func("r", " mov w0, w1") + rec(abi, "r", 4, "\t.4byte 1\n"))
    check(P("a record on a body that is not V is const-mismatch"), k == {("r", "const-mismatch")}, f)

for abi in ("ilp32", "lp64"):
    P = lambda n: "%s: %s" % (abi, n)   # noqa: E731
    k, f = judge(abi, func("i", " mov w0, w1") + ident(abi, "i", 1))
    check(P("an identity record k=1 certifies a body that returns its argument 1 (32-bit mov)"), not k, f)
    k, f = judge(abi, func("i", " mov x0, x1") + ident(abi, "i", 1))
    check(P("an identity record k=1 certifies a body that returns its argument 1 (64-bit mov)"), not k, f)
    k, f = judge(abi, func("i", "") + ident(abi, "i", 0))
    check(P("an identity record k=0 certifies a bare ret"), not k, f)
    k, f = judge(abi, func("i", " mov w0, w1") + ident(abi, "i", 0))
    check(P("an identity record of another argument (k=0, body returns 1) is const-mismatch"), k == {("i", "const-mismatch")}, f)
    k, f = judge(abi, func("i", " mov w0, w2") + ident(abi, "i", 1))
    check(P("an identity record k=1 on a body that returns argument 2 is const-mismatch"), k == {("i", "const-mismatch")}, f)
    k, f = judge(abi, func("i", " mov w0, w1\n str w0, [x2]") + ident(abi, "i", 1))
    check(P("an identity record on a body that is not V (a store through an argument) is const-mismatch"), k == {("i", "const-mismatch")}, f)
    k, f = judge(abi, func("i", " add w0, w1, #1") + ident(abi, "i", 1))
    check(P("an identity record on a body that changes the argument is const-mismatch"), k == {("i", "const-mismatch")}, f)
    k, f = judge(abi, func("i", "") + ident(abi, "i", 0) + rec(abi, "i", 0))
    check(P("a function with an identity record and a const record is const-duplicate"), k == {("i", "const-duplicate")}, f)
    k, f = judge(abi, func("i", " mov w0, #7") + ident(abi, "i", 0) + rec(abi, "i", 4, "\t.4byte 7\n"))
    check(P("an identity record and a value record on one function is const-duplicate"), k == {("i", "const-duplicate")}, f)
    k, f = judge(abi, func("i", " mov w0, w1") + ident(abi, "i", 1) + ident(abi, "i", 1))
    check(P("two identity records are const-duplicate"), k == {("i", "const-duplicate")}, f)
    k, f = judge(abi, func("i", " mov w0, w1"))
    check(P("a body that returns its argument and has no record is not V (an implementation, no finding)"), not k, f)
    k, f = judge(abi, func("i", " mov w0, w1") + ident(abi, "nowhere", 1))
    check(P("an identity record whose relocation is no function of the object is const-malformed"), any(kk == "const-malformed" for _, kk in k), f)

if FAILED:
    print("%d failed" % len(FAILED))
    sys.exit(1)
print("all passed")
