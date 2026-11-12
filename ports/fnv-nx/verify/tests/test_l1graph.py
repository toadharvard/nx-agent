#!/usr/bin/env python3
"""Tests for verify/l1graph.py with a synthetic contract dir (tiny fake tools in the argv shapes of the real ones). Plain script:
exit 0 on pass. Needs root (sandbox) and the host python as the runtime. Scratch: /root/l4/l1graph-test* if /root/l4 exists."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
PY = [sys.executable, "-I"]
BASE = tempfile.mkdtemp(dir="/root/l4" if os.path.isdir("/root/l4") else None, prefix="l1graph-test-")
FAILED = testcase.FAILED
TOOL = """import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
NAME = %r
a = sys.argv[1:]
d = dict(zip(a[::2], a[1::2]))
h = [NAME]
def read(p):
    if os.path.isdir(p):
        for n in sorted(os.listdir(p)):
            read(os.path.join(p, n))
    else:
        h.append(open(p).read())
for v in a[1::2]:
    if v.startswith("/in/"):
        read(v)
out = d["--out"]
if NAME == "registry":
    os.makedirs(out, exist_ok=True)
    open(out + "/gates.json", "w").write("g")
    open(out + "/bodysel.tsv", "w").write("b")
else:
    open(out, "w").write("|".join(h))
if NAME in ("n1", "registry", "bodysel"):
    import gatefile
if NAME == "n1":
    gatefile.write("n1", 3, 3, [], "ok")
if NAME == "registry":
    gatefile.write("n2_data", 4, 3, ["x1"], "one site differs")
    gatefile.write("xc", 2, 2, [], "ok")
if NAME == "bodysel":
    gatefile.write("bodysel", 5, 5, [], "ok")
"""
GATEFILE = """import json, os
def write(name, universe, certified, ids, detail):
    d = {"format": "fnvnx-gate/1", "name": name, "status": "green" if universe == certified else "red", "universe": universe,
         "certified": certified, "uncertified": sorted(ids)[:50], "detail": detail}
    open(os.path.join(os.environ["GATE_OUT"], name + ".json"), "w").write(json.dumps(d))
"""
FILES = {"exefacts": "exefacts.py", "n1": "n1.py", "objfacts": "objfacts.py", "registry": "registry.py", "bodysel": "bodysel.py",
         "proto": "proto.py"}


def sh(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def world(name, extra=""):
    """A host location: contract tools, private inputs; returns (contract-src root, inputs.json)."""
    root = os.path.join(BASE, name)
    cd = os.path.join(root, "src", "ports", "fnv-nx", "contract")
    os.makedirs(cd)
    for t, f in FILES.items():
        open(os.path.join(cd, f), "w").write(TOOL % t)
    for f in ("store.py", "gate_data.py", "gate_call.py", "gate_vcall.py", "gate_xc.py"):
        open(os.path.join(cd, f), "w").write("")
    open(os.path.join(cd, "gatefile.py"), "w").write(GATEFILE)
    ins = os.path.join(root, "in")
    for sub in ("msvc", "lo", "lf"):
        os.makedirs(os.path.join(ins, sub))
    for f, c in (("exe", "E"), ("units", "U"), ("universe", "A"), ("msvc/u0.obj", "M"), ("lo/addrmap.tsv", "T"), ("lf/a.o", "O")):
        open(os.path.join(ins, f), "w").write(c)
    j = {"exe": "exe", "units": "units", "msvc": "msvc", "link_out": "lo", "link_fixed": "lf", "universe": "universe"}
    json.dump({k: os.path.join(ins, v) for k, v in j.items()}, open(os.path.join(root, "inputs.json"), "w"))
    return os.path.join(root, "src"), os.path.join(root, "inputs.json")

def make(src, inputs, out):
    return sh(PY + [os.path.join(VERIFY, "l1graph.py"), "make", "--contract-src", src, "--inputs", inputs, "--runtime", BASE + "/runtime.json",
                    "--jobs-in-tools", "2", "-o", out])

def run(graph, store, rep, *extra):
    return sh(PY + [os.path.join(VERIFY, "graph.py"), "run", "--store", store, graph, "--jobs", "2", "--toolflags", "--report", rep] + list(extra))

def verdict(rep, store, out):
    p = sh(PY + [os.path.join(VERIFY, "l1graph.py"), "verdict", "--report", rep, "--store", store, "-o", out])
    return p, json.load(open(out))

def names(rep, st):
    return {s["name"] for s in json.load(open(rep))["stages"] if s["status"] == st}

def main():
    store = BASE + "/store"
    p = sh(PY + [os.path.join(VERIFY, "runtime.py"), "python", "--store", store, "-o", BASE + "/runtime.json"])
    case("runtime built", p.returncode == 0, p.stderr[-300:])
    if p.returncode:
        return
    src, inp = world("host1")
    p = make(src, inp, BASE + "/g1.json")
    case("make", p.returncode == 0, p.stderr[-300:])
    p = run(BASE + "/g1.json", store, BASE + "/r1.json")
    case("run: all six stages ran, toolflags clean", p.returncode == 0 and names(BASE + "/r1.json", "ran") == {"facts", "n1", "obj", "gates", "bodysel", "proto"}, p.stdout[-300:] + p.stderr[-300:])
    p, v = verdict(BASE + "/r1.json", store, BASE + "/v1.json")
    gs = {g["name"]: g for g in v["gates"]}
    case("verdict: red, lists the red gate", p.returncode == 2 and v["verdict"] == "red" and gs["n2_data"]["status"] == "red"
         and (gs["n2_data"]["certified"], gs["n2_data"]["universe"]) == (3, 4) and gs["n1"]["status"] == "green"
         and set(gs) == {"n1", "n2_data", "xc", "bodysel"} and v["failed_stages"] == [], json.dumps(v)[:400])
    r2 = BASE + "/r2.json"
    p = run(BASE + "/g1.json", store, r2)
    case("second run: all hits", p.returncode == 0 and names(r2, "hit") == names(BASE + "/r1.json", "ran"), p.stdout[-200:])
    # editing one tool file re-runs exactly the stages that load it; consumers re-run only if its output bytes change (early cutoff)
    with open(os.path.join(src, "ports/fnv-nx/contract/objfacts.py"), "a") as f:
        f.write("# neutral\n")
    make(src, inp, BASE + "/g1.json")
    r3 = BASE + "/r3.json"
    run(BASE + "/g1.json", store, r3)
    case("tool edit: objfacts only, same output = consumers hit", names(r3, "ran") == {"obj"} and len(names(r3, "hit")) == 5,
         str(names(r3, "ran")))
    with open(os.path.join(src, "ports/fnv-nx/contract/bodysel.py"), "a") as f:
        f.write("# neutral\n")
    make(src, inp, BASE + "/g1.json")
    r4 = BASE + "/r4.json"
    run(BASE + "/g1.json", store, r4)
    case("tool edit: bodysel only", names(r4, "ran") == {"bodysel"}, str(names(r4, "ran")))
    # same content at another host location: identical keys, all hits
    src2, inp2 = world("host2")
    for f in ("objfacts.py", "bodysel.py"):
        shutil.copy(os.path.join(src, "ports/fnv-nx/contract", f), os.path.join(src2, "ports/fnv-nx/contract", f))
    make(src2, inp2, BASE + "/g2.json")
    r5 = BASE + "/r5.json"
    p = run(BASE + "/g2.json", store, r5)
    k = lambda r: [(s["name"], s["key"]) for s in json.load(open(r))["stages"]]  # noqa: E731
    case("two host locations: identical keys", k(r5) == k(r4) and names(r5, "hit") == set(n for n, _ in k(r5)), p.stdout[-300:])
    g = json.load(open(BASE + "/g2.json"))
    keys = json.dumps([s for s in g["stages"]])
    case("no host path inside the stage documents", BASE not in keys)

main()
shutil.rmtree(BASE, ignore_errors=True)
print("FAILED: %s" % FAILED if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
