#!/usr/bin/env python3
"""Tests for verify/objstub.py (gate objstub). Plain script: one PASS/FAIL line per case, exit 0 on pass.
Compiles with aarch64-linux-gnu-gcc in both ABIs (ilp32, lp64); scratch under tempfile.mkdtemp."""
import json
import os
import re
import struct
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


def kinds(objs, terms=(), rows=None, osl=(), facts=None):
    """Object ids are the paths; a row keyed by a bare symbol belongs to the first object (rows are keyed (unit, symbol))."""
    objs = objs if isinstance(objs, list) else [objs]
    rows = {(objs[0], k) if isinstance(k, str) else k: v for k, v in (rows or {}).items()}
    items, finds = objstub.run_check([(p, p) for p in objs], set(terms), rows, set(osl), facts)
    return {(n, k) for _, n, k, _ in finds}, items, finds


def row(sym, kind, val=""):
    return {sym: (kind, val, "ref")}


# terminator derivation: plugin on a tiny header and on the real libc headers
tiny = os.path.join(ROOT, "tiny.h")
open(tiny, "w").write("void myabort(void) __attribute__((noreturn));\nvoid fine(void);\n")
tf = os.path.join(ROOT, "terms.txt")
names = objstub.derive_terminators(CC, ["-I" + ROOT], ["tiny.h"], "c", tf)
check("terminators: tiny header (and the EH-resume builtin as an unwinder)", [n for n in names if n[0] != "~"] == ["myabort"] and "~_Unwind_Resume" in names, names)
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
# --- reach assertion, exit rows, (unit, symbol) keys, cache, parallel
REACH = objstub.REACH
rsrc = ("void %s(unsigned) __attribute__((noreturn));\nvoid f(int a){ if(a) %s(7); }\nvoid g(unsigned a){ %s(a); }\n"
        "void h(int a){ if(a>3) %s(0x10001); %s(9); }\nvoid (*ptr)(unsigned) = %s;\n" % ((REACH,) * 6))
o = build(rsrc, "lp64")
k, _, f = kinds(o, terms=[REACH, "abort"], facts=None)
check("reach: constant argument without a facts file -> reach-uncertified; no T2, no T2-noreturn-tail for the reach call",
      {("f", "reach-uncertified"), ("h", "reach-uncertified")} <= k and not any(kd in ("T2", "T2-noreturn-tail") for _, kd in k), (k, f))
check("reach: non-constant argument -> reach-nonconstant (also as a tail call)", ("g", "reach-nonconstant") in k, k)
check("reach: function pointer to the symbol -> reach-address-taken", any(kd == "reach-address-taken" for _, kd in k), k)
k, _, f = kinds(o, terms=[REACH], facts={7, 0x10001, 9})
check("reach: facts 7, 65537 (MOVZ+MOVK), 9 certify every call site", not {x for x in k if x[1] in ("reach-uncertified", "reach-stale")}, (k, f))
k, _, f = kinds(o, terms=[REACH], facts={7, 9, 5})
check("reach: unknown fact 65537 -> reach-uncertified, fact 5 without a call site -> reach-stale", ("h", "reach-uncertified") in k
      and ("fact:5", "reach-stale") in k and ("f", "reach-uncertified") not in k, (k, f))
o = build(".text\n.global c1\n.type c1,%function\nc1: mov w0,#3\ncbz w1,1f\nmov w0,#4\n1: bl " + REACH + "\n.size c1,.-c1\n"
          ".global c2\n.type c2,%function\nc2: stp x29,x30,[sp,#-16]!\nmov w0,#5\nbl ext\nbl " + REACH + "\nldp x29,x30,[sp],#16\nret\n.size c2,.-c2\n"
          ".global c3\n.type c3,%function\nc3: stp x29,x30,[sp,#-16]!\nmov w0,#5\nbl ext\nmov w0,#6\nbl " + REACH + "\nldp x29,x30,[sp],#16\nret\n.size c3,.-c3\n", "lp64", "s")
k, _, f = kinds(o, facts={4, 3, 5, 6})
nc = {(r[0], r[2]) for x in [objstub.analyse_all([(o, o)], set(), set())[o]] for r in x["reach"]}
check("reach: constant propagation: two paths with different w0 -> not constant; a call clobbers w0 but a later move fixes it",
      ("c1", None) in nc and ("c2", None) in nc and ("c3", 6) in nc, nc)
# exit tails
EX = (".text\n.global %s\n.type %s,%%function\n%s: adrp x1,slot\nldr x1,[x1,:lo12:slot]\nblr x1\n.size %s,.-%s\n" % ((REACH,) * 5)
      + ".global ex\n.type ex,%function\nex: ldr x1,[x0]\ncbz x0,1f\nmov w0,#1\n1: blr x1\n.size ex,.-ex\n"
      ".global ex2\n.type ex2,%function\nex2: cbz x0,1f\nret\n1: ldr x1,[x0]\nblr x1\n.size ex2,.-ex2\n"
      ".global ex3\n.type ex3,%function\nex3: ldr x1,[x0]\nblr x1\nret\n.size ex3,.-ex3\n"
      ".global ex4\n.type ex4,%function\nex4: ldr x1,[x0]\nblr x1\nnop\nb ex4\n.size ex4,.-ex4\n.data\nslot: .quad 0\n")
o = build(EX, "lp64", "s")
k, _, f = kinds(o, rows={"ex": ("exit", "", "ExitProcess")})
check("exit: REACH definition ending in BLR is certified by itself; a row of kind exit certifies ex; no row -> indirect-noreturn-tail",
      (REACH, "indirect-noreturn-tail") not in k and ("ex", "indirect-noreturn-tail") not in k and ("ex2", "indirect-noreturn-tail") in k
      and ("ex4", "indirect-noreturn-tail") not in k, (k, f))
k, _, f = kinds(o, rows={"ex": ("exit", "", "r"), "ex2": ("exit", "", "r"), "ex3": ("exit", "", "r"), "ghost": ("exit", "", "r")})
check("exit: a row on a function with a RET path (ex2) or code after the BLR (ex3, ex4 is the reverse) -> exit-mismatch, finding stays; ghost row too",
      {("ex2", "exit-mismatch"), ("ex2", "indirect-noreturn-tail"), ("ex3", "exit-mismatch"), ("ghost", "exit-mismatch")} <= k
      and ("ex", "exit-mismatch") not in k, k)
o2 = build(".text\n.global %s\n.type %s,%%function\n%s: mov w0,#0\nret\n.size %s,.-%s\n" % ((REACH,) * 5), "lp64", "s")
k, _, _ = kinds(o2)
check("exit: a definition of REACH that returns -> reach-not-exit", (REACH, "reach-not-exit") in k, k)
# (unit, symbol) keys
loc = lambda v: ".text\n.type h,%%function\nh: mov w0,#%d\nret\n.size h,.-h\n.global pub%d\n.type pub%d,%%function\npub%d: b h\n.size pub%d,.-pub%d\n" % (v, v, v, v, v, v)
oa, ob = build(loc(1), "lp64", "s"), build(loc(2), "lp64", "s")
rr = {(oa, "h"): ("u32", "1", "r"), (ob, "h"): ("u32", "2", "r")}
items, finds = objstub.run_check([(oa, oa), (ob, ob)], set(), rr, set())
check("rows are keyed (unit, symbol): two local `h` with different results are certified by their own rows", not [x for x in finds if x[1] == "h"], finds)
items, finds = objstub.run_check([(oa, oa), (ob, ob)], set(), {(oa, "h"): ("u32", "2", "r"), (ob, "h"): ("u32", "2", "r")}, set())
check("a row of one unit does not certify the other unit's function", [x[0] for x in finds if x[1] == "h" and x[2] == "constants-mismatch"] == [oa], finds)
# cache and parallel run
cdir = tempfile.mkdtemp(dir=ROOT)
many = [(p, p) for p in [build("int f%d(int a){return a+%d;}\nvoid t%d(void){__builtin_trap();}" % (i, i, i), "lp64") for i in range(6)]]
t0 = time.time()
r1 = objstub.run_check(many, set(), {}, set(), None, cdir, 3)
t1 = time.time() - t0
t0 = time.time()
r2 = objstub.run_check(many, set(), {}, set(), None, cdir, 3)
t2 = time.time() - t0
r3 = objstub.run_check(many, set(), {}, set(), None, None, 1)
n = len([x for x in os.listdir(cdir + "/objstub") if x.endswith(".json")])
check("cache: cold (3 processes) == cached == uncached serial; one json per object", r1 == r2 == r3 and n == 6, (n, r1[1][:2]))
print("INFO 6 objects cold %.2f s, cached %.2f s" % (t1, t2))
rk = objstub.run_check(many[:1], {"abort"}, {}, set(), None, cdir, 1)
check("cache key holds only the terminators the object mentions: another terminator set, same objects -> cache hit",
      len([x for x in os.listdir(cdir + "/objstub") if x.endswith(".json")]) == n and rk == objstub.run_check(many[:1], set(), {}, set(), None, None, 1), os.listdir(cdir + "/objstub"))
check("cache key changes with the os-layer flag and with a terminator the object references",
      objstub.object_key(many[0][1], {"abort"}, True)[0] != objstub.object_key(many[0][1], {"abort"}, False)[0])
oc = build("void abort(void);\nvoid q(void){abort();}", "lp64")
check("cache key: terminators & names", objstub.object_key(oc, {"abort"}, False)[0] != objstub.object_key(oc, set(), False)[0])
# --- C++ objects (both ABIs): COMDAT sections, landing pads ending in _Unwind_Resume, thunks, guards, startup/cold/hot sections
CXX = """
#include <exception>
#include <cstdlib>
struct B { virtual ~B(); virtual int f(); };
struct C { virtual void g(); int c; };
struct D : B, C { void g() override; int f() override; };
struct L { L(); ~L(); int v; };
int may_throw(int);
int use(int x) { L l; return may_throw(x) + l.v; }
int stat_local(int x) { static int s = may_throw(x); return s; }
template <class T> inline T twice(T x) { return x + x; }
int tw(int x) { return twice(x) + (int)twice(2.0); }
int B::f() { return 3; }
B::~B() {}
void D::g() { may_throw(1); }
int D::f() { return may_throw(2); }
int thrower(int x) { if (x) throw 5; return 0; }
void caller(B *b) { delete b; }
int hot(int x) __attribute__((hot));
int hot(int x) { return x + 1; }
int cold(int x) __attribute__((cold));
int cold(int x) { return may_throw(x) * 2; }
L glob;
void term(int x) { if (x) std::terminate(); }
void ab(int x) { if (x) abort(); }
"""
for abi in ("ilp32", "lp64"):
    T = lambda n: "%s/%s" % (abi, n)
    o = build(CXX, abi, "cc")
    tl = os.path.join(ROOT, "cxx-%s.txt" % abi)
    cterms = set(objstub.derive_terminators("aarch64-linux-gnu-g++", ["-mabi=" + abi], ["cstdlib", "exception", "new", "typeinfo", "cxxabi.h", "unwind.h"], "c++", tl))
    secs = subprocess.run(["aarch64-linux-gnu-readelf", "-SW", o], capture_output=True, text=True).stdout
    check(T("C++ unit has COMDAT groups, .text.startup, .text.unlikely, .text.hot"), all(x in secs for x in (".group", ".text.startup", ".text.unlikely", ".text.hot")), secs[:300])
    check(T("terminator rule: process end only (terminate, abort, exit); unwinding and throwing are unwinders (~), the EH-resume builtin is one"),
          {"_ZSt9terminatev", "abort", "exit"} <= cterms and "~_Unwind_Resume" in cterms and "~__cxa_throw" in cterms and "__cxa_throw" not in cterms
          and "_Unwind_Resume" not in cterms, sorted(cterms))
    items, finds = objstub.run_check([(o, o)], cterms, {}, set())
    bad = {(n, k) for _, n, k, _ in finds if k not in ("V",)}
    check(T("C++: cleanup landing pads (_Unwind_Resume tail), throw, guards, thunks, startup/cold/hot, virtual destructors: only the two process-end calls are findings"),
          bad == {(f, k) for f in ("_Z4termi", "_Z2abi") for k in ("T2", "T2-noreturn-tail")} and len(items) >= 15, (bad, len(items)))
    check(T("C++: no T2-noreturn-tail anywhere (use, stat_local end in _Unwind_Resume; thrower ends in __cxa_throw)"), not any(k == "T2-noreturn-tail" and n not in ("_Z4termi", "_Z2abi") for _, n, k, _ in finds), finds)
    check(T("C++: without the unwinder entries the same objects are findings (the rule is what accepts them)"),
          any(k == "T2-noreturn-tail" and n == "_Z3usei" for _, n, k, _ in objstub.run_check([(o, o)], {x for x in cterms if x[0] != "~"}, {}, set())[1]))
    cc = build('#include <cstdlib>\nextern "C" void fatal(void) __attribute__((noreturn));\nint f(int x) { if (x) fatal(); return 1; }', abi, "cc")
    check(T("C++: a user noreturn function that is not nothrow is an unwinder by the compiler's flags, so a call in the middle is no finding (stated limit)"),
          not any(k == "T2" for _, _, k, _ in objstub.run_check([(cc, cc)], {x for x in cterms}, {}, set())[1]))

# --- wine_fixme (the bodies that do what Wine 9.0's simplified implementation does)
WB = {30: ("m", "wine-9.0:a.c:p", "branch", "-"), 31: ("m", "wine-9.0:a.c:q", "branch", "-")}
WT = {5: ("IDirect3DDevice9::X", "wine-9.0:dlls/d3d9/device.c:foo", "u32", "7"), 6: ("IDirect3DDevice9::Y", "wine-9.0:dlls/d3d9/device.c:bar", "void", "")}
HDR = "void fnvnx_wine_fixme(unsigned);\n"
for abi in ("ilp32", "lp64"):
    T = lambda n: "%s/%s" % (abi, n)
    wk = lambda src, tbl=WT, extra=(): kinds_w(build(HDR + src, abi, extra=extra), tbl)
    def kinds_w(o, tbl):
        items, finds = objstub.run_check([(o, o)], set(), {}, set(), None, None, 1, tbl)
        return {(n, k) for _, n, k, _ in finds}, finds
    k, f = wk("int f(void) { fnvnx_wine_fixme(5); return 7; }")
    check(T("wine: log call with constant id 5 and the row's value: certified (no V, no call finding)"), not k, f)
    k, f = wk("int f(void) { fnvnx_wine_fixme(5); return 8; }")
    check(T("wine: result differs from the row: wine-fixme-mismatch"), k == {("f", "wine-fixme-mismatch")}, f)
    k, f = wk("int f(void) { fnvnx_wine_fixme(9); return 7; }")
    check(T("wine: id not in the table: wine-fixme-unknown-id"), k == {("f", "wine-fixme-unknown-id")}, f)
    k, f = wk("void f(unsigned a) { fnvnx_wine_fixme(a); }", extra=["-fno-optimize-sibling-calls"])
    check(T("wine: non-constant id: wine-fixme-nonconstant"), ("f", "wine-fixme-nonconstant") in k, f)
    k, f = wk("int f(void) { fnvnx_wine_fixme(5); fnvnx_wine_fixme(5); return 7; }")
    check(T("wine: two log calls on one path: not exactly one id: wine-fixme-mismatch"), k == {("f", "wine-fixme-mismatch")}, f)
    k, f = wk("int g;\nint f(void) { fnvnx_wine_fixme(5); g = 1; return 7; }")
    check(T("wine: log call in a function that also stores to a global: wine-fixme-in-real-body"), k == {("f", "wine-fixme-in-real-body")}, f)
    k, f = wk("void f(void) { fnvnx_wine_fixme(6); }", extra=["-fno-optimize-sibling-calls"])
    check(T("wine: void kind, log call only: certified"), not k, f)
    k, f = wk("int f(void) { fnvnx_wine_fixme(5); return 7; }", tbl={})
    check(T("wine: empty table (the tree's header-only wine_fixme.tsv): wine-fixme-unknown-id"), k == {("f", "wine-fixme-unknown-id")}, f)
    k, f = wk("void *h(void) { return (void *)fnvnx_wine_fixme; }")
    check(T("wine: address taken in code: wine-fixme-address-taken"), ("h", "wine-fixme-address-taken") in k, f)
    k, f = wk("void (*volatile ptr)(unsigned) = fnvnx_wine_fixme;")
    check(T("wine: address taken in data: wine-fixme-address-taken"), any(kk == "wine-fixme-address-taken" for _, kk in k), f)
    k, f = wk("int f(void) { fnvnx_wine_fixme(5); return fnvnx_wine_fixme_res(); }\n".replace("fnvnx_wine_fixme_res()", "ext()") .replace("int f", "int ext(void);\nint f"))
    check(T("wine: another call in the body is a real body"), k == {("f", "wine-fixme-in-real-body")}, f)
    WTB = {**WT, **WB}
    RB = "int g;\nint f(int a) { if (a) { fnvnx_wine_fixme(%s); } g = a; return g + 1; }"
    k, f = wk(RB % 30, WTB)
    check(T("wine branch: id in a real body: certified"), not k, f)
    k, f = wk("int g;\nint f(int a) { if (a) fnvnx_wine_fixme(30); else fnvnx_wine_fixme(31); g = a; return g + 1; }", WTB)
    check(T("wine branch: two branch ids in one real body: certified"), not k, f)
    k, f = wk("int f(void) { fnvnx_wine_fixme(30); return 1; }", WTB)
    check(T("wine branch: id in a vacuous body: wine-fixme-branch-in-vacuous-body"), k == {("f", "wine-fixme-branch-in-vacuous-body")}, f)
    k, f = wk("int g;\nint f(int a) { if (a) fnvnx_wine_fixme(30); else fnvnx_wine_fixme(5); g = a; return 7; }", WTB)
    check(T("wine branch: branch id with a fixed-result id: wine-fixme-branch-mixed"), k == {("f", "wine-fixme-branch-mixed")}, f)
    k, f = wk("int g;\nint f(int a) { if (a) { fnvnx_wine_fixme(33); } g = a; return g + 1; }", WTB)
    check(T("wine branch: unknown id in a real body stays a finding"), k == {("f", "wine-fixme-in-real-body")}, f)
    k, f = wk("int g;\nint f(void) { fnvnx_wine_fixme(5); g = 1; return 7; }", WTB)
    check(T("wine branch: non-branch id in a real body: wine-fixme-in-real-body"), k == {("f", "wine-fixme-in-real-body")}, f)
    items, finds = objstub.run_check([(build(HDR + "int f(void) { fnvnx_wine_fixme(5); return 7; }\nint g(void) { fnvnx_wine_fixme(5); return 7; }", abi),) * 2], set(), {}, set(), None, None, 1, WT)
    cnt = objstub.wine_counts(objstub.analyse_all([("x", build(HDR + "int f(void) { fnvnx_wine_fixme(5); return 7; }\nint g(void) { fnvnx_wine_fixme(5); return 7; }", abi))], set(), set()))
    check(T("wine: call sites per id are counted"), cnt == {5: 2}, cnt)
# u32nz<N>: `p ? 0 : E_INVALIDCALL` style bodies at every optimisation level
NZ = {2: ("IDirect3DDevice9::SetClipStatus", "wine-9.0:dlls/wined3d/device.c:wined3d_device_set_clip_status", "u32nz1", "0x8876086C,0")}
SWAP = {2: NZ[2][:3] + ("0,0x8876086C",)}
for abi in ("ilp32", "lp64"):
    for opt in ("-O0", "-O1", "-O2", "-Os"):
        T = lambda n: "%s%s/%s" % (abi, opt, n)
        def nzk(src, tbl):
            o_ = build(HDR + src, abi, extra=[opt])
            items, finds = objstub.run_check([(o_, o_)], set(), {}, set(), None, None, 1, tbl)
            return {(n, k) for _, n, k, _ in finds}, finds
        SRC = "unsigned f(void *self, void *p) { (void)self; fnvnx_wine_fixme(2); return p ? 0u : 0x8876086Cu; }"
        k, f = nzk(SRC, NZ)
        check(T("wine u32nz1: p ? 0 : 0x8876086C certified"), not k, f)
        k, f = nzk(SRC, SWAP)
        check(T("wine u32nz1: swapped row values: wine-fixme-mismatch"), k == {("f", "wine-fixme-mismatch")}, f)
        k, f = nzk("unsigned f(void *self, int *p) { (void)self; fnvnx_wine_fixme(2); return *p ? 0u : 0x8876086Cu; }", NZ)
        check(T("wine u32nz1: a body that dereferences p: wine-fixme-in-real-body"), k == {("f", "wine-fixme-in-real-body")}, f)
        k, f = nzk("unsigned f(void *self, unsigned long p) { (void)self; fnvnx_wine_fixme(2); return p + 1 ? 0u : 0x8876086Cu; }", NZ)
        check(T("wine u32nz1: arithmetic on p: not certified"), k and ("f", "wine-fixme-mismatch") in k or ("f", "wine-fixme-in-real-body") in k, f)

# kind addr: the function returns the address of a named object
for abi in ("ilp32", "lp64"):
    ob = build(HDR + "static int obj;\nint *f(void) { fnvnx_wine_fixme(7); return &obj; }\nint *h(void) { fnvnx_wine_fixme(8); return &obj; }", abi)
    items, finds = objstub.run_check([(ob, ob)], set(), {}, set(), None, None, 1, {7: ("m", "wine-9.0:a.c:f", "addr", "obj"), 8: ("m", "wine-9.0:a.c:g", "addr", "other")})
    check("%s/wine: kind addr: the symbol's address certifies, another symbol is wine-fixme-mismatch" % abi, {(n, k) for _, n, k, _ in finds} == {("h", "wine-fixme-mismatch")}, finds)
for abi in ("ilp32", "lp64"):
    ob = build('extern "C" void fnvnx_wine_fixme(unsigned);\n' + "static char g_mem[32] = {1};\nvoid *f() { fnvnx_wine_fixme(20); return g_mem + 8; }", abi, "cc")
    for val, ok in (("_ZL5g_mem+8", True), ("_ZL5g_mem+0x8", True), ("_ZL5g_mem+4", False), ("_ZL5g_mem", False)):
        items, finds = objstub.run_check([(ob, ob)], set(), {}, set(), None, None, 1, {20: ("m", "wine-9.0:a.c:f", "addr", val)})
        check("%s/wine: kind addr SYMBOL+OFFSET (local C++ static, section+addend resolved): row %s -> %s" % (abi, val, "certified" if ok else "mismatch"),
              not finds if ok else {(n, k) for _, n, k, _ in finds} == {("_Z1fv", "wine-fixme-mismatch")}, finds)
# two ids on two paths (assembled: the paths branch on a register)
ao = build(".text\n.global f\n.type f,%function\nf: stp x29,x30,[sp,-16]!\n cbz x0, 1f\n mov w0,5\n bl fnvnx_wine_fixme\n b 2f\n1: mov w0,6\n bl fnvnx_wine_fixme\n2: ldp x29,x30,[sp],16\n ret\n.size f,.-f\n", "ilp32", "s")
oo = objstub.Obj(ao)
tsec = next(i for i, x in enumerate(oo.secs) if x[0] == ".text")
words = {a: struct.unpack_from("<I", oo.data, oo.secs[tsec][3] + a)[0] for a in range(0, oo.secs[tsec][4], 4)}
bls = [a for a, w in words.items() if w >> 26 == 0b100101]
check("wine: two different ids on two paths: no single id (wine_path_id None); one id on both paths: that id",
      objstub.wine_path_id(words, 0, oo.secs[tsec][4], {bls[0]: 5, bls[1]: 6}) is None and objstub.wine_path_id(words, 0, oo.secs[tsec][4], {bls[0]: 5, bls[1]: 5}) == 5)
for bad in ("5\tx\twine-9.0:a.c:f\tu32", "5\tx\twine:a.c:f\tu32\t1", "5\tx\twine-9.0:a.c:f\tu64\tzz", "x\tx\twine-9.0:a.c:f\tvoid\t", "5\tx\twine-9.0:a.c:f\tvoid\t1", "5\tx\twine-9.0:a.c:f\tbranch\t", "5\tx\twine-9.0:a.c:f\tbranch\t0",
            "5\tx\twine-9.0:a.c:f\tvoid\t\n5\ty\twine-9.0:a.c:g\tvoid\t"):
    tf_ = os.path.join(ROOT, "w.tsv")
    open(tf_, "w").write(bad + "\n")
    try:
        objstub.read_wine_table(tf_)
        check("wine table: malformed row rejected: %r" % bad[:30], False)
    except ValueError:
        check("wine table: malformed row rejected: %r" % bad[:30], True)
open(tf_, "w").write("5\tm\twine-9.0:a.c:f\tbranch\t-\n6\tm\twine-9.0:a.c:g\tbranch\t-\n")
check("wine table: kind branch with value - parses, contract name may repeat", objstub.read_wine_table(tf_)[6][2:] == ("branch", "-"))
open(tf_, "w").write("# header\n5\tm\twine-9.0:dlls/x/y.c:f\tu32\t0x10\n")
check("wine table: header comment and a valid row", objstub.read_wine_table(tf_) == {5: ("m", "wine-9.0:dlls/x/y.c:f", "u32", "0x10")})
try:
    objstub.read_wine_table(os.path.join(ROOT, "nonexistent.tsv"))
    check("wine table: missing file is an error", False)
except ValueError:
    check("wine table: missing file is an error", True)

# --- declared machine (CLAUDE.md "The declared machine"): fnvnx_declared_machine(id) + declared_machine.tsv, the same rule as wine_fixme
DT = {31: ("RegOpenKeyExA", "registry", "win-doc:sysinfo/regopenkeyex:RegOpenKeyExA", "u32", "2"),
      32: ("connect", "no-network", "win-doc:winsock/connect:connect", "u32", "0xFFFFFFFF"),
      33: ("WSACleanup", "no-network", "wine-9.0:dlls/ws2_32/socket.c:WSACleanup", "void", "")}
DH = "void fnvnx_declared_machine(unsigned);\nvoid fnvnx_wine_fixme(unsigned);\n"
for abi in ("ilp32", "lp64"):
    T = lambda n: "%s/%s" % (abi, n)
    def dk(src, tbl=DT, extra=(), wt=WT):
        o = build(DH + src, abi, extra=extra)
        items, finds = objstub.run_check([(o, o)], set(), {}, set(), None, None, 1, wt, tbl)
        return {(n, k) for _, n, k, _ in finds}, finds
    k, f = dk("unsigned f(void) { return 2; }")
    check(T("declared: the bare constant body (a registry read of an undeclared key) is V today, no citation: flagged V"), k == {("f", "V")}, f)
    k, f = dk("unsigned f(void) { fnvnx_declared_machine(31); return 2; }")
    check(T("declared: citation id 31 and the row's value (ERROR_FILE_NOT_FOUND): certified"), not k, f)
    k, f = dk("unsigned f(void) { fnvnx_declared_machine(32); return 0xFFFFFFFFu; }")
    check(T("declared: no-network row, SOCKET_ERROR-like value: certified"), not k, f)
    k, f = dk("unsigned f(void) { fnvnx_declared_machine(32); return 0; }")
    check(T("declared: no-network row, the body returns 0 (success): declared-machine-mismatch"), k == {("f", "declared-machine-mismatch")}, f)
    k, f = dk("void f(void) { fnvnx_declared_machine(33); }", extra=["-fno-optimize-sibling-calls"])
    check(T("declared: void row (WSACleanup, Wine 9.0 source): certified"), not k, f)
    k, f = dk("unsigned f(void) { fnvnx_declared_machine(40); return 2; }")
    check(T("declared: id not in declared_machine.tsv: declared-machine-unknown-id"), k == {("f", "declared-machine-unknown-id")}, f)
    k, f = dk("unsigned f(void) { fnvnx_declared_machine(31); return 2; }", tbl={})
    check(T("declared: empty table: declared-machine-unknown-id"), k == {("f", "declared-machine-unknown-id")}, f)
    k, f = dk("void f(unsigned a) { fnvnx_declared_machine(a); }", extra=["-fno-optimize-sibling-calls"])
    check(T("declared: non-constant id: declared-machine-nonconstant"), ("f", "declared-machine-nonconstant") in k, f)
    k, f = dk("int g;\nunsigned f(void) { fnvnx_declared_machine(31); g = 1; return 2; }")
    check(T("declared: citation in a body that also stores to a global: declared-machine-in-real-body"), k == {("f", "declared-machine-in-real-body")}, f)
    k, f = dk("unsigned f(void) { fnvnx_declared_machine(31); fnvnx_declared_machine(31); return 2; }")
    check(T("declared: two citations on one path: declared-machine-mismatch"), k == {("f", "declared-machine-mismatch")}, f)
    k, f = dk("unsigned f(int a) { if (a) { fnvnx_declared_machine(31); } else { fnvnx_wine_fixme(31); } return 2; }")
    check(T("declared: declared and wine citation on different paths: no single table, declared-machine-mismatch"),
          ("f", "declared-machine-mismatch") in k or ("f", "wine-fixme-mismatch") in k, f)
    k, f = dk("unsigned f(void) { fnvnx_wine_fixme(31); return 2; }")
    check(T("declared: a wine citation does not read the declared table (id 31 only in declared_machine.tsv): wine-fixme-unknown-id"),
          k == {("f", "wine-fixme-unknown-id")}, f)
    k, f = dk("unsigned f(void) { fnvnx_wine_fixme(5); return 7; }")
    check(T("declared: the wine citation is unchanged next to it (row 5 of the wine table): certified"), not k, f)
    k, f = dk("void *h(void) { return (void *)fnvnx_declared_machine; }")
    check(T("declared: address taken in code: declared-machine-address-taken"), ("h", "declared-machine-address-taken") in k, f)
    k, f = dk("void (*volatile ptr)(unsigned) = fnvnx_declared_machine;")
    check(T("declared: address taken in data: declared-machine-address-taken"), any(kk == "declared-machine-address-taken" for _, kk in k), f)
    k, f = dk("extern void set_last_error(unsigned);\nint f(void) { set_last_error(10050); return -1; }")
    check(T("declared: a body that calls set_last_error and returns -1 is no V, no citation needed (not flagged today)"), not k, f)
    k, f = dk("int e[4];\nint f(void) { e[0] = 10050; return -1; }")
    check(T("declared: a body that stores the error code (time-zone / last-error store) is no V (not flagged today)"), not k, f)
    cnt = objstub.declared_counts(objstub.analyse_all([("x", build(DH + "unsigned f(void) { fnvnx_declared_machine(31); return 2; }\nunsigned g(void) { fnvnx_declared_machine(31); return 2; }", abi))], set(), set()))
    check(T("declared: call sites per id are counted"), cnt == {31: 2}, cnt)
    check(T("declared: a call of the wine citation is not counted as declared"), objstub.declared_counts(objstub.analyse_all([("x", build(DH + "unsigned f(void) { fnvnx_wine_fixme(5); return 7; }", abi))], set(), set())) == {})
GOODS = ("31\tRegOpenKeyExA\tregistry\twin-doc:sysinfo/regopenkeyex:RegOpenKeyExA\tu32\t2",
         "32\tconnect\tno-network\twine-9.0:dlls/ws2_32/socket.c:connect\tu32\t0xFFFFFFFF",
         "33\tGetTimeZoneInformation\ttime-zone\twin-doc:timezoneapi/gettimezoneinformation:GetTimeZoneInformation\tu32nz0\t0,1")
for bad in ("31\tx\tnetwork\twin-doc:a/b:f\tu32\t1", "31\tx\tno-network\thttp://example.com\tu32\t1", "31\tx\tno-network\twin-doc:a/b:f\tu32",
            "31\tx\tno-network\twin-doc:a/b:f\tvoid\t1", "x\tx\tregistry\twin-doc:a/b:f\tu32\t1", "31\tx\tregistry\twine:a.c:f\tu32\t1",
            "31\tx\tregistry\twin-doc:a/b:f\tu32\t1\n31\ty\tregistry\twin-doc:a/b:g\tu32\t1", "31\tx\tregistry\tu32\t1"):
    tf_ = os.path.join(ROOT, "d.tsv")
    open(tf_, "w").write(bad + "\n")
    try:
        objstub.read_declared_table(tf_)
        check("declared table: malformed row rejected: %r" % bad[:36], False)
    except ValueError:
        check("declared table: malformed row rejected: %r" % bad[:36], True)
open(tf_, "w").write("# header\n" + "\n".join(GOODS) + "\n")
got = objstub.read_declared_table(tf_)
check("declared table: header comment and three valid rows (win-doc and wine-9.0 sources, three facts)",
      set(got) == {31, 32, 33} and got[32][1:3] == ("no-network", "wine-9.0:dlls/ws2_32/socket.c:connect"), got)
try:
    objstub.read_declared_table(os.path.join(ROOT, "nonexistent.tsv"))
    check("declared table: missing file is an error", False)
except ValueError:
    check("declared table: missing file is an error", True)

# gate on the toy product root of test_recipe.py
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import verdict  # noqa: E402

RV = ("#include \"toy.h\"\nvoid fnvnx_reach_violation(unsigned) __attribute__((noreturn));\n"
      "int f_c(int x) { if (x > 3) fnvnx_reach_violation(42); return x + TOY; }\n")
OS = TR.FILES["toy/os.S"] + (".globl fnvnx_reach_violation\n.type fnvnx_reach_violation,%function\nfnvnx_reach_violation:\n adrp x1, hostslot\n"
                             " ldr x1, [x1, :lo12:hostslot]\n blr x1\n.size fnvnx_reach_violation,.-fnvnx_reach_violation\n.data\n.balign 8\n.globl hostslot\nhostslot: .quad 0\n")
CONST = "b.c\tf_b\tu32\t14\tref b\n"


def gate(repo, out, cache=None):
    rc, g, secs, txt = TR.gate(repo, "objstub", out, cache)
    return rc, g, secs, txt, verdict._gate_result({"name": "objstub", "result": "objstub.json"}, out, rc)


def tree_of(repo):
    return TR.git(repo, "rev-parse", "HEAD^{tree}").strip()


for what in ("empty",):
    empty = TR.make_repo("os-empty")
    TR.w(empty, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": TR.T + "toy", "class": "doc", "gates": []}]}))
    rc, g, _, txt, st = gate(empty, ROOT + "/g0")
    check("gate with no product root: universe 0, green, accepted by verdict", rc == 0 and g["universe"] == 0 and st[0] == "green", (g, txt))
GC = ROOT + "/gcache"
repo = TR.make_repo("os-toy", files={"toy/constants.tsv": CONST})
rc, g, secs, txt, st = gate(repo, ROOT + "/g1", GC)
check("gate on the toy root (os_layer, constants row, terminators from its own units): green 4/4, accepted by verdict",
      rc == 0 and g["universe"] == 4 == g["certified"] and st[0] == "green", (g, txt))
print("INFO toy gate cold (with build) %.1f s" % secs)
rc, g, secs, txt, st = gate(repo, ROOT + "/g2", GC)
print("INFO toy gate warm %.1f s" % secs)
check("gate again with the cache: same result", rc == 0 and g["universe"] == 4 == g["certified"], (g, txt))
repo2 = TR.make_repo("os-toy2", files={"toy/constants.tsv": ""})
rc, g, _, txt, st = gate(repo2, ROOT + "/g3", GC)
check("toy root without the constants row of f_b: red, names the function by unit path", rc == 2 and g["uncertified"] == [TR.ROOT + "/b.c:f_b"]
      and st[0] == "red", (g, txt))
repo3 = TR.make_repo("os-reach", files={"toy/constants.tsv": CONST, "toy/c.c": RV, "toy/os.S": OS})
rc, g, _, txt, st = gate(repo3, ROOT + "/g4", GC)
check("reach call in the toy root with no reach facts file: red reach-uncertified, REACH's own definition and the terminator exemption accepted",
      rc == 2 and g["uncertified"] == [TR.ROOT + "/c.c:f_c"] and "reach-uncertified 1" in g["detail"], (g, txt))
facts = "%s/reach/%s/reach.facts.tsv" % (GC, tree_of(repo3))
os.makedirs(os.path.dirname(facts))
open(facts, "w").write("42\t" + "a" * 64 + "\n")
rc, g, _, txt, st = gate(repo3, ROOT + "/g5", GC)
check("with the fact 42 in the facts file of the same tree: green", rc == 0 and g["certified"] == g["universe"], (g, txt))
open(facts, "w").write("42\t" + "a" * 64 + "\n43\t" + "b" * 64 + "\n")
rc, g, _, txt, st = gate(repo3, ROOT + "/g6", GC)
check("a fact without a call site: red reach-stale", rc == 2 and "reach-stale 1" in g["detail"], (g, txt))
repo4 = TR.make_repo("os-abort", rec=TR.mut(link=None), files={"toy/constants.tsv": CONST, "toy/c.c": '#include <stdlib.h>\nint f_c(int x) { if (x > 3) abort(); return x + 1; }\n'})
rc, g, _, txt, st = gate(repo4, ROOT + "/g8", GC)
check("terminators come from the headers the unit includes (stdlib.h via the unit's preprocessed text): abort -> T2 in f_c, nothing else red",
      rc == 2 and g["uncertified"] == [TR.ROOT + "/c.c:f_c"] and "T2 1" in g["detail"], (g, txt))
open(facts, "w").write("not a row\n")
rc, g, _, txt, st = gate(repo3, ROOT + "/g7", GC)
check("a malformed facts file: red, input error", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))

# wine_fixme and ILP32 at the gate level
WC = '#include "toy.h"\nvoid fnvnx_wine_fixme(unsigned);\nint f_c(int x) { (void)x; fnvnx_wine_fixme(5); return 7; }\n'
for tag, tbl, want in (("row matches", "5\tm\twine-9.0:dlls/x/y.c:f\tu32\t7\n", "green"), ("row value differs", "5\tm\twine-9.0:dlls/x/y.c:f\tu32\t8\n", "red"),
                       ("malformed table", "5\tm\tnot-a-ref\tu32\t7\n", "input")):
    wr = TR.make_repo("os-wine-" + tag.replace(" ", "-"), rec=TR.mut(link=None), files={"toy/constants.tsv": CONST, "toy/c.c": WC, "verify/wine_fixme.tsv": tbl})
    rc, g, _, txt, st = gate(wr, ROOT + "/gw" + tag.replace(" ", "-"), GC)
    check("gate, wine_fixme table, case %s: expected %s" % (tag, want), (rc == 0 and g["certified"] == g["universe"] and g["wine_fixme"] == {"5": 1} and st[0] == "green") if want == "green" else
          (rc == 2 and ("wine-fixme-mismatch 1" in g["detail"] if want == "red" else "input error" in g["detail"])), (g, txt))
r32 = TR.make_repo("os-toy32", rec=TR.abi_rec("ilp32"), files={"toy/constants.tsv": CONST})
t0 = time.time()
rc, g, secs, txt, st = gate(r32, ROOT + "/g32", GC)
check("ILP32 toy root (ELF32 objects, aarch64linux32 link): gate green 4/4, accepted by verdict", rc == 0 and g["universe"] == 4 == g["certified"] and st[0] == "green", (g, txt))
print("INFO ILP32 toy gate cold %.1f s" % secs)
rc, g, secs, txt, st = gate(r32, ROOT + "/g32b", GC)
print("INFO ILP32 toy gate warm %.1f s" % secs)
r32r = TR.make_repo("os-reach32", rec=TR.abi_rec("ilp32"), files={"toy/constants.tsv": CONST, "toy/c.c": RV, "toy/os.S": OS.replace("ldr x1, [x1, :lo12:hostslot]", "ldr w1, [x1, :lo12:hostslot]").replace("blr x1", "blr x1").replace(".quad 0", ".word 0")})
rc, g, _, txt, st = gate(r32r, ROOT + "/g32r", GC)
check("ILP32 reach call (P32 CALL26 relocation) is judged by the reach rule: reach-uncertified, nothing else red", rc == 2 and g["uncertified"] == [TR.ROOT + "/c.c:f_c"] and "reach-uncertified 1" in g["detail"], (g, txt))

# a host root: the terminator plugin is built against the plugin headers of the root's own (x86-64 distro) compiler, and sees its noreturn declarations
xt = tf + ".x86"
open(tf + ".x86.h", "w").write("void fnvnx_die(void) __attribute__((noreturn));\n")
xn = objstub.derive_terminators("x86_64-linux-gnu-gcc-13", ["-I" + os.path.dirname(tf)], [os.path.basename(tf) + ".x86.h"], "c", xt)
check("host root compiler x86_64-linux-gnu-gcc-13: the plugin builds against its own plugin headers and finds the noreturn function", "fnvnx_die" in xn, xn)
xh = objstub.plugin_so("x86_64-linux-gnu-gcc-13", tempfile.mkdtemp(prefix="objstub-x86-"))[1]
check("the plugin key depends on the root compiler: x86-64 distro gcc and the aarch64 compiler give different plugins",
      xh != objstub.plugin_so("aarch64-linux-gnu-g++", tempfile.mkdtemp(prefix="objstub-a64-"))[1])

print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
sys.exit(1 if FAILED else 0)
