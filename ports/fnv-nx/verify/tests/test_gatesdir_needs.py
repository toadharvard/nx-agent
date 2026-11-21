#!/usr/bin/env python3
"""gatesdir `needs`: the run order of the gates is topological over `needs`, ties by name; an unknown name or a cycle is Bad; the real gates.d runs reach before objstub.
Plain script: exit 0 on pass, one PASS/FAIL line per case."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from fnvnx.verify import gatesdir  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    if not cond:
        FAILED.append(name)
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))


def entry(name, *needs):
    return gatesdir.parse_entry(name, json.dumps(dict({"format": gatesdir.FILE_FORMAT, "name": name, "argv": ["x"], "result": name + ".json"}, **({"needs": list(needs)} if needs else {}))))


names = lambda es: [g["name"] for g in gatesdir.order(es)]
check("no needs: sorted by name", names([entry("b"), entry("a"), entry("c")]) == ["a", "b", "c"])
check("a gate runs after the gate it needs, though its name sorts first", names([entry("objstub", "reach"), entry("reach"), entry("abs")]) == ["abs", "reach", "objstub"])
check("a chain: needs of needs", names([entry("a", "b"), entry("b", "c"), entry("c")]) == ["c", "b", "a"])
check("ties by name among ready gates", names([entry("z"), entry("m", "z"), entry("b")]) == ["b", "z", "m"])
for what, es in (("unknown name", [entry("a", "nope")]), ("cycle", [entry("a", "b"), entry("b", "a")]), ("self need", [entry("a", "a")])):
    try:
        gatesdir.order(es)
        ok = False
    except gatesdir.Bad:
        ok = True
    check("%s is Bad" % what, ok)
for what, v in (("a string", "reach"), ("a repeated name", ["a", "a"]), ("an empty name", [""])):
    try:
        gatesdir.parse_entry("g", json.dumps({"format": gatesdir.FILE_FORMAT, "name": "g", "argv": ["x"], "result": "g.json", "needs": v}))
        ok = False
    except gatesdir.Bad:
        ok = True
    check("needs as %s is Bad" % what, ok)
doc = gatesdir.load_dir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gates.d"))
n = [g["name"] for g in doc["gates"]]
check("the real gates.d: reach runs before objstub", n.index("reach") < n.index("objstub"), n)
print("%d failed" % len(FAILED))
sys.exit(1 if FAILED else 0)
