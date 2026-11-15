#!/usr/bin/env python3
"""Tests for verify/elffacts.py on a toy link made with the installed binutils (2.42, the version env.json pins): sections and symbols match an
independent struct reader of the ELF, the cross reference table gives the definer of every resolved reference of a product object, the definer of a
libc symbol is the archive member, a linker-defined name is `<linker>`, and every parser is total (a malformed row or a wrong count raises).
Plain script: exit 0 on pass. Needs gcc, ld, readelf and the static libc.a on the machine; the layouts are those of binutils 2.42."""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, os.path.join(os.path.dirname(VERIFY), "link"))
import elffacts  # noqa: E402
import relocs  # noqa: E402
import undefset  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)[:500]) if detail and not ok else ""), flush=True)
    if not ok:
        FAILED.append(name)


def sh(*a, **kw):
    return subprocess.run(a, capture_output=True, text=True, check=True, **kw).stdout


def struct_elf(path):
    """([(name, addr, size, flags)] of the sections, [(name, value, size, bind, shndx)] of the defined .symtab entries) read with struct (ELF64 LE)"""
    d = open(path, "rb").read()
    shoff, = struct.unpack_from("<Q", d, 0x28)
    esz, num, stri = struct.unpack_from("<HHH", d, 0x3a)
    sh_ = [struct.unpack_from("<IIQQQQIIQQ", d, shoff + i * esz) for i in range(num)]
    st = sh_[stri]
    names = d[st[4]:st[4] + st[5]]
    nm = lambda o: names[o:names.index(b"\0", o)].decode()  # noqa: E731
    secs = [(nm(s[0]), s[3], s[5], s[2]) for s in sh_[1:]]
    sy = next(s for s in sh_ if s[1] == 2)
    strt = sh_[sy[6]]
    stb = d[strt[4]:strt[4] + strt[5]]
    syms = []
    for k in range(sy[5] // 24):
        n, info, other, shndx, val, size = struct.unpack_from("<IBBHQQ", d, sy[4] + 24 * k)
        if shndx != 0:
            syms.append((stb[n:stb.index(b"\0", n)].decode(), val, size, info >> 4, shndx))
    return secs, syms


def main():
    tmp = tempfile.mkdtemp(prefix="elffacts-")
    os.chdir(tmp)
    open("a.c", "w").write("#include <string.h>\nint g_data = 3; static int loc = 1;\nint f_b(int);\nint f_a(const char *s) { return (int)strlen(s) + f_b(loc) + g_data; }\n")
    open("b.c", "w").write("int f_b(int x) { return x + 1; }\n")
    open("m.c", "w").write('extern char _end[];\nint f_a(const char *); int main(void) { return f_a("x") + (int)(long)_end; }\n')
    sh("gcc", "-c", "-O1", "a.c", "b.c", "m.c")
    prod = {"m.o": "root:m.c", "a.o": "root:a.c", "b.o": "root:b.c"}
    sh("gcc", "-static", "m.o", "a.o", "b.o", "-Wl,--cref", "-Wl,--no-demangle", "-Wl,-Map=x.map", "-o", "x.elf")
    secs_t, syms_t, cref_t = sh("readelf", "-W", "-S", "x.elf"), sh("readelf", "-W", "-s", "x.elf"), open("x.map").read()

    secs = elffacts.parse_sections(secs_t)
    ref_secs, ref_syms = struct_elf("x.elf")
    check("sections: name, address, size match the section header table read with struct",
          [(s["name"], s["address"], s["size"]) for s in secs] == [(n, a, z) for n, a, z, _ in ref_secs], (secs[:3], ref_secs[:3]))
    check("sections: flags are readelf's letters (.text AX, .data WA, .bss WA, .rodata A)",
          {s["name"]: s["flags"] for s in secs if s["name"] in (".text", ".data", ".bss", ".rodata")} == {".text": "AX", ".data": "WA", ".bss": "WA", ".rodata": "A"},
          {s["name"]: s["flags"] for s in secs})
    syms = elffacts.parse_symbols(syms_t)
    check("symbols: the defined .symtab symbols match the struct reader (name, value, size, binding, section)",
          [(s["name"], s["value"], s["size"], s["bind"], s["section"]) for s in syms]
          == [(n, v, z, {0: "LOCAL", 1: "GLOBAL", 2: "WEAK", 10: "UNIQUE"}[b], str(i) if i < 0xff00 else {0xfff1: "ABS", 0xfff2: "COM"}[i]) for n, v, z, b, i in ref_syms],
          (len(syms), len(ref_syms)))
    fa = next(s for s in syms if s["name"] == "f_a")
    check("symbols: f_a is a GLOBAL FUNC with its size, g_data a GLOBAL OBJECT of 4 bytes",
          (fa["type"], fa["bind"]) == ("FUNC", "GLOBAL") and fa["size"] > 0 and next(s for s in syms if s["name"] == "g_data")["size"] == 4 and
          next(s for s in syms if s["name"] == "g_data")["type"] == "OBJECT", fa)

    # the symbol tables of every input: the product objects, and every archive member and start file the link line names
    libdir = os.path.dirname(sh("gcc", "-print-file-name=libc.a").strip())
    inputs = {}
    for o in prod:
        inputs[o] = undefset.object_symbols(open(o, "rb").read())[0]
    cache = {}

    def defs_of(f):
        if f in inputs:
            return inputs[f]
        if f in cache:
            return cache[f]
        if f.endswith(")"):
            ar, mem = f[:-1].rsplit("(", 1)
            for n, _, b in relocs.ar_members(ar):
                cache[ar + "(" + n + ")"] = undefset.object_symbols(b)[0]
            return cache.get(f)
        if os.path.isfile(f):
            cache[f] = undefset.object_symbols(open(f, "rb").read())[0]
            return cache[f]
        return None
    rec = elffacts.record("x.elf", secs_t, syms_t, cref_t, prod, defs_of)
    refs = rec["references"]
    check("definers: f_b is defined by the product object b.o (referenced by a.o)", {"symbol": "f_b", "definer": "b.o"} in refs["root:a.c"], refs["root:a.c"])
    check("definers: f_a is defined by a.o, referenced by m.o", {"symbol": "f_a", "definer": "a.o"} in refs["root:m.c"], refs["root:m.c"])
    st = [x for x in refs["root:a.c"] if x["symbol"] == "strlen"]
    check("definers: the definer of the libc symbol strlen is the archive member libc.a(strlen.o)",
          len(st) == 1 and st[0]["definer"].endswith("libc.a(strlen.o)") and st[0]["definer"].startswith("/"), st)
    check("definers: a product object has no entry for what it defines itself, and none for a name nobody defines",
          all(x["symbol"] != "f_b" for x in refs["root:b.c"]) and all(x["symbol"] != "__gmon_start__" for v in refs.values() for x in v), refs["root:b.c"])
    cref = elffacts.parse_cref(cref_t)
    check("definers: _end referenced by m.o has the definer <linker>", {"symbol": "_end", "definer": "<linker>"} in refs["root:m.c"], (cref["_end"][:3], refs["root:m.c"]))
    check("record: deterministic (same texts, same record) and has the format key", elffacts.record("x.elf", secs_t, syms_t, cref_t, prod, defs_of) == rec and rec["format"] == elffacts.FORMAT)

    # totality: a malformed row, a wrong count, a missing table
    def raises(f, *a):
        try:
            f(*a)
        except elffacts.ElfFactsError:
            return True
        return False
    check("parse_sections: a row that does not fit raises", raises(elffacts.parse_sections, secs_t.replace(" .text ", " .text X ", 1)))
    check("parse_sections: a wrong section count raises", raises(elffacts.parse_sections, secs_t.replace("There are 28", "There are 29", 1).replace("There are 29", "There are 99", 1)
                                                                  if "There are 28" in secs_t else secs_t.replace("There are", "There are 9", 1)))
    check("parse_symbols: a wrong entry count raises", raises(elffacts.parse_symbols, syms_t.replace("contains", "contains 9", 1)))
    check("parse_symbols: a missing table raises", raises(elffacts.parse_symbols, syms_t, ".dynsym"))
    check("parse_cref: no table raises", raises(elffacts.parse_cref, "no table"))
    check("definers: a file ld printed that is no input of the link raises", raises(elffacts.definers, {"s": ["zzz.o"]}, {}, lambda f: None, set()))
    # a long name puts its file on the next line of ld's table
    long = elffacts.parse_cref("Cross Reference Table\n\nSymbol                                            File\n%s\n                                                  a.o\n                                                  b.o\nshort                                             c.o\n" % ("x" * 60))
    check("parse_cref: a name that fills the symbol column has its file on the next line", long == {"x" * 60: ["a.o", "b.o"], "short": ["c.o"]}, long)
    print("fails: %d" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
