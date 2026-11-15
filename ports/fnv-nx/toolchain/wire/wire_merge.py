#!/usr/bin/env python3
"""wire_merge.py - the wire set of a program from the per-unit facts of fnvnx_wire (see fnvnx_wire.cc).

  wire_merge.py DIR [--json OUT]     DIR holds the *.wire files of every unit of the program (libnx and the units that include switch.h)

Parameter summaries and a fixpoint over the call graph: W = {(g, j)}: argument j of g reaches a sink.
  (g, j) is in W if a sink fact of g has parameter j in its origin, or an edge (g -> h, k) with (h, k) in W has parameter j in its origin.
A type is wire if it is in the origin of a sink fact, or of an edge into a W member (a type pointer passed to a helper that forwards it).
Result origins: a token r=H in an origin stands for the types of H's returned values (ret facts), closed the same way.
Prints totals; the unresolved are reported, never guessed:
  unk   sink/edge origins without a type (a void*/integer loaded from memory, inline asm), with the places;
  ind   indirect calls that pass a pointer (callee unknown);
  ext   callees that are defined in no unit, not sink functions, and receive a typed pointer (libc, devoptab, ...), by callee name.
Exit 0 always; the caller (check_mirror C9) decides."""
import collections
import glob
import json
import os
import sys


def load(d):
    facts = []
    for p in sorted(glob.glob(os.path.join(d, "*.wire"))):
        for ln in open(p):
            ln = ln.strip()
            if ln:
                facts.append(json.loads(ln))
    return facts


def merge(facts):
    defs = {f["f"] for f in facts if f["k"] == "def"}
    rets = collections.defaultdict(lambda: {"t": set(), "p": set(), "r": set()})
    for f in facts:
        if f["k"] == "ret":
            r = rets[f["f"]]
            r["t"].update(f["t"]); r["p"].update(f["p"]); r["r"].update(f["r"])
    # closure of result tokens: types a call result can carry
    def ret_types(fn, seen=None):
        seen = seen if seen is not None else set()
        if fn in seen:
            return set()
        seen.add(fn)
        out = set(rets[fn]["t"]) if fn in rets else set()
        for g in (rets[fn]["r"] if fn in rets else ()):
            out |= ret_types(g, seen)
        return out
    def types_of(f):
        out = set(f["t"])
        for g in f["r"]:
            out |= ret_types(g)
        return out
    W = set()
    sinks = [f for f in facts if f["k"] == "sink"]
    edges = [f for f in facts if f["k"] == "edge"]
    for f in sinks:
        for j in f["p"]:
            W.add((f["f"], j))
    changed = True
    while changed:
        changed = False
        for e in edges:
            if tuple(e["to"]) in W:
                for j in e["p"]:
                    if (e["f"], j) not in W:
                        W.add((e["f"], j)); changed = True
    wire = collections.defaultdict(set)   # type -> reasons
    for f in sinks:
        for t in types_of(f):
            wire[t].add(f["why"])
    for e in edges:
        if tuple(e["to"]) in W:
            for t in types_of(e):
                wire[t].add("via " + e["to"][0])
    unk = [(f["f"], f.get("at", "?"), f["u"]) for f in sinks + [e for e in edges if tuple(e["to"]) in W] if f["u"]]
    ind = sorted({(f["f"], f["at"]) for f in facts if f["k"] == "ind"})
    ext = collections.Counter(e["to"][0] for e in edges if e["to"][0] not in defs and e["t"])
    return {"wire": {t: sorted(r) for t, r in sorted(wire.items())}, "params": len(W), "unk": unk, "ind": ind, "ext": dict(ext)}


def layouts(facts):
    out = {}
    for f in facts:
        if f["k"] == "layout":
            out[f["t"]] = (f["size"], f["align"], [tuple(m) for m in f["m"]])
    return out


def layout_diff(wire, l32, l64):
    """Wire types whose layout is not identical under ILP32 and LP64 (size, alignment, member offsets/sizes/kinds), or that are a
    model-dependent scalar (pointer, long) used as a wire object. A type present in one run only differs."""
    bad = {}
    for t in wire:
        kind = t.split(" ", 1)[0]
        if kind in ("ptr",) or t.startswith("scalar long"):
            bad[t] = "model-dependent scalar"
        elif t in l32 and t in l64 and l32[t] != l64[t]:
            a, b = l32[t], l64[t]
            bad[t] = "size %d/%d align %d/%d members differ %d" % (a[0], b[0], a[1], b[1], sum(1 for x, y in zip(a[2], b[2]) if x != y) + abs(len(a[2]) - len(b[2])))
        elif (t in l32) != (t in l64):
            bad[t] = "present in one model only"
    return bad


def main(argv):
    d = argv[1]
    facts = load(d)
    res = merge(facts)
    if "--lp64" in argv:
        bad = layout_diff(res["wire"], layouts(facts), layouts(load(argv[argv.index("--lp64") + 1])))
        res["layout_diff"] = bad
        print("wire types whose layout differs between ILP32 and LP64: %d" % len(bad))
    if "--json" in argv:
        json.dump(res, open(argv[argv.index("--json") + 1], "w"), indent=1, sort_keys=True)
    comp = [t for t in res["wire"] if t.split(" ", 1)[0] in ("struct", "union")]
    print("wire types %d (%d struct/union), summarised parameters %d, unresolved: unk %d sites, indirect %d, external callees with typed args %d"
          % (len(res["wire"]), len(comp), res["params"], len(res["unk"]), len(res["ind"]), len(res["ext"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
