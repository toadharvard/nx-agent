#!/usr/bin/env python3
"""graph: the incremental build. A build is a DAG of hermetic sandbox stages (sandbox.py, "fnvnx-stage/1"); a stage's key
hashes its declared inputs, so an unchanged stage is a HIT and never executes; independent stages run in parallel.

    graph.py run  --store S GRAPH.json [--jobs N] [--cold] [--report R.json] [--toolflags]   exit 0, 1 bad graph, 2 failed/violation, 3 cold diff
    graph.py keys --store S GRAPH.json                      name -> key (`pending` while a producer has no stored result); runs nothing
    graph.py why  --store S GRAPH.json --against OLD.json   for each stage whose key differs from OLD_REPORT: the changed input/field

GRAPH "fnvnx-graph/1": {"sources": {NAME: host path}, "runtimes": {NAME: runtime.json}, "targets": [names] (default all), "stages":
 [{"name","class","tool","runtime"?,"argv","cwd","env"?,"outputs":[dirs],"inputs":[{"at", ONE OF "source":NAME | "tree":HASH |
 "stage":NAME,"output":DIR,"path"?:file or subtree of that output}]}]}. Sources are ingested at start; the host path is in no key.
A stage with "runtime" gets runtime.py's inputs, env (TZ/LC_ALL/SOURCE_DATE_EPOCH/COLUMNS/PATH, then the stage's) and argv prefix
[python,-s,-B]. Concrete documents are stored as STORE/keys/<key>/stage.json next to result.json (the data of `why`). Each stage runs as
`python3 -I sandbox.py run` in a child process. --jobs only schedules: it is in no key, document or output. Stage report order =
graph order. A producer with rc != 0 makes every transitive consumer `not-run`; a "path" absent from the producer's output fails
the consumer (a graph error, not skipped). --toolflags checks every document BEFORE anything runs; a document's violations depend
on argv/env/tool/input `at` only (not on tree hashes), so documents of not-yet-run consumers are checked with placeholder trees.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cas  # noqa: E402
import sandbox  # noqa: E402
import toolflags  # noqa: E402

FORMAT = "fnvnx-graph/1"
PLACEHOLDER, COLD_MARK = "0" * 64, "NOT A FUNCTION OF THE KEY"

class GraphError(Exception):
    pass

def load_graph(path, store):
    g = json.load(open(path))
    if not (isinstance(g, dict) and g.get("format") == FORMAT and isinstance(g.get("stages"), list)):
        raise GraphError("not a %s document" % FORMAT)
    by, order = {}, []
    for s in g["stages"]:
        if not (isinstance(s, dict) and isinstance(s.get("name"), str)) or s["name"] in by:
            raise GraphError("stage without a name, or duplicate stage name")
        by[s["name"]] = s
        order.append(s["name"])
    rts = {n: json.load(open(p)) for n, p in g.get("runtimes", {}).items()}
    deps = {}
    for n, s in by.items():
        deps[n] = []
        if s.get("runtime") is not None and s["runtime"] not in rts:
            raise GraphError("stage %s: unknown runtime %s" % (n, s["runtime"]))
        for i in s.get("inputs", []):
            kinds = [k for k in ("source", "stage", "tree") if k in i]
            if len(kinds) != 1 or not isinstance(i.get("at"), str):
                raise GraphError("stage %s: input needs 'at' and exactly one of source/stage/tree" % n)
            if "source" in i and i["source"] not in g.get("sources", {}):
                raise GraphError("stage %s: unknown source %s" % (n, i["source"]))
            if "stage" in i:
                if i["stage"] not in by:
                    raise GraphError("stage %s: unknown stage %s" % (n, i["stage"]))
                if i.get("output") not in by[i["stage"]].get("outputs", []):
                    raise GraphError("stage %s: %s has no output %s" % (n, i["stage"], i.get("output")))
                deps[n].append(i["stage"])
    state = {}

    def visit(n):
        if state.get(n) == 1:
            raise GraphError("cycle through stage %s" % n)
        if n not in state:
            state[n] = 1
            for d in deps[n]:
                visit(d)
            state[n] = 2
    want = g.get("targets")
    for t in want or []:
        if t not in by:
            raise GraphError("unknown target %s" % t)
    for n in order:
        visit(n)
    if want:
        need, todo = set(), list(want)
        while todo:
            n = todo.pop()
            if n not in need:
                need.add(n)
                todo += deps[n]
        order = [n for n in order if n in need]
    g.update(by=by, order=order, deps=deps, rts=rts, srch={n: cas.ingest(p, store) for n, p in g.get("sources", {}).items()})
    for n in order:
        sandbox.check_doc(doc_of(g, by[n], lambda i: PLACEHOLDER))
    return g

def doc_of(g, s, tree_of):
    argv, env = list(s["argv"]), dict(s.get("env", {}))
    ins = [{"at": i["at"], "tree": tree_of(i)} for i in s.get("inputs", [])]
    if s.get("runtime") is not None:
        rt = g["rts"][s["runtime"]]
        e = {k: v for k, v in rt["env"].items() if k != "PYTHONNOUSERSITE"}
        e.update({"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80", "PATH": os.path.dirname(rt["python"])})
        e.update(env)
        argv, env, ins = [rt["python"], "-s", "-B"] + argv, e, rt["inputs"] + ins
    d = {"format": "fnvnx-stage/1", "stage": s["name"], "argv": argv, "cwd": s.get("cwd", "/"), "env": env,
         "inputs": sorted(ins, key=lambda i: i["at"]), "outputs": sorted(s["outputs"]), "class": s.get("class", "build")}
    if "tool" in s:
        d["tool"] = s["tool"]
    return d

def select(h, path, store, what):
    want = path.strip("/")
    for rel, e in cas.listing(h, store):
        if rel == want:
            if e["t"] == "d":
                return e["h"]
            data = cas.canon(e)
            sha = hashlib.sha256(data).hexdigest()
            cas._put(store, "tree", sha + ".json", data)
            return sha
    raise GraphError("%s: path %r is not in the producer's output tree" % (what, path))

def resolver(g, s, outs, store):
    def tree_of(i):
        if "source" in i:
            return g["srch"][i["source"]]
        if "tree" in i:
            return i["tree"]
        h = outs[i["stage"]][i["output"]]
        return select(h, i["path"], store, "stage %s input %s (from %s)" % (s["name"], i["at"], i["stage"])) if "path" in i else h
    return tree_of

def result_of(store, key):
    p = os.path.join(store, "keys", key, "result.json")
    return json.load(open(p)) if os.path.exists(p) else None

def plan(g, store):
    """{name: (doc, key) | None} for stages whose producers all have stored results (the others are None)."""
    sid, out, outs = sandbox.sandbox_id(), {}, {}
    for n in g["order"]:
        if all(d in outs for d in g["deps"][n]):
            doc = doc_of(g, g["by"][n], resolver(g, g["by"][n], outs, store))
            key = cas.stage_key(dict(doc, sandbox=sid))
            out[n] = (doc, key)
            res = result_of(store, key)
            if res:
                outs[n] = res["outputs"]
        else:
            out[n] = None
    return out

def write_doc(store, key, doc):
    d = os.path.join(store, "keys", key)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".stage-%d.json" % os.getpid())
    open(tmp, "wb").write(cas.canon(doc) + b"\n")
    cas._publish(tmp, os.path.join(d, "stage.json"))

def child(store, key, cold):
    cmd = [sys.executable, "-I", os.path.join(HERE, "sandbox.py"), "run", "--store", store,
           os.path.join(store, "keys", key, "stage.json")] + ["--cold"] * cold
    t = time.time()
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    return p, time.time() - t

def run_graph(g, store, jobs, cold, toolflags_on):
    by, order, deps = g["by"], g["order"], g["deps"]
    if toolflags_on:
        bad = [(n, v) for n in order for v in toolflags.check_doc(doc_of(g, by[n], lambda i: PLACEHOLDER))]
        for n, v in bad:
            print("%s: %s" % (n, " ".join(map(str, v))), file=sys.stderr)
        if bad:
            return None, 2
    sid, recs, outs, running = sandbox.sandbox_id(), {}, {}, {}
    ex = cf.ThreadPoolExecutor(jobs)

    def finish(n, key, res, secs, extra=None):
        rec = {"name": n, "key": key, "rc": res["rc"], "seconds": round(secs, 3), "outputs": res["outputs"],
               "status": "failed" if res["rc"] else None}
        rec.update(extra or {})
        recs[n] = rec
        if not res["rc"]:
            outs[n] = res["outputs"]
        return rec

    def fail(n, key, msg, secs, **kw):
        recs[n] = dict({"name": n, "key": key, "status": "failed", "rc": 1, "seconds": round(secs, 3), "outputs": {}, "error": msg}, **kw)
    waiting = list(order)
    while waiting or running:
        for n in list(waiting):
            if not all(d in recs for d in deps[n]):
                continue
            bad = [d for d in deps[n] if recs[d]["status"] in ("failed", "not-run")]
            if bad:
                recs[n] = {"name": n, "key": None, "status": "not-run", "rc": None, "seconds": 0, "outputs": {},
                           "reason": "producer %s failed" % bad[0]}
                waiting.remove(n)
                continue
            t = time.time()
            try:
                doc = doc_of(g, by[n], resolver(g, by[n], outs, store))
            except GraphError as e:
                fail(n, None, str(e), time.time() - t)
                waiting.remove(n)
                continue
            key = cas.stage_key(dict(doc, sandbox=sid))
            res = result_of(store, key)
            if res is not None and not cold:
                finish(n, key, res, time.time() - t, {"status": "failed" if res["rc"] else "hit"})
                waiting.remove(n)
            elif len(running) < jobs:
                write_doc(store, key, doc)
                running[ex.submit(child, store, key, cold)] = (n, key)
                waiting.remove(n)
        if running:
            done, _ = cf.wait(list(running), return_when=cf.FIRST_COMPLETED, timeout=None if not waiting else 0.05)
            for f in done:
                n, key = running.pop(f)
                p, secs = f.result()
                res = result_of(store, key)
                if p.returncode == 3 and COLD_MARK in p.stderr:
                    fail(n, key, "cold run differs", secs, rc=3, cold_diff=p.stderr.strip().splitlines())
                elif res is None:
                    fail(n, key, "sandbox: " + p.stderr.strip()[-500:], secs)
                else:
                    finish(n, key, res, secs, {"status": "failed" if res["rc"] else "ran"})
    return [recs[n] for n in order], 0

def cmd_run(a):
    g = load_graph(a.graph, a.store)
    t0 = time.time()
    recs, rc = run_graph(g, a.store, a.jobs, a.cold, a.toolflags)
    if recs is None:
        return rc
    wall = time.time() - t0
    for r in recs:
        print("%s %s %.2f" % (r["name"], r["status"], r["seconds"]))
    cnt = lambda s: sum(r["status"] == s for r in recs)  # noqa: E731
    print("graph: %d stages, %d hit, %d ran, %d failed, %.2f s wall" % (len(recs), cnt("hit"), cnt("ran"), cnt("failed"), wall))
    for r in recs:
        for ln in [r[k] for k in ("error", "reason") if k in r] + r.get("cold_diff", []):
            print("%s: %s" % (r["name"], ln), file=sys.stderr)
    if a.report:
        json.dump({"format": "fnvnx-graph-report/1", "stages": recs, "seconds_wall": round(wall, 3),
                   "seconds_sum": round(sum(r["seconds"] for r in recs), 3), "hits": cnt("hit"), "ran": cnt("ran"),
                   "failed": cnt("failed"), "jobs": a.jobs}, open(a.report, "w"), indent=1)
    return 3 if any(r.get("cold_diff") for r in recs) else 2 if cnt("failed") or cnt("not-run") else 0

def cmd_keys(a):
    g = load_graph(a.graph, a.store)
    for n, p in plan(g, a.store).items():
        print(n, p[1] if p else "pending")
    return 0

def cmd_why(a):
    g = load_graph(a.graph, a.store)
    old = {s["name"]: s["key"] for s in json.load(open(a.against))["stages"]}
    for n, p in plan(g, a.store).items():
        if p is None:
            print("%s: pending (a producer has no stored result)" % n)
        elif old.get(n) == p[1]:
            continue
        elif old.get(n) is None:
            print("%s: new stage (not in the old report)" % n)
        elif not os.path.exists(os.path.join(a.store, "keys", old[n], "stage.json")):
            print("%s: old document keys/%s/stage.json is not stored" % (n, old[n]))
        else:
            od, nd = json.load(open(os.path.join(a.store, "keys", old[n], "stage.json"))), p[0]
            rts = {i["at"] for i in g["rts"][g["by"][n]["runtime"]]["inputs"]} if g["by"][n].get("runtime") is not None else set()
            oi, ni = ({i["at"]: i["tree"] for i in d["inputs"]} for d in (od, nd))
            lines = ["%s %s: %s -> %s" % ("runtime" if at in rts else "input", at, oi.get(at), ni.get(at))
                     for at in sorted(set(oi) | set(ni)) if oi.get(at) != ni.get(at)]
            lines += ["%s: %s -> %s" % (f, json.dumps(od.get(f)), json.dumps(nd.get(f)))
                      for f in ("argv", "env", "cwd", "outputs", "class", "tool", "format") if od.get(f) != nd.get(f)]
            print("%s: %s" % (n, "; ".join(lines) or "sandbox (sandbox.py or cas.py changed)"))
    return 0

def main(argv):
    ap = argparse.ArgumentParser(prog="graph.py")
    ap.add_argument("cmd", choices=("run", "keys", "why"))
    ap.add_argument("--store", required=True)
    ap.add_argument("graph")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    for f in ("--cold", "--toolflags"):
        ap.add_argument(f, action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--against")
    a = ap.parse_args(argv)
    try:
        if a.jobs < 1 or (a.cmd == "why") != bool(a.against):
            raise GraphError("usage: see the module docstring")
        return {"run": cmd_run, "keys": cmd_keys, "why": cmd_why}[a.cmd](a)
    except (GraphError, sandbox.SbxError, cas.CasError, OSError, ValueError, KeyError) as e:
        print("graph: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
