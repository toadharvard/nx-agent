"""ea-rule of gate forms (F2): every memory access of a Form builder goes through ONE address function (forms.ea / addoff / ssa / esa / dsa), which applies the
effective segment and a16. Static, over the Form builders (proof/forms*.py), by syntax:
  raw-access      no forms_*.py except forms.py imports or calls `load`, or calls `.store(`;
  address-source  the address argument of every mload / mstore call in forms_*.py is a call of ea / addoff / ssa / esa / dsa, or a name assigned from one in the same function
                  (or the `addr` parameter of store_op, whose callers pass such an address). sload / sstore apply ssa themselves (the stack).
The registry check of forms.mload is only a guard: nodes are hash-consed, so a raw node equal to a tagged one is not distinguishable at run time; this static rule is the
enforcement. `violations(dir)` -> sorted [(file, line, rule)]; empty = the rule holds."""
import ast
import glob
import os

AF = {"ea", "addoff", "ssa", "esa", "dsa"}
IDX = {"mload": 0, "mstore": 1}
OWN = ("rd", "sload", "sstore", "store_op", "mload", "mstore")      # the functions of forms.py that implement the access itself


def violations(proof_dir):
    out = []
    for f in sorted(glob.glob(os.path.join(proof_dir, "forms*.py"))):
        base = os.path.basename(f)
        with open(f) as fh:
            tree = ast.parse(fh.read())
        if base != "forms.py":
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and any(a.name == "load" for a in n.names):
                    out.append((base, n.lineno, "raw-access: import load"))
                if isinstance(n, ast.Call):
                    g = n.func
                    if isinstance(g, ast.Name) and g.id == "load":
                        out.append((base, n.lineno, "raw-access: load("))
                    if isinstance(g, ast.Attribute) and g.attr == "store":
                        out.append((base, n.lineno, "raw-access: .store("))
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            okn = {a.arg for a in fn.args.args if a.arg == "addr"}
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", None) in AF:
                    okn |= {t.id for t in n.targets if isinstance(t, ast.Name)}
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) in IDX and (base != "forms.py" or fn.name not in OWN):
                    a = n.args[IDX[n.func.id]]
                    if not ((isinstance(a, ast.Call) and getattr(a.func, "id", None) in AF) or (isinstance(a, ast.Name) and a.id in okn)):
                        out.append((base, n.lineno, "address-source: %s argument is not from an address function" % n.func.id))
    return sorted(set(out))
