#!/usr/bin/env python3
"""Tests for verify/gate_hostapi.py: declared = used per root, negatives, universe 0. No game data."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import gate_hostapi as g  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


HDR = """#define FNVNX_EXT_PLATFORM 11u
#define FNVNX_EXT_DK 12u /* module id, not a service */
#define FNVNX_PLAT_A 1u /* FNVNX_PLAT_ZZ in a comment */
#define FNVNX_PLAT_B 2u
#define FNVNX_PLAT_FIRST 1u
#define FNVNX_PLAT_NOTNUM (1u+2u)
"""
d = g.declared(HDR)
check("declared by number, aliases join", d == {1: ["FNVNX_PLAT_A", "FNVNX_PLAT_FIRST"], 2: ["FNVNX_PLAT_B"]}, d)
check("module ids are no services", all(not n.startswith("FNVNX_EXT") for ns in d.values() for n in ns))
check("number grammar", [g.number(t) for t in ("5u", "0x1F", "010", "x1", "")] == [5, 31, 8, None, None])
u, c, ids = g.judge(d, {"FNVNX_PLAT_A": 1, "FNVNX_PLAT_B": 2})
check("all used green", (u, c, ids) == (2, 2, []), (u, c, ids))
u, c, ids = g.judge(d, {"FNVNX_PLAT_FIRST": 1, "FNVNX_PLAT_B": 2})
check("alias use counts as use of the number", (u, c) == (2, 2))
u, c, ids = g.judge(d, {"FNVNX_PLAT_A": 1})
check("unused service is red", (u, c) == (2, 1) and ids[0].startswith("unused service 2"), ids)
u, c, ids = g.judge(d, {"FNVNX_PLAT_A": 1, "FNVNX_PLAT_B": 2, "FNVNX_PLAT_C": 9})
check("undeclared use is red", u == 3 and c == 2 and "undeclared FNVNX_PLAT_C=9" in ids, (u, c, ids))
u, c, ids = g.judge(d, {"FNVNX_PLAT_A": 1, "FNVNX_PLAT_B": 3})
check("value mismatch is red", c < u, (u, c, ids))
check("includes_header", g.includes_header('# 1 "/x/fnvnx/hostapi.h" 1\n') and not g.includes_header('# 1 "/x/other.h"\n#define FNVNX_PLAT_A 1\n'))
check("used reads -dU defines", g.used(["#define FNVNX_PLAT_A 1u\n#define X 2\n"]) == {"FNVNX_PLAT_A": 1})

# universe 0 on a checkout without admitted roots: green, no exception list
out = tempfile.mkdtemp(prefix="gh-")
repo = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
env = dict(os.environ, GATE_OUT=out)
r = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_hostapi.py")], cwd=repo, env=env, capture_output=True, text=True)
res = json.load(open(os.path.join(out, "hostapi.json"))) if os.path.exists(os.path.join(out, "hostapi.json")) else {}
check("universe 0 green", r.returncode == 0 and res.get("status") == "green" and res.get("universe") == 0, (r.stdout, r.stderr, res))
r = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_hostapi.py"), "--header", "nope.h"], cwd=repo, env=env, capture_output=True, text=True)
check("universe 0 needs no header", r.returncode == 0, r.stdout + r.stderr)

print("FAILED: %d" % len(FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
