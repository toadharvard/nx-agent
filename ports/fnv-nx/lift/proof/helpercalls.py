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
Widths (`dwarf_bits`): ONE rule for every helper, whatever source named it: the input bits are the sum of the byte sizes of the DWARF formal_parameter types of the helper's
own definition (DW_TAG_subprogram of that name) in the objects of the root that defines it, times 8. A parameter that carries the MXCSR or another register value is a
parameter, so it is counted; global state read inside a body is not a parameter and is not derived here. A helper with no DWARF definition, a parameter type of unknown size,
a variadic parameter, or two definitions that disagree has NO width: it is red (`helper-width-underived`), never wide.
Classes (`classify`, over a derived width): "narrow" = input bits <= 32 (an exhaustive host check over the whole domain is possible); "wide" = more. The REMOVE record ASM-329 covers exactly the wide class."""
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
    """bits: a derived width (int)"""
    assert isinstance(bits, int)
    return "narrow" if bits <= NARROW_BITS else "wide"


_FOLLOW = ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type", "DW_TAG_atomic_type")


def _size(die):
    """byte size of the type DIE's value, following typedefs and qualifiers; None when the DWARF does not give it"""
    t = die
    while t is not None:
        if t.tag in _FOLLOW:
            if "DW_AT_type" not in t.attributes:
                return None
            t = t.get_DIE_from_attribute("DW_AT_type")
            continue
        a = t.attributes.get("DW_AT_byte_size")
        return a.value if a is not None else None
    return None


def dwarf_bits(paths):
    """-> {helper name: input bits or None} over the object files PATHS: the width rule of the module docstring. None = underived (a definition without usable DWARF, or definitions that disagree)"""
    from elftools.elf.elffile import ELFFile
    out = {}
    for p in paths:
        with open(p, "rb") as fh:
            elf = ELFFile(fh)
            if not elf.has_dwarf_info():
                continue
            for cu in elf.get_dwarf_info().iter_CUs():
                for d in cu.iter_DIEs():
                    if d.tag != "DW_TAG_subprogram" or "DW_AT_name" not in d.attributes or d.get_parent() is None:
                        continue
                    name = d.attributes["DW_AT_name"].value.decode()
                    kids = list(d.iter_children())
                    if not kids and "DW_AT_abstract_origin" in d.attributes:
                        continue      # a concrete instance: the abstract one carries the parameters
                    total = 0
                    for k in kids:
                        if k.tag == "DW_TAG_unspecified_parameters":
                            total = None
                            break
                        if k.tag != "DW_TAG_formal_parameter":
                            continue
                        sz = _size(k.get_DIE_from_attribute("DW_AT_type")) if "DW_AT_type" in k.attributes else None
                        if sz is None:
                            total = None
                            break
                        total += 8 * sz
                    out[name] = total if out.get(name, total) == total else None
    return out


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
