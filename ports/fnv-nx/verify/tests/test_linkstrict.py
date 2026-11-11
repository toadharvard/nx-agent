#!/usr/bin/env python3
"""Tests for verify/gate_linkstrict.py (class 3): the rules on synthetic objects (compiled with aarch64-linux-gnu-gcc) and the gate on the
toy product root of test_recipe.py (reused by import). Plain script: PASS/FAIL lines, exit 0 on pass."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, HERE)
import gate_linkstrict as L  # noqa: E402
import test_recipe as TR  # noqa: E402
import verdict  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="linkstrict-")
FAILED, N, T0 = [], [0], time.time()


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:500]))
    if not ok:
        FAILED.append(name)


def build(src, ext="c", extra=()):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.%s" % (N[0], ext)), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run(["aarch64-linux-gnu-gcc", "-O1", "-c", s, "-o", o] + list(extra), check=True, capture_output=True)
    return o


def kinds(objs, elfs=None, tables=None):
    items, finds = L.judge({p: L.elf_symbols(p) or [] for p in objs}, elfs or {}, tables or {})
    return {(n, k) for _, n, k, _ in finds}, items, finds


a = build("int ext(void);\nint wk(void) __attribute__((weak));\nint f(void){return ext() + (wk ? wk() : 0);}\nint ok(void){return 1;}")
b = build("int ext(void){return 2;}")
k, items, _ = kinds([a, b])
check("(b) a reference with one strong definition is certified; a weak undefined reference without a definition is `undefined`",
      ("ext", "undefined") not in k and ("ext", "unresolved") not in k and ("wk", "undefined") in k and len(items) == 5, (k, items))
d1, d2 = build("int dup(void){return 1;}\nint use(void);"), build("int dup(void){return 2;}")
k, _, _ = kinds([d1, d2])
check("(b) two strong definitions of one symbol -> duplicate-strong", ("dup", "duplicate-strong") in k, k)
w = build("__attribute__((weak)) int wd(void){return 1;}\nint wd2(void);\nint r(void){return wd();}", extra=["-DX"])
r_ = build("int wd(void);\nint r(void){return wd();}")
k, _, _ = kinds([w, r_])
check("(b) a weak definition without a strong one -> weak-only, and the reference to it is unresolved", ("wd", "weak-only") in k and ("wd", "unresolved") in k, k)
w2 = build("__attribute__((weak)) int wd(void){return 1;}")
s2 = build("int wd(void){return 3;}")
k, _, _ = kinds([w2, s2])
check("(b) a weak definition overridden by one strong definition is fine", not {x for x in k if x[0] == "wd"}, k)
c1 = build("int cm;", extra=["-fcommon"])
k, _, _ = kinds([c1])
check("(b) a common symbol -> common", ("cm", "common") in k, k)
i1 = build('static int impl(void){return 1;}\nint (*res(void))(void){return impl;}\nint ifn(void) __attribute__((ifunc("res")));')
k, _, _ = kinds([i1])
check("(b) an IFUNC definition -> ifunc", ("ifn", "ifunc") in k, k)
# (c) on symbol tuples: an alias stub (two contract names, one body) and the same body twice
elf = {"x.elf": [("a", 0x1000, 8, 2, 1, 1), ("b", 0x1000, 8, 2, 1, 1), ("c", 0x2000, 8, 2, 1, 1), ("z", 0x3000, 0, 2, 1, 1)]}
k, _, f = kinds([], elf, {("r", "x.elf"): [("a", "B1"), ("b", "B2"), ("c", "B3")]})
check("(c) two contract names with different body ids on one address -> contract-alias on both, the third is fine",
      {("contract:a", "contract-alias"), ("contract:b", "contract-alias")} <= k and ("contract:c", "contract-alias") not in k, k)
k, _, _ = kinds([], elf, {("r", "x.elf"): [("a", "B1"), ("b", "B1"), ("c", "B3")]})
check("(c) the same body id on one address is one body: green", not {x for x in k if x[0].startswith("contract:")}, k)
k, _, _ = kinds([], elf, {("r", "x.elf"): [("nope", "B1"), ("z", "B2")], ("q", None): [("a", "B1")]})
check("(c) a name missing from the ELF, a name of size 0, a table with no ELF -> not certified",
      {("contract:nope", "contract-undefined"), ("contract:z", "contract-undefined"), ("contract:a", "contract-no-elf")} <= k, k)
k, _, _ = kinds([], {"u.elf": [("g", 0, 0, 0, 1, 0), ("h", 0, 0, 0, 2, 0), ("i", 0x10, 4, 10, 1, 1)]})
check("(a) ELF with an undefined, a weak undefined and an IFUNC symbol; and one without .symtab", {("<elf>", "elf-undefined"), ("<elf>", "elf-weak-undefined"),
      ("<elf>", "elf-ifunc")} <= k and ("<elf>", "elf-no-symtab") in kinds([], {"n.elf": None})[0], k)

# gate on the toy root
LS = lambda repo, name, cache=None: (lambda r: (r, verdict._gate_result({"name": "linkstrict", "result": "linkstrict.json"}, TR.BASE + "/" + name, r[0])))(
    TR.gate(repo, "linkstrict", TR.BASE + "/" + name, cache))
GC = ROOT + "/cache"
empty = TR.make_repo("ls-empty")
TR.w(empty, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": TR.T + "toy", "class": "doc", "gates": []}]}))
(rc, g, _, txt), st = LS(empty, "e")
check("no product root: universe 0, green, accepted by the verdict", rc == 0 and g["universe"] == 0 and st[0] == "green", (g, txt))
repo = TR.make_repo("ls-toy")
t0 = time.time()
(rc, g, _, txt), st = LS(repo, "t1", GC)
check("toy root: every reference resolved, ELF closed: green, accepted (1 reference + 4 definitions + the ELF)", rc == 0 and g["universe"] == 6 == g["certified"]
      and st[0] == "green", (g, txt))
print("INFO toy gate (incl. build) %.1f s" % (time.time() - t0))
t0 = time.time()
(rc, g, _, txt), st = LS(repo, "t2", GC)
print("INFO toy gate warm %.1f s" % (time.time() - t0))
WK = '#include "toy.h"\nint wk(void) __attribute__((weak));\nint f_c(int x) { return wk ? wk() : x + TOY; }\n'
(rc, g, _, txt), st = LS(TR.make_repo("ls-weak", files={"toy/c.c": WK}), "t3", GC)
check("weak undefined symbol: ld links (and drops it from the static ELF, so rule (a) cannot see it) but rule (b) on the object is red",
      rc == 2 and st[0] == "red" and g["uncertified"] == [TR.ROOT + "/c.c:wk"] and "undefined 1" in g["detail"], (g, txt))
(rc, g, _, txt), st = LS(TR.make_repo("ls-nolink", rec=TR.mut(link=None), files={"toy/b.c": None}), "t4", GC)
check("root without a link: an undefined reference (f_b, no definition anywhere) is red", rc == 2 and g["uncertified"] == [TR.ROOT + "/a.c:f_b"], (g, txt))
ALIAS = '#include "toy.h"\nint f_b(void) { return TOY * 2; }\nint f_b2(void) __attribute__((alias("f_b")));\n'
for tag, rows, ok in (("different body ids", "f_b\tB1\nf_b2\tB2\n", False), ("one body id", "f_b\tB1\nf_b2\tB1\n", True)):
    rr = TR.make_repo("ls-alias-" + str(ok), rec=TR.mut(contract_names="contract.tsv"), files={"toy/b.c": ALIAS, "toy/contract.tsv": rows})
    (rc, g, _, txt), st = LS(rr, "t5" + str(ok), GC)
    check("recipe key contract_names, alias stub with %s: %s" % (tag, "green" if ok else "red contract-alias"),
          (rc == 0 and g["certified"] == g["universe"]) if ok else (rc == 2 and g["uncertified"] == [TR.ROOT + "/contract.tsv:contract:f_b", TR.ROOT + "/contract.tsv:contract:f_b2"]
                                                                  or "contract-alias 2" in g["detail"]), (g, txt))
print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
sys.exit(1 if FAILED else 0)
