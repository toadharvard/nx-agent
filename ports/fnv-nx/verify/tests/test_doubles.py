#!/usr/bin/env python3
"""doubles.judge ("no doubles in tests", gate objstub) and the `tests` key of recipe.check, on real AArch64 objects compiled with
aarch64-linux-gnu-gcc-13 (symbols read by recipe.symbols from the ELF .symtab). Plain script: exit 0 on pass, one ok/FAIL line per case.
Scratch: tempfile.mkdtemp, removed at exit."""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import doubles  # noqa: E402
import recipe  # noqa: E402

BASE = tempfile.mkdtemp(prefix="doubles-test-")
atexit.register(shutil.rmtree, BASE, True)
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else ": %s" % (detail,)))
    fails += not cond


def obj(unit, src, flags=()):
    """Compile SRC (C text) to an object; -> unit name. The file is BASE/<unit>.o"""
    c = os.path.join(BASE, unit + ".c")
    os.makedirs(os.path.dirname(c), exist_ok=True)
    open(c, "w").write(src)
    subprocess.run(["aarch64-linux-gnu-gcc-13", "-O1", "-ffreestanding", "-c", "-o", os.path.join(BASE, unit + ".o"), c, *flags], check=True)
    return unit


def defined(unit):
    return recipe.symbols(os.path.join(BASE, unit + ".o"), unit)[0]


def result(units, tests):
    """A build result as recipe.build writes it: units = product units (defined from their objects), tests = [(name, [test units], linked|None)]."""
    r = {"units": sorted(units), "defined": [d for u in units for d in defined(u)], "tests": []}
    for name, tus, linked in tests:
        r["tests"].append({"name": name, "units": tus, "defined": [d for u in tus for d in defined(u)],
                           "linked": sorted(linked if linked is not None else list(units) + tus), "elf": {"path": "x", "sha256": "0" * 64}})
    return r


def kinds(res):
    items, finds, st = doubles.judge(res)
    return sorted({k for _, _, k, _ in finds}), finds, st, items


obj("a", "int real_a(void){return 1;}\nint shared_data = 3;\n")
obj("b", "int real_b(void){return 2;}\n__attribute__((weak)) int weak_b(void){return 5;}\n")
obj("o", "int other_root_fn(void){return 7;}\n")
obj("t_clean", "int test_only(void){return 9;}\nint test_main(void){return 0;}\n")
obj("t_strong", "int real_a(void){return 100;}\nint test_main2(void){return 0;}\n")
obj("t_weak", "__attribute__((weak)) int real_b(void){return 100;}\n")
obj("t_weak2", "__attribute__((weak)) int weak_b(void){return 6;}\n")
obj("t_other", "int other_root_fn(void){return 8;}\n")
obj("t_data", "int shared_data = 4;\n")
obj("t_static", "static int real_a(void){return 1;}\nint use_a(void){return real_a();}\n")
obj("u_dup", "int real_b(void){return 66;}\n")

# positive: a test that defines only its own names, linking every product unit
k, f, st, items = kinds({"R": result(["a", "b"], [("clean", ["t_clean"], None)])})
check("clean test: green, 1 test, 0 doubles", k == [] and st == {"tests_total": 1, "tests_with_doubles": 0, "double_symbols": 0}, (k, f, st))
check("clean test: items are the test and the product units of the root", sorted(i for i, _ in items) == ["R:product-units", "R:test:clean"], items)
check("local (static) symbols are no definition", doubles.judge({"R": result(["a", "b"], [("st", ["t_static"], None)])})[1] == [], None)

# negatives
k, f, st, _ = kinds({"R": result(["a", "b"], [("s", ["t_strong"], None)])})
check("test defines a strong product symbol: red test-double naming test, symbol and product object",
      k == ["test-double"] and "R:test:s" == f[0][0] and "real_a" in f[0][3] and "R/a" in f[0][3] and st["tests_with_doubles"] == 1 and st["double_symbols"] == 1, (k, f, st))
check("test defines a weak symbol that a product object defines strongly: red", kinds({"R": result(["a", "b"], [("w", ["t_weak"], None)])})[0] == ["test-double"])
check("weak in the test, weak in the product: red (weak counts)", kinds({"R": result(["a", "b"], [("w", ["t_weak2"], None)])})[0] == ["test-double"])
check("data symbol: red", kinds({"R": result(["a", "b"], [("d", ["t_data"], None)])})[0] == ["test-double"])
k, f, st, _ = kinds({"R": result(["a", "b"], [("x", ["t_clean"], None)]), "S": result(["o"], [("y", ["t_other"], None)])})
check("another root: exactly the test y, naming root S's object", k == ["test-double"] and len(f) == 1 and f[0][0] == "S:test:y" and "S/o" in f[0][3], (k, f))
k, f, st, _ = kinds({"R": result(["a", "b"], [("x", ["t_other"], None)]), "S": result(["o"], [])})
check("test of root R replaces a product symbol of root S: red", k == ["test-double"] and f[0][0] == "R:test:x" and "S/o" in f[0][3], (k, f))
k, f, st, _ = kinds({"R": result(["a", "b"], [("om", ["t_clean"], ["a", "t_clean"])])})
check("test link omitting a product unit: red test-omits-unit naming it", k == ["test-omits-unit"] and "R/b" in f[0][3], (k, f))
res = result(["a", "b"], [("nl", ["t_clean"], None)])
res["tests"][0]["elf"] = None
check("declared test without a linked ELF: red", kinds({"R": res})[0] == ["test-not-linked"])
k, f, st, _ = kinds({"R": result(["a", "u_dup", "b"], [])})
check("undeclared test source (a product unit that replaces a product body): product-duplicate", k == ["product-duplicate"] and "real_b" in f[0][3] and "R/b" in f[0][3] and "R/u_dup" in f[0][3], (k, f))
check("two product objects with the same WEAK symbol: no finding (inline functions)",
      doubles.judge({"R": {"units": ["a", "b"], "defined": [{"symbol": "inl", "bind": "WEAK", "type": "FUNC", "object": "a"},
                                                                {"symbol": "inl", "bind": "WEAK", "type": "FUNC", "object": "b"}], "tests": []}})[1] == [])
check("no roots: universe 0, no finding, stats 0", doubles.judge({}) == ([], [], {"tests_total": 0, "tests_with_doubles": 0, "double_symbols": 0}))

# recipe.check: the `tests` key
T = "ports/fnv-nx/"
files = [T + "r/a.c", T + "r/b.c", T + "r/t1.c", T + "r/t2.c", T + "r/link.ld", T + "r/inc/x.h"]
roots = {T + "r": {"path": T + "r", "class": "product"}}
REC = {"format": "fnvnx-recipe/1", "compiler": {"cc": "aarch64-linux-gnu-gcc-13", "cxx": "aarch64-linux-gnu-g++-13"},
       "flags": {"c": [], "cxx": [], "asm": []}, "include": [T + "r/inc"], "units": "all",
       "link": {"script": "link.ld", "output": "r.elf", "flags": []}}


def errs(**kw):
    return recipe.check(dict(REC, **kw), T + "r", files, roots)


check("recipe with no tests key is valid", errs() == [], errs())
check("declared test: valid; product units exclude it", errs(tests=[{"name": "t1", "units": ["t1.c"]}]) == []
      and recipe.units_of(dict(REC, tests=[{"name": "t1", "units": ["t1.c"]}]), T + "r", files) == ["a.c", "b.c", "t2.c"])
check("test unit that is no source of the root: error", any("not a source" in e for e in errs(tests=[{"name": "t", "units": ["zz.c"]}])))
check("unit in two tests: error", any("two tests" in e for e in errs(tests=[{"name": "t", "units": ["t1.c"]}, {"name": "u", "units": ["t1.c"]}])))
check("duplicate test name: error", any("twice" in e for e in errs(tests=[{"name": "t", "units": ["t1.c"]}, {"name": "t", "units": ["t2.c"]}])))
check("unknown key in a test, empty units: error", len(errs(tests=[{"name": "t", "units": ["t1.c"], "x": 1}])) == 1 and len(errs(tests=[{"name": "t", "units": []}])) == 1)
rec2 = dict(REC, tests=[{"name": "t", "units": ["t1.c"]}])
del rec2["link"]
check("tests without the root's link: error", any("need the root's link" in e for e in recipe.check(rec2, T + "r", files, roots)))
check("os_layer entry that is a test unit: error", any("a test unit is no os_layer" in e for e in errs(os_layer=["t1.c"], tests=[{"name": "t", "units": ["t1.c"]}])))
sys.exit(1 if fails else 0)
