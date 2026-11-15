"""Tests of verify/bootfacts.py: the lookups in the link record (the one source of ELF facts) and bootlayout.file_bytes. Plain script, exit 0 on pass.
The record is written in the format of "fnvnx-elffacts/1" from the symbol table of an assembled and linked fixture (nm is the test's reference; the
product reads the record only)."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bootfacts  # noqa: E402
import bootlayout  # noqa: E402

FAILED, N = [], [0]


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def raises(f, what):
    try:
        f()
    except bootfacts.FactError as e:
        return what in str(e)
    return False


def main():
    from test_bootlayout import make_elf   # a valid image: PT_LOAD RW 0x12000..0x14000 of which 0x12000..0x12300 is file-backed (file offset 0x2000)
    raw = make_elf()

    def sym(n, v, sz, t="OBJECT"):
        return {"name": n, "value": v, "size": sz, "type": t, "bind": "GLOBAL", "section": "3"}
    symbols = [sym("tab", 0x12100, 8), sym("count", 0x12108, 4), sym("zz", 0x13000, 4), sym("_start", 0x1040, 4, "FUNC"),
               {"name": "a.o", "value": 0, "size": 0, "type": "FILE", "bind": "LOCAL", "section": "ABS"},
               {"name": "", "value": 0x12000, "size": 0, "type": "SECTION", "bind": "LOCAL", "section": "3"},
               {"name": "tab", "value": 0x500000, "size": 0, "type": "SECTION", "bind": "LOCAL", "section": "2"}]   # a SECTION row named like a symbol is no definition
    rec = {"format": "fnvnx-elffacts/1", "elf": "a.elf", "references": {}, "symbols": symbols,
           "sections": [{"index": 1, "name": ".text", "type": "PROGBITS", "address": 0x1040, "size": 4, "flags": "AX"},
                        {"index": 2, "name": ".data", "type": "PROGBITS", "address": 0x12000, "size": 0x300, "flags": "WA"},
                        {"index": 3, "name": ".data", "type": "PROGBITS", "address": 0x9000, "size": 8, "flags": "WA"}]}
    with tempfile.TemporaryDirectory() as d:
        p = d + "/elffacts.json"
        json.dump(rec, open(p, "w"))
        r = bootfacts.load(p)
        secs = bootfacts.sections_by_name(r)
        case("sections_by_name: by name, a repeated name keeps every range", secs[".data"] == [(0x12000, 0x300), (0x9000, 8)] and secs[".text"] == [(0x1040, 4)], secs)
        syms = bootfacts.symbol_values(r)
        case("symbol_values: values by name, FILE and SECTION rows are not symbols", syms["tab"] == [0x12100] and "a.o" not in syms and "" not in syms, syms)
        case("symbol: the one value", bootfacts.symbol(r, "_start") == 0x1040)
        case("symbol: an unknown name is a FactError", raises(lambda: bootfacts.symbol(r, "nope"), "0 times"))
        r2 = dict(rec, symbols=symbols + [sym("count", 0x12200, 4)])
        case("symbol: defined twice is a FactError", raises(lambda: bootfacts.symbol(r2, "count"), "2 times"))
        case("symbol_bytes: file-backed contents read back through the program headers",
             bootfacts.symbol_bytes(r, raw, "tab") == raw[0x2100:0x2108] and bootfacts.symbol_bytes(r, raw, "count") == raw[0x2108:0x210c])
        case("symbol_bytes: a bss symbol is refused (not file-backed)", raises(lambda: bootfacts.symbol_bytes(r, raw, "zz"), "not file-backed"))
        for what, doc in (("a wrong format", dict(rec, format="x/1")), ("no symbols list", {k: v for k, v in rec.items() if k != "symbols"})):
            json.dump(doc, open(d + "/bad.json", "w"))
            case("load: %s is a FactError" % what, raises(lambda: bootfacts.load(d + "/bad.json"), "bad.json"))
        case("load: a missing file is a FactError", raises(lambda: bootfacts.load(d + "/none.json"), "none.json"))
    print("%d failed" % len(FAILED) if FAILED else "all %d passed" % N[0])
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
