"""What a certificate is bound to besides the shape, the Form and the IR: the obligations the prover checks, and the platform sources of the declared outputs (declared.json).
One module, no SMT solver: the prover (prove.py, shapeprove.py), the certificate builder (certs_build.py) and the gate (formgate.py) all import it, so none keeps a copy."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", ".."))   # ports/fnv-nx: qualified imports of another root's modules (contract.X, verify.X)
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
OBLIGATIONS = ("def0", "xstate")      # what a record certifies beyond the field equalities; certs_build refuses a record without it, formgate a row without it (stale-obligations)


def declared_table():
    with open(os.path.join(HERE, "declared.json")) as fh:
        return json.load(fh)


def platform_sha(fns, root=None):
    """sha256 over (relative path, content) of the declared.json source files of the functions `fns`: part of the certificate; the gate recomputes it from the tree it runs in"""
    root = root or LIFT
    rows = {u: r for r in declared_table()["rows"] for u in r["ufs"]}
    h = hashlib.sha256()
    for src in sorted({rows[f]["source"] for f in fns}):
        h.update(src.encode()); h.update(open(os.path.join(root, src), "rb").read())
    return h.hexdigest()


PROVER_ROOTS = ("proof/prove.py", "proof/model_sail.py", "proof/symform.py", "proof/certbasis.py")      # the modules that define what a proof compares; the prover identity is these and every module of this repository that they import, transitively, paths relative to lift/ (stdlib and pinned wheels stay outside).  A prover fix invalidates by rule, an unrelated edit does not.


def _module_file(m):
    """lift/-relative file of the repository module `m` (dotted), with the __init__.py of each package it passes through; [] when `m` is not of this repository"""
    p = m.split(".")
    out = []
    for i in range(1, len(p)):
        init = os.path.join(*p[:i], "__init__.py")
        if os.path.isfile(os.path.join(LIFT, init)): out.append(init)
    f = os.path.join(*p) + ".py"
    if os.path.isfile(os.path.join(LIFT, f)): return out + [f]
    return []


class DynamicLoad(Exception):
    """a closure file loads code other than by an import statement: the closure (the prover's identity) would not see it"""


def _imports(rel):
    """the repository files that the file `rel` imports (any depth: function-level imports count, relative imports resolve against the file's package as Python does), by its syntax tree.
    Raises DynamicLoad on any importlib / __import__ / exec use: only import statements form the closure."""
    import ast
    out = set()
    pkg = os.path.dirname(rel).replace(os.sep, ".")
    for n in ast.walk(ast.parse(open(os.path.join(LIFT, rel), "rb").read())):
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
            bad = [m for m in names if m.split(".")[0] == "importlib"]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                up = pkg.split(".")
                up = up[:len(up) - (n.level - 1)]
                base = ".".join(up + ([n.module] if n.module else []))
            names = [base] + [base + "." + a.name for a in n.names]
            bad = [base] if base.split(".")[0] == "importlib" else []
        elif isinstance(n, ast.Name) and n.id in ("__import__", "exec"):
            raise DynamicLoad("%s:%d uses %s" % (rel, n.lineno, n.id))
        else:
            continue
        if bad: raise DynamicLoad("%s:%d imports %s" % (rel, n.lineno, bad[0]))
        for m in names: out.update(_module_file(m))
    return out


def prover_files():
    seen, todo = set(), list(PROVER_ROOTS)
    while todo:
        f = todo.pop()
        if f not in seen:
            seen.add(f); todo.extend(_imports(f))
    return sorted(seen)


# Data files of the closure's packages: everything that is not .py or documentation (*.md: under the repository convention no code reads them, so they are not program inputs), tests/ and caches left out.
# They decide verdicts (fnvlift/faults.json, proof/declared.json, the page tables ...), so the identity hashes them all.
# Left out, because they are already their own certificate component:
#   model_sha / closure_sha: the declared inputs of the recipe roots whose outputs those components are (COMPONENT_ROOTS, read from their recipes, the roots' own directories included);
#   x86sys_sha256: platform/declared/x86sys.json (outside these directories).
# A model input under proof/ that no component root declares stays hashed: it over-invalidates and is safe.
CLOSURE_MARK = "PROVER_SHA"      # the file mkclosure writes into a closure directory (it is the directory's marker, in lift/proof/)
COMPONENT_ROOTS = ("proof/sailmodel", "proof/isla")      # lift/-relative


def component_inputs():
    """lift/-relative paths of the declared repository inputs of COMPONENT_ROOTS (files and directories) and the roots' own directories, read through the recipe loader of the trunk (verify/recipe.py load_recipe,
    expand_run: the one parser of the recipe format) from the checkout this tree is part of.  Inside a closure directory (CLOSURE_MARK present) there is no checkout: mkclosure built that directory from exactly
    prover_files() + prover_data_files() of the checkout, verified its prover_sha equal, and no component input is in it, so the exclusion set is empty there."""
    if os.path.exists(os.path.join(HERE, CLOSURE_MARK)): return set()
    repo = os.path.normpath(os.path.join(LIFT, "..", "..", ".."))
    vdir = os.path.join(repo, "ports", "fnv-nx", "verify")
    if vdir not in sys.path: sys.path.insert(0, vdir)
    from verify import recipe
    roots = recipe.load_admit(repo)
    out = set()
    for r in COMPONENT_ROOTS:
        out.add(r)
        spec = "ports/fnv-nx/lift/" + r
        rec = recipe.load_recipe(repo, roots, spec)
        for st in recipe.expand_run(rec).get("run", []) + rec.get("generated", []):
            for i in st.get("inputs", []):
                if isinstance(i, str) and "/lift/" in i: out.add(i.split("/lift/", 1)[1])
    return out


def prover_data_files():
    pk = sorted({os.path.dirname(f) for f in prover_files()})
    skip = component_inputs()
    out = []
    for d in pk:
        for root, dirs, files in os.walk(os.path.join(LIFT, d)):
            rel = os.path.relpath(root, LIFT)
            dirs[:] = sorted(x for x in dirs if x not in ("tests", "__pycache__") and os.path.join(rel, x) not in skip and not os.path.isfile(os.path.join(root, x, "build.json")))      # a directory with a build.json is a recipe root: its files build something, they are not program inputs
            for f in sorted(files):
                p = os.path.join(rel, f)
                if not f.endswith((".py", ".pyc", ".md")) and p not in skip and p != os.path.join("proof", CLOSURE_MARK): out.append(p)
    return out


def prover_sha():
    h = hashlib.sha256()
    for f in prover_files() + prover_data_files():
        h.update(f.encode() + b"\0" + open(os.path.join(LIFT, f), "rb").read() + b"\0")
    return h.hexdigest()


def add_inputs_args(ap, required):
    """the lifter inputs a Form reads (fnvlift/inputs.py: cpumodel, rcptab, rcpcert), explicit arguments of every program that computes a shape's Form or its shape_sha; REQUIRED False: all three or none"""
    ap.add_argument('--cpumodel', required=required, help='verify/cpuid_model.json (the declared CPU data: MXCSR_MASK of the Forms)')
    ap.add_argument('--rcptab', required=required, help='rt/rcptab.c'); ap.add_argument('--rcpcert', required=required, help='rt/rcptab.cert.json')


def configure_inputs(a):
    given = [x for x in (a.cpumodel, a.rcptab, a.rcpcert) if x]
    if not given: return
    if len(given) != 3: raise SystemExit('--cpumodel, --rcptab and --rcpcert are given together')
    from fnvlift import inputs
    inputs.configure(cpumodel=os.path.abspath(a.cpumodel), rcptab=os.path.abspath(a.rcptab), rcpcert=os.path.abspath(a.rcpcert))
