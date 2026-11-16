#!/usr/bin/env python3
"""reached - the (dll, name) pairs the image set reaches (L1 CONTRACT): reached.tsv, columns dll, name, how, site.

    reached.py --sites dynsites.tsv --dir DIR ... --import-names import_names.tsv --facts DIR
               --declared declared_dlls.tsv --specs DIR --modules modules.tsv --out reached.tsv

how = iat: an import slot of an image (`site` = `<image>@<slot VA>`, from the import_names.tsv of modules.py's first entry plus the image base of modules.tsv: no bridge table, no cycle); how = gpa: a (module, name)
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
from fnvnx.contract import bridgetable, declareddlls, dllkind, gpa_names, iat, modules, treehash

COLS = ("dll", "name", "how", "site")


def read_bases(modules_text):
    """{image name: assigned base} of a modules.tsv (name, sha256, size, preferred_base, assigned_base, ...; the exe's row is the first image)."""
    return {p[0]: int(p[4], 16) for p in (ln.split("\t") for ln in modules_text.splitlines() if ln and ln[0] != "#")}


def read_import_names(text, bases):
    """{image: [(dll, name, slot VA as 0x<hex>)]} of an import_names.tsv (image, dll, name, slot RVA: the PE import directories alone, written by modules.py's first entry);
    the slot VA is the image's assigned base (BASES, read_bases) plus the RVA. An image without a base is an error."""
    out, missing = {}, set()
    for ln in text.splitlines():
        if not ln or ln[0] == "#":
            continue
        p = ln.split("\t")
        if len(p) != 4:
            raise ValueError("import_names row with %d columns, need 4: %r" % (len(p), ln))
        if p[0] not in bases:
            missing.add(p[0])
            continue
        out.setdefault(p[0], []).append((p[1], p[2], "0x%x" % (bases[p[0]] + int(p[3], 16))))
    if missing:
        raise ValueError("no base in modules.tsv for: " + ", ".join(sorted(missing)))
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
    ap.add_argument("--dir", action="append", required=True, help="where the image files are looked for (the declared file set); the images are the names of modules.tsv")
    ap.add_argument("--import-names", required=True, help="import_names.tsv of modules.py (first entry: the import directories alone, no bridge table)")
    ap.add_argument("--facts", required=True)
    ap.add_argument("--declared", required=True, help="declared_dlls.tsv: the dlls of the declared machine")
    ap.add_argument("--specs", required=True, help="the Wine 9.0 spec directory (the reference signatures)")
    ap.add_argument("--modules", required=True, help="modules.tsv (the image bases and the may-load names)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    with open(a.modules) as fh:
        modules_text = fh.read()
    with open(a.import_names) as fh:
        imps = {k.lower(): v for k, v in read_import_names(fh.read(), {k.lower(): v for k, v in read_bases(modules_text).items()}).items()}

    def spec_text(dll):
        with open(os.path.join(a.specs, dll + ".spec")) as fh:
            return fh.read()
    sig, errs = signatures([i for v in imps.values() for i in v], spec_text)
    if errs:
        sys.exit("reached: %d imports without a signature:\n%s" % (len(errs), "\n".join(errs)))
    mayload = set(declareddlls.read(a.declared)) | set(gpa_names.module_dlls(modules_text))
    with open(a.sites) as fh:
        rows = gpa_names.read_sites(fh.read())
    images = []
    for nm in sorted(read_bases(modules_text)):   # the image set is the rows of modules.tsv (exe included), each file found case-insensitively in the declared file set
        path = modules.find_file(a.dir, nm)
        if path is None:
            sys.exit("reached: image %s of modules.tsv has no file in %s" % (nm, ", ".join(a.dir)))
        images.append("%s=%s" % (nm, path))
    _, results = gpa_names.analyse(images, a.facts, treehash.tree_hash(a.facts), rows, sig, mayload)   # the facts pin is the tree hash of the mounted directory
    with open(a.out, "w", newline="\n") as fh:
        fh.write(tsv(rows_of(imps, results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
