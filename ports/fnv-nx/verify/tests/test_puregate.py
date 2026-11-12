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


def case(name, src, want, proj=None):
    got = V(src, proj)
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
one("dynamic vars(x)", "g = vars(obj)", "dynamic")
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
case("scope relative import is local", "from . import tm\nx = tm.time()\n", [], PJ("tm.py"))
case("scope except-as shadows", "import time\ndef f():\n    try:\n        pass\n    except Exception as time:\n"
     "        return time.x\n", [])
case("scope walrus in comprehension binds outside", "import os\ndef f(a):\n    [(os := y) for y in a]\n    return os.getpid()\n", [])
case("scope nested function sees outer shadow", "import time\ndef f(time):\n    def g():\n        return time.time()\n"
     "    return g\n", [])
# ---- import allow-list -----------------------------------------------------------------------------------------
one("import unlisted module", "import foo_bar", "import-unlisted")
one("import unlisted from-import", "from foo_bar import x", "import-unlisted")
one("import unlisted dotted", "import foo_bar.baz", "import-unlisted")
one("import unlisted stdlib: operator", "import operator", "import-unlisted")
one("import unlisted stdlib: operator.attrgetter", "from operator import attrgetter", "import-unlisted")
one("import unlisted stdlib: logging", "import logging", "import-unlisted")
one("import unlisted stdlib: ctypes", "import ctypes", "import-unlisted")
one("import unlisted inside function", "def f():\n    import foo_bar", "import-unlisted")
case("import star of unlisted also dynamic", "from foo_bar import *\n", [(1, "dynamic"), (1, "import-unlisted")])
for m in "ast json hashlib re struct collections itertools functools dataclasses typing enum abc math bisect heapq array " \
         "binascii base64 zlib io string textwrap shlex fnmatch posixpath copy contextlib types argparse errno stat csv " \
         "difflib bz2 lzma codecs unicodedata decimal fractions traceback warnings keyword tokenize token".split():
    case("import pure " + m, "import %s\n" % m, [])
case("import pure submodule", "import collections.abc\nimport json.decoder\nfrom collections import abc\n", [])
for m in "os os.path sys time datetime glob shutil subprocess pathlib concurrent.futures multiprocessing threading " \
         "gzip zipfile tarfile".split():
    case("import restricted " + m, "import %s\n" % m, [])
case("import restricted: from concurrent import futures", "from concurrent import futures\n", [])
for m in ("capstone", "elftools", "unicorn", "mako", "z3"):
    case("import external " + m, "import %s\nfrom %s.x import y\n" % (m, m), [])
check("external modules reported", V("import capstone\n") == [] and not isinstance(U("import capstone\n"), str) and
      pg.scan("import capstone\nimport mako.x\nimport json\n", "t.py")[2] == ["capstone", "mako"], "")
case("import project: from-import sibling", "from helper import f\n", [], PJ("helper.py"))
case("import project: import sibling", "import helper\nx = helper.f()\n", [], PJ("helper.py"))
case("import project: package", "import pkg.mod\n", [], PJ("pkg/__init__.py", "pkg/mod.py"))
case("import project: from package import submodule", "from pkg import mod\n", [], PJ("pkg/__init__.py", "pkg/mod.py"))
case("import project: from package import attribute", "from pkg import thing\n", [], PJ("pkg/__init__.py"))
case("import project: relative", "from . import tm\nfrom .tm import f\n", [], PJ("tm.py"))
case("import project: relative up", "from .. import tm\n", [], PJ("../tm.py"))
case("import project: found via given root", "import helper\n", [], PJ("lib/helper.py", roots=("lib",)))
one("import project: not in the checked set", "import helper", "import-unlisted")
case("import project: other name unresolved", "import other\n", [(1, "import-unlisted")], PJ("helper.py"))
case("import project: package module missing", "import pkg.nope\n", [(1, "import-unlisted")], PJ("pkg/__init__.py"))
case("import project: namespace dir has no attributes", "from ns import thing\n", [(1, "import-unlisted")], PJ("ns/a.py"))
case("import project: relative unresolved", "from . import tm\n", [(1, "import-unlisted")])
case("import project: shadowing a listed name is ambiguous", "import json\n", [(1, "import-unlisted")], PJ("json.py"))
case("import project: names stay unruled", "from . import tm\nx = tm.time()\n", [], PJ("tm.py"))
case("import banned module alone is clean", "import socket\nimport platform\n", [])
# ---- restricted members ----------------------------------------------------------------------------------------
one("os.nice is member-unlisted", "os.nice(1)", "member-unlisted", "import os\n")
one("os.getloadavg keeps host", "os.getloadavg()", "host", "import os\n")
one("os from-import member-unlisted", "x = nice", "member-unlisted", "from os import nice\n")
one("os.system unlisted", "os.system(c)", "member-unlisted", "import os\n")
case("os allowed members", "import os\nos.makedirs(d)\nos.remove(p)\nos.rename(a, b)\nos.fsync(fd)\nos.chmod(p, 0o644)\n"
     "x = (os.sep, os.linesep, os.environ, os.getcwd(), os.devnull, os.O_RDONLY, os.EX_OK, os.SEEK_SET, os.name)\n"
     "os.fspath(p)\nos.access(p, os.R_OK)\nos.isatty(1)\nos.pipe()\nos._exit(1)\n", [])
case("os.path members clean", "import os\nx = os.path.join(a, b) + os.path.realpath(c) + os.path.abspath(d)\n"
     "y = os.path.expanduser(e)\nos.path.exists(p)\nos.path.getsize(p)\n", [])
one("sys.getrefcount unlisted", "sys.getrefcount(x)", "member-unlisted", "import sys\n")
one("sys._getframe unlisted", "sys._getframe(0)", "member-unlisted", "import sys\n")
one("sys.getsizeof unlisted", "sys.getsizeof(x)", "member-unlisted", "import sys\n")
one("sys.settrace unlisted", "sys.settrace(f)", "member-unlisted", "import sys\n")
case("sys allowed members", "import sys\nsys.stdout.write(sys.argv[0])\nsys.stderr.buffer.write(b'')\nsys.path.insert(0, p)\n"
     "x = (sys.version_info, sys.maxsize, sys.byteorder, sys.executable, sys.platform, sys.flags.hash_randomization)\n"
     "sys.exit(2)\n", [])
one("sys.flags other attribute unlisted", "x = sys.flags.debug", "member-unlisted", "import sys\n")
one("time.monotonic keeps clock", "time.monotonic()", "clock", "import time\n")
case("datetime constructors clean", "import datetime\nx = datetime.datetime(2020, 1, 1).isoformat()\n"
     "y = datetime.datetime.strptime(s, f) + datetime.timedelta(1)\nz = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)\n", [])
one("datetime instance .today()", "x = d.today()", "clock")
one("datetime instance .now()", "x = d.now()", "clock")
one("datetime instance .utcnow()", "x = d.utcnow()", "clock")
case("glob members", "import glob\nx = glob.escape(p)\ny = glob.has_magic(p)\nz = sorted(glob.glob(p))\n", [])
one("glob unlisted member", "glob.glob1(d, p)", "member-unlisted", "import glob\n")
case("shutil allowed members", "import shutil\nshutil.copyfile(a, b)\nshutil.copyfileobj(f, g)\nshutil.copymode(a, b)\n"
     "shutil.rmtree(d)\nshutil.move(a, b)\nx = shutil.which('cc')\n", [])
one("shutil.copy2 is clock", "shutil.copy2(a, b)", "clock", "import shutil\n")
one("shutil.copystat is clock", "shutil.copystat(a, b)", "clock", "import shutil\n")
one("shutil.copytree is clock", "shutil.copytree(a, b)", "clock", "import shutil\n")
one("shutil.make_archive is clock", "shutil.make_archive(a, 'zip')", "clock", "import shutil\n")
one("shutil.get_terminal_size is host", "shutil.get_terminal_size()", "host", "import shutil\n")
one("shutil.copy unlisted", "shutil.copy(a, b)", "member-unlisted", "import shutil\n")
case("subprocess all members", "import subprocess\nsubprocess.run(c)\nsubprocess.check_output(c)\nsubprocess.Popen(c)\n"
     "subprocess.PIPE\n", [])
case("pathlib classes", "import pathlib\nfrom pathlib import Path\nx = pathlib.PurePath(a) / Path(b).name\n", [])
one("pathlib unlisted member", "pathlib.WindowsPath(a)", "member-unlisted", "import pathlib\n")
case("concurrent.futures executors", "import concurrent.futures as cf\nwith cf.ThreadPoolExecutor(2) as e:\n    list(e.map(f, a))\n"
     "g = cf.ProcessPoolExecutor\nh = cf.Future()\n", [])
one("concurrent.futures unlisted", "cf.ALL_COMPLETED", "member-unlisted", "import concurrent.futures as cf\n")
case("multiprocessing Pool", "import multiprocessing\np = multiprocessing.Pool(2)\nx = p.map(f, a)\ny = p.starmap(f, a)\nz = p.imap(f, a)\n", [])
one("multiprocessing unlisted", "multiprocessing.Process(target=f)", "member-unlisted", "import multiprocessing\n")
case("threading members", "import threading\nt = threading.Thread(target=f)\nl = threading.Lock()\nr = threading.RLock()\n"
     "e = threading.Event()\nx = threading.local()\n", [])
one("threading.active_count unlisted", "threading.active_count()", "member-unlisted", "import threading\n")
one("threading.enumerate unlisted", "threading.enumerate()", "member-unlisted", "import threading\n")
one("queue.LifoQueue unlisted", "queue.LifoQueue()", "member-unlisted", "import queue\n")
one("resource.getrlimit unlisted", "resource.getrlimit(1)", "member-unlisted", "import resource\n")
one("uuid.getnode unlisted", "uuid.getnode()", "member-unlisted", "import uuid\n")
case("uuid constructors", "import uuid\nx = uuid.UUID(s)\ny = uuid.uuid3(uuid.NAMESPACE_DNS, n)\n", [])
one("stat module name not a pure module escape", "x = stat.S_ISDIR(m) + stat.ST_INO", "inode", "import stat\n")
case("unlisted member dedup on deeper chain", "import os\nos.nice.__name__\n", [(2, "member-unlisted")])
# ---- compressed archives ---------------------------------------------------------------------------------------
case("gzip.open default read clean", "import gzip\nf = gzip.open(p)\ng = gzip.open(p, 'rb')\nh = gzip.open(p, mode='rt')\n", [])
one("gzip.open write", "f = gzip.open(p, 'wb')", "clock", "import gzip\n")
one("gzip.open append", "f = gzip.open(p, mode='ab')", "clock", "import gzip\n")
one("gzip.open write with mtime kw is still clock", "f = gzip.open(p, 'wb', mtime=0)", "clock", "import gzip\n")
case("gzip.GzipFile write with mtime=0 clean", "import gzip\nf = gzip.GzipFile(p, 'wb', mtime=0)\ng = gzip.GzipFile(fileobj=o, mode='wb', mtime=0)\n", [])
one("gzip.GzipFile write without mtime", "f = gzip.GzipFile(p, 'wb')", "clock", "import gzip\n")
one("gzip.GzipFile write mtime=None", "f = gzip.GzipFile(p, 'wb', mtime=None)", "clock", "import gzip\n")
one("gzip.GzipFile fileobj without mode", "f = gzip.GzipFile(fileobj=o)", "dynamic", "import gzip\n")
case("gzip.GzipFile read clean", "import gzip\nf = gzip.GzipFile(p)\ng = gzip.GzipFile(p, 'rb')\n", [])
one("gzip.compress without mtime", "x = gzip.compress(d)", "clock", "import gzip\n")
case("gzip.compress with mtime=0 clean", "import gzip\nx = gzip.compress(d, mtime=0)\ny = gzip.decompress(x)\n", [])
one("gzip non-constant mode", "f = gzip.open(p, m)", "dynamic", "import gzip\n")
one("gzip splat arguments", "f = gzip.open(*a)", "dynamic", "import gzip\n")
one("gzip.open not called", "f = gzip.open", "dynamic", "import gzip\n")
one("gzip unlisted member", "gzip.zlib", "member-unlisted", "import gzip\n")
case("zipfile read clean", "import zipfile\nz = zipfile.ZipFile(p)\ny = zipfile.ZipFile(p, 'r')\nw = zipfile.is_zipfile(p)\n", [])
one("zipfile write", "z = zipfile.ZipFile(p, 'w')", "clock", "import zipfile\n")
one("zipfile append mode kw", "z = zipfile.ZipFile(p, mode='a')", "clock", "import zipfile\n")
one("zipfile non-constant mode", "z = zipfile.ZipFile(p, m)", "dynamic", "import zipfile\n")
case("tarfile read clean", "import tarfile\nt = tarfile.open(p)\nu = tarfile.open(p, 'r:gz')\nv = tarfile.open(p, 'r|*')\n"
     "w = tarfile.TarFile(p, 'r')\n", [])
one("tarfile write", "t = tarfile.open(p, 'w:gz')", "clock", "import tarfile\n")
one("tarfile TarFile write", "t = tarfile.TarFile(p, 'w')", "clock", "import tarfile\n")
one("tarfile non-constant mode", "t = tarfile.open(p, mode=m)", "dynamic", "import tarfile\n")
# ---- reflection, builtins --------------------------------------------------------------------------------------
for a in ("__dict__", "__getattribute__", "__builtins__", "__import__", "__subclasses__", "__globals__", "__code__"):
    one("reflection attribute " + a, "x = o." + a, "dynamic")
one("reflection type(x).__dict__", "x = type(o).__dict__", "dynamic")
one("reflection getattr const __dict__", 'x = getattr(o, "__dict__")', "dynamic")
one("reflection vars(x)", "x = vars(o)", "dynamic")
one("reflection dir(x)", "x = dir(o)", "dynamic")
one("reflection dir()", "x = dir()", "dynamic")
case("reflection dir shadowed clean", "def f(dir):\n    return dir(1)\n", [])
one("sorted key=id", "x = sorted(a, key=id)", "entropy")
one("min key=hash", "x = min(a, key=hash)", "entropy")
one("max key=id", "x = max(a, key=id)", "entropy")
one("object.__hash__", "x = object.__hash__(o)", "entropy")
one("__hash__ call", "x = o.__hash__()", "entropy")
one("__hash__ definition using id", "class A:\n    def __hash__(self):\n        return id(self)", "entropy")
# ---- stat results ----------------------------------------------------------------------------------------------
one("stat result assigned", "st = os.stat(p)", "stat-result", "import os\n")
one("stat result indexed", "x = os.stat(p)[8]", "stat-result", "import os\n")
one("stat result unpacked", "a, b = os.stat(p)", "stat-result", "import os\n")
one("stat result passed on", "f(os.stat(p))", "stat-result", "import os\n")
one("lstat result assigned", "st = os.lstat(p)", "stat-result", "import os\n")
one("fstat result assigned", "st = os.fstat(fd)", "stat-result", "import os\n")
one("stat from-import assigned", "st = stat(p)", "stat-result", "from os import stat\n")
one("stat not called", "f = os.stat", "stat-result", "import os\n")
one("stat passed as function", "x = list(map(os.stat, ps))", "stat-result", "import os\n")
one("stat method result assigned", "st = e.stat()", "stat-result")
one("lstat method result assigned", "st = p.lstat()", "stat-result")
one("stat other attribute", "x = os.stat(p).st_blocks", "stat-result", "import os\n")
case("stat st_size clean", "import os\nx = os.stat(p).st_size\ny = os.lstat(p).st_mode\nz = os.fstat(fd).st_size\n", [])
case("stat entry method clean", "x = e.stat().st_size\ny = e.stat(follow_symlinks=False).st_mode\n", [])
case("stat st_mtime is reported once as clock", "import os\nx = os.stat(p).st_mtime\n", [(2, "clock")])
case("stat getsize/isfile family stays allowed",
     "import os\nos.path.getsize(p)\nos.path.isfile(p)\nos.path.isdir(p)\nos.path.islink(p)\nos.path.exists(p)\n", [])
one("DirEntry.inode()", "x = e.inode()", "inode")
# ---- sets ------------------------------------------------------------------------------------------------------
one("set: list(set())", "x = list(set(a))", "unsorted-set")
one("set: tuple(set())", "x = tuple(set(a))", "unsorted-set")
one("set: join(set())", 'x = "".join(set(a))', "unsorted-set")
one("set: enumerate(set())", "x = enumerate(set(a))", "unsorted-set")
one("set: next(iter(set()))", "x = next(iter(set(a)))", "unsorted-set")
one("set: list(frozenset())", "x = list(frozenset(a))", "unsorted-set")
one("set: list(set literal)", "x = list({1, 2})", "unsorted-set")
one("set: list(set comprehension)", "x = list({y for y in a})", "unsorted-set")
one("set: pop on set literal", "x = {1, 2}.pop()", "unsorted-set")
one("set: pop on set call", "x = set(a).pop()", "unsorted-set")
one("set: unpacking", "a, b = set(c)", "unsorted-set")
one("set: star in list", "x = [*set(a)]", "unsorted-set")
one("set: star in call", "f(*set(a))", "unsorted-set")
one("set: operator result iterated", "for x in set(a) | set(b): pass", "unsorted-set")
one("set: union method result listed", "x = list(set(a).union(b))", "unsorted-set")
one("set: map over set", "x = map(f, set(a))", "unsorted-set")
one("set: yield from", "def g():\n    yield from set(a)", "unsorted-set")
case("set: sorted set clean", "x = list(sorted(set(a)))\ny = sorted({1, 2})\nz = ''.join(sorted(set(a)))\n", [])
case("set: len/min/max/in/any clean", "x = len(set(a))\ny = min(set(a))\nz = 1 in {1, 2}\nw = any(set(a))\nv = set(a)\n", [])
case("set: shadowed list is clean", "def f(list):\n    return list(set(a))\n", [])
case("set: list pop clean", "x = [1, 2].pop()\ny = d.pop()\n", [])
case("set: variables stay assumption (A-hashseed)", "s = set(a)\nfor x in s:\n    pass\nlist(s)\n", [])
check("unproven: bare Name iteration counted", U("for k in d_keys_set:\n    pass\n") == 1, U("for k in d_keys_set:\n    pass\n"))
check("unproven: attribute iteration counted", U("for k in self.keys:\n    pass\n") == 1, "")
check("unproven: comprehension counted", U("x = [k for k in d]\ny = {k for k in e}\n") == 2, "")
check("unproven: calls and literals not counted", U("for k in f():\n    pass\nfor j in [1, 2]:\n    pass\nfor i in sorted(s):\n    pass\n") == 0, "")
check("unproven: a violation is not counted", U("for k in set(a):\n    pass\n") == 0, "")
check("unproven: nested function counted once", U("def f():\n    for k in d:\n        pass\n") == 1, "")
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
check("self: gate source passes the gate", own_v == [], own_v[:3])
check("self: policy tables pass the gate", pol_v == [], pol_v[:3])
check("self: at most 410 lines (logic only; tables are in puregate_policy.py)", own.count("\n") <= 410, own.count("\n"))
check("self: policy module is tables only (no def/class)", " def " not in pol and "\nclass " not in pol and "\ndef " not in pol, "")
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
check("cli: json unproven + external fields", js.get("set_iterations_unproven") == 0 and
      js.get("external_modules_assumed_pure") == [], (js.get("set_iterations_unproven"), js.get("external_modules_assumed_pure")))
check("cli: summary reports unproven and external", "set_iterations_unproven: 0" in lines[-1] and
      "external modules assumed pure: none" in lines[-1], lines[-1])
W("ext/e.py", "import capstone\nimport mako.template\nfor k in d:\n    pass\n")
rc, out, _ = run("check", os.path.join(tmp, "ext"))
check("cli: external modules listed in summary", rc == 0 and "set_iterations_unproven: 1" in out and
      "external modules assumed pure: capstone, mako" in out, out)
# project modules: resolved against the importing file's directory and the given roots, checked in the same run
W("proj/main.py", "import helper\nfrom pkg import leaf\nfrom . import helper as h2\nimport nothere\n")
W("proj/helper.py", "import time\nx = time.time()\n")
W("proj/pkg/__init__.py", "")
W("proj/pkg/leaf.py", "import os\ny = os.getpid()\n")
rc, out, _ = run("check", os.path.join(tmp, "proj"))
check("cli: project module violation reported under its own path",
      rc == 2 and ("proj/helper.py:2: clock time.time") in out and ("proj/pkg/leaf.py:2: host os.getpid") in out and
      ("proj/main.py:4: import-unlisted nothere") in out and "main.py:1:" not in out and "main.py:2:" not in out and
      "main.py:3:" not in out, out)
rc, out, _ = run("check", os.path.join(tmp, "proj", "main.py"))
check("cli: project module outside the PATHs is import-unlisted",
      rc == 2 and "main.py:1: import-unlisted helper" in out and "main.py:2: import-unlisted pkg" in out, out)
W("root/app/a.py", "import shared\n")
W("root/shared.py", "x = 1\n")
rc, out, _ = run("check", os.path.join(tmp, "root"))
check("cli: root directory resolves imports of nested files", rc == 0, out)
rc, out, _ = run("check", os.path.join(tmp, "root", "app", "a.py"), os.path.join(tmp, "root", "shared.py"))
check("cli: a given file resolves from the importing file's directory only", rc == 2 and "import-unlisted shared" in out, out)
rc, out, _ = run("audit-stdlib")
al = out.splitlines()
check("cli: audit-stdlib prints per module and a summary, exit 0",
      rc == 0 and len(al) <= 200 and any(l.startswith("json: ") for l in al) and "math: c-module, not audited" in al and
      al[-1].startswith("audit-stdlib: "), (rc, al[-1:] if al else out))
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
