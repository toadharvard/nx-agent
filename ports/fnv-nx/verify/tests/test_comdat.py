#!/usr/bin/env python3
"""Tests of link/comdat.py comdat_identity as used by gates odr/abi (link/odr.py) and linkstrict (verify/gate_linkstrict.py): toy product roots
built by the recipe (test_recipe.make_repo, the verdict's own build), never by hand-compiling. Plain script: PASS/FAIL lines, exit 0 on pass.
Cases: two units instantiating one template -> the identical COMDAT is one definition; another optimisation level (flags) in one unit -> red, both
copies named; a section-symbol relocation inside the group -> green; one outside the group with exactly one global symbol at that offset -> green,
with none -> `identity-undecidable`; typeinfo (_ZTI) and DW.ref groups -> green."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path[:0] = [VERIFY, HERE, os.path.join(VERIFY, "..", "link"), os.path.join(VERIFY, "..", "contract")]
import gate_build  # noqa: E402
import odr  # noqa: E402
import recipe  # noqa: E402
import test_recipe as TR  # noqa: E402
import verdict  # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:600]))
    if not ok:
        FAILED.append(name)


N = [0]
TEMPLATE = "template <class T> struct Box { T v; T get() const { return v; } };\nint use%s(Box<int>& b) { return b.get(); }\n"


def run(files, rec=None):
    """Build the toy root with FILES by the recipe; -> (odr (universe, red), linkstrict gate result, odr+linkstrict statuses)."""
    N[0] += 1
    repo = TR.make_repo("cd%d" % N[0], rec=rec, files=files)
    cache = os.path.join(TR.BASE, "cdcache")
    res, _ = gate_build.ensure(TR.ROOT, cache, repo)
    objs = [o["object"] for o in res["objects"] if o["unit"].endswith(recipe.CXX_EXT)]
    out = os.path.join(TR.BASE, "cdout%d" % N[0])
    rc, g, _, txt = TR.gate(repo, "linkstrict", out, cache)
    return res["status"], odr.check(objs), g, txt


# 1. two units instantiate the same template
st, (uni, red), g, txt = run({"toy/x1.cc": TEMPLATE % "A", "toy/x2.cc": TEMPLATE % "B"})
check("two units instantiating one template: the COMDAT is one definition (odr green, signature in the universe)",
      st == "green" and uni and not red, (st, uni, red))
check("... linkstrict has no comdat finding", g["status"] == "green", (g, txt))

# 2. different flags (an optimisation level by pragma) give different bytes: red, both copies named
OPT = "#pragma GCC optimize(\"O2\")\n"
st, (uni, red), g, txt = run({"toy/x1.cc": TEMPLATE % "A", "toy/x2.cc": OPT + TEMPLATE % "B"})
check("a unit built with another optimisation level: odr red, both objects named", any("x1.cc" in r and "x2.cc" in r for r in red.values()), (uni, red))
check("... linkstrict: comdat-differs", g["status"] == "red" and "comdat-differs" in g["detail"], (g, txt))


def asm(body):
    return "__asm__(%s);\n" % json.dumps(body)


# 3. a section symbol of a member of the same group
INGRP = (".section .text.sigA,\"axG\",@progbits,sigA,comdat\n.weak sigA\n.type sigA,%function\nsigA:\n adrp x0, .Lloc\n add x0, x0, :lo12:.Lloc\n ret\n"
         ".section .data.sigA,\"awG\",@progbits,sigA,comdat\n.balign 4\n.Lpad: .word 1\n.Lloc: .word 2\n")
st, (uni, red), g, txt = run({"toy/x1.cc": asm(INGRP), "toy/x2.cc": asm(INGRP)})
check("relocation by a section symbol inside the group: green", st == "green" and "sigA" in uni and not red and g["status"] == "green", (uni, red, g, txt))

# 4. a section symbol outside the group resolves to the ONE global symbol defined there (its name); no such symbol: identity-undecidable
HEAD = ".section .text.sigB,\"axG\",@progbits,sigB,comdat\n.weak sigB\n.type sigB,%function\nsigB:\n adrp x0, .Lout\n add x0, x0, :lo12:.Lout\n ret\n"
DATA = ".data\n.balign 4\n.word 7\n"
one = lambda g: HEAD + DATA + ".globl %s\n%s:\n.Lout: .word 9\n" % (g, g)
st, (uni, red), g, txt = run({"toy/x1.cc": asm(one("gv")), "toy/x2.cc": asm(one("gv").replace(".word 7", ".word 8"))})
check("a section symbol outside the group, one global symbol at that offset in each copy, the same name: green (other data of the section is not the copy's)",
      st == "green" and "sigB" in uni and not red, (st, uni, red))
st, (uni, red), g, txt = run({"toy/x1.cc": asm(one("gv1")), "toy/x2.cc": asm(one("gv2")).replace("gv2", "gv2")})
check("... the same relocation naming two different symbols: the copies differ (red, both objects named)",
      any("x1.cc" in r and "x2.cc" in r for r in red.values()) and "identity-undecidable" not in "".join(red.values()), (uni, red))
st, (uni, red), g, txt = run({"toy/x1.cc": asm(HEAD + DATA + ".Lout: .word 9\n"), "toy/x2.cc": asm(HEAD + DATA + ".Lout: .word 9\n")})
check("a section symbol outside the group with no symbol there: odr red, identity-undecidable, names object/section/offset",
      any(r.startswith("identity-undecidable") and "x1.cc" in r and ".data" in r and "offset 4" in r for r in red.values()), (uni, red))
check("... linkstrict: identity-undecidable", g["status"] == "red" and "identity-undecidable" in g["detail"], (g, txt))

# 5. typeinfo and DW.ref groups (exceptions + RTTI need the unwinder symbols: only the typeinfo of an object is compared, nothing is linked to run)
TI = "struct S { virtual ~S(); virtual int f(); };\nint use%s(S* s) { return s->f(); }\nstruct P : S { int f() { return 1; } };\nint t%s(S* s) { return dynamic_cast<P*>(s) != 0; }\n"
st, (uni, red), g, txt = run({"toy/x1.cc": TI % ("A", "A"), "toy/x2.cc": TI % ("B", "B")})
check("typeinfo (_ZTI) and vtable groups of two units: green", any(u.startswith("_ZTI") for u in uni) and not red, (uni, red))
DW = lambda: (".section .data.rel.ro.DW.ref.__gxx_personality_v0,\"awG\",@progbits,DW.ref.__gxx_personality_v0,comdat\n.balign 8\n"
              ".weak DW.ref.__gxx_personality_v0\nDW.ref.__gxx_personality_v0:\n.xword __gxx_personality_v0\n")
st, (uni, red), g, txt = run({"toy/x1.cc": asm(DW()), "toy/x2.cc": asm(DW())})
check("a DW.ref group (pointer to a named symbol): green", "DW.ref.__gxx_personality_v0" in uni and not red, (uni, red))

print("FAILED: %d" % len(FAILED) if FAILED else "OK")
sys.exit(1 if FAILED else 0)
