#!/usr/bin/env python3
"""Tests for verify/gate_datamodel.py (gate "datamodel"): guest roots only; the compiler pin, -mabi=ilp32 and the plugins are read from the CC stage
documents of the build. Real-compiler case: the pinned external of verify/guest_compiler.json (fetched by commit; red with the reason when it
cannot be). Plain script, needs root (sandbox)."""
import copy
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_plugins as tp
import test_guest as tg
from test_plugins import BASE, ROOT, T, REAL, VERIFY, check, REC

sys.path.insert(0, VERIFY)
import gate_datamodel as gd  # noqa: E402
import recipe  # noqa: E402


def gate(repo, cache):
    out = os.path.join(BASE, "gate-out-" + os.path.basename(repo))
    os.makedirs(out, exist_ok=True)
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_datamodel.py")], cwd=repo,
                       env=dict(os.environ, GATE_OUT=out, GATE_CACHE=cache), capture_output=True, text=True)
    f = os.path.join(out, "datamodel.json")
    return p.returncode, (json.load(open(f)) if os.path.exists(f) else {}), p.stdout + p.stderr


def main():
    pin = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
    ext = pin["external"]
    cache = os.path.join(BASE, "gate-cache-dm")
    rec = tg.guest_rec(ext)
    host = tg.guest_repo("d1", REC, pin, target="host")
    rc, r, o = gate(host, cache)
    check("no guest root: universe 0, green", rc == 0 and r["status"] == "green" and r["universe"] == 0, o)
    stock = tg.guest_repo("d2", REC, pin)
    rc, r, o = gate(stock, cache)
    check("guest root on the stock g++: red, compiler item uncertified", rc == 2 and r["certified"] == 0 and ROOT + ":compiler" in r["uncertified"], r)
    other = tg.guest_repo("d3", tg.guest_rec(dict(ext, commit="c" * 40)), pin)
    rc, r, o = gate(other, cache)
    check("guest root on another commit of the external: red", rc == 2 and ROOT + ":compiler" in r["uncertified"], r)
    check("the semantics flags of the recipe rule are the pin's (one source)", tuple(pin["flags"]["semantics"]) == recipe.SEMANTICS_FLAGS and "-mabi=ilp32" in recipe.SEMANTICS_FLAGS)
    # the certificate of a unit, on a CC stage document (pure function of the document)
    doc = {"argv": [ext["at"] + "/bin/g", "-mabi=ilp32"] + list(recipe.SEMANTICS_FLAGS) + list(pin["flags"]["required"]) +
           ["-fplugin=/plug/%s/%s.so" % (n, n) for n in recipe.GUEST_CXX_PLUGINS],
           "inputs": [{"at": ext["at"], "tree": ext["tree_sha256"]}] + [{"at": "/plug/" + n, "tree": "t"} for n in recipe.GUEST_CXX_PLUGINS]}
    check("complete CC document certifies a native C++ unit", gd.unit_why("a.cc", doc, True, pin) == [], gd.unit_why("a.cc", doc, True, pin))
    miss = copy.deepcopy(doc)
    miss["argv"] = [a for a in miss["argv"] if "fnvnx_ftoi" not in a]
    miss["inputs"] = [i for i in miss["inputs"] if i["at"] != "/plug/fnvnx_ftoi"]
    w = gd.unit_why("a.cc", miss, True, pin)
    check("C++ unit missing a plugin is red", len(w) == 2 and all("fnvnx_ftoi" in x for x in w), w)
    check("lifted C++ unit needs no plugin", gd.unit_why("a.cc", miss, False, pin) == [] or all("plug" not in x for x in gd.unit_why("a.cc", miss, False, pin)))
    noabi = copy.deepcopy(doc)
    noabi["argv"] = [a for a in noabi["argv"] if a != "-mabi=ilp32"]
    check("unit without -mabi=ilp32 is red", gd.unit_why("a.cc", noabi, True, pin) == ["argv lacks -mabi=ilp32"], gd.unit_why("a.cc", noabi, True, pin))
    noo = copy.deepcopy(doc)
    noo["argv"] = [x for x in noo["argv"] if x not in pin["flags"]["required"]]
    check("unit without -mno-outline-atomics is red", gd.unit_why("a.cc", noo, True, pin) == ["argv lacks -mno-outline-atomics"], gd.unit_why("a.cc", noo, True, pin))
    check("a C unit without it is red too", gd.unit_why("a.c", noo, True, pin) == ["argv lacks -mno-outline-atomics"], gd.unit_why("a.c", noo, True, pin))
    yes = copy.deepcopy(doc)
    yes["argv"].append("-moutline-atomics")
    check("unit with -moutline-atomics is red", gd.unit_why("a.cc", yes, True, pin) == ["argv holds -moutline-atomics"], gd.unit_why("a.cc", yes, True, pin))
    check("unit without a CC document is red", gd.unit_why("a.cc", None, True, pin) == ["no CC stage document"])
    # the real compiler: green
    g = tg.guest_repo("d4", rec, pin, files={ROOT + "/b.cc": "int b() { return 2; }\n"})
    rc, r, o = gate(g, cache)
    check("native guest root on the pinned compiler: green, compiler + 2 units", rc == 0 and r["status"] == "green" and (r["certified"], r["universe"]) == (3, 3), (rc, r, o))


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
