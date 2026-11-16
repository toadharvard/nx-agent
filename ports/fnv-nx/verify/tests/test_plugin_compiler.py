#!/usr/bin/env python3
# reads: ports/fnv-nx/verify
"""Tests for the plugin-compiler rule of verify/recipe.py (RECIPE.md "Plugins"): plugins are bound to the guest compiler.  Pure rule checks, no build:
a root of another compiler that declares plugins is red; a host native C++ root loads none and needs none; a guest root still needs the full set; a root with a unit and no
compiler is red."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
REAL = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
sys.path.insert(0, VERIFY)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import recipe  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)[:400]) if detail and not ok else ""), flush=True)
    if not ok:
        fails.append(name)


pin = recipe.load_pin(REAL)
ex = pin["external"]
cc, cxx = recipe.pin_drivers()
ROOT = "ports/fnv-nx/toy"
files = [ROOT + "/a.cc", ROOT + "/build.json"]
guest = {"format": "fnvnx-recipe/1", "compiler": {"external": ex["name"], "cc": cc, "cxx": cxx}, "external": [dict(ex)], "flags": {"c": [], "cxx": [recipe.SDD_DEFINE], "asm": []},
         "include": [], "units": "all", "plugins": [{"name": n} for n in recipe.PLUGIN_SOURCES]}
host = {"format": "fnvnx-recipe/1", "compiler": {"cc": recipe.PLUGIN_CXX.replace("g++", "gcc"), "cxx": recipe.PLUGIN_CXX}, "flags": {"c": [], "cxx": [], "asm": []},
        "include": [], "units": "all", "plugins": []}


def errs(rec, target, origin="native"):
    roots = {ROOT: {"path": ROOT, "class": "product", "target": target, "cxx_origin": origin, "recipe": ROOT + "/build.json"}}
    return recipe.plugin_errors(rec, ROOT, files, roots, [ROOT + "/a.cc"], [e["name"] for e in rec.get("external", [])], "", REAL)


e = errs(dict(host, plugins=[{"name": n} for n in recipe.PLUGIN_SOURCES]), "host")
check("host compiler declaring plugins is red", any("plugins need a compiler that targets the guest platform" in x for x in e), e)
e = errs(host, "host")
check("host native C++ root loads none and needs none", not e, e)
e = errs(guest, "guest")
check("guest root with the full set is valid", not e, e)
e = errs(dict(guest, plugins=[{"name": n} for n in list(recipe.PLUGIN_SOURCES)[:1]]), "guest")
check("guest root without the full set is red", any("does not load plugin" in x for x in e), e)
m = recipe.check({k: v for k, v in host.items() if k != "compiler"}, ROOT, files, {ROOT: {"path": ROOT, "class": "product", "target": "host", "recipe": ROOT + "/build.json"}}, pin, REAL)
check("a root with a unit and no compiler is red", any("missing key 'compiler'" in x for x in m), m)
other = dict(guest, compiler={"external": "stage1", "cc": "prefix/bin/aarch64-none-elf-gcc", "cxx": "prefix/bin/aarch64-none-elf-g++"}, external=[dict(ex, name="stage1")])
e = errs(other, "guest")
check("a guest-target external compiler other than the pin loads the full set", not e, e)
import tempfile  # noqa: E402
R = [c for c in vars(recipe).values() if isinstance(c, type) and hasattr(c, "plugin_env")][0]


def env_err(rec, ext, facts=None):
    r = R.__new__(R)
    r.rec, r.ext, r.root = rec, ext, ROOT
    r.compiler_fact = lambda q: (facts or {})[q]   # the stage's answer; the real method runs the hermetic stage cfact:<external>:<query>
    try:
        r.plugin_env(tempfile.mkdtemp())
    except recipe.BuildRed as x:
        return " ".join(x.args[0]["stderr"])
    return ""


c = {"compiler": {"external": "h", "cxx": "bin/cxx"}}
x = {"h": {"host": "/nonexistent", "at": "/ext/h"}}
check("a compiler whose -dumpmachine fact is not the guest triplet loads no plugin", "targets 'x86_64-linux-gnu'" in env_err(c, x, {"-dumpmachine": "x86_64-linux-gnu"}))
check("a plugin header dir outside the compiler's tree is red", "no plugin headers" in env_err(c, x, {"-dumpmachine": recipe._targets.load()["guest_platform"]["triplet"], "-print-file-name=plugin": "/usr/lib/gcc/plugin"}))
check("a host-driver compiler has no plugin env", "external compiler" in env_err({"compiler": {"cxx": "g++"}}, {}))
ins = [{"at": "/ext/h", "tree": "t1"}]
d1, d2 = recipe.compilerfact_doc("h", "/ext/h/bin/cxx", "-dumpmachine", ins), recipe.compilerfact_doc("h", "/ext/h/bin/cxx", "-dumpmachine", list(ins))
check("the same compiler and query make the same stage doc, hence the same key: a cached answer is reused, no second compiler run", d1 == d2 and d1["stage"] == "cfact:h:-dumpmachine" and d1["argv"][-1] == "-dumpmachine", d1)
check("another compiler tree makes another stage", recipe.compilerfact_doc("h", "/ext/h/bin/cxx", "-dumpmachine", [{"at": "/ext/h", "tree": "t2"}]) != d1)
td = tempfile.mkdtemp()
os.makedirs(td + "/inc")
open(td + "/as", "w").write("x")
open(td + "/inc/a.h", "w").write("x")
open(td + "/inc/b.h", "w").write("x")
own = {td + "/as": {"binutils"}, td + "/inc/a.h": {"libc-dev"}, td + "/inc/b.h": {"libc-dev"}}
real = lambda p: p  # noqa: E731
check("discovered: a program of a pinned package and a tree of pinned packages are green", recipe.discovered_errors([td + "/as"], [td + "/inc"], ["binutils", "libc-dev"], own, real) == [])
e = recipe.discovered_errors([td + "/as"], [], ["libc-dev"], own, real)
check("discovered: a program owned by a package env.json does not pin is red", len(e) == 1 and "owned by no package" in e[0], e)
e = recipe.discovered_errors([td + "/as"], [], ["binutils"], {}, real)
check("discovered: a program owned by no package is red", len(e) == 1, e)
e = recipe.discovered_errors([], [td + "/inc"], ["libc-dev"], dict(own, **{td + "/inc/b.h": {"other"}}), real)
check("discovered: a tree holding a file of an unpinned package is red", len(e) == 1 and "b.h" in e[0], e)
sys.exit(1 if fails else 0)
