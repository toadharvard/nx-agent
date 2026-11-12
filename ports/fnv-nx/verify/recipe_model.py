#!/usr/bin/env python3
"""recipe_model: the pure model of the one build (verify/recipe.py) and the manifest its gate leaves for the gates after it. File reads only: no stage, no
sandbox, no store, no compiler. Importers: recipe.py (builds), gate_build.py (builds once, writes the manifest), every consumer gate (reads it).
  - the admit.json / build.json / guest_compiler.json readers, the unit rule (units_of), the external and extension tables, RecipeError, BuildRed;
  - quantify: the universe convention of every per-root gate;
  - the build manifest "fnvnx-built/1", the hand-over from gate "build" to the gates that declare "needs": ["build"] in gates.json:
        GATE_CACHE/built/<git tree>/manifest.json  {"format", "tree", "roots": {root: {"objects": <path of its objects.json>}},
                                                     "externals": {name: <directory in GATE_CACHE/external-trees>}}
    A consumer reads it with read_built(); it never builds and never calls the compiler. A missing manifest is an error the consumer reports (red), so a
    gate that runs before "build" can not pass (verdict.py also refuses a gates.json that lists a consumer before its needs)."""
import json
import os
import posixpath
import subprocess

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
GUEST_PIN = TREE + "/verify/guest_compiler.json"   # the game's compiler: {"format", "external": {EXT_KEYS}, "cc", "cxx"} (RECIPE.md, "Guest roots")
GUEST_TARGETS = ("guest", "host")
EXT_KEYS = {"name", "repo", "ref", "commit", "tree_sha256", "at"}
ARTIFACTS_URL = "https://github.com/toadharvard/fnv-nx-build"
CC_EXT, CXX_EXT, ASM_EXT = (".c",), (".cc", ".cpp", ".cxx"), (".s", ".S")
BUILT_FORMAT = "fnvnx-built/1"
NO_ROOT = "no product root (nothing to build yet)"


class RecipeError(Exception):
    def __init__(self, errors):
        Exception.__init__(self, "; ".join(errors))
        self.errors = list(errors)


class BuildRed(Exception):
    pass


def is_unit(p):
    return p.endswith(CC_EXT + CXX_EXT + ASM_EXT)


def rel_ok(p):
    return isinstance(p, str) and p != "" and not p.startswith("/") and posixpath.normpath(p) == p and p != ".." \
        and not p.startswith("../")


def under(p, d):
    return p == d or p.startswith(d + "/")


def load_admit(repo="."):
    with open(os.path.join(repo, ADMIT)) as f:
        return {r["path"]: r for r in json.load(f)["roots"]}


def tracked(repo="."):
    out = subprocess.run(["git", "-C", repo, "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    return sorted(p for p in out.decode("utf-8", "surrogateescape").split("\0") if p)


def load_recipe(repo, roots, root):
    with open(os.path.join(repo, roots[root]["recipe"])) as f:
        return json.load(f)


def generated_outputs(rec):
    g = rec.get("generated")
    return [o for x in (g if isinstance(g, list) else []) if isinstance(x, dict) and isinstance(x.get("outputs"), list)
            for o in x["outputs"] if isinstance(o, str)]


def units_of(rec, root, files):
    """Sorted root-relative unit paths: tracked source files + generated outputs with a source name."""
    mine = {f[len(root) + 1:] for f in files if under(f, root)}
    return sorted(u for u in mine | set(generated_outputs(rec)) if is_unit(u))


def load_pin(repo="."):
    """verify/guest_compiler.json of the checkout, or None when the tree has none."""
    p = os.path.join(repo, GUEST_PIN)
    return json.load(open(p)) if os.path.exists(p) else None


def roots_target(roots, root):
    return roots.get(root, {}).get("target")


def product_roots(repo="."):
    return sorted(p for p, r in load_admit(repo).items() if r["class"] == "product")


def quantify(repo, judge_root):
    """The universe convention of every per-root gate: the admitted product roots of admit.json. JUDGE_ROOT(root) -> (universe, certified,
    ids, detail); the items are summed (ids <= 50), a root's red detail is kept. No product root: (0, 0, [], NO_ROOT), green."""
    u = c = 0
    ids, why = [], []
    roots = product_roots(repo)
    for root in roots:
        ru, rc, rids, rd = judge_root(root)
        u, c, ids = u + ru, c + rc, ids + rids
        if rc != ru:
            why.append("%s: %s" % (root, rd))
    if not roots:
        return 0, 0, [], NO_ROOT
    return u, c, sorted(ids)[:50], ("; ".join(why) or "%d roots" % len(roots))[:900]


def tree_of(repo="."):
    return subprocess.run(["git", "-C", repo, "rev-parse", os.environ.get("GATE_COMMIT", "HEAD") + "^{tree}"], capture_output=True,
                          text=True, check=True).stdout.strip()


def objects_dir(cache, tree, root):
    """Where the build of ROOT at git TREE keeps objects.json, obj/, the ELF and constants.tsv (the address is a function of cache, tree and root only)."""
    return os.path.join(cache, "objects", tree, root.replace("/", "__"))


def built_path(cache, tree):
    return os.path.join(cache, "built", tree, "manifest.json")


def write_built(cache, tree, roots, externals):
    """ROOTS {root: objects.json path}, EXTERNALS {name: directory} -> the manifest file (written whole, then renamed)."""
    path = built_path(cache, tree)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w") as fh:
        json.dump({"format": BUILT_FORMAT, "tree": tree, "roots": {r: {"objects": o} for r, o in sorted(roots.items())},
                   "externals": dict(sorted(externals.items()))}, fh, sort_keys=True)
    os.replace(tmp, path)
    return path


def read_built(cache, tree):
    """The manifest of the build of TREE. RecipeError if the build gate has not left one (a consumer runs after gate "build" or is red)."""
    path = built_path(cache, tree)
    try:
        doc = json.load(open(path))
    except (OSError, ValueError) as e:
        raise RecipeError(["no build manifest for tree %s (%s): gate build must run first" % (tree[:12], e)])
    if not (isinstance(doc, dict) and doc.get("format") == BUILT_FORMAT and doc.get("tree") == tree and isinstance(doc.get("roots"), dict)):
        raise RecipeError(["build manifest of tree %s is not %s" % (tree[:12], BUILT_FORMAT)])
    return doc


def objects_of(manifest, root):
    """The objects.json document of ROOT as the build recorded it (RecipeError if the root was not built)."""
    e = manifest["roots"].get(root)
    if not e:
        raise RecipeError(["root %s is not in the build manifest" % root])
    try:
        return json.load(open(e["objects"]))
    except (OSError, ValueError) as x:
        raise RecipeError(["%s: %s" % (e["objects"], x)])
