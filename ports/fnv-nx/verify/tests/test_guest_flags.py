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
    pin = json.load(open(os.path.join(os.path.dirname(HERE), "guest_compiler.json")))
    ext = pin["external"]
    try:
        got = recipe.fetch_externals([ext], os.path.join(BASE, "ingest-store"), os.path.join(BASE, "extcache"), BASE)
    except recipe.BuildRed as x:
        check("the pinned external is fetched", False, x.args)
        return
    pre = got[ext["name"]]["host"] + "/bin/aarch64-linux-gnu-"
    want = {"c": recipe.semantics_for(pre + "gcc", "c"), "c++": recipe.semantics_for(pre + "g++", "cxx"), "asm": recipe.semantics_for(pre + "gcc", "asm")}
    cache = os.path.join(BASE, "gf-cache")
    os.makedirs(cache + "/store")
    os.symlink(os.path.join(BASE, "extcache"), cache + "/external")
    for lang, w in want.items():
        r = subprocess.run([sys.executable, TOOL, "--lang", lang, "--cache", cache], capture_output=True, text=True)
        check("--lang %s prints the recipe's set as a JSON array" % lang, r.returncode == 0 and json.loads(r.stdout) == w, r.stderr[-300:])
    check("C drops -fno-sized-deallocation, C++ keeps it; the sets are the pin's flags in order", "-fno-sized-deallocation" in want["c++"] and "-fno-sized-deallocation" not in want["c"]
          and [f for f in recipe.SEMANTICS_FLAGS if f in want["c"]] == want["c"])
    r = subprocess.run([sys.executable, TOOL, "--lang", "rust"], capture_output=True, text=True)
    check("an unknown language is refused", r.returncode != 0)


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
