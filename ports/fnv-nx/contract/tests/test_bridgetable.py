#!/usr/bin/env python3
"""bridgetable.derive: an msvcrt C++ name (operator new/delete) is served by the name table like every other msvcrt name, never by a constructed symbol.
  - ??2@YAPAXI@Z with a name-table row `c_new func` is a `lookup` row with native `-` (the table's symbol is judged by contract/imports.judge);
  - the same name without a table row is bind `none` (red at the gate), not a made-up fnvnx_msvcrt_operator_new."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # ports/fnv-nx: contract.X by its qualified name
from contract import bridgetable as B  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


spec = lambda dll: "@ cdecl ??2@YAPAXI@Z(long)\n@ cdecl ??3@YAXPAX@Z(ptr)\n"  # noqa: E731
names = "msvcrt.dll\t??2@YAPAXI@Z\nmsvcrt.dll\t??3@YAXPAX@Z\n"
rows, errs, _ = B.derive(names, spec, None, B.read_name_table("??2@YAPAXI@Z\tc_new\tfunc\n??3@YAXPAX@Z\tc_free\tfunc\n"), [])
check("operator new/delete with table rows are lookup rows, native -", errs == [] and [(r[1], r[6], r[7]) for r in rows] == [("??2@YAPAXI@Z", "-", "lookup"), ("??3@YAXPAX@Z", "-", "lookup")], (rows, errs))
rows, errs, _ = B.derive(names, spec, None, B.read_name_table("??3@YAXPAX@Z\tc_free\tfunc\n"), [])
check("operator new without a table row is bind none, no invented symbol", [(r[1], r[6], r[7]) for r in rows][0] == ("??2@YAPAXI@Z", "-", "none"), rows)
check("no fnvnx_msvcrt_operator_* name is constructed", not any("operator" in r[6] for r in rows), rows)
sys.exit(1 if fails else 0)
