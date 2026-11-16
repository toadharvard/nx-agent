#!/usr/bin/env python3
"""recipe.call_refs: a name reached by a CALL relocation is a call; a name whose address is only taken (a table entry, a function pointer) is not (the gate machine's writer rule).
Host x86-64 objects from the system compiler of the sandbox-less test host. Run: python3 -I <this file>."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


def obj(d, n, src):
    open(d + "/%s.c" % n, "w").write(src)
    subprocess.run(["gcc", "-O2", "-c", "-fno-pic", "-o", d + "/%s.o" % n, d + "/%s.c" % n], check=True)
    return d + "/%s.o" % n


with tempfile.TemporaryDirectory() as d:
    check("a direct call is a call ref", recipe.call_refs(obj(d, "a", "int W(void);\nint f(void){return W();}\n")) == {"W"})
    check("a tail call is a call ref", recipe.call_refs(obj(d, "t", "int W(int);\nint f(int x){return W(x+1);}\n")) == {"W"})
    check("an address in a table is not a call ref", recipe.call_refs(obj(d, "b", "int W(void);\nvoid *const t[]={(void*)W};\nint g(void){return t!=0;}\n")) == set())
    check("an address taken in code and stored is not a call ref", recipe.call_refs(obj(d, "c", "int W(void);\nvoid *p;\nvoid g(void){p=(void*)W;}\n")) == set())
    check("a defined callee is not an undefined reference", recipe.call_refs(obj(d, "e", "int W(void){return 1;}\nint f(void){return W();}\n")) == set())
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
