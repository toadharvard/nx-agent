#!/usr/bin/env python3
# reads: ports/fnv-nx/verify
"""Tests for verify/gate_abspath.py on a toy tree (judge over invented sources). Plain script: exit 0 on pass."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_abspath as G  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


T = "ports/fnv-nx/t"


def run(seeds, **src):
    files = {"%s/%s.py" % (T, k): v for k, v in src.items()}
    return G.judge(files, ["%s/%s.py" % (T, s) for s in seeds])


def kinds(r):
    return sorted({x.split(":")[1] for x in r["findings"]})


r = run(["tool"], tool="def f():\n    return open('/home/x/y').read()\n")
check("stage tool opening /home/x/y is red", kinds(r) == ["literal"], r)
r = run(["tool"], tool="def f():\n    return open('/ext/x/y').read()\n")
check("stage tool opening /ext/x/y is green", r["findings"] == [], r)
r = run(["tool"], tool="import os\nD = '/uses/a'\ndef f():\n    return os.listdir(D), open('/out/z'), open('/tmp')\n")
check("mount prefixes (also through a name) are green", r["findings"] == [], r)
r = run(["tool"], tool="import os\nD = '/home/q'\ndef f():\n    return os.listdir(D)\n")
check("a constant that flows into os.listdir through a name is red", kinds(r) == ["literal"], r)
r = run(["tool"], tool="SEP = '/etc/x'\ndef f(a):\n    return a == SEP\n")
check("an absolute-looking string that is not used as a path is not judged", r["findings"] == [], r)
r = run(["tool"], tool="import os\nR = 'a'\ndef f(w):\n    return open(w + '/x.tsv'), open('%s/x' % w), os.path.join(R, 'q'), open(f'{w}/y')\n")
check("suffix fragments are not absolute paths", r["findings"] == [], r)
r = run(["tool"], tool="import os\nx = os.environ.get('K', 'd')\n")
check("an env default is red", kinds(r) == ["envdefault"], r)
r = run(["tool"], tool="import os\nx = os.environ.get('K')\ny = os.getenv('K', None)\nz = os.environ['K']\n")
check("a missing env input with no default is not red", r["findings"] == [], r)
r = run(["tool"], tool="import os\ntry:\n    x = os.environ['K']\nexcept KeyError:\n    x = 'd'\nos.environ.setdefault('A', 'b')\n")
check("os.environ[k] in a try with a fallback, and setdefault, are red", [x.split(":")[3] for x in r["findings"]] == ["3", "6"], r)
r = run(["tool"], tool="import os\nh = os.path.expanduser('~/x')\n")
check("home is red", kinds(r) == ["home"], r)
r = run(["tool"], tool="x = 1\n", helper="def f():\n    return open('/home/x/y')\n")
check("a test-only helper outside the closure is not judged", r["findings"] == [] and r["closure"] == 1, r)
r = run(["tool"], tool="import helper\nhelper.f()\n", helper="def f():\n    return open('/home/x/y')\n")
check("an imported helper with an absolute path is red", kinds(r) == ["literal"] and r["closure"] == 2, r)
r = run(["tool"], tool="from . import helper\n", helper="import os\nos.walk('/var/x')\n")
check("a relative import is followed", kinds(r) == ["literal"], r)
r = run(["tool"], tool="import importlib\nimportlib.import_module(name)\n")
check("a dynamic import is red", [x.split(":")[1] for x in r["findings"]] == ["dynamic-import"], r)
r = run(["tool"], tool="import importlib\nm = importlib.import_module('helper')\n", helper="x = 1\n")
check("a constant import_module is not dynamic", r["findings"] == [], r)
r = run(["tool"], tool="import subprocess\nsubprocess.run(['/usr/bin/git', 'x'])\n")
check("an absolute path in a subprocess argv is red", kinds(r) == ["literal"], r)
r = run(["tool"], tool="import runpy, sys\ndef require_mounted(p):\n    return p\ntool = require_mounted(sys.argv[1])\nrunpy.run_path(tool, run_name='__main__')\n")
check("a dynamic load through require_mounted (via a name) is green", r["findings"] == [], r)
r = run(["tool"], tool="import runpy, sys\ntool = sys.argv[1]\nrunpy.run_path(tool)\n")
check("an unguarded launch is red", [x.split(":")[1] for x in r["findings"]] == ["dynamic-import"], r)
r = run(["tool"], tool="import runpy, sys\ntool = require_mounted(sys.argv[1])\ntool = sys.argv[2]\nrunpy.run_path(tool)\n")
check("a name with one unguarded assignment is red", [x.split(":")[1] for x in r["findings"]] == ["dynamic-import"], r)
r = run(["tool"], tool="import os\ntry:\n    x = os.environ['K']\nexcept ValueError:\n    x = 1\n")
check("os.environ[k] in a try whose handlers do not catch a missing key is not a fallback", r["findings"] == [], r)
if fails:
    sys.exit(1)
