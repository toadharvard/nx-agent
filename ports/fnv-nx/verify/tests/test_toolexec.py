#!/usr/bin/env python3
"""Tool step `exec` forms {external, path} and {root, output}, the static ELF resolution and `env` (RECIPE.md "A tool that is not a deb binary").
Pure checks (no sandbox, no busybox):
  - elf_dyn reads PT_INTERP and DT_NEEDED from the program headers (a real dynamic ELF of this host; a header-only ELF has none);
  - exec_unresolved is empty when the declared debs' root layout provides the interpreter and every NEEDED name, and names `interp:<path>` / the library
    otherwise (the deb's own symlinks followed); an ELF with no dynamic section and a script have nothing to resolve;
  - recipe.check: red for an exec external that the recipe does not declare, a path outside its tree, a root/output that is not in `uses`, timeout_s,
    an env that holds PATH, a non-constant value or a path outside the step's scratch; green for the same forms done right.
Run: python3 -I <this file>."""
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


host = shutil.which("true")
dyn = recipe.elf_dyn(host)
check("a dynamic host ELF has an interpreter and NEEDED libc", dyn is not None and dyn[0] and "libc.so.6" in dyn[1], dyn)
with tempfile.TemporaryDirectory() as td:
    bare = os.path.join(td, "bare")
    open(bare, "wb").write(b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8) + struct.pack("<HHIQQQIHHHHHH", 2, 0xb7, 1, 0, 64, 0, 0, 64, 56, 0, 0, 0, 0))
    check("an ELF without program headers has nothing to resolve", recipe.elf_dyn(bare) == (None, [], []) and recipe.exec_unresolved(bare, td, []) == [])
    script = os.path.join(td, "s")
    open(script, "w").write("#!/bin/sh\n")
    check("a script is not an ELF64", recipe.elf_dyn(script) is None and recipe.exec_unresolved(script, td, []) == [])
    interp, need, _ = dyn
    d = os.path.join(td, "deb-libs")
    os.makedirs(os.path.join(d, "usr/lib64"))
    os.makedirs(os.path.join(d, "usr/lib/x86_64-linux-gnu"))
    os.symlink("usr/lib64", os.path.join(d, "lib64"))
    os.symlink("usr/lib", os.path.join(d, "lib"))
    for n in need:
        open(os.path.join(d, "usr/lib/x86_64-linux-gnu", n), "w").close()
    check("the interpreter is missing: red interp:<path>", recipe.exec_unresolved(host, td, ["libs"]) == ["interp:" + interp], recipe.exec_unresolved(host, td, ["libs"]))
    open(os.path.join(d, "usr/lib64", os.path.basename(interp)), "w").close()
    check("interpreter (through the deb's own /lib64 link) and libraries provided: nothing unresolved", recipe.exec_unresolved(host, td, ["libs"]) == [], recipe.exec_unresolved(host, td, ["libs"]))
    os.remove(os.path.join(d, "usr/lib/x86_64-linux-gnu", "libc.so.6"))
    check("a NEEDED library no deb provides is named", recipe.exec_unresolved(host, td, ["libs"]) == ["libc.so.6"], recipe.exec_unresolved(host, td, ["libs"]))

R = "ports/fnv-nx/tools/x"
ext = [{"name": "isla", "repo": "toadharvard/fnv-nx-build", "commit": "0" * 40, "tree_sha256": "0" * 64}]


def errs(step, uses=()):
    rec = {"format": "fnvnx-recipe/1", "external": ext, "uses": list(uses), "outputs": {"o": "o.txt"},
           "run": [dict({"name": "one", "args": [], "inputs": [], "debs": ["toybb"], "outputs": ["o.txt"], "scratch": ["/scratch"]}, **step)]}
    recipe.deb_pins = lambda: {n: {"version": "1", "sha256": "0" * 64} for n in ("toybb", "base-files")}
    return [e for e in recipe.check(rec, R, {R + "/build.json"}, {R: {"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}}) if e.startswith("run[0]")]


check("exec external green", errs({"exec": {"external": "isla", "path": "bin/isla"}}) == [], errs({"exec": {"external": "isla", "path": "bin/isla"}}))
check("exec external undeclared is red", any("external of this recipe" in e for e in errs({"exec": {"external": "nope", "path": "bin/x"}})))
check("exec external path outside the tree is red", any("external of this recipe" in e for e in errs({"exec": {"external": "isla", "path": "../x"}})))
check("exec root/output not in uses is red", any("`uses`" in e for e in errs({"exec": {"root": "ports/fnv-nx/a", "output": "b"}})))
check("exec root/output in uses is green", errs({"exec": {"root": "ports/fnv-nx/a", "output": "b"}}, [{"root": "ports/fnv-nx/a", "output": "b"}]) == [], errs({"exec": {"root": "ports/fnv-nx/a", "output": "b"}}, [{"root": "ports/fnv-nx/a", "output": "b"}]))
check("timeout_s is red", any("timeout_s" in e for e in errs({"exec": "/usr/bin/x", "timeout_s": 5})))
check("env with PATH is red", any("env must" in e for e in errs({"exec": "/usr/bin/x", "env": {"PATH": "/x"}})))
check("env with a path outside scratch is red", any("scratch" in e for e in errs({"exec": "/usr/bin/x", "env": {"CARGO_HOME": "/home/x"}})))
check("env constants and a scratch path are green", errs({"exec": "/usr/bin/x", "env": {"CARGO_HOME": "/scratch/cargo", "RUST_BACKTRACE": "0"}}) == [], errs({"exec": "/usr/bin/x", "env": {"CARGO_HOME": "/scratch/cargo"}}))
prof = recipe.deb_profiles().get("compiler-build")
check("debs profile: the tops of env.json debs.profiles, same as the written closure form", prof and recipe.step_debs({"debs": {"profile": "compiler-build"}}, closure=False) == recipe.step_debs({"debs": {"closure": prof}}, closure=False), prof)
try:
    recipe.step_debs({"debs": {"profile": "nope"}}, closure=False)
    check("debs profile: an unknown profile is red", False)
except ValueError as e:
    check("debs profile: an unknown profile is red", "debs.profile" in str(e), str(e))
sys.exit(1 if fails else 0)
