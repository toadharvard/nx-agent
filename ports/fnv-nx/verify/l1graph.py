#!/usr/bin/env python3
"""l1graph: the L1 CONTRACT stages and gates as one "fnvnx-graph/1" graph (graph.py), and the merge verdict read from its report.

    l1graph.py make --contract-src DIR --inputs INPUTS.json --runtime runtime.json --jobs-in-tools N -o graph.json
    l1graph.py verdict --report GRAPH_REPORT.json --store S -o verdict.json        exit 0 green, 2 red, 1 usage/format error

make: DIR is a checkout root holding ports/fnv-nx/contract. INPUTS.json (private, host paths live only there and in the
sources of the generated graph, never in a key) names: exe, units, msvc, link_out (dir with addrmap.tsv, aliases.ld), link_fixed
(dir of objects), universe. Stages, each ONE hermetic tool invocation (python -s -B via the runtime), wired by output paths:
    facts    exefacts.py                              obj      objfacts.py   (facts)
    n1       n1.py                  gate n1           gates    registry.py   (facts obj n1) gates n2_data n2_call n2_vcall xc
    bodysel  bodysel.py (n1 gates)  gate bodysel      proto    proto.py      (n1)
Every tool is declared as the FILES it imports (its own file plus the sibling modules in TOOLS), not as its directory: an edit
of one tool file changes the key of exactly the stages that load that file, and those stages' consumers. Gate files go to
GATE_OUT=/gate (output dir), data to /out. Class: all stages are "build". n1, the registry gates and bodysel are the build's
own static certificates (bodysel's input is the gate rows), so "verification" would make provenance reject them; no stage
of this graph is verification-only, hence none has that class. --jobs-in-tools is in argv and so in the keys.
NOT in the graph: gate_xunit, gate_rtti (subprocess `aarch64-linux-gnu-nm` / `-readelf` by bare name), gate_pcrel (same plus
contract/pcrel_abs.py): they need those ELF programs and their loader closure as declared inputs; link/tables.py needs
vtid.json, whose producer vtid.py needs L2's pair file (--pairs).

verdict: every file of every stage output tree (cas listing) of at most GATE_MAX bytes (a gate file holds <= 50 ids) that parses
as JSON with "format" == "fnvnx-gate/1" is a gate record, judged by verdict.py's _gate_result (rc = producing stage's rc).
Verdict green iff >= 1 gate, all green with certified == universe, and no stage failed or was not run.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cas  # noqa: E402
import verdict  # noqa: E402

GATE_MAX = 1 << 20
REG = ["gate_data.py", "gate_call.py", "gate_vcall.py", "gate_xc.py", "registry.py", "gatefile.py", "store.py"]
TOOLS = {"exefacts": ["exefacts.py", "store.py"], "n1": ["n1.py", "exefacts.py", "gatefile.py", "store.py"], "objfacts": ["objfacts.py", "store.py"],
         "registry": REG, "bodysel": ["bodysel.py", "gatefile.py", "store.py"], "proto": ["proto.py", "store.py"]}
C = "/in/contract/"

def make(a):
    inp = json.load(open(a.inputs))
    cdir = os.path.join(a.contract_src, "ports", "fnv-nx", "contract")
    src = {k: inp[k] for k in ("exe", "units", "msvc", "link_out", "link_fixed", "universe")}
    src.update({"c/" + f: os.path.join(cdir, f) for fs in TOOLS.values() for f in fs})
    j = str(a.jobs_in_tools)

    def stage(name, tool, script, args, ins, outs=("/out",), gate=False):
        i = [{"at": C + f, "source": "c/" + f} for f in TOOLS[tool]] + ins
        s = {"name": name, "class": "build", "tool": "python", "runtime": "py", "argv": [C + script] + args, "cwd": "/",
             "inputs": i, "outputs": list(outs)}
        if gate:
            s["env"] = {"GATE_OUT": "/gate"}
        return s

    def so(at, stg, path=None, out="/out"):
        d = {"at": at, "stage": stg, "output": out}
        return dict(d, path=path) if path else d

    def sr(at, k):
        return {"at": at, "source": k}
    exe, units = sr("/in/exe", "exe"), sr("/in/units", "units")
    st = [stage("facts", "exefacts", "exefacts.py", ["--exe", "/in/exe", "--units", "/in/units", "--out", "/out/facts.json", "--jobs", j],
                [exe, units]),
          stage("n1", "n1", "n1.py", ["--exe", "/in/exe", "--units", "/in/units", "--msvc", "/in/msvc", "--out", "/out/n1.json"],
                [exe, units, sr("/in/msvc", "msvc")], ("/out", "/gate"), True),
          stage("obj", "objfacts", "objfacts.py", ["--link", "/in/link", "--facts", "/in/facts.json", "--out", "/out/obj.json", "--jobs", j],
                [sr("/in/link/out", "link_out"), sr("/in/link/fixed", "link_fixed"), so("/in/facts.json", "facts", "facts.json")]),
          stage("gates", "registry", "registry.py", ["--facts", "/in/facts.json", "--obj", "/in/obj.json", "--n1", "/in/n1.json",
                                                   "--link", "/in/link", "--out", "/out"],
                [so("/in/facts.json", "facts", "facts.json"), so("/in/obj.json", "obj", "obj.json"), so("/in/n1.json", "n1", "n1.json"),
                 sr("/in/link/out", "link_out")], ("/out", "/gate"), True),
          stage("bodysel", "bodysel", "bodysel.py", ["--universe", "/in/universe.tsv", "--n1", "/in/n1.json", "--gates", "/in/gates",
                                                    "--out", "/out/bodysel.tsv"],
                [sr("/in/universe.tsv", "universe"), so("/in/n1.json", "n1", "n1.json"), so("/in/gates", "gates")], ("/out", "/gate"), True),
          stage("proto", "proto", "proto.py", ["--n1", "/in/n1.json", "--out", "/out/proto.tsv"], [so("/in/n1.json", "n1", "n1.json")])]
    json.dump({"format": "fnvnx-graph/1", "sources": src, "runtimes": {"py": a.runtime}, "stages": st}, open(a.o, "w"), indent=1)
    return 0

def gates_of(report, store):
    found = []
    for r in report["stages"]:
        for d, h in sorted(r.get("outputs", {}).items()):
            for rel, e in cas.listing(h, store):
                if e.get("t") != "f":
                    continue
                p = cas._obj(store, e)
                if os.path.getsize(p) > GATE_MAX:
                    continue
                try:
                    g = json.load(open(p))
                except ValueError:
                    continue
                if isinstance(g, dict) and g.get("format") == "fnvnx-gate/1" and isinstance(g.get("name"), str):
                    st, u, c, why = verdict._gate_result({"name": g["name"], "result": os.path.basename(p)}, os.path.dirname(p), r["rc"] or 0)
                    found.append({"name": g["name"], "status": st, "universe": u, "certified": c, "seconds": r["seconds"],
                                  "reason": why, "stage": r["name"]})
    return sorted(found, key=lambda g: g["name"])

def cmd_verdict(a):
    rep = json.load(open(a.report))
    if rep.get("format") != "fnvnx-graph-report/1":
        raise ValueError("not a graph report")
    gs = gates_of(rep, a.store)
    bad = [r["name"] for r in rep["stages"] if r["status"] not in ("hit", "ran")]
    green = bool(gs) and not bad and all(g["status"] == "green" and g["certified"] == g["universe"] for g in gs)
    v = {"format": "fnvnx-verdict/1", "gates": gs, "failed_stages": bad, "seconds_wall": rep["seconds_wall"], "hits": rep["hits"],
         "ran": rep["ran"], "verdict": "green" if green else "red"}
    open(a.o, "wb").write(cas.canon(v) + b"\n")
    for g in gs:
        print("%-5s %-10s %d/%d %s" % (g["status"], g["name"], g["certified"], g["universe"], g["reason"][:100]))
    print("verdict %s (%d gates, %d stages not ok, %.1f s wall)" % (v["verdict"], len(gs), len(bad), rep["seconds_wall"]))
    return 0 if green else 2

def main(argv):
    ap = argparse.ArgumentParser(prog="l1graph.py")
    ap.add_argument("cmd", choices=("make", "verdict"))
    ap.add_argument("-o", required=True)
    for k in ("--contract-src", "--inputs", "--runtime", "--report", "--store"):
        ap.add_argument(k)
    ap.add_argument("--jobs-in-tools", type=int)
    a = ap.parse_args(argv)
    need = ("contract_src", "inputs", "runtime", "jobs_in_tools") if a.cmd == "make" else ("report", "store")
    if any(getattr(a, k) is None for k in need):
        print("l1graph: usage: see the module docstring", file=sys.stderr)
        return 1
    try:
        return {"make": make, "verdict": cmd_verdict}[a.cmd](a)
    except (OSError, ValueError, KeyError, cas.CasError) as e:
        print("l1graph: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
