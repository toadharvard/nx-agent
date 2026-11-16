#!/usr/bin/env python3
"""bridgetable.derive: the native symbol and the return letter of a function row come from its export record alone.
  - ??2@YAPAXI@Z with a record `c_new` is a `link` row with native c_new and the record's ret;
  - the same name without a record is an error, never a made-up fnvnx_msvcrt_operator_new and never a lookup row."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))   # ports/fnv-nx: contract.X by its qualified name
from contract import bridgetable as B  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


spec = lambda dll: "@ cdecl ??2@YAPAXI@Z(long)\n@ cdecl ??3@YAXPAX@Z(ptr)\n"  # noqa: E731
names = "msvcrt.dll\t??2@YAPAXI@Z\nmsvcrt.dll\t??3@YAXPAX@Z\n"
ex = {("msvcrt.dll", "??2@YAPAXI@Z"): ("c_new", "func", "i", "i"), ("msvcrt.dll", "??3@YAXPAX@Z"): ("c_free", "func", "v", "i")}
rows, errs, _ = B.derive(names, spec, None, [], {}, ex)
check("operator new/delete with records are link rows with the record's symbol and ret", errs == [] and [(r[1], r[4], r[6], r[7]) for r in rows] == [("??2@YAPAXI@Z", "i", "c_new", "link"), ("??3@YAXPAX@Z", "v", "c_free", "link")], (rows, errs))
rows, errs, _ = B.derive(names, spec, None, [], {}, {k: v for k, v in ex.items() if "??3" in k[1]})
check("operator new without a record is an error, no invented symbol", any("??2@YAPAXI@Z: no export record" in e for e in errs) and not any("operator" in r[6] for r in rows), (rows, errs))
sys.exit(1 if fails else 0)
