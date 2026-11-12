#!/usr/bin/env python3
"""Tests for the assume_violation rule of verify/objstub.py (gate objstub). Plain script: one PASS/FAIL line per case, exit 0 on pass.
Compiles with aarch64-linux-gnu-gcc (ilp32 and lp64); scratch under tempfile.mkdtemp."""
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
HDR = ("extern const unsigned fnvnx_errno_tbl[4];\n"
       "void fnvnx_assume_violation(unsigned, unsigned) __attribute__((noreturn));\n")


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


def run(body, abi, domains=("fnvnx_errno_tbl",), registry=REG, defs=""):
    """-> (kinds {(function, kind)}, counts) of one consumer object holding BODY, judged against REGISTRY and the domain symbols."""
    o = build(HDR + body, abi)
    res = objstub.analyse_all([(o, o)], set(), set())
    items, finds = objstub.judge(res, {}, None, None, None, (registry, set(domains)))
    return {(n, k) for _, n, k, _ in finds}, objstub.assume_counts(res, (registry, set(domains)))


for abi in ("ilp32", "lp64"):
    T = lambda s: "%s: %s" % (abi, s)
    k, c = run("int tr(unsigned e) { if (fnvnx_errno_tbl[e & 3] != e) fnvnx_assume_violation(4, e); return e; }", abi)
    check(T("integer id of an OWNER record, call guarded by the generated table: accepted"), not k and c == {"sites": 1, "accepted": 1, "red": 0, "by_id": {"ASM-004": 1}}, (k, c))
    for lit, want in (("ASM-004", set()), ("ASM-001", {("tr", "assume-id-not-owner")}), ("ASM-999", {("tr", "assume-id-not-owner")})):
        k, c = run('int tr(unsigned e) { if (fnvnx_errno_tbl[e & 3] != e) fnvnx_assume_violation((unsigned long)"%s", e); return e; }' % lit, abi)
        check(T("string literal id %s: %s" % (lit, "accepted" if not want else "assume-id-not-owner")), k == want, k)
    k, c = run("int tr(unsigned e) { for (unsigned i = 0; i < 4; i++) if (fnvnx_errno_tbl[i] == e) return (int)i; fnvnx_assume_violation(2, e); }", abi)
    check(T("loop over the generated table, call after the loop: accepted"), not k, k)
    k, c = run("int tr(unsigned e) { if (fnvnx_errno_tbl[e & 3] != e) fnvnx_assume_violation(1, e); return e; }", abi)
    check(T("id of a REMOVE record: assume-id-not-owner"), k == {("tr", "assume-id-not-owner")} and c["red"] == 1, k)
    k, c = run("int tr(unsigned e) { if (fnvnx_errno_tbl[e & 3] != e) fnvnx_assume_violation(77, e); return e; }", abi)
    check(T("id without a record: assume-id-not-owner"), k == {("tr", "assume-id-not-owner")}, k)
    k, c = run("int tr(unsigned e) { if (fnvnx_errno_tbl[e & 3] != e) fnvnx_assume_violation(e, e); return e; }", abi)
    check(T("non-literal id: assume-id-nonliteral"), k == {("tr", "assume-id-nonliteral")}, k)
    k, c = run("int tr(unsigned e) { if (fnvnx_errno_tbl[e & 3] != e) fnvnx_assume_violation(4, e); return e; }", abi, domains=())
    check(T("the table is not a generated output (hand-written domain): assume-domain-not-generated"), k == {("tr", "assume-domain-not-generated")}, k)
    k, c = run("int tr(unsigned e) { fnvnx_assume_violation(4, e); }", abi)
    check(T("no guard at all: assume-unguarded"), k == {("tr", "assume-unguarded")}, k)
    k, c = run("int tr(unsigned e) { if (e > 3) fnvnx_assume_violation(4, e); return e; }", abi)
    check(T("guard that does not depend on a table: assume-unguarded"), k == {("tr", "assume-unguarded")}, k)
    k, c = run("int tr(unsigned e, unsigned f) { if (fnvnx_errno_tbl[f & 3] != f) fnvnx_assume_violation(4, e); return e; }", abi)
    check(T("guard on a table lookup of another value than the one reported: assume-unguarded"), k == {("tr", "assume-unguarded")}, k)
    k, c = run("int tr(unsigned e) { if (fnvnx_errno_tbl[e & 3] != e) fnvnx_assume_violation(4, e); return e; }\n"
               "int tq(unsigned e) { fnvnx_assume_violation(1, e); }", abi)
    check(T("counts are global over the object: 2 sites, 1 accepted, 1 red"), c["sites"] == 2 and c["accepted"] == 1 and c["red"] == 1, (k, c))
    k, c = run("typedef void (*F)(unsigned, unsigned);\nF keep = (F)fnvnx_assume_violation;\n", abi)
    check(T("address of the function taken: assume-address-taken (not a call)"), any(kk == "assume-address-taken" for _, kk in k), k)

# the definition: a function that ends in a call through a pointer on every path is the exit (like REACH); another body is not
DEF_OK = ("extern void (*hostslot)(unsigned, unsigned);\nvoid fnvnx_assume_violation(unsigned a, unsigned v) { hostslot(a, v); __builtin_unreachable(); }\n")
o = build(DEF_OK, "ilp32")
items, finds = objstub.judge(objstub.analyse_all([(o, o)], set(), set()), {}, None, None, None, (REG, set()))
check("definition of assume_violation ending in the call through the host slot: no finding of the exit kind",
      not any(k in ("assume-not-exit", "constants-mismatch") for _, _, k, _ in finds) and not any(n == "fnvnx_assume_violation" and k == "indirect-noreturn-tail" for _, n, k, _ in finds), finds)
o = build("int g;\nvoid fnvnx_assume_violation(unsigned a, unsigned v) { g = a + v; }\n", "ilp32")
items, finds = objstub.judge(objstub.analyse_all([(o, o)], set(), set()), {}, None, None, None, (REG, set()))
check("definition that returns: assume-not-exit or another finding (never accepted)", any(n == "fnvnx_assume_violation" for _, n, _, _ in finds), finds)

# domain derivation: only the objects of generated units contribute, only defined global OBJECT symbols
g1 = build("const unsigned fnvnx_errno_tbl[4] = {0, 1, 2, 3};\nstatic const int loc[2] = {1, 2};\nint use(int i) { return loc[i & 1]; }\n", "ilp32")
g2 = build("const unsigned hand_tbl[4] = {0, 1, 2, 3};\n", "ilp32")
check("generated_domains: the global object of a generated unit only", objstub.generated_domains([("gen/a.c", g1), ("hand/b.c", g2)], {"gen/a.c"}) == {"fnvnx_errno_tbl"},
      objstub.generated_domains([("gen/a.c", g1), ("hand/b.c", g2)], {"gen/a.c"}))
check("generated_domains: no generated unit, no domain", objstub.generated_domains([("hand/b.c", g2)], set()) == set())

print("FAILED: %s" % FAILED if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
