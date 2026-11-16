#!/usr/bin/env python3
"""Tests of gate_fpexact for a host-profile root (RECIPE.md TARGETS elf_x86_64): the object's own e_machine is its root's profile, so an x86-64 object is judged by the
x86 rule (cvtt* are the specified semantics, RCPSS / RSQRTSS are the estimate class) and never rejected as "not AArch64". Plain script: one PASS/FAIL line per case."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, "..", ".."))
VERIFY = os.path.normpath(os.path.join(LIFT, "..", "verify"))
for p in (os.path.join(LIFT, "proof"), VERIFY, os.path.join(LIFT, "..", "contract"), LIFT):
    sys.path.insert(0, p)
import gate_fpexact as G  # noqa: E402

TMP = tempfile.mkdtemp(prefix="fpexact-host-")
FAILED = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:500]))
    if not cond:
        FAILED.append(name)


def build(name, text):
    c, o = os.path.join(TMP, name + ".c"), os.path.join(TMP, name + ".o")
    open(c, "w").write(text)
    subprocess.run(["x86_64-linux-gnu-gcc-13", "-O2", "-fno-asynchronous-unwind-tables", "-c", c, "-o", o], check=True, capture_output=True)
    return o


est = build("est", "#include <xmmintrin.h>\nint f(float x) { return (int)x; }\nfloat g(float x) { return _mm_cvtss_f32(_mm_rcp_ss(_mm_set_ss(x))); }\n")
clean = build("clean", "int k(int x) { return x + 1; }\nint f(float x) { return (int)x; }\n")
r = G.judge_object_coded("h/est.c", est, False, "est.c", None)
check("an x86-64 object is judged by the x86 rule (no input error)", set(r) == {"rcp:h/est.c", "f2i:h/est.c"}, r)
check("cvttss2si is the x86 semantics: no f2i finding and no plugin report needed", r["f2i:h/est.c"] == [], r)
check("rcpss is an rcp-estimate finding", [c for c, _ in r["rcp:h/est.c"]] == ["rcp-estimate"], r)
c = G.judge_object_coded("h/clean.c", clean, False, "clean.c", None)
check("an object without estimate instructions is clean", c == {"rcp:h/clean.c": [], "f2i:h/clean.c": []}, c)
check("the scan names the object's architecture", G.scan_object(est)["arch"] == "x86_64" and G.scan_object(clean)["arch"] == "x86_64")
sys.exit(1 if FAILED else 0)
