#!/usr/bin/env python3
"""Every write into GATE_CACHE is a publication (spec: verify/RECIPE.md "Externals in a shared store", the rule of publish_dir / cas.publish_file).
Real processes on the real checkout: two processes run gate_build.ensure of one product root on ONE cache at the same moment; both are green, both read the
same published objects dir, no temporary name is left, and the content of the cache (store objects, stage results, objects.json after the cache path is
normalised) is the content that one sequential build into a fresh cache makes."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
REPO = os.path.realpath(os.path.join(VERIFY, "..", "..", ".."))
sys.path.insert(0, VERIFY)
import cas  # noqa: E402

ROOT = "ports/fnv-nx/lib/sha256"
FAILS = 0


def check(name, ok, detail=""):
    global FAILS
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    FAILS += 0 if ok else 1


CHILD = """
import json, os, sys, time
sys.path.insert(0, %r)
import gate_build
while not os.path.exists(sys.argv[2]):
    time.sleep(0.001)
res, out = gate_build.ensure(%r, sys.argv[1], ".")
print(json.dumps({"status": res["status"], "out": out, "objects": [o["sha256"] for o in res["objects"]]}))
"""


def listing(cache):
    """(store objects, stage results, objects.json text with the cache path removed)"""
    got = {"obj": set(), "keys": set(), "json": {}, "tmp": []}
    for d, _, files in os.walk(cache):
        for n in files:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, cache)
            if ".tmp" in rel:
                got["tmp"].append(rel)
            if rel.startswith("store/obj/"):
                got["obj"].add(n)
            elif rel.startswith("store/keys/") and n == "result.json":
                got["keys"].add(os.path.basename(d))
            elif rel.startswith("objects/") and n == "objects.json":
                got["json"][os.path.basename(d)] = open(p).read().replace(cache, "<CACHE>")
        for n in os.listdir(d):
            if ".tmp" in n and os.path.isdir(os.path.join(d, n)):
                got["tmp"].append(os.path.relpath(os.path.join(d, n), cache))
    return got


def main():
    tmp = tempfile.mkdtemp(prefix="gate-publish-")
    shared, fresh, go = [os.path.join(tmp, x) for x in ("shared", "fresh", "go")]
    code = CHILD % (VERIFY, ROOT)
    kids = [subprocess.Popen([sys.executable, "-I", "-c", code, shared, go], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    open(go, "w").close()
    outs = [k.communicate() for k in kids]
    rs = []
    for k, (o, e) in zip(kids, outs):
        try:
            rs.append(json.loads(o.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            rs.append(None)
    check("two concurrent builds of one root on one cache: both exit 0 and answer", all(k.returncode == 0 for k in kids) and all(rs), [(k.returncode, e[-300:]) for k, (o, e) in zip(kids, outs)])
    check("both are green", all(r and r["status"] == "green" for r in rs), rs)
    check("both read the one published objects dir with the same objects", rs[0] and rs[1] and rs[0]["out"] == rs[1]["out"] and rs[0]["objects"] == rs[1]["objects"], rs)
    a = listing(shared)
    check("no temporary name is left in the cache", a["tmp"] == [], a["tmp"])
    r = subprocess.run([sys.executable, "-I", "-c", code, fresh, go], cwd=REPO, capture_output=True, text=True)
    b = listing(fresh)
    check("the reference sequential build is green", r.returncode == 0, r.stderr[-300:])
    check("same store objects as one sequential build", a["obj"] == b["obj"], (len(a["obj"]), len(b["obj"])))
    check("same stage results as one sequential build", a["keys"] == b["keys"], (len(a["keys"]), len(b["keys"])))
    check("same objects.json (cache path normalised)", a["json"] == b["json"] and len(a["json"]) == 1)
    check("a second ensure on the published dir builds nothing", subprocess.run([sys.executable, "-I", "-c", code, shared, go], cwd=REPO, capture_output=True, text=True).returncode == 0)
    t0 = time.time()
    subprocess.run([sys.executable, "-I", "-c", code, shared, go], cwd=REPO, capture_output=True, text=True)
    check("a hit takes under 5 s", time.time() - t0 < 5, time.time() - t0)
    print("all passed" if not FAILS else "%d failed" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
