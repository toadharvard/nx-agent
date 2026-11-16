"""What a certificate is bound to besides the shape, the Form and the IR: the obligations the prover checks, and the platform sources of the declared outputs (declared.json).
One module, no SMT solver: the prover (prove.py, shapeprove.py), the certificate builder (certs_build.py) and the gate (formgate.py) all import it, so none keeps a copy."""
import hashlib
import json
import os

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


def _imports(rel):
    """the repository files that the file `rel` imports (any depth: function-level imports count), by its syntax tree"""
    import ast
    out = set()
    for n in ast.walk(ast.parse(open(os.path.join(LIFT, rel), "rb").read())):
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            names = [n.module] + [n.module + "." + a.name for a in n.names]
        else:
            continue
        for m in names: out.update(_module_file(m))
    return out


def prover_files():
    seen, todo = set(), list(PROVER_ROOTS)
    while todo:
        f = todo.pop()
        if f not in seen:
            seen.add(f); todo.extend(_imports(f))
    return sorted(seen)


def prover_sha():
    h = hashlib.sha256()
    for f in prover_files():
        h.update(f.encode() + b"\0" + open(os.path.join(LIFT, f), "rb").read() + b"\0")
    return h.hexdigest()
