#!/usr/bin/env python3
"""Generator: the op census of x87spec at the grain of the ruling after the first census (X87FORMS.md): the part (i) functions with a floating-point / Real expansion are op symbols
(x87form.instrument, derived from the ast of x87spec.py), the rest is bit-vector, array and logic.
    python3 -I x87ops_census.py [OUT.json]       default OUT = x87ops.json next to this file; prints the table
Every entry of x87spec.REGISTRY (and every register index i of a uses_i entry; the memory operand is a free bit-vector of the entry's width, ef three free Booleans) is run by
x87spec.execute on the from_dsl state over the symbolic DSL pre-state, under x87form.instrument(). Every output term is walked (to_dsl(Out.s), mem, wr, ax, eflags, the fault kind, trap,
pre, undef, mem_undef, nd, nd_pre). Per entry: `plain` = the z3 decl kinds of the DAG, `ops` = the op symbols (name and sorts) it applies, `leaks` = floating-point or Real kinds
that reach the DAG outside a symbol (a part (i) function that is not a pure value function: the translator raises Unsupported for them). Output {"format": "fnvnx-x87ops/2", "entries":
{key: {...}}, "kinds": {kind: entries using it}, "symbols": {symbol: entries using it}}."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]
import z3  # noqa: E402
import x87form as XF  # noqa: E402
import x87spec as X  # noqa: E402

FORMAT = "fnvnx-x87ops/2"
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


def walk(terms):
    """-> (plain kinds, op symbols, leaks) of the DAG under TERMS"""
    seen, plain, ops, leaks = set(), set(), set(), set()
    stack = list(terms)
    while stack:
        t = stack.pop()
        if t.get_id() in seen:
            continue
        seen.add(t.get_id())
        k = t.decl().kind()
        name = KIND.get(k, "KIND%d" % k)
        if k == z3.Z3_OP_UNINTERPRETED:
            if not z3.is_const(t):
                ops.add("%s(%s)->%s" % (t.decl().name(), ",".join(t.arg(j).sort().sexpr() for j in range(t.num_args())), t.sort().sexpr()))
        elif XF._fp_node(t):
            leaks.add(name)
        else:
            plain.add(name)
        stack.extend(t.arg(j) for j in range(t.num_args()))
    return plain, ops, leaks


def census():
    d = pre_state()
    ent, kinds, syms = {}, {}, {}
    with XF.instrument():
        for key in sorted(X.REGISTRY, key=str):
            e = X.REGISTRY[key]
            plain, ops, leaks = set(), set(), set()
            for i in (range(8) if e["uses_i"] else (None,)):
                p, o, l = walk(outputs(key, e, d, i))
                plain |= p
                ops |= o
                leaks |= l
            ent[str(key)] = {"name": e["name"], "plain": sorted(plain), "ops": sorted(ops), "leaks": sorted(leaks)}
            for k in plain:
                kinds[k] = kinds.get(k, 0) + 1
            for o in ops:
                syms[o] = syms.get(o, 0) + 1
    return {"format": FORMAT, "entries": ent, "kinds": dict(sorted(kinds.items())), "symbols": dict(sorted(syms.items()))}


if __name__ == "__main__":
    doc = census()
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "x87ops.json")
    with open(out, "w", newline="\n") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    ent = doc["entries"]
    clean = [k for k, v in ent.items() if not v["ops"] and not v["leaks"]]
    print("entries %d, plain kinds %d, op symbols %d; entries with no symbol and no leak: %d, with leaks: %d" % (
        len(ent), len(doc["kinds"]), len(doc["symbols"]), len(clean), sum(1 for v in ent.values() if v["leaks"])))
    for k, v in doc["symbols"].items():
        print("%-60s %d" % (k, v))
