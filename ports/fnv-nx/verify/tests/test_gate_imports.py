#!/usr/bin/env python3
"""Tests for verify/gate_imports.py (class 4): the driver on the toy product root of test_recipe.py (reused by import) with a synthetic manifest
published as an external of a stand-in artifacts repository; the pure rules are tested by contract/tests/test_imports.py.
The bridge table is stage B: its function rows come from the `.fnvnx.export` records of the root's objects, read by the exports root (contract/exports) that the
gate builds. The toy carries the real exportgen.py (with the files it imports), fnvnx_export.h and decltype_spell.inc; the toy units hold FNVNX_EXPORT records
(and hand-assembled `.fnvnx.decltype` rows, the rows the layout plugin writes in a product build), and the toy exports root is the real recipe with the one
difference that it selects the objects of host roots (the toy compiler is the stock one, so its root is a host root).
Needs the farm's declared environment (gcc, pyelftools in the pinned runtime). Plain script: PASS/FAIL lines, exit 0 on pass."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import gate_imports as G  # noqa: E402
import linkset  # noqa: E402
import test_recipe as TR  # noqa: E402
import verdict  # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:500]))
    if not ok:
        FAILED.append(name)


# symbols(): defined + referenced
res = {"defined": [{"symbol": "a", "bind": "GLOBAL", "type": "FUNC", "object": "x.o"}, {"symbol": "a", "bind": "WEAK", "type": "FUNC", "object": "y.o"}],
       "undefined": ["b"]}
check("symbols: defined entries and referenced names", G.symbols(res) == {"a": [("GLOBAL", "FUNC"), ("WEAK", "FUNC")], "b": [("UND", "NOTYPE")]}, G.symbols(res))

TR.recipe.ARTIFACTS = TR.BASE + "/artifacts.git"
TR.subprocess.run(["git", "init", "-q", "--bare", TR.BASE + "/artifacts.git"], check=True)
GC = TR.BASE + "/cache"


def run(repo, tag):
    rc, g, _, txt = TR.gate(repo, "imports", TR.BASE + "/o-" + tag, GC)
    return rc, g, txt, TR.accepted("imports", TR.BASE + "/o-" + tag)


# no product root: universe 0, green, accepted by the verdict
empty = TR.make_repo("im-empty")
TR.w(empty, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": TR.T + "toy", "class": "doc"}]}))
rc, g, txt, acc = run(empty, "none")
check("no product root: universe 0, green, accepted by the verdict", rc == 0 and g["universe"] == 0 and g["status"] == "green" and acc[0] == "green", (g, txt))

rc, g, txt, acc = run(TR.make_repo("im-undeclared"), "undecl")
check("product root without imports.in.json: red, one item", rc == 2 and g["status"] == "red" and g["universe"] == 1 and "imports.in.json" in g["detail"], (g, txt))

# header-only root (gate_build.unitless): no unit and no link, so it holds no code and has no imports by construction; it needs no imports.in.json
# and counts 0 items. The same root with one unit added and still no imports.in.json is red again (one item): the rule is "no code", not "no file".
HO = {"format": "fnvnx-recipe/1", "compiler": TR.RECIPE["compiler"], "flags": TR.RECIPE["flags"], "include": [TR.ROOT + "/inc"], "units": "all", "link": None}
HO_FILES = {"toy/a.c": None, "toy/b.c": None, "toy/c.c": None, "toy/os.S": None, "toy/link.ld": None, "toy/constants.tsv": None}
rc, g, txt, acc = run(TR.make_repo("im-hdronly", rec=HO, files=HO_FILES), "hdronly")
check("header-only product root without imports.in.json: 0 items, green", rc == 0 and g["status"] == "green" and g["universe"] == 0, (g, txt))
rc, g, txt, acc = run(TR.make_repo("im-hdrunit", rec=HO, files=dict(HO_FILES, **{"toy/c.c": TR.FILES["toy/c.c"]})), "hdrunit")
check("the same root with one unit and no imports.in.json: red, one item", rc == 2 and g["status"] == "red" and g["universe"] == 1, (g, txt))

SPEC = {"toy.spec": "1 stdcall f_a()\n2 stdcall f_b(long)\n3 stdcall f_c(long)\n4 extern ctab\n", "msvcrt.spec": "1 cdecl memcpy(ptr ptr long)\n2 cdecl strlen(str)\n"}

REAL = os.path.join(HERE, "..", "..")   # ports/fnv-nx of the tree under test
T = TR.T
EXPORT_FILES = ("contract/exportgen.py", "contract/dllkind.py", "contract/l2sections.py", "link/ctypes_compat.py", "toolchain/layout/decltype_spell.inc", "contract/include/fnvnx_export.h")
# the toy units: (file, definer, decltype, [(module, import name)]); a record is dropped by naming it in `drop`
UNITS = (("toy/a.c", "f_a", "()int", [("toy.dll", "f_a")]), ("toy/b.c", "f_b", "()int", [("toy.dll", "f_b"), ("msvcrt.dll", "memcpy")]),
         ("toy/c.c", "f_c", "(int)int", [("toy.dll", "f_c"), ("msvcrt.dll", "strlen")]))


def decl_row(sym, typ):
    return '__asm__(".pushsection .fnvnx.decltype,\\"\\",@progbits\\n\\t.ascii \\"%s\\\\t%s\\\\n\\"\\n\\t.popsection");\n' % (sym, typ)


def export_files(drop=(), extra=""):
    """toy units with their FNVNX_EXPORT records: the original unit text, the decltype row of the definer and one record per (module, name) not dropped"""
    out = {}
    for f, sym, typ, recs in UNITS:
        text = TR.FILES[f].replace('#include "toy.h"\n', '#include "toy.h"\n#include "fnvnx_export.h"\n', 1) + decl_row(sym, typ)
        text += "".join('FNVNX_EXPORT("%s","%s",%s,"func");\n' % (m, n, sym) for m, n in recs if (m, n) not in drop)
        out[f] = text + (extra if f == "toy/a.c" else "")
    return out


def tree_files():
    """the real files of the tree that the toy exports root needs, and its recipe: the tree's own contract/exports/build.json with the objects selector of the toy's host target"""
    out = {f: open(os.path.join(REAL, f)).read() for f in EXPORT_FILES}
    rec = json.load(open(os.path.join(REAL, "contract/exports/build.json")))
    for g in rec["generated"]:
        g["objects_of"]["target"] = "host"
    out["contract/exports/build.json"] = json.dumps(rec)
    return out


def manifest(names):
    def pop(d):
        os.makedirs(d + "/specs")
        open(d + "/sys_names.tsv", "w").write(names)
        for k, v in SPEC.items():
            open(d + "/specs/" + k, "w").write(v)
    return pop


def toy_repo(tag, names, drop=(), decl_extra=None, extra_unit=""):
    commit, thash = TR.publish("im-" + tag, manifest(names))
    ex = {"name": "manifest", "repo": "fnv-nx-build", "ref": "im-" + tag, "commit": commit, "tree_sha256": thash, "at": "/ext/manifest"}
    rec = dict(TR.RECIPE, external=[ex], include=TR.RECIPE["include"] + [T + "contract/include"], uses=[{"root": T + "contract/include", "output": "INCLUDE"}])
    decl = {"format": "fnvnx-imports-in/1", "names": {"external": "manifest", "path": "sys_names.tsv"}, "specs": {"external": "manifest", "path": "specs"}, "extra": [], "extra_other": [],
            "va": "msvcrt_va.tsv"}
    decl.update(decl_extra or {})
    roots = [{"path": T + "contract/include", "class": "header"}, {"path": T + "contract/exports", "class": "tool", "target": "host", "entry": [], "recipe": T + "contract/exports/build.json"}]
    files = dict(tree_files(), **export_files(drop, extra_unit))
    files.update({"toy/imports.in.json": json.dumps(decl), "toy/msvcrt_va.tsv": ""})
    return TR.make_repo("im-" + tag, rec=rec, files=files, extra_roots=roots)


# green: every import has an export record in the toy objects (f_a, f_b, f_c global functions; the records serve memcpy by f_b, strlen by f_c)
repo = toy_repo("ok", "toy.dll\tf_a\ntoy.dll\tf_b\nmsvcrt.dll\tmemcpy\nmsvcrt.dll\tstrlen\n")
rc, g, txt, acc = run(repo, "ok")
check("green: link rows proven by the export records and the link set's definers", rc == 0 and g["status"] == "green" and (g["universe"], g["certified"]) == (4, 4) and acc[0] == "green", (g, txt))

# red: a row whose definer is not proven: a data import (extern in the spec) with no export record and no symbol of its name in the build is class `none`
repo = toy_repo("bad", "toy.dll\tf_a\ntoy.dll\tctab\n")
rc, g, txt, acc = run(repo, "bad")
ids = open(TR.BASE + "/o-bad/imports.ids").read().split("\n") if os.path.exists(TR.BASE + "/o-bad/imports.ids") else []
check("red: a data import with no record and no symbol of its name", rc == 2 and g["status"] == "red" and (g["universe"], g["certified"]) == (2, 1) and ids[:1] == ["toy.dll!ctab\tnone"] and acc[0] == "red", (g, ids, txt))

# red: a function import with no export record has no source for its native symbol (a bridge error: the whole declaration is one red item with the reason)
repo = toy_repo("norec", "toy.dll\tf_a\ntoy.dll\tf_c\n", drop=[("toy.dll", "f_c")])
rc, g, txt, acc = run(repo, "norec")
check("a function import with no export record is red with the reason", rc == 2 and g["status"] == "red" and "no export record" in g["detail"], (g, txt))

# red: an export record given twice is an error of the exports stage (one definer per import)
repo = toy_repo("duprec", "toy.dll\tf_a\n", extra_unit='FNVNX_EXPORT("toy.dll","f_a",f_a,"func");\n')
rc, g, txt, acc = run(repo, "duprec")
check("an export record given twice is red", rc == 2 and g["status"] == "red", (g, txt))

# red: the retired `name_table` key is no key of the declaration any more; bad input is red with the reason
repo = toy_repo("badtab", "msvcrt.dll\tmemcpy\n", decl_extra={"name_table": "msvcrt_names.tsv"})
rc, g, txt, acc = run(repo, "badtab")
check("the retired name_table key is red with the reason", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))
repo = toy_repo("badin", "toy.dll\tf_a\n", decl_extra={"extra": [{"external": "manifest", "path": "missing.tsv"}]})
rc, g, txt, acc = run(repo, "badin")
check("missing declared input is red", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))
repo = toy_repo("badext", "toy.dll\tf_a\n", decl_extra={"names": {"external": "nonexistent", "path": "sys_names.tsv"}})
rc, g, txt, acc = run(repo, "badext")
check("declared external that the recipe lacks is red", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))
repo = toy_repo("badkeys", "toy.dll\tf_a\n", decl_extra={"bogus": 1})
rc, g, txt, acc = run(repo, "badkeys")
check("imports.in.json with unknown keys is red", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))

# class import-provider (a fact of the build's symbol table): a second product root that declares nothing is outside the gate's universe when it
# defines no native symbol of the bridge table (the loader, lib/sha256), and is the one red item when it defines one. The native symbols are the definers
# of the export records plus the data imports served by their own name: the member here is the data object `ctab` (a function symbol of a record cannot be
# defined by a second root: the exports stage allows one definer in the tree).
def two_roots(tag, body, names="toy.dll\tf_a\ntoy.dll\tf_b\ntoy.dll\tctab\nmsvcrt.dll\tmemcpy\nmsvcrt.dll\tstrlen\n"):
    repo = toy_repo(tag, names)
    TR.w(repo, "toy/own.c", "int ctab = 1;\n")
    rec2 = {"format": "fnvnx-recipe/1", "compiler": TR.RECIPE["compiler"], "flags": TR.RECIPE["flags"], "include": [], "units": "all", "link": None}
    TR.w(repo, "toy2/x.c", body)
    TR.w(repo, "toy2/build.json", json.dumps(rec2))
    ad = json.load(open(repo + "/" + TR.T + "verify/admit.json"))
    ad["roots"].append({"path": TR.T + "toy2", "class": "product", "target": "host", "recipe": TR.T + "toy2/build.json"})
    TR.w(repo, "verify/admit.json", json.dumps(ad))
    TR.git(repo, "add", "-A")
    TR.git(repo, "commit", "-q", "-m", "toy2")
    return repo


rc, g, txt, acc = run(two_roots("prov-out", "int loader_only(void) { return 1; }\n"), "provout")
check("non-member: a root that defines no bridge symbol is outside the class, 0 items; the declaring root is judged as before",
      rc == 0 and g["status"] == "green" and (g["universe"], g["certified"]) == (5, 5), (g, txt))
rc, g, txt, acc = run(two_roots("prov-in", "int ctab = 2;\n"), "provin")
check("a root that enters the class by defining ctab and has no imports.in.json is the one red item",
      rc == 2 and g["status"] == "red" and (g["universe"], g["certified"]) == (6, 5) and TR.T + "toy2:imports.in.json" in g["uncertified"] and "ctab" in g["detail"], (g, txt))


print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
