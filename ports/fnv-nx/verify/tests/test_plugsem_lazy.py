#!/usr/bin/env python3
"""Selftest (no build): importing verify/plugsem.py needs no DATAMODEL.json at the repository path (a stage that mounts verify/ at /imp/verify has the table only as its own input);
the semantic set is computed when asked, from the table's path given by the caller, and equals the set of the checkout. Run: python3 -I <this file>."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
DM = os.path.join(VERIFY, "..", "toolchain", "msvcmodel", "DATAMODEL.json")
with tempfile.TemporaryDirectory() as td:
    v = os.path.join(td, "imp", "verify")
    os.makedirs(v)
    for f in os.listdir(VERIFY):
        if f.endswith((".py", ".json")):
            shutil.copy(os.path.join(VERIFY, f), v)
    shutil.copy(DM, os.path.join(td, "dm.json"))
    code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import plugsem; "
            "print(' '.join(plugsem.semantic_flags(%r)))") % (v, os.path.join(td, "imp"), os.path.join(td, "dm.json"))
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
sys.path.insert(0, VERIFY)
import plugsem  # noqa: E402
ok = r.returncode == 0 and r.stdout.split() == list(plugsem.semantic_flags()) and "-mabi=ilp32" in r.stdout
print("%s plugsem imports without DATAMODEL at the repo path; semantic_flags(path) equals the checkout's set" % ("PASS" if ok else "FAIL"), "" if ok else r.stderr[-400:])
ok2 = plugsem.SEMANTICS_FLAGS == plugsem.semantic_flags() and plugsem.SEMANTICS_LIFTED[:len(plugsem.SEMANTICS_FLAGS)] == plugsem.SEMANTICS_FLAGS
print("%s SEMANTICS_FLAGS / SEMANTICS_LIFTED read as attributes" % ("PASS" if ok2 else "FAIL"))
sys.exit(0 if ok and ok2 else 1)
