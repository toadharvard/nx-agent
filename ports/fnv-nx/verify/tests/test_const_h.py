#!/usr/bin/env python3
"""hostabi/include/fnvnx/const.h (FNVNX_CONST, FNVNX_CONST_VOID, FNVNX_CONST_IDENT): each macro compiled in C and C++ (a free function, a static function, a member, a const member,
a virtual override), for the ILP32 guest (aarch64 -mabi=ilp32) and the host (x86-64 LP64), and read back by objstub.read_consts (contract/l2consts.py). The sections are PROGBITS with
no SHF_ALLOC. The records equal, byte for byte and relocation for relocation, what manifest/gen_env_const.py's own record templates emit for the same rows.
Plain script, one PASS/FAIL line per case, exit 0 on pass. Needs aarch64-linux-gnu-g++ (env.json) and g++."""
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx)
from fnvnx.verify import objstub  # noqa: E402

TREE = os.path.join(HERE, "..", "..")
INC = os.path.join(TREE, "hostabi", "include")
ROOT = tempfile.mkdtemp(prefix="const-h-")
FAILED = []
N = [0]
TARGETS = (("guest ILP32", ["aarch64-linux-gnu-%s", "-mabi=ilp32"], 4), ("host LP64", ["%s"], 8))


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def build(tgt, lang, src, opt="-O0"):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.%s" % (N[0], "c" if lang == "c" else "cc")), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    cc = [tgt[1][0] % ("gcc" if lang == "c" else "g++")] + tgt[1][1:]
    p = subprocess.run(cc + ["-I" + INC, "-Wall", "-Wextra", "-Werror", opt, "-c", s, "-o", o], capture_output=True, text=True)
    return o, p


def read(o):
    ob = objstub.Obj(o)
    at, bad = objstub.read_consts(ob)
    names = {(s[5], s[1]): s[0] for s in ob.syms if s[3] == 2}
    return ob, {names[k]: v for k, v in at.items()}, bad


C_SRC = """#include "fnvnx/const.h"
int f(void) { return 7; }
static int g(void) { return 9; }
void v(void) {}
int id1(int a, int b) { (void)a; return b; }
FNVNX_CONST(f, int, 7)
FNVNX_CONST(g, int, 9)
FNVNX_CONST_VOID(v)
FNVNX_CONST_IDENT(id1, 1)
FNVNX_CONST(&f, unsigned char, 0xfe)
"""
CC_SRC = """#include "fnvnx/const.h"
struct C { int m(int) const; virtual long vm(); int n(int); static int s(); };
int C::m(int) const { return 5; }
long C::vm() { return 6; }
int C::n(int a) { return a; }
int C::s() { return 3; }
struct D : C { long vm() override { return 8; } };
static int sf() { return 4; }
FNVNX_CONST((void *)(int (*)(const C *, int))&C::m, int, 5)
FNVNX_CONST((void *)(long (*)(C *))&C::vm, long long, 6)
FNVNX_CONST((void *)(long (*)(D *))&D::vm, long, 8)
FNVNX_CONST_IDENT((void *)(int (*)(C *, int))&C::n, 1)
FNVNX_CONST(&C::s, short, 3)
FNVNX_CONST(sf, int, 4)
"""

for tgt in TARGETS:
    label, _, ptr = tgt
    for lang, src in (("c", C_SRC), ("c++", CC_SRC)):
        for opt in ("-O0", "-O2"):
            o, p = build(tgt, lang, src, opt)
            check("%s %s %s: compiles with -Wall -Wextra -Werror" % (label, lang, opt), p.returncode == 0, p.stderr)
            if p.returncode:
                continue
            ob, recs, bad = read(o)
            check("%s %s %s: read back without a malformed record" % (label, lang, opt), not bad, bad)
            secs = {s[0]: s for s in ob.secs}
            check("%s %s %s: .fnvnx.const and .fnvnx.ident are PROGBITS with no SHF_ALLOC" % (label, lang, opt),
                  all(secs[n][1] == 1 and not secs[n][2] & 2 for n in (".fnvnx.const", ".fnvnx.ident")), secs)
            if lang == "c":
                want = {"f": [{"len": 4, "int": 7, "addr": None}, {"len": 1, "int": 0xfe, "addr": None}], "g": [{"len": 4, "int": 9, "addr": None}],
                        "v": [{"len": 0, "int": 0, "addr": None}], "id1": [{"ident": 1}]}
                check("%s c %s: records of a free function, a static function, void and ident" % (label, opt), {k: sorted(v, key=repr) for k, v in recs.items()} == {k: sorted(v, key=repr) for k, v in want.items()}, recs)   # the order of the objects in the section is the compiler's
            else:
                by = {n: r for n, r in recs.items()}
                vals = sorted((tuple(sorted((k, str(x)) for k, x in r.items())) for rs in by.values() for r in rs))
                check("%s c++ %s: six records: member, const member, virtual override, static member, static function, ident" % (label, opt), len(vals) == 6
                      and sum("ident" in r for rs in by.values() for r in rs) == 1, by)
                check("%s c++ %s: the member symbols are the compiler's own (_ZN1C...)" % (label, opt),
                      {"_ZNK1C1mEi", "_ZN1C2vmEv", "_ZN1D2vmEv", "_ZN1C1nEi", "_ZN1C1sEv", "_ZL2sfv"} <= set(by), sorted(by))
                lens = {n: rs[0].get("len", "id") for n, rs in by.items()}
                check("%s c++ %s: sizeof(type) is the length of each record" % (label, opt), lens["_ZN1C2vmEv"] == 8 and lens["_ZN1C1sEv"] == 2 and lens["_ZNK1C1mEi"] == 4, lens)

# the same record, byte for byte, as manifest/gen_env_const.py's own template for the same row
spec = importlib.util.spec_from_file_location("gen_env_const", os.path.join(TREE, "manifest", "gen_env_const.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
for tgt in TARGETS:
    label, _, ptr = tgt
    rows = ("int f(void) { return 7; }\nlong long g(void) { return 6; }\nint h(int a, int b) { (void)a; return b; }\n")
    mac = '#include "fnvnx/const.h"\n' + rows + "FNVNX_CONST(f, int, 7)\nFNVNX_CONST(g, long long, 6)\nFNVNX_CONST_IDENT(h, 1)\n"
    ref = '#include "fnvnx/const.h"\n' + gen.SA_DEF.replace("static_assert\n", "static_assert\n") + rows
    ref += gen.RECORD % {"n": 0, "ret": "int", "fnty": "int (*)(void)", "addr": "f", "value": "7"}
    ref += gen.RECORD % {"n": 1, "ret": "long long", "fnty": "long long (*)(void)", "addr": "g", "value": "6"}
    ref += gen.IDENT_RECORD % {"n": 2, "fnty": "int (*)(int, int)", "addr": "h", "k": 1}
    a, pa = build(tgt, "c++", mac)
    b, pb = build(tgt, "c++", ref)
    check("%s: both the macro and gen_env_const's template compile" % label, pa.returncode == 0 and pb.returncode == 0, (pa.stderr, pb.stderr))
    if pa.returncode or pb.returncode:
        continue
    oa, ob_ = objstub.Obj(a), objstub.Obj(b)

    def dump(ob):
        out = {}
        for si, s in enumerate(ob.secs):
            if s[0] in (".fnvnx.const", ".fnvnx.ident"):
                out[s[0]] = (ob.data[s[3]:s[3] + s[4]], sorted((off, ob.syms[r[0]][0], r[1], r[2]) for off, r in ob.rela.get(si, {}).items()), s[2])
        return out
    check("%s: the macro's records equal gen_env_const's byte for byte (bytes, relocations, flags)" % label, dump(oa) == dump(ob_), (dump(oa), dump(ob_)))

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
