#!/usr/bin/env python3
"""One module path: a declared root R is `fnvnx.<R relative to ports/fnv-nx, / as .>`. `fnvnx.platform.x` resolves where the standard library has `platform`; an
unprefixed qualified import and a bare module name are gen:foreign-import with the prefixed name as the hint; the top name is checked against the host Python."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))   # ports: `fnvnx` is ports/fnvnx -> fnv-nx
from fnvnx.verify import gate_admit, recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


T = "ports/fnv-nx"
FILES = {
    T + "/platform/x.py": "import os\n",
    T + "/contract/store.py": "",
    T + "/render/total/sm.py": "",
    T + "/tools/gen_ok.py": "import platform\nfrom fnvnx.platform import x\nimport fnvnx.render.total.sm\nfrom fnvnx.contract import store\n",
    T + "/tools/gen_bare_pkg.py": "from contract import store\n",
    T + "/tools/gen_bare_mod.py": "import x\n",
    T + "/tools/helper.py": "import os\n",
    T + "/tools/gen_sib.py": "from fnvnx.tools import helper\nimport fnvnx.tools.gen_sib2\n",
    T + "/tools/gen_sib2.py": "from fnvnx.tools import gen_sib\n",
    T + "/platform/y.py": "import x\n",
}
roots = [T + "/platform", T + "/contract", T + "/render/total"]
with tempfile.TemporaryDirectory() as td:
    for p, t in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(td, p)), exist_ok=True)
        open(os.path.join(td, p), "w").write(t)
    files = sorted(FILES)
    pre = gate_admit.import_roots(roots, T)
    fi = lambda tool: recipe.foreign_imports(tool, T + "/tools", files, [], td, None, roots, pre)  # noqa: E731
    own = roots + [T + "/tools"]
    pre_own = gate_admit.import_roots(own, T)
    fo = lambda tool, clo=None: recipe.foreign_imports(tool, T + "/tools", files, [], td, clo, own, pre_own)  # noqa: E731
    check("every declared root is importable as fnvnx.<its path below ports/fnv-nx>", sorted(pre) == ["fnvnx.contract", "fnvnx.platform", "fnvnx.render.total"], sorted(pre))
    check("fnvnx.platform.x resolves although the standard library has `platform`", fi(T + "/tools/gen_ok.py") == [], fi(T + "/tools/gen_ok.py"))
    r = fi(T + "/tools/gen_bare_pkg.py")
    check("an unprefixed qualified import is red, the prefixed name is the hint", len(r) == 1 and r[0].startswith("contract") and "fnvnx.contract" in r[0], r)
    r = fi(T + "/tools/gen_bare_mod.py")
    check("the bare name of a module of a declared root is red, the qualified name is the hint", len(r) == 1 and "fnvnx.platform.x" in r[0], r)
    clo = []
    check("a tool that imports its own root's siblings by fnvnx.<rel> is green", fo(T + "/tools/gen_sib.py", clo) == [], fo(T + "/tools/gen_sib.py"))
    check("its closure holds the sibling and the tool itself (a sibling imports the tool)", {T + "/tools/helper.py", T + "/tools/gen_sib.py", T + "/tools/gen_sib2.py"} <= set(clo), clo)
    r = fo(T + "/platform/y.py")
    check("a bare sibling import inside a declared root stays red, the prefixed name is the hint", len(r) == 1 and "fnvnx.platform.x" in r[0], r)
check("the top name `fnvnx` names nothing else on the host Python (stdlib, pinned modules, installed distributions)", recipe.module_top_errors() == (), recipe.module_top_errors())
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
