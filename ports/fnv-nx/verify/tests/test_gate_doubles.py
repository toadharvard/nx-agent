#!/usr/bin/env python3
"""Tests for verify/gate_doubles.py: the pure rule on real objects built with the pinned cross compiler, universe 0, admit class "test". No game data."""
import json
import os
import subprocess
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import gate_doubles as g  # noqa: E402
import gate_admit  # noqa: E402

FAILED = []
CXX = "aarch64-linux-gnu-g++-13"


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


work = tempfile.mkdtemp(prefix="doubles-")


def build(name, text, hdr=None, flags=("-g",), prefix=None):
    d = os.path.join(work, name)
    os.makedirs(d)
    open(os.path.join(d, name + ".cc"), "w").write(text)
    for h, t in (hdr or {}).items():
        open(os.path.join(d, h), "w").write(t)
    o = os.path.join(d, name + ".o")
    r = subprocess.run([CXX, "-c", "-I", os.path.join(work, "inc"), "-fno-pic", "-o", o, os.path.join(d, name + ".cc"), *flags], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return o


os.makedirs(os.path.join(work, "inc"))
open(os.path.join(work, "inc", "prod.h"), "w").write("inline int pinl(int x) { return x + 1; }\ntemplate <class T> T ptpl(T x) { return x; }\n")
prod = build("prod", '#include "prod.h"\nextern "C" int plat_fn(int x) { return pinl(x); }\nint plat_tpl(int x) { return ptpl(x); }\nint plat_var = 3;\n')
psyms = set(g.object_symbols(prod))
check("product object: its definitions are read (extern C function, a variable, a C++ function, the comdat inline)", {"plat_fn", "plat_var"} <= psyms and any("pinl" in s for s in psyms), sorted(psyms))

own = ["/src"]
decl = lambda o: (lambda s: g.decl_files(o).get(s, set()))  # noqa: E731
t_call = build("tcall", '#include "prod.h"\nextern "C" int plat_fn(int);\nint tmain() { return plat_fn(2) + pinl(1); }\n')
ts = g.object_symbols(t_call)
f, n = g.judge_root("t", ts, psyms, decl(t_call), own)
check("a test that only calls a product function and an inline of the product's header is green", f == [], f)
t_dbl = build("tdbl", 'extern "C" int plat_fn(int x) { return 42; }\n')
f, n = g.judge_root("t", g.object_symbols(t_dbl), psyms, decl(t_dbl), own)
check("a test that redefines a product function is red (double)", f == [("plat_fn", "double")], f)
t_var = build("tvar", "int plat_var = 9;\n")
f, n = g.judge_root("t", g.object_symbols(t_var), psyms, decl(t_var), own)
check("a test that redefines a product variable is red (double)", f == [("plat_var", "double")], f)
t_w = build("tw", 'extern "C" __attribute__((weak)) int plat_fn(int x) { return 1; }\n')
f, n = g.judge_root("t", g.object_symbols(t_w), psyms, decl(t_w), own)
check("a weak definition counts as well", f == [("plat_fn", "double")], f)
t_other = build("tother", 'extern "C" int only_in_test(int x) { return x; }\n')
f, n = g.judge_root("t", g.object_symbols(t_other), psyms, decl(t_other), own)
check("a symbol only the test defines is no double", f == [] and n == 1, (f, n))
# COMDAT: an instance from the test's own header/source is a double; the same symbol declared in a product header is not
t_inl = build("tinl", "inline int pinl(int x) { return x + 7; }\nint use() { return pinl(1); }\n")
tsy = g.object_symbols(t_inl)
f, n = g.judge_root("t", tsy, psyms, decl(t_inl), ["/nonexistent"])
check("a comdat instance whose declaration file is not a file of the test root is not a test definition", f == [], f)
f, n = g.judge_root("t", tsy, psyms, decl(t_inl), [os.path.join(work, "tinl")])
check("a comdat instance declared in a file of the test root, also defined by the product, is a double", [c for _, c in f] == ["double"], f)
t_nodw = build("tnodw", "inline int pinl(int x) { return x + 7; }\nint use() { return pinl(1); }\n", flags=())
f, n = g.judge_root("t", g.object_symbols(t_nodw), psyms, decl(t_nodw), own)
check("a comdat symbol the product also defines, with no DWARF declaration file, is no-decl-file (never a guess)", [c for _, c in f] == ["no-decl-file"], f)
check("DWARF declaration files are read through the object", any(f.endswith("tinl.cc") for fs in g.decl_files(t_inl).values() for f in fs))
o = g.own_dirs({"include": ["ports/fnv-nx/t/inc", "ports/fnv-nx/p/inc", "e/x"], "external": [{"at": "e"}]}, "ports/fnv-nx/t")
check("own_dirs numbers the non-external include directories like the recipe and keeps the ones inside the root", o == ["/src", "/inc/0"], o)

# admit: class "test" needs doubles and a recipe
bad, err = gate_admit.judge(["ports/fnv-nx/t/a.cc"], {"format": "fnvnx-admit/1", "roots": [{"path": "ports/fnv-nx/t", "class": "test", "gates": []}]}, ["doubles"], lambda p: True)
check("admit: a test root without the doubles gate or a recipe is red", bad and any("doubles" in e and "recipe" in e for e in err), err)
bad, err = gate_admit.judge(["ports/fnv-nx/t/a.cc"], {"format": "fnvnx-admit/1", "roots": [{"path": "ports/fnv-nx/t", "class": "test", "gates": ["doubles"], "recipe": "ports/fnv-nx/t/build.json"}]}, ["doubles"], lambda p: True)
check("admit: a test root with doubles and a recipe is admitted", not bad and not err, (bad, err))

# universe 0: no test root in this tree
out = tempfile.mkdtemp(prefix="gd-")
repo = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
r = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_doubles.py")], cwd=repo, env=dict(os.environ, GATE_OUT=out), capture_output=True, text=True)
res = json.load(open(os.path.join(out, "doubles.json"))) if os.path.exists(os.path.join(out, "doubles.json")) else {}
check("no test root: universe 0 green with the global counts", r.returncode == 0 and res.get("universe") == 0 and res.get("test_roots") == 0 and res.get("doubles") == 0, (r.stdout, r.stderr, res))
shutil.rmtree(work, ignore_errors=True)
print("FAILED: %d" % len(FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
