#!/usr/bin/env python3
"""Generator: the op census of x87spec (X87FORMS.md "Ops": the universe of ops is derived, never written by hand).
    python3 -I x87ops_census.py [OUT.json]       default OUT = x87ops.json next to this file; prints the table
For every entry of x87spec.REGISTRY (every class, and every register index i of a uses_i entry, with the memory operand a free bit-vector of the entry's width and
ef three free Booleans) the entry is run by x87spec.execute on the state that from_dsl builds over the symbolic DSL pre-state (fr0..fr7, ftop, ftag, fsw, fcw, fip, fdp,
fop, fcs, fds as free bit-vectors of x87spec.DSL_WIDTH). Every output term is walked: to_dsl(Out.s), mem, wr, ax, eflags, the fault kind, trap, pre, undef, mem_undef, nd, nd_pre.
The z3 declaration kind of every distinct node (by z3 node id) is collected with its result and argument sorts; a nullary uninterpreted declaration is an INPUT (named,
not an op). Output: {"format": "fnvnx-x87ops/1", "entries": N, "ops": {kind: {"nodes": distinct nodes over all entries, "entries": entries using it, "sorts": [...]}},
"inputs": [...]}. The translator (x87form.py) maps exactly the kinds of this table (X87OPS), and a test checks that the table and the map agree."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import z3  # noqa: E402
import x87spec as X  # noqa: E402

FORMAT = "fnvnx-x87ops/1"
KIND = {getattr(z3, n): n[len("Z3_OP_"):] for n in dir(z3) if n.startswith("Z3_OP_") and isinstance(getattr(z3, n), int)}


def pre_state():
    return {n: z3.BitVec("in_" + n, w) for n, w in X.DSL_WIDTH.items()}


def outputs(key, e, d, i):
    s = X.from_dsl(d)
    m = z3.BitVec("in_m", e["mem_bits"]) if e["mem_bits"] else None
    ef = tuple(z3.Bool("in_ef%d" % k) for k in range(3)) if e["uses_ef"] else None
    o = X.execute(key, s, m, i, ef)
    out = list(X.to_dsl(o.s).values()) + [o.wr, o.trap, o.pre, o.undef, o.nd_pre] + ([X.fault_kind(o)] if o.fcw_in is not None else [])
    for t in (o.mem, o.ax, o.mem_undef):
        if t is not None:
            out.append(t)
    if o.eflags is not None:
        out.extend(o.eflags)
    return out + list(o.nd)


def walk(terms, ops, inputs, seen):
    stack = list(terms)
    while stack:
        t = stack.pop()
        if t.get_id() in seen:
            continue
        seen.add(t.get_id())
        k = t.decl().kind()
        if z3.is_const(t) and k == z3.Z3_OP_UNINTERPRETED:
            inputs.add((t.decl().name(), t.sort().sexpr()))
            continue
        if k in (z3.Z3_OP_BNUM, z3.Z3_OP_TRUE, z3.Z3_OP_FALSE, z3.Z3_OP_FPA_NUM, z3.Z3_OP_FPA_PLUS_INF, z3.Z3_OP_FPA_MINUS_INF, z3.Z3_OP_FPA_NAN,
                z3.Z3_OP_FPA_PLUS_ZERO, z3.Z3_OP_FPA_MINUS_ZERO) and False:
            pass
        r = ops.setdefault(KIND.get(k, "KIND%d" % k), {"nodes": 0, "sorts": set()})
        r["nodes"] += 1
        r["sorts"].add("%s -> %s" % (" ".join(t.arg(j).sort().sexpr() for j in range(t.num_args())), t.sort().sexpr()))
        stack.extend(t.arg(j) for j in range(t.num_args()))


def census():
    ops, inputs, per = {}, set(), {}
    d = pre_state()
    n = 0
    for key in sorted(X.REGISTRY, key=str):
        e = X.REGISTRY[key]
        used = set()
        for i in (range(8) if e["uses_i"] else (None,)):
            seen = set()
            local, inp = {}, set()
            walk(outputs(key, e, d, i), local, inp, seen)
            used |= set(local)
            inputs |= inp
            for k, v in local.items():
                r = ops.setdefault(k, {"nodes": 0, "sorts": set()})
                r["nodes"] += v["nodes"]
                r["sorts"] |= v["sorts"]
        for k in used:
            per[k] = per.get(k, 0) + 1
        n += 1
    return {"format": FORMAT, "entries": n,
            "ops": {k: {"nodes": v["nodes"], "entries": per[k], "sorts": sorted(v["sorts"])} for k, v in sorted(ops.items())},
            "inputs": sorted("%s %s" % x for x in inputs)}


if __name__ == "__main__":
    doc = census()
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "x87ops.json")
    with open(out, "w", newline="\n") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("entries %d, op kinds %d, inputs %d" % (doc["entries"], len(doc["ops"]), len(doc["inputs"])))
    for k, v in doc["ops"].items():
        print("%-24s nodes %-8d entries %d" % (k, v["nodes"], v["entries"]))
