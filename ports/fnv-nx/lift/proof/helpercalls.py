#!/usr/bin/env python3
"""The Form -> rt helper call table, the one source of the helper universe of gate fpexact check 0.

A Form (fnvlift/dsl.py) is a DAG of nodes; the lifter's emitter (dsl.InlineEmit, the one that emits Forms into lifted functions) writes a node either as an inline
expression or as a call of an lift/rt helper. This module asks the emitter itself: `node_call(n)` emits the node and reports the callee iff the text of the
expression is a call (an identifier directly followed by an opening parenthesis), with the INPUT BITS of the call = the sum of the widths of the node's operands.
No name list and no pattern over source text: a node op that a later change of the emitter turns into a call enters the table by itself.
Not in the table, by rule: `uf` nodes (the declared platform functions, certified through the declared column of the form certificates, proof/declared.json).
`table(keyed_forms)` -> {callee: {"ops", "callers"}}; `universe(repo, cache)` -> the rows {helper, callers}, the importable form of the universe.
`pack_forms(cache)` -> [(shape key, Form)] of every shape of the lifted pack of formgate_inputs.json that has a symbolic builder (the shapes that formgate judges);
raises formgate.Red when the inputs are not declared.
Every helper of the table is covered by the REMOVE record ASM-329 (any width), or by rcpcert when its defining unit is the rcpcert table unit (decided by gate_fpexact from the object symbol tables)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
sys.path.insert(0, LIFT)
from fnvnx.lift.fnvlift import dsl  # noqa: E402
from fnvnx.lift.proof import formgate, shape, symform, x86sys  # noqa: E402

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


def table(keyed_forms):
    """KEYED_FORMS [(shape key, Form)] -> {callee: {"ops": sorted node ops that call it, "callers": sorted shape keys}}; no widths: every helper is covered by ASM-329 or rcpcert"""
    t = {}
    for key, f in keyed_forms:
        for n in nodes(f.roots()):
            c = node_call(n)
            if c:
                e = t.setdefault(c[0], {"ops": set(), "callers": set()})
                e["ops"].add(n.op)
                e["callers"].add(key)
    return {k: {"ops": sorted(v["ops"]), "callers": sorted(v["callers"])} for k, v in sorted(t.items())}


def pack_forms(cache, repo=None):
    x86sys.configure(formgate.X86SYS if repo is None else os.path.join(repo, "ports", "fnv-nx", "platform", "declared", "x86sys.json"))
    doc = formgate.load_inputs()
    ext = formgate.fetch(doc, cache)
    keys = sorted(formgate.load_shapes(formgate.path_in(ext, doc["shapes"], "shapes")))
    out = []
    for k in keys:
        try:
            out.append((k, symform.symbuild(k).form))
        except (KeyError, AssertionError, symform.SymbolicBranch, symform.FieldDecodeError, shape.ShapeError):
            pass      # no symbolic Form: the shape is uncertified by the forms gate and calls no helper through a Form
    return out


def universe(repo, cache):
    """THE helper universe, importable: (repo = the checkout root, cache = a directory for the externals)
    -> [{"helper": name, "callers": [shape keys of the Forms that call it]}] sorted by helper. Raises formgate.Red when the pack's inputs are not declared.
    The one derivation: the call table of the Forms (this module); nothing else defines a helper."""
    t = table(pack_forms(cache, repo))
    return [{"helper": h, "callers": v["callers"]} for h, v in t.items()]
