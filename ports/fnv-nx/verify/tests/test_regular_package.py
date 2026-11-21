#!/usr/bin/env python3
"""recipe.foreign_imports over a regular package (a directory of a declared root with __init__.py): `import fnvnx.<root>.<pkg>` puts the package's __init__.py and
every module its __init__ imports (relative or qualified) into the import closure, so the stage mounts them and the __init__ runs; a directory without __init__.py
stays an implicit namespace package that contributes no file. (The defect: the namespace test fired first, so fnvnx.lift.fnvlift's __init__.py was never mounted and
`fnvlift.inputs` was absent at run time.) Plain script, one PASS/FAIL line per case, exit 0 on pass."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from fnvnx.verify import gate_admit, recipe  # noqa: E402

FAILED = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + repr(info)[:400]))
    if not ok:
        FAILED.append(name)


T, OWN = recipe.TREE, recipe.TREE + "/lt"
SRC = {
    OWN + "/tools/t.py": "import fnvnx.lt.pkg as p\nimport fnvnx.lt.ns.leaf\nprint(p.sub.x)\n",
    OWN + "/pkg/__init__.py": "from . import sub\nfrom fnvnx.lt.pkg.core import y\n",
    OWN + "/pkg/sub.py": "x = 1\n",
    OWN + "/pkg/core.py": "y = 2\n",
    OWN + "/pkg/unused.py": "z = 3\n",
    OWN + "/ns/leaf.py": "w = 4\n",
}
with tempfile.TemporaryDirectory() as td:
    for p, text in SRC.items():
        os.makedirs(os.path.dirname(os.path.join(td, p)), exist_ok=True)
        open(os.path.join(td, p), "w").write(text)
    clo = []
    bad = recipe.foreign_imports(OWN + "/tools/t.py", OWN, set(SRC), [], td, clo, [OWN], gate_admit.import_roots([OWN], T))
    check("no foreign import", bad == [], bad)
    check("the regular package's __init__.py is in the closure", OWN + "/pkg/__init__.py" in clo, clo)
    check("its relative and qualified imports follow (sub, core)", {OWN + "/pkg/sub.py", OWN + "/pkg/core.py"} <= set(clo), clo)
    check("a module nothing imports stays out", OWN + "/pkg/unused.py" not in clo, clo)
    check("a namespace directory (no __init__.py) adds no file of its own; its imported module is in", OWN + "/ns/leaf.py" in clo and not any(c.startswith(OWN + "/ns/__init__") for c in clo), clo)
print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
