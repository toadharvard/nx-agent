#!/usr/bin/env python3
"""Tests for verify/runtime.py (python runtime as sandbox inputs + stage documents). Plain script: exit 0 on pass,
one PASS/FAIL/SKIP line per case. Needs root (sandbox). Store: /root/l4/rt-test."""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402

BASE = "/root/l4/rt-test"
STORE = BASE + "/store"
WORK = BASE + "/work"
PY = [sys.executable, "-I"]
FAILED = []
TIMES = {}
SCRIPT = r'''
import argparse, collections, hashlib, json, math, re, struct, sys, zlib
p = argparse.ArgumentParser()
p.add_argument("--mod")
a = p.parse_args()
if a.mod:
    __import__(a.mod)
    sys.exit(0)
r = {"sha": hashlib.sha256(b"fnvnx").hexdigest(), "re": re.sub("a+", "b", "caaat"), "struct": struct.pack("<IH", 1, 2).hex(),
     "ctr": collections.Counter("abracadabra").most_common(2), "zlib": zlib.crc32(zlib.compress(b"x" * 1000)),
     "math": math.floor(math.sqrt(1e6)), "hash_a": hash("a"), "set_order": list({"b", "a", "c"}), "ver": sys.version}
json.dump(r, open("/out/r.json", "w"), sort_keys=True)
'''


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + detail) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)


def rt(*args):
    return run(PY + [os.path.join(VERIFY, "runtime.py")] + list(args))


def sbx(*args):
    return run(PY + [os.path.join(VERIFY, "sandbox.py")] + list(args))


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(WORK + "/in")
    open(WORK + "/in/calc.py", "w").write(SCRIPT)
    real = os.path.realpath(sys.executable)
    t = time.time()
    p = rt("python", "--store", STORE, "--python", real, "-o", WORK + "/runtime.json")
    TIMES["build cold"] = time.time() - t
    check("build runtime (cold store)", p.returncode == 0 and os.path.exists(WORK + "/runtime.json"), p.stdout[-400:])
    if p.returncode:
        return
    t = time.time()
    p = rt("python", "--store", STORE, "--python", real, "-o", WORK + "/runtime2.json")
    TIMES["build warm"] = time.time() - t
    r1, r2 = json.load(open(WORK + "/runtime.json")), json.load(open(WORK + "/runtime2.json"))
    check("runtime_id stable across two builds", p.returncode == 0 and r1 == r2 and r1["runtime_id"] == cas.stage_key(r1["inputs"]))
    check("runtime.json shape", r1["format"] == "fnvnx-runtime/1" and r1["python"] == real and r1["version"] == sys.version
          and r1["env"] == {"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
          and [i["at"] for i in r1["inputs"]] == sorted(i["at"] for i in r1["inputs"]))
    ats = [i["at"] for i in r1["inputs"]]
    check("no input nests in another", not any(a != b and a.startswith(b + "/") for a in ats for b in ats))
    check("no __pycache__ in the runtime", not any("__pycache__" in r for i in r1["inputs"] for r, _ in cas.listing(i["tree"], STORE)))
    n = size = 0
    for i in r1["inputs"]:
        for r, e in cas.listing(i["tree"], STORE):
            if e["t"] == "f":
                n += 1
                size += os.path.getsize(os.path.join(STORE, "obj", e["h"][:2], e["h"] + (".x" if e["x"] else "")))
    TIMES["files"], TIMES["MB"] = n, round(size / 1e6, 1)
    # closure == ldd
    import sysconfig
    ldd = ""
    for f in [real] + sorted(os.path.join(r, n) for r, _, ns in os.walk(sysconfig.get_config_var("DESTSHARED")) for n in ns if n.endswith(".so")):
        ldd += run(["ldd", f]).stdout
    want = {os.path.realpath(m) for m in re.findall(r"=> (/\S+)", ldd)} | {os.path.realpath(m) for m in re.findall(r"^\s*(/\S+) \(", ldd, re.M)}
    got = {os.path.realpath(i["at"]) for i in r1["inputs"] if cas.manifest(i["tree"], STORE).get("t") == "f"} - {real}
    check("loader closure (interpreter + extension modules) equals ldd (real files)", want == got, "only ldd %s; only runtime %s" % (sorted(want - got), sorted(got - want)))
    # stage
    args = ["stage", "--store", STORE, "--runtime", WORK + "/runtime.json", "--name", "rt-probe", "--class", "verification",
            "--input", "/in=" + WORK + "/in", "--output", "/out", "-o", WORK + "/stage.json", "--", "/in/calc.py"]
    p = rt(*args)
    check("stage document written", p.returncode == 0, p.stdout[-400:])
    d = json.load(open(WORK + "/stage.json"))
    check("stage argv/env", d["argv"] == [real, "-s", "-B", "/in/calc.py"] and d["tool"] == "python" and d["class"] == "verification"
          and d["env"]["PYTHONHASHSEED"] == "0" and d["env"]["PATH"] == os.path.dirname(real) and d["env"]["TZ"] == "UTC")
    p = run(PY + [os.path.join(VERIFY, "toolflags.py"), "check", WORK + "/stage.json"])
    check("stage passes toolflags", p.returncode == 0, p.stdout[-600:])
    run_doc = WORK + "/stage.run.json"
    check("sandbox-accepted run document written next to it", os.path.exists(run_doc))
    t = time.time()
    p = sbx("run", "--store", STORE, run_doc, "--result", WORK + "/res0.json")
    TIMES["sandbox run cold"] = time.time() - t
    check("sandbox run rc 0", p.returncode == 0 and json.load(open(WORK + "/res0.json"))["rc"] == 0, p.stdout[-600:])
    t = time.time()
    p = sbx("run", "--store", STORE, run_doc, "--result", WORK + "/res1.json")
    TIMES["sandbox run HIT"] = time.time() - t
    base = json.load(open(WORK + "/res0.json"))
    check("HIT returns the same result", p.returncode == 0 and json.load(open(WORK + "/res1.json")) == base)
    hs = []
    for k in range(2):
        p = sbx("run", "--store", STORE, run_doc, "--cold", "--result", WORK + "/resc%d.json" % k)
        hs.append((p.returncode, json.load(open(WORK + "/resc%d.json" % k))["outputs"]["/out"] if p.returncode == 0 else p.stdout[-300:]))
    check("--cold twice: identical output hashes (hash seed pinned)", all(h == (0, base["outputs"]["/out"]) for h in hs), str(hs))
    sbx("get", "--store", STORE, base["key"], "/out", WORK + "/got")
    got_json = json.load(open(WORK + "/got/r.json"))
    check("output carries computed values", got_json["hash_a"] == int(run(["python3", "-s", "-B", "-c", "print(hash('a'))"],
          env={"PYTHONHASHSEED": "0", "PATH": os.environ["PATH"]}).stdout) and len(got_json["set_order"]) == 3 and got_json["math"] == 1000)
    # a module outside the runtime
    os.makedirs(WORK + "/hostmod", exist_ok=True)
    open(WORK + "/hostmod/zz_hostonly.py", "w").write("X = 1\n")
    sys.path.insert(0, WORK + "/hostmod")
    p = run(["python3", "-s", "-B", "-c", "import zz_hostonly"], env={"PYTHONPATH": WORK + "/hostmod", "PATH": os.environ["PATH"]})
    check("probe module importable on the host", p.returncode == 0)
    open(WORK + "/in/imp.py", "w").write(SCRIPT)
    d2 = json.load(open(run_doc))
    d2["stage"], d2["argv"] = "rt-missing", d2["argv"][:-1] + ["/in/calc.py", "--mod", "zz_hostonly"]
    json.dump(d2, open(WORK + "/miss.json", "w"))
    p = sbx("run", "--store", STORE, WORK + "/miss.json")
    check("import of a module outside the runtime fails in the sandbox", p.returncode != 0 and '"rc":0' not in p.stdout.replace(" ", ""), p.stdout[-300:])
    # site package
    if run(["python3", "-s", "-c", "import capstone"]).returncode == 0:
        p = rt("python", "--store", STORE, "--python", real, "--site", "capstone", "-o", WORK + "/runtime-cs.json")
        check("build runtime with --site capstone", p.returncode == 0, p.stdout[-400:])
        a2 = ["stage", "--store", STORE, "--runtime", WORK + "/runtime-cs.json", "--name", "rt-capstone", "--class", "verification",
              "--input", "/in=" + WORK + "/in", "--output", "/out", "-o", WORK + "/stage-cs.json", "--", "/in/calc.py", "--mod", "capstone"]
        rt(*a2)
        p = sbx("run", "--store", STORE, WORK + "/stage-cs.run.json", "--result", WORK + "/res-cs.json")
        check("import capstone works inside the sandbox", p.returncode == 0 and json.load(open(WORK + "/res-cs.json"))["rc"] == 0, p.stdout[-300:])
    else:
        print("SKIP capstone cases (python3 -c 'import capstone' fails here)")


if os.geteuid() != 0:
    print("SKIP: needs root")
    sys.exit(0)
main()
print("TIMES " + json.dumps(TIMES, sort_keys=True))
print("%d failed: %s" % (len(FAILED), FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
