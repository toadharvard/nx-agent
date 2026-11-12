#!/usr/bin/env python3
"""Tests for verify/graph.py (DAG runner over sandbox stages). Plain script: exit 0 on pass, one PASS/FAIL line per case.
Needs root and a static busybox (busybox-static) as the only host file any stage sees. No --toolflags except one case."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH = os.path.join(os.path.dirname(HERE), "graph.py")
BB = shutil.which("busybox")
ROOT = tempfile.mkdtemp(dir="/root/l4/sbx-test" if os.path.isdir("/root/l4/sbx-test") else os.path.expanduser("~"), prefix="graph-")
FAILED = []
_n = [0]
SH = "B=/bin/busybox; "

def case(name, ok, detail=""):
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, "" if ok else detail))
    if not ok:
        FAILED.append(name)

def tmp(kind):
    _n[0] += 1
    return os.path.join(ROOT, "%s%d" % (kind, _n[0]))

def src(text):
    p = tmp("src")
    open(p, "w").write(text)
    return p

def stage(name, script, ins=(), outs=("/out",), **kw):
    """ins: (at, spec) with spec 'src:NAME' or 'NAME' (stage) or 'NAME/sub/path'; busybox is always /bin/busybox."""
    inputs = [{"at": "/bin/busybox", "source": "bb"}]
    for at, spec in ins:
        if spec.startswith("src:"):
            inputs.append({"at": at, "source": spec[4:]})
        else:
            n, _, p = spec.partition("/")
            inputs.append(dict({"at": at, "stage": n, "output": "/out"}, **({"path": p} if p else {})))
    d = {"name": name, "class": "build", "tool": "other", "argv": ["/bin/busybox", "sh", "-c", SH + script], "cwd": "/work",
         "env": {}, "inputs": inputs, "outputs": list(outs)}
    d.update(kw)
    return d

def graph(sources, stages, **kw):
    g = {"format": "fnvnx-graph/1", "sources": dict(sources, bb=BB), "stages": stages}
    g.update(kw)
    p = tmp("graph") + ".json"
    json.dump(g, open(p, "w"))
    return p

def run(cmd, store, gpath, *extra):
    rp = tmp("report") + ".json"
    args = [sys.executable, "-I", GRAPH, cmd, "--store", store, gpath] + list(extra)
    if cmd == "run":
        args += ["--report", rp]
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    rep = json.load(open(rp)) if cmd == "run" and os.path.exists(rp) else None
    return p, rep

def status(rep):
    return {s["name"]: s["status"] for s in rep["stages"]}

def ran(rep):
    return {s["name"] for s in rep["stages"] if s["status"] == "ran"}

def cat(store, rep, name, path="out"):
    s = next(x for x in rep["stages"] if x["name"] == name)
    d = tmp("get")
    subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "sandbox.py"), "get", "--store", store, s["key"], "/out", d],
                   check=True)
    return open(os.path.join(d, path)).read()

def diamond_graph(sa, sc):
    return graph({"sa": sa, "sc": sc}, [
        stage("a", "$B cat /in/s > /out/v", [("/in/s", "src:sa")]),
        stage("b", "$B cat /in/a/v > /out/v; echo b >> /out/v", [("/in/a", "a")]),
        stage("c", "$B cat /in/b/v /in/s > /out/v", [("/in/b", "b"), ("/in/s", "src:sc")]),
        stage("d", "$B cat /in/a/v > /out/v; echo d >> /out/v", [("/in/a", "a")]),
    ])

def t_chain_hit():
    st, sa = tmp("store"), src("one\n")
    g = graph({"sa": sa}, [stage("a", "$B cat /in/s > /out/v", [("/in/s", "src:sa")]),
                           stage("b", "$B cat /in/a/v > /out/v; echo b >> /out/v", [("/in/a", "a")]),
                           stage("c", "$B cat /in/b/v > /out/v; echo c >> /out/v", [("/in/b", "b")])])
    p, r = run("run", st, g)
    case("chain first run", p.returncode == 0 and status(r) == {"a": "ran", "b": "ran", "c": "ran"}, p.stderr[-300:])
    case("chain content", cat(st, r, "c", "v") == "one\nb\nc\n")
    case("chain order and summary", [s["name"] for s in r["stages"]] == ["a", "b", "c"]
         and p.stdout.splitlines()[-1].startswith("graph: 3 stages, 0 hit, 3 ran, 0 failed, "), p.stdout)
    p2, r2 = run("run", st, g)
    case("second run all hits, near zero", status(r2) == {"a": "hit", "b": "hit", "c": "hit"} and r2["seconds_sum"] < 0.5
         and r2["hits"] == 3 and [s["key"] for s in r2["stages"]] == [s["key"] for s in r["stages"]],
         json.dumps(r2)[:300])
    return r

def t_diamond_cone():
    st, sa, sc = tmp("store"), src("x\n"), src("y\n")
    p, r = run("run", st, diamond_graph(sa, sc))
    case("diamond runs", p.returncode == 0 and ran(r) == {"a", "b", "c", "d"} and cat(st, r, "c", "v") == "x\nb\ny\n", p.stderr[-300:])
    open(sc, "w").write("z\n")
    p, r = run("run", st, diamond_graph(sa, sc))
    case("leaf-only source reruns only the leaf", ran(r) == {"c"} and r["hits"] == 3, str(status(r)))
    open(sa, "w").write("w\n")
    p, r = run("run", st, diamond_graph(sa, sc))
    case("root source reruns the whole cone", ran(r) == {"a", "b", "c", "d"}, str(status(r)))
    p, r = run("run", st, diamond_graph(sa, sc), "--jobs", "4")
    case("host path is not part of the key", p.returncode == 0 and r["hits"] == 4)

def t_parallel():
    sl = [stage(n, "$B sleep 1; echo %s > /out/v" % n) for n in "pq"]
    res = {}
    for j in (2, 1):
        st = tmp("store")
        p, r = run("run", st, graph({}, sl), "--jobs", str(j))
        res[j] = (r, [(s["key"], s["outputs"]) for s in r["stages"]])
    # no upper bound on the wall clock here: it depends on machine load (the verdict must not). That --jobs 2 really overlaps
    # stages is not asserted by this test; keys and outputs being independent of --jobs is (below).
    case("jobs 1 at least 2 s", res[1][0]["seconds_wall"] >= 2.0, str(res[1][0]["seconds_wall"]))
    case("keys and outputs independent of --jobs", res[1][1] == res[2][1] and res[1][0]["jobs"] == 1)

def t_failure():
    st = tmp("store")
    g = graph({}, [stage("a", "exit 1"), stage("b", "echo b > /out/v", [("/in/a", "a")]), stage("c", "echo c > /out/v"),
                   stage("d", "echo d > /out/v", [("/in/c", "c")]), stage("e", "echo e > /out/v", [("/in/b", "b")])])
    p, r = run("run", st, g)
    s = {x["name"]: x for x in r["stages"]}
    case("failing producer: consumers not-run, independent branch runs, exit 2",
         p.returncode == 2 and status(r) == {"a": "failed", "b": "not-run", "c": "ran", "d": "ran", "e": "not-run"}
         and "producer a failed" in json.dumps(s["b"]) and r["failed"] == 1, str(status(r)) + p.stderr[-200:])

def t_path():
    st, sa = tmp("store"), src("p\n")
    mk = lambda path: graph({"sa": sa}, [  # noqa: E731
        stage("a", "$B mkdir -p /out/sub; $B cat /in/s > /out/sub/f; echo top > /out/g", [("/in/s", "src:sa")]),
        stage("b", "$B cat /in/f > /out/v", [("/in/f", "a/" + path)])])
    p, r = run("run", st, mk("sub/f"))
    case("path input selects a file", p.returncode == 0 and cat(st, r, "b", "v") == "p\n", p.stderr[-300:])
    p, r = run("run", st, mk("sub/none"))
    b = next(x for x in r["stages"] if x["name"] == "b")
    case("missing path fails the consumer", p.returncode == 2 and b["status"] == "failed" and "sub/none" in json.dumps(b), p.stdout + p.stderr[-200:])

def t_invalid():
    for what, stages in (("cycle", [stage("a", "true", [("/in/b", "b")]), stage("b", "true", [("/in/a", "a")])]),
                         ("unknown", [stage("a", "true", [("/in/z", "z")])]),
                         ("duplicate", [stage("a", "true"), stage("a", "true")])):
        st = tmp("store")
        p, r = run("run", st, graph({}, stages))
        case("invalid graph (%s) exit 1, nothing ran" % what, p.returncode == 1 and not os.path.exists(os.path.join(st, "keys")), p.stderr[-200:])

def t_cold():
    st, sa = tmp("store"), src("c\n")
    g = diamond_graph(sa, sa)
    run("run", st, g)
    p, r = run("run", st, g, "--cold")
    case("--cold on a deterministic graph exits 0", p.returncode == 0, p.stderr[-300:])
    g2 = graph({}, [stage("t", "$B sleep 1; $B date +%s > /out/v")])
    run("run", st, g2)
    p, r = run("run", st, g2, "--cold")
    case("--cold on a wall-clock stage exits 3 and lists the stage", p.returncode == 3 and "NOT A FUNCTION" in p.stderr + p.stdout, p.stderr[-300:])

def t_keys_why():
    st, sa, sc = tmp("store"), src("1\n"), src("2\n")
    g = diamond_graph(sa, sc)
    p = subprocess.run([sys.executable, "-I", GRAPH, "keys", "--store", st, g], stdout=subprocess.PIPE, text=True)
    first = dict(l.split() for l in p.stdout.splitlines())
    case("keys before any run: roots keyed, rest pending", p.returncode == 0 and len(first["a"]) == 64 and first["b"] == "pending", p.stdout)
    p, r = run("run", st, g)
    rp = tmp("old") + ".json"
    json.dump(r, open(rp, "w"))
    p = subprocess.run([sys.executable, "-I", GRAPH, "keys", "--store", st, g], stdout=subprocess.PIPE, text=True)
    case("keys after run equal the report", dict(l.split() for l in p.stdout.splitlines()) == {s["name"]: s["key"] for s in r["stages"]})
    open(sc, "w").write("3\n")
    p, r = run("run", st, g)
    w = subprocess.run([sys.executable, "-I", GRAPH, "why", "--store", st, g, "--against", rp], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
    case("why names the changed input", w.returncode == 0 and "c:" in w.stdout and "/in/s" in w.stdout and "/in/b" not in w.stdout
         and "a:" not in w.stdout, w.stdout + w.stderr)

def t_toolflags():
    st = tmp("store")
    p, r = run("run", st, graph({}, [stage("a", "echo x > /out/v")]), "--toolflags")
    case("--toolflags blocks a bad document, nothing runs", p.returncode == 2 and not os.path.exists(os.path.join(st, "keys")), p.stderr[-200:])

if __name__ == "__main__":
    try:
        for t in (t_chain_hit, t_diamond_cone, t_parallel, t_failure, t_path, t_invalid, t_cold, t_keys_why, t_toolflags):
            t()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
    print("FAILED: %s" % FAILED if FAILED else "all passed")
    sys.exit(1 if FAILED else 0)
