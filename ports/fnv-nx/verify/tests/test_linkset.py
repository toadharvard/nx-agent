#!/usr/bin/env python3
"""linkset.py: the host-root definers and the linker-synthesised names (the dispatch; the ld stage itself runs on a farm).
  - host_system_defs: the defined .dynsym exports of a file of link.libs.system that a pinned deb of env.json owns are the root's definers (source sys:<file>);
    a file no pinned deb owns (a toy library built here) is ValueError host-lib-unpinned; a recipe with no link has none;
  - synth_defs: a guest root asks recipe.synth_names for kind pie with link/guest.json's script; a host root for kinds shared and exe with no script; the names come out as
    source ld:<kind> (the seam is recipe.synth_names, which needs the pinned compiler and the sandbox).
Run: python3 -I <this file> (needs the pinned env.json packages' files on this machine for the green case)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import envcheck  # noqa: E402
import linkset  # noqa: E402
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
with tempfile.TemporaryDirectory() as td:
    R = "ports/fnv-nx/toy"
    os.makedirs(os.path.join(td, R))
    os.makedirs(os.path.join(td, "ports/fnv-nx/verify"))
    open(os.path.join(td, recipe.ENV_JSON), "w").write(open(os.path.join(REPO, recipe.ENV_JSON)).read())

    def root_with(system):
        rec = {"format": "fnvnx-recipe/1", "link": {"output": "x.so", "kind": "shared", "flags": ["-m", "elf_x86_64"], "libs": {"system": system}} if system is not None else None}
        json.dump(rec, open(os.path.join(td, R, "build.json"), "w"))
        return {R: {"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}}

    lib = os.path.realpath("/lib/x86_64-linux-gnu/libm.so.6")
    owners = {}
    try:
        owners = envcheck.file_owners()
    except Exception as e:   # noqa: BLE001
        print("file_owners unavailable:", e)
    d = linkset.host_system_defs(R, root_with(["/lib/x86_64-linux-gnu/libm.so.6"]), td, owners)
    check("a pinned deb's library gives its .dynsym exports", "cos" in d and all(list(v) == ["sys:/lib/x86_64-linux-gnu/libm.so.6"] for v in d.values()), list(d)[:3])
    open(os.path.join(td, "t.c"), "w").write("int toy_export(void){return 1;}\n")
    subprocess.run(["cc", "-shared", "-fPIC", "-o", os.path.join(td, "libtoy.so"), os.path.join(td, "t.c")], check=True)
    try:
        linkset.host_system_defs(R, root_with([os.path.join(td, "libtoy.so")]), td, owners)
        check("a library no pinned deb owns is red", False)
    except ValueError as e:
        check("a library no pinned deb owns is red host-lib-unpinned", "host-lib-unpinned" in str(e), str(e))
    check("a recipe with no link has no system definers", linkset.host_system_defs(R, root_with(None), td, owners) == {})

calls = []
recipe.synth_names = lambda repo, root, cache, kinds, script=None: calls.append((root, tuple(kinds), script)) or {k: {"_GLOBAL_OFFSET_TABLE_", "__toy"} for k in kinds}
adm = {"g": {"target": "guest"}, "h": {"target": "host"}}
recipe.load_guest_link = lambda repo=".": {"script": "link/guest.ld"}
dg, dh = linkset.synth_defs("g", adm, ".", "c"), linkset.synth_defs("h", adm, ".", "c")
check("a guest root: kind pie with the guest script, source ld:pie", calls[0] == ("g", ("pie",), recipe.TREE + "/link/guest.ld") and set(dg["__toy"]) == {"ld:pie"}, calls)
check("a host root: kinds shared and exe, no script", calls[1] == ("h", ("shared", "exe"), None) and set(dh["_GLOBAL_OFFSET_TABLE_"]) == {"ld:shared", "ld:exe"}, calls)
sys.exit(1 if fails else 0)
