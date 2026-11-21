#!/usr/bin/env python3
"""Tests of the clone facts (`.fnvnx.clones`, toolchain/names) as objstub judges them: a clone is judged by the records of its root; toy ILP32 objects from assembler.
Plain script, one PASS/FAIL line per case, exit 0 on pass. Needs aarch64-linux-gnu-gcc (env.json)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx)
from fnvnx.verify import objstub  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="objstub-clones-")
FAILED, N = [], [0]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def func(name, body):
    return ".text\n.global %s\n.type %s,%%function\n%s:\n%s\n ret\n.size %s,.-%s\n" % (name, name, name, body, name, name)


def const(fn, length, value=""):
    return ".pushsection .fnvnx.const,\"\",%%progbits\n.balign 4\n.4byte %s\n.4byte %d\n%s.balign 4\n.popsection\n" % (fn, length, value)


def ident(fn, k):
    return ".pushsection .fnvnx.ident,\"\",%%progbits\n.balign 4\n.4byte %s\n.4byte %d\n.balign 4\n.popsection\n" % (fn, k)


def clones(*rows):
    return ".pushsection .fnvnx.clones,\"\",%progbits\n" + "".join('.ascii "%s\\n"\n' % "\\t".join(r) for r in rows) + ".popsection\n"


def row(clone, origin, root, kind, params):
    return (clone, origin, root, kind, json.dumps(params, separators=(",", ":")).replace('"', '\\"'))


def judge(src):
    N[0] += 1
    s, o = os.path.join(ROOT, "s%d.s" % N[0]), os.path.join(ROOT, "o%d.o" % N[0])
    open(s, "w").write(src)
    subprocess.run(["aarch64-linux-gnu-gcc", "-mabi=ilp32", "-c", s, "-o", o], check=True, capture_output=True)
    _, finds = objstub.run_check([(o, o)], set(), set())
    return {(n, k) for _, n, k, _ in finds}, finds


cp = lambda j, i: {"j": j, "origin": i, "op": "copy"}   # noqa: E731
seven = " mov w0, #7"
k, f = judge(func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("f.constprop.0", seven))
check("a V clone with no row and no record is const-missing (it is no clone)", k == {("f.constprop.0", "const-missing")}, f)
k, f = judge(func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("f.constprop.0", seven) + clones(row("f.constprop.0", "f", "f", "constprop", [cp(0, 0), {"origin": 1, "op": "const", "value": 7}])))
check("a V clone with a row takes the record of its root", not k, f)
k, f = judge(func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("f.constprop.0", " mov w0, #8") + clones(row("f.constprop.0", "f", "f", "constprop", [cp(0, 0)])))
check("a clone whose result differs from the root's record is const-mismatch", k == {("f.constprop.0", "const-mismatch")}, f)
k, f = judge(func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("f.part.0", seven) + clones(row("f.part.0", "f", "f", "part", None)))
check("a part never takes the record of its root", k == {("f.part.0", "const-missing")}, f)
k, f = judge(func("i", " mov w0, w2") + ident("i", 2) + func("i.isra.0", " mov w0, w1") + clones(row("i.isra.0", "i", "i", "isra", [cp(0, 0), cp(1, 2), {"origin": 1, "op": "removed"}])))
check("an identity record k moves to the clone parameter that copies it", not k, f)
k, f = judge(func("i", " mov w0, w2") + ident("i", 2) + func("i.isra.0", " mov w0, w2") + clones(row("i.isra.0", "i", "i", "isra", [cp(0, 0), cp(1, 2), {"origin": 1, "op": "removed"}])))
check("the root's index is not the clone's: reading the old register is const-mismatch", k == {("i.isra.0", "const-mismatch")}, f)
k, f = judge(func("i", " mov w0, w1") + ident("i", 1) + func("i.isra.0", " mov w0, w1") + clones(row("i.isra.0", "i", "i", "isra", [{"j": 0, "origin": 1, "op": "split", "offset": 0, "size": 4, "by_ref": True}, {"origin": 0, "op": "removed"}])))
check("a split parameter does not keep the identity", k == {("i.isra.0", "const-mismatch")}, f)
k, f = judge(func("i", " mov w0, w1") + ident("i", 1) + func("i.constprop.0", " mov w0, w1") + func("i.constprop.0.isra.0", " mov w0, w0")
             + clones(row("i.constprop.0", "i", "i", "constprop", [cp(0, 0), cp(1, 1)]), row("i.constprop.0.isra.0", "i.constprop.0", "i", "isra", [cp(0, 1), {"origin": 0, "op": "removed"}])))
check("a clone of a clone composes the maps along the chain to the root", k == set(), f)   # root 1 -> constprop 1 -> isra 0
k, f = judge(func("f", seven) + func("g", seven) + const("f", 4, "\t.4byte 7\n") + const("g", 4, "\t.4byte 7\n") + clones(row("g", "f", "f", "clone", None)))
check("a row naming a function with a body that is no clone's: the clone is judged by the root's record", k == set(), f)
for why, src in (
        ("a row with 4 fields", func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("c", seven) + ".pushsection .fnvnx.clones,\"\",%progbits\n.ascii \"c\\tf\\tf\\tisra\\n\"\n.popsection\n"),
        ("a parameter map that is not JSON", func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("c", seven) + ".pushsection .fnvnx.clones,\"\",%progbits\n.ascii \"c\\tf\\tf\\tisra\\t[x\\n\"\n.popsection\n"),
        ("a clone that is not defined here", func("f", seven) + const("f", 4, "\t.4byte 7\n") + clones(row("c", "f", "f", "isra", None))),
        ("a kind outside the four", func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("c", seven) + clones(row("c", "f", "f", "other", None))),
        ("a part with a map", func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("c", seven) + clones(row("c", "f", "f", "part", [cp(0, 0)]))),
        ("a duplicated clone parameter", func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("c", seven) + clones(row("c", "f", "f", "isra", [cp(0, 0), cp(0, 1)]))),
        ("an origin that is no row and is not the root", func("f", seven) + const("f", 4, "\t.4byte 7\n") + func("g", seven) + func("c", seven) + clones(row("c", "g", "f", "isra", [cp(0, 0)]))),
        ("a cycle of origins", func("f", seven) + func("c", seven) + func("d", seven) + clones(row("c", "d", "f", "clone", None), row("d", "c", "f", "clone", None))),
        ("a split without by_ref", func("f", seven) + func("c", seven) + clones(row("c", "f", "f", "isra", [{"j": 0, "origin": 0, "op": "split", "offset": 0, "size": 4}])))):
    k, f = judge(src)
    check("%s is clones-malformed" % why, any(kk == "clones-malformed" for _, kk in k), f)
print("fails: %d" % len(FAILED))
sys.exit(1 if FAILED else 0)
