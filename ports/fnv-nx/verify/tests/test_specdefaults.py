#!/usr/bin/env python3
"""Tests for specdefaults.py and its use in recipe.py (RECIPE.md "Distribution spec defaults"). Plain script: PASS/FAIL lines, exit 0 on pass.
The reference compiler is a stand-in: the distribution's own cross gcc started with the options a GNU release has by default (a wrapper script
in a pinned external, as the real reference is a pinned external); the rule itself is the difference of the two compilers' reports."""
import json
import os
import stat
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, VERIFY)
import test_recipe as TR  # noqa: E402
import recipe  # noqa: E402
import specdefaults as S  # noqa: E402

T0 = time.time()
FAILED, check = TR.FAILED, TR.check
NEUTRAL = ["-fno-stack-protector", "-fno-stack-clash-protection", "-fcf-protection=none", "-fno-PIE", "-fno-pie", "-U_FORTIFY_SOURCE"]
CC, CXX = "aarch64-linux-gnu-gcc-13", "aarch64-linux-gnu-g++-13"
# a dk-style unit: libc calls that FORTIFY_SOURCE rewrites to *_chk and a local buffer that -fstack-protector-strong guards
UNIT = ('#include <stdio.h>\n#include <string.h>\nint f_a(const char *s, unsigned n) {\n  char buf[64];\n  memcpy(buf, s, n);\n'
        '  snprintf(buf, sizeof buf, "%s", s);\n  return buf[n & 15];\n}\n')


def wrapper(d, name, driver):
    p = os.path.join(d, "bin", name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write('#!/bin/sh\nexec /usr/bin/%s %s "$@"\n' % (driver, " ".join(NEUTRAL)))
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def syms(obj):
    return subprocess.run(["aarch64-linux-gnu-nm", obj], capture_output=True, text=True, check=True).stdout


def compile_obj(cc, flags, d, name):
    src = os.path.join(d, name + ".c")
    open(src, "w").write(UNIT)
    subprocess.run([cc, "-O2", "-c", src, "-o", os.path.join(d, name + ".o")] + flags, check=True, capture_output=True)
    return syms(os.path.join(d, name + ".o"))


def main():
    # --- the pure rule
    check("parse_help: states and one-token values; `<file> /tmp/x` lines are not options",
          S.parse_help("  -fa  \t[enabled]\n  -fb \t[disabled]\n  -fcf-protection \t-fcf-protection=full\n  -o <file>   \t\t/tmp/x.s\n  -mabi= \t[default]\n") ==
          {"-fa": "[enabled]", "-fb": "[disabled]", "-fcf-protection": "-fcf-protection=full"}, S.parse_help("  -o <file> \t/tmp/x.s\n"))
    check("candidates: enabled keeps the name, disabled takes the off form",
          S.candidates("-fa", "[enabled]") == ["-fa"] and S.candidates("-fa", "[disabled]") == ["-fno-a"] and S.candidates("-mx", "[disabled]") == ["-mno-x"])
    check("candidates: an option with no off form of its own falls back to its family (-fstack-protector-strong -> -fno-stack-protector)",
          S.candidates("-fstack-protector-strong", "[disabled]") == ["-fno-stack-protector-strong", "-fno-stack-protector", "-fno-stack"])
    check("derive_from: a difference becomes the reference's value; macros: distro-only -> -U, differing -> reference value",
          S.derive_from({"-fa": "[disabled]", "-fc": "[enabled]"}, {"-fa": "[enabled]", "-fc": "[enabled]", "-fz": "[enabled]"},
                        {"M": "1", "N": "2"}, {"M": "1", "N": "3", "_FORTIFY_SOURCE": "3"}) == ["-DN=2", "-U_FORTIFY_SOURCE", "-fno-a"])
    ov = S.overlap(["-O2", "-fno-stack-clash-protection", "-D_FORTIFY_SOURCE=2", "-fcf-protection=full", "-Wall"],
                   ["-U_FORTIFY_SOURCE", "-fcf-protection=none", "-fno-stack-clash-protection", "-fno-stack-protector"])
    ov = S.overlap(["-O2", "-fno-stack-clash-protection", "-D_FORTIFY_SOURCE=2", "-fcf-protection=full", "-Wall"],
                   ["-U_FORTIFY_SOURCE", "-fcf-protection=none", "-fno-stack-clash-protection", "-fno-stack-protector"])
    check("overlap: -O2 and -Wall are free, the rest is refused", ov == ["-fno-stack-clash-protection", "-D_FORTIFY_SOURCE=2", "-fcf-protection=full"], ov)
    ov = S.overlap(["-fstack-protector-strong", "-fstack-protector"], ["-fno-stack-protector"])
    check("overlap: -fstack-protector-strong is the same family as -fno-stack-protector", ov == ["-fstack-protector-strong", "-fstack-protector"], ov)
    D0 = {"forced": ["-U_FORTIFY_SOURCE", "-fno-stack-clash-protection", "-fno-stack-protector"], "target": ["-fno-PIC", "-fno-PIE"]}
    fo, er = S.for_root(["-O2", "-fPIE"], D0)
    check("for_root: a root that states -fPIE has stated its target choice; the forced flags apply", fo == D0["forced"] and er == [], (fo, er))
    fo, er = S.for_root(["-O2"], D0)
    check("for_root: a target choice left implicit is red (implicit target choice)", len(er) == 2 and all("implicit target choice" in e for e in er), er)
    fo, er = S.for_root(["-fPIE", "-fno-stack-protector", "-D_FORTIFY_SOURCE=2"], D0)
    check("for_root: stating a target option does not free the hardening flags (still refused)", len(er) == 2 and "-fno-stack-protector" in er[0] and "-D_FORTIFY_SOURCE=2" in er[1], er)
    check("norm: either sign, value dropped; option_names reads the driver's report",
          S.norm("-fno-PIE") == "-fPIE" and S.norm("-mabi=ilp32") == "-mabi" and S.option_names("  -mabi=[ilp32|lp64]  x\n  -mno-foo   y\n") == {"-mabi", "-mfoo"})
    # --- the real rule on real compilers
    ref = os.path.join(TR.BASE, "ref")
    wrapper(ref, "cc", CC)
    wrapper(ref, "c++", CXX)
    dv = S.derive(os.path.join(ref, "bin/cc"), "/usr/bin/" + CC, "c")
    fl = dv["forced"] + dv["target"]
    check("derive (distribution cross gcc vs GNU defaults): hardening is forced (stack protector class, _FORTIFY_SOURCE), PIC/PIE is a target choice",
          "-fno-stack-protector" in dv["forced"] and "-fno-stack-clash-protection" in dv["forced"] and "-U_FORTIFY_SOURCE" in dv["forced"]
          and "-fno-PIE" in dv["target"] and not any(f in dv["forced"] for f in ("-fno-PIE", "-fno-PIC")), dv)
    fx = S.derive(os.path.join(ref, "bin/c++"), "/usr/bin/" + CXX, "c++")
    check("derive for the C++ driver: the same classes", "-fno-stack-protector" in fx["forced"] and "-U_FORTIFY_SOURCE" in fx["forced"], fx)
    check("derive is a function of the two compilers: a second run gives the identical list", dv == S.derive(os.path.join(ref, "bin/cc"), "/usr/bin/" + CC, "c"))
    same = S.derive("/usr/bin/" + CC, "/usr/bin/" + CC, "c")
    check("derive of a compiler against itself is empty", same == {"forced": [], "target": []}, same)
    # --- what it is for: a dk-style unit has no __stack_chk_* and no *_chk calls
    d = os.path.join(TR.BASE, "u")
    os.makedirs(d)
    raw = compile_obj("/usr/bin/" + CC, [], d, "raw")
    check("positive control: the distribution compiler by itself builds __stack_chk_fail / *_chk references into the unit",
          "__stack_chk" in raw and "_chk" in raw.replace("__stack_chk", ""), raw)
    neu = compile_obj("/usr/bin/" + CC, dv["forced"], d, "neu")
    check("with the derived flags the unit has no __stack_chk_* and no *_chk symbol", "_chk" not in neu and "__stack_chk" not in neu, neu)
    # --- in the recipe: a guest pin names the reference external; the derived flags enter the unit; a root's own overlap is red
    def refext(dd):
        wrapper(dd, "cc", CC)
        wrapper(dd, "c++", CXX)
    subprocess.run(["git", "init", "-q", "--bare", TR.BASE + "/artifacts.git"], check=True)
    os.environ["ARTIFACTS_URL"] = TR.BASE + "/artifacts.git"
    commit, th = TR.publish("ref", refext)
    pin = {"format": "fnvnx-guest-compiler/1", "external": {"name": "ref", "repo": "fnv-nx-build", "ref": "ref", "commit": commit, "tree_sha256": th, "at": "/ext/ref"},
           "cc": "bin/cc", "cxx": "bin/c++"}
    files = {"toy/a.c": UNIT, "toy/constants.tsv": None, "toy/os.S": None, "verify/guest_compiler.json": json.dumps(pin)}
    OK = {"c": ["-O2", "-fno-asynchronous-unwind-tables", "-fPIE"], "cxx": [], "asm": []}
    rec = TR.mut(link=None, os_layer=[], constants=None, generated=[], include=[TR.ROOT + "/inc"], flags=OK)
    repo = TR.make_repo("sd-ok", rec=rec, files=files)
    res = recipe.build(repo, TR.ROOT, TR.BASE + "/st", TR.BASE + "/o1", ext=TR.BASE + "/ec")
    check("recipe: a distribution-compiler root with a reference pin builds", res["status"] == "green", res.get("failure"))
    ob = next((o["object"] for o in res.get("objects", []) if o["unit"].endswith("a.c")), None)
    check("recipe: the object of the dk-style unit has no __stack_chk_* and no *_chk symbol", ob is not None and "_chk" not in syms(ob) and "__stack_chk" not in syms(ob),
          syms(ob) if ob else res)
    cached = [json.load(open(os.path.join(TR.BASE, "st", "rt", n))) for n in os.listdir(TR.BASE + "/st/rt") if n.startswith("specdefaults-")]
    check("recipe: the derived set is cached once, keyed on environment, drivers, reference pin and derivation code",
          len(cached) == 1 and "-fno-stack-protector" in cached[0]["c"]["forced"] and "-U_FORTIFY_SOURCE" in cached[0]["cxx"]["forced"], cached)
    bad = TR.mut(link=None, os_layer=[], constants=None, generated=[], include=[TR.ROOT + "/inc"], flags=dict(OK, c=OK["c"] + ["-fstack-protector-strong"]))
    resb = recipe.build(TR.make_repo("sd-bad", rec=bad, files=files), TR.ROOT, TR.BASE + "/st", TR.BASE + "/o2", ext=TR.BASE + "/ec")
    check("recipe: a root that sets a flag of the derived family is red at specdefaults, naming it",
          resb["status"] == "red" and resb["failure"]["stage"] == "specdefaults" and "-fstack-protector-strong" in " ".join(resb["failure"]["stderr"]) and "implicit" not in " ".join(resb["failure"]["stderr"]), resb.get("failure"))
    imp = TR.mut(link=None, os_layer=[], constants=None, generated=[], include=[TR.ROOT + "/inc"], flags={"c": ["-O2"], "cxx": [], "asm": []})
    resi = recipe.build(TR.make_repo("sd-implicit", rec=imp, files=files), TR.ROOT, TR.BASE + "/st", TR.BASE + "/o4", ext=TR.BASE + "/ec")
    check("recipe: a root that leaves PIE implicit is red at specdefaults (implicit target choice); stating -fPIE (the ok root above) is green",
          resi["status"] == "red" and resi["failure"]["stage"] == "specdefaults" and "implicit target choice" in " ".join(resi["failure"]["stderr"]), resi.get("failure"))
    nopin = TR.make_repo("sd-nopin", rec=rec, files=dict(files, **{"verify/guest_compiler.json": None}))
    resn = recipe.build(nopin, TR.ROOT, TR.BASE + "/st", TR.BASE + "/o3", ext=TR.BASE + "/ec")
    on = next((o["object"] for o in resn.get("objects", []) if o["unit"].endswith("a.c")), None)
    check("recipe: a tree with no reference pin has nothing to remove (the object keeps the distribution's defaults)",
          resn["status"] == "green" and on is not None and "__stack_chk" in syms(on), resn.get("failure"))
    print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
