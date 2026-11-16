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
import test_plugins as TP  # noqa: E402
import test_recipe as TR  # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:600]))
    if not ok:
        FAILED.append(name)


N = [0]
TEMPLATE = "template <class T> struct Box { T v; __attribute__((noinline)) T get() const { return v; } };\nint use%s(Box<int>& b) { return b.get(); }\n"


def run(files):
    """Build the toy native C++ root (test_plugins: both plugins, no link) with FILES by the recipe; -> (build status, odr.check (universe, red),
    linkstrict gate result, its text)."""
    N[0] += 1
    repo = TP.make_repo("cd%d" % N[0], TP.REC, "native", files=dict({TP.ROOT + "/a.cc": None}, **{TP.ROOT + "/" + k[4:]: v for k, v in files.items()}))
    cache = os.path.join(TP.BASE, "cdcache")
    res, _ = gate_build.ensure(TP.ROOT, cache, repo)
    objs = [o["object"] for o in res["objects"] if o["unit"].endswith(recipe.CXX_EXT)]
    out = os.path.join(TP.BASE, "cdout%d" % N[0])
    rc, g, _, txt = TR.gate(repo, "linkstrict", out, cache)
    return res["status"], odr.check(objs), g, txt


# 1. two units instantiate the same template
st, (uni, red), g, txt = run({"toy/x1.cc": TEMPLATE % "A", "toy/x2.cc": TEMPLATE % "B"})
check("two units instantiating one template: the COMDAT is one definition (odr green, signature in the universe)",
      st == "green" and uni and not red, (st, uni, red))
check("... linkstrict has no comdat finding", g["status"] == "green", (g, txt))

# 2. different flags (an optimisation level by pragma) give different bytes: red, both copies named
OPT = "#pragma GCC optimize(\"O0\")\n"
st, (uni, red), g, txt = run({"toy/x1.cc": TEMPLATE % "A", "toy/x2.cc": OPT + TEMPLATE % "B"})
check("a unit built with another optimisation level (O0 against the root's O2): bytes differ, the definition records are equal (tier 2): odr green", st == "green" and not red, (uni, red))
check("... linkstrict green (one entity by its record)", g["status"] == "green", (g, txt))
# 2b. a different token sequence in the same inline function: tier 2 differs, red in both gates, both objects named
DIFF = TEMPLATE.replace("return v;", "return v + 0;")
st, (uni, red), g, txt = run({"toy/x1.cc": TEMPLATE % "A", "toy/x2.cc": OPT + DIFF % "B"})
check("a different token sequence: odr red `comdat-differs`, both objects named", any(r.startswith("comdat-differs") and "x1.cc" in r and "x2.cc" in r for r in red.values()), (uni, red))
check("... linkstrict: comdat-differs", g["status"] == "red" and "comdat-differs" in g["detail"], (g, txt))


def asm(body):
    return "__asm__(%s);\n" % json.dumps(body)


# 3. a section symbol of a member of the same group
INGRP = (".section .text.sigA,\"axG\",@progbits,sigA,comdat\n.weak sigA\n.type sigA,%function\nsigA:\n adrp x0, .Lloc\n add x0, x0, :lo12:.Lloc\n ret\n"
         ".section .data.sigA,\"awG\",@progbits,sigA,comdat\n.balign 4\n.Lpad: .word 1\n.Lloc: .word 2\n")
st, (uni, red), g, txt = run({"toy/x1.cc": asm(INGRP), "toy/x2.cc": asm(INGRP)})
check("relocation by a section symbol inside the group: green", st == "green" and "sigA" in uni and not red and g["status"] == "green", (uni, red, g, txt))

# 4. a section symbol outside the group resolves to the ONE global/weak symbol defined there (its name); none: identity-undecidable
HEAD = ".section .text.sigB,\"axG\",@progbits,sigB,comdat\n.weak sigB\n.type sigB,%function\nsigB:\n adrp x0, .Lout\n add x0, x0, :lo12:.Lout\n ret\n"
SIGC = ".section .text.sigC,\"axG\",@progbits,sigC,comdat\n.weak sigC\n.type sigC,%function\nsigC:\n.Lout: ret\n"
st, (uni, red), g, txt = run({"toy/x1.cc": asm(HEAD + SIGC), "toy/x2.cc": asm(HEAD + SIGC)})
check("a section symbol into ANOTHER group, one weak symbol at that offset, identical in both objects: green, the name is a fact (unique across identical copies)",
      st == "green" and "name:sigC" in uni and "sigB" in uni and not red and g["status"] == "green", (st, uni, red, g, txt))
DATA = ".data\n.balign 4\n.word 7\n"
one = lambda g: HEAD + DATA + ".globl %s\n%s:\n.Lout: .word 9\n" % (g, g)
st, (uni, red), g, txt = run({"toy/x1.cc": asm(one("gv")), "toy/x2.cc": asm(one("gv"))})
check("... the name defined outside any COMDAT group by two objects: red `name-resolved target not unique` (odr and linkstrict)",
      "not unique" in red.get("name:gv", "") and "name-resolved-not-unique" in g["detail"], (red, g, txt))
st, (uni, red), g, txt = run({"toy/x1.cc": asm(one("gv1")), "toy/x2.cc": asm(one("gv2"))})
check("... the same relocation naming two different symbols: the copies differ (red, both objects named)",
      "x1.cc" in red.get("sigB", "") and "x2.cc" in red["sigB"], (uni, red))
st, (uni, red), g, txt = run({"toy/x1.cc": asm(HEAD + DATA + ".Lout: .word 9\n"), "toy/x2.cc": asm(HEAD + DATA + ".Lout: .word 9\n")})
check("a section symbol outside the group with no symbol there: odr red, identity-undecidable, names object/section/offset",
      any(r.startswith("identity-undecidable") and "x1.cc" in r and ".data" in r and "offset 4" in r for r in red.values()), (uni, red))
check("... linkstrict: identity-undecidable", g["status"] == "red" and "identity-undecidable" in g["detail"], (g, txt))

st, (uni, red), g, txt = run({"toy/x1.cc": asm(HEAD + DATA + ".Lout: .word 9\n")})
check("... the same group in ONE object only: nothing to compare, not red", "sigB" not in uni and g["status"] == "green", (uni, red, g, txt))

# 4c. rule E: a relocation to a LOCAL sized symbol outside the group (a static function, an .isra clone): the entity is compared by bisimulation, cycles identical
HEADE = (".section .text.sigE,\"axG\",@progbits,sigE,comdat\n.weak sigE\n.type sigE,%function\nsigE:\n adrp x0, ea\n add x0, x0, :lo12:ea\n ret\n")
LOCE = lambda eb: (".section .text.ea,\"ax\"\n.type ea,%function\nea: bl eb\n ret\n.size ea,8\n.section .text.eb,\"ax\"\n.type eb,%function\neb:" + eb + "\n.size eb,16\n")
MUT = " bl ea\n ret\n nop\n nop"
st, (uni, red), g, txt = run({"toy/x1.cc": asm(HEADE + LOCE(MUT)), "toy/x2.cc": asm(HEADE + LOCE(MUT))})
check("a mutually recursive pair of local functions, identical in both objects: green (bisimulation)", "sigE" in uni and not red and g["status"] == "green", (uni, red, g, txt))
st, (uni, red), g, txt = run({"toy/x1.cc": asm(HEADE + LOCE(MUT)), "toy/x2.cc": asm(HEADE + LOCE(" bl ea\n ret\n nop\n brk #1"))})
check("... one callee body differs: red, both objects named", "x1.cc" in red.get("sigE", "") and "x2.cc" in red["sigE"], (uni, red))
U = lambda n: '.section .text.c,"ax",@progbits,unique,%d\n' % n
REF = lambda x: " adrp x1, %s\n add x1, x1, :lo12:%s\n ret\n" % (x, x)   # lo12 relocations are always emitted, even inside one section
TWO = HEADE + U(1) + ".type ea,%function\nea:" + REF("eb") + ".size ea,12\n" + U(2) + ".type eb,%function\neb:" + REF("ea") + ".size eb,12\n"
ONE = HEADE + U(1) + ".type ea,%function\nea:" + REF("ea") + ".size ea,12\n"
st, (uni, red), g, txt = run({"toy/x1.cc": asm(TWO), "toy/x2.cc": asm(ONE)})
check("... a cycle of two against a self-loop of the same body: bisimilar, green (greatest fixed point)", "sigE" in uni and not red, (uni, red))

# 4b. the symbols a group defines are part of its identity: same bytes, another size or visibility is another definition
SYM = lambda extra: (".section .text.sigD,\"axG\",@progbits,sigD,comdat\n.weak sigD\n" + extra + ".type sigD,%function\nsigD: ret\n")
st, (uni, red), g, txt = run({"toy/x1.cc": asm(SYM("") + ".size sigD, 4\n"), "toy/x2.cc": asm(SYM("") + ".size sigD, 4\n")})
check("same bytes, same symbols: green", "sigD" in uni and not red, (uni, red))
st, (uni, red), g, txt = run({"toy/x1.cc": asm(SYM("") + ".size sigD, 4\n"), "toy/x2.cc": asm(SYM("") + ".size sigD, 8\n")})
check("same bytes, another st_size: red, both digests named", "sigD" in red and "x1.cc" in red["sigD"] and "x2.cc" in red["sigD"], (uni, red))
st, (uni, red), g, txt = run({"toy/x1.cc": asm(SYM("") + ".size sigD, 4\n"), "toy/x2.cc": asm(SYM(".hidden sigD\n") + ".size sigD, 4\n")})
check("same bytes, a hidden against a default signature: red; linkstrict undecidable", "sigD" in red and "undecidable: no definition record" in g["detail"], (uni, red, g))

# 5. typeinfo and DW.ref groups (exceptions + RTTI need the unwinder symbols: only the typeinfo of an object is compared, nothing is linked to run)
TI = "struct S { virtual ~S(); virtual int f(); };\nint use%s(S* s) { return s->f(); }\nstruct P : S { int f() { return 1; } };\nint t%s(S* s) { return dynamic_cast<P*>(s) != 0; }\n"
st, (uni, red), g, txt = run({"toy/x1.cc": TI % ("A", "A"), "toy/x2.cc": TI % ("B", "B")})
check("typeinfo (_ZTI) and vtable groups of two units: green", any(u.startswith("_ZTI") for u in uni) and not red, (uni, red))
DW = lambda: (".section .data.rel.ro.DW.ref.__gxx_personality_v0,\"awG\",@progbits,DW.ref.__gxx_personality_v0,comdat\n.balign 4\n"
              ".weak DW.ref.__gxx_personality_v0\nDW.ref.__gxx_personality_v0:\n.word __gxx_personality_v0\n")
st, (uni, red), g, txt = run({"toy/x1.cc": asm(DW()), "toy/x2.cc": asm(DW())})
check("a DW.ref group (pointer to a named symbol): green", "DW.ref.__gxx_personality_v0" in uni and not red, (uni, red))

print("FAILED: %d" % len(FAILED) if FAILED else "OK")
sys.exit(1 if FAILED else 0)
