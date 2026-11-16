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
    "a/p2/__init__.py": "",
    "a/p2/m.py": "ctl = 1\n",
    "a/p2/sub.py": "import proto\n",
    "a/p2/ok_attr.py": "from .m import ctl\n",
    "a/p2/ok_sub.py": "from . import m\nfrom .m import ctl\n",
    "a/ok_pkg_sub.py": "from .p2 import m\n",
    "a/ok_pkg_attr.py": "from .p2 import whatever\n",
    "a/bad_mod.py": "from .nomod import x\n",
    "a/bad_sub.py": "from . import nosuch\n",
    "a/ok_sub_follow.py": "from .p2 import sub\n",
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
    check("a from-import of a name inside a module is green (the name is an attribute, never looked up as a file)", fi("a/p2/ok_attr.py") == [], fi("a/p2/ok_attr.py"))
    check("a submodule from-import is green", fi("a/p2/ok_sub.py") == [] and fi("a/ok_pkg_sub.py") == [], (fi("a/p2/ok_sub.py"), fi("a/ok_pkg_sub.py")))
    check("a name a package does not hold as a file is an attribute: green", fi("a/ok_pkg_attr.py") == [], fi("a/ok_pkg_attr.py"))
    check("a missing module is red", fi("a/bad_mod.py") == [".nomod"], fi("a/bad_mod.py"))
    check("a missing submodule file (from . import NAME, no such file) is red", fi("a/bad_sub.py") == [".nosuch"], fi("a/bad_sub.py"))
    check("a submodule from-import is followed into the closure (its own imports count)", fi("a/ok_sub_follow.py") == ["proto"], fi("a/ok_sub_follow.py"))
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
