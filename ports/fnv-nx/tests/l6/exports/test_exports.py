#!/usr/bin/env python3
"""test_exports.py: the export surface of our d3d9.dll (render/d3d9/dev/d3d9_exports.cpp).  python3 -I tests/l6/exports/test_exports.py
 a  the resolver's names == the exports with a signature in the interface table (spec), in the table's order
 b  every such name is a defined external C symbol of the product object (the import bridge binds by symbol: bridge.tsv `sym`)
 c  the D3DPERF contract and the factory forwarding (t_exports.cpp)"""
import json, os, re, subprocess, sys, tempfile, shutil
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install()
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEV = os.path.join(ROOT, "render", "d3d9", "dev"); GEN = os.path.join(ROOT, "render", "d3d9", "gen"); SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include")


def sh(cmd): return subprocess.run(cmd, capture_output=True, text=True)


def main():
    fails = []; tmp = tempfile.mkdtemp(prefix="d9exp_")
    r = sh([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC])
    if r.returncode == 0: r = sh([sys.executable, "-I", os.path.join(GEN, "gen_wine_ids.py"), tmp])
    if r.returncode: print("GEN FAILED\n" + r.stderr[-800:]); return 1
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-Wall", "-Werror", "-Wno-unused", "-I", INC, "-I", tmp, "-I", DEV]
    obj = os.path.join(tmp, "d3d9_exports.o"); exe = os.path.join(tmp, "t_exports")
    for cmd in (fl + ["-c", os.path.join(DEV, "d3d9_exports.cpp"), "-o", obj], fl + [os.path.join(HERE, "t_exports.cpp"), obj, "-o", exe]):
        r = sh(cmd)
        if r.returncode: print("BUILD FAILED\n" + r.stderr[-2000:]); return 1
    want = [e["name"] for e in json.load(open(SPEC))["exports"] if not e.get("undocumented")]
    undoc = [e["name"] for e in json.load(open(SPEC))["exports"] if e.get("undocumented")]
    r = sh([exe]); print(r.stdout[-1500:])
    have = re.findall(r"^EXPORT (\S+)$", r.stdout, re.M)
    if have != want: fails.append(f"a: resolver names {have} != spec {want}")
    m = re.search(r"PART exports: (\d+) checks, (\d+) failed", r.stdout)
    if r.returncode or not m or int(m.group(2)) or not int(m.group(1)): fails.append(f"c: {m.groups() if m else r.returncode}")
    defined = set(re.findall(r"^[0-9a-f]+ T (\S+)$", sh(["nm", "--defined-only", "-g", obj]).stdout, re.M))
    miss = [n for n in want if n not in defined]
    if miss: fails.append(f"b: no external symbol for {miss}")
    print(f"exports: spec with signature {len(want)}, resolved {len(have)}, external symbols {len(want) - len(miss)}; spec without signature (not exported by us) {len(undoc)}: {undoc}")
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
