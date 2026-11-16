#!/usr/bin/env python3
"""gen:file-escape (recipe.check, pathflow.escapes: the rule the abspath gate runs too): a generator tool (or a module it imports) that reads a file by a path built from
__file__ / __path__ / sys.argv[0] is red when the path leaves the tree the stage mounts the tool from, or cannot be decided; green for a file inside that tree and for a
read guarded by require_mounted/require_under. Toy repos in temp git dirs; `recipe.check` only (no stage runs)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import test_link as TL  # noqa: E402

recipe, T, ROOT, check = TR.recipe, TR.T, TR.ROOT, TR.check
OUT = "import sys\nopen('/out/gen/o.c', 'w').write('int o;\\n')\n"


def errs(name, tool_src, extra=None):
    r = TL.main_rec()
    r["generated"] = [{"tool": T + "gen/g.py", "args": [], "inputs": [], "outputs": ["gen/o.c"]}]
    files = dict(TL.BASE_FILES, **{"gen/g.py": tool_src, "toy/b.c": "int f_b(void) { return 1; }\n"})
    files.update(extra or {})
    repo = TL.mk(name, r, files)
    return [e for e in recipe.check(r, ROOT, recipe.tracked(repo), recipe.load_admit(repo), None, repo) if "gen:file-escape" in e]


def main():
    e = errs("ge1", "import os\nopen(os.path.join(os.path.dirname(__file__), '..', '..', 'other', 'x.json'))\n")
    check("red: a read built from __file__ that leaves the mounted tree (..)", len(e) == 1 and ":gen:0:" in e[0], e)
    e = errs("ge2", "import os\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json'))\n", {"gen/data.json": "{}\n"})
    check("green: a file inside the tool's own mounted tree", e == [], e)
    e = errs("ge3", "import os, sys\nopen(os.path.join(os.path.dirname(sys.argv[0]), '..', '..', '..', 'x'))\n")
    check("red: sys.argv[0] built path that leaves the tree", len(e) == 1, e)
    e = errs("ge4", "import os, sys\nopen(os.path.join(os.path.dirname(__file__), sys.argv[1]))\n")
    check("red: a path with a part the rule cannot decide is never green", len(e) == 1, e)
    e = errs("ge5", "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\nopen(sys.argv[1])\n")
    check("green: a read of an argument (declared `uses` input passed as an argument)", e == [], e)
    e = errs("ge6", "import os\nimport helper\n", {"gen/helper.py": "import os\nopen(os.path.join(os.path.dirname(__file__), '..', '..', 'x'))\n"})
    check("red: the escape is found in a module of the tool's import closure too", len(e) == 1 and "helper.py" in e[0], e)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
