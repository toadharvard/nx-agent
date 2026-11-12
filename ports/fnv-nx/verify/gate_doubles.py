#!/usr/bin/env python3
"""Gate "doubles" (fnvnx-gate/1): no test defines a symbol that a product root defines (owner order of 2026-11-11, night: "no doubles in tests").
    python3 -I gate_doubles.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/doubles.json (+ doubles.detail.json)
Rule: for every admitted TEST root (admit.json class "test", a recipe like a product root, built by gate_build.ensure with the same machinery),
    (global symbols, strong or weak, defined by the test root's own objects)  ∩  (global symbols defined by any product root's objects) = ∅
No allow-list. The symbols are read from the objects the recipe stored (ELF .symtab, the objects.json "defined" list), nothing is guessed from names.
What counts as a definition BY THE TEST: a defined global/weak symbol of a test object whose section is not in a COMDAT group (SHF_GROUP: an ordinary
  definition, whatever file the text came from), or whose section is in a COMDAT group and whose DWARF declaration file (DW_AT_decl_file of the DIE of
  the definition, through DW_AT_specification / DW_AT_abstract_origin) is a file of the test root (/src/... or an include directory of the recipe that
  lies inside the test root). A COMDAT instance whose declaration is in a product or system header (an inline function, a template, a vtable of the
  product's own headers) is not a test definition. A COMDAT symbol that is also a product symbol and has no DWARF declaration file cannot be decided:
  it is the finding `no-decl-file` (red), never a guess; a COMDAT symbol that no product root defines needs no decision (nothing to double).
Findings {id: "<test root>:<symbol>", code: "double" | "no-decl-file" | "root"}; "root" is a test root whose recipe is invalid or whose build is red.
Universe: one item per global symbol defined by a test object of a test root, plus one per root that did not build. Certified: items that are no finding.
No test root: universe 0, green (there is nothing a test could double). The result carries the global counts: test roots, product roots, test symbols
checked, doubles. The full list of findings is in GATE_OUT/doubles.detail.json; "uncertified" holds the first 50 ids. Exit 0 iff green."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402

SHF_GROUP = 0x200


def test_roots(repo="."):
    return sorted(p for p, r in recipe.load_admit(repo).items() if r["class"] == "test")


def file_table(dw, cu):
    """DWARF line-program file table of CU -> {file index: path} (DWARF 5 indices start at 0, earlier ones at 1)."""
    lp = dw.line_program_for_CU(cu)
    if lp is None:
        return {}
    h, ver = lp.header, lp.header["version"]
    dirs = [d.decode(errors="replace") if isinstance(d, bytes) else d for d in h["include_directory"]]
    out = {}
    for i, fe in enumerate(h["file_entry"]):
        name = fe.name.decode(errors="replace") if isinstance(fe.name, bytes) else fe.name
        di = fe.dir_index
        d = dirs[di] if ver >= 5 else (dirs[di - 1] if di > 0 else "")
        out[i if ver >= 5 else i + 1] = name if name.startswith("/") else os.path.join(d, name)
    return out


def decl_files(path):
    """-> {linkage symbol: set of declaration file paths} of the function and variable DIEs of the object PATH that carry a symbol name (a
    definition DIE takes name and declaration file from its DW_AT_specification / DW_AT_abstract_origin target)."""
    from elftools.elf.elffile import ELFFile
    out = {}
    with open(path, "rb") as f:
        dw = ELFFile(f).get_dwarf_info() if ELFFile(f).has_dwarf_info() else None
        if dw is None:
            return out
        for cu in dw.iter_CUs():
            ft = file_table(dw, cu)

            def attr(die, name):
                """attribute NAME of DIE or of the DIE it refers to by specification / abstract origin (followed)"""
                seen = 0
                while die is not None and seen < 8:
                    if name in die.attributes:
                        return die.attributes[name].value, die
                    ref = die.attributes.get("DW_AT_specification") or die.attributes.get("DW_AT_abstract_origin")
                    die = die.get_DIE_from_attribute(ref.name) if ref else None
                    seen += 1
                return None, None
            for die in cu.iter_DIEs():
                if die.tag not in ("DW_TAG_subprogram", "DW_TAG_variable") or "DW_AT_low_pc" not in die.attributes and "DW_AT_location" not in die.attributes:
                    continue
                link, _ = attr(die, "DW_AT_linkage_name")
                if link is None:
                    link, _ = attr(die, "DW_AT_MIPS_linkage_name")
                if link is None:
                    link, _ = attr(die, "DW_AT_name")   # C: the symbol is the name
                idx, _ = attr(die, "DW_AT_decl_file")
                if link is None or idx is None:
                    continue
                n = link.decode(errors="replace") if isinstance(link, bytes) else link
                out.setdefault(n, set()).add(ft.get(idx, "?"))
    return out


def object_symbols(path):
    """-> {symbol: in_comdat bool} of the global and weak symbols DEFINED by the ELF object PATH (section flag SHF_GROUP = COMDAT member)."""
    from elftools.elf.elffile import ELFFile
    out = {}
    with open(path, "rb") as f:
        e = ELFFile(f)
        st = e.get_section_by_name(".symtab")
        for s in st.iter_symbols() if st else []:
            if s["st_info"]["bind"] not in ("STB_GLOBAL", "STB_WEAK") or not isinstance(s["st_shndx"], int) or s["st_shndx"] == 0 or s["st_shndx"] >= 0xFF00:
                continue
            if s["st_info"]["type"] in ("STT_SECTION", "STT_FILE") or not s.name:
                continue
            out[s.name] = bool(e.get_section(s["st_shndx"])["sh_flags"] & SHF_GROUP)
    return out


def own_dirs(rec, root):
    """Sandbox spelling of the directories that belong to the test root itself: /src and /inc/N for every include directory of the recipe that
    lies inside ROOT (the numbering of recipe.Job.includes: externals are passed by their own path and take no number)."""
    own, n = ["/src"], 0
    ext_at = [e["at"] for e in rec.get("external", [])]
    for d in rec.get("include", []):
        if any(recipe.under(d, a) for a in ext_at):
            continue
        if recipe.under(d, root):
            own.append("/inc/%d" % n)
        n += 1
    return own


def judge_root(root, test_syms, product, decl, own):
    """Pure core. TEST_SYMS {symbol: in_comdat}; PRODUCT set of product symbols; DECL(symbol) -> set of declaration files; OWN list of own dir prefixes.
    -> (findings [(symbol, code)], checked int)."""
    out = []
    for s, comdat in sorted(test_syms.items()):
        if not comdat:
            if s in product:
                out.append((s, "double"))
        elif s in product:
            files = decl(s)
            if not files:
                out.append((s, "no-decl-file"))
            elif any(any(recipe.under(f, o) for o in own) for f in files):
                out.append((s, "double"))
    return out, len(test_syms)


def product_symbols(repo, cache):
    """-> (set of the global symbols defined by the objects of every product root, [product roots that did not build])"""
    syms, bad = set(), []
    for root in gate_build.product_roots(repo):
        try:
            res, out = gate_build.ensure(root, cache, repo)
        except recipe.RecipeError:
            bad.append(root)
            continue
        if res["status"] == "red":
            bad.append(root)
            continue
        syms |= {d["symbol"] for d in res["defined"]}
    return syms, bad


def main():
    repo = "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-doubles-")
    cache = os.environ.get("GATE_CACHE") or tmp
    findings, items, tests, nsyms = [], [], test_roots(repo), 0
    try:
        if tests:
            product, pbad = product_symbols(repo, cache)
            for p in pbad:
                findings.append(("product root %s" % p, "root"))
        for root in tests:
            rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
            try:
                res, out = gate_build.ensure(root, cache, repo)
            except recipe.RecipeError as e:
                items.append(root + ":root")
                findings.append((root + ":root", "root"))
                continue
            if res["status"] == "red":
                items.append(root + ":root")
                findings.append((root + ":root", "root"))
                continue
            for o in res["objects"]:
                path = os.path.join(out, "obj", o["unit"] + ".o")
                syms = object_symbols(path)
                dec = None

                def decl(s, path=path):
                    nonlocal dec
                    if dec is None:
                        dec = decl_files(path)
                    return dec.get(s, set())
                f, n = judge_root(root, syms, product, decl, own_dirs(rec, root))
                nsyms += n
                items += ["%s:%s" % (root, s) for s in syms]
                findings += [("%s:%s" % (root, s), code) for s, code in f]
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    fid = sorted({i for i, _ in findings})
    ndouble = sum(1 for _, c in findings if c == "double")
    universe = len(set(items))
    status = "green" if not findings else "red"
    detail = "%d test roots, %d product roots, %d test symbols checked, %d doubles, %d findings" % (
        len(tests), len(gate_build.product_roots(repo)), nsyms, ndouble, len(findings)) if tests else "no test root: nothing a test could double"
    res = {"format": "fnvnx-gate/1", "name": "doubles", "status": status, "universe": universe, "certified": universe - len(set(fid) & set(items)),
           "uncertified": fid[:50], "detail": detail, "test_roots": len(tests), "symbols_checked": nsyms, "doubles": ndouble}
    out = os.environ["GATE_OUT"]
    with open(os.path.join(out, "doubles.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    with open(os.path.join(out, "doubles.detail.json"), "w") as fh:
        json.dump([{"id": i, "code": c} for i, c in sorted(findings)], fh, indent=1)
    print("doubles: %s %d/%d %s" % (status, res["certified"], universe, detail))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
