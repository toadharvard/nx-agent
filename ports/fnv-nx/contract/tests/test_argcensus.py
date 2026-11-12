#!/usr/bin/env python3
"""Tests for contract/argcensus.py: the classification of derived leaves, the dynamic-reach rule, the bridge rows read. No game data."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import argcensus  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


class R:
    def __init__(self, leaves, breaks=()):
        self.leaves, self.breaks = leaves, set(breaks)

    def sorted_leaves(self):
        return sorted(self.leaves, key=repr)


c, v, o, b = argcensus.leaves(R([("imm", 2, ()), ("imm", 1, ()), ("imm", 2, ())]))
check("immediates only: closed, sorted unique values", (c, v, o, b) == (True, [1, 2], [], []), (c, v, o, b))
c, v, o, b = argcensus.leaves(R([("imm", 1, ()), ("str", ("x", None), ())]))
check("a string leaf is not an immediate: open, class any", c is False and v == [1] and len(o) == 1, (c, v, o))
c, v, o, b = argcensus.leaves(R([("imm", 6, ())], [(0x401000, "live-in at function entry")]))
check("a break makes the argument open", c is False and b == ["401000 live-in at function entry"], b)
check("hex rendering", argcensus.hx([2, 17]) == "0x2,0x11" and argcensus.hx([]) == "-")

gpa = "module\tsite\tapi\toperand\tstatus\tmodules\tnpairs\tndlls\tpairs\tuses\tproof\tbreaks\n" \
      "a.exe\t0x10\tGetProcAddress\tdynamic\tclosed\tderived\t1\t1\tkernel32.dll!Foo\t-\t-\t-\n"
ll = "module\tsite\tcalls\tapi\tstatus\tnames\tbreaks\na.exe\t0x20\t1f\tLoadLibraryA\tclosed\tuser32.dll\t-\n"
check("no open row, no pair, no name of the dll: no dynamic reach", argcensus.reach(["wsock32.dll"], gpa, ll) == (True, []))
none, items = argcensus.reach(["wsock32.dll"], gpa.replace("kernel32.dll!Foo", "WSOCK32!send").replace("closed\tderived", "closed\tderived"), ll)
check("a gpa pair of the dll: reach possible", not none and items == ["gpa pair a.exe 0x10 WSOCK32!send"], items)
none, items = argcensus.reach(["wsock32.dll"], gpa.replace("\tclosed\t", "\topen\t", 1), ll)
check("an open gpa row: reach possible (the name is not known)", not none and items == ["gpa open a.exe 0x10"], items)
none, items = argcensus.reach(["wsock32.dll"], gpa, ll.replace("user32.dll", "wsock32.dll"))
check("a LoadLibrary name of the dll: reach possible", not none and items == ["loadlib name a.exe 0x20 wsock32.dll"], items)
none, items = argcensus.reach(["wsock32.dll"], gpa, ll.replace("closed\tuser32.dll", "open\t-"))
check("an open LoadLibrary site: reach possible", not none and items == ["loadlib open a.exe 0x20"], items)

d = tempfile.mkdtemp(prefix="ac-")
bp = os.path.join(d, "bridge.tsv")
open(bp, "w").write("dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n"
                    "wsock32.dll\tsend\t19\tstdcall\ti\tiiii\tf\tsym\nwsock32.dll\thtons\t9\tstdcall\ti\ti\tf\tsym\n"
                    "kernel32.dll\tSleep\t\tstdcall\ti\ti\tg\tsym\nwsock32.dll\tfoo\t99\tcdecl\ti\t\tf\tsym\n")
check("bridge rows of the chosen dll: stdcall functions, argument count, sorted",
      argcensus.bridge_rows(bp, ["wsock32.dll"]) == [("wsock32.dll", "htons", 1, "9"), ("wsock32.dll", "send", 4, "19")], argcensus.bridge_rows(bp, ["wsock32.dll"]))

if FAILED:
    print("FAILED: %d" % len(FAILED))
    sys.exit(1)
print("all passed")
