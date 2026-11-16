#!/usr/bin/env python3
"""The Form -> rt helper call table, the one source of the helper universe of gate fpexact check 0.

A Form (fnvlift/dsl.py) is a DAG of nodes; the lifter's emitter (dsl.InlineEmit, the one that emits Forms into lifted functions) writes a node either as an inline
expression or as a call of an lift/rt helper. This module asks the emitter itself: `node_call(n)` emits the node and reports the callee iff the text of the
expression is a call (an identifier directly followed by an opening parenthesis), with the INPUT BITS of the call = the sum of the widths of the node's operands.
No name list and no pattern over source text: a node op that a later change of the emitter turns into a call enters the table by itself.
Not in the table, by rule: `uf` nodes (the declared platform functions, certified through the declared column of the form certificates, proof/declared.json).
`table(forms)` -> {callee: {"bits": the largest input bits of any call, "ops": sorted node ops that call it}}.
`pack_forms(cache)` -> the Forms of every shape of the lifted pack of formgate_inputs.json that has a symbolic builder (the shapes that formgate judges);
raises formgate.Red when the inputs are not declared.
Classes (`classify`): "narrow" = input bits <= 32 (an exhaustive host check over the whole domain is possible); "wide" = anything else, which includes a helper
whose width is not derived. The REMOVE record ASM-329 covers exactly the wide class."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
sys.path.insert(0, LIFT)
from fnvlift import dsl  # noqa: E402
from proof import formgate, shape, symform, x86sys  # noqa: E402

NARROW_BITS = 32


def node_call(n):
    """-> (callee, input bits) iff the emitter writes node N as a call of a runtime helper, else None"""
    if n.op in ("const", "var", "uf"):
        return None
    callee, paren, _ = dsl.InlineEmit([n]).ex(n).partition("(")
    if not paren or not callee.isidentifier():
        return None
    return callee, sum(c.w for c in n.a)


def nodes(roots):
    """every node of the DAGs under ROOTS, once"""
    seen, stack, out = set(), list(roots), []
    while stack:
        n = stack.pop()
        if n.u in seen:
            continue
        seen.add(n.u)
        out.append(n)
        stack.extend(n.a)
    return out


def table(forms):
    t = {}
    for f in forms:
        for n in nodes(f.roots()):
            c = node_call(n)
            if c:
                e = t.setdefault(c[0], {"bits": 0, "ops": set()})
                e["bits"] = max(e["bits"], c[1])
                e["ops"].add(n.op)
    return {k: {"bits": v["bits"], "ops": sorted(v["ops"])} for k, v in sorted(t.items())}


def classify(bits):
    return "narrow" if bits is not None and bits <= NARROW_BITS else "wide"


def pack_forms(cache):
    x86sys.configure(formgate.X86SYS)
    doc = formgate.load_inputs()
    ext = formgate.fetch(doc, cache)
    keys = sorted(formgate.load_shapes(formgate.path_in(ext, doc["shapes"], "shapes")))
    out = []
    for k in keys:
        try:
            out.append(symform.symbuild(k).form)
        except (KeyError, AssertionError, symform.SymbolicBranch, symform.FieldDecodeError, shape.ShapeError):
            pass      # no symbolic Form: the shape is uncertified by the forms gate and calls no helper through a Form
    return out
