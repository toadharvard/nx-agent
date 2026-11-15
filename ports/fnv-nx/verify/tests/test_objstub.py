#!/usr/bin/env python3
"""Tests for verify/objstub.py (gate objstub). Plain script: one PASS/FAIL line per case, exit 0 on pass.
Compiles with aarch64-linux-gnu-gcc in both ABIs (ilp32, lp64); scratch under tempfile.mkdtemp."""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import objstub  # noqa: E402
import scratch  # noqa: E402

scratch.install()

sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import recipe  # noqa: E402
import cas  # noqa: E402

CC = "aarch64-linux-gnu-gcc"
ROOT = tempfile.mkdtemp(prefix="objstub-")
FAILED = []
N = [0]
T0 = time.time()


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


PLUG = {}


def plug(cc):
    """The analysis plugin (recipe.ANALYSIS_PLUGIN, the recipe's hermetic plugin stage) for the compiler CC: the stage of a Job over a toy root whose compiler
    is CC's pair (the driver of the same target: <triple>-gcc-13 / -g++-13 or, for the x86-64 distribution, gcc-13 / g++-13)."""
    base = re.sub(r"-g\+\+(-13)?$|-gcc(-13)?$", "", cc)
    pair = ({"cc": "gcc-13", "cxx": "g++-13"} if base == "x86_64-linux-gnu" else {"cc": base + "-gcc-13", "cxx": base + "-g++-13"})
    key = pair["cc"]
    if key not in PLUG:
        rec = TR.mut()
        rec["compiler"] = pair
        repo = TR.make_repo("plug-" + key, rec=rec)
        store = os.path.join(ROOT, "pstore")
        j = recipe.Job(repo, TR.ROOT, store, 1, {})
        ws = tempfile.mkdtemp(prefix="objstub-ws-")
        t = j.analysis_plugin(ws)
        dest = os.path.join(ROOT, "plug-" + key, "objstub_plugin.so")   # GCC names a plugin by the base name of its file
        os.makedirs(os.path.dirname(dest))
        shutil.copyfile(cas._obj(store, cas.manifest(t, store)["entries"]["objstub_plugin.so"]), dest)
        PLUG[key] = dest
    return PLUG[key]


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


# liveness of the input registers is a union (OR) over the successors: a loop converges (a sum of the masks never did; ftbbox.c ran > 30 min)
import signal  # noqa: E402
LOOPS = "int f(int n, int m) { int s = 0; for (int i = 0; i < n; i++) for (int j = 0; j < m; j++) { if ((i ^ j) & 1) s += i * j; else s -= j; } return s; }\n"
signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError()))
signal.alarm(120)
try:
    kinds(build(LOOPS, "lp64"))
    kinds(build(LOOPS, "ilp32"))
    done = True
except TimeoutError:
    done = False
signal.alarm(0)
check("a function with nested loops is analysed (liveness converges, LP64 and ILP32)", done)

# terminator derivation: plugin on a tiny header and on the real libc headers
tiny = os.path.join(ROOT, "tiny.h")
open(tiny, "w").write("void myabort(void) __attribute__((noreturn));\nvoid fine(void);\n")
tf = os.path.join(ROOT, "terms.txt")
names = objstub.derive_terminators(CC, ["-I" + ROOT], ["tiny.h"], "c", tf, so=plug(CC))
check("terminators: tiny header (and the EH-resume builtin as an unwinder)", [n for n in names if n[0] != "~"] == ["myabort"] and "~_Unwind_Resume" in names, names)
libc = objstub.derive_terminators(CC, [], ["stdlib.h", "unistd.h", "assert.h", "signal.h"], "c", tf + ".libc", so=plug(CC))
check("terminators: libc set", all(x in libc for x in ("abort", "exit", "_Exit", "quick_exit")) and "atexit" not in libc
      and "raise" not in libc, libc)
cxx = objstub.derive_terminators("aarch64-linux-gnu-g++", [], ["cstdlib", "exception"], "c++", tf + ".cxx", so=plug("aarch64-linux-gnu-g++"))
cxx_sj = objstub.derive_terminators("aarch64-linux-gnu-g++", [], ["csetjmp", "cstdlib", "cxxabi.h"], "c++", tf + ".cxxsj", so=plug("aarch64-linux-gnu-g++"))
cxx_ne = objstub.derive_terminators("aarch64-linux-gnu-g++", ["-fno-exceptions"], ["csetjmp", "cstdlib", "cxxabi.h"], "c++", tf + ".cxxne", so=plug("aarch64-linux-gnu-g++"))
libc_sj = objstub.derive_terminators(CC, [], ["setjmp.h", "stdlib.h"], "c", tf + ".libcsj", so=plug(CC))
check("terminators: C++ set has mangled terminate", "_ZSt9terminatev" in cxx and "abort" in cxx, cxx)
check("terminators: longjmp family (<setjmp.h>) is a control transfer, an unwinder, in C and C++", all("~" + n in cxx_sj and n not in cxx_sj for n in ("longjmp", "siglongjmp", "_longjmp")) and "abort" in cxx_sj
      and all("~" + n in libc_sj and n not in libc_sj for n in ("longjmp", "siglongjmp")), (cxx_sj, libc_sj))
check("terminators: __cxa_throw is an unwinder when exceptions are enabled and a terminator under -fno-exceptions", "~__cxa_throw" in cxx_sj and "__cxa_throw" not in cxx_sj
      and "__cxa_throw" in cxx_ne and "~__cxa_throw" not in cxx_ne, (cxx_sj, cxx_ne))
os.makedirs(os.path.join(ROOT, "fake"), exist_ok=True)
open(os.path.join(ROOT, "fake", "setjmp.h"), "w").write("void fake_jmp(void) __attribute__((noreturn));\n")   # a same-named header that is NOT the pinned runtime's
fk_other = objstub.derive_terminators(CC, ["-I" + os.path.join(ROOT, "fake")], ["setjmp.h"], "c", tf + ".fk1", so=plug(CC))
fk_pinned = objstub.derive_terminators(CC, ["-I" + os.path.join(ROOT, "fake")], ["setjmp.h"], "c", tf + ".fk2", sjdirs=(os.path.join(ROOT, "fake"),), so=plug(CC))
check("terminators: a setjmp.h outside the pinned header directories is no unwinder source (its noreturn function is a terminator); inside a pinned directory it is",
      "fake_jmp" in fk_other and "~fake_jmp" not in fk_other and "~fake_jmp" in fk_pinned and "fake_jmp" not in fk_pinned, (fk_other, fk_pinned))
open(os.path.join(ROOT, "infer.h"), "w").write("#include <setjmp.h>\nextern jmp_buf jb;\nvoid helper(int e);\nvoid decl_nr(void) __attribute__((noreturn));\n")
open(os.path.join(ROOT, "infer.c"), "w").write('#include "infer.h"\njmp_buf jb;\nvoid helper(int e) { longjmp(jb, e); }\n')
so_inf = plug(CC)
subprocess.run([CC, "-O2", "-I" + ROOT, "-S", "-o", os.devnull, "-fplugin=" + so_inf, "-fplugin-arg-objstub_plugin-out=" + tf + ".inf", "-fplugin-arg-objstub_plugin-ret=" + tf + ".infr", os.path.join(ROOT, "infer.c")], check=True)
inf_names = objstub.read_list(tf + ".inf")
check("terminators: a function GCC infers to never return (ends in longjmp) is not declared noreturn and is no terminator; a declared one is", "decl_nr" in inf_names and "helper" not in inf_names, inf_names)
print("INFO libc set size %d, c++ set size %d" % (len(libc), len(cxx)))

for abi in ("ilp32", "lp64"):
    T = lambda n: "%s/%s" % (abi, n)
    o = build("void f(void){__builtin_trap();}", abi)
    k, _, _ = kinds(o)
    check(T("trap -> T1"), ("f", "T1") in k, k)
    o = build('#include "tiny.h"\nvoid f(int a){ if(a) myabort(); }\nvoid g(void){myabort();}', abi, extra=["-I" + ROOT])
    k, _, _ = kinds(o, ["myabort"])
    check(T("myabort: g (every path ends in it) -> T2 and noreturn tail; f (a returning path: a mixed body) is no T2"), {("g", "T2"), ("g", "T2-noreturn-tail")} <= k and ("f", "T2") not in k, k)
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
void term(int) { std::terminate(); }
void ab(int) { abort(); }
void always_throw() { throw 5; }
"""
for abi in ("ilp32", "lp64"):
    T = lambda n: "%s/%s" % (abi, n)
    o = build(CXX, abi, "cc")
    tl = os.path.join(ROOT, "cxx-%s.txt" % abi)
    cterms = set(objstub.derive_terminators("aarch64-linux-gnu-g++", ["-mabi=" + abi], ["cstdlib", "exception", "new", "typeinfo", "cxxabi.h", "unwind.h"], "c++", tl, so=plug("aarch64-linux-gnu-g++")))
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
          any(k == "T2-noreturn-tail" and n == "_Z12always_throwv" for _, n, k, _ in objstub.run_check([(o, o)], {x for x in cterms if x[0] != "~"}, {}, set())[1]))
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
# tail B of the log function = BL then RET (the compiler's sibling call); several names at one address are one body
def asmf(names, body):
    return ".text\n" + "".join(".global %s\n.type %s,%%function\n" % (n, n) for n in names) + "".join("%s:\n" % n for n in names) + body + \
        "".join(".size %s,.-%s\n" % (n, names[0]) for n in names)
for abi in ("ilp32", "lp64"):
    T = lambda n: "%s/%s" % (abi, n)
    def wa(names, body, tbl=WT, rows=None):
        o = build(asmf(names, body), abi, "s")
        items, finds = objstub.run_check([(o, o)], set(), {(o, k): v for k, v in (rows or {}).items()}, set(), None, None, 1, tbl)
        return {(n, k) for _, n, k, _ in finds}, finds
    k, f = wk("void f(void) { fnvnx_wine_fixme(6); }")
    check(T("wine tail: compiler-made `b fnvnx_wine_fixme` (void row): certified like bl + ret"), not k, f)
    k, f = wa(["f"], " mov w0, 6\n b fnvnx_wine_fixme\n")
    check(T("wine tail: assembled `mov w0,6; b fnvnx_wine_fixme`: certified"), not k, f)
    k, f = wa(["f"], " mov w0, 9\n b fnvnx_wine_fixme\n")
    check(T("wine tail: id not in the table: wine-fixme-unknown-id"), k == {("f", "wine-fixme-unknown-id")}, f)
    k, f = wa(["f"], " mov w0, 5\n b fnvnx_wine_fixme\n")
    check(T("wine tail: a u32 row needs the result the tail call leaves in x0 (the callee's): wine-fixme-mismatch"), k == {("f", "wine-fixme-mismatch")}, f)
    k, f = wa(["f"], " cbz w1, 1f\n mov w0, 6\n b fnvnx_wine_fixme\n1: mov w0, 5\n b fnvnx_wine_fixme\n")
    check(T("wine tail: two ids on two paths: wine-fixme-mismatch"), k == {("f", "wine-fixme-mismatch")}, f)
    k, f = wa(["f"], " mov w0, 6\n b fnvnx_other\n")
    check(T("wine tail: a tail b of a function that is not cited is no wine body (a real body, no wine-* finding), as before"), not {x for x in k if x[1].startswith("wine")}, f)
    k, f = wa(["f"], " adrp x1, g\n mov w2, 1\n str w2, [x1, :lo12:g]\n mov w0, 6\n b fnvnx_wine_fixme\n.bss\ng: .word 0\n.text\n")
    check(T("wine tail: a store before the tail call: wine-fixme-in-real-body"), k == {("f", "wine-fixme-in-real-body")}, f)
    k, f = wa(["f", "g"], " mov w0, 6\n b fnvnx_wine_fixme\n")
    check(T("alias pair (ICF) of a tail-b wine body, one id: both names certified"), not k, f)
    k, f = wa(["f", "g"], " stp x29, x30, [sp, -16]!\n mov w0, 6\n bl fnvnx_wine_fixme\n ldp x29, x30, [sp], 16\n ret\n")
    check(T("alias pair of a bl wine body: both names certified"), not k, f)
    k, f = wa(["f", "g"], " mov w0, 9\n b fnvnx_wine_fixme\n")
    check(T("alias pair with an id that is not a row: both names wine-fixme-unknown-id"), k == {("f", "wine-fixme-unknown-id"), ("g", "wine-fixme-unknown-id")}, f)
    k, f = wa(["f", "g"], " mov w0, 3\n ret\n", rows={"f": ("u32", "3", "r"), "g": ("u32", "3", "r")})
    check(T("alias pair of a V body, a constants row for each name: both certified"), not k, f)
    k, f = wa(["f", "g"], " mov w0, 3\n ret\n", rows={"f": ("u32", "3", "r")})
    check(T("alias pair of a V body, a row for one name only: the other name is red (V, no constants row)"), k == {("g", "V")}, f)
    k, f = wa(["f", "g"], " stp x29, x30, [sp, -16]!\n mov w0, w1\n bl fnvnx_wine_fixme\n ldp x29, x30, [sp], 16\n ret\n")
    check(T("alias pair, a log call whose id is not a constant: the finding is on both names"), {("f", "wine-fixme-nonconstant"), ("g", "wine-fixme-nonconstant")} <= k, f)
    o = build(asmf(["f", "g"], " mov w0, 6\n b fnvnx_wine_fixme\n"), abi, "s")
    cnt = objstub.wine_counts(objstub.analyse_all([("x", o)], set(), set()))
    check(T("alias pair: the log call is counted once (one body)"), cnt == {6: 1}, cnt)

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

# --- declared machine (CLAUDE.md "The declared machine") and terminators: rule C, the FNVNX_CITE records of hostabi/include/fnvnx/cite.h
DT = {31: ("f", "registry", "win-doc:sysinfo/regopenkeyex:RegOpenKeyExA", "u32", "2", "-"),
      32: ("conn", "no-network", "win-doc:winsock/connect:connect", "u32", "0xFFFFFFFF", "-"),
      33: ("cleanup", "no-network", "wine-9.0:dlls/ws2_32/socket.c:WSACleanup", "void", "", "-"),
      34: ("die", "process-end", "win-doc:processthreadsapi/exitprocess:ExitProcess", "noreturn", "-", "-"),
      35: ("tend", "thread-end", "std:POSIX.1-2017:pthread_exit", "noreturn", "-", "-"),
      36: ("mixed3", "single-process", "win-doc:processthreadsapi/terminateprocess:TerminateProcess", "u32", "0", "ERROR_INVALID_HANDLE=6")}
CH = ('#define FNVNX_CITE(fn, id) __asm__(".pushsection .fnvnx.cite,\\"\\",%progbits\\n\\t.balign 4\\n\\t.4byte " #fn "\\n\\t.4byte 2f-1f\\n1:\\t.ascii \\"" id "\\"\\n2:\\t.balign 4\\n\\t.popsection")\n'
      "void fnvnx_wine_fixme(unsigned);\n")
for abi in ("ilp32", "lp64"):
    T = lambda n: "%s/%s" % (abi, n)
    def dk(src, tbl=DT, extra=(), wt=WT, terms=(), cited=True):
        o = build(CH + src, abi, extra=extra)
        items, finds = objstub.run_check([(o, o)], set(terms), {}, set(), None, None, 1, wt, tbl)
        if cited:   # the rows no record cites are a finding of their own: only the findings of the cases' own functions are compared
            finds = [f for f in finds if f[2] != "declared-machine-uncited"]
        return {(n, k) for _, n, k, _ in finds}, finds
    k, f = dk("unsigned f(void) { return 2; }")
    check(T("rule C: the bare constant body (a registry read of an undeclared key) with no record is V: flagged V"), k == {("f", "V")}, f)
    k, f = dk('unsigned f(void) { return 2; }\nFNVNX_CITE(f, "dm:31");')
    check(T("rule C: record dm:31 and the row's value (ERROR_FILE_NOT_FOUND): certified"), not k, f)
    k, f = dk('unsigned conn(void) { return 0xFFFFFFFFu; }\nFNVNX_CITE(conn, "dm:32");')
    check(T("rule C: no-network row, SOCKET_ERROR-like value: certified"), not k, f)
    k, f = dk('unsigned conn(void) { return 0; }\nFNVNX_CITE(conn, "dm:32");')
    check(T("rule C: no-network row, the body returns 0 (success): declared-machine-mismatch"), k == {("conn", "declared-machine-mismatch")}, f)
    k, f = dk('void cleanup(void) { }\nFNVNX_CITE(cleanup, "dm:33");')
    check(T("rule C: void row (WSACleanup, Wine 9.0 source): certified"), not k, f)
    k, f = dk('unsigned f(void) { return 2; }\nFNVNX_CITE(f, "dm:40");')
    check(T("rule C: id not in declared_machine.tsv: declared-machine-unknown-id"), k == {("f", "declared-machine-unknown-id")}, f)
    k, f = dk('unsigned f(void) { return 2; }\nFNVNX_CITE(f, "dm:31");', tbl={})
    check(T("rule C: empty table: declared-machine-unknown-id"), k == {("f", "declared-machine-unknown-id")}, f)
    k, f = dk('int g;\nunsigned f(void) { g = 1; return 2; }\nFNVNX_CITE(f, "dm:31");')
    check(T("rule C: a cited body that also stores to a global: declared-machine-in-real-body"), k == {("f", "declared-machine-in-real-body")}, f)
    k, f = dk('unsigned f(void) { return 2; }\nFNVNX_CITE(f, "dm:31");\nFNVNX_CITE(f, "dm:31");')
    check(T("rule C: two records for one body: cite-mismatch"), k == {("f", "cite-mismatch")}, f)
    k, f = dk('unsigned g(void) { return 2; }\nFNVNX_CITE(g, "dm:31");')
    check(T("rule C: the record names function g, the row 31 is for the contract name f: declared-machine-name-mismatch"), k == {("g", "declared-machine-name-mismatch")}, f)
    k, f = dk('unsigned f(void) { return 2; }\nFNVNX_CITE(f, "dm:31");', cited=False)
    check(T("rule C: a row no record cites is red (dm:32..36 here), the cited row is not"), {n for n, kk in k if kk == "declared-machine-uncited"} == {"dm:%d" % i for i in (32, 33, 34, 35, 36)}, f)
    k, f = dk('unsigned IDirect3DDevice9_X(void) { return 7; }\nFNVNX_CITE(IDirect3DDevice9_X, "wf:5");', wt={5: ("IDirect3DDevice9_X",) + WT[5][1:]})
    check(T("rule C: a wf: record is judged against wine_fixme.tsv: certified"), not k, f)
    k, f = dk('unsigned IDirect3DDevice9_X(void) { return 8; }\nFNVNX_CITE(IDirect3DDevice9_X, "wf:5");', wt={5: ("IDirect3DDevice9_X",) + WT[5][1:]})
    check(T("rule C: a wf: record with a different value: wine-fixme-mismatch"), k == {("IDirect3DDevice9_X", "wine-fixme-mismatch")}, f)
    for what, rec in (("an id of no table", 'FNVNX_CITE(f, "xx:3");'), ("a non-decimal id", 'FNVNX_CITE(f, "dm:x");'), ("an empty id", 'FNVNX_CITE(f, "");'),
                      ("a function that is not defined in the object", "extern unsigned ext(void);\nFNVNX_CITE(ext, \"dm:31\");")):
        k, f = dk("unsigned f(void) { return 2; }\n" + rec)
        check(T("rule C: a record with " + what + ": cite-malformed"), any(kk == "cite-malformed" for _, kk in k), f)
    k, f = dk('unsigned f(void) { return 2; }\nFNVNX_CITE(f, "dm:31");\nunsigned h(void) { return 5; }')
    check(T("rule C: the uncited constant body next to a cited one is still V (the record is per body)"), k == {("h", "V")}, f)
    cnt = objstub.declared_counts(objstub.analyse_all([("x", build(CH + 'unsigned f(void) { return 2; }\nFNVNX_CITE(f, "dm:31");', abi))], set(), set()))
    check(T("rule C: records per id are counted"), cnt == {31: 1}, cnt)
    # terminators
    NR = "extern void abort(void) __attribute__((noreturn));\nextern void (*volatile host_exit)(void);\n"
    k, f = dk(NR + 'void die(void) __attribute__((noreturn));\nvoid die(void) { host_exit(); __builtin_unreachable(); }\nFNVNX_CITE(die, "dm:34");', terms=("die", "abort"))
    check(T("terminator: a declared-noreturn body that ends in the host exit slot and is cited process-end: certified"), not k, f)
    k, f = dk(NR + 'void die(void) __attribute__((noreturn));\nvoid die(void) { host_exit(); __builtin_unreachable(); }', terms=("die", "abort"))
    check(T("terminator: the same body with no record: indirect-noreturn-tail and noreturn-uncited"), {("die", "indirect-noreturn-tail"), ("die", "noreturn-uncited")} <= k, f)
    k, f = dk(NR + 'void tend(void);\nvoid tend(void) { host_exit(); __builtin_unreachable(); }\nFNVNX_CITE(tend, "dm:35");', terms=("abort",))
    check(T("terminator: a cited process-end/thread-end body that is not declared noreturn: cite-not-declared-noreturn"), ("tend", "cite-not-declared-noreturn") in k, f)
    k, f = dk(NR + 'void caller(void) { abort(); }', terms=("abort",))
    check(T("terminator: a body whose every path ends in a library terminator is T2"), any(kk in ("T2", "T2-noreturn-tail") for n, kk in k if n == "caller"), f)
    k, f = dk(NR + 'int mixed(int x) { if (x) abort(); return x + 1; }', terms=("abort",))
    check(T("terminator: a body with a returning path is no T2 (a real control path)"), not k, f)
    k, f = dk(NR + 'int mixed2(void) { volatile int a = 0; if (a) abort(); return 3; }', terms=("abort",))
    check(T("terminator: a mixed body whose returning paths are an uncited constant is red (V, no row)"), ("mixed2", "constants-mismatch") in k or ("mixed2", "V") in k, f)
    k, f = dk(NR + 'int mixed3(void) { volatile int a = 0; if (a) abort(); return 0; }\nFNVNX_CITE(mixed3, "dm:36");', terms=("abort",))
    check(T("terminator: the same mixed body cited by the row 36 (u32 0): clean"), not k, f)
    k, f = dk(NR + 'void die2(void) __attribute__((noreturn));\nvoid die2(void) { abort(); }', terms=("die2", "abort"))
    check(T("coverage: a declared-noreturn definition that ends in a library terminator on every path needs no record"), not k, f)
    k, f = dk(NR + 'void jmp(void) __attribute__((noreturn));\nvoid jmp_user(void) __attribute__((noreturn));\nvoid jmp_user(void) { jmp(); }', terms=("jmp_user", "~jmp"))
    check(T("coverage: a declared-noreturn definition that ends in an unwinder (longjmp, throw) on every path is a control transfer, covered, no record"), not k, f)
    k, f = dk(NR + 'void die(void) __attribute__((noreturn));\nvoid die(void) { host_exit(); __builtin_unreachable(); }\nvoid die3(void) __attribute__((noreturn));\nvoid die3(void) { die(); }', terms=("die", "die3", "abort"))
    check(T("coverage: a declared-noreturn body that ends in an uncited defined terminator is uncovered too"), ("die3", "noreturn-uncited") in k, f)
STD_FIX = ({"7.22.4.4", "7.22.4.1"}, {"pthread_exit"})   # the pinned std index, as the loaders return it (std_index_load is tested below on fixture trees)
WIN_FIX = {"ERROR_INVALID_HANDLE": 6, "WSAHOST_NOT_FOUND": 11001}   # the pinned winerror.h/winsock2.h, as winerror_load returns it
rdt = lambda path, std=STD_FIX, win=WIN_FIX: objstub.read_declared_table(path, (lambda: std) if std is not None else None, (lambda: win) if win is not None else None)
HDR_REAL = ('# fact: no-network | time-zone | registry | no-galaxy | single-process | process-end | thread-end (CLAUDE.md "The declared machine"); source: win-doc:<documentation page or header path>:<function> | '
            'wine-9.0:<path>:<function> | std:<doc>:<section> | galaxy-sdk:<page>:<function>; kind void|u32|u64|addr|u32nz<N>|noreturn')
GOODS = ("31\tRegOpenKeyExA\tregistry\twin-doc:sysinfo/regopenkeyex:RegOpenKeyExA\tu32\t2\t-",
         "32\tconnect\tno-network\twine-9.0:dlls/ws2_32/socket.c:connect\tu32\t0xFFFFFFFF\t-",
         "33\tGetTimeZoneInformation\ttime-zone\twin-doc:timezoneapi/gettimezoneinformation:GetTimeZoneInformation\tu32nz0\t0,1\t-")
for bad in ("31\tx\tnetwork\twin-doc:a/b:f\tu32\t1\t-", "31\tx\tno-network\thttp://example.com\tu32\t1\t-", "31\tx\tno-network\twin-doc:a/b:f\tu32\t-",
            "31\tx\tno-network\twin-doc:a/b:f\tvoid\t1\t-", "x\tx\tregistry\twin-doc:a/b:f\tu32\t1\t-", "31\tx\tregistry\twine:a.c:f\tu32\t1\t-",
            "31\tx\tregistry\twin-doc:a/b:f\tu32\t1\t-\n31\ty\tregistry\twin-doc:a/b:g\tu32\t1\t-", "31\tx\tregistry\tu32\t1\t-"):
    tf_ = os.path.join(ROOT, "d.tsv")
    open(tf_, "w").write(HDR_REAL + "\n" + bad + "\n")
    try:
        rdt(tf_)
        check("declared table: malformed row rejected: %r" % bad[:36], False)
    except ValueError:
        check("declared table: malformed row rejected: %r" % bad[:36], True)
open(tf_, "w").write(HDR_REAL + "\n" + "\n".join(GOODS) + "\n")
got = rdt(tf_)
check("declared table: header comment and three valid rows (win-doc and wine-9.0 sources, three facts)",
      set(got) == {31, 32, 33} and got[32][1:3] == ("no-network", "wine-9.0:dlls/ws2_32/socket.c:connect"), got)
# the facts and sources come from the header line of the table itself
GAL = "34\tGalaxyInit\tno-galaxy\tgalaxy-sdk:IGalaxy/Init:Init\tu32\t1\t-"
open(tf_, "w").write(HDR_REAL + "\n" + GAL + "\n")
check("declared table: a galaxy-sdk row with fact no-galaxy is accepted", rdt(tf_).get(34, ("",))[1:3] == ("no-galaxy", "galaxy-sdk:IGalaxy/Init:Init"), "")
for what, row in (("an undeclared fact", "35\tx\tno-wifi\twin-doc:a/b:f\tu32\t1\t-"), ("an undeclared source prefix", "35\tx\tno-galaxy\tgog-sdk:a/b:f\tu32\t1\t-")):
    open(tf_, "w").write(HDR_REAL + "\n" + row + "\n")
    try:
        rdt(tf_)
        check("declared table: a row with " + what + " is red", False)
    except ValueError:
        check("declared table: a row with " + what + " is red", True)
for what, row, good in (("c11 clause digits and dots", "36\texit\tprocess-end\tstd:C11:7.22.4.4\tnoreturn\t-\t-", True),
                        ("c11 clause that is not digits and dots", "36\texit\tprocess-end\tstd:C11:7.x\tnoreturn\t-\t-", False),
                        ("posix-2017 page name", "37\tpthread_exit\tthread-end\tstd:POSIX.1-2017:pthread_exit\tnoreturn\t-\t-", True),
                        ("posix-2017 page that is not a name", "37\tpthread_exit\tthread-end\tstd:POSIX.1-2017:p/q\tnoreturn\t-\t-", False),
                        ("process-end with a kind that returns", "38\texit\tprocess-end\tstd:C11:7.22.4.4\tu32\t0\t-", False),
                        ("noreturn with a fact that is not a terminator", "38\texit\tregistry\tstd:C11:7.22.4.4\tnoreturn\t-\t-", False),
                        ("noreturn with a value", "38\texit\tprocess-end\tstd:C11:7.22.4.4\tnoreturn\t0\t-", False),
                        ("sources joined by | (sorted, unique, each token valid)", "39\tx\tregistry\twin-doc:a/b:f|wine-9.0:dlls/a.c:f\tu32\t1\t-", True),
                        ("sources joined by | with a token that fails the grammar", "39\tx\tregistry\twin-doc:a/b:f|http://x\tu32\t1\t-", False),
                        ("sources joined by | not in sorted order", "39\tx\tregistry\twine-9.0:dlls/a.c:f|win-doc:a/b:f\tu32\t1\t-", False),
                        ("sources joined by | with a repeated token", "39\tx\tregistry\twin-doc:a/b:f|win-doc:a/b:f\tu32\t1\t-", False)):
    open(tf_, "w").write(HDR_REAL + "\n" + row + "\n")
    try:
        rdt(tf_)
        got_ = True
    except ValueError:
        got_ = False
    check("declared table: " + what + (" is accepted" if good else " is red"), got_ == good, row)
HDR_HOST = HDR_REAL.replace(" | galaxy-sdk:<page>:<function>", " | galaxy-sdk:<page>:<function> | hostabi:<header>:<service>")
for what, row, good in (("hostabi source for a noreturn property of our own symbol", "40\tfnvnx_thread_exit\tthread-end\thostabi:hostabi/include/fnvnx/hostapi.h:thread_exit\tnoreturn\t-\t-", True),
                        ("hostabi source as the source of a Win32 value (a return value)", "41\tx\tno-network\thostabi:hostabi/include/fnvnx/hostapi.h:connect\tu32\t0xFFFFFFFF\t-", False),
                        ("hostabi joined to a wine source for a Win32 value", "41\tx\tno-network\thostabi:a.h:s|wine-9.0:dlls/a.c:f\tu32\t1\t-", False)):
    open(tf_, "w").write(HDR_HOST + "\n" + row + "\n")
    try:
        rdt(tf_)
        got_ = True
    except ValueError:
        got_ = False
    check("declared table: " + what + (" is accepted" if good else " is red"), got_ == good, row)
slim = HDR_REAL.replace(" | no-galaxy", "").replace(" | galaxy-sdk:<page>:<function>", "")
open(tf_, "w").write(slim + "\n" + GAL + "\n")
try:
    rdt(tf_)
    check("declared table: without no-galaxy in its header a galaxy row is red (table and check agree)", False)
except ValueError:
    check("declared table: without no-galaxy in its header a galaxy row is red (table and check agree)", True)
open(tf_, "w").write("# header\n" + "\n".join(GOODS) + "\n")
try:
    rdt(tf_)
    check("declared table: no header line is an error", False)
except ValueError:
    check("declared table: no header line is an error", True)
try:
    objstub.read_declared_table(os.path.join(ROOT, "nonexistent.tsv"))
    check("declared table: missing file is an error", False)
except ValueError:
    check("declared table: missing file is an error", True)

# lasterror (7th column), the std: citations against the pinned index, and the pinned loaders
for what, row, good in (("lasterror ERROR_INVALID_HANDLE=6 (a define of the pinned header)", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\tERROR_INVALID_HANDLE=6", True),
                        ("lasterror WSAHOST_NOT_FOUND=11001 (winsock2.h)", "50\tx\tno-network\twin-doc:a/b:f\tu32\t0xFFFFFFFF\tWSAHOST_NOT_FOUND=11001", True),
                        ("lasterror - on a returning row", "50\tx\tno-network\twin-doc:a/b:f\tu32\t0\t-", True),
                        ("lasterror with a lower-case name", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\terror_invalid_handle=6", False),
                        ("lasterror with a malformed name (starts with a digit)", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\t6ERROR=6", False),
                        ("lasterror with a name and no =N", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\tERROR_INVALID_HANDLE", False),
                        ("lasterror with = and no number", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\tERROR_INVALID_HANDLE=", False),
                        ("lasterror with a hex number", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\tERROR_INVALID_HANDLE=0x6", False),
                        ("lasterror with a number that is not the header's value", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\tERROR_INVALID_HANDLE=7", False),
                        ("lasterror with a name that is no define of the pinned headers", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\tERROR_NO_SUCH_THING=6", False),
                        ("a noreturn row with a non-- lasterror", "50\texit\tprocess-end\tstd:C11:7.22.4.4\tnoreturn\t-\tERROR_INVALID_HANDLE=6", False),
                        ("a row with six fields (no lasterror column)", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0", False),
                        ("a row with eight fields", "50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\t-\t-", False),
                        ("a hostabi: source with a non-- lasterror", "50\tx\tsingle-process\thostabi:a.h:s\tu32\t0\tERROR_INVALID_HANDLE=6", False),
                        ("a hostabi: noreturn row with lasterror -", "50\tfnvnx_thread_exit\tthread-end\thostabi:hostabi/include/fnvnx/hostapi.h:thread_exit\tnoreturn\t-\t-", True),
                        ("std:C11 clause that is in the pinned index", "50\texit\tprocess-end\tstd:C11:7.22.4.1\tnoreturn\t-\t-", True),
                        ("std:C11 clause that is not in the pinned index", "50\texit\tprocess-end\tstd:C11:7.22.4.9\tnoreturn\t-\t-", False),
                        ("std:POSIX.1-2017 page that is not in the pinned index", "50\tx\tthread-end\tstd:POSIX.1-2017:pthread_nothing\tnoreturn\t-\t-", False),
                        ("std with an unknown document", "50\tx\tprocess-end\tstd:C99:7.22.4.4\tnoreturn\t-\t-", False)):
    open(tf_, "w").write(HDR_HOST + "\n" + row + "\n")
    try:
        rdt(tf_)
        got_ = True
    except ValueError:
        got_ = False
    check("declared table: " + what + (" is accepted" if good else " is red"), got_ == good, row)
open(tf_, "w").write(HDR_HOST + "\n50\texit\tprocess-end\tstd:C11:7.22.4.4\tnoreturn\t-\t-\n")
try:
    rdt(tf_, std=None)
    check("declared table: a std: citation with no pinned index given is red", False)
except ValueError:
    check("declared table: a std: citation with no pinned index given is red", True)
open(tf_, "w").write(HDR_HOST + "\n50\tx\tsingle-process\twin-doc:a/b:f\tu32\t0\tERROR_INVALID_HANDLE=6\n")
try:
    rdt(tf_, win=None)
    check("declared table: a lasterror with no pinned headers given is red", False)
except ValueError:
    check("declared table: a lasterror with no pinned headers given is red", True)
got = rdt(tf_)
check("declared table: the row tuple is (name, fact, source, kind, value, lasterror)", got[50] == ("x", "single-process", "win-doc:a/b:f", "u32", "0", "ERROR_INVALID_HANDLE=6"), got)
# the loaders of the pinned documents: fixture trees, sha256 of every file checked
import hashlib as _hl  # noqa: E402
fx = os.path.join(ROOT, "stdfix")
os.makedirs(fx, exist_ok=True)
open(os.path.join(fx, "c11.tsv"), "w").write("7.22.4.4\tThe exit function\n")
open(os.path.join(fx, "posix2017.tsv"), "w").write("pthread_exit\n")
sha = lambda p_: _hl.sha256(open(os.path.join(fx, p_), "rb").read()).hexdigest()
pin = {"files": {"c11": {"path": "c11.tsv", "sha256": sha("c11.tsv")}, "posix": {"path": "posix2017.tsv", "sha256": sha("posix2017.tsv")}}}
check("std_index_load: the pinned files give the clause and page sets", objstub.std_index_load(fx, pin) == ({"7.22.4.4"}, {"pthread_exit"}), "")
open(os.path.join(fx, "c11.tsv"), "a").write("7.22.4.1\tThe abort function\n")
try:
    objstub.std_index_load(fx, pin)
    check("std_index_load: a file that differs from its pinned sha256 is an error", False)
except ValueError:
    check("std_index_load: a file that differs from its pinned sha256 is an error", True)
wx = os.path.join(ROOT, "wdkfix")
os.makedirs(wx, exist_ok=True)
open(os.path.join(wx, "winerror.h"), "w", newline="").write("#define ERROR_INVALID_HANDLE             6L\r\n#define HR_X ((HRESULT)0x80070005L)\r\n#define ERROR_ALIAS ERROR_INVALID_HANDLE\r\n")
open(os.path.join(wx, "winsock2.h"), "w", newline="").write("#define WSABASEERR              10000\n#define WSAEINTR                (WSABASEERR+4)\n#define EINTR_ALIAS WSAEINTR\n")
wsha = lambda p_: _hl.sha256(open(os.path.join(wx, p_), "rb").read()).hexdigest()
wpin = {"files": {"winerror": {"path": "winerror.h", "sha256": wsha("winerror.h")}, "winsock2": {"path": "winsock2.h", "sha256": wsha("winsock2.h")}}}
wv = objstub.winerror_load(wx, wpin)
check("winerror_load: decimals, hex with a cast, (WSABASEERR+N) and an alias of another define", wv["ERROR_INVALID_HANDLE"] == 6 and wv["HR_X"] == 0x80070005 and wv["WSAEINTR"] == 10004 and wv["EINTR_ALIAS"] == 10004 and wv["ERROR_ALIAS"] == 6, wv)

# gate on the toy product root of test_recipe.py
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
    TR.w(empty, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": TR.T + "toy", "class": "doc"}]}))
    rc, g, _, txt, st = gate(empty, ROOT + "/g0")
    check("gate with no product root: universe 0, green, accepted by verdict", rc == 0 and g["universe"] == 0 and st[0] == "green", (g, txt))
GC = ROOT + "/gcache"
repo = TR.make_repo("os-toy", files={"toy/constants.tsv": CONST})
rc, g, secs, txt, st = gate(repo, ROOT + "/g1", GC)
# the toy's functions: f_a, f_b, f_c, os_svc, plus the functions of the product's crt0 the toy links (rt/guest/crt0.s, %function symbols)
NTOY = 5 + len(re.findall(r"^\s*\.type\s+\S+,\s*%function", open(os.path.join(os.path.dirname(HERE), "..", "rt", "guest", "crt0.s")).read(), re.M))
# (+1: the `<root>:product-units` item that the no-doubles rule, doubles.py, adds per root)
check("gate on the toy root (os_layer, constants row, terminators from its own units): green NTOY/NTOY, accepted by verdict",
      rc == 0 and g["universe"] == NTOY == g["certified"] and st[0] == "green", (g, txt))
print("INFO toy gate cold (with build) %.1f s" % secs)
rc, g, secs, txt, st = gate(repo, ROOT + "/g2", GC)
print("INFO toy gate warm %.1f s" % secs)
check("gate again with the cache: same result", rc == 0 and g["universe"] == NTOY == g["certified"], (g, txt))
repo2 = TR.make_repo("os-toy2", files={"toy/constants.tsv": ""})
rc, g, _, txt, st = gate(repo2, ROOT + "/g3", GC)
check("toy root without the constants row of f_b: red, names the function by unit path", rc == 2 and g["uncertified"] == [TR.ROOT + "/b.c:f_b"]
      and st[0] == "red", (g, txt))
IDB = '#include "toy.h"\nint f_b(int x) { return x; }\n'
for tag, tbl, want in (("identity row", "b.c\tf_b\tidentity\t0\tref b\n", "green"), ("void row also matches the same body", "b.c\tf_b\tvoid\t\tref b\n", "green"),
                       ("no row", "", "red"), ("identity of another argument is no V row", "b.c\tf_b\tidentity\t1\tref b\n", "input")):
    ri_ = TR.make_repo("os-id-" + tag.replace(" ", "-"), files={"toy/constants.tsv": tbl, "toy/b.c": IDB})
    rc, g, _, txt, st = gate(ri_, ROOT + "/gid-" + tag.replace(" ", "-"), GC)
    check("constants kind identity, case %s: %s" % (tag, want), (rc == 0 and g["certified"] == g["universe"]) if want == "green" else
          (rc == 2 and g["uncertified"] == [TR.ROOT + "/b.c:f_b"]) if want == "red" else (rc == 2 and "input error" in g["detail"]), (g, txt))
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
repo4 = TR.make_repo("os-abort", rec=TR.mut(link=None), files={"toy/constants.tsv": CONST, "toy/c.c": '#include <stdlib.h>\nint f_c(int x) { abort(); }\n'})
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
check("ILP32 toy root (ELF32 objects, aarch64linux32 link): gate green NTOY/NTOY, accepted by verdict", rc == 0 and g["universe"] == NTOY == g["certified"] and st[0] == "green", (g, txt))
print("INFO ILP32 toy gate cold %.1f s" % secs)
rc, g, secs, txt, st = gate(r32, ROOT + "/g32b", GC)
print("INFO ILP32 toy gate warm %.1f s" % secs)
r32r = TR.make_repo("os-reach32", rec=TR.abi_rec("ilp32"), files={"toy/constants.tsv": CONST, "toy/c.c": RV, "toy/os.S": OS.replace("ldr x1, [x1, :lo12:hostslot]", "ldr w1, [x1, :lo12:hostslot]").replace("blr x1", "blr x1").replace(".quad 0", ".word 0")})
rc, g, _, txt, st = gate(r32r, ROOT + "/g32r", GC)
check("ILP32 reach call (P32 CALL26 relocation) is judged by the reach rule: reach-uncertified, nothing else red", rc == 2 and g["uncertified"] == [TR.ROOT + "/c.c:f_c"] and "reach-uncertified 1" in g["detail"], (g, txt))

# a failing unit is a red finding of that unit, never a gate crash (exit 2 from an exception)
rb = TR.make_repo("os-buildred", rec=TR.mut(link=None), files={"toy/constants.tsv": CONST, "toy/c.c": "int f_c(int x) { return x +; }\n"})
rc, g, _, txt, st = gate(rb, ROOT + "/gbr", GC)
check("a build-red unit: red finding build-red naming the unit, not an input error", rc == 2 and g["status"] == "red" and "build-red" in g["detail"]
      and "input error" not in g["detail"] and any(u.endswith("c.c") for u in g["uncertified"]), (g, txt))
bad_ii = tf + ".bad.ii"
open(bad_ii, "w").write("int f(void) { return +; }\n")
tc = objstub.unit_terminators("aarch64-linux-gnu-g++", [("pk-bad", bad_ii, "c")], {"c": []}, tempfile.mkdtemp(prefix="objstub-tc-"), 1, so=plug("aarch64-linux-gnu-g++"))
check("a unit the terminator plugin cannot process: a failure of that unit in the result (no exception)", tc[0] == [] and len(tc[2]) == 1 and tc[2][0][0] == "pk-bad", tc)

# the final image: every function body of the linked ELF is judged for the fixed-failure class (a weak `errno = ENOSYS; return -1` fallback left in it is red;
# the same image with a strong override is green, the weak body is not in the image)
ERR = '#include "toy.h"\nint e;\nint *__errno_location(void) { return &e; }\n'
WEAK = '#include "toy.h"\nint *__errno_location(void);\nint __attribute__((weak)) sched_getcpu(void) { *__errno_location() = 38; return -1; }\n'
USE = '#include "toy.h"\nint sched_getcpu(void);\nint f_c(int x) { return x + TOY + sched_getcpu(); }\n'
ri = TR.make_repo("os-image-weak", files={"toy/constants.tsv": CONST + "e.c\t__errno_location\taddr\te\tref e\n", "toy/c.c": USE, "toy/e.c": ERR, "toy/w.c": WEAK})
rc, g, _, txt, st = gate(ri, ROOT + "/gi1", GC)
check("image with a weak errno/-1 fallback: red fixed-failure, the function named under the image", rc == 2 and g["uncertified"] == [TR.ROOT + "/toy.elf:sched_getcpu"]
      and "fixed-failure 1" in g["detail"] and g["image"]["fixed_failure"] == 1 and g["image"]["bodies"] > 0, (g, txt))
rs = TR.make_repo("os-image-strong", files={"toy/constants.tsv": CONST + "e.c\t__errno_location\taddr\te\tref e\n", "toy/c.c": USE, "toy/e.c": ERR, "toy/w.c": WEAK, "toy/s.c": '#include "toy.h"\nint *__errno_location(void);\nint sched_getcpu(void) { return *__errno_location() + 3; }\n'})
rc, g, _, txt, st = gate(rs, ROOT + "/gi2", GC)
check("the same image with a strong override: green, the weak body is not in the image", rc == 0 and g["image"]["fixed_failure"] == 0 and g["image"]["bodies"] > 0, (g, txt))
# errno is derived by a probe compile with the root's own flags: an errno.h the root cannot compile is a red finding `errno-probe`, not a crash
rn2 = TR.mut(); rn2["flags"] = {k: v + ["-D_ERRNO_H"] for k, v in rn2["flags"].items()}   # errno.h becomes empty: `errno` is undeclared
rd = TR.make_repo("os-image-noprobe", rec=rn2, files={"toy/constants.tsv": CONST})
rc, g, _, txt, st = gate(rd, ROOT + "/gi3", GC)
check("a root whose errno.h does not compile: red errno-probe (the probe does not compile with the root's flags), never green", rc == 2 and "errno-probe" in g["detail"], (g, txt))
# the accessor is derived, no root declares it: a host root and a target root get theirs from their own libc's errno.h, and the key is no recipe key any more
ec = tempfile.mkdtemp(prefix="objstub-errno-")
check("errno accessor derived for a host compiler (x86-64 glibc: the call of __errno_location), no declaration",
      objstub.errno_probe("x86_64-linux-gnu-gcc-13", [], ec) == ("call", "__errno_location"), objstub.errno_probe("x86_64-linux-gnu-gcc-13", [], ec))
check("errno accessor derived for the AArch64 compiler (a call of the libc's accessor), no declaration",
      objstub.errno_probe("aarch64-linux-gnu-gcc-13", ["-mabi=lp64"], ec) == ("call", "__errno_location"), objstub.errno_probe("aarch64-linux-gnu-gcc-13", ["-mabi=lp64"], ec))
rk = TR.mut(); rk["errno_accessor"] = "__errno"
check("a recipe that declares errno_accessor is rejected (the accessor is derived, the key does not exist)", any("errno_accessor" in e_ for e_ in TR.errs(rk)), TR.errs(rk))
# a body with a data island in the final image is unjudged, and unjudged is red
ISL = '.text\n.globl isl\n.type isl,%function\nisl:\n mov w0, #1\n ret\n .word 0x12345678\n.size isl,.-isl\n'
rj = TR.make_repo("os-image-island", files={"toy/constants.tsv": CONST + "e.c\t__errno_location\taddr\te\tref e\n", "toy/c.c": USE.replace("sched_getcpu()", "0"), "toy/e.c": ERR, "toy/isl.S": ISL})
rc, g, _, txt, st = gate(rj, ROOT + "/gi4", GC)
check("an image body with a data island is red unjudged", rc == 2 and "unjudged" in g["detail"] and g["image"]["unjudged"] >= 1, (g, txt))

# a host root: the terminator plugin is built against the plugin headers of the root's own (x86-64 distro) compiler, and sees its noreturn declarations
xt = tf + ".x86"
open(tf + ".x86.h", "w").write("void fnvnx_die(void) __attribute__((noreturn));\n")
xn = objstub.derive_terminators("x86_64-linux-gnu-gcc-13", ["-I" + os.path.dirname(tf)], [os.path.basename(tf) + ".x86.h"], "c", xt, so=plug("x86_64-linux-gnu-gcc-13"))
check("host root compiler x86_64-linux-gnu-gcc-13: the plugin builds against its own plugin headers and finds the noreturn function", "fnvnx_die" in xn, xn)
xh = objstub.plugin_hash(plug("x86_64-linux-gnu-gcc-13"))
check("the plugin key depends on the root compiler: x86-64 distro gcc and the aarch64 compiler give different plugins",
      xh != objstub.plugin_hash(plug("aarch64-linux-gnu-g++")))

print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
sys.exit(1 if FAILED else 0)
