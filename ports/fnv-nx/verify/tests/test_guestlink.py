#!/usr/bin/env python3
"""Tests for verify/gate_guestlink.py and the derivation helpers of recipe.py: parse_driver_link (the driver's -### line -> profile libs), onedef (the
one-definition exclusion), outside_files and in_multilib. Pure: a canned driver line, no build."""
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


# the driver text is the pinned compiler's own -### output (the external of guest_compiler.json, mounted: FNVNX_EXTCACHE or fetched), never a canned line
import json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
PIN = json.load(open(os.path.join(HERE, "..", "guest_compiler.json")))["external"]
EXTC = os.environ.get("FNVNX_EXTCACHE") or tempfile.mkdtemp(prefix="glx-")
AT = recipe.fetch_externals([PIN], tempfile.mkdtemp(prefix="gls-"), EXTC, tempfile.mkdtemp(prefix="glw-"))[PIN["name"]]["host"]
DRV = os.path.join(AT, recipe.pin_drivers()[1])
FL = recipe.semantics_for(DRV, "cxx", recipe.SEMANTICS_FLAGS)
_d = tempfile.mkdtemp(prefix="glp-")
open(os.path.join(_d, "x.cc"), "w").write(recipe.PROBE_SRC)
TEXT = subprocess.run([DRV] + FL + ["-pie", "-###", os.path.join(_d, "x.cc"), "-o", "/out/x.elf"], capture_output=True, text=True, check=True).stderr
ML = subprocess.run([DRV] + FL + ["-print-multi-directory"], capture_output=True, text=True, check=True).stdout.strip()
raw = recipe.parse_driver_link(TEXT, AT)
rel = lambda ns, xs: all(any(os.path.basename(x) == n for x in xs) for n in ns)  # noqa: E731
check("startfiles: the .o files before the library group, normalised and relative to the external, crt0/crti/crtbegin among them",
      rel(["crt0.o", "crti.o", "crtbegin.o"], raw["startfiles"]) and all(not os.path.isabs(x) for x in raw["startfiles"]), raw["startfiles"])
check("endfiles: the .o files after --end-group (crtend, crtn)", rel(["crtend.o", "crtn.o"], raw["endfiles"]), raw["endfiles"])
check("dirs: -L inside the external, each once, in order", raw["dirs"] and len(set(raw["dirs"])) == len(raw["dirs"]) and all(os.path.isdir(os.path.join(AT, d)) for d in raw["dirs"]), raw["dirs"])
check("names: the -l libraries in order of appearance, the group's included", all(n in raw["names"] for n in ("stdc++", "m", "gcc", "c")), raw["names"])
check("the object of the program (/tmp/ccx.o) is outside the external: not a runtime file", all("ccx" not in x for x in raw["startfiles"] + raw["endfiles"]))
check("the link is the last command line; a COLLECT_GCC_OPTIONS line after it is not a command", recipe.parse_driver_link("junk\n" + TEXT + "COLLECT_GCC_OPTIONS='-pie' '-o' '/out/x.elf'\n\n", AT) == raw)

o = {"a.o": {"_start", "x"}, "b.o": {"y"}, "c.o": set(), "d.o": {"__fnvnx_guest_start"}}
check("onedef: the objects that define a symbol a root defines, sorted", recipe.onedef(o, {"_start", "__fnvnx_guest_start"}) == ["a.o", "d.o"], recipe.onedef(o, {"_start"}))
check("onedef: no symbol shared, nothing is excluded (no name decides)", recipe.onedef(o, {"zzz"}) == [], "")
check("onedef: a weak definition counts as a definition (the caller passes global and weak symbols)", recipe.onedef({"p.o": {"_cpu_init_hook"}}, {"_cpu_init_hook"}) == ["p.o"], "")

crt = {"crt0.o": {"_start", "__libc_start_main"}, "crti.o": set(), "crtbegin.o": {"__do_global_dtors_aux"}}
check("entry_object: ENTRY names another symbol, the startfile that defines _start is the runtime's own process start and is excluded",
      recipe.entry_object(crt, "__fnvnx_guest_start") == ["crt0.o"], recipe.entry_object(crt, "__fnvnx_guest_start"))
check("entry_object: the default entry _start keeps it", recipe.entry_object(crt, "_start") == [], "")

check("outside_files: every .o of the link under the runtime external (the program's /tmp/cc*.o excepted): none", recipe.outside_files(TEXT, AT) == [], recipe.outside_files(TEXT, AT))
check("outside_files: a start file of another place is reported", recipe.outside_files(TEXT.replace([x for x in TEXT.split() if x.strip('"').endswith("/crt0.o")][0].strip('"'), "/usr/lib/crt1.o"), AT) == ["/usr/lib/crt1.o"], "")
check("the pinned link is all in the multilib the flags select (-print-multi-directory %r) and under the external" % ML,
      recipe.in_multilib([x for k in ("startfiles", "endfiles") for x in raw[k]] + raw["dirs"][:2], ML) == [] and recipe.outside_files(TEXT, AT) == [], (ML, raw))
SF = [x for x in raw["startfiles"] if os.path.basename(x) == "crtbegin.o"][0]
check("in_multilib: the pinned crtbegin is in the multilib directory the flags select", recipe.in_multilib([SF], ML) == [], (SF, ML))
OTHER = SF.replace(ML, "zz/other") if ML not in (".", "") else "lib/zz/other/crtbegin.o"
check("in_multilib: a crtbegin of another multilib directory is reported", ML in (".", "") or recipe.in_multilib([OTHER], ML) == [OTHER], (OTHER, ML))
check("in_multilib: the default multilib (.) has no directory to hold", recipe.in_multilib(["lib/crtbegin.o"], ".") == [], "")

print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
