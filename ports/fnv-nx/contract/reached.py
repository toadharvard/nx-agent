#!/usr/bin/env python3
"""reached - the (dll, name) pairs the image set reaches (L1 CONTRACT): reached.tsv, columns dll, name, how, site.

    reached.py --sites dynsites.tsv --image NAME=pe ... --imports NAME=imports.tsv ... --facts DIR --facts-tree SHA
               --declared declared_dlls.tsv --specs DIR [--modules modules.tsv] --out reached.tsv

how = iat: an import slot of an image (`site` = `<image>@<slot VA>`, from the imports/<image>.tsv of modules.py); how = gpa: a (module, name)
pair a GetProcAddress site can ask for (gpa_names.py, `site` = `<image>@<site VA>`; a module the facts cannot place is `*`). One row per
(dll, name, how, site), sorted. The universe of the rows is the images; nothing filters it.

The input that gpa_names.py takes as bridge.tsv (the stack bytes and the argument letters of an import, the dll names of the may-load set) is
derived here from the declared machine alone and is NOT a bridge.tsv row: the signature of an import is the reference spec's (bridgetable.resolve,
the one function that derives a bridge row's conv and args), the may-load set is the images, modules.tsv and declared_dlls.tsv. So the table
L6's bridgefacts filter by reached.tsv is never an input of reached.tsv (no cycle). Every import that has no signature is an error and the
stage lists them all.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name (contract.X)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.contract import bridgetable, declareddlls, dllkind, gpa_names, iat

COLS = ("dll", "name", "how", "site")


def read_imports(text):
    """[(dll, name, slot)] of an imports/<name>.tsv (slot, dll, name, kind, value)."""
    out = []
    for ln in text.splitlines():
        p = ln.split("\t")
        if not ln or ln[0] == "#" or p[0] == "slot":
            continue
        if len(p) < 4:
            raise ValueError("imports row with %d columns, need 4: %r" % (len(p), ln))
        out.append((p[1], p[2], p[0]))
    return out


def signatures(imports, spec_text):
    """-> (text, [error]): one row `dll name ordinal conv ret args native bind` (the 8 columns the gpa_names readers parse) per distinct import (dll, name), from the
    reference spec (bridgetable.resolve); a dll without a spec is a vendor SDK's (cdecl, bridgetable.NOSPEC_CONV). The ordinal and native columns are empty: this
    is a signature table, not a bridge table, and nothing of it binds a symbol."""
    rows, errs, cache = [], [], {}
    for dll, name in sorted({(iat.norm_dll(d), n) for d, n, _ in imports}):
        if dllkind.is_runtime(dll):
            continue
        if not bridgetable.has_spec(spec_text, dll):
            rows.append((dll, name, "", bridgetable.NOSPEC_CONV, "-", "", "-", "none"))
            continue
        try:
            real, ordn, conv, args, _ = bridgetable.resolve(spec_text, dll, name, cache)
        except (KeyError, ValueError, OSError) as e:
            errs.append("%s!%s: %s" % (dll, name, e))
            continue
        rows.append((dll, real, ordn, conv, "-", args, "-", "none"))
    return "\n".join(["\t".join(("dll", "name", "ordinal", "conv", "ret", "args", "native_symbol", "bind"))] + ["\t".join(r) for r in rows]) + "\n", errs


def rows_of(iat_imports, results):
    """[(dll, name, how, site)] sorted: IAT_IMPORTS {image: [(dll, name, slot)]}, RESULTS gpa_names.run(): every pair of every site."""
    out = set()
    for img, imps in iat_imports.items():
        for dll, name, slot in imps:
            out.add((iat.norm_dll(dll), name, "iat", "%s@%s" % (img, slot)))
    for (mod, site, api, operand), d in results:
        for dll, name in d["pairs"]:
            out.add((dll if dll == "*" else iat.norm_dll(dll), name, "gpa", "%s@0x%x" % (mod.lower(), site)))
    return sorted(out)


def tsv(rows):
    return "\n".join(["\t".join(COLS)] + ["\t".join(r) for r in rows]) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", required=True)
    ap.add_argument("--image", action="append", required=True, help="NAME=image path (its runs come from --facts)")
    ap.add_argument("--imports", action="append", required=True, help="NAME=imports/<name>.tsv of modules.py, one per image")
    ap.add_argument("--facts", required=True)
    ap.add_argument("--facts-tree", required=True)
    ap.add_argument("--declared", required=True, help="declared_dlls.tsv: the dlls of the declared machine")
    ap.add_argument("--specs", required=True, help="the Wine 9.0 spec directory (the reference signatures)")
    ap.add_argument("--modules", help="modules.tsv")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    imps = {}
    for s in a.imports:
        nm, p = s.split("=", 1)
        with open(p) as fh:
            imps[nm.lower()] = read_imports(fh.read())

    def spec_text(dll):
        with open(os.path.join(a.specs, dll + ".spec")) as fh:
            return fh.read()
    sig, errs = signatures([i for v in imps.values() for i in v], spec_text)
    if errs:
        sys.exit("reached: %d imports without a signature:\n%s" % (len(errs), "\n".join(errs)))
    mayload = set(declareddlls.read(a.declared))
    if a.modules:
        with open(a.modules) as fh:
            mayload |= set(gpa_names.module_dlls(fh.read()))
    with open(a.sites) as fh:
        rows = gpa_names.read_sites(fh.read())
    _, results = gpa_names.analyse(a.image, a.facts, a.facts_tree, rows, sig, mayload)
    with open(a.out, "w", newline="\n") as fh:
        fh.write(tsv(rows_of(imps, results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
