#!/usr/bin/env python3
"""Gate "headers" (fnvnx-gate/1): every header of every admitted headers tree is read by an admitted unit (RECIPE.md "Headers trees").
    python3 -I gate_headers.py     cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/headers.json
Universe: every tracked file under a root of class `headers` in verify/admit.json (admit.json refuses a unit file, a recipe and a link there).
One declaration per symbol: the declarations of every header of every headers tree and of every header of a product root are taken from the
compiler (gcc -fdump-ada-spec on the header alone, so it must be self-contained: a header that does not parse is red), as (external name ->
declaring file:line); a symbol declared in two different header files is red (`declared-twice`, listed with both places).  A header that
declares a symbol that another header also declares is the second source of truth that the owner's rule of one declaration forbids; the header
of the symbol's owner is the one place, and every other root includes it.
Certified: a file that the preprocessor stage of at least one unit of an admitted product root (every variant) read: the stage's own
dependency list (deps.d of the stage result found by the pp_key of objects.json), so a header is certified through the units that include it, and
those units' objects are judged by objstub and the other gates. A header no unit reads is not certified (uncertified lists them, <= 50, and the
detail gives the count). A root whose build is red certifies nothing. No headers tree: universe 0, green."""
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402
import unitstage  # noqa: E402
import unitview  # noqa: E402


def read_set(repo, cache):
    """-> (set of repo paths read by some unit's PP stage, [reasons])"""
    seen, why, store = set(), [], os.path.join(cache, "store")
    for root in gate_build.product_roots(repo):
        try:
            res, _ = gate_build.ensure(root, cache, repo)
        except recipe.RecipeError as e:
            why.append("%s: invalid recipe: %s" % (root, "; ".join(e.errors)[:200]))
            continue
        if res["status"] == "red":
            why.append("%s: build red at %s" % (root, res["failure"]["stage"]))
            continue
        try:
            rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
        except (OSError, KeyError, ValueError):
            rec = {}
        # /inc/N = the N-th include dir that is no external (recipe order, see Build.includes): back to its repo path
        incs = [d for d in rec.get("include", []) if not d.startswith("/")]
        for key in res["pp_keys"].values():
            text = open(unitstage.result_file(store, key, "deps.d")).read()
            for p in unitview.parse_deps(text):
                p = posixpath.normpath(p)
                if p.startswith("/src/"):
                    seen.add(root + "/" + p[len("/src/"):])
                elif p.startswith("/inc/"):
                    n, _, rest = p[len("/inc/"):].partition("/")
                    if n.isdigit() and int(n) < len(incs):
                        seen.add(incs[int(n)] + "/" + rest)
    return seen, why


HDR_EXT = (".h", ".hh", ".hpp", ".hxx")
ADA_NAME = re.compile(r'External_Name => "([^"]+)"')
ADA_AT = re.compile(r"--\s+(\S+?):\d+\s*$")


def declared_symbols(repo, headers, incdirs):
    """-> ({symbol: sorted [header]}, [reasons]); headers/incdirs are repo-relative paths.  One gcc -fdump-ada-spec run per header."""
    decl, why = {}, []
    root = os.path.abspath(repo)
    for h in headers:
        with tempfile.TemporaryDirectory(prefix="declonce-") as td:
            p = subprocess.run(["gcc", "-fdump-ada-spec", "-C", "-fsyntax-only", "-x", "c++"] + ["-I" + os.path.join(root, d) for d in incdirs]
                               + [os.path.join(root, h)], cwd=td, capture_output=True, text=True)
            if p.returncode != 0:
                why.append("%s does not parse alone (a header is self-contained): %s" % (h, p.stderr.strip().splitlines()[-1][:120] if p.stderr.strip() else ""))
                continue
            for n in sorted(os.listdir(td)):
                if not n.endswith(".ads"):
                    continue
                last = None
                for line in open(os.path.join(td, n), errors="replace"):
                    m = ADA_AT.search(line)
                    if m:
                        last = m.group(1)
                    e = ADA_NAME.search(line)
                    if e and last:
                        f = os.path.relpath(os.path.join(td, last) if not os.path.isabs(last) else last, root)
                        if not f.startswith(".."):
                            decl.setdefault(e.group(1), set()).add(f)
    return {k: sorted(v) for k, v in decl.items()}, why


def twice(repo, tracked, admit):
    """-> (findings [{id, code}], reasons) over the headers trees and the product roots' headers"""
    trees = [p for p, r in admit.items() if r["class"] == "headers"]
    prods = [p for p, r in admit.items() if r["class"] == "product"]
    hs = sorted(f for f in tracked if f.endswith(HDR_EXT) and any(recipe.under(f, r) for r in trees + prods))
    incdirs = sorted(set(trees) | set(posixpath.dirname(h) for h in hs))
    decl, why = declared_symbols(repo, hs, incdirs)
    # a header in a headers tree and the same name's declaration in another file: only a symbol declared in two different files counts
    bad = [{"id": "%s: %s" % (k, ", ".join(v)), "code": "declared-twice"} for k, v in sorted(decl.items()) if len(v) > 1]
    return bad, why


def main():
    repo = "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-headers-")
    cache = os.environ.get("GATE_CACHE") or tmp
    try:
        roots = [p for p, r in recipe.load_admit(repo).items() if r["class"] == "headers"]
        files = sorted(f for f in recipe.tracked(repo) if any(recipe.under(f, r) for r in roots))
        seen, why = read_set(repo, cache) if files else (set(), [])
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    bad = [f for f in files if f not in seen]
    admit = recipe.load_admit(repo)
    dup, dwhy = twice(repo, recipe.tracked(repo), admit) if files or any(r["class"] == "product" for r in admit.values()) else ([], [])
    why += dwhy
    detail = "; ".join(why)[:600] or ("%d of %d headers read by no unit" % (len(bad), len(files)) if bad else
                                       ("%d headers in %d trees, each read by an admitted unit" % (len(files), len(roots)) if files else "no headers tree"))
    res = {"format": "fnvnx-gate/1", "name": "headers", "status": "red" if bad or dup or why else "green", "universe": len(files),
           "certified": len(files) - len(bad), "uncertified": bad[:50], "detail": detail, "findings": dup}
    with open(os.path.join(os.environ["GATE_OUT"], "headers.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("headers: %s %d/%d %s%s" % (res["status"], res["certified"], res["universe"], detail[:200],
                                 "".join("; " + d["code"] + " " + d["id"] for d in dup)[:300]))
    return 2 if res["status"] == "red" else 0


if __name__ == "__main__":
    sys.exit(main())
