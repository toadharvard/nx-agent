#!/usr/bin/env python3
import os, shutil, subprocess, sys, tempfile

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "test_vtnames.cpp")
clang = shutil.which("clang++-18")
if not clang or not os.path.exists("/usr/lib/llvm-18/include/clang/AST/VTableBuilder.h"):
    print("skip: clang++-18 or clang dev headers missing")
    sys.exit(0)

fails = []
def check(ok, msg):
    if not ok:
        fails.append(msg)

tmp = tempfile.mkdtemp()
r = subprocess.run([os.path.join(here, "build.sh"), tmp], capture_output=True, text=True)
if r.returncode != 0:
    print("build failed:\n" + r.stderr[-2000:])
    sys.exit(1)
so = r.stdout.strip().splitlines()[-1]

def run(target, out):
    return subprocess.run(
        [clang, "-target", target, "-fsyntax-only", "-std=c++17", "-fplugin=" + so,
         "-fplugin-arg-fnvnx_vtnames-out=" + out, src],
        capture_output=True, text=True)

out = os.path.join(tmp, "out.txt")
r = run("i686-pc-windows-msvc", out)
check(r.returncode == 0, "msvc run failed: " + r.stderr[-500:])
rows = []
if os.path.exists(out):
    rows = [l.split("\t") for l in open(out).read().splitlines()]
check(all(len(c) == 4 for c in rows), "every line has 4 columns")
lines = ["\t".join(c) for c in rows]
check(len(lines) == len(set(lines)), "duplicate lines")

def sel(t):
    return [c for c in rows if c[0] == t]

check("1A\t0\t??_7A@@6B@\t0" in lines, "single inheritance A")
check("1M\t0\t??_7M@@6BA@@@\t0" in lines, "M primary table")
check("1M\t8\t??_7M@@6BS2@@@\t0" in lines, "M secondary table")
check(len(sel("1M")) == 2, "M has exactly two tables")
check(len(sel("2S2")) == 1, "S2 one table")
for t, name, off in [("N1n1BE", "B", "0"), ("N5Outer5InnerE", "Inner", "0"), ("2TTIiE", "TT", "0")]:
    s = sel(t)
    check(len(s) == 1 and s[0][1] == off and s[0][2].startswith("??_7") and name in s[0][2]
          and s[0][3] == "0", "structure of " + t + ": " + repr(s))
check(not any("NeverUsed" in c[0] or "NeverUsed" in c[2] for c in rows), "uninstantiated template emitted")
check(not any(c[0] == "5Plain" for c in rows), "non-polymorphic emitted")
s = sel("9WithVBase")
check(len(s) >= 1 and all(c[3] == "1" and c[2].startswith("??_7") and "WithVBase" in c[2] for c in s),
      "virtual base class: " + repr(s))
check(not any(c[0] == "2VB" for c in rows), "VB has no vtable")

r = run("aarch64-linux-gnu", os.path.join(tmp, "out2.txt"))
check(r.returncode != 0 and "fnvnx_vtnames: target is not the Microsoft" in r.stderr,
      "aarch64 must fail with plugin error: " + r.stderr[-300:])

# ---- sympair= / members= ----
sp_src = os.path.join(here, "test_sympair.cpp")
spf, mbf = os.path.join(tmp, "sp.txt"), os.path.join(tmp, "mb.txt")
r = subprocess.run([clang, "-target", "i686-pc-windows-msvc", "-fsyntax-only", "-std=c++17",
                    "-fplugin=" + so, "-fplugin-arg-fnvnx_vtnames-sympair=" + spf,
                    "-fplugin-arg-fnvnx_vtnames-members=" + mbf, sp_src],
                   capture_output=True, text=True)
check(r.returncode == 0, "sympair run failed: " + r.stderr[-500:])
spl = open(spf).read().splitlines() if os.path.exists(spf) else []
mbl = open(mbf).read().splitlines() if os.path.exists(mbf) else []
check(spl == sorted(set(spl)) and mbl == sorted(set(mbl)), "sympair/members sorted and unique")
sp = [l.split("\t") for l in spl]
check(all(len(c) == 4 for c in sp), "sympair has 4 columns")
def has(ita, kind, dr, name):
    f = [c for c in sp if c[0] == ita]
    return (len(f) == 1 and f[0][2] == kind and f[0][3] == dr
            and f[0][1].startswith("?") and name in f[0][1])
check("_Z3addii\t?add@@YAHHH@Z\tfunc\tdef" in spl, "add def")
check("_Z6calleei\t?callee@@YAHH@Z\tfunc\tref" in spl, "callee ref")
check(has("_Z10def_global", "data", "def", "def_global"), "global def")
check(has("_Z10ext_global", "data", "ref", "ext_global"), "extern global ref")
check(has("_ZN1S2sfEi", "func", "def", "sf"), "static member")
check(has("_ZN1S2vfEv", "func", "def", "vf"), "virtual member defined out of line")
for v in ("C1", "C2"):
    check(has("_ZN1S%sEv" % v, "ctor", "def", "S"), "ctor " + v)
for v in ("D0", "D1", "D2"):
    check(has("_ZN1S%sEv" % v, "dtor", "def", "S"), "dtor " + v)
check(has("_ZN4Imp3D1Ev", "dtor", "ref", "Imp3"), "dtor ref via delete")
check(has("_Z5twiceIiET_S0_", "func", "def", "twice"), "template instance")
check(not any("anon_fn" in c[0] or "stat_fn" in c[0] for c in sp), "anonymous namespace / static emitted")
check(len([c for c in sp if "twice" in c[0]]) == 1, "template pattern emitted")
check("1S\t_ZN1SD1Ev\tdeclared" in mbl, "S dtor declared")
check("6Holder\t_ZN6HolderD1Ev\timplicit" in mbl, "Holder implicit dtor")
check(not any(l.startswith("4Imp2\t_ZN4Imp2D") for l in mbl), "Imp2 must not have a dtor")
check(not any(l.startswith("1S\t_ZN1S1gEv") or l.startswith("1S\t_Z3add") for l in mbl),
      "undeclared member present")
r = subprocess.run([clang, "-target", "aarch64-linux-gnu", "-fsyntax-only", "-std=c++17",
                    "-fplugin=" + so, "-fplugin-arg-fnvnx_vtnames-sympair=" + spf, sp_src],
                   capture_output=True, text=True)
check(r.returncode != 0, "sympair on non-MS target must fail")

shutil.rmtree(tmp, ignore_errors=True)
if fails:
    for f in fails:
        if f:
            print("FAIL: " + f)
    sys.exit(1)
print("test_vtnames: ok")
