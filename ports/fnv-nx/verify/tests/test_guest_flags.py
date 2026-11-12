#!/usr/bin/env python3
"""Tests for verify/guest_flags.py: its --lang output equals the set the recipe passes to a C, a C++ and an asm unit (recipe.semantics_for on the
pinned driver). Plain script: PASS/FAIL lines, exit 0 on pass."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import test_plugins as tp  # noqa: E402
from test_plugins import BASE, check  # noqa: E402
import recipe  # noqa: E402

TOOL = os.path.join(os.path.dirname(HERE), "guest_flags.py")


def main():
    check("plugin_args: a copy of the fixed arguments for every origin", recipe.plugin_args("native") == recipe.PLUGIN_ARGS and recipe.plugin_args(None) is not recipe.PLUGIN_ARGS
          and recipe.plugin_args({"lifted_by": "x.py", "pack": "p"}).keys() == recipe.PLUGIN_SOURCES.keys())
    check("PLUGIN_CXX is derived from env.json", recipe.PLUGIN_CXX == "x86_64-linux-gnu-g++-13")
    pin = json.load(open(os.path.join(os.path.dirname(HERE), "guest_compiler.json")))
    ext = pin["external"]
    try:
        got = recipe.fetch_externals([ext], os.path.join(BASE, "ingest-store"), os.path.join(BASE, "extcache"), BASE)
    except recipe.BuildRed as x:
        check("the pinned external is fetched", False, x.args)
        return
    pre = got[ext["name"]]["host"] + "/bin/aarch64-linux-gnu-"
    lift = {"lifted_by": "x.py", "pack": "p"}
    drv = {"c": pre + "gcc", "c++": pre + "g++", "asm": pre + "gcc"}
    kind = {"c": "c", "c++": "cxx", "asm": "asm"}
    want = {(o, l): recipe.semantics_for(drv[l], kind[l], origin=lift if o == "lifted" else "native") for o in ("native", "lifted") for l in drv}
    check("native = the common set, lifted contains it (every language)", all(recipe.semantics_for(drv[l], kind[l], origin="native") == recipe.semantics_for(drv[l], kind[l]) and
          set(want["native", l]) <= set(want["lifted", l]) for l in drv))
    check("lifted adds -ffreestanding -fno-exceptions -fno-asynchronous-unwind-tables -fno-unwind-tables after the common set", all(
          want["lifted", l][-4:] == ["-ffreestanding", "-fno-exceptions", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables"] for l in drv) and recipe.SEMANTICS_LIFTED[:len(recipe.SEMANTICS_FLAGS)] == recipe.SEMANTICS_FLAGS)
    check("plugin_args: a lifted origin has ftoi mode lifted, native has native, the others are equal",
          recipe.plugin_args(lift)["fnvnx_ftoi"] == {"mode": "lifted"} and recipe.plugin_args("native")["fnvnx_ftoi"] == {"mode": "native"}
          and {n: a for n, a in recipe.plugin_args(lift).items() if n != "fnvnx_ftoi"} == {n: a for n, a in recipe.plugin_args("native").items() if n != "fnvnx_ftoi"})
    cache = os.path.join(BASE, "gf-cache")
    os.makedirs(cache + "/store")
    os.symlink(os.path.join(BASE, "extcache"), cache + "/external")
    for (o, lang), w in want.items():
        r = subprocess.run([sys.executable, TOOL, "--lang", lang, "--origin", o, "--cache", cache], capture_output=True, text=True)
        check("--lang %s --origin %s prints the recipe's set as a JSON array" % (lang, o), r.returncode == 0 and json.loads(r.stdout) == w, r.stderr[-300:])
    check("C drops -fno-sized-deallocation, C++ keeps it; the sets are the pin's flags in order", "-fno-sized-deallocation" in want["native", "c++"] and "-fno-sized-deallocation" not in want["native", "c"]
          and [f for f in recipe.SEMANTICS_FLAGS if f in want["native", "c"]] == want["native", "c"])
    r = subprocess.run([sys.executable, TOOL, "--lang", "rust"], capture_output=True, text=True)
    check("an unknown language is refused", r.returncode != 0)


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
