#!/usr/bin/env python3
"""Tests for undefset.py: the static undefined set of a link from the symbol tables of its inputs. Pure."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import undefset as U  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


S = lambda *n: set(n)  # noqa: E731
names = ["u%02d" % i for i in range(30)]
prod = {"r:a.o": (S("fa"), set(names[:12]), set()), "r:b.o": (S("fb"), set(names[10:22]), set()), "r:c.o": (S("fc"), set(names[20:]) | {"sysfn"}, set())}
u = U.closure(prod, {}, [], {"sysfn"}, set())
check("30 distinct undefined symbols across 3 objects all appear", sorted(u) == names, sorted(u))
check("each lists all its referencing objects (root and unit)", u["u10"] == ["r:a.o", "r:b.o"] and u["u20"] == ["r:b.o", "r:c.o"] and u["u00"] == ["r:a.o"], (u["u10"], u["u20"]))
check("a symbol defined only by a system library (.dynsym) is not listed", "sysfn" not in u)
u = U.closure({"r:a.o": (S("x"), S("x", "ld_sym"), set())}, {}, [], set(), S("ld_sym"))
check("a name the link script assigns is not listed; a name an input defines is not", set(u) == set(), u)
rt = {"crtbegin.o": (set(), set(), S("__gmon_start__", "_ITM_x"))}
u = U.closure({"r:a.o": (set(), set(), S("pw"))}, rt, [], set(), set())
check("a weak undefined of a startfile is not listed (ruling A); one of a product object is", list(u) == ["pw"] and u["pw"] == ["r:a.o"], u)
arch = [("libc.a(m1.o)", S("memcpy"), S("deep"), set()), ("libc.a(m2.o)", S("deep"), set(), set()), ("libc.a(m3.o)", S("unused"), S("never"), set())]
u = U.closure({"r:a.o": (set(), S("memcpy"), set())}, {}, arch, set(), set())
check("an archive member is pulled only for a still undefined name, and its own references count (closure to a fixpoint)", u == {}, u)
u = U.closure({"r:a.o": (set(), S("memcpy", "nothere"), set())}, {}, arch, set(), set())
check("an undefined no member defines stays listed; an unpulled member's references do not", list(u) == ["nothere"], u)
check("script_defined: assignments, PROVIDE and HIDDEN, not comparisons or comments",
      U.script_defined("/* x = 1; */ ENTRY(e)\n PROVIDE(__a = .); __b = .; HIDDEN(_c = 1); q == r;") == {"__a", "__b", "_c"})
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
