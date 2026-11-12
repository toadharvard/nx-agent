#!/usr/bin/env python3
"""check_funcfacts.py - the gate that keeps ASM-501 removed: image bytes and code facts enter the manifest passes only through funcfacts.py.

    python3 -I check_funcfacts.py            # scans every .py file of this directory; exit 1 and the findings on stderr if any

Structural rule over the import and data graph (no names, no byte patterns):
  G1  only funcfacts.py imports a reader of an image: pefile, dnfile (mmap, lief, elftools, ctypes: nobody). No module of this directory, funcfacts.py
      included, imports a decoder of machine code (capstone): the one x86 decoder is the L1 contract's imagerec.py (funcfacts.use_contract).
  G2  in the image-pass graph (funcfacts.py and every module that imports, directly or through other modules of this directory, funcfacts
      or a module of the graph: pemanifest, regsites, regclass, rtti, filesites, fontfaces, prefs_settings, ...) a file is opened in
      binary mode only by funcfacts.py: a call open(...) with a mode that contains `b` or is not a literal, os.open, Path.read_bytes /
      open_bytes, and any read of the bytes of an image or section through the parser object (`.get_memory_mapped_image`,
      `.get_data`, `.get_string_at_rva`, `.read_bytes`).
  G3  in the graph the mapped bytes are not reached by attribute: no access to `.img`, `._img`, `.pe` of any object (the Image record's
      accessors u8/u32/bytes_at/zstr/cstr/find_bytes and funcfacts' typed code facts are the interface).
A module outside the graph (the generators of the platform tables, which read their own text inputs) is not constrained by G2/G3; G1
holds for every module. The graph is computed from the imports, never listed.
The self-tests build synthetic modules for each of the three rules and for a module that reaches the graph through another module.
"""
import ast, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
READERS = {"capstone", "pefile", "dnfile", "mmap", "lief", "elftools", "ctypes"}
IMAGE_PARSERS = {"pefile", "dnfile"}      # funcfacts.py alone
IMAGE_ATTRS = {"img", "_img", "pe"}
PARSER_CALLS = {"get_memory_mapped_image", "get_data", "get_string_at_rva", "read_bytes", "open_bytes"}


def parse(path):
    return ast.parse(open(path).read(), path)


def imports(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module.split(".")[0])
            out |= {a.name.split(".")[0] for a in n.names}   # `from . import x` / `from pkg import mod`
    return out


def graph(trees):
    """module -> its tree, for funcfacts and every module that imports a module already in the graph (least fixed point)."""
    g = {"funcfacts"} & set(trees)
    changed = True
    while changed:
        changed = False
        for m, t in trees.items():
            if m not in g and imports(t) & g:
                g.add(m)
                changed = True
    return g


def binary_open(n):
    if not isinstance(n, ast.Call):
        return False
    f = n.func
    if isinstance(f, ast.Name) and f.id == "open":
        mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
        return mode is not None and not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" not in mode.value)
    return isinstance(f, ast.Attribute) and (f.attr in PARSER_CALLS or f.attr == "open" and isinstance(f.value, ast.Name) and f.value.id == "os")


def findings(trees):
    """{module: [(line, rule, text)]} for the modules of `trees` ({name: ast}); funcfacts itself only for G1 (it may import pefile, dnfile)."""
    g = graph(trees)
    out = {}
    for m, t in trees.items():
        r = []
        for n in ast.walk(t):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                bad = [x for x in imports(ast.Module([n], [])) if x in READERS - (IMAGE_PARSERS if m == "funcfacts" else set())]
                r += [(n.lineno, "G1", "imports %s" % x) for x in bad]
            if m in g and m != "funcfacts":
                if binary_open(n):
                    r.append((n.lineno, "G2", "opens or reads a file as bytes / through the parser"))
                if isinstance(n, ast.Attribute) and n.attr in IMAGE_ATTRS:
                    r.append((n.lineno, "G3", "attribute .%s reaches the mapped image or its parser" % n.attr))
        if r:
            out[m] = r
    return out


def main():
    names = sorted(f for f in os.listdir(HERE) if f.endswith(".py"))
    trees = {f[:-3]: parse(os.path.join(HERE, f)) for f in names}
    bad = findings(trees)
    for m, rs in sorted(bad.items()):
        for ln, rule, text in sorted(rs):
            sys.stderr.write("%s.py:%d: %s %s\n" % (m, ln, rule, text))
    selftest()
    if bad:
        sys.exit(1)
    print("funcfacts gate ok: %d modules, %d in the image-pass graph, image bytes only through funcfacts.py"
          % (len(trees), len(graph(trees))))


def selftest():
    def run(sources):
        trees = {k: ast.parse(v) for k, v in sources.items()}
        return findings(trees)
    base = {"funcfacts": "import pefile\n"}
    assert run(dict(base, a="import capstone\n")).get("a", [])[0][1] == "G1"
    assert run({"funcfacts": "import capstone\n"})["funcfacts"][0][1] == "G1"          # no decoder in platform/, funcfacts included
    assert "funcfacts" not in run({"funcfacts": "import pefile\nx = open(p, 'rb')\n"})
    assert run(dict(base, a="from pefile import PE\n"))["a"][0][1] == "G1"
    assert run(dict(base, a="import funcfacts\nx = open(p, 'rb')\n"))["a"][0][1] == "G2"
    assert run(dict(base, a="import funcfacts\nx = open(p, mode)\n"))["a"][0][1] == "G2"
    assert run(dict(base, a="import funcfacts\nx = f.get_data(1, 2)\n"))["a"][0][1] == "G2"
    assert run(dict(base, a="import funcfacts\nx = m.img[0]\n"))["a"][0][1] == "G3"
    # before use_contract nothing of the contract is usable: every reader raises, no module-level fallback
    sys.path.insert(0, HERE)
    import funcfacts
    assert funcfacts._ic is None, "selftest runs before funcfacts.use_contract"
    for f in (lambda: funcfacts.X.X86_OP_IMM, lambda: funcfacts.FuncFacts("x", funcfacts.Image.synthetic("x", 0x400000, b"\x90", [], [], [(0x400000, 1)]),
                                                                        {0x400000: [(0x400000, 0x400001)]}).ins(0x400000)):
        try:
            f()
        except SystemExit:
            continue
        raise AssertionError("a reader worked before funcfacts.use_contract")
    # a module reaches the graph only through another module: still constrained
    assert run(dict(base, a="import funcfacts\n", b="import a\nx = open(p, 'rb')\n"))["b"][0][1] == "G2"
    # outside the graph: binary files of other tables are not image reads; text opens are fine inside it
    assert "c" not in run(dict(base, c="x = open(p, 'rb')\ny = o.img\n"))
    assert "a" not in run(dict(base, a="import funcfacts\nx = open(p, encoding='utf-8')\ny = open(p, 'w')\n"))
    assert "a" not in run(dict(base, a="import funcfacts\nx = [b'\\x55\\x8b\\xec']\n"))   # the rule is the graph, not byte patterns


if __name__ == "__main__":
    main()
