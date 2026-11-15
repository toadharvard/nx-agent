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
    __import__('atexit').register(__import__('shutil').rmtree, tmp, True)   # a test leaves no scratch behind
    os.chdir(tmp)
    open("a.c", "w").write("#include <string.h>\nint g_data = 3; static int loc = 1;\nint f_b(int);\nint (*fp)(int) = f_b; int f_a(const char *s) { return (int)strlen(s) + f_b(loc) + g_data + fp(2); }\n")
    open("b.c", "w").write("int f_b(int x) { return x + 1; }\n")
    open("m.c", "w").write('extern char _end[];\nint f_a(const char *); int main(void) { return f_a("x") + (int)(long)_end; }\n')
    sh("gcc", "-c", "-O1", "a.c", "b.c", "m.c")
    prod = {"m.o": "root:m.c", "a.o": "root:a.c", "b.o": "root:b.c"}
    sh("gcc", "-static", "m.o", "a.o", "b.o", "-Wl,--emit-relocs", "-Wl,--defsym,fnv_abs=0x1000", "-Wl,--cref", "-Wl,--no-demangle", "-Wl,-Map=x.map", "-o", "x.elf")
    all_t, cref_t = sh("readelf", "-W", "-h", "-S", "-r", "-s", "x.elf"), open("x.map").read()
    secs_t = syms_t = all_t

    secs = elffacts.parse_sections(secs_t)
    ref_secs, ref_syms = struct_elf("x.elf")
    check("sections: name, address, size match the section header table read with struct",
          [(s["name"], s["address"], s["size"]) for s in secs] == [(n, a, z) for n, a, z, _ in ref_secs], (secs[:3], ref_secs[:3]))
    check("sections: flags are readelf's letters (.text AX, .data WA, .bss WA, .rodata A)",
          {s["name"]: s["flags"] for s in secs if s["name"] in (".text", ".data", ".bss", ".rodata")} == {".text": "AX", ".data": "WA", ".bss": "WA", ".rodata": "A"},
          {s["name"]: s["flags"] for s in secs})
    syms = elffacts.parse_symbols(syms_t)
    check("symbols: the defined .symtab symbols match the struct reader (name, value, size, binding, section)",
          [("" if s["type"] == "SECTION" else s["name"], s["value"], s["size"], s["bind"], s["section"]) for s in syms]
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
    rec = elffacts.record("x.elf", all_t, cref_t, cref_t, prod, defs_of)
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
    check("record: deterministic (same texts, same record) and has the format key", elffacts.record("x.elf", all_t, cref_t, cref_t, prod, defs_of) == rec and rec["format"] == elffacts.FORMAT)

    # header, relocations, call edges, address-taken symbols, archive members
    check("header: machine, type and entry point of the ELF match readelf -h and the struct reader",
          rec["header"]["entry"] == struct.unpack_from("<Q", open("x.elf", "rb").read(), 0x18)[0] and rec["header"]["type"] in ("EXEC", "DYN") and rec["header"]["machine"].endswith("X86-64"), rec["header"])
    check("relocations: --emit-relocs keeps the static relocations, one row per readelf entry",
          len(rec["relocations"]) == sum(int(m) for m in __import__("re").findall(r"^Relocation section .* contains (\d+) entr", all_t, __import__("re").M)) and any(r[0] == ".rela.text" for r in rec["relocations"]), len(rec["relocations"]))
    edges = [e for e in rec["call_edges"] if e["caller"] == ["f_a"] and "f_b" in e["callee"]]
    fa_ = next(s for s in syms if s["name"] == "f_a")
    check("call edges: f_a calls f_b at a site inside f_a with its offset", len(edges) == 1 and fa_["value"] <= edges[0]["site"] < fa_["value"] + fa_["size"] and edges[0]["caller_offset"] == edges[0]["site"] - fa_["value"], rec["call_edges"][:3])
    check("call edges: main calls f_a", any(e["caller"] == ["main"] and e["callee"] == ["f_a"] for e in rec["call_edges"]))
    check("address taken: f_b is (the function pointer fp), f_a is not (it is only called)",
          any("f_b" in a["symbol"] for a in rec["address_taken"]) and not any("f_a" in a["symbol"] for a in rec["address_taken"]), [a for a in rec["address_taken"] if a["symbol"]][:6])
    check("members: libc.a(strlen.o) is pulled in by a.o for strlen",
          any(m["member"].endswith("libc.a(strlen.o)") and m["requested_by"].endswith("a.o") and m["symbol"] == "strlen" for m in rec["members"]), rec["members"][:3])
    check("members: every member is an ARCHIVE(MEMBER) and the order is ld's", all(m["member"].endswith(")") for m in rec["members"]) and rec["members"] == elffacts.parse_members(cref_t))
    nr = elffacts.parse_relocs("There are no relocations in this file.\n")
    check("relocations: a file with none gives no rows", nr == [])
    sec0 = elffacts.parse_relocs("Relocation section '.rela.dyn' at offset 0x1 contains 1 entry:\n    Offset Info Type Symbol's Value Symbol's Name + Addend\n0000000000003df0  0000000000000008 R_X86_64_RELATIVE                         1120\n")
    check("relocations: a row with no symbol carries its addend as target", sec0 == [[".rela.dyn", 0x3df0, "R_X86_64_RELATIVE", "", 0x1120, 0x1120]], sec0)

    # the consumer of the ABS symbols (contract/pcrel_abs.py) reads the record, and agrees with nm
    import json
    sys.path.insert(0, os.path.join(os.path.dirname(VERIFY), "contract"))
    import pcrel_abs
    json.dump(rec, open("facts.json", "w"))
    nm_abs = {f[2] for f in (l.split() for l in sh("nm", "x.elf").splitlines()) if len(f) == 3 and f[1] in "Aa"}
    check("pcrel_abs.absolute_symbols reads the record and equals nm's absolute symbols (and has the --defsym)",
          pcrel_abs.absolute_symbols("facts.json") == nm_abs and "fnv_abs" in nm_abs, (sorted(nm_abs)[:5], sorted(pcrel_abs.absolute_symbols("facts.json"))[:5]))

    # totality: a malformed row, a wrong count, a missing table
    def raises(f, *a):
        try:
            f(*a)
        except elffacts.ElfFactsError:
            return True
        return False
    check("parse_sections: a row that does not fit raises", raises(elffacts.parse_sections, secs_t.replace(" .text ", " .text X ", 1)))
    check("parse_sections: a wrong section count raises", raises(elffacts.parse_sections, __import__("re").sub(r"(Number of section headers:\s+)(\d+)", lambda m: m[1] + str(int(m[2]) + 1), secs_t, count=1)))
    check("parse_symbols: a wrong entry count raises", raises(elffacts.parse_symbols, syms_t.replace("Symbol table '.symtab' contains", "Symbol table '.symtab' contains 9", 1)))
    check("parse_symbols: a missing table raises", raises(elffacts.parse_symbols, syms_t, ".dynsym"))
    check("parse_relocs: a wrong entry count raises", raises(elffacts.parse_relocs, "Relocation section '.rela.dyn' at offset 0x1 contains 2 entries:\n0000000000003df0  0000000000000008 R_X86_64_RELATIVE   1120\n"))
    check("parse_relocs: a row that does not fit raises", raises(elffacts.parse_relocs, "Relocation section '.s' at offset 0x1 contains 1 entry:\nnot a row\n"))
    check("parse_header: a header without an entry point raises", raises(elffacts.parse_header, "  Type: EXEC\n  Machine: x\n"))
    check("derive: a machine with no call-relocation table raises", raises(elffacts.derive, {"machine": "Z80"}, [], [], []))
    check("parse_members: no section in a text that is no ld map raises", raises(elffacts.parse_members, "nothing"))
    check("parse_members: a map with no archive section pulled no member (ld prints the heading with the first one)", elffacts.parse_members("Memory Configuration\n\nName Origin\n") == [])
    check("parse_cref: no table raises", raises(elffacts.parse_cref, "no table"))
    check("definers: a file ld printed that is no input of the link raises", raises(elffacts.definers, {"s": ["zzz.o"]}, {}, lambda f: None, set()))
    # a long name puts its file on the next line of ld's table
    long = elffacts.parse_cref("Cross Reference Table\n\nSymbol                                            File\n%s\n                                                  a.o\n                                                  b.o\nshort                                             c.o\n" % ("x" * 60))
    check("parse_cref: a name that fills the symbol column has its file on the next line", long == {"x" * 60: ["a.o", "b.o"], "short": ["c.o"]}, long)
    print("fails: %d" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
