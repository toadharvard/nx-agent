#!/usr/bin/env python3
"""Tests for verify/puregate.py (rule R12-T). Plain script: exit 0 on pass, one PASS/FAIL line per case."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from textwrap import dedent

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(os.path.dirname(HERE), "puregate.py")
spec = importlib.util.spec_from_file_location("puregate", GATE)
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)
FAILED = []


def V(src):
    return [(ln, rule) for ln, rule, _ in pg.check_source(dedent(src).lstrip("\n"), "t.py")]


def case(name, src, want):
    got = V(src)
    ok = got == sorted(want)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got=%r want=%r" % (got, sorted(want))))
    if not ok:
        FAILED.append(name)


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(info)))
    if not ok:
        FAILED.append(name)


def one(name, body, rule, pre=""):
    """Positive: `pre` + body gives exactly one violation of `rule` on the last line."""
    src = pre + body
    case(name, src, [(src.count("\n") + 1, rule)])


# ---- clock -----------------------------------------------------------------------------------------------------
one("clock time.time", "time.time()", "clock", "import time\n")
one("clock import-as", "t.monotonic()", "clock", "import time as t\n")
one("clock from-import", "time()", "clock", "from time import time\n")
one("clock from-import-as", "now()", "clock", "from time import time as now\n")
one("clock time.time_ns", "time.time_ns()", "clock", "import time\n")
case("clock time.sleep clean", "import time\ntime.sleep(1)\n", [])
case("clock from time import sleep clean", "from time import sleep\nsleep(1)\n", [])
one("clock datetime as dt", "dt.datetime.now()", "clock", "import datetime as dt\n")
one("clock from datetime import datetime", "datetime.now()", "clock", "from datetime import datetime\n")
one("clock datetime.today", "datetime.datetime.today()", "clock", "import datetime\n")
one("clock datetime.utcnow", "datetime.datetime.utcnow()", "clock", "import datetime\n")
one("clock date.today", "date.today()", "clock", "from datetime import date\n")
case("clock timedelta clean", "import datetime\nx = datetime.timedelta(1)\n", [])
one("clock os.path.getmtime", "os.path.getmtime(x)", "clock", "import os\n")
one("clock from os import path as p", "p.getmtime(x)", "clock", "from os import path as p\n")
one("clock import os.path", "os.path.getctime(x)", "clock", "import os.path\n")
one("clock import os.path as q", "q.getatime(x)", "clock", "import os.path as q\n")
one("clock posixpath", "pp.getmtime(x)", "clock", "import posixpath as pp\n")
one("clock from os.path import", "getmtime(x)", "clock", "from os.path import getmtime\n")
one("clock st_mtime attr", "x = f().stat().st_mtime", "clock")
for a in ("st_atime", "st_ctime", "st_mtime_ns", "st_atime_ns", "st_ctime_ns", "st_birthtime"):
    one("clock attr " + a, "x = y." + a, "clock")
case("clock st_size clean", "x = y.st_size\nst_mtime = 1\n", [])
one("clock stat.ST_MTIME", "x = s[stat.ST_MTIME]", "clock", "import stat\n")
one("clock utime no times", "os.utime(p)", "clock", "import os\n")
one("clock utime None", "os.utime(p, None)", "clock", "import os\n")
one("clock utime times=None", "os.utime(p, times=None)", "clock", "import os\n")
one("clock utime variable times", "os.utime(p, t)", "clock", "import os\n")
case("clock utime tuple clean", "import os\nos.utime(p, (1, 2))\n", [])
case("clock utime ns clean", "import os\nos.utime(p, ns=(1, 2))\n", [])
# ---- inode -----------------------------------------------------------------------------------------------------
for a in ("st_ino", "st_dev", "st_nlink"):
    one("inode attr " + a, "x = y." + a, "inode")
one("inode samefile", "os.path.samefile(a, b)", "inode", "import os\n")
case("inode negative", "import os\nos.path.exists(a)\n", [])
# ---- entropy ---------------------------------------------------------------------------------------------------
one("entropy random", "random.random()", "entropy", "import random\n")
one("entropy random from-import", "choice(x)", "entropy", "from random import choice\n")
one("entropy random Random class", "r = R()", "entropy", "from random import Random as R\n")
one("entropy secrets", "secrets.token_hex()", "entropy", "import secrets\n")
one("entropy uuid4", "uuid.uuid4()", "entropy", "import uuid\n")
one("entropy uuid1", "u1()", "entropy", "from uuid import uuid1 as u1\n")
case("entropy uuid5 clean", "import uuid\nuuid.uuid5(a, b)\n", [])
one("entropy os.urandom", "os.urandom(8)", "entropy", "import os\n")
one("entropy os.getrandom", "os.getrandom(8)", "entropy", "import os\n")
one("entropy tempfile", "tempfile.mkdtemp()", "entropy", "import tempfile\n")
one("entropy id()", "x = id(y)", "entropy")
one("entropy hash()", "x = hash(y)", "entropy")
case("entropy import alone clean", "import random\n", [])
case("entropy shadowed id/hash clean", "def f(id, hash):\n    return id(1) + hash(2)\n", [])
case("entropy module-level def id clean", "def id(x):\n    return x\ny = id(1)\n", [])
# ---- unsorted-listing ------------------------------------------------------------------------------------------
case("listing sorted clean", "import os\nx = sorted(os.listdir(d))\n", [])
case("listing sorted reverse clean", "import os\nx = sorted(os.listdir(d), reverse=True)\n", [])
case("listing from-import sorted clean", "from os import listdir\nx = sorted(listdir(d))\n", [])
one("listing for-loop", "for f in os.listdir(d): pass", "unsorted-listing", "import os\n")
one("listing list()", "x = list(os.listdir(d))", "unsorted-listing", "import os\n")
one("listing key= not first positional", "x = sorted(key=k, iterable=os.listdir(d))", "unsorted-listing", "import os\n")
one("listing scandir", "x = os.scandir(d)", "unsorted-listing", "import os\n")
one("listing glob.glob", "x = glob.glob(p)", "unsorted-listing", "import glob\n")
one("listing glob.iglob", "x = iglob(p)", "unsorted-listing", "from glob import iglob\n")
case("listing glob sorted clean", "import glob\nx = sorted(glob.glob(p))\n", [])
one("listing iterdir", "x = p.iterdir()", "unsorted-listing")
one("listing .glob method", "x = p.glob('*')", "unsorted-listing")
one("listing .rglob method", "x = p.rglob('*')", "unsorted-listing")
one("listing method alias", "f = p.glob", "unsorted-listing")
case("listing method sorted clean", "x = sorted(p.iterdir())\ny = sorted(p.rglob('*'))\n", [])
one("listing sorted shadowed", "x = sorted(os.listdir(d))", "unsorted-listing", "import os\nsorted = list\n")
WALK_OK = """
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    for f in sorted(files):
        use(f)
"""
case("walk accepted: sorted(files)", WALK_OK, [])
case("walk accepted: files.sort second", """
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    files.sort()
    for f in files:
        use(f)
""", [])
case("walk accepted: fwalk 4 targets", """
import os
for root, dirs, files, fd in os.fwalk(d):
    dirs.sort()
    for f in sorted(files):
        use(f)
""", [])
case("walk accepted: prune after sort", """
import os
for root, dirs, files in os.walk(d, topdown=True):
    dirs.sort()
    dirs[:] = [x for x in dirs if x != "a"]
    for f in sorted(files):
        use(f)
""", [])
case("walk rejected: no dirs.sort", """
import os
for root, dirs, files in os.walk(d):
    for f in sorted(files):
        use(f)
""", [(2, "unsorted-listing")])
case("walk rejected: dirs.sort not first", """
import os
for root, dirs, files in os.walk(d):
    use(root)
    dirs.sort()
    for f in sorted(files):
        use(f)
""", [(2, "unsorted-listing")])
case("walk rejected: files unsorted", """
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    for f in files:
        use(f)
""", [(2, "unsorted-listing")])
case("walk rejected: files.sort third", """
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    use(root)
    files.sort()
    for f in files:
        use(f)
""", [(2, "unsorted-listing")])
case("walk rejected: list(os.walk)", "import os\nx = list(os.walk(d))\n", [(2, "unsorted-listing")])
case("walk rejected: bottom-up", """
import os
for root, dirs, files in os.walk(d, topdown=False):
    dirs.sort()
    for f in sorted(files):
        use(f)
""", [(2, "unsorted-listing")])
case("walk rejected: dirs rebound", """
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    dirs = list(dirs)
    for f in sorted(files):
        use(f)
""", [(2, "unsorted-listing")])
# ---- unsorted-set ----------------------------------------------------------------------------------------------
one("set literal for", "for x in {1, 2}: pass", "unsorted-set")
one("set() call for", "for x in set(a): pass", "unsorted-set")
one("frozenset call for", "for x in frozenset(a): pass", "unsorted-set")
one("set literal in comprehension", "y = [x for x in {1, 2}]", "unsorted-set")
one("set comp iterated", "for x in {y for y in a}: pass", "unsorted-set")
case("set sorted clean", "for x in sorted({1, 2}):\n    pass\n", [])
case("list literal clean", "for x in [1, 2]:\n    pass\ny = {x for x in a}\n", [])
# ---- host ------------------------------------------------------------------------------------------------------
for fn, imp in (("os.cpu_count", "os"), ("multiprocessing.cpu_count", "multiprocessing"), ("os.sched_getaffinity", "os"),
                ("os.uname", "os"), ("os.getpid", "os"), ("os.getppid", "os"), ("os.getlogin", "os"),
                ("os.getuid", "os"), ("threading.get_ident", "threading"),
                ("threading.get_native_id", "threading"), ("resource.getrusage", "resource"), ("os.times", "os"),
                ("os.getloadavg", "os"), ("shutil.disk_usage", "shutil"), ("os.statvfs", "os")):
    one("host " + fn, "x = " + fn + "()", "host", "import " + imp + "\n")
for mod in ("platform", "socket", "getpass", "pwd", "grp"):
    one("host module " + mod, "x = " + mod + ".f()", "host", "import " + mod + "\n")
one("host module from-import", "x = gethostname()", "host", "from socket import gethostname\n")
one("host /proc/ path", 'f = open("/" + "x") or "/proc/self/maps"', "host")
one("host /sys/ path", 'f = "/sys/devices"', "host")
one("host /proc exact", 'f = "/proc"', "host")
one("host /sys exact", 'f = "/sys"', "host")
one("host path in f-string", 'f = f"/proc/{p}/stat"', "host")
case("host /process clean", 'f = "/process"\ng = "/system"\nh = "x/proc/y"\n', [])
case("host getcwd/environ clean", "import os\nx = os.getcwd()\ny = os.environ\n", [])
# ---- completion-order ------------------------------------------------------------------------------------------
one("completion as_completed", "x = concurrent.futures.as_completed(f)", "completion-order", "import concurrent.futures\n")
one("completion from-import", "x = as_completed(f)", "completion-order", "from concurrent.futures import as_completed\n")
one("completion wait", "x = futures.wait(f)", "completion-order", "from concurrent import futures\n")
one("completion imap_unordered", "x = pool.imap_unordered(f, a)", "completion-order")
one("completion queue.Queue", "q = queue.Queue()", "completion-order", "import queue\n")
one("completion SimpleQueue", "q = multiprocessing.SimpleQueue()", "completion-order", "import multiprocessing\n")
one("completion multiprocessing.Queue", "q = Queue()", "completion-order", "from multiprocessing import Queue\n")
case("completion executor map clean", "import concurrent.futures\nx = concurrent.futures.ThreadPoolExecutor(2).map(f, a)\n"
     "y = pool.imap(f, a)\n", [])
# ---- dynamic ---------------------------------------------------------------------------------------------------
for b in ("eval", "exec", "compile", "__import__"):
    one("dynamic " + b, "x = " + b + "(s)", "dynamic")
one("dynamic importlib", "importlib.import_module(n)", "dynamic", "import importlib\n")
one("dynamic getattr module non-const", "x = getattr(os, n)", "dynamic", "import os\n")
one("dynamic getattr module const", 'x = getattr(os, "path")', "dynamic", "import os\n")
one("dynamic getattr obj non-const", "x = getattr(obj, n)", "dynamic")
one("dynamic setattr non-const", "setattr(obj, n, 1)", "dynamic")
one("dynamic delattr non-const", "delattr(obj, n)", "dynamic")
one("dynamic getattr alias", "g = getattr", "dynamic")
one("dynamic getattr st_mtime const", 'x = getattr(obj, "st_mtime")', "clock")
case("dynamic getattr const clean", 'x = getattr(obj, "name")\nsetattr(obj, "a", 1)\n', [])
one("dynamic star import", "from os import *", "dynamic")
one("dynamic globals", "g = globals()", "dynamic")
one("dynamic locals", "g = locals()", "dynamic")
one("dynamic vars()", "g = vars()", "dynamic")
case("dynamic vars(x) clean", "g = vars(obj)\n", [])
one("dynamic alias module", "t = time", "dynamic", "import time\n")
one("dynamic alias via import-as", "x = o", "dynamic", "import os as o\n")
one("dynamic module as argument", "f(os)", "dynamic", "import os\n")
one("dynamic module in container", "x = [os.path]", "dynamic", "import os\n")
one("dynamic class alias", "D = datetime", "dynamic", "from datetime import datetime\n")
one("dynamic builtins module", "builtins.open(p)", "dynamic", "import builtins\n")
one("dynamic sys.modules", "m = sys.modules[n]", "dynamic", "import sys\n")
case("dynamic module attr base clean", "import os\nx = os.path.join(a, b)\ny = os.sep\n", [])
# ---- syntax ----------------------------------------------------------------------------------------------------
case("syntax error", "def (:\n", [(1, "syntax")])
# ---- scopes ----------------------------------------------------------------------------------------------------
case("scope param shadows", "import time\ndef f(time):\n    return time.time()\n", [])
case("scope param shadow is local", "import time\ndef f(time):\n    pass\ndef g():\n    return time.time()\n", [(5, "clock")])
case("scope local assignment shadows", "import random\ndef f():\n    random = Foo()\n    return random.random()\n", [])
case("scope for target shadows", "import time\ndef f(xs):\n    for time in xs:\n        pass\n    return time.x\n", [])
case("scope lambda param shadows", "import time\nf = lambda time: time.time()\n", [])
case("scope comprehension var shadows", "import time\ny = [time.x for time in a]\n", [])
case("scope import inside function", "def f():\n    import time\n    return time.time()\n", [(3, "clock")])
case("scope import inside function not leaked", "def f():\n    import time\ndef g():\n    return time.time()\n", [])
case("scope from-import in nested", "def f():\n    def g():\n        from os import path as p\n"
     "        return p.getmtime(x)\n    return g\n", [(4, "clock")])
case("scope module import seen in nested class method",
     "import time\nclass A:\n    time = 1\n    def f(self):\n        return time.time()\n", [(5, "clock")])
case("scope class-body name shadows only in class", "import time\nclass A:\n    time = 1\n    y = time\n", [])
case("scope same-scope import+assign is ambiguous", "import time\ntime = 3\n", [(2, "dynamic")])
case("scope import twice same target ok", "import time\nimport time\ntime.sleep(1)\n", [])
case("scope global rebind is ambiguous", "import time\ndef f():\n    global time\n    time = 1\n", [(4, "dynamic")])
case("scope relative import is local", "from . import time\nx = time.time()\n", [])
case("scope except-as shadows", "import time\ndef f():\n    try:\n        pass\n    except Exception as time:\n"
     "        return time.x\n", [])
case("scope walrus in comprehension binds outside", "import os\ndef f(a):\n    [(os := y) for y in a]\n    return os.getpid()\n", [])
case("scope nested function sees outer shadow", "import time\ndef f(time):\n    def g():\n        return time.time()\n"
     "    return g\n", [])
# ---- the gate over its own source ------------------------------------------------------------------------------
with open(GATE, encoding="utf-8") as fh:
    own = fh.read()
check("self: gate source passes the gate", pg.check_source(own, GATE) == [], pg.check_source(own, GATE)[:3])
check("self: at most 300 lines", own.count("\n") <= 300, own.count("\n"))
# ---- CLI -------------------------------------------------------------------------------------------------------
def run(*args, cwd=None):
    p = subprocess.run([sys.executable, "-I", GATE] + list(args), capture_output=True, text=True, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


tmp = tempfile.mkdtemp(prefix="puregate-")
def W(rel, text, mode=0o644):
    p = os.path.join(tmp, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    os.chmod(p, mode)
    return p


good = W("good/a.py", "import time\ntime.sleep(1)\n")
bad = W("bad/b.py", "import time\nx = time.time()\ny = id(x)\n")
W("bad/z.py", "def (:\n")
W("bad/run.sh", "#!/bin/sh\ndate\ndate\n")
W("bad/tool", "#!/usr/bin/env bash\ndate\n")
W("bad/pytool", "#!/usr/bin/env python3\nimport time\ntime.time()\n")
W("bad/notes.txt", "import time\ntime.time()\n")
W("bad/lib.bash", "echo hi\n")
rc, out, _ = run("check", good)
check("cli: clean exit 0", rc == 0 and "puregate: 1 files, 0 violations" in out, (rc, out))
rc, out, _ = run("check", bad)
check("cli: violations exit 2, line format", rc == 2 and ("%s:2: clock time.time" % bad) in out and
      ("%s:3: entropy id" % bad) in out, (rc, out))
rc, out, _ = run("check", os.path.join(tmp, "bad"), "--json", os.path.join(tmp, "o.json"))
lines = out.splitlines()
check("cli: dir walk sorted, shell + syntax reported, non-py ignored",
      rc == 2 and "run.sh:1: shell-unanalysable" in out and "bad/tool:1: shell-unanalysable" in out and
      "lib.bash:1: shell-unanalysable" in out and "z.py:1: syntax" in out and "pytool" not in out and
      "notes.txt" not in out and [l.split(":")[0] for l in lines[:-1]] == sorted(l.split(":")[0] for l in lines[:-1]),
      (rc, out))
check("cli: one shell violation per file", out.count("run.sh:") == 1, out)
check("cli: summary line", lines[-1].startswith("puregate: 5 files, 6 violations (") and
      "shell-unanalysable: 3" in lines[-1], lines[-1])
with open(os.path.join(tmp, "o.json")) as fh:
    js = json.load(fh)
with open(GATE, "rb") as fh:
    sha = hashlib.sha256(fh.read()).hexdigest()
check("cli: json schema, tool hash, counts", js["schema"] == "fnvnx-puregate/1" and js["tool"] == sha and
      js["counts_by_rule"]["shell-unanalysable"] == 3 and len(js["violations"]) == 6 and
      sum(js["counts_by_file"].values()) == 6 and len(js["files"]) == 5 and
      set(js["violations"][0]) == {"file", "line", "rule", "symbol"}, js.get("counts_by_rule"))
many = W("many/m.py", "import time\n" + "x = time.time()\n" * 250)
rc, out, _ = run("check", many, "--json", os.path.join(tmp, "m.json"))
with open(os.path.join(tmp, "m.json")) as fh:
    mj = json.load(fh)
check("cli: stdout capped at 200 violation lines, json has all",
      len(out.splitlines()) == 201 and len(mj["violations"]) == 250 and "250 violations" in out, len(out.splitlines()))
rc, _, _ = run("check")
check("cli: no path is usage error 1", rc == 1)
rc, _, _ = run("check", os.path.join(tmp, "nope"))
check("cli: missing path is usage error 1", rc == 1)
rc, _, _ = run("frob", good)
check("cli: unknown command is usage error 1", rc == 1)
rc, out, _ = run("check", os.path.join(tmp, "good"), os.path.join(tmp, "bad", "b.py"))
check("cli: several paths", rc == 2 and "puregate: 2 files" in out, out)

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
