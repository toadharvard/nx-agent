#!/usr/bin/env python3
"""recipe.import_mount: a data file that a module reads by a path of its own location (`<module dir>/../msvcmodel/DATAMODEL.json`, built from __file__) in a SIBLING
tree of a declared import root is mounted with the module (at the same repo-relative place, so the read resolves in the stage), and is in the stage's key; a read that
leaves every mounted tree stays a file-escape. (The defect: the owner of a read was the module's own tree alone, so the generator that imports a layout module found
no DATAMODEL.json at /imp/fnvnx/toolchain/msvcmodel/.) Toy tree. Plain script, one PASS/FAIL line per case, exit 0 on pass."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from fnvnx.verify import pathflow, recipe  # noqa: E402

FAILED = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + repr(info)[:400]))
    if not ok:
        FAILED.append(name)


T = recipe.TREE
GEN, LAY, MSV, OTH = T + "/gen", T + "/layout", T + "/msvcmodel", T + "/other"
READ = "import os\nHERE = os.path.dirname(os.path.abspath(__file__))\nopen(os.path.join(HERE, '..', '%s', '%s')).read()\n"
SRC = {
    GEN + "/t.py": "import fnvnx.layout.crossing\n",
    LAY + "/crossing.py": READ % ("msvcmodel", "DATAMODEL.json"),
    LAY + "/escape.py": READ % ("other", "x.json"),
    MSV + "/DATAMODEL.json": "{}\n",
    MSV + "/DATAMODEL.md": "unread\n",
    OTH + "/x.json": "{}\n",
}
ROOTS = {GEN: {"class": "tool", "path": GEN, "imports": [LAY, MSV]}, LAY: {"class": "tool", "path": LAY}, MSV: {"class": "tool", "path": MSV}, OTH: {"class": "tool", "path": OTH}}   # admit.json keys a root by its path
with tempfile.TemporaryDirectory() as td:
    for p, text in SRC.items():
        os.makedirs(os.path.dirname(os.path.join(td, p)), exist_ok=True)
        open(os.path.join(td, p), "w").write(text)
    bad, mount = recipe.import_mount(GEN + "/t.py", GEN, set(SRC), [], ROOTS, td)
    check("no foreign import", bad == [], bad)
    check("the module is mounted", LAY + "/crossing.py" in mount, mount)
    check("the data file it reads in the sibling root is mounted at its repo path", MSV + "/DATAMODEL.json" in mount, mount)
    check("a file of that root that nothing reads stays out of the key", MSV + "/DATAMODEL.md" not in mount, mount)
    esc = pathflow.escapes_of(LAY + "/crossing.py", SRC[LAY + "/crossing.py"], recipe.import_roots_of(ROOTS, GEN))
    check("the read is no file-escape for the stage", esc == [], esc)
    esc = pathflow.escapes_of(LAY + "/escape.py", SRC[LAY + "/escape.py"], recipe.import_roots_of(ROOTS, GEN))
    check("a read in a tree the stage does not mount is a file-escape", len(esc) == 1, esc)
print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
