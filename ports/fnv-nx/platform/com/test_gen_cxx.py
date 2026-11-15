#!/usr/bin/env python3
"""test_gen_cxx.py CONTRACT_DIR: tests of gen_cxx.py (the record kind of the COM bridge generator).

  1. the decoder: for every convention letter of the MSVC mangling grammar (A cdecl, C pascal, E thiscall, G stdcall, I fastcall) the decoded `cc` is the one
     the letter table of the grammar names, and the generator accepts exactly the conventions of the bridge grammar (pascal: refused);
  2. the thunk against the convention spec, for every convention the generator emits: thiscall (`this` first, the row's args are the stack words WITHOUT
     this), stdcall and cdecl (every argument a stack word): the thunk's parameter count and the row's args agree with the decoded stack words;
  3. a signature that disagrees with the decorated name, a base without cxx info and a citation that is not wine-9.0 stop the generator."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, sys.argv[1])
import gen_cxx
import proto

LETTERS = {"A": "cdecl", "C": "pascal", "E": "thiscall", "G": "stdcall", "I": "fastcall"}
fails = []


def check(name, ok, why=""):
    if not ok:
        fails.append(name)
        print("FAIL", name, why)


def run(text):
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as f:
        f.write(text)
    try:
        return gen_cxx.build(f.name, proto)
    finally:
        os.unlink(f.name)


def dies(text):
    try:
        run(text)
    except SystemExit as e:
        return e.code == 2
    return False


CLS = "class\ttype_info\t.?AVtype_info@@\t72\t-\tf0\t-\t-\t-\twine-9.0:dlls/msvcrt/cxx.h:x\n"
for letter, cc in LETTERS.items():
    mangled = "?f@C@@QA%sXH@Z" % letter if letter == "E" else "?f@@Y%sXH@Z" % letter
    d = proto.declared(mangled)
    check("decoder " + letter, d is not None and d["cc"] == cc, "%s -> %s" % (mangled, d and d["cc"]))
# one function per convention the generator emits; the thunk follows the decoded stack words
rows = {
    "thiscall": ("?f@C@@QAEXH@Z", "void(T*,int)"),
    "stdcall": ("?f@@YGXHH@Z", "void(int,int)"),
    "cdecl": ("?f@@YAXHH@Z", "void(int,int)"),
}
for cc, (name, sig) in rows.items():
    text = "fn\tf0\t%s\t%s\twine-9.0:a.c:f\n%s" % (name, sig.replace("T*", "T*"), CLS)
    text = text.replace("void(T*", "void(unsigned*")
    r, thunks, *_ = run(text)
    d = proto.declared(name)
    check("row conv " + cc, r[0][3] == cc, r[0])
    check("row args " + cc, r[0][5] == "".join(d["args"]), r[0])
    n_params = len(re.search(r"nxb_cxx_0\((.*?)\)", thunks[0]).group(1).split(","))
    check("thunk params " + cc, n_params == len(d["args"]) + (1 if cc == "thiscall" else 0), thunks[0])
check("pascal refused", dies("fn\tf0\t?f@@YCXH@Z\tvoid(int)\twine-9.0:a.c:f\n" + CLS))
check("fastcall refused", dies("fn\tf0\t?f@@YIXHH@Z\tvoid(int,int)\twine-9.0:a.c:f\n" + CLS))
check("signature arity", dies("fn\tf0\t?f@C@@QAEXH@Z\tvoid(unsigned*)\twine-9.0:a.c:f\n" + CLS))
check("citation", dies("fn\tf0\t?f@C@@QAEXH@Z\tvoid(unsigned*,int)\tsomewhere\n" + CLS))
check("base without cxx", dies("fn\tf0\t?f@C@@QAEXH@Z\tvoid(unsigned*,int)\twine-9.0:a.c:f\n" + CLS +
      "class\tb\t.?AVb@@\t4\t-\t-\t-\t-\t-\twine-9.0:a.c:b\nclass\td\t.?AVd@@\t4\tb\t-\tinfo\tf0\t-\twine-9.0:a.c:d\n"))
print("test_gen_cxx: %s" % ("FAILED %s" % fails if fails else "ok"))
sys.exit(1 if fails else 0)
