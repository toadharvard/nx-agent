#!/usr/bin/env python3
"""Gate "admit" (fnvnx-gate/1): nothing is in the trunk without the gates that close its classes of failure.
    python3 -I gate_admit.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/admit.json

Rule. verify/admit.json ("fnvnx-admit/1") lists the roots of the port tree: {"path", "class", "gates", ["recipe"], ["imports"], ["trees"], ["entry"]}.
`entry` (class tool: required, may be empty): the stage entry points of the root, .py files inside its trees. gate_pure's universe is the
import closure of these and of the `generated` tools of the recipes; a script only a gate driver runs is not an entry point.
`trees` (optional): the list of the directories (or exact files) of one root, at least two, the first equal to "path"; the root's
universe is their union, and its python is judged in one puregate run, so trees that import each other form ONE root with no
file moved. Two roots never share or nest a tree. The root is named by "path" in `imports`.
`imports` (a root's python cross-root imports): a list of paths of other admitted roots; "pure" (gate_pure.py) judges the root
with its own directory and the parent directory of each imported root as import bases, so a root's python may import another
root's modules only when it declares it. Each entry must be an admitted root other than the root itself, without repeats; the
declared imports form no cycle (an error of the table: every file is uncertified).
Universe: every file git tracks below ports/fnv-nx (`git ls-files -z`), an item is its repo-relative path.
A file is certified iff ALL hold:
  1. exactly one root owns it: the innermost root that contains it (a root is a directory prefix or one exact file path; roots do not
     nest, with one exception: a headers tree may sit inside a root of another class and then owns its files, so that a layer
     keeps its shared headers in its own directory);
  2. the root's class is one of CLASSES and the root names every gate REQUIRED for that class;
  3. every gate the root names is a gate file of verify/gates.d of this same tree;
  4. a product root names a "recipe": a file inside the root that the build gates read (how its code is compiled in the
     declared environment); the file exists.
Classes:
  doc      text for people: no gate required. Nothing in a doc root is compiled or run by a build.
  tool     stage and verification tools: "selftests" required (their tests pass in the declared environment).
  product  code that is compiled into the shipped binary: the gates of gates.d whose `covers` include a class of the root (product-guest, lifted or
           host-product, and `linked` when its build.json declares a link; gatesdir.root_class), derived, not listed. A product root cannot
           be admitted before the gates that judge its classes exist in gates.d, so code enters the trunk only together with them.
  test     tests linked against the real files of the product roots: the gates whose `covers` include `test`, a "recipe" like a product root
           and a "target"; never the product set.
  headers  a tree of headers that product roots include (no recipe, no unit, no link, no outputs): "headers" required (gate_headers.py:
           every header is certified by the deps.d of admitted units, one declaration per symbol). A headers tree may nest in another
           root and owns its files (the innermost root decides); no other roots nest.
  fixture  a root that is built only to be judged red (RECIPE.md "Fixtures"): a "recipe" like a product root and "expect": a non-empty list of
           {"gate", "code"}; it is not a product root, and gates run on it alone (gate_fixtures.py, env GATE_ONLY_ROOT).
  ref      reference files that tests/oracle.json records point at (spec, contract, model): no gate named, no recipe, no units, no link,
           no outputs. Every file of a ref root is the `path` of a reference of some tests/oracle.json, and none is a build.json or a
           test (is_test_file): a product or test root is never a ref root. The gates that cover class ref (gates.d `covers`) are the
           ones that read any root: pure, assumptions (and the Legal and stub gates where they exist).
Argument: the verdict is green only if certified == universe, so a tracked file outside every root, a root with a
missing gate, or a product root without its gates makes every merge red. The table maps directories to gates; it
holds no exception for a file, a function or a finding.
Assumption: `git ls-files` lists the tree under test (the verdict runs gates in a clean worktree of the commit). (ASM-041)"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatesdir  # noqa: E402

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
CLASSES = ("doc", "tool", "product", "ref", "test", "headers", "fixture")
UNIT_EXT = (".c", ".cc", ".cpp", ".cxx", ".S", ".s")   # recipe.is_unit: a headers tree holds none
# The gates a product or test root names are DERIVED, not listed: the gates of gates.d whose `covers` include a class of the root (gatesdir.root_class,
# plus `linked` when the root's build.json declares a link). A gate added to gates.d with covers is required of every root of that class from then on, and
# a root cannot be admitted before the gates that judge its class exist (CLAUDE.md: code enters the trunk only together with its gates).
REQUIRED = {"doc": (), "tool": ("selftests",), "ref": (), "headers": ("headers",), "fixture": ()}   # classes whose required set is fixed by what they are


def required_gates(entry, covers, linked):
    """The gates of gates.d (COVERS {name: [root classes]}) that judge the classes of admit ENTRY of class product or test; LINKED: the root has a link stage."""
    c = gatesdir.root_class(entry)
    cl = {c} | ({"linked"} if c in ("product-guest", "lifted", "host-product") and linked else set())
    return sorted(g for g, cv in covers.items() if cl & set(cv))


def trees(r):
    """The trees of an admit root: its `trees` list, else [path]."""
    t = r.get("trees")
    return list(t) if isinstance(t, list) else [r.get("path", "")]


def imports_errors(roots):
    """Errors of the declared imports of ROOTS (list of admit root dicts): an unknown root, itself, a repeat, a cycle."""
    by = {r.get("path"): r for r in roots}
    errs, edges = [], {}
    for r in roots:
        imp = r.get("imports", [])
        p = r.get("path")
        if not isinstance(imp, list) or any(not isinstance(i, str) for i in imp):
            errs.append("root %s: imports is not a list of paths" % p)
            continue
        if len(set(imp)) != len(imp):
            errs.append("root %s: imports repeats a root" % p)
        for i in imp:
            if i == p:
                errs.append("root %s imports itself" % p)
            elif i not in by:
                errs.append("root %s: import %s is not an admitted root" % (p, i))
        edges[p] = [i for i in imp if i in by and i != p]
    state = {}

    def visit(n, stack):
        state[n] = 1
        for m in sorted(edges.get(n, [])):
            if state.get(m) == 1:
                errs.append("imports cycle: " + " -> ".join(stack[stack.index(m):] + [n, m] if m in stack else [n, m]))
            elif m not in state:
                visit(m, stack + [n])
        state[n] = 2
    for n in sorted(edges):
        if n not in state:
            visit(n, [])
    return errs


def is_test_file(f):
    b = f.rsplit("/", 1)[-1]
    return b == "build.json" or (b.startswith("test_") and b.endswith(".py")) or "/tests/" in f


def judge(files, admit, gate_names, exists, ref_paths=frozenset(), covers=None, linked=lambda path: False):
    """-> (uncertified {path: reason}, root errors [str]); pure function of its arguments. REF_PATHS: the tracked paths that a tests/oracle.json
    reference (spec, contract, model) points at; the files of a root of class ref must all be among them."""
    roots, errors = [], []
    if admit.get("format") != "fnvnx-admit/1":
        errors.append("admit.json: format is not fnvnx-admit/1")
    for r in admit.get("roots", []):
        path, cls, gates = r.get("path", ""), r.get("class"), r.get("gates", [])
        why = []
        if "trees" in r and not (isinstance(r["trees"], list) and len(r["trees"]) >= 2 and r["trees"][0] == path
                                 and len(set(r["trees"])) == len(r["trees"]) and all(isinstance(t, str) for t in r["trees"])):
            why.append("trees is not a list of at least two distinct paths starting with path")
        if cls == "tool":
            en = r.get("entry")
            if not isinstance(en, list) or any(not isinstance(e, str) or not e.endswith(".py") or not exists(e)
                                               or not any(e.startswith(t + "/") for t in trees(r)) for e in en):
                why.append("entry is not a list of existing .py files inside the root's trees")
        for t in (trees(r) if not why else [path]):
            if not t or t != t.strip("/") or not (t == TREE or t.startswith(TREE + "/")):
                why.append("%s is not below %s" % (t, TREE))
        if cls not in CLASSES:
            why.append("class %r is not one of %s" % (cls, ", ".join(CLASSES)))
        else:
            need = required_gates(r, covers or {}, linked(path)) if cls in ("product", "test") else REQUIRED[cls]
            why += ["required gate %s not named" % g for g in need if g not in gates]
            if cls == "ref" and ("recipe" in r or "gates" in r and r["gates"]):
                why.append("a ref root has no recipe and names no gate (it has no units, no link and no outputs)")
            if cls == "test" and r.get("target") not in ("guest", "host"):
                why.append('a test root declares "target": "guest" or "host"')
            if cls == "headers":
                if "recipe" in r or "target" in r:
                    why.append("a headers root has no recipe (no units, no link, no outputs)")
                why += ["%s is a unit file in a headers tree" % f for f in files if any(f.startswith(t + "/") for t in trees(r)) and f.endswith(UNIT_EXT)]
            if cls == "fixture":
                ex = r.get("expect")
                if not (isinstance(ex, list) and ex and all(isinstance(e, dict) and set(e) == {"gate", "code"} and e["gate"] in gate_names
                                                          and isinstance(e["code"], str) and e["code"] for e in ex)):
                    why.append('a fixture root declares "expect": a non-empty list of {"gate": a gate of gates.d, "code": a finding code}%s'
                               % "".join("; %r is no gate of gates.d" % e["gate"] for e in (ex if isinstance(ex, list) else []) if isinstance(e, dict) and e.get("gate") not in gate_names))
            if "variants" in r:   # the variants of a product root: {NAME: {"target": "guest"|"host"}}, addressed as root@NAME (recipe.entry_of); the recipe holds their flags
                vs = r["variants"]
                if cls != "product" or not (isinstance(vs, dict) and vs and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", n) and isinstance(e, dict)
                                                                                   and set(e) <= {"target"} and e.get("target", "guest") in ("guest", "host") for n, e in vs.items())):
                    why.append('"variants" is allowed on a product root only: {NAME: {"target": "guest"|"host"}}')
            if cls in ("product", "test", "fixture"):
                rec = r.get("recipe", "")
                if not any(rec.startswith(t + "/") for t in trees(r)) or not exists(rec):
                    why.append("recipe %r is not an existing file inside the root" % rec)
        why += ["gate %s is not a file of verify/gates.d" % g for g in gates if g not in gate_names]
        roots.append((trees(r) if not why else [path], why))
    errors += imports_errors(admit.get("roots", []))
    paths = [(t, i) for i, (ts, _) in enumerate(roots) for t in ts]
    cls_of_tree = {t: admit["roots"][i].get("class") for t, i in paths}
    for n, (a, i) in enumerate(paths):
        for b, j in paths[n + 1:]:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                inner = a if a.startswith(b + "/") else b if b.startswith(a + "/") else None
                outer = b if inner == a else a
                if not (inner is not None and cls_of_tree[inner] == "headers" and cls_of_tree[outer] != "headers"):   # a headers tree may nest in another root and owns its files
                    errors.append("trees %s and %s overlap" % (a, b))
    cls_of = {r.get("path", ""): r.get("class") for r in admit.get("roots", [])}
    bad = {}
    for f in files:
        hit = [(p, ts[0], why) for ts, why in roots for p in ts if f == p or f.startswith(p + "/")]
        if len(hit) > 1:   # the innermost root decides (a headers tree nested in another root)
            top = max(len(p) for p, _, _ in hit)
            hit = [h for h in hit if len(h[0]) == top]
        hit = [(a, w) for _, a, w in hit]
        if len(hit) != 1:
            bad[f] = "in %d roots" % len(hit)
        elif hit[0][1]:
            bad[f] = "root %s: %s" % (hit[0][0], "; ".join(hit[0][1]))
        elif cls_of[hit[0][0]] == "ref" and (is_test_file(f) or f not in ref_paths):
            bad[f] = "root %s is a ref root: %s" % (hit[0][0], "a build file or a test is not a reference" if is_test_file(f)
                                                   else "the file is not the path of a reference of any tests/oracle.json")
    errors += ["root %s: %s" % (ts[0], "; ".join(why)) for ts, why in roots if why]
    return bad, errors


def main():
    out = os.environ["GATE_OUT"]
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    files = sorted(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)
    with open(ADMIT) as f:
        admit = json.load(f)
    gate_names = [g["name"] for g in gatesdir.load_dir().get("gates", [])]
    ref_paths = set()
    for f in files:
        if f.endswith("/tests/oracle.json"):
            try:
                with open(f) as fh:
                    for e in json.load(fh).get("tests", {}).values():
                        ref_paths.update(x["path"] for x in e.get("references", []) if isinstance(x, dict) and x.get("kind") in ("spec", "contract", "model") and isinstance(x.get("path"), str))
            except (OSError, ValueError, AttributeError):
                pass          # gate oracle judges a malformed declaration; here it names no reference
    covers = {g["name"]: g.get("covers", []) for g in gatesdir.load_dir().get("gates", [])}
    bad, errors = judge(files, admit, gate_names, os.path.isfile, ref_paths, covers, lambda p: gatesdir.has_link(".", p))
    red = bool(bad or errors)
    certified = 0 if errors else len(files) - len(bad)
    detail = "%d files, %d roots; %s" % (len(files), len(admit.get("roots", [])),
                                         "; ".join(sorted(errors))[:400] if errors else "every file under a gated root")
    res = {"format": "fnvnx-gate/1", "name": "admit", "status": "red" if red else "green", "universe": len(files),
           "certified": certified, "uncertified": sorted(bad)[:50], "detail": detail}
    with open(os.path.join(out, "admit.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    print("admit: %s %d/%d %s" % (res["status"], certified, len(files), detail))
    return 2 if red else 0


if __name__ == "__main__":
    sys.exit(main())
