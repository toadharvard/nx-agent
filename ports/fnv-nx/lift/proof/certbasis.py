"""What a certificate is bound to besides the shape, the Form and the IR: the obligations the prover checks, and the platform sources of the declared outputs (declared.json).
One module, no SMT solver: the prover (prove.py, shapeprove.py), the certificate builder (certs_build.py) and the gate (formgate.py) all import it, so none keeps a copy."""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
OBLIGATIONS = ("def0",)      # what a record certifies beyond the field equalities; certs_build refuses a record without it, formgate a row without it (stale-obligations)


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
