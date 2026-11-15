"""Tests of verify/bootfacts.py: the lookups in the link record (the one source of ELF facts) and bootlayout.file_bytes. Plain script, exit 0 on pass.
The record and the image are the guest image's own build (test_bootmachine.build / facts_path: verify/recipe.py through gate_build.ensure); the error cases
are variants of that record (a row added, a field removed), never a record written by hand."""
import copy
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import bootfacts  # noqa: E402
import test_bootmachine as tb  # noqa: E402

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
    raw, _path = tb.build()
    rec = bootfacts.load(tb.facts_path())
    secs = bootfacts.sections_by_name(rec)
    case("sections_by_name: every section row of the record, ranges per name", sum(len(v) for v in secs.values()) == len(rec["sections"]) and ".text" in secs, sorted(secs))
    syms = bootfacts.symbol_values(rec)
    case("symbol_values: every named symbol of the record, and only those", all(n and any(r["name"] == n for r in rec["symbols"]) for n in syms)
         and all(r["name"] in syms for r in rec["symbols"] if r["name"]))
    case("symbol: the guest image's mode table symbols are each defined once", all(bootfacts.symbol(rec, n) == syms[n][0] for n in ("fnvboot_mode_count", "fnvboot_modes")))
    n, = struct.unpack("<I", bootfacts.symbol_bytes(rec, raw, "fnvboot_mode_count"))
    t = bootfacts.symbol_bytes(rec, raw, "fnvboot_modes")
    case("symbol_bytes: the mode table's bytes read through the program headers, 8 bytes a row", n > 0 and len(t) == 8 * n, (n, len(t)))
    case("symbol: an unknown name is a FactError", raises(lambda: bootfacts.symbol(rec, "no_such_symbol_zz"), "0 times"))
    dup = copy.deepcopy(rec)
    row = next(r for r in dup["symbols"] if r["name"] == "fnvboot_mode_count")
    dup["symbols"].append(dict(row, value=row["value"] + 4))
    case("symbol: defined twice (a variant of the real record) is a FactError", raises(lambda: bootfacts.symbol(dup, "fnvboot_mode_count"), "2 times"))
    bss = copy.deepcopy(rec)
    top = max(s["address"] + s["size"] for s in rec["sections"])
    bss["symbols"].append({"name": "zz_beyond", "value": top + 0x10000, "size": 4, "type": "OBJECT", "bind": "GLOBAL", "section": "1"})
    case("symbol_bytes: a symbol beyond the file-backed part of every PT_LOAD is refused", raises(lambda: bootfacts.symbol_bytes(bss, raw, "zz_beyond"), "not file-backed"))
    with tempfile.TemporaryDirectory() as d:
        for what, doc in (("a wrong format", dict(rec, format="x/1")), ("no symbols list", {k: v for k, v in rec.items() if k != "symbols"})):
            json.dump(doc, open(d + "/bad.json", "w"))
            case("load: %s is a FactError" % what, raises(lambda: bootfacts.load(d + "/bad.json"), "bad.json"))
        case("load: a missing file is a FactError", raises(lambda: bootfacts.load(d + "/none.json"), "none.json"))
    print("%d failed" % len(FAILED) if FAILED else "all %d passed" % N[0])
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
