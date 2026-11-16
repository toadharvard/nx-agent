#!/usr/bin/env python3
# reads: ports/fnv-nx/verify
"""Tests for envcheck.system_lib_errors / spellings: the link.libs.system rule (a system library of a link is a file of a package env.json pins; the
loader is a library like another). The rule on synthetic file lists, then on this machine's dpkg state and the real env.json."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import envcheck  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


ISF = lambda p: True  # noqa: E731
IDENT = lambda p: p  # noqa: E731
OWN = {"/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2": {"libc6"}, "/usr/lib/x86_64-linux-gnu/libc.so.6": {"libc6"}, "/usr/lib/foo/libfoo.so": {"foo-dev"}}
PIN = {"libc6", "g++"}
f = lambda ps: envcheck.system_lib_errors(ps, PIN, OWN, real=IDENT, isfile=ISF)  # noqa: E731
check("a library of a pinned package is accepted", f(["/usr/lib/x86_64-linux-gnu/libc.so.6"]) == [], f(["/usr/lib/x86_64-linux-gnu/libc.so.6"]))
check("the loader, spelled merged or unmerged, is a pinned library", f(["/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"]) == []
      and f(["/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"]) == [], f(["/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"]))
e = f(["/usr/lib/foo/libfoo.so"])
check("a library of an unpinned package is red, naming the path", len(e) == 1 and e[0].startswith("/usr/lib/foo/libfoo.so:") and "no package that env.json pins" in e[0], e)
e = f(["/usr/lib/x86_64-linux-gnu/libnone.so"])
check("a file no package owns is red", len(e) == 1, e)
e = envcheck.system_lib_errors(["/nofile.so"], PIN, OWN, real=IDENT, isfile=lambda p: False)
check("a path that is no file is red", len(e) == 1 and "not a file" in e[0], e)
check("spellings: itself, resolved, merged-in and merged-out forms, once each", envcheck.spellings("/lib64/ld.so", real=lambda p: "/usr/lib/x86_64-linux-gnu/ld.so") == [
    "/lib64/ld.so", "/usr/lib64/ld.so", "/usr/lib/x86_64-linux-gnu/ld.so", "/lib/x86_64-linux-gnu/ld.so"], envcheck.spellings("/lib64/ld.so", real=lambda p: "/usr/lib/x86_64-linux-gnu/ld.so"))
# this machine: the real dpkg lists and env.json
env = json.load(open(os.path.join(os.path.dirname(HERE), "env.json")))
own = envcheck.file_owners()
for p in ("/lib64/ld-linux-x86-64.so.2", "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", "/usr/lib/x86_64-linux-gnu/libc.so.6"):
    if os.path.isfile(p):
        e = envcheck.system_lib_errors([p], env["packages"], own)
        check("this machine: %s is a pinned system library" % p, e == [], e)
e = envcheck.system_lib_errors(["/etc/hostname"], env["packages"], own)
check("this machine: a file of no package (/etc/hostname) is red", len(e) == 1, e)
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
