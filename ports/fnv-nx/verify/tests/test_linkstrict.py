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


ABI = ["lp64"]


def check(name, ok, extra=""):
    name = "[%s] %s" % (ABI[0], name)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:500]))
    if not ok:
        FAILED.append(name)


def build(src, ext="c", extra=()):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.%s" % (N[0], ext)), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    cc = "aarch64-linux-gnu-g++" if ext == "cc" else "aarch64-linux-gnu-gcc"
    subprocess.run([cc, "-mabi=" + ABI[0], "-O1", "-c", s, "-o", o] + list(extra), check=True, capture_output=True)
    return o


def kinds(objs, elfs=None, tables=None):
    items, finds = L.judge({p: L.elf_symbols(p) or [] for p in objs}, elfs or {}, tables or {}, {p: L.elf_comdat(p) for p in objs})
    return {(n, k) for _, n, k, _ in finds}, items, finds


for ABI[0] in ("lp64", "ilp32"):
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
    k, _, _ = kinds([], {"l.elf": [("loc", 0, 0, 0, 0, 0)]})
    check("(a) a LOCAL undefined symbol (a target a relocation could name) is red", ("<elf>", "elf-undefined") in k, k)
    k, _, _ = kinds([], {"u.elf": [("g", 0, 0, 0, 1, 0), ("h", 0, 0, 0, 2, 0), ("i", 0x10, 4, 10, 1, 1)]})
    check("(a) ELF with an undefined, a weak undefined and an IFUNC symbol; and one without .symtab", {("<elf>", "elf-undefined"), ("<elf>", "elf-weak-undefined"),
          ("<elf>", "elf-ifunc")} <= k and ("<elf>", "elf-no-symtab") in kinds([], {"n.elf": None})[0], k)


    # (b1) C++ vague linkage: COMDAT groups
    INL = "__attribute__((noinline)) inline int sq(int x) { return x * x + K; }\n"
    ca = build("#define K 1\n" + INL + "const char *oa() { return \"only-in-a\"; }\nint fa(int v) { return sq(v); }\n", "cc")
    cb = build("#define K 1\n" + INL + "int fb(int v) { return sq(v) + 1; }\n", "cc")
    cc_ = build("int sq(int);\nint fc(int v) { return sq(v); }\n", "cc")
    k, items, _ = kinds([ca, cb, cc_])
    check("(b1) an inline function instantiated identically in two units is one definition: no weak-only, the reference is certified, the signature is an item",
          not {x for x in k if x[0] == "_Z2sqi"} and ("<comdat>", "_Z2sqi") in {(i, n) for i, n in items}, (k, items))
    cd = build("#define K 2\n" + INL + "int fb(int v) { return sq(v) + 1; }\n", "cc")
    k, _, f = kinds([ca, cd, cc_])
    check("(b1) the same inline function with another macro value in one unit: comdat-differs on the signature, the symbol and the reference",
          {("_Z2sqi", "comdat-differs")} <= k and any(i == "<comdat>" and n == "_Z2sqi" and kd == "comdat-differs" for i, n, kd, _ in f)
          and any(i == cc_ and n == "_Z2sqi" for i, n, _, _ in f), (k, f))
    VT = "struct B { virtual ~B() {} virtual int f() { return 1; } };\nB *%s() { return new B; }\n"
    v1, v2 = build(VT % "mk1", "cc"), build(VT % "mk2", "cc")
    k, _, f = kinds([v1, v2])
    check("(b1) a class with a virtual destructor in two units: vtable, typeinfo and member groups are identical, no finding of vague linkage",
          not {x for x in k if x[1] in ("weak-only", "comdat-differs")} and any(n == "_ZTV1B" for _, n, _, _ in f) is False, (k, f))
    v3 = build(VT.replace("return 1", "return 3") % "mk3", "cc")
    k, _, _ = kinds([v1, v3])
    check("(b1) the same class with another inline body: its member function and vtable group differ", {("_ZN1B1fEv", "comdat-differs"), ("<defs>", "_ZN1B1fEv")} & k
          and ("_ZN1B1fEv", "comdat-differs") in k, k)
    SL = "const char *%s() { return \"zzzz-%s\"; }\n__attribute__((noinline)) inline const char *nm() { return \"hello\"; }\nconst char *u%s() { return nm(); }\n"
    s1, s2 = build(SL % ("p", "p", "1"), "cc"), build(SL % ("q", "q-longer", "2"), "cc")
    k, _, f = kinds([s1, s2])
    check("(b1) a string literal in an inline function certifies wherever the object laid it out (SHF_MERGE entity, not offset)", not {x for x in k if x[1] == "comdat-differs"}, (k, f))
    wk = build("__attribute__((weak)) int wk() { return 1; }\n", "cc")
    k, _, _ = kinds([wk])
    check("(b1) a weak definition outside every COMDAT group stays weak-only in C++", ("wk", "weak-only") in k or ("_Z2wkv", "weak-only") in k, k)
    mem, grp = L.elf_comdat(ca)
    check("elf_comdat: signature -> digest, member section indices; no group in a C object", "_Z2sqi" in grp and mem and not any(L.elf_comdat(build("int x(void){return 1;}")))
          , (mem, grp))
    # (b2) script symbols
    ref = build("extern char __image_end[];\nchar *e(void) { return __image_end; }")
    elf_ok = {"r/x.elf": [("__image_end", 0x1000, 0, 0, 1, 1)]}
    elf_weak = {"r/x.elf": [("__image_end", 0x1000, 0, 0, 2, 1)]}
    elf_none = {"r/x.elf": [("other", 0x1000, 0, 0, 1, 1)]}
    for tag, el, bad in (("defined GLOBAL in the ELF of its root", elf_ok, False), ("defined only weak", elf_weak, True), ("not defined in the ELF", elf_none, True)):
        items, finds = L.judge({"r/o.o": L.elf_symbols(ref)}, el, {})
        check("(b2) a reference to a link-script symbol, %s: %s" % (tag, "undefined" if bad else "certified"),
              (("r/o.o", "__image_end", "undefined", finds[0][3] if finds else "") in finds) if bad else not finds, finds)
    items, finds = L.judge({"r/o.o": L.elf_symbols(ref)}, elf_ok, {}, {})
    items2, finds2 = L.judge({"q/o.o": L.elf_symbols(ref)}, elf_ok, {})
    check("(b2) the ELF of another root does not define it for this root's objects", not finds and any(n == "__image_end" for _, n, _, _ in finds2), finds2)
ABI[0] = "lp64"

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
# the toy root in the ILP32 ABI (ELF32 objects, aarch64linux32 link), and a link-script symbol end to end in both ABIs
for abi in ("ilp32", "lp64"):
    ABI[0] = abi
    t0 = time.time()
    (rc, g, _, txt), st = LS(TR.make_repo("ls-toy-" + abi, rec=TR.abi_rec(abi)), "ta" + abi, GC)
    check("toy root: green 6/6 and accepted", rc == 0 and g["universe"] == 6 == g["certified"] and st[0] == "green", (g, txt))
    print("INFO %s toy gate %.1f s" % (abi, time.time() - t0))
    IE = {"toy/a.c": TR.FILES["toy/a.c"].replace("int f_a(void) {", "extern char __image_end[];\nint f_a(void) { (void)*(volatile char *)__image_end;"),
          "toy/link.ld": TR.FILES["toy/link.ld"].replace("/DISCARD/", "__image_end = .; /DISCARD/")}
    (rc, g, _, txt), st = LS(TR.make_repo("ls-ie-" + abi, rec=TR.abi_rec(abi), files=IE), "ie" + abi, GC)
    check("link script defines __image_end, the root links an ELF: the reference is certified", rc == 0 and g["certified"] == g["universe"] and st[0] == "green", (g, txt))
    (rc, g, _, txt), st = LS(TR.make_repo("ls-ie0-" + abi, rec=TR.abi_rec(abi, TR.mut(link=None, os_layer=["os.S"])), files=IE), "ie0" + abi, GC)
    check("the same objects, the recipe links no ELF: __image_end is undefined", rc == 2 and g["uncertified"] == [TR.ROOT + "/a.c:__image_end"], (g, txt))
# (d) the host boundary: the exports of the pinned host runtime (libc6, libgcc-s1, libstdc++6)
exp, hk = L.host_exports(GC)
check("(d) host_exports: the pinned runtime's exports hold libc, libstdc++ and libgcc_s names and no local name", {"printf", "memcpy", "sqrt", "__cxa_throw", "_Unwind_Resume"} <= set(exp)
      and "fnvnx_nothing" not in exp and len(hk) == 64 and L.host_exports(GC)[1] == hk, (len(exp), hk))
href = build("int printf(const char *, ...);\nint nothere(void);\nint u(void) { return printf(\"x\") + nothere(); }\n")
hs = L.elf_symbols(href)
items, finds = L.judge({"h/o.o": hs}, {}, {}, {}, (["h/"], set(exp)))
check("(d) an object of a host root: printf is certified (exported by the host runtime), the name nothing defines stays undefined", [(o, n, k) for o, n, k, _ in finds] == [("h/o.o", "nothere", "undefined")], finds)
items, finds = L.judge({"g/o.o": hs}, {}, {}, {}, (["h/"], set(exp)))
check("(d) the same object in a guest root (not a host prefix): printf is undefined too, no boundary", sorted(n for _, n, _, _ in finds) == ["nothere", "printf"], finds)
items, finds = L.judge({"h/o.o": hs}, {}, {}, {}, None)
check("(d) without host roots the rule does not exist: printf and nothere are undefined", sorted(n for _, n, _, _ in finds) == ["nothere", "printf"], finds)
HB = '#include "toy.h"\nint printf(const char *, ...);\nint f_b(void) { return printf("x") + TOY; }\n'
(rc, g, _, txt), st = LS(TR.make_repo("ls-host", rec=TR.mut(link=None), files={"toy/b.c": HB}), "t6", GC)
check("gate on a host-target root without a link: a reference to printf is certified, accepted", rc == 0 and g["certified"] == g["universe"] and st[0] == "green", (g, txt))
HW = HB.replace("printf", "nothere_zz")
(rc, g, _, txt), st = LS(TR.make_repo("ls-host2", rec=TR.mut(link=None), files={"toy/b.c": HW}), "t7", GC)
check("gate on a host-target root: a name the host runtime does not export is red", rc == 2 and g["uncertified"] == [TR.ROOT + "/b.c:nothere_zz"], (g, txt))
# (e) outline atomics in guest objects
AT = "int v;\nint f(void) { return __atomic_fetch_add(&v, 1, __ATOMIC_RELAXED); }\n"
ao = build(AT, "c", ["-moutline-atomics"])
an = build(AT, "c", ["-mno-outline-atomics"])
items, finds = L.judge({"g/o.o": L.elf_symbols(ao)}, {}, {}, {}, None, ["g/"])
check("(e) a guest object compiled with -moutline-atomics references __aarch64_ldadd*: finding outline-atomic", [(n, k) for _, n, k, _ in finds if k == "outline-atomic"] == [("__aarch64_ldadd4_relax", "outline-atomic")], finds)
items, finds = L.judge({"g/o.o": L.elf_symbols(an)}, {}, {}, {}, None, ["g/"])
check("(e) the same code with -mno-outline-atomics: no reference, no finding", not finds, finds)
items, finds = L.judge({"h/o.o": L.elf_symbols(ao)}, {}, {}, {}, None, ["g/"])
check("(e) outside a guest root the clause does not apply (an undefined name only)", [k for _, _, k, _ in finds] == ["undefined"], finds)
ABI[0] = "lp64"
print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
sys.exit(1 if FAILED else 0)
