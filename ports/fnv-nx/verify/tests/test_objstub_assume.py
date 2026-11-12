#!/usr/bin/env python3
"""Tests for the assume_violation rule of verify/objstub.py (gate objstub). Plain script: one PASS/FAIL line per case, exit 0 on pass.
Compiles with aarch64-linux-gnu-gcc (ilp32 and lp64); scratch under tempfile.mkdtemp. The generated unit is the object whose id is in the
set of generated units (what gate_objstub derives from recipe.generated_outputs); the generator's own code is what the cases model."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import objstub  # noqa: E402
import scratch  # noqa: E402

scratch.install()
ROOT = tempfile.mkdtemp(prefix="objstub-assume-")
FAILED, N = [], [0]
CC = "aarch64-linux-gnu-gcc"
REG = {"ASM-004": "OWNER", "ASM-002": "OWNER", "ASM-001": "REMOVE"}
HDR = "void fnvnx_assume_violation(unsigned, unsigned) __attribute__((noreturn));\n"
CHECK = ("const int fnvnx_dom_errno[4] = {1, 2, 5, 9};\n"
         "int fnvnx_dom_errno_check(int v) { for (int i = 0; i < 4; i++) if (fnvnx_dom_errno[i] == v) return v; fnvnx_assume_violation(%s, (unsigned)v); }\n")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:600]))
    if not cond:
        FAILED.append(name)


def build(src, abi):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.c" % N[0]), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run([CC, "-mabi=" + abi, "-O2", "-c", s, "-o", o], check=True, capture_output=True)
    return o


def run(body, abi, generated=True, registry=REG):
    """-> (kinds {(function, kind)}, counts) of one object holding BODY, a generated unit or not."""
    o = build(HDR + body, abi)
    res = objstub.analyse_all([(o, o)], set(), set())
    a = (registry, {o} if generated else set())
    items, finds = objstub.judge(res, {}, None, None, None, a)
    return {(n, k) for _, n, k, _ in finds}, objstub.assume_counts(res, a)


for abi in ("ilp32", "lp64"):
    T = lambda s: "%s: %s" % (abi, s)
    k, c = run(CHECK % "4", abi)
    check(T("generated check function, integer id of an OWNER record: accepted"), not k and c == {"sites": 1, "accepted": 1, "red": 0, "by_id": {"ASM-004": 1}}, (k, c))
    for lit, want in (("ASM-004", set()), ("ASM-001", {("fnvnx_dom_errno_check", "assume-id-not-owner")}), ("ASM-999", {("fnvnx_dom_errno_check", "assume-id-not-owner")})):
        k, c = run(CHECK % ('(unsigned)(unsigned long)"%s"' % lit), abi)
        check(T("string literal id %s: %s" % (lit, "accepted" if not want else "assume-id-not-owner")), k == want, k)
    k, c = run(CHECK % "1", abi)
    check(T("id of a REMOVE record: assume-id-not-owner"), k == {("fnvnx_dom_errno_check", "assume-id-not-owner")} and c["red"] == 1, k)
    k, c = run(CHECK % "77", abi)
    check(T("id without a record: assume-id-not-owner"), k == {("fnvnx_dom_errno_check", "assume-id-not-owner")}, k)
    k, c = run(CHECK % "(unsigned)v", abi)
    check(T("non-literal id: assume-id-nonliteral"), k == {("fnvnx_dom_errno_check", "assume-id-nonliteral")}, k)
    k, c = run(CHECK % "4", abi, generated=False)
    check(T("the same code in a hand-written unit (table not a generated output): assume-direct-call"), k == {("fnvnx_dom_errno_check", "assume-direct-call")}, k)
    k, c = run("int tr(unsigned e) { if (e > 3) fnvnx_assume_violation(4, e); return e; }", abi, generated=False)
    check(T("direct call from hand-written code: assume-direct-call"), k == {("tr", "assume-direct-call")} and c["red"] == 1, k)
    k, c = run(CHECK % "4" + "int tq(unsigned e) { fnvnx_assume_violation(2, e); }", abi, generated=False)
    check(T("counts are global over the object: 2 sites, 0 accepted, 2 red"), c["sites"] == 2 and c["accepted"] == 0 and c["red"] == 2, (k, c))
    k, c = run("typedef void (*F)(unsigned, unsigned);\nF keep = (F)fnvnx_assume_violation;\n", abi)
    check(T("address of the function taken: assume-address-taken (not a call)"), any(kk == "assume-address-taken" for _, kk in k), k)
    k, c = run("", abi)
    check(T("no call: nothing"), not k and c["sites"] == 0)

# the definition: a function that ends in a call through a pointer on every path is the exit (like REACH); another body is not
o = build("extern void (*hostslot)(unsigned, unsigned);\nvoid fnvnx_assume_violation(unsigned a, unsigned v) { hostslot(a, v); __builtin_unreachable(); }\n", "ilp32")
items, finds = objstub.judge(objstub.analyse_all([(o, o)], set(), set()), {}, None, None, None, (REG, set()))
check("definition of assume_violation ending in the call through the host slot: no exit finding",
      not any(k in ("assume-not-exit", "constants-mismatch") for _, _, k, _ in finds) and not any(n == "fnvnx_assume_violation" and k == "indirect-noreturn-tail" for _, n, k, _ in finds), finds)
o = build("int g;\nvoid fnvnx_assume_violation(unsigned a, unsigned v) { g = a + v; }\n", "ilp32")
items, finds = objstub.judge(objstub.analyse_all([(o, o)], set(), set()), {}, None, None, None, (REG, set()))
check("definition that returns: a finding (never accepted)", any(n == "fnvnx_assume_violation" for _, n, _, _ in finds), finds)

print("FAILED: %s" % FAILED if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
