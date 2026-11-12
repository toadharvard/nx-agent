#!/usr/bin/env python3
"""check_funcfacts.py - the gate that keeps ASM-501 (function boundaries) removed: image bytes and code facts enter the manifest passes only through funcfacts.py.

    python3 -I check_funcfacts.py            # scans every .py file of this directory; exit 1 and the findings on stderr if any

Structural rule over the import and data graph (no names, no byte patterns):
  G1  only funcfacts.py imports a reader of an image or of machine code: capstone, pefile, dnfile, mmap, lief, elftools, ctypes.
  G2  in the image-pass graph (funcfacts.py and every module that imports, directly or through other modules of this directory, funcfacts
      or a module of the graph: pemanifest, regsites, regclass, rtti, filesites, fontfaces, prefs_settings, ...) a file is opened in
      binary mode only by funcfacts.py: a call open(...) with a mode that contains `b` or is not a literal, os.open, Path.read_bytes /
      open_bytes, and any read of the bytes of an image or section through the parser object (`.get_memory_mapped_image`,
      `.get_data`, `.get_string_at_rva`, `.read_bytes`).
  G3  code is not readable as bytes (a run-time rule of funcfacts.Image, proven by the self-test below): every byte reader of an Image
      (u8, u32, bytes_at, zstr, cstr) raises funcfacts.CodeRead, a BaseException that `except Exception` does not catch, for any byte
      inside a code range of the L3a facts (an image without facts: every executable section); find_bytes searches only outside code.
      The one code read allowed is a relocated 32-bit word at its relocation address (an absolute address, not an opcode). Code is
      reachable only as decoded facts: FuncFacts.ins / edges / insn_at / heads. Data reads (strings, tables, relocated words) are free.
A module outside the graph (the generators of the platform tables, which read their own text inputs) is not constrained by G2/G3; G1
holds for every module. The graph is computed from the imports, never listed.
The self-tests build synthetic modules for each of the three rules and for a module that reaches the graph through another module.
"""
import ast, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
READERS = {"capstone", "pefile", "dnfile", "mmap", "lief", "elftools", "ctypes"}
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
    """{module: [(line, rule, text)]} for the modules of `trees` ({name: ast}) other than funcfacts."""
    g = graph(trees)
    out = {}
    for m, t in trees.items():
        if m == "funcfacts":
            continue
        r = []
        for n in ast.walk(t):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                bad = [x for x in imports(ast.Module([n], [])) if x in READERS]
                r += [(n.lineno, "G1", "imports %s" % x) for x in bad]
            if m in g:
                if binary_open(n):
                    r.append((n.lineno, "G2", "opens or reads a file as bytes / through the parser"))
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


def selftest_code_read():
    """G3: a byte read at a code address is red; data reads, a relocated word in code, and decoded facts are not."""
    sys.path.insert(0, HERE)
    import funcfacts
    code = bytes.fromhex("558bec" "b800304000" "5dc3")            # push ebp; mov ebp,esp; mov eax,0x403000; pop ebp; ret
    full = bytearray(0x3100)
    full[0x1000:0x1000 + len(code)] = code
    full[0x3000:0x3006] = b"hello\0"
    im = funcfacts.Image.synthetic("g3.exe", 0x400000, full, [0x401004], [], [(0x401000, 0x100)])
    funcfacts.declare(im, {0x401000: [(0x401000, 0x401000 + len(code))]})
    assert im.zstr(0x403000, 16) == b"hello" and im.cstr(0x403000) == "hello" and im.bytes_at(0x403000, 3) == b"hel"   # data
    assert im.u32(0x401004) == 0x403000                                                                              # a relocated word
    for read in (lambda: im.u8(0x401000), lambda: im.bytes_at(0x401000, 4), lambda: im.u32(0x401001), lambda: im.u32(0x401005),
                 lambda: im.zstr(0x401000, 8), lambda: im.bytes_at(0x400ffe, 4)):
        try:
            read()
        except Exception:
            raise AssertionError("CodeRead must not be an Exception")
        except funcfacts.CodeRead:
            continue
        raise AssertionError("a byte read at a code address was not red")
    assert im.find_bytes(b"\x55\x8b\xec") is None and im.find_bytes(b"hello\0") == 0x403000                       # code is not searched
    ff = funcfacts.for_image(im)
    assert [i.mnemonic for i in ff.ins(0x401000)] == ["push", "mov", "mov", "pop", "ret"] and ff.insn_at(0x401003).mnemonic == "mov"
    nf = funcfacts.Image.synthetic("g3b.exe", 0x400000, full, [], [], [(0x401000, 0x100)])   # facts are absent: all of the section is code
    funcfacts.declare_none(nf)
    try:
        nf.u8(0x401050)
    except funcfacts.CodeRead:
        pass
    else:
        raise AssertionError("no facts: the executable section must be unreadable")


def selftest():
    selftest_code_read()
    def run(sources):
        trees = {k: ast.parse(v) for k, v in sources.items()}
        return findings(trees)
    base = {"funcfacts": "import capstone, pefile\n"}
    assert run(dict(base, a="import capstone\n")).get("a", [])[0][1] == "G1"
    assert run(dict(base, a="from pefile import PE\n"))["a"][0][1] == "G1"
    assert run(dict(base, a="import funcfacts\nx = open(p, 'rb')\n"))["a"][0][1] == "G2"
    assert run(dict(base, a="import funcfacts\nx = open(p, mode)\n"))["a"][0][1] == "G2"
    assert run(dict(base, a="import funcfacts\nx = f.get_data(1, 2)\n"))["a"][0][1] == "G2"
    # a module reaches the graph only through another module: still constrained
    assert run(dict(base, a="import funcfacts\n", b="import a\nx = open(p, 'rb')\n"))["b"][0][1] == "G2"
    # outside the graph: binary files of other tables are not image reads; text opens are fine inside it
    assert "c" not in run(dict(base, c="x = open(p, 'rb')\n"))
    assert "a" not in run(dict(base, a="import funcfacts\nx = open(p, encoding='utf-8')\ny = open(p, 'w')\n"))
    assert "a" not in run(dict(base, a="import funcfacts\nx = [b'\\x55\\x8b\\xec']\n"))   # the rule is the graph, not byte patterns


if __name__ == "__main__":
    main()
