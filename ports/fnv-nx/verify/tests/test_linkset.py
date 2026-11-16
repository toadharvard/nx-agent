#!/usr/bin/env python3
"""linkset.py: the host-root definers and the linker-synthesised names (the dispatch; the ld stage itself runs on a farm).
  - host_system_defs: the defined .dynsym exports of a file of link.libs.system that a pinned deb of env.json owns are the root's definers (source sys:<file>);
    a file no pinned deb owns (a toy library built here) is ValueError host-lib-unpinned; a recipe with no link has none;
  - synth_defs: a guest root asks recipe.synth_names for kind pie with link/guest.json's script; a host root for kinds shared and exe with no script; the names come out as
    source ld:<kind> (the seam is recipe.synth_names, which needs the pinned compiler and the sandbox).
Run: python3 -I <this file> (needs the pinned env.json packages' files on this machine for the green case)."""
import json
import os
import shutil
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
recipe.synth_names = lambda repo, root, cache, kinds, script=None, objs=(): calls.append((root, tuple(kinds), script, tuple(objs))) or {k: {"_GLOBAL_OFFSET_TABLE_", "__toy"} for k in kinds}
adm = {"g": {"target": "guest"}, "h": {"target": "host"}}
recipe.load_guest_link = lambda repo=".": {"script": "link/guest.ld"}
recipe.link_candidates = lambda admit, spec, target, repo: []
linkset.gate_build.ensure = lambda m, cache, repo=".": ({"status": "green", "objects": [{"unit": "u", "object": "/o/u.o"}]}, "")
recipe.load_recipe = lambda repo, admit, m: {"link": {"kind": "shared"}} if m == "h" else {}
dg, dh = linkset.synth_defs("g", adm, ".", "c"), linkset.synth_defs("h", adm, ".", "c")
check("a guest root: kind pie with the guest script and its own objects, source ld:<member>:pie", calls[0] == ("g", ("pie",), recipe.TREE + "/link/guest.ld", (("u", "/o/u.o"),)) and set(dg["__toy"]) == {"ld:g:pie"}, calls)
check("a host root: the kind its recipe declares, no script", calls[1] == ("h", ("shared",), None, (("u", "/o/u.o"),)) and set(dh["_GLOBAL_OFFSET_TABLE_"]) == {"ld:h:shared"}, calls)

# the derivation itself, with the host ld on toy x86_64 objects (cross case: the farm)
if shutil.which("ld") and shutil.which("gcc"):
    with tempfile.TemporaryDirectory() as td3:
        def build(name, src):
            open(os.path.join(td3, name + ".c"), "w").write(src)
            subprocess.run(["gcc", "-c", "-fPIC", "-O1", os.path.join(td3, name + ".c"), "-o", os.path.join(td3, name + ".o")], check=True)
            subprocess.run(["ld", "-shared", "-z", "now", "-m", "elf_x86_64", "--unresolved-symbols=ignore-all", "--no-gc-sections", "-o", os.path.join(td3, name + ".so"), os.path.join(td3, name + ".o")], check=True)
            return open(os.path.join(td3, name + ".o"), "rb").read(), open(os.path.join(td3, name + ".so"), "rb").read()
        oa, la = build("a", "extern int ext_v; int f(void){ return ext_v; }\n")
        ob, lb = build("b", "int g(void){ return 3; }\n")
        sa, sb = recipe.synth_set(la, [oa]), recipe.synth_set(lb, [ob])
        check("an object with a GOT reference gets _GLOBAL_OFFSET_TABLE_ (and _DYNAMIC) as synthesised, not its own f", "_GLOBAL_OFFSET_TABLE_" in sa and "f" not in sa, sorted(sa))
        check("the same without that reference does not", "_GLOBAL_OFFSET_TABLE_" not in sb and "g" not in sb, sorted(sb))
        check("a name the script assigns is not synthesised", "_GLOBAL_OFFSET_TABLE_" not in recipe.synth_set(la, [oa], "_GLOBAL_OFFSET_TABLE_ = 0;\n"), sorted(recipe.synth_set(la, [oa], "_GLOBAL_OFFSET_TABLE_ = 0;\n")))
sys.exit(1 if fails else 0)
