#!/usr/bin/env python3
"""No gate builds the path of an external's checkout by hand. The layout of the external cache (<cache>/external/<commit> the bare fetch, <commit>.co the checkout,
<cache>/exttree/<commit> the gate view) is recipe.py's alone: every other reader takes the checkout from recipe.fetch_externals' result or recipe.exttree.
Rule over the whole tree (every .py file under verify/, contract/, link/, lift/, platform/ by walking, tests excepted): outside recipe.py, no os.path.join call has the
constant "external" or "exttree" followed by a further argument, and no string constant ends in ".co" or names the layout (…/external/…). The cache directory itself is named by one function, recipe.ext_dir(cache) (recipe.ext_of_store(store)):
`os.path.join(<dir>, "external")` is a hit too. Run: python3 -I <this file> (cwd anywhere)."""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.dirname(os.path.dirname(HERE))   # ports/fnv-nx
fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:600]))
    fails += 0 if ok else 1


def hits(src, name="<src>"):
    """[(line, what)] of the hand-built external paths in python source SRC"""
    out = []
    for n in ast.walk(ast.parse(src, name)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "join":
            consts = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            for i, a in enumerate(n.args):
                if isinstance(a, ast.Constant) and a.value in ("external", "exttree") and i > 0:
                    out.append((n.lineno, "join(..., %r) outside recipe.ext_dir" % a.value))
            if any(c.startswith("external/") or c.endswith("/external") or c == ".co" for c in consts):
                out.append((n.lineno, "join with a layout string"))
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and (n.value.endswith(".co") or "/exttree/" in n.value or "external/%s" in n.value):
            out.append((n.lineno, "layout string %r" % n.value))
    return out


# the checker itself: a hand-built checkout path is caught, the cache directory alone and the accessor are not
check("checker: join(cache, 'external', commit) is a hit", bool(hits('import os\nx = os.path.join(cache, "external", c)\n')))
check("checker: a '.co' suffix is a hit", bool(hits('x = p + ".co"\n')))
check("checker: join(cache, 'external') alone is a hit (use recipe.ext_dir)", bool(hits('import os\nx = os.path.join(cache, "external")\n')))
check("checker: recipe.exttree(e, cache) is no hit", not hits('import recipe\nx = recipe.exttree(e, cache)\n'))

import subprocess  # noqa: E402
bad = []
tracked = subprocess.run(["git", "-C", TREE, "ls-files", "-z", "--", "*.py"], capture_output=True, text=True, check=True).stdout.split("\0")   # the universe: every tracked python file of the tree, tests included
for rel in sorted(x for x in tracked if x):
    p = os.path.join(TREE, rel)
    if p in (os.path.join(TREE, "verify", "recipe.py"), os.path.abspath(__file__)) or not os.path.isfile(p):
        continue
    try:
        found = hits(open(p, encoding="utf-8").read(), p)
    except (SyntaxError, UnicodeDecodeError, ValueError):
        bad.append("%s: unparseable" % rel)
        continue
    bad += ["%s:%d %s" % (rel, l, w) for l, w in found]
check("no file outside recipe.py builds an external's path by hand", not bad, bad)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
