#!/usr/bin/env python3
"""gate_abi.judge_objects: the crossing universe and the va_list rule, on objects with the plugin's sections (host gcc + objcopy build the ELF; the sections are the
documented text records). A term is judged only for a symbol of the universe; a va_list term there is green only for the native end of a msvcrt_va row.
  - a native-only va_list callee (not in the universe): green
  - an import-bound va_list symbol with no msvcrt_va row: red (abi:va)
  - the reader of a msvcrt_va row (the symbol is in va_forwarders): green
Run: python3 -I <this file>. Needs gcc, objcopy."""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
V = os.path.dirname(HERE)
sys.path.insert(0, V)
import gate_abi  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def obj(td, sym, ms, terms):
    c = os.path.join(td, sym + ".c")
    open(c, "w").write("int %s(int a){return a;}\n" % sym)
    o = os.path.join(td, sym + ".o")
    subprocess.run(["gcc", "-c", c, "-o", o], check=True)
    secs = {".fnvnx.sympair": "%s\t%s\tfunc\tdef\n" % (sym, ms), ".fnvnx.decltype": "%s\t(int)int\n" % sym, ".fnvnx.crossing": "%s\t%s\n" % (sym, terms)}
    cmd = ["objcopy"]
    for n, t in secs.items():
        p = os.path.join(td, sym + n)
        open(p, "w").write(t)
        cmd += ["--add-section", "%s=%s" % (n, p)]
    subprocess.run(cmd + [o], check=True)
    return o


with tempfile.TemporaryDirectory() as td:
    o = obj(td, "fmt_native", "?fmt_native@@YAHH@Z", "va_list")
    u, c, ids, d = gate_abi.judge_objects([("n.c", o)], universe=set(), va_forwarders=frozenset())
    check("a native-only va_list callee (not in the universe) is green", u == c, (u, c, ids))
    u, c, ids, d = gate_abi.judge_objects([("n.c", o)], universe={"fmt_native"}, va_forwarders=frozenset())
    check("an import-bound va_list symbol with no msvcrt_va row is red (abi:va)", u > c and any("abi:va:fmt_native" in i for i in ids), (u, c, ids))
    u, c, ids, d = gate_abi.judge_objects([("n.c", o)], universe={"fmt_native"}, va_forwarders=frozenset({"fmt_native"}))
    check("the native end of a msvcrt_va row is green", u == c, (u, c, ids))
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
