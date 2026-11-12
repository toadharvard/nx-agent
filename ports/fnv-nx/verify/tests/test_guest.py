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



def guest_rec(ext, **kw):
    r = mut(compiler={"external": "gcc-msvcmodel", "cc": "bin/aarch64-linux-gnu-gcc", "cxx": "bin/aarch64-linux-gnu-g++"}, external=[ext], **kw)
    if "flags" not in kw:   # every guest root carries -Werror=attributes in flags.c and flags.cxx
        # a guest build.json holds only per-root flags (recipe.PER_ROOT); every semantic flag comes from the pin and is appended by the recipe
        r["flags"] = {"c": ["-O2", recipe.WERROR_ATTR], "cxx": ["-O2", recipe.SDD_DEFINE, recipe.WERROR_ATTR], "asm": []}
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
    pin_real = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
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
    check("guest root with -Werror=attributes in flags.c and flags.cxx has no attribute-flag error", not [x for x in errs(g, rec) if "attributes" in x], errs(g, rec))
    for k in ("c", "cxx"):
        bad = guest_rec(ext)
        bad["flags"][k] = [f for f in bad["flags"][k] if f != recipe.WERROR_ATTR]
        check("guest root without -Werror=attributes in flags.%s is invalid" % k, any("flags.%s lacks %s" % (k, recipe.WERROR_ATTR) in x for x in errs(g, bad)), errs(g, bad))
    for f in ("-fpermissive", "-w", "-Wno-error", "-Wno-error=attributes"):
        for k in ("c", "cxx"):
            bad = guest_rec(ext)
            bad["flags"][k] = bad["flags"][k] + [f]
            check("guest root with %s in flags.%s is invalid" % (f, k), any("flags.%s: %s downgrades" % (k, f) in x for x in errs(g, bad)), errs(g, bad))
    check("host root may carry -fpermissive", not [x for x in errs(host, dict(REC, flags=dict(REC["flags"], cxx=REC["flags"]["cxx"] + ["-fpermissive"]))) if "downgrades" in x])
    check("host root needs no -Werror=attributes", not [x for x in errs(host, REC) if "attributes" in x], errs(host, REC))
    # --- the runtime external (guest libc/libstdc++) and the sample build.json of the platform root
    rtp = pin_real["runtime"]
    sample = json.load(open(os.path.join(REAL, T + "verify/samples/platform.build.json")))
    gs = guest_repo("gs", sample, pin_real, files={ROOT + "/a.cc": SRC})
    check("sample platform build.json is accepted by recipe check", errs(gs, sample) == [], errs(gs, sample))
    bad_rt = json.loads(json.dumps(sample))
    bad_rt["external"] = [x if x["name"] != rtp["name"] else dict(x, commit="e" * 40) for x in bad_rt["external"]]
    check("a recipe whose runtime external is not the pinned one is red", any("not the pinned runtime" in x for x in errs(gs, bad_rt)), errs(gs, bad_rt))
    no_rt = json.loads(json.dumps(sample))
    no_rt["external"] = [x for x in no_rt["external"] if x["name"] != rtp["name"]]
    no_rt["include"] = []
    check("a guest root may leave the runtime out", not [x for x in errs(gs, no_rt) if "runtime" in x], errs(gs, no_rt))
    # --- the real compiler, by the pin of the checkout (no environment switch, no skip)
    pin = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
    ext = pin["external"]
    rec = guest_rec(ext)
    prefix = os.path.join(BASE, "mirror")
    try:
        got = recipe.fetch_externals([ext], os.path.join(BASE, "ingest-store"), os.path.join(BASE, "extcache"), BASE)
        prefix = got[ext["name"]]["host"]
        check("the pinned external is fetched by its commit and its tree hash matches", True)
    except recipe.BuildRed as x:
        check("the pinned external is fetched by its commit and its tree hash matches", False, x.args)
        return
    src = SRC + "void sz(decltype(sizeof 0) n) { (void)n; }\n"
    gr = guest_repo("gb", rec, pin, files={ROOT + "/a.cc": src})
    check("guest root with the real pin is valid", errs(gr, rec) == [], errs(gr, rec))
    check("the pin's semantic set has the ABI, FP and outline-atomics flags", all(f in recipe.SEMANTICS_FLAGS for f in (
        "-mabi=ilp32", "-fPIC", "-ftls-model=local-exec", "-ffp-contract=off", "-frounding-math", "-fshort-wchar", "-mno-outline-atomics", "-ffixed-x19")), recipe.SEMANTICS_FLAGS)
    # a guest build.json carries no semantic flag: refused in each flag list, whatever it is (an allow-list of the per-root classes, no list of semantic flags)
    for f in ("-mabi=ilp32", "-march=armv8-a", "-mtune=cortex-a57", "-fPIC", "-fPIC", "-ftls-model=initial-exec", "-fsigned-char", "-funsigned-char", "-frounding-math",
              "-fshort-wchar", "-mno-outline-atomics", "-moutline-atomics", "-ffast-math", "-fno-exceptions", "-fno-devirtualize", "-Ofast", "-Wa,-mfoo", "-Wl,-x", "-Wp,-D"):
        for k in ("c", "cxx", "asm"):
            bad = guest_rec(ext)
            bad["flags"][k] = bad["flags"][k] + [f]
            check("guest root with %s in flags.%s is refused (not a per-root flag)" % (f, k), any("flags.%s: %s is not a per-root flag" % (k, f) in x for x in errs(gr, bad)), errs(gr, bad))
    for f in ("-Wall", "-Werror=format", "-DX=1", "-std=gnu++20", "-O3", "-Os", "-g", "-g1", "-ffunction-sections", "-fdata-sections"):
        for k in ("c", "cxx", "asm"):
            ok = guest_rec(ext)
            ok["flags"][k] = ok["flags"][k] + [f]
            check("guest root with %s in flags.%s is accepted (per-root class)" % (f, k), not [x for x in errs(gr, ok) if "per-root" in x], errs(gr, ok))
    # the recipe's semantic set per language is the driver's own answer: C drops what the driver says is C++-only; C++ and asm keep all
    drv = lambda n: os.path.join(prefix, "bin/aarch64-linux-gnu-" + n)  # noqa: E731
    sc, sx, sa = (recipe.semantics_for(drv(d), k) for d, k in (("gcc", "c"), ("g++", "cxx"), ("gcc", "asm")))
    check("semantics_for: C++ and asm keep the whole set, C drops only what the driver says does not apply", sx == list(recipe.SEMANTICS_FLAGS) and sa == sx
          and sc == [f for f in sx if f != "-fno-sized-deallocation"], (len(sc), len(sx), len(sa), set(sx) - set(sc)))
    for k, d in (("c", "gcc"), ("cxx", "g++"), ("asm", "gcc")):   # the compiler's own statement of the data model under the recipe's flags
        m = recipe.predefined(drv(d), k, recipe.semantics_for(drv(d), k))
        check("predefined %s: size_t unsigned int, ptrdiff_t int, plain char signed" % k, m["__SIZE_TYPE__"] == "unsigned int" and m["__PTRDIFF_TYPE__"] == "int" and "__CHAR_UNSIGNED__" not in m, m.get("__SIZE_TYPE__"))
    check("without -fsigned-char the compiler's plain char is unsigned (the gate's clause has something to catch)", "__CHAR_UNSIGNED__" in recipe.predefined(drv("gcc"), "c", ["-mabi=ilp32"]))
    try:
        recipe.semantics_for(drv("gcc"), "c", ["-mabi=ilp32", "-fnosuchflag"])
        check("semantics_for: a flag the driver does not know is an error", False)
    except recipe.BuildRed:
        check("semantics_for: a flag the driver does not know is an error", True)

    ra, ta = build(gr, "GA")
    check("guest root builds on the external compiler", ra["status"] == "green", ra.get("failure"))
    pl = recipe.plugin_languages(drv("gcc"), drv("g++"), {p["name"]: p["path"] for p in ra.get("plugins", [])})
    check("plugin_languages: cc1plus and cc1 load all 8 plugins (the C++ front end's symbols are weak references, not load requirements: CC1PLUS-SYMS)",
          pl["cxx"] == list(recipe.PLUGIN_SOURCES) and pl["c"] == list(recipe.PLUGIN_SOURCES), pl)
    if ra["status"] != "green":
        return
    rb, tb = build(gr, "GB")
    rw, tw = build(gr, "GA", os.path.join(BASE, "out-GA2"))
    so = lambda r: {p["name"]: p["sha256"] for p in r["plugins"]}  # noqa: E731
    check("plugins are built against the external compiler's headers, .so deterministic (two cold stores)", so(ra) == so(rb) and len(so(ra)) == len(recipe.PLUGIN_SOURCES), (so(ra), so(rb)))
    check("cold and warm build: warm runs no stage, objects equal", rw["stages"]["run"] == 0 and [o["sha256"] for o in rw["objects"]] == [
        o["sha256"] for o in ra["objects"]] == [o["sha256"] for o in rb["objects"]], (rw["stages"], ra["stages"]))
    print("TIME guest cold A %.1fs, cold B %.1fs, warm %.1fs" % (ta, tb, tw))
    # per-target recipes: one root, one set of units, a recipe per target; the host recipe is a variant keyed by its target
    pt = guest_repo("pt", rec, pin, files={ROOT + "/a.cc": src})
    w(pt, ROOT + "/host.json", json.dumps(REC))
    ad = json.load(open(os.path.join(pt, T + "verify/admit.json")))
    for r in ad["roots"]:
        if r["path"] == ROOT:
            r["recipe"] = {"guest": ROOT + "/build.json", "host": ROOT + "/host.json"}
    w(pt, T + "verify/admit.json", json.dumps(ad))
    git(pt, "add", "-A")
    git(pt, "commit", "-q", "-m", "per-target")
    adm_pt = recipe.load_admit(pt)
    check("per-target recipes: the pairs of the root are the guest base and root@host",
          recipe.pairs_of(json.load(open(os.path.join(pt, ROOT + "/build.json"))), ROOT, adm_pt[ROOT]) == [ROOT, ROOT + "@host"], adm_pt[ROOT])
    ept = recipe.check(rec, ROOT, recipe.tracked(pt), adm_pt, recipe.load_pin(pt), pt)
    check("per-target recipes: the root and both recipes are valid together", ept == [], ept)
    rg, _ = build(pt, "PTG")
    rh = recipe.build(pt, ROOT + "@host", os.path.join(BASE, "PTH"), os.path.join(BASE, "out-PTH"))
    check("per-target recipes: both targets build green from the same units", rg["status"] == "green" and rh["status"] == "green" and rh["root"] == ROOT + "@host"
          and [o["unit"] for o in rg["objects"]] == [o["unit"] for o in rh["objects"]], (rg.get("failure"), rh.get("failure")))
    check("per-target recipes: each target has its own keys and objects (compiler, flags and variant are in the key)",
          not set(rg["stage_keys"]) & set(rh["stage_keys"]) and [o["sha256"] for o in rg["objects"]] != [o["sha256"] for o in rh["objects"]], "")
    check("per-target recipes: the guest target is judged by the guest rules, the host target by none of them",
          recipe.guest_errors(rec, ROOT, adm_pt, recipe.load_pin(pt)) == [] and recipe.guest_errors(REC, ROOT + "@host", adm_pt, recipe.load_pin(pt)) == [], "")
    o = next(x["object"] for x in ra["objects"] if x["unit"] == "a.cc")
    syms = subprocess.run(["aarch64-linux-gnu-nm", o], capture_output=True, text=True).stdout
    check("msvcmodel data model: size_t is unsigned int (mangled j)", "_Z2szj" in syms, syms)
    check("volatile is ldar/stlr and float -> int is _ftol on the external compiler", "ldar" in disasm(ra, "_Z2rdv") and "stlr" in disasm(ra, "_Z2wri")
          and "#0x80000000" in disasm(ra, "_Z3f2if"))
    t = os.path.join(os.path.dirname(VERIFY), "toolchain", "msvcmodel", "test_msvcmodel.py")
    p = subprocess.run([sys.executable, "-I", t], env=dict(os.environ, CXX=os.path.join(prefix, "bin/aarch64-linux-gnu-g++")), capture_output=True, text=True)
    # the sample root builds a unit that includes the guest's libstdc++ and newlib headers (compiler 13.3 reads gcc 15.2 headers: -std=gnu++20)
    hdr = "#include <vector>\n#include <string>\n#include <cstdio>\nint hv(int n) { std::vector<int> v(n); std::string s(\"x\"); std::printf(\"%d\", (int)v.size()); return (int)s.size(); }\n"
    gp = guest_repo("gp", sample, pin_real, files={ROOT + "/h.cc": hdr})
    rp, tpb = build(gp, "GP")
    check("sample platform root builds a unit using the runtime's libstdc++ and newlib headers", rp["status"] == "green", rp.get("failure"))
    check("test_msvcmodel.py on the external", p.returncode == 0 and "skip" not in p.stdout, p.stdout + p.stderr)


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
