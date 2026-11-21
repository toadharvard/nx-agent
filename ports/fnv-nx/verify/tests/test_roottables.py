#!/usr/bin/env python3
"""The declared-machine and Wine rows of the admitted roots (objstub.read_root_tables, objstub.orphan_findings): the union over the roots' own tables, a duplicate id across roots,
orphan rows (VERDICT.md "Declared-machine bodies", rows are owned by the root that cites them)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx)
from fnvnx.verify import objstub  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


WH = "# id\tcontract_name\twine_ref\tkind\tvalue\n"
DH = "# id\tcontract_name\tfact\tsource\tkind\tvalue\tlasterror\n# fact: no-network | time-zone | registry | no-galaxy | single-process | process-end | thread-end\n"
with tempfile.TemporaryDirectory() as td:
    def put(root, fname, text):
        os.makedirs(os.path.join(td, root), exist_ok=True)
        open(os.path.join(td, root, fname), "w").write(text)

    def row(i, f):
        return "%d\tm\twine-9.0:dlls/x/y.c:%s\tu32\t1\n" % (i, f)
    put("a", "wine_fixme.tsv", WH + row(5, "f") + row(6, "g"))
    put("b", "wine_fixme.tsv", WH + row(7, "h"))
    w, d, ow = objstub.read_root_tables(td, ["a", "b", "c"])
    check("the union over the admitted roots; a root without a table has no rows", sorted(w) == [5, 6, 7] and d == {} and ow[("wf", 7)] == "b", (w, ow))
    w, d, ow = objstub.read_root_tables(td, ["a"])
    check("a table in a root that is not admitted is outside the universe", sorted(w) == [5, 6], w)
    res = {"a/x.c": {"cite": {"f": ["wf:5"]}, "wine": []}, "a/y.c": {"cite": {}, "wine": [("g", 0, 6)]}, "b/z.c": {"cite": {}, "wine": []}}
    _, _, ow = objstub.read_root_tables(td, ["a", "b"])
    orf = objstub.orphan_findings(res, ow)
    check("orphan: a row that no record of its own root cites is red; a cite record and a wine call site count", [(n, k) for _, n, k, _ in orf] == [("b:wf:7", "wine-fixme-orphan")], orf)
    res["a/x.c"]["cite"] = {}
    orf = objstub.orphan_findings(res, ow)
    check("orphan: a citation by another root's object does not cover the row", [n for _, n, _, _ in orf] == ["a:wf:5", "b:wf:7"], orf)
    put("b", "wine_fixme.tsv", WH + row(5, "h"))
    try:
        objstub.read_root_tables(td, ["a", "b"])
        check("an id in two roots' tables of one class is an error", False)
    except ValueError as e:
        check("an id in two roots' tables of one class is an error", "a and b" in str(e), str(e))
    put("b", "wine_fixme.tsv", WH + "5\tm\tnot-a-ref\tu32\t1\n")
    try:
        objstub.read_root_tables(td, ["b"])
        check("a malformed table is an error", False)
    except ValueError:
        check("a malformed table is an error", True)
sys.exit(1 if fails else 0)
