#!/usr/bin/env python3
"""Tests for verify/gate_imports.py (class 4): the driver on the toy product root of test_recipe.py (reused by import) with a synthetic manifest
published as an external of a stand-in artifacts repository; the pure rules are tested by contract/tests/test_imports.py.
Plain script: PASS/FAIL lines, exit 0 on pass."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import gate_imports as G  # noqa: E402
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

os.environ["ARTIFACTS_URL"] = TR.BASE + "/artifacts.git"
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

SPEC = {"toy.spec": "1 stdcall f_a()\n2 stdcall f_b(long)\n3 stdcall f_c(long)\n", "msvcrt.spec": "1 cdecl memcpy(ptr ptr long)\n2 cdecl strlen(str)\n"}


def manifest(names):
    def pop(d):
        os.makedirs(d + "/specs")
        open(d + "/sys_names.tsv", "w").write(names)
        for k, v in SPEC.items():
            open(d + "/specs/" + k, "w").write(v)
    return pop


def toy_repo(tag, names, table, decl_extra=None):
    commit, thash = TR.publish("im-" + tag, manifest(names))
    ex = {"name": "manifest", "repo": "fnv-nx-build", "ref": "im-" + tag, "commit": commit, "tree_sha256": thash, "at": "/ext/manifest"}
    rec = dict(TR.RECIPE, external=[ex])
    decl = {"format": "fnvnx-imports-in/1", "names": {"external": "manifest", "path": "sys_names.tsv"}, "specs": {"external": "manifest", "path": "specs"}, "extra": [], "extra_other": [],
            "name_table": "msvcrt_names.tsv"}
    decl.update(decl_extra or {})
    return TR.make_repo("im-" + tag, rec=rec, files={"toy/imports.in.json": json.dumps(decl), "toy/msvcrt_names.tsv": table})


# green: every import has a body in the toy objects (f_a, f_b, f_c global functions; the name table serves memcpy by f_b, strlen by f_c)
repo = toy_repo("ok", "toy.dll\tf_a\ntoy.dll\tf_b\nmsvcrt.dll\tmemcpy\nmsvcrt.dll\tstrlen\n", "memcpy\tf_b\tfunc\nstrlen\tf_c\tfunc\n")
rc, g, txt, acc = run(repo, "ok")
check("green: sym and lookup rows proven by the objects.json of the build", rc == 0 and g["status"] == "green" and (g["universe"], g["certified"]) == (4, 4) and acc[0] == "green", (g, txt))

# red: a name with no symbol (none), a lookup entry naming an undefined symbol (lookup-miss), an msvcrt name outside the table (lookup-miss)
repo = toy_repo("bad", "toy.dll\tf_a\ntoy.dll\tf_c\nmsvcrt.dll\tmemcpy\nmsvcrt.dll\tstrlen\n", "memcpy\tno_such_symbol\tfunc\n")
rc, g, txt, acc = run(repo, "bad")
ids = open(TR.BASE + "/o-bad/imports.ids").read().split("\n") if os.path.exists(TR.BASE + "/o-bad/imports.ids") else []
check("red: lookup entry without a proven symbol and an unserved msvcrt name", rc == 2 and g["status"] == "red" and g["universe"] == 4 and g["certified"] == 2
      and ids[:2] == ["msvcrt.dll!memcpy\tlookup-miss", "msvcrt.dll!strlen\tlookup-miss"] and acc[0] == "red", (g, ids, txt))

# red: a name table entry of the wrong kind (func entry naming an object) is lookup-miss; bad input is red with the reason
repo = toy_repo("badtab", "msvcrt.dll\tmemcpy\n", "memcpy\tf_b\n")
rc, g, txt, acc = run(repo, "badtab")
check("bad name table is red with the reason", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))
repo = toy_repo("badin", "toy.dll\tf_a\n", "", {"extra": [{"external": "manifest", "path": "missing.tsv"}]})
rc, g, txt, acc = run(repo, "badin")
check("missing declared input is red", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))
repo = toy_repo("badext", "toy.dll\tf_a\n", "", {"names": {"external": "nonexistent", "path": "sys_names.tsv"}})
rc, g, txt, acc = run(repo, "badext")
check("declared external that the recipe lacks is red", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))
repo = toy_repo("badkeys", "toy.dll\tf_a\n", "", {"bogus": 1})
rc, g, txt, acc = run(repo, "badkeys")
check("imports.in.json with unknown keys is red", rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))

# class import-provider (a fact of the build's symbol table): a second product root that declares nothing is outside the gate's universe when it
# defines no native symbol of the bridge table (the loader, lib/sha256), and is the one red item when it defines one (f_a here).
def two_roots(tag, body):
    repo = toy_repo(tag, "toy.dll\tf_a\ntoy.dll\tf_b\nmsvcrt.dll\tmemcpy\nmsvcrt.dll\tstrlen\n", "memcpy\tf_b\tfunc\nstrlen\tf_c\tfunc\n")
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
      rc == 0 and g["status"] == "green" and (g["universe"], g["certified"]) == (4, 4), (g, txt))
rc, g, txt, acc = run(two_roots("prov-in", "int f_a(void) { return 2; }\n"), "provin")
check("a root that enters the class by defining f_a and has no imports.in.json is the one red item",
      rc == 2 and g["status"] == "red" and (g["universe"], g["certified"]) == (5, 4) and TR.T + "toy2:imports.in.json" in g["uncertified"] and "f_a" in g["detail"], (g, txt))


print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
