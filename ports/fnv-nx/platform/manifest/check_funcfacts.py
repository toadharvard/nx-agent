#!/usr/bin/env python3
"""check_funcfacts.py - the gate that keeps the removal of ASM-501 (function boundaries) in force.

    python3 -I check_funcfacts.py            # scans every .py file of this directory; exit 1 and the findings on stderr if any

Rule: the function boundaries of every manifest pass come from funcfacts.py (L3a's function facts) and only from there. By AST, in every
.py file of platform/manifest other than funcfacts.py, none of these occurs:
  P1  a bytes or str literal that holds a function-prologue or padding pattern (push ebp; mov ebp,esp in either encoding of the mov,
      hot-patch mov edi,edi; push ebp, int3 runs) anywhere except as an element of a list or tuple: that is a scan for a function
      start (a search, a match, a regex, a comparison). An element of a list (the instruction bytes of a synthetic image in a
      self-test) is data, not a scan.
  P2  a name, attribute, parameter or function definition called func_start, function_start, funcstart or fend: a boundary computed here.
  P3  a call of capstone.Cs(...) (the decoder is constructed only by funcfacts.py, which decodes only inside the reached runs).
No allow-list. The check builds its patterns from hex strings, so this file passes its own rule.
"""
import ast, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRO = [bytes.fromhex(h) for h in ("558bec", "55 89e5".replace(" ", ""), "8bff55", "cccccc")]
NAMES = {"func_start", "function_start", "funcstart", "fend"}


def prologue(v):
    if isinstance(v, str):
        v = v.encode("latin1", "ignore")
        v = v.replace(b"\\x", b"")  # a regex spelled with \x escapes: compare in the raw hex digits
        return any(p.hex().encode() in v.lower() for p in PRO)
    return isinstance(v, bytes) and any(p in v for p in PRO)


def findings(path):
    tree = ast.parse(open(path).read(), path)
    out = []
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, ast.Constant) and isinstance(c.value, (bytes, str)) and prologue(c.value):
                if not isinstance(n, (ast.List, ast.Tuple)):
                    out.append((c.lineno, "P1 a literal prologue/padding pattern used other than as an element of a list of instruction bytes"))
        names = [n.id] if isinstance(n, ast.Name) else [n.attr] if isinstance(n, ast.Attribute) else \
            [n.name] if isinstance(n, (ast.FunctionDef, ast.ClassDef)) else [n.arg] if isinstance(n, ast.arg) else []
        out += [(n.lineno, "P2 `%s`: a function boundary computed outside funcfacts.py" % x) for x in names if x in NAMES]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "Cs" and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "capstone":
            out.append((n.lineno, "P3 capstone.Cs constructed outside funcfacts.py"))
    return out


def scan(files):
    return {f: findings(f) for f in files}


def main():
    files = sorted(os.path.join(HERE, f) for f in os.listdir(HERE) if f.endswith(".py") and f not in ("funcfacts.py",))
    bad = {f: r for f, r in scan(files).items() if r}
    for f, rs in bad.items():
        for ln, msg in rs:
            sys.stderr.write("%s:%d: %s\n" % (os.path.basename(f), ln, msg))
    # the self-test of the rule: each pattern class is found in a synthetic source
    import tempfile
    A, B = (repr(bytes.fromhex(h)) for h in ("558bec", "8bff55"))
    cases = {"P1": "import re\nre.finditer(bytes.fromhex('00') + %s, s)\n" % A, "P1b": "if x == %s: pass\n" % B,
             "P1c": "re.search(%s, s)\n" % repr("".join("\\x" + h for h in ("cc", "55", "8b", "ec"))), "P2": "def func_start(a): pass\n", "P3": "import capstone\nmd = capstone.Cs(1, 2)\n"}
    for k, src in cases.items():
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as t:
            t.write(src)
        try:
            assert findings(t.name), "self-test: %s not found" % k
        finally:
            os.unlink(t.name)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as t:
        t.write("pro = [%s]\n" % A)
    try:
        assert not findings(t.name), "self-test: a list element is data"
    finally:
        os.unlink(t.name)
    if bad:
        sys.exit(1)
    print("funcfacts gate ok: %d files, no boundary computed outside funcfacts.py" % len(files))


if __name__ == "__main__":
    main()
