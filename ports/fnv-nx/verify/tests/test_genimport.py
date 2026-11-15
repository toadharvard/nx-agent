#!/usr/bin/env python3
"""gen:foreign-import (recipe.foreign_imports): a generator tool imports only the standard library, the pinned modules, modules of its own root and modules a declared
input carries. An import of another root's module that no input carries is red before any run (the stage mounts only the tool's root). Pure."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


FILES = {
    "a/gen.py": "import os, json\nimport helper\nfrom pkg import mod\nimport numpy\nimport z3\n",
    "a/helper.py": "import re\n",
    "a/pkg/__init__.py": "",
    "a/pkg/mod.py": "from . import sibling\n",
    "a/pkg/sibling.py": "",
    "b/spec_extract.py": "import json\n",
    "b/proto.py": "import store\n",
    "b/store.py": "",
    "a/bad.py": "import spec_extract\n",
    "a/bad2.py": "import helper\ndef f():\n    import spec_extract\n",
    "a/bad3.py": "import helper2\n",
    "a/helper2.py": "import proto\n",
    "a/rel.py": "from .. import nothing\n",
}
with tempfile.TemporaryDirectory() as td:
    for p, t in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(td, p)), exist_ok=True)
        open(os.path.join(td, p), "w").write(t)
    files = sorted(FILES)
    fi = lambda tool, ins=(): recipe.foreign_imports(tool, "a", files, list(ins), td)  # noqa: E731
    check("stdlib, pinned modules, own-root modules and packages (relative imports too) are green", fi("a/gen.py") == [], fi("a/gen.py"))
    check("an import of another root's module is red", fi("a/bad.py") == ["spec_extract"], fi("a/bad.py"))
    check("an import inside a function is found too", fi("a/bad2.py") == ["spec_extract"], fi("a/bad2.py"))
    check("a module of another root that a declared input carries is green (and its own imports are followed)", fi("a/bad.py", ["b/spec_extract.py"]) == [], fi("a/bad.py", ["b/spec_extract.py"]))
    check("the imports of an own-root module count (transitively)", fi("a/bad3.py") == ["proto"], fi("a/bad3.py"))
    check("a declared input carries a module and what it imports", fi("a/bad3.py", ["b/proto.py", "b/store.py"]) == [], fi("a/bad3.py", ["b/proto.py", "b/store.py"]))
    check("a declared directory input carries its modules", fi("a/bad3.py", ["b"]) == [], fi("a/bad3.py", ["b"]))
    check("a relative import that leaves the tree is red", fi("a/rel.py") == ["nothing"] or fi("a/rel.py") == [".nothing"] or len(fi("a/rel.py")) == 1, fi("a/rel.py"))
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
