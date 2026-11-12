#!/usr/bin/env python3
"""Tests for verify/puregate.py (rule R12-T). Plain script: exit 0 on pass, one PASS/FAIL line per case."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
tempfile.tempdir = tempfile.mkdtemp(prefix="scratch-")  # every scratch dir of this test lives here and is removed at exit
atexit.register(shutil.rmtree, tempfile.tempdir, True)
from textwrap import dedent

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(os.path.dirname(HERE), "puregate.py")
spec = importlib.util.spec_from_file_location("puregate", GATE)
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)
FAILED = testcase.FAILED


def norm(p):
    return os.path.normpath(os.path.abspath(p))


def PJ(*files, roots=()):
    """Project context: the checked files (relative to cwd) and the given root directories."""
    return frozenset(norm(f) for f in files), tuple(norm(r) for r in roots)


def V(src, proj=None, name="t.py"):
    try:
        args = () if proj is None else (proj,)
        return [(ln, rule) for ln, rule, _ in pg.check_source(dedent(src).lstrip("\n"), name, *args)]
    except Exception as e:  # a gate that cannot run the case fails it
        return [("ERR", repr(e)[:60])]


def U(src):
    """set_iterations_unproven of a source text."""
    try:
        return pg.scan(dedent(src).lstrip("\n"), "t.py")[1]
    except Exception as e:
        return repr(e)[:60]








# ---- clock -----------------------------------------------------------------------------------------------------
case("clock time.time", got=V("import time\n" + "time.time()", None), want=[(("import time\n" + "time.time()").count("\n") + 1, "clock")])
case("clock import-as", got=V("import time as t\n" + "t.monotonic()", None), want=[(("import time as t\n" + "t.monotonic()").count("\n") + 1, "clock")])
case("clock from-import", got=V("from time import time\n" + "time()", None), want=[(("from time import time\n" + "time()").count("\n") + 1, "clock")])
case("clock from-import-as", got=V("from time import time as now\n" + "now()", None), want=[(("from time import time as now\n" + "now()").count("\n") + 1, "clock")])
case("clock time.time_ns", got=V("import time\n" + "time.time_ns()", None), want=[(("import time\n" + "time.time_ns()").count("\n") + 1, "clock")])
case("clock time.sleep clean", got=V("import time\ntime.sleep(1)\n", None), want=sorted([]))
case("clock from time import sleep clean", got=V("from time import sleep\nsleep(1)\n", None), want=sorted([]))
case("clock datetime as dt", got=V("import datetime as dt\n" + "dt.datetime.now()", None), want=[(("import datetime as dt\n" + "dt.datetime.now()").count("\n") + 1, "clock")])
case("clock from datetime import datetime", got=V("from datetime import datetime\n" + "datetime.now()", None), want=[(("from datetime import datetime\n" + "datetime.now()").count("\n") + 1, "clock")])
case("clock datetime.today", got=V("import datetime\n" + "datetime.datetime.today()", None), want=[(("import datetime\n" + "datetime.datetime.today()").count("\n") + 1, "clock")])
case("clock datetime.utcnow", got=V("import datetime\n" + "datetime.datetime.utcnow()", None), want=[(("import datetime\n" + "datetime.datetime.utcnow()").count("\n") + 1, "clock")])
case("clock date.today", got=V("from datetime import date\n" + "date.today()", None), want=[(("from datetime import date\n" + "date.today()").count("\n") + 1, "clock")])
case("clock timedelta clean", got=V("import datetime\nx = datetime.timedelta(1)\n", None), want=sorted([]))
case("clock os.path.getmtime", got=V("import os\n" + "os.path.getmtime(x)", None), want=[(("import os\n" + "os.path.getmtime(x)").count("\n") + 1, "clock")])
case("clock from os import path as p", got=V("from os import path as p\n" + "p.getmtime(x)", None), want=[(("from os import path as p\n" + "p.getmtime(x)").count("\n") + 1, "clock")])
case("clock import os.path", got=V("import os.path\n" + "os.path.getctime(x)", None), want=[(("import os.path\n" + "os.path.getctime(x)").count("\n") + 1, "clock")])
case("clock import os.path as q", got=V("import os.path as q\n" + "q.getatime(x)", None), want=[(("import os.path as q\n" + "q.getatime(x)").count("\n") + 1, "clock")])
case("clock posixpath", got=V("import posixpath as pp\n" + "pp.getmtime(x)", None), want=[(("import posixpath as pp\n" + "pp.getmtime(x)").count("\n") + 1, "clock")])
case("clock from os.path import", got=V("from os.path import getmtime\n" + "getmtime(x)", None), want=[(("from os.path import getmtime\n" + "getmtime(x)").count("\n") + 1, "clock")])
case("clock st_mtime attr", got=V("" + "x = f().stat().st_mtime", None), want=[(("" + "x = f().stat().st_mtime").count("\n") + 1, "clock")])
for a in ("st_atime", "st_ctime", "st_mtime_ns", "st_atime_ns", "st_ctime_ns", "st_birthtime"):
    case("clock attr ", got=V("" + "x = y." + a, None), want=[(("" + "x = y." + a).count("\n") + 1, "clock")], key=a)
case("clock st_size clean", got=V("x = y.st_size\nst_mtime = 1\n", None), want=sorted([]))
case("clock stat.ST_MTIME", got=V("import stat\n" + "x = s[stat.ST_MTIME]", None), want=[(("import stat\n" + "x = s[stat.ST_MTIME]").count("\n") + 1, "clock")])
case("clock utime no times", got=V("import os\n" + "os.utime(p)", None), want=[(("import os\n" + "os.utime(p)").count("\n") + 1, "clock")])
case("clock utime None", got=V("import os\n" + "os.utime(p, None)", None), want=[(("import os\n" + "os.utime(p, None)").count("\n") + 1, "clock")])
case("clock utime times=None", got=V("import os\n" + "os.utime(p, times=None)", None), want=[(("import os\n" + "os.utime(p, times=None)").count("\n") + 1, "clock")])
case("clock utime variable times", got=V("import os\n" + "os.utime(p, t)", None), want=[(("import os\n" + "os.utime(p, t)").count("\n") + 1, "clock")])
case("clock utime tuple clean", got=V("import os\nos.utime(p, (1, 2))\n", None), want=sorted([]))
case("clock utime ns clean", got=V("import os\nos.utime(p, ns=(1, 2))\n", None), want=sorted([]))
# ---- inode -----------------------------------------------------------------------------------------------------
for a in ("st_ino", "st_dev", "st_nlink"):
    case("inode attr ", got=V("" + "x = y." + a, None), want=[(("" + "x = y." + a).count("\n") + 1, "inode")], key=a)
case("inode samefile", got=V("import os\n" + "os.path.samefile(a, b)", None), want=[(("import os\n" + "os.path.samefile(a, b)").count("\n") + 1, "inode")])
case("inode negative", got=V("import os\nos.path.exists(a)\n", None), want=sorted([]))
# ---- entropy ---------------------------------------------------------------------------------------------------
case("entropy random", got=V("import random\n" + "random.random()", None), want=[(("import random\n" + "random.random()").count("\n") + 1, "entropy")])
case("entropy random from-import", got=V("from random import choice\n" + "choice(x)", None), want=[(("from random import choice\n" + "choice(x)").count("\n") + 1, "entropy")])
case("entropy random Random class", got=V("from random import Random as R\n" + "r = R()", None), want=[(("from random import Random as R\n" + "r = R()").count("\n") + 1, "entropy")])
case("entropy secrets", got=V("import secrets\n" + "secrets.token_hex()", None), want=[(("import secrets\n" + "secrets.token_hex()").count("\n") + 1, "entropy")])
case("entropy uuid4", got=V("import uuid\n" + "uuid.uuid4()", None), want=[(("import uuid\n" + "uuid.uuid4()").count("\n") + 1, "entropy")])
case("entropy uuid1", got=V("from uuid import uuid1 as u1\n" + "u1()", None), want=[(("from uuid import uuid1 as u1\n" + "u1()").count("\n") + 1, "entropy")])
case("entropy uuid5 clean", got=V("import uuid\nuuid.uuid5(a, b)\n", None), want=sorted([]))
case("entropy os.urandom", got=V("import os\n" + "os.urandom(8)", None), want=[(("import os\n" + "os.urandom(8)").count("\n") + 1, "entropy")])
case("entropy os.getrandom", got=V("import os\n" + "os.getrandom(8)", None), want=[(("import os\n" + "os.getrandom(8)").count("\n") + 1, "entropy")])
case("entropy tempfile", got=V("import tempfile\n" + "tempfile.mkdtemp()", None), want=[(("import tempfile\n" + "tempfile.mkdtemp()").count("\n") + 1, "entropy")])
case("entropy id()", got=V("" + "x = id(y)", None), want=[(("" + "x = id(y)").count("\n") + 1, "entropy")])
case("entropy hash()", got=V("" + "x = hash(y)", None), want=[(("" + "x = hash(y)").count("\n") + 1, "entropy")])
case("entropy import alone clean", got=V("import random\n", None), want=sorted([]))
case("entropy shadowed id/hash clean", got=V("def f(id, hash):\n    return id(1) + hash(2)\n", None), want=sorted([]))
case("entropy module-level def id clean", got=V("def id(x):\n    return x\ny = id(1)\n", None), want=sorted([]))
# ---- unsorted-listing ------------------------------------------------------------------------------------------
case("listing sorted clean", got=V("import os\nx = sorted(os.listdir(d))\n", None), want=sorted([]))
case("listing sorted reverse clean", got=V("import os\nx = sorted(os.listdir(d), reverse=True)\n", None), want=sorted([]))
case("listing from-import sorted clean", got=V("from os import listdir\nx = sorted(listdir(d))\n", None), want=sorted([]))
case("listing for-loop", got=V("import os\n" + "for f in os.listdir(d): pass", None), want=[(("import os\n" + "for f in os.listdir(d): pass").count("\n") + 1, "unsorted-listing")])
case("listing list()", got=V("import os\n" + "x = list(os.listdir(d))", None), want=[(("import os\n" + "x = list(os.listdir(d))").count("\n") + 1, "unsorted-listing")])
case("listing key= not first positional", got=V("import os\n" + "x = sorted(key=k, iterable=os.listdir(d))", None), want=[(("import os\n" + "x = sorted(key=k, iterable=os.listdir(d))").count("\n") + 1, "unsorted-listing")])
case("listing scandir", got=V("import os\n" + "x = os.scandir(d)", None), want=[(("import os\n" + "x = os.scandir(d)").count("\n") + 1, "unsorted-listing")])
case("listing glob.glob", got=V("import glob\n" + "x = glob.glob(p)", None), want=[(("import glob\n" + "x = glob.glob(p)").count("\n") + 1, "unsorted-listing")])
case("listing glob.iglob", got=V("from glob import iglob\n" + "x = iglob(p)", None), want=[(("from glob import iglob\n" + "x = iglob(p)").count("\n") + 1, "unsorted-listing")])
case("listing glob sorted clean", got=V("import glob\nx = sorted(glob.glob(p))\n", None), want=sorted([]))
case("listing iterdir", got=V("" + "x = p.iterdir()", None), want=[(("" + "x = p.iterdir()").count("\n") + 1, "unsorted-listing")])
case("listing .glob method", got=V("" + "x = p.glob('*')", None), want=[(("" + "x = p.glob('*')").count("\n") + 1, "unsorted-listing")])
case("listing .rglob method", got=V("" + "x = p.rglob('*')", None), want=[(("" + "x = p.rglob('*')").count("\n") + 1, "unsorted-listing")])
case("listing method alias", got=V("" + "f = p.glob", None), want=[(("" + "f = p.glob").count("\n") + 1, "unsorted-listing")])
case("listing method sorted clean", got=V("x = sorted(p.iterdir())\ny = sorted(p.rglob('*'))\n", None), want=sorted([]))
case("listing sorted shadowed", got=V("import os\nsorted = list\n" + "x = sorted(os.listdir(d))", None), want=[(("import os\nsorted = list\n" + "x = sorted(os.listdir(d))").count("\n") + 1, "unsorted-listing")])
WALK_OK = """
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    for f in sorted(files):
        use(f)
"""
case("walk accepted: sorted(files)", got=V(WALK_OK, None), want=sorted([]))
case("walk accepted: files.sort second", got=V("""
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    files.sort()
    for f in files:
        use(f)
""", None), want=sorted([]))
case("walk accepted: fwalk 4 targets", got=V("""
import os
for root, dirs, files, fd in os.fwalk(d):
    dirs.sort()
    for f in sorted(files):
        use(f)
""", None), want=sorted([]))
case("walk accepted: prune after sort", got=V("""
import os
for root, dirs, files in os.walk(d, topdown=True):
    dirs.sort()
    dirs[:] = [x for x in dirs if x != "a"]
    for f in sorted(files):
        use(f)
""", None), want=sorted([]))
case("walk rejected: no dirs.sort", got=V("""
import os
for root, dirs, files in os.walk(d):
    for f in sorted(files):
        use(f)
""", None), want=sorted([(2, "unsorted-listing")]))
case("walk rejected: dirs.sort not first", got=V("""
import os
for root, dirs, files in os.walk(d):
    use(root)
    dirs.sort()
    for f in sorted(files):
        use(f)
""", None), want=sorted([(2, "unsorted-listing")]))
case("walk rejected: files unsorted", got=V("""
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    for f in files:
        use(f)
""", None), want=sorted([(2, "unsorted-listing")]))
case("walk rejected: files.sort third", got=V("""
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    use(root)
    files.sort()
    for f in files:
        use(f)
""", None), want=sorted([(2, "unsorted-listing")]))
case("walk rejected: list(os.walk)", got=V("import os\nx = list(os.walk(d))\n", None), want=sorted([(2, "unsorted-listing")]))
case("walk rejected: bottom-up", got=V("""
import os
for root, dirs, files in os.walk(d, topdown=False):
    dirs.sort()
    for f in sorted(files):
        use(f)
""", None), want=sorted([(2, "unsorted-listing")]))
case("walk rejected: dirs rebound", got=V("""
import os
for root, dirs, files in os.walk(d):
    dirs.sort()
    dirs = list(dirs)
    for f in sorted(files):
        use(f)
""", None), want=sorted([(2, "unsorted-listing")]))
# ---- unsorted-set ----------------------------------------------------------------------------------------------
case("set literal for", got=V("" + "for x in {1, 2}: pass", None), want=[(("" + "for x in {1, 2}: pass").count("\n") + 1, "unsorted-set")])
case("set() call for", got=V("" + "for x in set(a): pass", None), want=[(("" + "for x in set(a): pass").count("\n") + 1, "unsorted-set")])
case("frozenset call for", got=V("" + "for x in frozenset(a): pass", None), want=[(("" + "for x in frozenset(a): pass").count("\n") + 1, "unsorted-set")])
case("set literal in comprehension", got=V("" + "y = [x for x in {1, 2}]", None), want=[(("" + "y = [x for x in {1, 2}]").count("\n") + 1, "unsorted-set")])
case("set comp iterated", got=V("" + "for x in {y for y in a}: pass", None), want=[(("" + "for x in {y for y in a}: pass").count("\n") + 1, "unsorted-set")])
case("set sorted clean", got=V("for x in sorted({1, 2}):\n    pass\n", None), want=sorted([]))
case("list literal clean", got=V("for x in [1, 2]:\n    pass\ny = {x for x in a}\n", None), want=sorted([]))
# ---- host ------------------------------------------------------------------------------------------------------
for fn, imp in (("os.cpu_count", "os"), ("multiprocessing.cpu_count", "multiprocessing"), ("os.sched_getaffinity", "os"),
                ("os.uname", "os"), ("os.getpid", "os"), ("os.getppid", "os"), ("os.getlogin", "os"),
                ("os.getuid", "os"), ("threading.get_ident", "threading"),
                ("threading.get_native_id", "threading"), ("resource.getrusage", "resource"), ("os.times", "os"),
                ("os.getloadavg", "os"), ("shutil.disk_usage", "shutil"), ("os.statvfs", "os")):
    case("host ", got=V("import " + imp + "\n" + "x = " + fn + "()", None), want=[(("import " + imp + "\n" + "x = " + fn + "()").count("\n") + 1, "host")], key=fn)
for mod in ("platform", "socket", "getpass", "pwd", "grp"):
    case("host module ", got=V("import " + mod + "\n" + "x = " + mod + ".f()", None), want=[(("import " + mod + "\n" + "x = " + mod + ".f()").count("\n") + 1, "host")], key=mod)
case("host module from-import", got=V("from socket import gethostname\n" + "x = gethostname()", None), want=[(("from socket import gethostname\n" + "x = gethostname()").count("\n") + 1, "host")])
case("host /proc/ path", got=V("" + 'f = open("/" + "x") or "/proc/self/maps"', None), want=[(("" + 'f = open("/" + "x") or "/proc/self/maps"').count("\n") + 1, "host")])
case("host /sys/ path", got=V("" + 'f = "/sys/devices"', None), want=[(("" + 'f = "/sys/devices"').count("\n") + 1, "host")])
case("host /proc exact", got=V("" + 'f = "/proc"', None), want=[(("" + 'f = "/proc"').count("\n") + 1, "host")])
case("host /sys exact", got=V("" + 'f = "/sys"', None), want=[(("" + 'f = "/sys"').count("\n") + 1, "host")])
case("host path in f-string", got=V("" + 'f = f"/proc/{p}/stat"', None), want=[(("" + 'f = f"/proc/{p}/stat"').count("\n") + 1, "host")])
case("host /process clean", got=V('f = "/process"\ng = "/system"\nh = "x/proc/y"\n', None), want=sorted([]))
case("host getcwd/environ clean", got=V("import os\nx = os.getcwd()\ny = os.environ\n", None), want=sorted([]))
# ---- completion-order ------------------------------------------------------------------------------------------
case("completion as_completed", got=V("import concurrent.futures\n" + "x = concurrent.futures.as_completed(f)", None), want=[(("import concurrent.futures\n" + "x = concurrent.futures.as_completed(f)").count("\n") + 1, "completion-order")])
case("completion from-import", got=V("from concurrent.futures import as_completed\n" + "x = as_completed(f)", None), want=[(("from concurrent.futures import as_completed\n" + "x = as_completed(f)").count("\n") + 1, "completion-order")])
case("completion wait", got=V("from concurrent import futures\n" + "x = futures.wait(f)", None), want=[(("from concurrent import futures\n" + "x = futures.wait(f)").count("\n") + 1, "completion-order")])
case("completion imap_unordered", got=V("" + "x = pool.imap_unordered(f, a)", None), want=[(("" + "x = pool.imap_unordered(f, a)").count("\n") + 1, "completion-order")])
case("completion queue.Queue", got=V("import queue\n" + "q = queue.Queue()", None), want=[(("import queue\n" + "q = queue.Queue()").count("\n") + 1, "completion-order")])
case("completion SimpleQueue", got=V("import multiprocessing\n" + "q = multiprocessing.SimpleQueue()", None), want=[(("import multiprocessing\n" + "q = multiprocessing.SimpleQueue()").count("\n") + 1, "completion-order")])
case("completion multiprocessing.Queue", got=V("from multiprocessing import Queue\n" + "q = Queue()", None), want=[(("from multiprocessing import Queue\n" + "q = Queue()").count("\n") + 1, "completion-order")])
case("completion executor map clean", got=V("import concurrent.futures\nx = concurrent.futures.ThreadPoolExecutor(2).map(f, a)\n"
     "y = pool.imap(f, a)\n", None), want=sorted([]))
# ---- dynamic ---------------------------------------------------------------------------------------------------
for b in ("eval", "exec", "compile", "__import__"):
    case("dynamic ", got=V("" + "x = " + b + "(s)", None), want=[(("" + "x = " + b + "(s)").count("\n") + 1, "dynamic")], key=b)
case("dynamic importlib", got=V("import importlib\n" + "importlib.import_module(n)", None), want=[(("import importlib\n" + "importlib.import_module(n)").count("\n") + 1, "dynamic")])
case("dynamic getattr module non-const", got=V("import os\n" + "x = getattr(os, n)", None), want=[(("import os\n" + "x = getattr(os, n)").count("\n") + 1, "dynamic")])
case("dynamic getattr module const", got=V("import os\n" + 'x = getattr(os, "path")', None), want=[(("import os\n" + 'x = getattr(os, "path")').count("\n") + 1, "dynamic")])
case("dynamic getattr obj non-const", got=V("" + "x = getattr(obj, n)", None), want=[(("" + "x = getattr(obj, n)").count("\n") + 1, "dynamic")])
case("dynamic setattr non-const", got=V("" + "setattr(obj, n, 1)", None), want=[(("" + "setattr(obj, n, 1)").count("\n") + 1, "dynamic")])
case("dynamic delattr non-const", got=V("" + "delattr(obj, n)", None), want=[(("" + "delattr(obj, n)").count("\n") + 1, "dynamic")])
case("dynamic getattr alias", got=V("" + "g = getattr", None), want=[(("" + "g = getattr").count("\n") + 1, "dynamic")])
case("dynamic getattr st_mtime const", got=V("" + 'x = getattr(obj, "st_mtime")', None), want=[(("" + 'x = getattr(obj, "st_mtime")').count("\n") + 1, "clock")])
case("dynamic getattr const clean", got=V('x = getattr(obj, "name")\nsetattr(obj, "a", 1)\n', None), want=sorted([]))
case("dynamic star import", got=V("" + "from os import *", None), want=[(("" + "from os import *").count("\n") + 1, "dynamic")])
case("dynamic globals", got=V("" + "g = globals()", None), want=[(("" + "g = globals()").count("\n") + 1, "dynamic")])
case("dynamic locals", got=V("" + "g = locals()", None), want=[(("" + "g = locals()").count("\n") + 1, "dynamic")])
case("dynamic vars()", got=V("" + "g = vars()", None), want=[(("" + "g = vars()").count("\n") + 1, "dynamic")])
case("dynamic vars(x)", got=V("" + "g = vars(obj)", None), want=[(("" + "g = vars(obj)").count("\n") + 1, "dynamic")])
case("dynamic alias module", got=V("import time\n" + "t = time", None), want=[(("import time\n" + "t = time").count("\n") + 1, "dynamic")])
case("dynamic alias via import-as", got=V("import os as o\n" + "x = o", None), want=[(("import os as o\n" + "x = o").count("\n") + 1, "dynamic")])
case("dynamic module as argument", got=V("import os\n" + "f(os)", None), want=[(("import os\n" + "f(os)").count("\n") + 1, "dynamic")])
case("dynamic module in container", got=V("import os\n" + "x = [os.path]", None), want=[(("import os\n" + "x = [os.path]").count("\n") + 1, "dynamic")])
case("dynamic class alias", got=V("from datetime import datetime\n" + "D = datetime", None), want=[(("from datetime import datetime\n" + "D = datetime").count("\n") + 1, "dynamic")])
case("dynamic builtins module", got=V("import builtins\n" + "builtins.open(p)", None), want=[(("import builtins\n" + "builtins.open(p)").count("\n") + 1, "dynamic")])
case("dynamic sys.modules", got=V("import sys\n" + "m = sys.modules[n]", None), want=[(("import sys\n" + "m = sys.modules[n]").count("\n") + 1, "dynamic")])
case("dynamic module attr base clean", got=V("import os\nx = os.path.join(a, b)\ny = os.sep\n", None), want=sorted([]))
# ---- syntax ----------------------------------------------------------------------------------------------------
case("syntax error", got=V("def (:\n", None), want=sorted([(1, "syntax")]))
# ---- scopes ----------------------------------------------------------------------------------------------------
case("scope param shadows", got=V("import time\ndef f(time):\n    return time.time()\n", None), want=sorted([]))
case("scope param shadow is local", got=V("import time\ndef f(time):\n    pass\ndef g():\n    return time.time()\n", None), want=sorted([(5, "clock")]))
case("scope local assignment shadows", got=V("import random\ndef f():\n    random = Foo()\n    return random.random()\n", None), want=sorted([]))
case("scope for target shadows", got=V("import time\ndef f(xs):\n    for time in xs:\n        pass\n    return time.x\n", None), want=sorted([]))
case("scope lambda param shadows", got=V("import time\nf = lambda time: time.time()\n", None), want=sorted([]))
case("scope comprehension var shadows", got=V("import time\ny = [time.x for time in a]\n", None), want=sorted([]))
case("scope import inside function", got=V("def f():\n    import time\n    return time.time()\n", None), want=sorted([(3, "clock")]))
case("scope import inside function not leaked", got=V("def f():\n    import time\ndef g():\n    return time.time()\n", None), want=sorted([]))
case("scope from-import in nested", got=V("def f():\n    def g():\n        from os import path as p\n"
     "        return p.getmtime(x)\n    return g\n", None), want=sorted([(4, "clock")]))
case("scope module import seen in nested class method", got=V("import time\nclass A:\n    time = 1\n    def f(self):\n        return time.time()\n", None), want=sorted([(5, "clock")]))
case("scope class-body name shadows only in class", got=V("import time\nclass A:\n    time = 1\n    y = time\n", None), want=sorted([]))
case("scope same-scope import+assign is ambiguous", got=V("import time\ntime = 3\n", None), want=sorted([(2, "dynamic")]))
case("scope import twice same target ok", got=V("import time\nimport time\ntime.sleep(1)\n", None), want=sorted([]))
case("scope global rebind is ambiguous", got=V("import time\ndef f():\n    global time\n    time = 1\n", None), want=sorted([(4, "dynamic")]))
case("scope relative import is local", got=V("from . import tm\nx = tm.time()\n", PJ("tm.py")), want=sorted([]))
case("scope except-as shadows", got=V("import time\ndef f():\n    try:\n        pass\n    except Exception as time:\n"
     "        return time.x\n", None), want=sorted([]))
case("scope walrus in comprehension binds outside", got=V("import os\ndef f(a):\n    [(os := y) for y in a]\n    return os.getpid()\n", None), want=sorted([]))
case("scope nested function sees outer shadow", got=V("import time\ndef f(time):\n    def g():\n        return time.time()\n"
     "    return g\n", None), want=sorted([]))
# ---- import allow-list -----------------------------------------------------------------------------------------
case("import unlisted module", got=V("" + "import foo_bar", None), want=[(("" + "import foo_bar").count("\n") + 1, "import-unlisted")])
case("import unlisted from-import", got=V("" + "from foo_bar import x", None), want=[(("" + "from foo_bar import x").count("\n") + 1, "import-unlisted")])
case("import unlisted dotted", got=V("" + "import foo_bar.baz", None), want=[(("" + "import foo_bar.baz").count("\n") + 1, "import-unlisted")])
case("import unlisted stdlib: operator", got=V("" + "import operator", None), want=[(("" + "import operator").count("\n") + 1, "import-unlisted")])
case("import unlisted stdlib: operator.attrgetter", got=V("" + "from operator import attrgetter", None), want=[(("" + "from operator import attrgetter").count("\n") + 1, "import-unlisted")])
case("import unlisted stdlib: logging", got=V("" + "import logging", None), want=[(("" + "import logging").count("\n") + 1, "import-unlisted")])
case("import unlisted stdlib: ctypes", got=V("" + "import ctypes", None), want=[(("" + "import ctypes").count("\n") + 1, "import-unlisted")])
case("import unlisted inside function", got=V("" + "def f():\n    import foo_bar", None), want=[(("" + "def f():\n    import foo_bar").count("\n") + 1, "import-unlisted")])
case("import star of unlisted also dynamic", got=V("from foo_bar import *\n", None), want=sorted([(1, "dynamic"), (1, "import-unlisted")]))
for m in "ast json hashlib re struct collections itertools functools dataclasses typing enum abc math bisect heapq array " \
         "binascii base64 zlib io string textwrap shlex fnmatch posixpath copy contextlib types argparse errno stat csv " \
         "difflib bz2 lzma codecs unicodedata decimal fractions traceback warnings keyword tokenize token".split():
    case("import pure ", got=V("import %s\n" % m, None), want=sorted([]), key=m)
case("import pure submodule", got=V("import collections.abc\nimport json.decoder\nfrom collections import abc\n", None), want=sorted([]))
for m in "os os.path sys time datetime glob shutil subprocess pathlib concurrent.futures multiprocessing threading " \
         "gzip zipfile tarfile".split():
    case("import restricted ", got=V("import %s\n" % m, None), want=sorted([]), key=m)
case("import restricted: from concurrent import futures", got=V("from concurrent import futures\n", None), want=sorted([]))
for m in ("capstone", "elftools", "unicorn", "mako", "z3"):
    case("import external ", got=V("import %s\nfrom %s.x import y\n" % (m, m), None), want=sorted([]), key=m)
case("external modules reported", V("import capstone\n") == [] and not isinstance(U("import capstone\n"), str) and
      pg.scan("import capstone\nimport mako.x\nimport json\n", "t.py")[2] == ["capstone", "mako"], "")
case("import project: from-import sibling", got=V("from helper import f\n", PJ("helper.py")), want=sorted([]))
case("import project: import sibling", got=V("import helper\nx = helper.f()\n", PJ("helper.py")), want=sorted([]))
case("import project: package", got=V("import pkg.mod\n", PJ("pkg/__init__.py", "pkg/mod.py")), want=sorted([]))
case("import project: from package import submodule", got=V("from pkg import mod\n", PJ("pkg/__init__.py", "pkg/mod.py")), want=sorted([]))
case("import project: from package import attribute", got=V("from pkg import thing\n", PJ("pkg/__init__.py")), want=sorted([]))
case("import project: relative", got=V("from . import tm\nfrom .tm import f\n", PJ("tm.py")), want=sorted([]))
case("import project: relative up", got=V("from .. import tm\n", PJ("../tm.py")), want=sorted([]))
case("import project: found via given root", got=V("import helper\n", PJ("lib/helper.py", roots=("lib",))), want=sorted([]))
case("import project: not in the checked set", got=V("" + "import helper", None), want=[(("" + "import helper").count("\n") + 1, "import-unlisted")])
case("import project: other name unresolved", got=V("import other\n", PJ("helper.py")), want=sorted([(1, "import-unlisted")]))
case("import project: package module missing", got=V("import pkg.nope\n", PJ("pkg/__init__.py")), want=sorted([(1, "import-unlisted")]))
case("import project: namespace dir has no attributes", got=V("from ns import thing\n", PJ("ns/a.py")), want=sorted([(1, "import-unlisted")]))
case("import project: relative unresolved", got=V("from . import tm\n", None), want=sorted([(1, "import-unlisted")]))
case("import project: shadowing a listed name is ambiguous", got=V("import json\n", PJ("json.py")), want=sorted([(1, "import-unlisted")]))
case("import project: names stay unruled", got=V("from . import tm\nx = tm.time()\n", PJ("tm.py")), want=sorted([]))
case("import banned module alone is clean", got=V("import socket\nimport platform\n", None), want=sorted([]))
# ---- restricted members ----------------------------------------------------------------------------------------
case("os.nice is member-unlisted", got=V("import os\n" + "os.nice(1)", None), want=[(("import os\n" + "os.nice(1)").count("\n") + 1, "member-unlisted")])
case("os.getloadavg keeps host", got=V("import os\n" + "os.getloadavg()", None), want=[(("import os\n" + "os.getloadavg()").count("\n") + 1, "host")])
case("os from-import member-unlisted", got=V("from os import nice\n" + "x = nice", None), want=[(("from os import nice\n" + "x = nice").count("\n") + 1, "member-unlisted")])
case("os.system unlisted", got=V("import os\n" + "os.system(c)", None), want=[(("import os\n" + "os.system(c)").count("\n") + 1, "member-unlisted")])
case("os allowed members", got=V("import os\nos.makedirs(d)\nos.remove(p)\nos.rename(a, b)\nos.fsync(fd)\nos.chmod(p, 0o644)\n"
     "x = (os.sep, os.linesep, os.environ, os.getcwd(), os.devnull, os.O_RDONLY, os.EX_OK, os.SEEK_SET, os.name)\n"
     "os.fspath(p)\nos.access(p, os.R_OK)\nos.isatty(1)\nos.pipe()\nos._exit(1)\n", None), want=sorted([]))
case("os.path members clean", got=V("import os\nx = os.path.join(a, b) + os.path.realpath(c) + os.path.abspath(d)\n"
     "y = os.path.expanduser(e)\nos.path.exists(p)\nos.path.getsize(p)\n", None), want=sorted([]))
case("sys.getrefcount unlisted", got=V("import sys\n" + "sys.getrefcount(x)", None), want=[(("import sys\n" + "sys.getrefcount(x)").count("\n") + 1, "member-unlisted")])
case("sys._getframe unlisted", got=V("import sys\n" + "sys._getframe(0)", None), want=[(("import sys\n" + "sys._getframe(0)").count("\n") + 1, "member-unlisted")])
case("sys.getsizeof unlisted", got=V("import sys\n" + "sys.getsizeof(x)", None), want=[(("import sys\n" + "sys.getsizeof(x)").count("\n") + 1, "member-unlisted")])
case("sys.settrace unlisted", got=V("import sys\n" + "sys.settrace(f)", None), want=[(("import sys\n" + "sys.settrace(f)").count("\n") + 1, "member-unlisted")])
case("sys allowed members", got=V("import sys\nsys.stdout.write(sys.argv[0])\nsys.stderr.buffer.write(b'')\nsys.path.insert(0, p)\n"
     "x = (sys.version_info, sys.maxsize, sys.byteorder, sys.executable, sys.platform, sys.flags.hash_randomization)\n"
     "sys.exit(2)\n", None), want=sorted([]))
case("sys.flags other attribute unlisted", got=V("import sys\n" + "x = sys.flags.debug", None), want=[(("import sys\n" + "x = sys.flags.debug").count("\n") + 1, "member-unlisted")])
case("time.monotonic keeps clock", got=V("import time\n" + "time.monotonic()", None), want=[(("import time\n" + "time.monotonic()").count("\n") + 1, "clock")])
case("datetime constructors clean", got=V("import datetime\nx = datetime.datetime(2020, 1, 1).isoformat()\n"
     "y = datetime.datetime.strptime(s, f) + datetime.timedelta(1)\nz = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)\n", None), want=sorted([]))
case("datetime instance .today()", got=V("" + "x = d.today()", None), want=[(("" + "x = d.today()").count("\n") + 1, "clock")])
case("datetime instance .now()", got=V("" + "x = d.now()", None), want=[(("" + "x = d.now()").count("\n") + 1, "clock")])
case("datetime instance .utcnow()", got=V("" + "x = d.utcnow()", None), want=[(("" + "x = d.utcnow()").count("\n") + 1, "clock")])
case("glob members", got=V("import glob\nx = glob.escape(p)\ny = glob.has_magic(p)\nz = sorted(glob.glob(p))\n", None), want=sorted([]))
case("glob unlisted member", got=V("import glob\n" + "glob.glob1(d, p)", None), want=[(("import glob\n" + "glob.glob1(d, p)").count("\n") + 1, "member-unlisted")])
case("shutil allowed members", got=V("import shutil\nshutil.copyfile(a, b)\nshutil.copyfileobj(f, g)\nshutil.copymode(a, b)\n"
     "shutil.rmtree(d)\nshutil.move(a, b)\nx = shutil.which('cc')\n", None), want=sorted([]))
case("shutil.copy2 is clock", got=V("import shutil\n" + "shutil.copy2(a, b)", None), want=[(("import shutil\n" + "shutil.copy2(a, b)").count("\n") + 1, "clock")])
case("shutil.copystat is clock", got=V("import shutil\n" + "shutil.copystat(a, b)", None), want=[(("import shutil\n" + "shutil.copystat(a, b)").count("\n") + 1, "clock")])
case("shutil.copytree is clock", got=V("import shutil\n" + "shutil.copytree(a, b)", None), want=[(("import shutil\n" + "shutil.copytree(a, b)").count("\n") + 1, "clock")])
case("shutil.make_archive is clock", got=V("import shutil\n" + "shutil.make_archive(a, 'zip')", None), want=[(("import shutil\n" + "shutil.make_archive(a, 'zip')").count("\n") + 1, "clock")])
case("shutil.get_terminal_size is host", got=V("import shutil\n" + "shutil.get_terminal_size()", None), want=[(("import shutil\n" + "shutil.get_terminal_size()").count("\n") + 1, "host")])
case("shutil.copy unlisted", got=V("import shutil\n" + "shutil.copy(a, b)", None), want=[(("import shutil\n" + "shutil.copy(a, b)").count("\n") + 1, "member-unlisted")])
case("subprocess all members", got=V("import subprocess\nsubprocess.run(c)\nsubprocess.check_output(c)\nsubprocess.Popen(c)\n"
     "subprocess.PIPE\n", None), want=sorted([]))
case("pathlib classes", got=V("import pathlib\nfrom pathlib import Path\nx = pathlib.PurePath(a) / Path(b).name\n", None), want=sorted([]))
case("pathlib unlisted member", got=V("import pathlib\n" + "pathlib.WindowsPath(a)", None), want=[(("import pathlib\n" + "pathlib.WindowsPath(a)").count("\n") + 1, "member-unlisted")])
case("concurrent.futures executors", got=V("import concurrent.futures as cf\nwith cf.ThreadPoolExecutor(2) as e:\n    list(e.map(f, a))\n"
     "g = cf.ProcessPoolExecutor\nh = cf.Future()\n", None), want=sorted([]))
case("concurrent.futures unlisted", got=V("import concurrent.futures as cf\n" + "cf.ALL_COMPLETED", None), want=[(("import concurrent.futures as cf\n" + "cf.ALL_COMPLETED").count("\n") + 1, "member-unlisted")])
case("multiprocessing Pool", got=V("import multiprocessing\np = multiprocessing.Pool(2)\nx = p.map(f, a)\ny = p.starmap(f, a)\nz = p.imap(f, a)\n", None), want=sorted([]))
case("multiprocessing unlisted", got=V("import multiprocessing\n" + "multiprocessing.Process(target=f)", None), want=[(("import multiprocessing\n" + "multiprocessing.Process(target=f)").count("\n") + 1, "member-unlisted")])
case("threading members", got=V("import threading\nt = threading.Thread(target=f)\nl = threading.Lock()\nr = threading.RLock()\n"
     "e = threading.Event()\nx = threading.local()\n", None), want=sorted([]))
case("threading.active_count unlisted", got=V("import threading\n" + "threading.active_count()", None), want=[(("import threading\n" + "threading.active_count()").count("\n") + 1, "member-unlisted")])
case("threading.enumerate unlisted", got=V("import threading\n" + "threading.enumerate()", None), want=[(("import threading\n" + "threading.enumerate()").count("\n") + 1, "member-unlisted")])
case("queue.LifoQueue unlisted", got=V("import queue\n" + "queue.LifoQueue()", None), want=[(("import queue\n" + "queue.LifoQueue()").count("\n") + 1, "member-unlisted")])
case("resource.getrlimit unlisted", got=V("import resource\n" + "resource.getrlimit(1)", None), want=[(("import resource\n" + "resource.getrlimit(1)").count("\n") + 1, "member-unlisted")])
case("uuid.getnode unlisted", got=V("import uuid\n" + "uuid.getnode()", None), want=[(("import uuid\n" + "uuid.getnode()").count("\n") + 1, "member-unlisted")])
case("uuid constructors", got=V("import uuid\nx = uuid.UUID(s)\ny = uuid.uuid3(uuid.NAMESPACE_DNS, n)\n", None), want=sorted([]))
case("stat module name not a pure module escape", got=V("import stat\n" + "x = stat.S_ISDIR(m) + stat.ST_INO", None), want=[(("import stat\n" + "x = stat.S_ISDIR(m) + stat.ST_INO").count("\n") + 1, "inode")])
case("unlisted member dedup on deeper chain", got=V("import os\nos.nice.__name__\n", None), want=sorted([(2, "member-unlisted")]))
# ---- compressed archives ---------------------------------------------------------------------------------------
case("gzip.open default read clean", got=V("import gzip\nf = gzip.open(p)\ng = gzip.open(p, 'rb')\nh = gzip.open(p, mode='rt')\n", None), want=sorted([]))
case("gzip.open write", got=V("import gzip\n" + "f = gzip.open(p, 'wb')", None), want=[(("import gzip\n" + "f = gzip.open(p, 'wb')").count("\n") + 1, "clock")])
case("gzip.open append", got=V("import gzip\n" + "f = gzip.open(p, mode='ab')", None), want=[(("import gzip\n" + "f = gzip.open(p, mode='ab')").count("\n") + 1, "clock")])
case("gzip.open write with mtime kw is still clock", got=V("import gzip\n" + "f = gzip.open(p, 'wb', mtime=0)", None), want=[(("import gzip\n" + "f = gzip.open(p, 'wb', mtime=0)").count("\n") + 1, "clock")])
case("gzip.GzipFile write with mtime=0 clean", got=V("import gzip\nf = gzip.GzipFile(p, 'wb', mtime=0)\ng = gzip.GzipFile(fileobj=o, mode='wb', mtime=0)\n", None), want=sorted([]))
case("gzip.GzipFile write without mtime", got=V("import gzip\n" + "f = gzip.GzipFile(p, 'wb')", None), want=[(("import gzip\n" + "f = gzip.GzipFile(p, 'wb')").count("\n") + 1, "clock")])
case("gzip.GzipFile write mtime=None", got=V("import gzip\n" + "f = gzip.GzipFile(p, 'wb', mtime=None)", None), want=[(("import gzip\n" + "f = gzip.GzipFile(p, 'wb', mtime=None)").count("\n") + 1, "clock")])
case("gzip.GzipFile fileobj without mode", got=V("import gzip\n" + "f = gzip.GzipFile(fileobj=o)", None), want=[(("import gzip\n" + "f = gzip.GzipFile(fileobj=o)").count("\n") + 1, "dynamic")])
case("gzip.GzipFile read clean", got=V("import gzip\nf = gzip.GzipFile(p)\ng = gzip.GzipFile(p, 'rb')\n", None), want=sorted([]))
case("gzip.compress without mtime", got=V("import gzip\n" + "x = gzip.compress(d)", None), want=[(("import gzip\n" + "x = gzip.compress(d)").count("\n") + 1, "clock")])
case("gzip.compress with mtime=0 clean", got=V("import gzip\nx = gzip.compress(d, mtime=0)\ny = gzip.decompress(x)\n", None), want=sorted([]))
case("gzip non-constant mode", got=V("import gzip\n" + "f = gzip.open(p, m)", None), want=[(("import gzip\n" + "f = gzip.open(p, m)").count("\n") + 1, "dynamic")])
case("gzip splat arguments", got=V("import gzip\n" + "f = gzip.open(*a)", None), want=[(("import gzip\n" + "f = gzip.open(*a)").count("\n") + 1, "dynamic")])
case("gzip.open not called", got=V("import gzip\n" + "f = gzip.open", None), want=[(("import gzip\n" + "f = gzip.open").count("\n") + 1, "dynamic")])
case("gzip unlisted member", got=V("import gzip\n" + "gzip.zlib", None), want=[(("import gzip\n" + "gzip.zlib").count("\n") + 1, "member-unlisted")])
case("zipfile read clean", got=V("import zipfile\nz = zipfile.ZipFile(p)\ny = zipfile.ZipFile(p, 'r')\nw = zipfile.is_zipfile(p)\n", None), want=sorted([]))
case("zipfile write", got=V("import zipfile\n" + "z = zipfile.ZipFile(p, 'w')", None), want=[(("import zipfile\n" + "z = zipfile.ZipFile(p, 'w')").count("\n") + 1, "clock")])
case("zipfile append mode kw", got=V("import zipfile\n" + "z = zipfile.ZipFile(p, mode='a')", None), want=[(("import zipfile\n" + "z = zipfile.ZipFile(p, mode='a')").count("\n") + 1, "clock")])
case("zipfile non-constant mode", got=V("import zipfile\n" + "z = zipfile.ZipFile(p, m)", None), want=[(("import zipfile\n" + "z = zipfile.ZipFile(p, m)").count("\n") + 1, "dynamic")])
case("tarfile read clean", got=V("import tarfile\nt = tarfile.open(p)\nu = tarfile.open(p, 'r:gz')\nv = tarfile.open(p, 'r|*')\n"
     "w = tarfile.TarFile(p, 'r')\n", None), want=sorted([]))
case("tarfile write", got=V("import tarfile\n" + "t = tarfile.open(p, 'w:gz')", None), want=[(("import tarfile\n" + "t = tarfile.open(p, 'w:gz')").count("\n") + 1, "clock")])
case("tarfile TarFile write", got=V("import tarfile\n" + "t = tarfile.TarFile(p, 'w')", None), want=[(("import tarfile\n" + "t = tarfile.TarFile(p, 'w')").count("\n") + 1, "clock")])
case("tarfile non-constant mode", got=V("import tarfile\n" + "t = tarfile.open(p, mode=m)", None), want=[(("import tarfile\n" + "t = tarfile.open(p, mode=m)").count("\n") + 1, "dynamic")])
# ---- reflection, builtins --------------------------------------------------------------------------------------
for a in ("__dict__", "__getattribute__", "__builtins__", "__import__", "__subclasses__", "__globals__", "__code__"):
    case("reflection attribute ", got=V("" + "x = o." + a, None), want=[(("" + "x = o." + a).count("\n") + 1, "dynamic")], key=a)
case("reflection type(x).__dict__", got=V("" + "x = type(o).__dict__", None), want=[(("" + "x = type(o).__dict__").count("\n") + 1, "dynamic")])
case("reflection getattr const __dict__", got=V("" + 'x = getattr(o, "__dict__")', None), want=[(("" + 'x = getattr(o, "__dict__")').count("\n") + 1, "dynamic")])
case("reflection vars(x)", got=V("" + "x = vars(o)", None), want=[(("" + "x = vars(o)").count("\n") + 1, "dynamic")])
case("reflection dir(x)", got=V("" + "x = dir(o)", None), want=[(("" + "x = dir(o)").count("\n") + 1, "dynamic")])
case("reflection dir()", got=V("" + "x = dir()", None), want=[(("" + "x = dir()").count("\n") + 1, "dynamic")])
case("reflection dir shadowed clean", got=V("def f(dir):\n    return dir(1)\n", None), want=sorted([]))
case("sorted key=id", got=V("" + "x = sorted(a, key=id)", None), want=[(("" + "x = sorted(a, key=id)").count("\n") + 1, "entropy")])
case("min key=hash", got=V("" + "x = min(a, key=hash)", None), want=[(("" + "x = min(a, key=hash)").count("\n") + 1, "entropy")])
case("max key=id", got=V("" + "x = max(a, key=id)", None), want=[(("" + "x = max(a, key=id)").count("\n") + 1, "entropy")])
case("object.__hash__", got=V("" + "x = object.__hash__(o)", None), want=[(("" + "x = object.__hash__(o)").count("\n") + 1, "entropy")])
case("__hash__ call", got=V("" + "x = o.__hash__()", None), want=[(("" + "x = o.__hash__()").count("\n") + 1, "entropy")])
case("__hash__ definition using id", got=V("" + "class A:\n    def __hash__(self):\n        return id(self)", None), want=[(("" + "class A:\n    def __hash__(self):\n        return id(self)").count("\n") + 1, "entropy")])
# ---- stat results ----------------------------------------------------------------------------------------------
case("stat result assigned", got=V("import os\n" + "st = os.stat(p)", None), want=[(("import os\n" + "st = os.stat(p)").count("\n") + 1, "stat-result")])
case("stat result indexed", got=V("import os\n" + "x = os.stat(p)[8]", None), want=[(("import os\n" + "x = os.stat(p)[8]").count("\n") + 1, "stat-result")])
case("stat result unpacked", got=V("import os\n" + "a, b = os.stat(p)", None), want=[(("import os\n" + "a, b = os.stat(p)").count("\n") + 1, "stat-result")])
case("stat result passed on", got=V("import os\n" + "f(os.stat(p))", None), want=[(("import os\n" + "f(os.stat(p))").count("\n") + 1, "stat-result")])
case("lstat result assigned", got=V("import os\n" + "st = os.lstat(p)", None), want=[(("import os\n" + "st = os.lstat(p)").count("\n") + 1, "stat-result")])
case("fstat result assigned", got=V("import os\n" + "st = os.fstat(fd)", None), want=[(("import os\n" + "st = os.fstat(fd)").count("\n") + 1, "stat-result")])
case("stat from-import assigned", got=V("from os import stat\n" + "st = stat(p)", None), want=[(("from os import stat\n" + "st = stat(p)").count("\n") + 1, "stat-result")])
case("stat not called", got=V("import os\n" + "f = os.stat", None), want=[(("import os\n" + "f = os.stat").count("\n") + 1, "stat-result")])
case("stat passed as function", got=V("import os\n" + "x = list(map(os.stat, ps))", None), want=[(("import os\n" + "x = list(map(os.stat, ps))").count("\n") + 1, "stat-result")])
case("stat method result assigned", got=V("" + "st = e.stat()", None), want=[(("" + "st = e.stat()").count("\n") + 1, "stat-result")])
case("lstat method result assigned", got=V("" + "st = p.lstat()", None), want=[(("" + "st = p.lstat()").count("\n") + 1, "stat-result")])
case("stat other attribute", got=V("import os\n" + "x = os.stat(p).st_blocks", None), want=[(("import os\n" + "x = os.stat(p).st_blocks").count("\n") + 1, "stat-result")])
case("stat st_size clean", got=V("import os\nx = os.stat(p).st_size\ny = os.lstat(p).st_mode\nz = os.fstat(fd).st_size\n", None), want=sorted([]))
case("stat entry method clean", got=V("x = e.stat().st_size\ny = e.stat(follow_symlinks=False).st_mode\n", None), want=sorted([]))
case("stat st_mtime is reported once as clock", got=V("import os\nx = os.stat(p).st_mtime\n", None), want=sorted([(2, "clock")]))
case("stat getsize/isfile family stays allowed", got=V("import os\nos.path.getsize(p)\nos.path.isfile(p)\nos.path.isdir(p)\nos.path.islink(p)\nos.path.exists(p)\n", None), want=sorted([]))
case("DirEntry.inode()", got=V("" + "x = e.inode()", None), want=[(("" + "x = e.inode()").count("\n") + 1, "inode")])
# ---- sets ------------------------------------------------------------------------------------------------------
case("set: list(set())", got=V("" + "x = list(set(a))", None), want=[(("" + "x = list(set(a))").count("\n") + 1, "unsorted-set")])
case("set: tuple(set())", got=V("" + "x = tuple(set(a))", None), want=[(("" + "x = tuple(set(a))").count("\n") + 1, "unsorted-set")])
case("set: join(set())", got=V("" + 'x = "".join(set(a))', None), want=[(("" + 'x = "".join(set(a))').count("\n") + 1, "unsorted-set")])
case("set: enumerate(set())", got=V("" + "x = enumerate(set(a))", None), want=[(("" + "x = enumerate(set(a))").count("\n") + 1, "unsorted-set")])
case("set: next(iter(set()))", got=V("" + "x = next(iter(set(a)))", None), want=[(("" + "x = next(iter(set(a)))").count("\n") + 1, "unsorted-set")])
case("set: list(frozenset())", got=V("" + "x = list(frozenset(a))", None), want=[(("" + "x = list(frozenset(a))").count("\n") + 1, "unsorted-set")])
case("set: list(set literal)", got=V("" + "x = list({1, 2})", None), want=[(("" + "x = list({1, 2})").count("\n") + 1, "unsorted-set")])
case("set: list(set comprehension)", got=V("" + "x = list({y for y in a})", None), want=[(("" + "x = list({y for y in a})").count("\n") + 1, "unsorted-set")])
case("set: pop on set literal", got=V("" + "x = {1, 2}.pop()", None), want=[(("" + "x = {1, 2}.pop()").count("\n") + 1, "unsorted-set")])
case("set: pop on set call", got=V("" + "x = set(a).pop()", None), want=[(("" + "x = set(a).pop()").count("\n") + 1, "unsorted-set")])
case("set: unpacking", got=V("" + "a, b = set(c)", None), want=[(("" + "a, b = set(c)").count("\n") + 1, "unsorted-set")])
case("set: star in list", got=V("" + "x = [*set(a)]", None), want=[(("" + "x = [*set(a)]").count("\n") + 1, "unsorted-set")])
case("set: star in call", got=V("" + "f(*set(a))", None), want=[(("" + "f(*set(a))").count("\n") + 1, "unsorted-set")])
case("set: operator result iterated", got=V("" + "for x in set(a) | set(b): pass", None), want=[(("" + "for x in set(a) | set(b): pass").count("\n") + 1, "unsorted-set")])
case("set: union method result listed", got=V("" + "x = list(set(a).union(b))", None), want=[(("" + "x = list(set(a).union(b))").count("\n") + 1, "unsorted-set")])
case("set: map over set", got=V("" + "x = map(f, set(a))", None), want=[(("" + "x = map(f, set(a))").count("\n") + 1, "unsorted-set")])
case("set: yield from", got=V("" + "def g():\n    yield from set(a)", None), want=[(("" + "def g():\n    yield from set(a)").count("\n") + 1, "unsorted-set")])
case("set: sorted set clean", got=V("x = list(sorted(set(a)))\ny = sorted({1, 2})\nz = ''.join(sorted(set(a)))\n", None), want=sorted([]))
case("set: len/min/max/in/any clean", got=V("x = len(set(a))\ny = min(set(a))\nz = 1 in {1, 2}\nw = any(set(a))\nv = set(a)\n", None), want=sorted([]))
case("set: shadowed list is clean", got=V("def f(list):\n    return list(set(a))\n", None), want=sorted([]))
case("set: list pop clean", got=V("x = [1, 2].pop()\ny = d.pop()\n", None), want=sorted([]))
case("set: variables stay assumption (A-hashseed)", got=V("s = set(a)\nfor x in s:\n    pass\nlist(s)\n", None), want=sorted([]))
case("unproven: bare Name iteration counted", U("for k in d_keys_set:\n    pass\n") == 1, U("for k in d_keys_set:\n    pass\n"))
case("unproven: attribute iteration counted", U("for k in self.keys:\n    pass\n") == 1, "")
case("unproven: comprehension counted", U("x = [k for k in d]\ny = {k for k in e}\n") == 2, "")
case("unproven: calls and literals not counted", U("for k in f():\n    pass\nfor j in [1, 2]:\n    pass\nfor i in sorted(s):\n    pass\n") == 0, "")
case("unproven: a violation is not counted", U("for k in set(a):\n    pass\n") == 0, "")
case("unproven: nested function counted once", U("def f():\n    for k in d:\n        pass\n") == 1, "")
# ---- the gate over its own source ------------------------------------------------------------------------------
with open(GATE, encoding="utf-8") as fh:
    own = fh.read()
POLICY_PY = os.path.join(os.path.dirname(HERE), "puregate_policy.py")
SELF = PJ(GATE, POLICY_PY)
pol = open(POLICY_PY, encoding="utf-8").read() if os.path.exists(POLICY_PY) else "MISSING"
try:
    own_v = pg.check_source(own, GATE, SELF)
    pol_v = pg.check_source(pol, POLICY_PY, SELF)
except Exception as e:
    own_v = pol_v = [repr(e)]
case("self: gate source passes the gate", own_v == [], own_v[:3])
case("self: policy tables pass the gate", pol_v == [], pol_v[:3])
case("self: at most 410 lines (logic only; tables are in puregate_policy.py)", own.count("\n") <= 410, own.count("\n"))
case("self: policy module is tables only (no def/class)", " def " not in pol and "\nclass " not in pol and "\ndef " not in pol, "")
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
case("cli: clean exit 0", rc == 0 and "puregate: 1 files, 0 violations" in out, (rc, out))
rc, out, _ = run("check", bad)
case("cli: violations exit 2, line format", rc == 2 and ("%s:2: clock time.time" % bad) in out and
      ("%s:3: entropy id" % bad) in out, (rc, out))
rc, out, _ = run("check", os.path.join(tmp, "bad"), "--json", os.path.join(tmp, "o.json"))
lines = out.splitlines()
case("cli: dir walk sorted, shell + syntax reported, non-py ignored",
      rc == 2 and "run.sh:1: shell-unanalysable" in out and "bad/tool:1: shell-unanalysable" in out and
      "lib.bash:1: shell-unanalysable" in out and "z.py:1: syntax" in out and "pytool" not in out and
      "notes.txt" not in out and [l.split(":")[0] for l in lines[:-1]] == sorted(l.split(":")[0] for l in lines[:-1]),
      (rc, out))
case("cli: one shell violation per file", out.count("run.sh:") == 1, out)
case("cli: summary line", lines[-1].startswith("puregate: 5 files, 6 violations (") and
      "shell-unanalysable: 3" in lines[-1], lines[-1])
with open(os.path.join(tmp, "o.json")) as fh:
    js = json.load(fh)
with open(GATE, "rb") as fh:
    sha = hashlib.sha256(fh.read()).hexdigest()
case("cli: json schema, tool hash, counts", js["schema"] == "fnvnx-puregate/1" and js["tool"] == sha and
      js["counts_by_rule"]["shell-unanalysable"] == 3 and len(js["violations"]) == 6 and
      sum(js["counts_by_file"].values()) == 6 and len(js["files"]) == 5 and
      set(js["violations"][0]) == {"file", "line", "rule", "symbol"}, js.get("counts_by_rule"))
many = W("many/m.py", "import time\n" + "x = time.time()\n" * 250)
rc, out, _ = run("check", many, "--json", os.path.join(tmp, "m.json"))
with open(os.path.join(tmp, "m.json")) as fh:
    mj = json.load(fh)
case("cli: stdout capped at 200 violation lines, json has all",
      len(out.splitlines()) == 201 and len(mj["violations"]) == 250 and "250 violations" in out, len(out.splitlines()))
case("cli: json unproven + external fields", js.get("set_iterations_unproven") == 0 and
      js.get("external_modules_assumed_pure") == [], (js.get("set_iterations_unproven"), js.get("external_modules_assumed_pure")))
case("cli: summary reports unproven and external", "set_iterations_unproven: 0" in lines[-1] and
      "external modules assumed pure: none" in lines[-1], lines[-1])
W("ext/e.py", "import capstone\nimport mako.template\nfor k in d:\n    pass\n")
rc, out, _ = run("check", os.path.join(tmp, "ext"))
case("cli: external modules listed in summary", rc == 0 and "set_iterations_unproven: 1" in out and
      "external modules assumed pure: capstone, mako" in out, out)
# project modules: resolved against the importing file's directory and the given roots, checked in the same run
W("proj/main.py", "import helper\nfrom pkg import leaf\nfrom . import helper as h2\nimport nothere\n")
W("proj/helper.py", "import time\nx = time.time()\n")
W("proj/pkg/__init__.py", "")
W("proj/pkg/leaf.py", "import os\ny = os.getpid()\n")
rc, out, _ = run("check", os.path.join(tmp, "proj"))
case("cli: project module violation reported under its own path",
      rc == 2 and ("proj/helper.py:2: clock time.time") in out and ("proj/pkg/leaf.py:2: host os.getpid") in out and
      ("proj/main.py:4: import-unlisted nothere") in out and "main.py:1:" not in out and "main.py:2:" not in out and
      "main.py:3:" not in out, out)
rc, out, _ = run("check", os.path.join(tmp, "proj", "main.py"))
case("cli: project module outside the PATHs is import-unlisted",
      rc == 2 and "main.py:1: import-unlisted helper" in out and "main.py:2: import-unlisted pkg" in out, out)
W("root/app/a.py", "import shared\n")
W("root/shared.py", "x = 1\n")
rc, out, _ = run("check", os.path.join(tmp, "root"))
case("cli: root directory resolves imports of nested files", rc == 0, out)
rc, out, _ = run("check", os.path.join(tmp, "root", "app", "a.py"), os.path.join(tmp, "root", "shared.py"))
case("cli: a given file resolves from the importing file's directory only", rc == 2 and "import-unlisted shared" in out, out)
rc, out, _ = run("audit-stdlib")
al = out.splitlines()
case("cli: audit-stdlib prints per module and a summary, exit 0",
      rc == 0 and len(al) <= 200 and any(l.startswith("json: ") for l in al) and "math: c-module, not audited" in al and
      al[-1].startswith("audit-stdlib: "), (rc, al[-1:] if al else out))
rc, _, _ = run("check")
case("cli: no path is usage error 1", rc == 1)
rc, _, _ = run("check", os.path.join(tmp, "nope"))
case("cli: missing path is usage error 1", rc == 1)
rc, _, _ = run("frob", good)
case("cli: unknown command is usage error 1", rc == 1)
rc, out, _ = run("check", os.path.join(tmp, "good"), os.path.join(tmp, "bad", "b.py"))
case("cli: several paths", rc == 2 and "puregate: 2 files" in out, out)

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
