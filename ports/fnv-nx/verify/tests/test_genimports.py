#!/usr/bin/env python3
"""Declared imports of a generator's tool (admit.json `imports`; RECIPE.md "Declared imports of a generator's tool"): an import that resolves inside a declared import root
is green and only its import closure is mounted (so an edit elsewhere in that root leaves the key unchanged); an undeclared root is red; a declaration no generator
reaches is red. Pure: recipe.check on toy git repos and the closure functions; no stage runs."""
import hashlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import test_link as TL  # noqa: E402

recipe, T, ROOT, check = TR.recipe, TR.T, TR.ROOT, TR.check
LIB = T + "lib"   # an admitted tool root the generator imports from
GEN = "import sys\nimport libmod\nopen('/out/gen/o.c', 'w').write('int o;\\n')\n"
FILES = {"gen/g.py": GEN, "toy/b.c": "int f_b(void) { return 1; }\n"}
LIBFILES = {"libmod.py": "import libdep\n", "libdep.py": "X = 1\n", "unused.py": "Y = 2\n"}


def errs(name, imports, gen=GEN, libfiles=LIBFILES):
    r = TL.main_rec()
    r["generated"] = [{"tool": T + "gen/g.py", "args": [], "inputs": [], "outputs": ["gen/o.c"]}]
    repo = TL.mk(name, r, dict(FILES, **{"gen/g.py": gen}), libs=[(LIB, ({"format": "fnvnx-recipe/1"}, libfiles))])
    roots = recipe.load_admit(repo)
    ow = recipe.tree_of(roots, T + "gen/g.py")   # the admitted tool root that owns the generator
    roots[ow] = dict(roots[ow], imports=imports)
    return recipe.check(r, ROOT, recipe.tracked(repo), roots, None, repo), repo, roots


def main():
    e, repo, roots = errs("gi1", [LIB])
    fi = [x for x in e if "gen:foreign-import" in x or "gen:unused-import" in x]
    check("green: an import that resolves in a declared import root (and its own imports)", fi == [], fi)
    files = set(recipe.tracked(repo))
    bad, mounted = recipe.import_mount(T + "gen/g.py", recipe.tree_of(roots, T + "gen/g.py"), files, [], roots, repo)
    check("the mount is exactly the import closure of the declared root (libmod, libdep), not unused.py", bad == [] and [os.path.basename(f) for f in mounted] == ["libdep.py", "libmod.py"], (bad, mounted))
    h = lambda: hashlib.sha256(b"".join(open(os.path.join(repo, f), "rb").read() for f in mounted)).hexdigest()  # noqa: E731
    h0 = h()
    open(os.path.join(repo, LIB, "unused.py"), "w").write("Y = 3\n")
    check("an edit to a file of the import root outside the closure leaves the mounted content (the key) unchanged", h() == h0, "")
    open(os.path.join(repo, LIB, "libdep.py"), "w").write("X = 2\n")
    check("an edit to a closure file changes it", h() != h0, "")
    e, _, _ = errs("gi2", [])
    fi = [x for x in e if "gen:foreign-import" in x]
    check("red: the same import with no declaration is a foreign import", len(fi) == 1 and "libmod" in fi[0], e)
    e, _, _ = errs("gi3", [LIB], gen="import sys\nopen('/out/gen/o.c', 'w').write('int o;\\n')\n")
    un = [x for x in e if "gen:unused-import" in x]
    check("red: a declared import root that no generator reaches", len(un) == 1 and LIB in un[0], e)
    lf = dict(LIBFILES, **{"libmod.py": "import os\nopen(os.path.join(os.path.dirname(__file__), 'data.json'))\n", "data.json": "{}\n", "other.json": "{}\n"})
    e, repo4, roots4 = errs("gi4", [LIB], libfiles=lf)
    check("green: a module of an import root reading a tracked data file by a path of its own location", [x for x in e if "gen:file-escape" in x] == [], e)
    bad, m4 = recipe.import_mount(T + "gen/g.py", recipe.tree_of(roots4, T + "gen/g.py"), set(recipe.tracked(repo4)), [], roots4, repo4)
    check("the data file it reads is mounted with the closure, and only that one (not other.json, not unused.py)", sorted(os.path.basename(f) for f in m4) == ["data.json", "libmod.py"], m4)
    e, _, _ = errs("gi5", [LIB], libfiles=dict(LIBFILES, **{"libmod.py": "import os\nopen(os.path.join(os.path.dirname(__file__), '..', 'elsewhere.json'))\n"}))
    es = [x for x in e if "gen:file-escape" in x]
    check("red: a read by a path of its own location that leaves the import root's tree", len(es) == 1 and "libmod.py" in es[0], e)
    e, _, _ = errs("gi6", [LIB], libfiles=dict(LIBFILES, **{"libmod.py": "import os, sys\nopen(os.path.join(os.path.dirname(__file__), sys.argv[1]))\n"}))
    check("red: a read whose path the rule cannot decide", len([x for x in e if "gen:file-escape" in x]) == 1, e)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
