#!/usr/bin/env python3
"""Tests for verify/gate_argorder.py (gate "argordergate"): the universe is the C++ units of native guest roots; a unit is certified only when its
root's recipe loads the rule's plugin list (fnvnx_order included) in the rule's order and the build holds the pi .so. Real-compiler case: the pinned external
of verify/guest_compiler.json (fetched by commit; red with the reason when it cannot be). Plain script, needs root (sandbox)."""
import copy
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_plugins as tp
import test_guest as tg
from test_plugins import BASE, ROOT, T, REAL, VERIFY, git, w, make_repo, REC
import recipe

sys.path.insert(0, VERIFY)
import gate_argorder as ga  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402


def gate(repo, cache):
    """Run the gate itself in REPO (cwd), as the verdict does -> (exit code, result dict)."""
    out = os.path.join(BASE, "gate-out-" + os.path.basename(repo))
    os.makedirs(out, exist_ok=True)
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_argorder.py")], cwd=repo,
                       env=dict(os.environ, GATE_OUT=out, GATE_CACHE=cache), capture_output=True, text=True)
    f = os.path.join(out, "argordergate.json")
    return p.returncode, (json.load(open(f)) if os.path.exists(f) else {}), p.stdout + p.stderr


def main():
    pin = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
    ext = pin["external"]
    cache = os.path.join(BASE, "gate-cache")
    rec = tg.guest_rec(ext)
    # --- no native guest root: universe 0, green (host root, lifted root)
    host = tg.guest_repo("a1", REC, pin, target="host")
    rc, r, o = gate(host, cache)
    case("no guest root: universe 0, green", rc == 0 and r["status"] == "green" and r["universe"] == 0, o)
    # --- native guest root without fnvnx_order: red, every C++ unit uncertified, no build needed
    nopi = copy.deepcopy(rec)
    nopi["plugins"] = [p for p in nopi["plugins"] if p["name"] != "fnvnx_order"]
    g = tg.guest_repo("a2", nopi, pin, files={ROOT + "/b.cc": "int b() { return 2; }\n"})
    rc, r, o = gate(g, cache)
    case("native guest root without fnvnx_order: red, units uncertified", rc == 2 and r["status"] == "red" and r["universe"] == 2 and r["certified"] == 0
          and r["uncertified"] == [ROOT + "/a.cc", ROOT + "/b.cc"], (rc, r))
    # --- the load order is the rule's
    rev = copy.deepcopy(rec)
    rev["plugins"] = list(reversed(rev["plugins"]))
    g = tg.guest_repo("a3", rev, pin)
    rc, r, o = gate(g, cache)
    case("plugins in another order: red", rc == 2 and r["certified"] == 0 and "order" in r["detail"], r)
    # --- the real compiler: green, every unit certified
    g = tg.guest_repo("a4", rec, pin, files={ROOT + "/b.cc": "int b() { return 2; }\n"})
    rc, r, o = gate(g, cache)
    case("native guest root on the pinned compiler: green, 2/2", rc == 0 and r["status"] == "green" and (r["certified"], r["universe"]) == (2, 2), (rc, r, o))


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
