#!/usr/bin/env python3
"""Tests for verify/gate_hostapi.py: declared = used per root, negatives, universe 0. No game data."""
import json
import os
import subprocess
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import gate_hostapi as g  # noqa: E402
import scratch  # noqa: E402

scratch.install()   # every mkdtemp is removed synchronously at exit

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

# (i) wrapper rule: a header whose function body expands a FNVNX_PLAT_ macro makes the -dU evidence vacuous (every includer "expands" it);
# the gate makes such a header red.
hdr = os.path.join(work, "wrap", "hostapi.h")
os.makedirs(os.path.dirname(hdr))
wrapped = "#define FNVNX_PLAT_A 1u\n#define FNVNX_PLAT_B 2u\nstatic inline int wrap_b(void) { return FNVNX_PLAT_B; }\n"
open(hdr, "w").write(wrapped)
check("(i) a static inline wrapper expanding a service macro is a wrapper use (red)", [n for _, n in g.wrapper_uses(wrapped)] == ["FNVNX_PLAT_B"], g.wrapper_uses(wrapped))
check("(i) a macro-defined function is a wrapper use", g.wrapper_uses("#define CALL() (FNVNX_PLAT_B)\n") != [])
clean = "/* FNVNX_PLAT_B in a comment */\n#define FNVNX_PLAT_A 1u // FNVNX_PLAT_B\n#define FNVNX_PLAT_B 2u\nconst char *s = \"FNVNX_PLAT_B\";\n"
check("(i) declarations, comments and strings are not wrapper uses", g.wrapper_uses(clean) == [], g.wrapper_uses(clean))
real = None
for p_ in g.DEFAULT_HEADERS:
    q = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(VERIFY))), p_)
    if os.path.isfile(q):
        real = open(q).read()
        break
check("(i) the real header has no wrapper use", real is not None and g.wrapper_uses(real) == [], g.wrapper_uses(real or ""))
# (i) class rule: a FNVNX_PLAT_ identifier only in hostapi.h's #define lines and in unit files of the root; a shim-style header is red
import subprocess as _sp
_repo = tempfile.mkdtemp(prefix="gh-repo-")
_r = "ports/fnv-nx/root"
for rel, txt in {_r + "/main.cc": "int f(){return FNVNX_PLAT_A;}\n", _r + "/inc/hostapi.h": "#define FNVNX_PLAT_A 1u\n",
                 _r + "/inc/shim.h": "static inline int w(void){return FNVNX_PLAT_A;}\n", _r + "/inc/ok.h": "int ok(void);\n"}.items():
    os.makedirs(os.path.dirname(os.path.join(_repo, rel)), exist_ok=True)
    open(os.path.join(_repo, rel), "w").write(txt)
_sp.run(["git", "init", "-q", _repo], check=True)
_sp.run(["git", "-C", _repo, "add", "-A"], check=True)
_rec = {"include": [_r + "/inc"]}
check("(i) a shim-style header with an inline wrapper is red; the unit's own use, hostapi.h's define and an unrelated header are not",
      g.foreign_uses(_repo, _rec, _r, "/nonexistent") == ["wrapper use in %s/inc/shim.h" % _r], g.foreign_uses(_repo, _rec, _r, "/nonexistent"))
open(os.path.join(_repo, _r, "inc/hostapi.h"), "w").write("#define FNVNX_PLAT_A 1u\nstatic inline int h(void){return FNVNX_PLAT_A;}\n")
_sp.run(["git", "-C", _repo, "add", "-A"], check=True)
check("(i) a body in hostapi.h itself is red", "wrapper use in %s/inc/hostapi.h" % _r in g.foreign_uses(_repo, _rec, _r, "/nonexistent"))
shutil.rmtree(_repo, ignore_errors=True)

# stale names: a deleted name in an admitted file is red; the journal (history) and the registry are exempt
ML, SF = "main" + "_loop", "SOCK" + "_FIRST"   # spelled in parts: this file is itself scanned
_repo = tempfile.mkdtemp(prefix="gh-stale-")
_t = "ports/fnv-nx"
for rel, txt in {_t + "/verify/admit.json": json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": _t + "/verify", "class": "tool", "gates": []}, {"path": _t + "/journal", "class": "doc", "gates": []}]}),
                 _t + "/verify/hostapi_holes.json": json.dumps({"names": [ML, SF]}), _t + "/verify/a.py": "x = 1  # h_" + ML + "\n",
                 _t + "/verify/b.py": SF + "X = 1\nmainloop = 2\n", _t + "/journal/h.md": ML + " was deleted\n"}.items():
    os.makedirs(os.path.dirname(os.path.join(_repo, rel)), exist_ok=True)
    open(os.path.join(_repo, rel), "w").write(txt)
subprocess.run(["git", "init", "-q", _repo], check=True)
subprocess.run(["git", "-C", _repo, "add", "-A"], check=True)
check("stale names: h_<name> in a tool file is red; <name>X, mainloop, the journal and the registry are not",
      g.stale_names(_repo) == [ML + " in %s/verify/a.py" % _t], g.stale_names(_repo))
shutil.rmtree(_repo, ignore_errors=True)

# (ii) no range marker aliases a service; the 13 socket services are declared (the platform's sock_guest.c calls each of them)
if real is not None:
    dr = g.declared(real)
    names = [n for ns in dr.values() for n in ns]
    check("(ii) no *_FIRST / *_LAST marker is declared", not any(n.endswith(("_FIRST", "_LAST")) for n in names), names)
    check("(ii) the socket services 16..26, 30, 31 are declared, one name each", all(len(dr[n]) == 1 for n in (*range(16, 27), 30, 31)), {n: dr.get(n) for n in range(16, 32)})
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
