#!/usr/bin/env python3
"""Gate "imports" (fnvnx-gate/1, class 4 of VERDICT.md): every import of the manifest has a body, proven by the build products (rules: contract/imports.py).
    python3 -I gate_imports.py      cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/imports.json and GATE_OUT/imports.ids
Roots come only from verify/admit.json (class product); for each the gate calls gate_build.ensure (build or cache hits) and reads the symbol
tables the recipe wrote (objects.json: defined {symbol, bind, type}, undefined). No nm, no ELF parsing, no probe run.
A product root DECLARES its imports in `<root>/imports.in.json` ("fnvnx-imports-in/1"):
  {"format", "external": NAME of an entry of the root's recipe `external` (the private manifest: fnv-nx-build commit + tree_sha256),
   "names": path of sys_names.tsv inside it, "specs": directory of the reference environment's .spec files inside it,
   "extra": [row files inside it, judged against this root], "extra_other": [row files of another owner's build, judged against the other roots],
   "name_table": the msvcrt name table `name TAB symbol TAB func|data` (the same file the root generates its lookup table from), "va": the va record
   `name TAB symbol` (the exports whose native body reads the x86 stack words of the call); every path of the declaration is a resolve_input ref
   (a tracked path of the root, {root, path} of a tool root such as bridgefacts, {external, path} or {stage, path})}
The external is checked out from the build's own fetch (GATE_CACHE/external/<commit>) and its tree hash must equal tree_sha256.
Universe (gate_build.quantify, the convention of build/objstub): the admitted product roots; per root every row of the bridge table (contract/bridgetable.derive). A root without imports.in.json is one red item, unless it is header-only (gate_build.unitless: no unit, no link; it holds no code, so it has no imports by construction): 0 items, or outside the class `import-provider`: the symbol table of its build defines no native symbol of the bridge table (natives_of: the table the declaring roots derive), so it has no import body: 0 items. A root that defines one enters the class and is the one red item. If no root declares imports the table is not known and the root is the one red item. Certified: rows with no class
(contract/imports.py). uncertified: the first 50 ids `dll!name`; imports.ids lists `id TAB class`, sorted. No product root: universe 0, green. Bad input, a red build, an invalid recipe or an
external that fails its hash is red with the reason in detail. Exit 0 iff green."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "contract"))
import bridgetable  # noqa: E402
import cas  # noqa: E402
import gate_build  # noqa: E402
import imports  # noqa: E402
import recipe  # noqa: E402

IN_KEYS = {"format", "names", "specs", "extra", "extra_other", "name_table", "va"}


def symbols(res):
    """{symbol: [(bind, type)]} of an objects.json; a name that is only referenced has [("UND", "NOTYPE")]."""
    out = {}
    for d in res["defined"]:
        out.setdefault(d["symbol"], []).append((d["bind"], d["type"]))
    for u in res["undefined"]:
        out.setdefault(u, []).append(("UND", "NOTYPE"))
    return out


def read(path):
    with open(path, encoding="latin1") as f:
        return f.read()


def bridge_of(root, rec, res, decl, repo, cache):
    """-> (rows, errors, extra_of, table, syms) of the bridge table that ROOT's declaration derives (every path of DECL read through
    recipe.resolve_input); the one derivation of the judged rows and of the import-provider class."""
    if set(decl) != IN_KEYS or decl["format"] != "fnvnx-imports-in/1":
        raise ValueError("%s/imports.in.json: need exactly the keys %s and format fnvnx-imports-in/1" % (root, sorted(IN_KEYS)))
    def rp(ref):
        return recipe.resolve_input(root, ref, repo, cache, rec)[0]
    syms = symbols(res)
    table = bridgetable.read_name_table(read(rp(decl["name_table"])))
    specs = rp(decl["specs"])
    rows, errors, extra_of = bridgetable.derive(
        read(rp(decl["names"])), lambda d: read(os.path.join(specs, d + ".spec")), set(syms), table,
        [read(rp(p)) for p in decl["extra"] + decl["extra_other"]], bridgetable.read_va(read(rp(decl["va"]))))
    return rows, errors, extra_of, table, syms


def root_rows(root, rec, res, decl, repo, cache, others):
    """-> [(id, class)] of one root that declares imports"""
    rows, errors, extra_of, table, syms = bridge_of(root, rec, res, decl, repo, cache)
    if errors:
        raise ValueError("%d bridge errors, first: %s" % (len(errors), errors[0]))
    other = {k for k, i in sorted(extra_of.items()) if i >= len(decl["extra"])}
    return imports.judge(rows, syms, table, others, other)


def natives_of(repo, cache, admit, roots):
    """The set of native symbols of the bridge table of every root that declares imports (rows whose native symbol is `-` name none). The
    import-provider class is a fact over it: a root belongs iff it defines one of these (see judge_root). ValueError when no root declares
    imports: the table is then not known, and no root can be put outside the class."""
    out, declared = set(), 0
    for r2 in roots:
        p = os.path.join(repo, r2, "imports.in.json")
        if not os.path.isfile(p):
            continue
        declared += 1
        with open(p) as f:
            decl = json.load(f)
        res = gate_build.ensure(r2, cache, repo)[0]
        if res["status"] == "red":
            raise ValueError("%s: build red at stage %s" % (r2, res["failure"]["stage"]))
        rows, errors, _, _, _ = bridge_of(r2, recipe.load_recipe(repo, admit, r2), res, decl, repo, cache)
        if errors:
            raise ValueError("%d bridge errors, first: %s" % (len(errors), errors[0]))
        out |= {r[6] for r in rows if r[6] != "-"}
    if not declared:
        raise ValueError("no root declares imports: the bridge table is not known")
    return out


def provider_item(root, repo, cache, admit, roots):
    """-> (members [native symbols the root defines]) for a root with no imports.in.json: the class fact import-provider. A root that defines no
    native symbol of the bridge table has no import body, by the symbol table of its build, so it is outside the gate's universe (0 items); one
    that defines any enters the class and needs its declaration (the caller makes that one red item)."""
    res = gate_build.ensure(root, cache, repo)[0]
    if res["status"] == "red":
        raise ValueError("build red at stage %s" % res["failure"]["stage"])
    return sorted({d["symbol"] for d in res["defined"]} & natives_of(repo, cache, admit, roots))


def judge_root(root, repo, cache, admit, roots, bad):
    """One admitted product ROOT -> (universe, certified, ids, detail) (gate_build.quantify); input errors are red with the reason, never raised.
    BAD collects (id, class) of the uncertified rows."""
    p = os.path.join(repo, root, "imports.in.json")
    try:
        if not os.path.isfile(p):
            if gate_build.unitless(root, cache, repo):
                return 0, 0, [], "header-only root: no code, so no imports (by construction)"
            try:
                members = provider_item(root, repo, cache, admit, roots)
            except (ValueError, KeyError, OSError, subprocess.CalledProcessError, cas.CasError, recipe.RecipeError) as e:
                return 1, 0, [root + ":imports.in.json"], "declares no imports.in.json, and the class import-provider cannot be decided: %s" % (str(e)[:200] or type(e).__name__)
            if not members:
                return 0, 0, [], "outside the class import-provider: defines no native symbol of the bridge table"
            return 1, 0, [root + ":imports.in.json"], "defines %d native symbols of the bridge table (%s) and declares no imports.in.json" % (len(members), ",".join(members[:3]))
        with open(p) as f:
            decl = json.load(f)
        built = {}
        for r2 in roots:
            built[r2] = gate_build.ensure(r2, cache, repo)[0]
            if built[r2]["status"] == "red":
                raise ValueError("%s: build red at stage %s" % (r2, built[r2]["failure"]["stage"]))
        rec = recipe.load_recipe(repo, admit, root)
        others = {}
        for r2 in roots:
            if r2 != root:
                for k, v in symbols(built[r2]).items():
                    others.setdefault(k, []).extend(v)
        rows = root_rows(root, rec, built[root], decl, repo, cache, others)
    except recipe.RecipeError as e:
        return 1, 0, [root + ":recipe"], "recipe invalid: %s" % "; ".join(e.errors)[:200]
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, cas.CasError) as e:
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)
    mine = sorted((rid, c) for rid, c in rows if c)
    bad += mine
    classes = {}
    for _, c in mine:
        classes[c] = classes.get(c, 0) + 1
    return len(rows), len(rows) - len(mine), [rid for rid, _ in mine], ", ".join("%s %d" % kv for kv in sorted(classes.items())) or "ok"


def main():
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-imports-")
    cache, repo, bad = os.environ.get("GATE_CACHE") or tmp, ".", []
    try:
        admit, roots = recipe.load_admit(repo), gate_build.product_roots(repo)
        u, c, ids, detail = gate_build.quantify(repo, lambda root: judge_root(root, repo, cache, admit, roots, bad))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    status = "green" if u == c else "red"
    doc = {"format": "fnvnx-gate/1", "name": "imports", "status": status, "universe": u, "certified": c, "uncertified": ids[:50],
           "detail": detail}
    out = os.environ["GATE_OUT"]
    with open(os.path.join(out, "imports.json"), "w") as fh:
        json.dump(doc, fh, sort_keys=True)
    with open(os.path.join(out, "imports.ids"), "w") as fh:
        for rid, cl in sorted(bad):
            fh.write("%s\t%s\n" % (rid, cl))
    print("imports: %s %d/%d %s" % (status, c, u, detail[:200]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
