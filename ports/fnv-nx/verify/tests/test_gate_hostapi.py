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

# ---- the real compiler: what -dU writes, what the CC stage does with it (pinned cross g++, the only compiler of env.json)
CXX = "aarch64-linux-gnu-g++-13"


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


work = tempfile.mkdtemp(prefix="gh-cc-")
# (a) -dU output compiles in the CC stage (-fpreprocessed) to the same object bytes as the PP output without -dU
src = os.path.join(work, "u.cc")
open(src, "w").write('#define FNVNX_PLAT_A 1u\n#define UNUSED 7\n#define TWICE(x) ((x) * 2)\n'
                     'int f(int v) { return TWICE(v) + FNVNX_PLAT_A; }\n')
objs = {}
for tag, flags in (("dU", ["-E", "-dU"]), ("plain", ["-E"])):
    ii = os.path.join(work, tag + ".ii")
    r = run([CXX] + flags + ["-o", ii, src])
    check("PP %s runs" % tag, r.returncode == 0, r.stderr)
    o = os.path.join(work, tag + ".o")
    r = run([CXX, "-fpreprocessed", "-c", "-o", o, ii])
    check("CC of the %s unit.ii compiles (-fpreprocessed)" % tag, r.returncode == 0, r.stderr)
    objs[tag] = open(o, "rb").read() if os.path.exists(o) else None
check("-dU unit.ii and plain unit.ii give the same object bytes", objs["dU"] is not None and objs["dU"] == objs["plain"])
dii = open(os.path.join(work, "dU.ii")).read()
check("-dU lists the expanded FNVNX_PLAT_ macro and not the unexpanded one",
      g.used([dii]) == {"FNVNX_PLAT_A": 1} and "#define UNUSED" not in dii, dii)

# (i) a header whose static inline wrappers expand FNVNX_PLAT_ macros inside the header itself: the expansion happens when the header is
# preprocessed, so every includer of it "expands" that service whether or not it calls the wrapper. FACT (reported to the lead): for such
# a header the used-evidence is vacuous for those services; the gate counts them used by every includer.
hdr = os.path.join(work, "wrap", "hostapi.h")
os.makedirs(os.path.dirname(hdr))
open(hdr, "w").write("#define FNVNX_PLAT_A 1u\n#define FNVNX_PLAT_B 2u\n"
                     "static inline int wrap_b(void) { return FNVNX_PLAT_B; }\n")
unit = os.path.join(work, "inc.cc")
open(unit, "w").write('#include "wrap/hostapi.h"\nint never_calls_b(void) { return FNVNX_PLAT_A; }\n')
r = run([CXX, "-E", "-dU", "-o", os.path.join(work, "inc.ii"), unit])
iii = open(os.path.join(work, "inc.ii")).read()
dh = g.declared(open(hdr).read())
u, c, ids = g.judge(dh, g.used([iii]))
check("(i) header-internal wrapper: includes_header sees the header", g.includes_header(iii))
check("(i) header-internal wrapper: service B counts as used though the unit never calls wrap_b (rule is vacuous for it)",
      (u, c, ids) == (2, 2, []) and g.used([iii]) == {"FNVNX_PLAT_A": 1, "FNVNX_PLAT_B": 2}, (u, c, ids, g.used([iii])))
# the same unit through a header with no wrapper: B is unused and red
open(hdr, "w").write("#define FNVNX_PLAT_A 1u\n#define FNVNX_PLAT_B 2u\n")
r = run([CXX, "-E", "-dU", "-o", os.path.join(work, "inc2.ii"), unit])
u, c, ids = g.judge(dh, g.used([open(os.path.join(work, "inc2.ii")).read()]))
check("(i) control: without the wrapper B is unused and red", (u, c) == (2, 1) and ids[0].startswith("unused service 2"), (u, c, ids))

# (ii) range markers SOCK_FIRST / SOCK_LAST of the real header
real = None
for p in g.DEFAULT_HEADERS:
    q = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(VERIFY))), p)
    if os.path.isfile(q):
        real = open(q).read()
        break
check("(ii) the real header is found", real is not None)
if real is not None:
    dr = g.declared(real)
    byname = {n: v for v, ns in dr.items() for n in ns}
    check("(ii) SOCK_FIRST aliases SOCK_SOCKET and SOCK_LAST aliases SOCK_SETFL (no item of their own)",
          byname["FNVNX_PLAT_SOCK_FIRST"] == byname["FNVNX_PLAT_SOCK_SOCKET"] == 16
          and byname["FNVNX_PLAT_SOCK_LAST"] == byname["FNVNX_PLAT_SOCK_SETFL"] == 31
          and dr[16] == ["FNVNX_PLAT_SOCK_FIRST", "FNVNX_PLAT_SOCK_SOCKET"] and dr[31] == ["FNVNX_PLAT_SOCK_LAST", "FNVNX_PLAT_SOCK_SETFL"], (dr.get(16), dr.get(31)))
    nsvc = len(dr)
    u, c, ids = g.judge(dr, {"FNVNX_PLAT_SOCK_FIRST": 16, "FNVNX_PLAT_SOCK_LAST": 31})
    check("(ii) a range loop FIRST..LAST uses only the two end services: 16 and 31 certified, the 17..26 and 30 in between stay unused",
          u == nsvc and c == 2 and all("service %d " % n in " ".join(ids) for n in (17, 18, 26, 30)) and not any("service 16 " in i or "service 31 " in i for i in ids), (u, c, ids[:4]))
    u, c, ids = g.judge(dr, {"FNVNX_PLAT_SOCK_FIRST": 17})
    check("(ii) a marker with a changed value is an undeclared use (red)", "undeclared FNVNX_PLAT_SOCK_FIRST=17" in ids, ids)
# a marker whose number equals no service is a declared item of its own (nothing hides it)
dm = g.declared("#define FNVNX_PLAT_X 1u\n#define FNVNX_PLAT_X_LAST 5u\n")
u, c, ids = g.judge(dm, {"FNVNX_PLAT_X": 1})
check("(ii) a marker equal to no service is its own item and red while unused", (u, c) == (2, 1) and ids == ["unused service 5 (FNVNX_PLAT_X_LAST)"], (u, c, ids))

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
