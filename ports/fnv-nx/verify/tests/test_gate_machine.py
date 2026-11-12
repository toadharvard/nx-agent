#!/usr/bin/env python3
"""Tests for verify/gate_machine.py (class G): the driver on the toy product root of test_recipe.py with a synthetic regsites.tsv published as an
external of a stand-in artifacts repository; the pure rules are tested by contract/tests/test_hive.py. Plain script: exit 0 on pass."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "contract"))
import gate_machine as G  # noqa: E402
import hive  # noqa: E402
import test_recipe as TR  # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:500]))
    if not ok:
        FAILED.append(name)


os.environ["ARTIFACTS_URL"] = TR.BASE + "/artifacts.git"
TR.subprocess.run(["git", "init", "-q", "--bare", TR.BASE + "/artifacts.git"], check=True)
GC = TR.BASE + "/cache"
LM = "HKEY_LOCAL_MACHINE"
SH = "\t".join(hive.SITES_HEAD) + "\n"
DH = "\t".join(hive.DECL_HEAD) + "\n"
READ = "\t".join(["t.exe", "0x1", "RegQueryValueExA", LM, "SOFTWARE\\Toy", "Path", "static", "r"]) + "\n"
DYN = "\t".join(["t.exe", "0x2", "RegQueryValueExA", LM, "?", "?", "dynamic", "r"]) + "\n"
DECL = DH + "%s\tSOFTWARE\\Toy\tPath\tREG_SZ\t{game_dir}\tdeclared machine\n" % LM
WRITER = '#include "inc/toy.h"\nint RegSetValueExA(void);\nint seed(void) { return RegSetValueExA() + TOY; }\n'


def run(repo, tag):
    rc, g, _, txt = TR.gate(repo, "machine", TR.BASE + "/o-" + tag, GC)
    return rc, g, txt


def toy_repo(tag, sites, declared=DECL, seed=None, unit="seed.c", writers=None, decl_extra=None):
    commit, thash = TR.publish("rg-" + tag, lambda d: open(d + "/regsites.tsv", "w").write(sites))
    ex = {"name": "manifest", "repo": "fnv-nx-build", "ref": "rg-" + tag, "commit": commit, "tree_sha256": thash, "at": "/ext/manifest"}
    rec = dict(TR.RECIPE, external=[ex])
    decl = {"format": "fnvnx-registry-in/1", "external": "manifest", "sites": "regsites.tsv", "declared": "declared.tsv", "seed": "seed.inc",
            "seed_unit": unit}
    decl.update(decl_extra or {})
    files = {"toy/registry.in.json": json.dumps(decl), "toy/declared.tsv": declared,
             "toy/seed.inc": hive.generate(sites, declared) if seed is None else seed}
    files["toy/regapi.c"] = "int RegSetValueExA(void) { return 1; }\n"     # the fixture's own body of the API, so that the toy links
    files.update(writers if writers is not None else {"toy/seed.c": WRITER})
    return TR.make_repo("rg-" + tag, rec=rec, files=files)


noroot = TR.make_repo("rg-noroot")
TR.w(noroot, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": TR.T + "gen", "class": "tool", "gates": []}]}))
TR.git(noroot, "commit", "-q", "-am", "no product root")
rc, g, txt = run(noroot, "noroot")
check("no product root: universe 0, green", rc == 0 and g["universe"] == 0 and g["status"] == "green", (g, txt))

rc, g, txt = run(TR.make_repo("rg-empty"), "none")
check("product root without registry.in.json: red", rc == 2 and g["status"] == "red" and g["universe"] == 1 and "registry.in.json" in g["detail"], (g, txt))

rc, g, txt = run(toy_repo("ok", SH + READ), "ok")
check("green: declared row read, seed equal, only the seed unit writes", rc == 0 and g["status"] == "green" and g["universe"] == 3 == g["certified"], (g, txt))

rc, g, txt = run(toy_repo("dyn", SH + READ + DYN), "dyn")
check("dynamic read site: red, a measurement build", rc == 2 and g["status"] == "red" and "MEASUREMENT" in g["detail"] and g["certified"] == 3 and g["universe"] == 4, (g, txt))

rc, g, txt = run(toy_repo("seed", SH + READ, seed="/* hand edited */\n"), "seed")
check("seed include that differs from the generator output: red", rc == 2 and g["status"] == "red" and "seed-differs" in open(TR.BASE + "/o-seed/machine.ids").read(), (g, txt))

rc, g, txt = run(toy_repo("foreign", SH + READ, unit="other.c"), "foreign")
check("writer outside the seed unit: red", rc == 2 and g["status"] == "red" and "writer:seed.c\tforeign-writer" in open(TR.BASE + "/o-foreign/machine.ids").read(), (g, txt))

rc, g, txt = run(toy_repo("unread", SH, seed=""), "unread")
check("declared row with no read site: red", rc == 2 and g["status"] == "red" and "no-read-site" in open(TR.BASE + "/o-unread/machine.ids").read(), (g, txt))

for tag, kw in (("badkeys", {"decl_extra": {"bogus": 1}}), ("badext", {"decl_extra": {"external": "nonexistent"}}), ("badpath", {"decl_extra": {"sites": "nope.tsv"}})):
    rc, g, txt = run(toy_repo(tag, SH + READ, **kw), tag)
    check("bad registry.in.json (%s) is red with the reason" % tag, rc == 2 and g["status"] == "red" and "input error" in g["detail"], (g, txt))

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
