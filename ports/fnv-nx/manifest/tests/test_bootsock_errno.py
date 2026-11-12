"""Every errno that verify/bootsock.py (the host of the boot machine) can return is a name of the generated hostabi/include/fnvnx/sockwire_errno.h
with the same number. The header is read, there is no second list. bootsock.py writes errnos only through neg(WE_<NAME>), WE_<NAME> being
module-level integers; the test is red on any other argument of neg() and on any WE_ name or number that is not in the list."""
import ast, os, re, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
hdr = open(os.path.join(ROOT, "hostabi", "include", "fnvnx", "sockwire_errno.h")).read()
wire = {m.group(1): int(m.group(2)) for m in re.finditer(r"X\((E[A-Z0-9]+), (\d+)\)", hdr)}
src = open(os.path.join(ROOT, "verify", "bootsock.py")).read()
tree = ast.parse(src)
consts = {}
for n in ast.walk(tree):
    if isinstance(n, ast.Assign):
        for t in n.targets:
            names = t.elts if isinstance(t, ast.Tuple) else [t]
            vals = n.value.elts if isinstance(n.value, ast.Tuple) else [n.value]
            if len(names) == len(vals):
                for a, v in zip(names, vals):
                    if isinstance(a, ast.Name) and a.id.startswith("WE_") and isinstance(v, ast.Constant) and isinstance(v.value, int):
                        consts[a.id] = v.value
bad = []
for name, num in sorted(consts.items()):
    if wire.get("E" + name[3:]) != num:
        bad.append("%s = %d is not in the generated list" % (name, num))
used = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "neg":
        a = n.args[0] if n.args else None
        if isinstance(a, ast.Name) and a.id in consts:
            used.add(a.id)
        elif not (isinstance(n.func, ast.Name) and isinstance(a, ast.Name) and a.id == "e"):   # neg's own definition body uses `e`
            bad.append("line %d: neg() of something that is not a WE_ constant" % n.lineno)
if bad:
    sys.exit("bootsock.py returns errnos outside the generated list:\n  " + "\n  ".join(bad))
print("bootsock errnos ok: %d constants, %d used, all in the %d-name generated list" % (len(consts), len(used), len(wire)))
