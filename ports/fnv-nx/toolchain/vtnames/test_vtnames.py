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

shutil.rmtree(tmp, ignore_errors=True)
if fails:
    for f in fails:
        if f:
            print("FAIL: " + f)
    sys.exit(1)
print("test_vtnames: ok")
