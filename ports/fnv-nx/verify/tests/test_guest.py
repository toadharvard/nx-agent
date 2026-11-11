#!/usr/bin/env python3
"""Tests for the guest-root rule of verify/recipe.py: a product root with "target": "guest" is compiled, C and C++, by the external compiler of
verify/guest_compiler.json (GCC with msvcmodel.patch, toolchain/msvcmodel), the plugins are built against ITS plugin headers, and the whole
chain is deterministic and cached. The compiler prefix is built by toolchain/msvcmodel/build.sh; FNVNX_TEST_GCC_PREFIX names it (the official
mirror is fnv-nx-build tools/gcc-ilp32-msvcmodel). Without it the cases that need the compiler print SKIP (counted, never PASS).
Plain script, needs root (sandbox) and the compilers of verify/env.json."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import test_plugins as tp
from test_plugins import BASE, ROOT, T, REC, PLUGS, SRC, check, git, w, make_repo, errs, build, disasm, mut, recipe, cas, REAL, VERIFY

PREFIX = os.environ.get("FNVNX_TEST_GCC_PREFIX")
SKIPPED = []


def skip(name):
    SKIPPED.append(name)
    print("SKIP %s: FNVNX_TEST_GCC_PREFIX not set" % name, flush=True)


def external_pack(prefix):
    """The compiler prefix as a fnv-nx-build style commit in a local repo -> (repo, commit, tree_sha256)."""
    repo = os.path.join(BASE, "gccpack")
    shutil.copytree(prefix, repo, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "gcc")
    commit = git(repo, "rev-parse", "HEAD").strip()
    out = os.path.join(BASE, "gccpack-tree")
    subprocess.run(["git", "-C", repo, "checkout-index", "-a", "-f", "--prefix=" + out + "/"], check=True)
    return repo, commit, cas.ingest(out, os.path.join(BASE, "ingest-store"))


def guest_rec(ext, **kw):
    r = mut(compiler={"external": "gcc-msvcmodel", "cc": "bin/aarch64-linux-gnu-gcc", "cxx": "bin/aarch64-linux-gnu-g++"}, external=[ext], **kw)
    return r


def guest_repo(name, rec, pin, target="guest", files=None):
    repo = make_repo(name, rec, "native", files)
    ad = json.load(open(os.path.join(repo, T + "verify/admit.json")))
    for r in ad["roots"]:
        if r["path"] == ROOT:
            if target is None:
                r.pop("target", None)
            else:
                r["target"] = target
    w(repo, T + "verify/admit.json", json.dumps(ad))
    if pin is not None:
        w(repo, T + "verify/guest_compiler.json", json.dumps(pin))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "guest")
    return repo


def main():
    ext = {"name": "gcc-msvcmodel", "repo": "fnv-nx-build", "ref": "tools/gcc-ilp32-msvcmodel", "commit": "a" * 40, "tree_sha256": "b" * 64, "at": "/ext/gcc"}
    pin = {"format": "fnvnx-guest-compiler/1", "external": ext, "cc": "bin/aarch64-linux-gnu-gcc", "cxx": "bin/aarch64-linux-gnu-g++"}
    rec = guest_rec(ext)
    # --- the rule, validation only (no compiler needed)
    g = guest_repo("g1", rec, pin)
    check("guest root on the pinned external compiler is valid", errs(g, rec) == [] or [x for x in errs(g, rec) if "guest" in x] == [], errs(g, rec))
    stock = guest_repo("g2", REC, pin)
    check("guest root on the stock g++-13 is red", any("must be the external compiler" in x for x in errs(stock, REC)), errs(stock, REC))
    other = dict(ext, commit="c" * 40)
    check("guest root on another commit of the external is red", any("not the pinned one" in x for x in errs(g, guest_rec(other))))
    check("guest root on another tree hash is red", any("not the pinned one" in x for x in errs(g, guest_rec(dict(ext, tree_sha256="d" * 64)))))
    r2 = guest_rec(ext)
    r2["compiler"]["cxx"] = "bin/other-g++"
    check("guest root on another driver path is red", any("compiler.cxx" in x for x in errs(g, r2)))
    nopin = guest_repo("g3", rec, None)
    check("guest root without guest_compiler.json is red", any("guest_compiler.json" in x for x in errs(nopin, rec)), errs(nopin, rec))
    notarget = guest_repo("g4", REC, pin, target=None)
    check("product root without target is red", any('"target"' in x for x in errs(notarget, REC)), errs(notarget, REC))
    host = guest_repo("g5", REC, pin, target="host")
    check("host root may use the distribution compiler", not [x for x in errs(host, REC) if "guest" in x or "target" in x], errs(host, REC))
    # --- the real compiler
    if not PREFIX:
        for n in ("guest root builds on the external compiler", "plugins are built against the external compiler's headers, .so deterministic",
                  "msvcmodel data model (size_t is unsigned int)", "test_msvcmodel.py on the external", "cold and warm build"):
            skip(n)
        return
    repo, commit, tree = external_pack(PREFIX)
    os.environ["ARTIFACTS_URL"] = repo
    ext = dict(ext, commit=commit, tree_sha256=tree)
    pin = dict(pin, external=ext)
    rec = guest_rec(ext)
    src = SRC + "void sz(decltype(sizeof 0) n) { (void)n; }\n"
    gr = guest_repo("gb", rec, pin, files={ROOT + "/a.cc": src})
    check("guest root with the real pin is valid", errs(gr, rec) == [], errs(gr, rec))
    ra, ta = build(gr, "GA")
    check("guest root builds on the external compiler", ra["status"] == "green", ra.get("failure"))
    if ra["status"] != "green":
        return
    rb, tb = build(gr, "GB")
    rw, tw = build(gr, "GA", os.path.join(BASE, "out-GA2"))
    so = lambda r: {p["name"]: p["sha256"] for p in r["plugins"]}  # noqa: E731
    check("plugins are built against the external compiler's headers, .so deterministic (two cold stores)", so(ra) == so(rb) and len(so(ra)) == 2, (so(ra), so(rb)))
    check("cold and warm build: warm runs no stage, objects equal", rw["stages"]["run"] == 0 and [o["sha256"] for o in rw["objects"]] == [
        o["sha256"] for o in ra["objects"]] == [o["sha256"] for o in rb["objects"]], (rw["stages"], ra["stages"]))
    print("TIME guest cold A %.1fs, cold B %.1fs, warm %.1fs" % (ta, tb, tw))
    o = next(x["object"] for x in ra["objects"] if x["unit"] == "a.cc")
    syms = subprocess.run(["aarch64-linux-gnu-nm", o], capture_output=True, text=True).stdout
    check("msvcmodel data model: size_t is unsigned int (mangled j)", "_Z2szj" in syms, syms)
    check("volatile is ldar/stlr and float -> int is _ftol on the external compiler", "ldar" in disasm(ra, "_Z2rdv") and "stlr" in disasm(ra, "_Z2wri")
          and "#0x80000000" in disasm(ra, "_Z3f2if"))
    t = os.path.join(os.path.dirname(VERIFY), "toolchain", "msvcmodel", "test_msvcmodel.py")
    p = subprocess.run([sys.executable, "-I", t], env=dict(os.environ, CXX=os.path.join(PREFIX, "bin/aarch64-linux-gnu-g++")), capture_output=True, text=True)
    check("test_msvcmodel.py on the external", p.returncode == 0 and "skip" not in p.stdout, p.stdout + p.stderr)


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed (%d skipped)" % len(SKIPPED))
    sys.exit(1 if tp.FAILED else 0)
