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
from test_plugins import BASE, ROOT, T, REAL, VERIFY, REC

sys.path.insert(0, VERIFY)
import gate_datamodel as gd  # noqa: E402
import recipe  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402


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
    case("no guest root: universe 0, green", rc == 0 and r["status"] == "green" and r["universe"] == 0, o)
    stock = tg.guest_repo("d2", REC, pin)
    rc, r, o = gate(stock, cache)
    case("guest root on the stock g++: red, compiler item uncertified", rc == 2 and r["certified"] == 0 and ROOT + ":compiler" in r["uncertified"], r)
    other = tg.guest_repo("d3", tg.guest_rec(dict(ext, commit="c" * 40)), pin)
    rc, r, o = gate(other, cache)
    case("guest root on another commit of the external: red", rc == 2 and ROOT + ":compiler" in r["uncertified"], r)
    case("the semantics flags of the recipe rule are the pin's (one source)", tuple(pin["flags"]["semantics"]) == recipe.SEMANTICS_FLAGS and "-mabi=ilp32" in recipe.SEMANTICS_FLAGS
          and "-mno-outline-atomics" in recipe.SEMANTICS_FLAGS and "-ffp-contract=off" in recipe.SEMANTICS_FLAGS, pin["flags"])
    # the certificate of a unit, on a CC stage document (pure function of the document and the semantic set of the unit's language)
    sem = list(recipe.SEMANTICS_FLAGS)
    doc = {"argv": [ext["at"] + "/bin/g"] + sem + ["-O2", "-Werror=attributes"] + ["-fplugin=/plug/%s/%s.so" % (n, n) for n in recipe.PLUGIN_SOURCES] +
           ["-frandom-seed=x", "-Werror=date-time"],
           "inputs": [{"at": ext["at"], "tree": ext["tree_sha256"]}] + [{"at": "/plug/" + n, "tree": "t"} for n in recipe.PLUGIN_SOURCES]}
    case("complete CC document certifies a C++ unit", gd.unit_why("a.cc", doc, sem, pin) == [], gd.unit_why("a.cc", doc, sem, pin))
    miss = copy.deepcopy(doc)
    miss["argv"] = [a for a in miss["argv"] if "fnvnx_ftoi" not in a]
    miss["inputs"] = [i for i in miss["inputs"] if i["at"] != "/plug/fnvnx_ftoi"]
    w = gd.unit_why("a.cc", miss, sem, pin)
    case("C++ unit missing a plugin is red", len(w) == 2 and all("fnvnx_ftoi" in x for x in w), w)
    cp = ["fnvnx_ftoi", "fnvnx_order", "fnvnx_dispatch", "fnvnx_addr"]   # what cc1 can load (the real answer is checked in test_guest from the ELF files)
    cdoc = copy.deepcopy(doc)
    cdoc["argv"] = [a for a in cdoc["argv"] if not any("/%s/" % n in a for n in recipe.PLUGIN_SOURCES if n not in cp)]
    cdoc["inputs"] = [i for i in cdoc["inputs"] if not any(i["at"] == "/plug/" + n for n in recipe.PLUGIN_SOURCES if n not in cp)]
    case("C unit with the plugins cc1 can load is green", gd.unit_why("a.c", cdoc, sem, pin, cp) == [], gd.unit_why("a.c", cdoc, sem, pin, cp))
    case("C unit that lacks a loadable plugin is red", len(gd.unit_why("a.c", miss, sem, pin, cp)) == 2 or any("fnvnx_ftoi" in x for x in gd.unit_why("a.c", miss, sem, pin, cp)))
    case("C unit that loads a plugin cc1 cannot load is red", any("fnvnx_sdd" in x and "cannot load" in x for x in gd.unit_why("a.c", doc, sem, pin, cp)), gd.unit_why("a.c", doc, sem, pin, cp))
    case("every plugin of the set is required, one by one", all(len(gd.unit_why("a.c", dict(doc, argv=[x for x in doc["argv"] if "/%s/" % n not in x], inputs=[
        i for i in doc["inputs"] if i["at"] != "/plug/" + n]), sem, pin)) == 2 for n in recipe.PLUGIN_SOURCES))
    noabi = copy.deepcopy(doc)
    noabi["argv"] = [a for a in noabi["argv"] if a != "-mabi=ilp32"]
    case("unit without -mabi=ilp32 is red", "argv lacks -mabi=ilp32" in gd.unit_why("a.cc", noabi, sem, pin), gd.unit_why("a.cc", noabi, sem, pin))
    for f in sem:   # every flag of the set is required, one by one (the gate keeps no list of its own)
        d = copy.deepcopy(doc)
        d["argv"] = [a for a in d["argv"] if a != f]
        case("unit without %s is red", any(f in x for x in gd.unit_why("a.cc", d, sem, pin)), gd.unit_why("a.cc", d, sem, pin), key=f)
    swapped = copy.deepcopy(doc)
    swapped["argv"][1], swapped["argv"][2] = swapped["argv"][2], swapped["argv"][1]
    case("the semantic flags out of order are red", gd.unit_why("a.cc", swapped, sem, pin) != [])
    for f in ("-moutline-atomics", "-fno-fp-contract", "-ffast-math", "-fno-signed-char", "-march=native", "-Ofast", "-fPIE"):
        d = copy.deepcopy(doc)
        d["argv"].insert(-2, f)
        case("a foreign semantic flag %s is red", "argv holds " + f in gd.unit_why("a.cc", d, sem, pin), gd.unit_why("a.cc", d, sem, pin), key=f)
    csem = [f for f in sem if f != "-fno-sized-deallocation"]   # a language the driver says a flag does not apply to: the set, minus it
    cdoc = copy.deepcopy(doc)
    cdoc["argv"] = [a for a in cdoc["argv"] if a != "-fno-sized-deallocation"]
    case("a C document with the driver's C subset is green", gd.unit_why("a.c", cdoc, csem, pin) == [], gd.unit_why("a.c", cdoc, csem, pin))
    case("a C document that still holds the C++-only flag is red", "argv holds -fno-sized-deallocation" in gd.unit_why("a.c", doc, csem, pin))
    good = {"__SIZE_TYPE__": "unsigned int", "__PTRDIFF_TYPE__": "int"}
    case("the exe's data model macros certify a unit", gd.unit_why("a.cc", doc, sem, pin, None, good) == [])
    case("size_t = long unsigned int is red", any("__SIZE_TYPE__" in x for x in gd.unit_why("a.cc", doc, sem, pin, None, dict(good, __SIZE_TYPE__="long unsigned int"))))
    case("ptrdiff_t = long int is red", any("__PTRDIFF_TYPE__" in x for x in gd.unit_why("a.cc", doc, sem, pin, None, dict(good, __PTRDIFF_TYPE__="long int"))))
    case("unsigned plain char is red", any("plain char" in x for x in gd.unit_why("a.cc", doc, sem, pin, None, dict(good, __CHAR_UNSIGNED__="1"))))
    case("unit without a CC document is red", gd.unit_why("a.cc", None, sem, pin) == ["no CC stage document"])
    # the real compiler: green
    g = tg.guest_repo("d4", rec, pin, files={ROOT + "/b.cc": "int b() { return 2; }\n", ROOT + "/c.c": "int c(float f) { return (int)f; }\n",
                                                     ROOT + "/s.S": ".text\n.globl sx\nsx: ret\n"})
    rc, r, o = gate(g, cache)
    case("native guest root on the pinned compiler: green, compiler + C++, C and asm units", rc == 0 and r["status"] == "green" and (r["certified"], r["universe"]) == (5, 5), (rc, r, o))


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
