#!/usr/bin/env python3
"""Tests for the guest-root rule of verify/recipe.py: a product root with "target": "guest" is compiled, C and C++, by the external compiler of
verify/guest_compiler.json (GCC with msvcmodel.patch, toolchain/msvcmodel), the plugins are built against ITS plugin headers, and the whole
chain is deterministic and cached. The compiler is fetched by the pin of verify/guest_compiler.json (exact commit of fnv-nx-build from
ARTIFACTS_URL, tree hash checked); when it cannot be fetched the real-compiler cases are red with the reason, never skipped.
Plain script, needs root (sandbox) and the compilers of verify/env.json."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_plugins as tp
from test_plugins import BASE, ROOT, T, REC, PLUGS, SRC, check, git, w, make_repo, errs, build, disasm, mut, recipe, cas, REAL, VERIFY

def guest_rec(ext, rt, **kw):
    """A guest recipe: the compiler external, the runtime external, -nostdinc(++), -Werror=attributes and the include dirs of the compiler's own headers and the runtime's."""
    r = mut(compiler={"external": ext["name"], "cc": "bin/aarch64-linux-gnu-gcc", "cxx": "bin/aarch64-linux-gnu-g++"}, external=[ext, rt],
            include=[ext["at"] + "/lib/gcc/aarch64-linux-gnu/13/include", rt["at"] + "/include"], **kw)
    r["flags"] = {"c": ["-nostdinc"], "cxx": ["-nostdinc", "-nostdinc++"] + r["flags"]["cxx"], "asm": ["-nostdinc"]}
    if "flags" not in kw:   # every guest root carries -Werror=attributes in flags.c and flags.cxx
        r["flags"] = dict(r["flags"], **{k: list(r["flags"][k]) + [recipe.WERROR_ATTR] for k in ("c", "cxx")})
    return r


def copy_rec(r):
    return json.loads(json.dumps(r))


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
    pin_real = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
    ext = {"name": "gcc-msvcmodel", "repo": "fnv-nx-build", "ref": "tools/gcc-ilp32-msvcmodel", "commit": "a" * 40, "tree_sha256": "b" * 64, "at": "/ext/gcc"}
    rt = {"name": "rt", "repo": "fnv-nx-build", "ref": "tools/rt", "commit": "c" * 40, "tree_sha256": "d" * 64, "at": "/ext/rt"}
    pin = dict(pin_real, external=ext, runtime=rt)
    rec = guest_rec(ext, rt)
    # --- the rule, validation only (no compiler needed)
    g = guest_repo("g1", rec, pin)
    check("guest root on the pinned compiler and runtime is valid", errs(g, rec) == [], errs(g, rec))
    stock = guest_repo("g2", REC, pin)
    check("guest root on the stock g++-13 is red", any("must be the external compiler" in x for x in errs(stock, REC)), errs(stock, REC))
    check("guest root on another commit of the external is red", any("not the pinned one" in x for x in errs(g, guest_rec(dict(ext, commit="e" * 40), rt))))
    check("guest root on another tree hash is red", any("not the pinned one" in x for x in errs(g, guest_rec(dict(ext, tree_sha256="f" * 64), rt))))
    r2 = guest_rec(ext, rt)
    r2["compiler"]["cxx"] = "bin/other-g++"
    check("guest root on another driver path is red", any("compiler.cxx" in x for x in errs(g, r2)))
    nopin = guest_repo("g3", rec, None)
    check("guest root without guest_compiler.json is red", any("guest_compiler.json" in x for x in errs(nopin, rec)), errs(nopin, rec))
    notarget = guest_repo("g4", REC, pin, target=None)
    check("product root without target is red", any('"target"' in x for x in errs(notarget, REC)), errs(notarget, REC))
    host = guest_repo("g5", REC, pin, target="host")
    check("host root may use the distribution compiler", not [x for x in errs(host, REC) if "guest" in x or "target" in x], errs(host, REC))
    check("guest root with -Werror=attributes in flags.c and flags.cxx has no attribute-flag error", not [x for x in errs(g, rec) if "attributes" in x], errs(g, rec))
    for k in ("c", "cxx"):
        bad = guest_rec(ext, rt)
        bad["flags"][k] = [f for f in bad["flags"][k] if f != recipe.WERROR_ATTR]
        check("guest root without -Werror=attributes in flags.%s is invalid" % k, any("flags.%s lacks %s" % (k, recipe.WERROR_ATTR) in x for x in errs(g, bad)), errs(g, bad))
    for f in ("-fpermissive", "-w", "-Wno-error", "-Wno-error=attributes"):
        for k in ("c", "cxx"):
            bad = guest_rec(ext, rt)
            bad["flags"][k] = bad["flags"][k] + [f]
            check("guest root with %s in flags.%s is invalid" % (f, k), any("flags.%s: %s downgrades" % (k, f) in x for x in errs(g, bad)), errs(g, bad))
    check("host root may carry -fpermissive", not [x for x in errs(host, dict(REC, flags=dict(REC["flags"], cxx=REC["flags"]["cxx"] + ["-fpermissive"]))) if "downgrades" in x])
    check("host root needs no -Werror=attributes", not [x for x in errs(host, REC) if "attributes" in x], errs(host, REC))
    # --- the runtime external: declared exactly, system headers only from it
    no_rt = copy_rec(rec)
    no_rt["external"] = [ext]
    check("guest root without the runtime external is red", any("must be declared exactly as pinned" in x for x in errs(g, no_rt)), errs(g, no_rt))
    check("guest root with another commit of the runtime is red", any("must be declared exactly" in x for x in errs(g, guest_rec(ext, dict(rt, commit="9" * 40)))))
    ns = copy_rec(rec)
    ns["flags"]["cxx"].remove("-nostdinc++")
    check("guest root without -nostdinc++ is red", any("lacks -nostdinc++" in x for x in errs(g, ns)), errs(g, ns))
    ns = copy_rec(rec)
    ns["flags"]["c"] = []
    check("guest root without -nostdinc in flags.c is red", any("flags.c lacks -nostdinc" in x for x in errs(g, ns)), errs(g, ns))
    gi = copy_rec(rec)
    gi["include"] = gi["include"] + ["/usr/aarch64-linux-gnu/include"]
    check("guest root with a glibc include dir is red", any("neither inside a declared external" in x for x in errs(g, gi)), errs(g, gi))
    # --- the manifest of the runtime against the guest compiler's own (pure rule)
    good = good_manifest(pin_real)
    check("manifest equal to the guest compiler's: no error", recipe.runtime_errors(json.dumps(good), pin_real) == [])
    for name, mut_ in (("compiler 15.2.0", {"compiler_version": "15.2.0"}), ("4-byte wchar_t", {"sizeof": {"wchar_t": 4, "size_t": 4, "long": 4}}),
                       ("another patch", {"msvcmodel_patch_sha256": "0" * 64}), ("no -fshort-wchar", {"flags": [f for f in recipe.SEMANTICS_FLAGS if f != "-fshort-wchar"]})):
        e = recipe.runtime_errors(json.dumps(dict(good, **mut_)), pin_real)
        check("manifest with %s is red" % name, bool(e), e)
    check("manifest that is not JSON is red", bool(recipe.runtime_errors("x", pin_real)))
    # --- the real compiler, by the pin of the checkout (no environment switch, no skip)
    ext = pin_real["external"]
    rt = pin_real["runtime"]
    prefix = None
    try:
        got = recipe.fetch_externals([ext], os.path.join(BASE, "ingest-store"), os.path.join(BASE, "extcache"), BASE)
        prefix = got[ext["name"]]["host"]
        check("the pinned external is fetched by its commit and its tree hash matches", True)
    except recipe.BuildRed as x:
        check("the pinned external is fetched by its commit and its tree hash matches", False, x.args)
        return
    # the pinned runtime as the mirror holds it: its manifest must be there and equal the guest compiler's own
    sample = json.load(open(os.path.join(REAL, T + "verify/samples/platform.build.json")))
    gs = guest_repo("gs", sample, pin_real, files={ROOT + "/a.cc": SRC})
    check("sample platform build.json is accepted by recipe check", errs(gs, sample) == [], errs(gs, sample))
    rr, _ = build(gs, "GR")
    f = rr.get("failure") or {}
    check("the pinned runtime is judged by its manifest: green only if it states the guest compiler's version, patch, flags and data model",
          (rr["status"] == "green") == (f.get("stage") != "runtime_manifest") and (rr["status"] == "green" or f.get("stage") == "runtime_manifest"), f)
    print("INFO pinned runtime %s: %s %s" % (rt["commit"][:8], rr["status"], "; ".join(f.get("stderr", []))[:300]))
    rt2, pin2, work, art = valid_runtime(ext, pin_real, good)
    rec = guest_rec(ext, rt2)
    src = SRC + "void sz(decltype(sizeof 0) n) { (void)n; }\n#include <sys/socket.h>\nfnvnx_t use() { return sock(); }\n"
    gr = guest_repo("gb", rec, pin2, files={ROOT + "/a.cc": src})
    check("guest root with the real compiler and a valid runtime is valid", errs(gr, rec) == [], errs(gr, rec))
    ra, ta = build(gr, "GA")
    check("guest root builds; <sys/socket.h> finds the runtime's sys/_types.h (no glibc header)", ra["status"] == "green", ra.get("failure"))
    if ra["status"] != "green":
        return
    gl = guest_repo("gl", rec, pin2, files={ROOT + "/a.cc": src, ROOT + "/glibc.cc": "#include </usr/aarch64-linux-gnu/include/bits/wordsize.h>\nint g() { return 1; }\n"})
    rl, _ = build(gl, "GL")
    check("a unit that reads a glibc cross header is red, stage closure naming unit and file", rl["status"] == "red" and rl["failure"]["stage"] == "closure"
          and any("glibc.cc" in x and "/usr/aarch64-linux-gnu/include" in x for x in rl["failure"]["stderr"]), rl.get("failure"))
    bad = dict(good, compiler_version="15.2.0")
    w(work, "MANIFEST.json", json.dumps(bad))
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "bad")
    git(work, "push", "-q", art, "HEAD:refs/heads/rt-bad")
    rt3 = dict(rt2, commit=git(work, "rev-parse", "HEAD").strip(), tree_sha256=cas.ingest(work_checkout(work), os.path.join(BASE, "ingest-store3")))
    gm = guest_repo("gm", guest_rec(ext, rt3), dict(pin_real, runtime=rt3), files={ROOT + "/a.cc": SRC})
    rm, _ = build(gm, "GM")
    check("a runtime whose manifest names compiler 15.2.0 is red, stage runtime_manifest", rm["status"] == "red" and rm["failure"]["stage"] == "runtime_manifest", rm.get("failure"))
    rb, tb = build(gr, "GB")
    rw, tw = build(gr, "GA", os.path.join(BASE, "out-GA2"))
    so = lambda r: {p["name"]: p["sha256"] for p in r["plugins"]}  # noqa: E731
    check("plugins are built against the external compiler's headers, .so deterministic (two cold stores)", so(ra) == so(rb) and len(so(ra)) == len(recipe.PLUGIN_SOURCES), (so(ra), so(rb)))
    check("cold and warm build: warm runs no stage, objects equal", rw["stages"]["run"] == 0 and [o["sha256"] for o in rw["objects"]] == [
        o["sha256"] for o in ra["objects"]] == [o["sha256"] for o in rb["objects"]], (rw["stages"], ra["stages"]))
    print("TIME guest cold A %.1fs, cold B %.1fs, warm %.1fs" % (ta, tb, tw))
    o = next(x["object"] for x in ra["objects"] if x["unit"] == "a.cc")
    syms = subprocess.run(["aarch64-linux-gnu-nm", o], capture_output=True, text=True).stdout
    check("msvcmodel data model: size_t is unsigned int (mangled j)", "_Z2szj" in syms, syms)
    check("volatile is ldar/stlr and float -> int is _ftol on the external compiler", "ldar" in disasm(ra, "_Z2rdv") and "stlr" in disasm(ra, "_Z2wri")
          and "#0x80000000" in disasm(ra, "_Z3f2if"))
    t = os.path.join(os.path.dirname(VERIFY), "toolchain", "msvcmodel", "test_msvcmodel.py")
    p = subprocess.run([sys.executable, "-I", t], env=dict(os.environ, CXX=os.path.join(prefix, "bin/aarch64-linux-gnu-g++")), capture_output=True, text=True)
    check("test_msvcmodel.py on the external", p.returncode == 0 and "skip" not in p.stdout, p.stdout + p.stderr)


def good_manifest(pin_real):
    """the manifest a runtime pack must carry: the guest compiler's own version, patch, semantics flags and data model"""
    return {"format": "fnvnx-runtime-manifest/1", "compiler_version": "13.3.0", "msvcmodel_patch_sha256": pin_real["runtime_manifest"]["msvcmodel_patch_sha256"],
            "flags": list(recipe.SEMANTICS_FLAGS), "sizeof": {"wchar_t": 2, "size_t": 4, "long": 4}}


def valid_runtime(ext, pin_real, good):
    """A runtime pack with a valid manifest GOOD in a local artifacts repository (next to the real compiler commit); sets ARTIFACTS_URL to it.
    -> (rt2 external entry, pin2, work dir, artifacts repo)"""
    rt = pin_real["runtime"]
    art = os.path.join(BASE, "artifacts.git")
    work = os.path.join(BASE, "rtpack")
    subprocess.run(["git", "init", "-q", "--bare", art], check=True)
    subprocess.run(["git", "-C", art, "fetch", "-q", "--depth", "1", os.environ.get("ARTIFACTS_URL", recipe.ARTIFACTS_URL), ext["commit"]], check=True)
    w(work, "MANIFEST.json", json.dumps(good))
    w(work, "include/sys/_types.h", "typedef unsigned int fnvnx_t;\n")
    w(work, "include/sys/socket.h", "#include <sys/_types.h>\ninline fnvnx_t sock() { return 1; }\n")
    git(work, "init", "-q")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "rt")
    git(work, "push", "-q", art, "HEAD:refs/heads/rt")
    rt2 = dict(rt, name="rtpack", ref="rt", commit=git(work, "rev-parse", "HEAD").strip(), tree_sha256=cas.ingest(work_checkout(work), os.path.join(BASE, "ingest-store2")), at="/ext/rt")
    pin2 = dict(pin_real, runtime=rt2)
    os.environ["ARTIFACTS_URL"] = art
    return rt2, pin2, work, art


def work_checkout(work):
    """The committed tree of WORK checked out into a clean directory (what fetch_externals ingests)."""
    out = work + "-co-" + os.urandom(3).hex()
    subprocess.run(["git", "-C", work, "checkout-index", "-a", "-f", "--prefix=" + out + "/"], check=True)
    return out


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
