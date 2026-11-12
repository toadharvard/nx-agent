#!/usr/bin/env python3
"""Gate "imports" (fnvnx-gate/1, class 4 of VERDICT.md): every import of the manifest has a body, proven by the build products (rules: contract/imports.py).
    python3 -I gate_imports.py      cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/imports.json and GATE_OUT/imports.ids
Roots come only from verify/admit.json (class product); for each the gate calls gate_build.ensure (build or cache hits) and reads the symbol
tables the recipe wrote (objects.json: defined {symbol, bind, type}, undefined). No nm, no ELF parsing, no probe run.
A product root DECLARES its imports in `<root>/imports.in.json` ("fnvnx-imports-in/1"):
  {"format", "external": NAME of an entry of the root's recipe `external` (the private manifest: fnv-nx-build commit + tree_sha256),
   "names": path of sys_names.tsv inside it, "specs": directory of the reference environment's .spec files inside it,
   "extra": [row files inside it, judged against this root], "extra_other": [row files of another owner's build, judged against the other roots],
   "name_table": path inside the root of the msvcrt name table `name TAB symbol TAB func|data` (the same file the root generates its lookup table from)}
The external is checked out from the build's own fetch (GATE_CACHE/external/<commit>) and its tree hash must equal tree_sha256.
Universe (gate_build.quantify, the convention of build/objstub): the admitted product roots; per root every row of the bridge table (contract/bridgetable.derive). A root without imports.in.json is one red item. Certified: rows with no class
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

IN_KEYS = {"format", "external", "names", "specs", "extra", "extra_other", "name_table"}


def symbols(res):
    """{symbol: [(bind, type)]} of an objects.json; a name that is only referenced has [("UND", "NOTYPE")]."""
    out = {}
    for d in res["defined"]:
        out.setdefault(d["symbol"], []).append((d["bind"], d["type"]))
    for u in res["undefined"]:
        out.setdefault(u, []).append(("UND", "NOTYPE"))
    return out


def ext_tree(rec, name, cache):
    """Checkout of the recipe's external NAME (exact commit, from the bare fetch of the build) into CACHE/exttree/<commit>; tree hash checked."""
    e = next((x for x in rec.get("external", []) if x["name"] == name), None)
    if e is None:
        raise ValueError("imports.in.json: the recipe has no external %r" % name)
    dest = os.path.join(cache, "exttree-imports", e["commit"])
    if not os.path.isdir(dest):
        tmp = "%s.tmp%d-%s" % (dest, os.getpid(), os.urandom(4).hex())
        os.makedirs(tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp + ".idx")
        bare = os.path.join(cache, "external", e["commit"])
        for cmd, kw in ((["--git-dir", bare, "read-tree", e["commit"]], {}),
                        (["--git-dir", bare, "--work-tree", tmp, "checkout-index", "-a", "-f"], {})):
            subprocess.run(["git"] + cmd, env=env, check=True, capture_output=True, **kw)
        os.unlink(tmp + ".idx")
        try:
            os.rename(tmp, dest)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
    if cas.ingest(dest, os.path.join(cache, "store")) != e["tree_sha256"]:
        raise ValueError("external %r: tree_sha256 differs from the checkout" % name)
    return dest


def read(path):
    with open(path, encoding="latin1") as f:
        return f.read()


def root_rows(root, rec, res, decl, ext, repo, others):
    """-> [(id, class)] of one root that declares imports"""
    if set(decl) != IN_KEYS or decl["format"] != "fnvnx-imports-in/1":
        raise ValueError("%s/imports.in.json: need exactly the keys %s and format fnvnx-imports-in/1" % (root, sorted(IN_KEYS)))
    syms = symbols(res)
    table = bridgetable.read_name_table(read(os.path.join(repo, root, decl["name_table"])))
    specs = os.path.join(ext, decl["specs"])
    rows, errors, extra_of = bridgetable.derive(
        read(os.path.join(ext, decl["names"])), lambda d: read(os.path.join(specs, d + ".spec")), set(syms), table,
        [read(os.path.join(ext, p)) for p in decl["extra"] + decl["extra_other"]])
    if errors:
        raise ValueError("%d bridge errors, first: %s" % (len(errors), errors[0]))
    other = {k for k, i in sorted(extra_of.items()) if i >= len(decl["extra"])}
    return imports.judge(rows, syms, table, others, other)


def judge_root(root, repo, cache, admit, roots, bad):
    """One admitted product ROOT -> (universe, certified, ids, detail) (gate_build.quantify); input errors are red with the reason, never raised.
    BAD collects (id, class) of the uncertified rows."""
    p = os.path.join(repo, root, "imports.in.json")
    try:
        if not os.path.isfile(p):
            return 1, 0, [root + ":imports.in.json"], "declares no imports.in.json (the root's imports are undeclared)"
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
        rows = root_rows(root, rec, built[root], decl, ext_tree(rec, decl["external"], cache), repo, others)
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
