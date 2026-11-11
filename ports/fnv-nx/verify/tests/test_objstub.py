#!/usr/bin/env python3
"""Tests for verify/objstub.py (gate objstub). Plain script: one PASS/FAIL line per case, exit 0 on pass.
Compiles with aarch64-linux-gnu-gcc in both ABIs (ilp32, lp64); scratch under tempfile.mkdtemp."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import objstub  # noqa: E402

CC = "aarch64-linux-gnu-gcc"
ROOT = tempfile.mkdtemp(prefix="objstub-")
FAILED = []
N = [0]
T0 = time.time()


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def build(src, abi, ext="c", extra=()):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.%s" % (N[0], ext)), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run([CC, "-mabi=" + abi, "-O2", "-c", s, "-o", o] + list(extra), check=True, capture_output=True)
    return o


def kinds(objs, terms=(), rows=None, osl=()):
    items, finds = objstub.run_check(objs if isinstance(objs, list) else [objs], set(terms), rows or {}, list(osl))
    return {(n, k) for _, n, k, _ in finds}, items, finds


def row(sym, kind, val=""):
    return {sym: (kind, val, "ref")}


# terminator derivation: plugin on a tiny header and on the real libc headers
tiny = os.path.join(ROOT, "tiny.h")
open(tiny, "w").write("void myabort(void) __attribute__((noreturn));\nvoid fine(void);\n")
tf = os.path.join(ROOT, "terms.txt")
names = objstub.derive_terminators(CC, ["-I" + ROOT], ["tiny.h"], "c", tf)
check("terminators: tiny header", names == ["myabort"], names)
libc = objstub.derive_terminators(CC, [], ["stdlib.h", "unistd.h", "assert.h", "signal.h"], "c", tf + ".libc")
check("terminators: libc set", all(x in libc for x in ("abort", "exit", "_Exit", "quick_exit")) and "atexit" not in libc
      and "raise" not in libc, libc)
cxx = objstub.derive_terminators("aarch64-linux-gnu-g++", [], ["cstdlib", "exception"], "c++", tf + ".cxx")
check("terminators: C++ set has mangled terminate", "_ZSt9terminatev" in cxx and "abort" in cxx, cxx)
print("INFO libc set size %d, c++ set size %d" % (len(libc), len(cxx)))

for abi in ("ilp32", "lp64"):
    T = lambda n: "%s/%s" % (abi, n)
    o = build("void f(void){__builtin_trap();}", abi)
    k, _, _ = kinds(o)
    check(T("trap -> T1"), ("f", "T1") in k, k)
    o = build('#include "tiny.h"\nvoid f(int a){ if(a) myabort(); }\nvoid g(void){myabort();}', abi, extra=["-I" + ROOT])
    k, _, _ = kinds(o, ["myabort"])
    check(T("myabort -> T2 (and noreturn tail for g)"), {("f", "T2"), ("g", "T2"), ("g", "T2-noreturn-tail")} <= k, k)
    check(T("no terminator set -> f not T2"), ("f", "T2") not in kinds(o)[0])
    o = build("#include <stdlib.h>\nvoid f(int a){ if(a) abort(); exit(a); }\nvoid g(void){atexit(0);}", abi)
    k, _, _ = kinds(o, libc)
    check(T("stdlib abort/exit -> T2, atexit not"), ("f", "T2") in k and ("g", "T2") not in k, k)
    o = build(".text\n.global a\n.type a,%function\na: udf #0\n.size a,.-a\n.global b\n.type b,%function\nb: hlt #1\n.size b,.-b\n"
              ".global c\n.type c,%function\nc: svc #0\nret\n.size c,.-c\n.global d\n.type d,%function\nd: nop\n.inst 0x00300000\nret\n.size d,.-d\n",
              abi, "s")
    k, _, f = kinds(o)
    check(T("asm udf/hlt/svc/undecodable"), {("a", "T1"), ("b", "T1"), ("c", "T1"), ("d", "undecodable")} <= k, k)
    k, _, _ = kinds(o, osl=[o])
    check(T("svc with --os-layer not flagged"), ("c", "T1") not in k and ("a", "T1") in k, k)
    o = build(".text\n.global f\n.type f,%function\nf: ldr x0, 1f\nret\n1: .quad 0xffffffffffffffff\n.size f,.-f\n", abi, "s")
    k, _, _ = kinds(o)
    check(T("literal pool under $d: no T finding, constant load is still V"), k == {("f", "V")}, k)
    # V class
    src = ("void v0(void){}\nint r0(void){return 0;}\nint r42(void){return 42;}\nstatic int cnt;\nint *ad(void){return &cnt;}\n"
           "long long big(void){return 0x123456789abcdefLL;}\n"
           "int dead(void){ int x=3; x*=7; return 5;}\nint inc(int a){return a+1;}\nint gl;\nint get(void){return gl;}\n"
           "void set(void){gl=1;}\nvoid ext(void);\nvoid call(void){ext();}\nint cond(int a){return a?1:2;}\nvoid fp(void);\n"
           "float one(void){return 1.0f;}\n")
    o = build(src, abi)
    k, _, f = kinds(o)
    vs = {n for n, kd in k if kd == "V"}
    check(T("V set"), vs == {"v0", "r0", "r42", "ad", "big", "dead", "one"}, (sorted(vs), [x[3] for x in f]))
    rows = {"v0": ("void", "", "r"), "r0": ("u32", "0", "r"), "r42": ("u32", "42", "r"), "ad": ("addr", "cnt", "r"),
            "big": ("u64", "0x123456789abcdef", "r"), "dead": ("u32", "5", "r")}
    k, _, f = kinds(o, rows=rows)
    check(T("matching rows certify (the float constant has no row kind and stays V)"), {n for n, kd in k if kd == "V"} == {"one"}
          and not any(kd == "constants-mismatch" for _, kd in k), (k, f))
    bad = dict(rows, r42=("u32", "43", "r"), v0=("u32", "0", "r"), inc=("u32", "1", "r"), ghost=("void", "", "r"))
    k, _, f = kinds(o, rows=bad)
    check(T("mismatch / non-V row / stale row -> constants-mismatch"),
          {("r42", "constants-mismatch"), ("v0", "constants-mismatch"), ("inc", "constants-mismatch"),
           ("ghost", "constants-mismatch")} <= k and ("r42", "V") not in k, k)
    o = build(".text\n.global lp\n.type lp,%function\nlp: mov w0,#0\nmov w9,#10\n1: sub w9,w9,#1\ncbnz w9,1b\nret\n.size lp,.-lp\n"
              ".global lq\n.type lq,%function\nlq: mov w0,#10\n1: sub w0,w0,#1\ncbnz w0,1b\nret\n.size lq,.-lq\n"
              ".global fr\n.type fr,%function\nfr: stp x29,x30,[sp,#-16]!\nmov x29,sp\nmov w0,#7\nldp x29,x30,[sp],#16\nret\n.size fr,.-fr\n"
              ".global st\n.type st,%function\nst: str w0,[sp,#8]\nret\n.size st,.-st\n", abi, "s")
    k, _, _ = kinds(o)
    check(T("loop / counter result / frame save V; store above frame and input read not V"),
          {n for n, kd in k if kd == "V"} == {"lp", "lq", "fr"}, k)
    k, _, f = kinds(o, rows={"fr": ("u32", "7", "r"), "lp": ("u32", "0", "r")})
    check(T("frame-shuffling constant function certified by its row; counter result stays red"),
          {n for n, kd in k if kd == "V"} == {"lq"} and not any(kd == "constants-mismatch" for _, kd in k), (k, f))
    pre = ".text\n.global %s\n.type %s,%%function\n%s:\n%s\n.size %s,.-%s\n"
    A = lambda n, body: pre % (n, n, n, body, n, n)
    o = build(A("mv", "movi v0.4s,#0\nret") + A("cs", "mov w9,#1\nmov w10,#2\ncmp w9,w10\ncsel w0,w9,w10,eq\nret")
              + A("sh", "sub sp,sp,#16\nmov w9,#3\nstr w9,[sp,#4]\nldrb w10,[sp,#4]\nmov w0,#9\nadd sp,sp,#16\nret")
              + A("x8", "str wzr,[x8]\nmov w0,#1\nret") + A("lit", "ldr w0,1f\nret\n1: .word 5")
              + A("br", "mov w9,#1\ncbz w9,1f\nmov w0,#4\nret\n1: add w0,w0,#1\nmov w0,#0\nret")
              + A("fm", "fmov s0,#1.0\nret") + A("cm", "cmp w0,#0\ncset w0,eq\nret") + A("sa", "ldr x0,[sp,#8]\nret")
              + A("fp", "ldr x0,[x29,#8]\nmov w0,#1\nret") + A("pr", "prfm pldl1keep,[x1]\nmov w0,#1\nret"), abi, "s")
    k, _, f = kinds(o, rows={"sh": ("u32", "9", "r")})
    V = {n for n, kd in k if kd == "V"}
    check(T("V: movi, csel, frame shuffle (certified), literal load, fmov imm"), V == {"mv", "cs", "lit", "fm"}, (V, k))
    check(T("not V: x8 store (ii/v), one-branch input use, cmp w0, caller-frame loads (iii)"),
          not ({"x8", "br", "cm", "sa", "fp", "pr"} & V) and ("sh", "V") not in k, k)
    o = build(".text\n.global sz\n.type sz,%function\nsz: ret\n", abi, "s")
    k, _, _ = kinds(o)
    check(T("STT_FUNC size 0 -> uncovered"), ("sz", "uncovered") in k, k)
    o = build("void a(void){}\n__attribute__((aligned(64))) void b(void){}\n", abi)
    k, items, _ = kinds(o)
    check(T("NOP padding between functions is not uncovered"), not any(kd == "uncovered" for _, kd in k), k)
    o = build(".text\n.global g1\n.type g1,%function\ng1: ret\n.size g1,.-g1\n.word 0x12345678\n.global g2\n.type g2,%function\ng2: ret\n.size g2,.-g2\n", abi, "s")
    k, _, _ = kinds(o)
    check(T("stray bytes between functions -> uncovered"), any(kd == "uncovered" for _, kd in k), k)
    # a64access (LLVM) vs objdump on a mixed object
    o = build(src, abi)
    dump = subprocess.run(["aarch64-linux-gnu-objdump", "-d", o], capture_output=True, text=True).stdout
    od = [int(m.group(1), 16) for m in re.finditer(r"^\s+([0-9a-f]+):\t[0-9a-f]{8} \t(?!\.word)", dump, re.M)]
    ob = objstub.Obj(o)
    si = next(i for i, s in enumerate(ob.secs) if s[0] == ".text")
    sec = ob.secs[si]
    cs = [a for a, i in sorted(objstub.decode(ob.data, [(sec[3], sec[3] + sec[4])]).items()) if i]
    cs = [a - sec[3] for a in cs]
    check(T("LLVM (a64access) decodes the same words as objdump"), od == cs and len(od) > 10, (len(od), len(cs)))

# determinism, CLI, gate wrapper, verdict acceptance
o1 = build("void f(void){__builtin_trap();}\nint r(int a){return a+1;}", "lp64")
cmd = [sys.executable, "-I", os.path.join(VERIFY, "objstub.py"), "check", "--terminators", tf, o1]
a, b = (subprocess.run(cmd, capture_output=True, text=True) for _ in range(2))
check("CLI: findings printed sorted, exit 1, deterministic",
      a.returncode == 1 and a.stdout == b.stdout and a.stdout.splitlines() == sorted(a.stdout.splitlines()) and "T1" in a.stdout, a.stdout)
clean = build("int g(int a){return a+1;}", "lp64")
c = subprocess.run(cmd[:-1] + [clean], capture_output=True, text=True)
check("CLI: clean object exit 0", c.returncode == 0 and not c.stdout, c.stdout)
sys.path.insert(0, VERIFY)
import verdict  # noqa: E402


def gate(objs, extra=()):
    out = tempfile.mkdtemp(dir=ROOT)
    lst = os.path.join(out, "list.txt")
    open(lst, "w").write("".join(x + "\n" for x in objs))
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_objstub.py"), "--objects-list", lst, "--terminators", tf]
                       + list(extra), capture_output=True, text=True, env=dict(os.environ, GATE_OUT=out))
    return p, out


p, out = gate([])
res = json.load(open(os.path.join(out, "objstub.json")))
check("gate: empty list green, universe 0",
      p.returncode == 0 and res["status"] == "green" and res["universe"] == 0 and "empty" in res["detail"], (p.stderr, res))
p, out = gate([clean])
st = verdict._gate_result({"name": "objstub", "result": "objstub.json"}, out, p.returncode)
check("gate: clean object accepted by verdict", st[0] == "green" and st[1] == 1 and st[2] == 1, (st, p.stderr))
p, out = gate([clean, o1])
st = verdict._gate_result({"name": "objstub", "result": "objstub.json"}, out, p.returncode)
res = json.load(open(os.path.join(out, "objstub.json")))
check("gate: trap object red, 1 of 3 uncertified listed", st[0] == "red" and res["universe"] == 3 and res["certified"] == 2
      and len(res["uncertified"]) == 1, (st, res))
p, out = gate([os.path.join(ROOT, "missing.o")])
check("gate: unreadable object is red", p.returncode != 0 and json.load(open(os.path.join(out, "objstub.json")))["status"] == "red")

print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
sys.exit(1 if FAILED else 0)
