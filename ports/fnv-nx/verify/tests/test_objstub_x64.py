#!/usr/bin/env python3
"""Tests for verify/objstub_x64.py: the x86-64 body analyzer of the host-profile roots (one rule set for both ISAs, objstub.judge shared).
Plain script, one PASS/FAIL line per case, exit 0 on pass. Objects: the x86-64 distribution gcc and aarch64-linux-gnu-gcc, scratch under tempfile.mkdtemp."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import objstub  # noqa: E402
import scratch  # noqa: E402

scratch.install()
ROOT = tempfile.mkdtemp(prefix="objstub-x64-")
FAILED, N = [], [0]
CC = {"x64": ["gcc", "-fcf-protection=none", "-fno-asynchronous-unwind-tables", "-fno-pic", "-fno-stack-protector"], "a64": ["aarch64-linux-gnu-gcc", "-mabi=lp64"] if __import__("shutil").which("aarch64-linux-gnu-gcc") else ["clang", "--target=aarch64-linux-gnu", "-fno-asynchronous-unwind-tables"]}   # the AArch64 object of the per-ISA selftest: the pinned cross gcc when installed, else clang of env.json


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def build(src, isa, opt="-O2"):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.c" % N[0]), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run(CC[isa] + [opt, "-c", s, "-o", o], check=True, capture_output=True)
    return o


def kinds(path, terms=(), rows=None, osl=(), machine=None):
    rows = {(path, k): v for k, v in (rows or {}).items()}
    items, finds = objstub.run_check([(path, path)], set(terms), rows, set(osl), None, machine=machine)
    return {(n, k) for _, n, k, _ in finds}, items, finds


STUB = "int stub(void) { return 0; }\n"
REAL = "extern int ext(int);\nint g;\nint real(int a) { g = a; return ext(a) + 1; }\n"
try:
    objstub.tool()
    A64 = True
except ValueError as e:   # the container has no LLVM 18 development files: the AArch64 half runs on the verdict environment (env.json packages)
    A64 = False
    print("SKIP a64 cases: " + str(e)[:80].replace("\n", " "))
for isa, m in (("a64", 183), ("x64", 62)):
    if isa == "a64" and not A64:
        continue
    for opt in ("-O0", "-O2"):
        k, _, _ = kinds(build(STUB, isa, opt), machine=m)
        check("%s %s: a stub body (constant 0, no row) is red: V without a row" % (isa, opt), ("stub", "V") in k, k)
        k, _, _ = kinds(build(STUB, isa, opt), rows={"stub": ("u32", "0", "r")}, machine=m)
        check("%s %s: the same body with its row is green" % (isa, opt), not k, k)
        k, _, _ = kinds(build(STUB, isa, opt), rows={"stub": ("u32", "1", "r")}, machine=m)
        check("%s %s: a row that differs from the body's constant is red" % (isa, opt), ("stub", "constants-mismatch") in k, k)
        k, _, _ = kinds(build(REAL, isa, opt), machine=m)
        check("%s %s: a real body (a store to a global, a call, an input read) is green with no row" % (isa, opt), not k, k)
for isa, m, other in (("a64", 183, 62), ("x64", 62, 183)):
    o = build(REAL, isa)   # e_machine is read from the header before any decoder runs: needs no AArch64 decoder
    try:
        kinds(o, machine=other)
        check("%s object under a profile that requires e_machine %d is red" % (isa, other), False)
    except ValueError as e:
        check("%s object under a profile that requires e_machine %d is red" % (isa, other), "e_machine" in str(e), e)

# x86-64 specific shapes
V = {
    "void empty": ("void f(void) {}\n", {"f": ("void", "", "r")}),
    "constant 7": ("int f(void) { return 7; }\n", {"f": ("u32", "7", "r")}),
    "u64 constant": ("long long f(void) { return 0x123456789LL; }\n", {"f": ("u64", "0x123456789", "r")}),
    "address of a global": ("static int g; int *f(void) { return &g; }\n", {"f": ("addr", "g", "r")}),
    "frame only": ("int f(void) { int x[4]; x[0] = 3; return x[0]; }\n", {"f": ("u32", "3", "r")}),
}
for what, (src, rows) in V.items():
    for opt in ("-O0", "-O2"):
        p = build(src, "x64", opt)
        k, _, f = kinds(p)
        check("x64 %s: %s is V, red without a row" % (opt, what), ("f", "V") in k, f)
        k, _, f = kinds(p, rows=rows)
        check("x64 %s: %s with its row is green" % (opt, what), not k, f)
NOTV = {
    "reads its argument": "int f(int a) { return a; }\n",
    "stores through a pointer": "void f(int *p) { *p = 1; }\n",
    "loads a global": "int g; int f(void) { return g; }\n",
    "calls": "extern void h(void); void f(void) { h(); }\n",
    "indirect call": "void f(void (*h)(void)) { h(); }\n",
    "branches on an input": "int f(int a) { return a ? 1 : 2; }\n",
}
for what, src in NOTV.items():
    for opt in ("-O0", "-O2"):
        k, _, f = kinds(build(src, "x64", opt))
        check("x64 %s: %s is not V (green, no row needed)" % (opt, what), ("f", "V") not in k and not any(x[2] == "V" for x in f), f)

# T classes
k, _, f = kinds(build("void f(void) { __builtin_trap(); }\n", "x64"))
check("x64: ud2 is T1", ("f", "T1") in k, f)
k, _, f = kinds(build("void f(void) { __asm__ volatile(\"int3\"); }\n", "x64"))
check("x64: int3 is T1", ("f", "T1") in k, f)
k, _, f = kinds(build("void f(void) { __asm__ volatile(\"hlt\"); }\n", "x64"))
check("x64: hlt is T1", ("f", "T1") in k, f)
k, _, f = kinds(build("void f(void) { __asm__ volatile(\"syscall\"); }\n", "x64"))
check("x64: syscall is T1 outside an os-layer object", ("f", "T1") in k, f)
p = build("void f(void) { __asm__ volatile(\"syscall\"); }\n", "x64")
k, _, f = kinds(p, osl=[p])
check("x64: syscall is accepted in an os-layer object", ("f", "T1") not in k, f)
k, _, f = kinds(build("extern void die(void);\nvoid f(int a) { if (a) die(); }\n", "x64"), terms={"die"})
check("x64: a MIXED body (one path ends in a terminator, one returns) is not T2 (judged by the V rules)", ("f", "T2") not in k, f)
k, _, f = kinds(build("extern void die(void) __attribute__((noreturn));\nvoid f(void) { die(); }\n", "x64"), terms={"die"})
check("x64: a body that only calls a noreturn terminator is T2 (and T2-noreturn-tail or T2)", ("f", "T2") in k, f)
k, _, f = kinds(build("void f(void (*h)(void)) { h(); __builtin_unreachable(); }\n", "x64"))
check("x64: a last call through a pointer is indirect-noreturn-tail", ("f", "indirect-noreturn-tail") in k, f)
k, _, f = kinds(build("int a(void) { return 1; }\nint b(void) { return 1; }\n", "x64"), rows={"a": ("u32", "1", "r"), "b": ("u32", "1", "r")})
check("x64: two V bodies, each with its row, are green", not k, f)

# an undecodable byte run and an uncovered gap
S = os.path.join(ROOT, "bad.s")
open(S, "w").write(".text\n.globl f\n.type f,@function\nf:\n .byte 0x06\n ret\n.size f,.-f\n")
subprocess.run(["gcc", "-c", S, "-o", os.path.join(ROOT, "bad.o")], check=True, capture_output=True)
k, _, f = kinds(os.path.join(ROOT, "bad.o"))
check("x64: a byte sequence the decoder rejects is undecodable", ("f", "undecodable") in k, f)
open(S, "w").write(".text\n.globl f\n.type f,@function\nf:\n ret\n.size f,.-f\n mov $1,%eax\n ret\n")
subprocess.run(["gcc", "-c", S, "-o", os.path.join(ROOT, "gap.o")], check=True, capture_output=True)
k, i, f = kinds(os.path.join(ROOT, "gap.o"))
check("x64: executable bytes outside every function are uncovered", any(x[2] == "uncovered" for x in f), f)

# the analysis is cached per object and keyed by the ISA analyzer and the decoder version
cdir = os.path.join(ROOT, "cache")
o1 = build(STUB, "x64")
r1 = objstub.analyse_all([(o1, o1)], set(), set(), cdir, 1, None, 62)
r2 = objstub.analyse_all([(o1, o1)], set(), set(), cdir, 1, None, 62)
check("x64: a second run is served from the cache with the same result", r1 == r2 and os.listdir(os.path.join(cdir, "objstub")), r1)

# image_check on a linked x86-64 image (the host profile's elf output): the same fixed-failure rule, errno reached through a call
IMG = ("#include <errno.h>\n#include <unistd.h>\n"
       "int fixedfail(void) { errno = 22; return -1; }\n"
       "int realone(int fd) { errno = 22; return (int)write(fd, \"x\", 1); }\n"
       "int noerrno(void) { return -1; }\n"
       "int twice(void) { errno = 5; errno = 6; return 0; }\n"
       "int main(void) { return fixedfail() + realone(1) + noerrno() + twice(); }\n")
src_ = os.path.join(ROOT, "img.c")
open(src_, "w").write(IMG)
for tag, extra in (("dynamic", []), ("static", ["-static"])):
    exe = os.path.join(ROOT, "img-" + tag)
    subprocess.run(CC["x64"] + ["-O2", "-no-pie"] + extra + [src_, "-o", exe], check=True, capture_output=True)
    n, out = objstub.image_check(exe, ("call", "__errno_location"))
    got = {(nm, k) for nm, k, _ in out}
    check("x64 image (%s): errno=K; return K is fixed-failure" % tag, ("fixedfail", "fixed-failure") in got and ("twice", "fixed-failure") in got, out)
    check("x64 image (%s): a body that also calls write, or has no errno store, is not judged a fixed failure" % tag, not {"realone", "noerrno", "main"} & {nm for nm, _ in got}, out)
    check("x64 image (%s): every function body of the image is counted" % tag, n >= 5, n)
n, out = objstub.image_check(os.path.join(ROOT, "img-dynamic"), ("tls", "errno"))
check("x64 image: errno through a thread-local symbol is unjudged (red, never skipped)", [k for _, k, _ in out] == ["unjudged"], out)
try:
    objstub.image_check(os.path.join(ROOT, "o1.o"), ("call", "__errno_location"))
    check("x64 image: a relocatable object is not an image", False)
except ValueError:
    check("x64 image: a relocatable object is not an image", True)

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
