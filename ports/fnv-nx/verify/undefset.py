#!/usr/bin/env python3
"""The full static undefined set of a link, from the inputs' symbol tables (never from ld's text).

closure(products, runtime, archives, dynamic, linker_defined) -> {symbol: [referencers]}:
  products  {label: (defs, strong, weak)}   the objects of the product roots (label `<root>:<unit>`), linked whole
  runtime   {label: (defs, strong, weak)}   the start/end files of the pinned toolchain, linked whole
  archives  [(label, defs, strong, weak)]   the members of the libraries (`-l`): a member is pulled iff it defines a still undefined name, and then
                                            its own references count (the same pull-in rule the linker applies; the fixpoint of it)
  dynamic   set of names                    the .dynsym of the declared system libraries and of the declared loader root's defined set
  linker_defined set of names               the names the link script assigns (script_defined)
Undefined = (strong references of every linked object) minus (defined by a linked object, by a dynamic library, by the linker). A WEAK reference counts
only when the referencing object belongs to PRODUCTS (ruling A: a weak undefined in a startfile or runtime file of the pinned toolchain is the compiler's
runtime, gABI value 0). Each symbol lists every referencing label, sorted."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "link"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "contract"))
import ldcheck  # noqa: E402

SHN_UNDEF, SHN_COMMON = 0, 0xfff2


def object_symbols(d):
    """(defs, strong, weak) of the ELF relocatable bytes D of either class: global and weak definitions (COMMON included), undefined global and weak references."""
    defs, strong, weak = set(), set(), set()
    for n, bind, _, shndx, typ in ldcheck.syms_any(d, 2):
        if typ in (3, 4) or bind not in (1, 2):
            continue
        (defs if shndx != SHN_UNDEF else strong if bind == 1 else weak).add(n)
    return defs, strong, weak


def script_defined(text):
    """The names a link script assigns: `NAME = ...;`, PROVIDE(NAME = ...), PROVIDE_HIDDEN(...), HIDDEN(...) at any depth; comments removed."""
    t = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return set(re.findall(r"(?:^|[;{}\s(])([A-Za-z_.$][\w.$]*)\s*(?:\+|-|\*|/|&|\|)?=(?!=)", t, flags=re.M)) - {"ALIGN", "SORT"}


def closure(products, runtime, archives, dynamic, linker_defined):
    objs = [(k, v, True) for k, v in sorted(products.items())] + [(k, v, False) for k, v in sorted(runtime.items())]
    defined = set(dynamic) | set(linker_defined)
    refs = {}      # symbol -> referencers
    def add(label, v, product):
        defined.update(v[0])
        for n in v[1] | (v[2] if product else set()):
            refs.setdefault(n, set()).add(label)
    for label, v, product in objs:
        add(label, v, product)
    pulled, changed = set(), True
    while changed:
        changed = False
        for i, (label, d, s, w) in enumerate(archives):
            if i not in pulled and d & {n for n in refs if n not in defined}:
                pulled.add(i)
                add(label, (d, s, w), False)
                changed = True
    return {n: sorted(r) for n, r in sorted(refs.items()) if n not in defined}

