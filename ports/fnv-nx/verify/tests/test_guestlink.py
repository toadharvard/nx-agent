#!/usr/bin/env python3
"""Tests for verify/gate_guestlink.py and the derivation helpers of recipe.py: parse_driver_link (the driver's -### line -> profile libs), onedef (the
one-definition exclusion), judge (derived against profile). Pure: a canned driver line, no build."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_guestlink as G  # noqa: E402
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


AT = "/ext/a64"
B = AT + "/bin/../lib/gcc/aarch64-none-elf/15.2.0/"
TEXT = """Target: aarch64-none-elf
 "/ext/a64/bin/../libexec/gcc/aarch64-none-elf/15.2.0/cc1plus" "-quiet" "x.cc"
 "/ext/a64/bin/../lib/gcc/aarch64-none-elf/15.2.0/../../../../aarch64-none-elf/bin/ld" "-pie" "-o" "/out/x.elf" "%(b)silp32/pic/crti.o" "%(b)silp32/pic/crtbegin.o" "%(a)s/bin/../lib/gcc/aarch64-none-elf/15.2.0/../../../../aarch64-none-elf/lib/ilp32/pic/crt0.o" "-L%(b)silp32/pic" "-L%(a)s/bin/../lib/gcc/aarch64-none-elf/15.2.0/../../../../aarch64-none-elf/lib/ilp32/pic" "-L%(b)s" "-L%(a)s/bin/../lib/gcc" "-L/usr/lib" "/tmp/ccx.o" "-lstdc++" "-lm" "--start-group" "-lgcc" "-lc" "-lsysbase" "--end-group" "%(b)silp32/pic/crtend.o" "%(b)silp32/pic/crtn.o"
""" % {"b": B, "a": AT}
raw = recipe.parse_driver_link(TEXT, AT)
check("startfiles: the .o files before the library group, normalised and relative to the external",
      raw["startfiles"] == ["lib/gcc/aarch64-none-elf/15.2.0/ilp32/pic/crti.o", "lib/gcc/aarch64-none-elf/15.2.0/ilp32/pic/crtbegin.o",
                            "aarch64-none-elf/lib/ilp32/pic/crt0.o"], raw["startfiles"])
check("endfiles: the .o files after --end-group", raw["endfiles"] == ["lib/gcc/aarch64-none-elf/15.2.0/ilp32/pic/crtend.o",
                                                                      "lib/gcc/aarch64-none-elf/15.2.0/ilp32/pic/crtn.o"], raw["endfiles"])
check("dirs: -L inside the external, each once, in order; a -L outside it is not taken (/usr/lib)",
      raw["dirs"] == ["lib/gcc/aarch64-none-elf/15.2.0/ilp32/pic", "aarch64-none-elf/lib/ilp32/pic", "lib/gcc/aarch64-none-elf/15.2.0", "lib/gcc"], raw["dirs"])
check("names: the -l libraries in order of appearance, the group's included", raw["names"] == ["stdc++", "m", "gcc", "c", "sysbase"], raw["names"])
check("the object of the program (/tmp/ccx.o) is outside the external: not a runtime file", all("ccx" not in x for x in raw["startfiles"] + raw["endfiles"]))
check("the link is the last command line; a COLLECT_GCC_OPTIONS line after it is not a command", recipe.parse_driver_link("junk\n" + TEXT + "COLLECT_GCC_OPTIONS='-pie' '-o' '/out/x.elf'\n\n", AT) == raw)

o = {"a.o": {"_start", "x"}, "b.o": {"y"}, "c.o": set(), "d.o": {"__fnvnx_guest_start"}}
check("onedef: the objects that define a symbol a root defines, sorted", recipe.onedef(o, {"_start", "__fnvnx_guest_start"}) == ["a.o", "d.o"], recipe.onedef(o, {"_start"}))
check("onedef: no symbol shared, nothing is excluded (no name decides)", recipe.onedef(o, {"zzz"}) == [], "")
check("onedef: a weak definition counts as a definition (the caller passes global and weak symbols)", recipe.onedef({"p.o": {"_cpu_init_hook"}}, {"_cpu_init_hook"}) == ["p.o"], "")

P = {"dirs": ["d1", "d2"], "names": ["c", "m"], "startfiles": ["s.o"], "endfiles": ["e.o"]}
items, finds = G.judge(dict(P), dict(P))
check("judge: equal lists are green with every entry an item", finds == [] and len(items) == 6, (items, finds))
items, finds = G.judge(dict(P, dirs=["d1"]), dict(P))
check("judge: a missing derived directory is a finding naming the key", len(finds) == 1 and finds[0].startswith("dirs:"), finds)
items, finds = G.judge(dict(P), dict(P, names=["m", "c"]))
check("judge: the order of the libraries is part of the profile", len(finds) == 1 and finds[0].startswith("names:"), finds)
items, finds = G.judge(dict(P), dict(P, startfiles=["s.o", "hand.o"]))
check("judge: a hand-added startfile is a finding", len(finds) == 1 and finds[0].startswith("startfiles:"), finds)
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
