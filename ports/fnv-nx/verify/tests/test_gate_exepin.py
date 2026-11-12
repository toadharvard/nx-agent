#!/usr/bin/env python3
"""Tests for verify/gate_exepin.py (judge on invented sources) and the tree itself. Plain script: exit 0 on pass."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_exepin as G  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


OPT = "import argparse, os, subprocess\nap = argparse.ArgumentParser()\nap.add_argument('--exe')\na = ap.parse_args()\n"
PIN = "def load_exe(path):\n    return open(path, 'rb').read()\n"


def judge(**src):
    bad, err = G.judge({"ports/fnv-nx/contract/%s.py" % k: v for k, v in src.items()})
    return sorted(os.path.basename(p)[:-3] for p in bad), err


bad, err = judge(exepin=PIN, t=OPT + "import exepin\nd = exepin.load_exe(a.exe)\nprint(len(d))\n")
check("load_exe is the one way: certified", bad == [] and err == [], (bad, err))
bad, _ = judge(t=OPT + "d = open(a.exe, 'rb').read()\n")
check("open(a.exe) is refused", bad == ["t"], bad)
bad, _ = judge(t=OPT + "x = os.path.join(os.path.dirname(a.exe), 'y')\nopen(x)\nopen(os.path.dirname(a.exe))\n")
check("a path derived with os.path.* is still the exe path", bad == ["t"], bad)
bad, _ = judge(t=OPT + "def rd(p):\n    return open(p, 'rb').read()\nrd(a.exe)\n")
check("through a parameter of a tracked function", bad == ["t"], bad)
bad, _ = judge(h="def rd(p):\n    return open(p, 'rb').read()\n", t=OPT + "import h\nh.rd(a.exe)\n")
check("through a parameter of a function of another file (mod.f)", bad == ["h", ] or bad == ["h", "t"], bad)
bad, _ = judge(h="def rd(p):\n    return open(p, 'rb').read()\n", t=OPT + "import exepin\nexepin.load_exe(a.exe)\nh.rd('x')\n", exepin=PIN)
check("same-named parameter of another function is not tainted", bad == [], bad)
bad, _ = judge(t=OPT + "class C:\n    def __init__(self, p):\n        self.d = open(p, 'rb').read()\nC(a.exe)\n")
check("through a constructor", bad == ["t"], bad)
bad, _ = judge(exepin=PIN, t=OPT + "import exepin\nclass C:\n    def __init__(self, p):\n        self.d = exepin.load_exe(p)\nC(a.exe)\n")
check("a constructor that calls load_exe is certified", bad == [], bad)
bad, _ = judge(t="import os\ndef f(g):\n    return open(os.path.join(g, 'FalloutNV.exe'), 'rb').read()\n")
check("the file name FalloutNV.exe joined and opened is refused", bad == ["t"], bad)
bad, _ = judge(t='"""about FalloutNV.exe"""\nimport os\nx = open(os.path.join("d", "notes.txt"))\n')
check("a docstring or another name is not a source", bad == [], bad)
bad, _ = judge(t=OPT + "subprocess.run(['xxd', a.exe])\n")
check("subprocess with the exe path is refused", bad == ["t"], bad)
bad, _ = judge(t=OPT + "subprocess.run(['tool.py', '--exe', a.exe, '--out', 'o'])\n")
check("forwarding after the literal '--exe' to another entry point is allowed", bad == [], bad)
bad, _ = judge(t=OPT + "def py(*a):\n    return list(a)\nsubprocess.run(py('n1.py', '--exe', a.exe))\n")
check("forwarding through a list builder is allowed", bad == [], bad)
bad, _ = judge(t=OPT + "def src():\n    return a.exe\nopen(src())\n")
check("a tracked function returning the path taints its result", bad == ["t"], bad)
bad, _ = judge(t="import argparse\nap = argparse.ArgumentParser()\na = ap.parse_args()\nopen(a.exe)\n")
check("a file without the --exe option has no source", bad == [], bad)
bad, err = judge(t="def (:\n")
check("a file that does not parse is uncertified and an error", bad == ["t"] and err, (bad, err))

files = ["ports/fnv-nx/a/x.py", "ports/fnv-nx/a/tests/test_x.py", "ports/fnv-nx/a/y.md", "other/z.py"]
check("universe: tracked .py of the tree outside tests", G.universe(files) == ["ports/fnv-nx/a/x.py"], G.universe(files))
sys.exit(1 if fails else 0)
