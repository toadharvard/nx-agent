#!/usr/bin/env python3
"""Tests for the runtime_cflags field of verify/guest_compiler.json: read through the record (recipe.runtime_cflags, guest_flags.py --runtime), and
no tracked build script spells a flag of it. Plain script: PASS/FAIL lines, exit 0 on pass."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V = os.path.dirname(HERE)
ROOT = os.path.dirname(V)
sys.path.insert(0, V)
import recipe  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " %s" % (detail,)))
    if not ok:
        FAILED.append(name)


pin = json.load(open(os.path.join(V, "guest_compiler.json")))
flags = recipe.runtime_cflags(pin)
check("the record holds -DPREFER_SIZE_OVER_SPEED and -g (DWARF CU language for the rtasm gate)", flags == ["-DPREFER_SIZE_OVER_SPEED", "-g"], flags)
out = subprocess.run([sys.executable, "-I", os.path.join(V, "guest_flags.py"), "--runtime"], capture_output=True, text=True)
check("guest_flags.py --runtime prints the record's list", out.returncode == 0 and json.loads(out.stdout) == flags, out.stderr)
for bad in (None, {}, {"runtime_cflags": "-DX"}, {"runtime_cflags": ["X"]}):
    try:
        recipe.runtime_cflags(bad)
        check("malformed %r is red" % (bad,), False)
    except recipe.BuildRed:
        check("malformed %r is red" % (bad,), True)
tracked = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True, text=True).stdout.split("\0")
spell = []
for f in tracked:
    if not f.endswith((".sh", ".mk", "Makefile", ".py", ".json")) or f.endswith(("guest_compiler.json", "PERFDEBT.json", "test_runtime_cflags.py")):
        continue
    p = os.path.join(ROOT, f)
    try:
        t = open(p, errors="ignore").read()
    except OSError:
        continue
    if any(x in t for x in flags):
        spell.append(f)
check("no tracked build script spells a runtime_cflags flag", not spell, spell)
sys.exit(1 if FAILED else 0)
