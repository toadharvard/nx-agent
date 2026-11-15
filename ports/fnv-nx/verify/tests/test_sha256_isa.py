#!/usr/bin/env python3
"""Tests for the lib/sha256 body selection: the guest root (console_cpu.features has crypto, so the compiler defines __ARM_FEATURE_SHA2 from the generated
-march) compiles the Arm SHA2 body; the host variant (x86) compiles the portable body. The choice is the feature macro, nothing else.
Plain script: PASS/FAIL lines, exit 0 on pass. Needs the pinned compiler (fetched by recipe into the store)."""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
V = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(V)))
sys.path.insert(0, V)
import targets  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " %s" % (detail,)))
    if not ok:
        FAILED.append(name)


SRC = open(os.path.join(os.path.dirname(V), "lib", "sha256", "sha256.c")).read()
check("console_cpu.features holds crypto (the fact the macro comes from)", "crypto" in targets.load()["console_cpu"]["features"])
check("-march of the generated prefix carries +crypto", any(f.startswith("-march=") and "+crypto" in f for f in targets.semantic_prefix(targets.load())))
check("the body is selected by __ARM_FEATURE_SHA2 only", [c.strip() for c in re.findall(r"^#\s*if\w*\s+(.*)", SRC, re.M)] == ["__ARM_FEATURE_SHA2"] * 2)
check("the source spells no -march or runtime detection", not re.search(r"getauxval|HWCAP|-march|__builtin_cpu", SRC))


def objdump_count(spec, tool):
    with tempfile.TemporaryDirectory(prefix="sha-isa-") as d:
        store, ext = os.environ.get("GATE_CACHE", "/tmp/v12/cache"), None
        cmd = [sys.executable, "-I", os.path.join(V, "recipe.py"), "build", "--root", spec, "--store", os.path.join(store, "store"),
               "--ext", os.path.join(store, "external"), "--out", d, "--jobs", "4"]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode:
            return None, r.stdout + r.stderr
        o = os.path.join(d, "obj", "sha256.c.o")
        out = subprocess.run([tool, "-d", o], capture_output=True, text=True)
        return len(re.findall(r"\bsha256(?:h2?|su[01])\b", out.stdout)), out.stderr


g, why = objdump_count("ports/fnv-nx/lib/sha256", "aarch64-linux-gnu-objdump")
check("guest object has sha256h/h2/su0/su1 instructions", g is not None and g >= 4, (g, why[-300:]))
h, why = objdump_count("ports/fnv-nx/lib/sha256@host", "objdump")
check("host object has none (portable body)", h == 0, (h, why[-300:]))
sys.exit(1 if FAILED else 0)
