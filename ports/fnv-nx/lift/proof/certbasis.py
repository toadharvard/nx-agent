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


PROVER_ROOTS = ("prove.py", "model_sail.py", "symform.py", "certbasis.py")      # the modules that define what a proof compares; the prover identity is these and everything of this package they import, transitively (a prover fix invalidates by rule, an unrelated edit does not)


def _imports(path):
    """the modules of this package that the file at `path` imports (any depth: function-level imports count), by its syntax tree"""
    import ast
    out = set()
    for n in ast.walk(ast.parse(open(path, "rb").read())):
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            names = [n.module] + [n.module + "." + a.name for a in n.names]
        else:
            continue
        for m in names:
            p = m.split(".")
            if len(p) == 2 and p[0] == "proof" and os.path.isfile(os.path.join(HERE, p[1] + ".py")): out.add(p[1] + ".py")
    return out


def prover_files():
    seen, todo = set(), list(PROVER_ROOTS)
    while todo:
        f = todo.pop()
        if f not in seen:
            seen.add(f); todo.extend(_imports(os.path.join(HERE, f)))
    return sorted(seen)


def prover_sha():
    h = hashlib.sha256()
    for f in prover_files():
        h.update(f.encode() + b"\0" + open(os.path.join(HERE, f), "rb").read() + b"\0")
    return h.hexdigest()
