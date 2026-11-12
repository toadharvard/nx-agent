#!/usr/bin/env python3
"""Tests for the plugin stage of verify/recipe.py (fnvnx_order, fnvnx_ftoi) on toy roots in temp git repos. Plain script: exit 0 on pass, one
PASS/FAIL line per case. Needs root (sandbox) and the compilers of verify/env.json. Scratch: mkdtemp, removed at exit.
Cases: (a) the .so is byte-equal in two cold stores and a warm build runs no stage; (b) a volatile access is ldar/stlr; (c) float -> int is
MSVC's _ftol (0x80000000 for NaN and out of range) where a root without the plugin has the bare fcvtzs; (d) the gate rule: a native C++
root without a plugin is invalid (red build gate), a lifted root builds with fnvnx_ftoi (mode lifted) alone and is red without it; the plugin is a declared input of the CC stage."""
import atexit
import copy
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
REAL = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))   # checkout root
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
import recipe  # noqa: E402
import scratch  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

scratch.install()   # BASE and every other mkdtemp is removed synchronously at exit
BASE = tempfile.mkdtemp(prefix="plugin-test-")
FAILED = testcase.FAILED
T = "ports/fnv-nx/"
ROOT = T + "toy"
PLUGS = [dict({"name": n, "source": recipe.PLUGIN_SOURCES[n], "args": dict(recipe.PLUGIN_ARGS[n])},
              **({"inputs": recipe.PLUGIN_INPUTS[n]} if n in recipe.PLUGIN_INPUTS else {})) for n in recipe.PLUGIN_SOURCES]
P = {p["name"]: p for p in PLUGS}
SRC = "volatile int g;\nint rd() { return g; }\nvoid wr(int v) { g = v; }\nint f2i(float f) { return (int)f; }\nint ic(int (*f)(int)) { return f(1); }\nstruct S { int a; char b; };\nS gs;\nregister int hr asm(\"x27\");\nregister unsigned eax asm(\"x19\");\nint hrw(int v) { hr = v; eax = v + 1; return hr + eax; }\n"
REC = {"format": "fnvnx-recipe/1", "compiler": {"cc": "aarch64-linux-gnu-gcc-13", "cxx": "aarch64-linux-gnu-g++-13"},
       "flags": {"c": [], "cxx": ["-O2", "-mabi=ilp32"] + list(recipe.SDD_FLAGS), "asm": []}, "include": [], "units": "all",
       "plugin_cxx": "x86_64-linux-gnu-g++-13", "plugins": PLUGS}




def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def make_repo(name, rec, origin, files=None, extra_roots=()):
    repo = os.path.join(BASE, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    for n in list(recipe.PLUGIN_SOURCES.values()) + [i for v in recipe.PLUGIN_INPUTS.values() for i in v]:   # the real plugin sources
        os.makedirs(os.path.dirname(os.path.join(repo, n)), exist_ok=True)
        shutil.copyfile(os.path.join(REAL, n), os.path.join(repo, n))
    for k, v in dict({ROOT + "/a.cc": SRC}, **(files or {})).items():
        if v is not None:
            w(repo, k, v)
    w(repo, ROOT + "/build.json", json.dumps(rec, indent=1))
    w(repo, recipe.ENV_JSON, open(os.path.join(REAL, recipe.ENV_JSON)).read())   # the declared environment: a build runs only on the machine it describes
    root = {"path": ROOT, "class": "product", "target": "host", "gates": [], "recipe": ROOT + "/build.json"}
    if origin is not None:
        root["cxx_origin"] = origin
    w(repo, T + "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        root, {"path": T + "toolchain", "class": "tool", "gates": []}, {"path": T + "verify", "class": "tool", "gates": []},
        {"path": T + "gen", "class": "tool", "gates": []}] + list(extra_roots)}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo


def errs(repo, rec):
    return recipe.check(rec, ROOT, recipe.tracked(repo), recipe.load_admit(repo), recipe.load_pin(repo))


def build(repo, store, out=None):
    t0 = time.time()
    r = recipe.build(repo, ROOT, os.path.join(BASE, store), out or os.path.join(BASE, "out-" + store))
    return r, time.time() - t0


def disasm(res, sym, unit="a.cc"):
    o = next(x["object"] for x in res["objects"] if x["unit"] == unit)
    txt = subprocess.run(["aarch64-linux-gnu-objdump", "-d", o], capture_output=True, text=True, check=True).stdout
    return txt.split("<%s>:" % sym)[1].split("\n\n")[0]


def mut(**kw):
    r = copy.deepcopy(REC)
    r.update(kw)
    return r


def main():
    # --- gate rule, validation level (no build)
    repo = make_repo("native", REC, "native")
    case("native root with both plugins is valid", errs(repo, REC) == [], errs(repo, REC))
    for gone in recipe.PLUGIN_SOURCES:
        r = mut(plugins=[p for p in PLUGS if p["name"] != gone])
        e = errs(repo, r)
        case("native root without %s is red", any("does not load plugin " + gone in x for x in e), e, key=gone)
    case("native root without plugins is red", len([x for x in errs(repo, mut(plugins=[], plugin_cxx=None)) if "does not load" in x]) == len(recipe.PLUGIN_SOURCES))
    r = mut(plugins=[dict(p, args=dict(p["args"], args="ltr")) if p["name"] == "fnvnx_order" else p for p in PLUGS])
    case("A/B-only argument value is red", any("arguments must be" in x for x in errs(repo, r)), errs(repo, r))
    case("plugin source outside the pinned file is red", any("source must be" in x or "not a tracked" in x for x in errs(
        repo, mut(plugins=[dict(p, source=recipe.PLUGIN_SOURCES["fnvnx_ftoi"]) if p["name"] == "fnvnx_order" else p for p in PLUGS]))))
    case("plugins out of load order are red", any("order" in x for x in errs(repo, mut(plugins=PLUGS[::-1]))))
    case("native root without a semantics flag is red", any("lacks -fno-devirtualize" in x for x in errs(repo, mut(
        flags={"c": [], "cxx": [f for f in REC["flags"]["cxx"] if f != "-fno-devirtualize"], "asm": []}))))
    case("-fplugin in flags is red", any("plugin" in x for x in errs(repo, mut(flags={"c": [], "cxx": ["-fplugin=/x.so"], "asm": []}))))
    case("plugins without plugin_cxx is red", any("plugin_cxx" in x for x in errs(repo, mut(plugin_cxx=None))) or
          any("plugin_cxx" in x for x in errs(repo, {k: v for k, v in REC.items() if k != "plugin_cxx"})))
    nor = make_repo("noorigin", REC, None)
    case("C++ root with no cxx_origin is red", any("cxx_origin" in x for x in errs(nor, REC)), errs(nor, REC))

    # --- (a) determinism and caching of the plugin stage, (b) ldar/stlr, (c) _ftol
    ra, ta = build(repo, "A")
    case("native root builds with the plugins", ra["status"] == "green", ra.get("failure"))
    if ra["status"] != "green":
        return
    rb, tb = build(repo, "B")
    rw, tw = build(repo, "A", os.path.join(BASE, "out-A2"))
    so = lambda r: {p["name"]: p["sha256"] for p in r["plugins"]}  # noqa: E731
    case("plugin .so is byte-equal in two cold stores", so(ra) == so(rb) and len(so(ra)) == len(recipe.PLUGIN_SOURCES), (so(ra), so(rb)))
    case("the .so on disk is the one hashed", all(hashlib.sha256(open(p["path"], "rb").read()).hexdigest() == p["sha256"] for p in ra["plugins"]))
    case("warm build runs no stage (all hits, same objects)", rw["stages"]["run"] == 0 and rw["stages"]["hit"] == ra["stages"]["run"] and
          [o["sha256"] for o in rw["objects"]] == [o["sha256"] for o in ra["objects"]] and so(rw) == so(ra), (rw["stages"], ra["stages"]))
    print("TIME cold A %.1fs, cold B %.1fs, warm %.1fs (whole root: %d plugins + 1 unit)" % (ta, tb, tw, len(recipe.PLUGIN_SOURCES)))
    o = next(x["object"] for x in ra["objects"] if x["unit"] == "a.cc")
    secs = subprocess.run(["aarch64-linux-gnu-readelf", "-SW", o], capture_output=True, text=True).stdout
    have = set(x for x in secs.split() if ".fnvnx" in x)
    case("pi, names and layout plugins wrote their sections", {".fnvnx.n3", ".fnvnx.sympair", ".fnvnx.layout"} <= have, sorted(have))
    case("addr plugin wrote .fnvnx.addr (symbol, seen, routed; seen == routed), no .cold section", ".fnvnx.addr" in have and not any(".cold" in x for x in secs.split()), sorted(have))
    rows = subprocess.run(["aarch64-linux-gnu-objcopy", "-O", "binary", "--set-section-flags=.fnvnx.addr=alloc,load", "--only-section=.fnvnx.addr", o, "/dev/stdout"], capture_output=True, text=True).stdout.splitlines()
    ok = bool(rows) and all(len(r.split("\t")) == 3 and r.split("\t")[1] == r.split("\t")[2] and r.split("\t")[1].isdigit() for r in rows) and "_Z2rdv" in [r.split("\t")[0] for r in rows]
    case(".fnvnx.addr lines are <symbol>\\t<seen>\\t<routed> and name the unit's functions", ok, rows[:4])
    und = subprocess.run(["aarch64-linux-gnu-nm", "-u", o], capture_output=True, text=True).stdout
    case("dispatch plugin routes an indirect call through fnvnx_dispatch", "fnvnx_dispatch" in und, und)
    rd, wr_, f2i = disasm(ra, "_Z2rdv"), disasm(ra, "_Z2wri"), disasm(ra, "_Z3f2if")
    case("volatile read is ldar", "ldar" in rd, rd)
    case("volatile write is stlr", "stlr" in wr_, wr_)
    hw = disasm(ra, "_Z3hrwi")
    case("HARDREG: a global register variable is no memory: no ldar/stlr/dmb, no ICE", all(x not in hw for x in ("ldar", "stlr", "dmb")), hw)
    case("float -> int is _ftol: 0x80000000 for NaN and out of range", "#0x80000000" in f2i, f2i)
    # fnvnx_ftoi rewrites every FIX_TRUNC_EXPR or fails the compile: a conversion it does not cover (here to __int128) fails the build
    wide = make_repo("wide", REC, "native", {ROOT + "/c.cc": "__int128 w(double d) { return (__int128)d; }\n"})
    rwd, _ = build(wide, "W")
    case("double -> __int128 fails the build with an FTOI error naming both types", rwd["status"] == "red" and any(
        "FTOI" in x and "double" in x and "__int128" in x for x in rwd["failure"]["stderr"]), rwd.get("failure"))
    case("the ordinary conversions of the same root still compile", "#0x80000000" in f2i)
    # --- ORDER-CONT: a block-ending statement without a fall-through edge needs no fence; every resumption point begins with dmb ishld
    cont_src = (
        "#include <setjmp.h>\n"
        "struct NF { void *jb[5]; };\nstruct C { NF *nf; int v; };\n"
        "void lj(C *c) { if (c->nf) __builtin_longjmp((void **)c->nf->jb, 1); }\n"
        "void thrower();\nint sj(void **b, int *p) { if (__builtin_setjmp(b)) return *p; thrower(); return 0; }\n"
        "int tc(int *p) { try { thrower(); } catch (...) { return *p; } return 0; }\n"
        "struct cpu { int r[4]; };\nregister cpu *cc asm(\"x26\");\nint hl(int v) { register unsigned loc asm(\"x25\") = v; cc->r[1] = loc; return cc->r[1]; }\n"
        "int sj2(jmp_buf b, int *p) { if (setjmp(b)) return *p; return 0; }\n")
    crepo = make_repo("cont", REC, "native", {ROOT + "/b.cc": cont_src})
    rc_, _ = build(crepo, "C")
    case("ORDER-CONT: longjmp / setjmp / try-catch fixtures compile with no error", rc_["status"] == "green", rc_.get("failure"))
    if rc_["status"] == "green":
        ob = next(x["object"] for x in rc_["objects"] if x["unit"] == "b.cc")
        syms = {n.split()[-1] for n in subprocess.run(["aarch64-linux-gnu-nm", ob], capture_output=True, text=True).stdout.splitlines()}

        def lines(sym):
            sym = next((n for n in syms if sym in n and n.startswith("_Z")), sym)
            return [x.split("\t", 2)[-1].strip() for x in disasm(rc_, sym, "b.cc").split("\n") if "\t" in x]
        before_ldar = lambda ls: any("dmb\tishld" in x for x in ls) and next(i for i, x in enumerate(ls) if "dmb\tishld" in x) < max(i for i, x in enumerate(ls) if x.startswith("ldar"))  # noqa: E731
        lj = lines("2ljP1C")
        hl = lines("2hli")
        case("HARDREG: local register variable plain; the load/store through a register-held pointer stays ordered", not any("dmb" in x and "ishld" in x and False for x in hl) and any("stlr" in x for x in hl) and any("ldar" in x for x in hl), hl)
        print("DISASM lj:", lj)
        case("(i) noreturn __builtin_longjmp: no fence needed after it", any(x.startswith("br") or x.startswith("b ") or x.startswith("bl") or x.startswith("ldr") for x in lj) and lj, lj)
        s2 = lines("2sjPPvPi")
        case("(ii) __builtin_setjmp: dmb ishld at the receiver, before the load", before_ldar(s2), s2)
        tc = lines("2tcPi")
        case("(iii) catch landing pad: dmb ishld before the load in the catch", before_ldar(tc), tc)
        s3 = lines("3sj2P")
        i3 = [i for i, x in enumerate(s3) if "_setjmp" in x or "setjmp" in x]
        print("DISASM sj2:", s3, "\nDISASM sj:", s2, "\nDISASM tc:", tc)
        case("(iv) setjmp(): dmb ishld right after the call", bool(i3) and any("dmb" in x and "ishld" in x for x in s3[i3[0] + 1:i3[0] + 3]), s3)
    # the plugin is a declared input of the CC stage and its flags are in the stage document
    docs = [json.load(open(p)) for p in glob.glob(os.path.join(BASE, "A", "stage-docs", "*", "*.json"))]
    cc = [d for d in docs if d["stage"] == "cc:a.cc"]
    case("CC stage declares all plugins as inputs and loads them", len(cc) == 1 and all(
        any(i["at"] == "/plug/" + n for i in cc[0]["inputs"]) and "-fplugin=/plug/%s/%s.so" % (n, n) in cc[0]["argv"] for n in recipe.PLUGIN_SOURCES)
        and "-fplugin-arg-fnvnx_order-args=rtl" in cc[0]["argv"], cc and cc[0]["argv"])
    pl = [a for a in cc[0]["argv"] if a.startswith("-fplugin=")]
    case("plugins load in compile.sh order", [os.path.basename(a)[:-3] for a in pl] == list(recipe.PLUGIN_SOURCES), pl)
    pp = [d for d in docs if d["stage"] == "pp:a.cc"]
    case("PP stage has no plugin", len(pp) >= 1 and not any(i["at"].startswith("/plug") for d in pp for i in d["inputs"]) and
          not any(a.startswith("-fplugin") for d in pp for a in d["argv"]), [d["argv"] for d in pp])

    # --- (d) gate: a native root without a plugin is a red build gate; a lifted root builds without plugins
    bad = make_repo("nogate", mut(plugins=[p for p in PLUGS if p["name"] != "fnvnx_ftoi"]), "native")
    os.makedirs(os.path.join(BASE, "gate-out"))
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_build.py")], cwd=bad, env=dict(
        os.environ, GATE_OUT=os.path.join(BASE, "gate-out"), GATE_CACHE=os.path.join(BASE, "gate-cache")), capture_output=True, text=True)
    res = json.load(open(os.path.join(BASE, "gate-out", "build.json")))
    case("build gate is red for a native root without fnvnx_ftoi", res["status"] == "red" and p.returncode == 2 and "fnvnx_ftoi" in res["detail"], res)

    ext = os.path.join(BASE, "pack")
    os.makedirs(ext)
    git(ext, "init", "-q")
    w(ext, "pack.txt", "lifter product\n")
    git(ext, "add", "-A")
    git(ext, "commit", "-q", "-m", "pack")
    commit = git(ext, "rev-parse", "HEAD").strip()
    chk = os.path.join(BASE, "pack-tree")
    subprocess.run(["git", "-C", ext, "checkout-index", "-a", "-f", "--prefix=" + chk + "/"], check=True)
    tree = cas.ingest(chk, os.path.join(BASE, "ingest-store"))
    lrec = {"format": "fnvnx-recipe/1", "compiler": REC["compiler"], "flags": REC["flags"], "include": [], "units": "all", "plugin_cxx": REC["plugin_cxx"],
            "plugins": [dict(P["fnvnx_ftoi"], args=dict(recipe.LIFTED_ARGS["fnvnx_ftoi"]))],
            "generated": [{"tool": T + "gen/lift.py", "args": [], "inputs": [], "outputs": ["gen/a.cc"]}],
            "external": [{"name": "pack", "repo": "fnv-nx-build", "ref": "t", "commit": commit, "tree_sha256": tree, "at": "/ext/pack"}]}
    LSRC = SRC.replace("int f2i(float f)", "__attribute__((fnvnx_f2i)) int f2i(float f)")   # lifted C++ converts inside a helper only
    lift = ('import os\nos.makedirs("/out/gen")\nopen("/out/gen/a.cc", "w").write(%r)\n' % LSRC)
    lrepo = make_repo("lifted", lrec, {"lifted_by": T + "gen/lift.py", "pack": "pack"}, {T + "gen/lift.py": lift, ROOT + "/a.cc": None})
    case("lifted root with fnvnx_ftoi in mode lifted is valid", errs(lrepo, lrec) == [], errs(lrepo, lrec))
    case("lifted root without fnvnx_ftoi is red", any("does not load plugin fnvnx_ftoi" in x for x in errs(lrepo, dict(lrec, plugins=[], plugin_cxx=None))))
    case("lifted root with fnvnx_ftoi in mode native is red", any("arguments of a lifted root" in x for x in errs(lrepo, dict(
        lrec, plugins=[dict(P["fnvnx_ftoi"], args=dict(recipe.PLUGIN_ARGS["fnvnx_ftoi"]))]))))
    os.environ["ARTIFACTS_URL"] = ext
    rl, _ = build(lrepo, "L")
    case("lifted root builds green with only fnvnx_ftoi (lifted)", rl["status"] == "green" and [p["name"] for p in rl["plugins"]] == ["fnvnx_ftoi"], rl.get("failure"))
    if rl["status"] == "green":
        case("without the plugin the conversion is the bare fcvtzs", "#0x80000000" not in disasm(rl, "_Z3f2if", "gen/a.cc") and "fcvtzs" in disasm(rl, "_Z3f2if", "gen/a.cc"))
    # --- the CC stage mounts no external but the compiler's: a new commit of another external re-runs PP only; CC cannot read it
    def pack_commit(text):
        w(ext, "pack.txt", text)
        git(ext, "add", "-A")
        git(ext, "commit", "-q", "-m", text)
        c = git(ext, "rev-parse", "HEAD").strip()
        d = os.path.join(BASE, "pt-" + c[:8])
        subprocess.run(["git", "-C", ext, "checkout-index", "-a", "-f", "--prefix=" + d + "/"], check=True)
        return c, cas.ingest(d, os.path.join(BASE, "ingest-store"))

    def xrec(c, t):
        r = mut(external=[{"name": "pack", "repo": "fnv-nx-build", "ref": "t", "commit": c, "tree_sha256": t, "at": "/ext/pack"}])
        return r

    def kinds():
        ds = [json.load(open(p))["stage"].split(":")[0] for p in glob.glob(os.path.join(BASE, "X", "stage-docs", "*", "*.json"))]
        return {k: ds.count(k) for k in ("pp", "cc")}

    c1, t1 = pack_commit("one\n")
    x1 = make_repo("ext1", xrec(c1, t1), "native")
    r1, _ = build(x1, "X")
    k1 = kinds()
    c2, t2 = pack_commit("two\n")
    x2 = make_repo("ext2", xrec(c2, t2), "native")
    r2, _ = build(x2, "X", os.path.join(BASE, "out-X2"))
    k2 = kinds()
    case("external commit change: PP re-runs, CC runs nothing", r1["status"] == r2["status"] == "green" and k2["pp"] > k1["pp"] and k2["cc"] == k1["cc"]
          and [o["sha256"] for o in r1["objects"]] == [o["sha256"] for o in r2["objects"]], (k1, k2, r2.get("failure")))
    ccd = [json.load(open(p)) for p in glob.glob(os.path.join(BASE, "X", "stage-docs", "*", "*.json"))]
    ccd = [d for d in ccd if d["stage"].startswith("cc:")]
    case("CC stage document holds no /ext/pack, a PP document does", ccd and not any(i["at"] == "/ext/pack" for d in ccd for i in d["inputs"]) and any(
        i["at"] == "/ext/pack" for p in glob.glob(os.path.join(BASE, "X", "stage-docs", "*", "*.json")) for i in json.load(open(p))["inputs"]))
    inc = make_repo("incbin", xrec(c2, t2), "native", {ROOT + "/a.cc": SRC + 'asm(".incbin \\"/ext/pack/pack.txt\\"");\n'})
    ri, _ = build(inc, "I")
    case("CC cannot read an external (.incbin of its file fails)", ri["status"] == "red" and ri["failure"]["stage"].startswith("cc:"), ri.get("failure"))
    tracked = make_repo("liftedbad", lrec, {"lifted_by": T + "gen/lift.py", "pack": "pack"}, {T + "gen/lift.py": lift, ROOT + "/b.cc": "int b;\n", ROOT + "/a.cc": None})
    case("lifted root with a tracked C++ unit is red", any("is not an output" in x for x in errs(tracked, lrec)), errs(tracked, lrec))
    nopack = make_repo("nopack", lrec, {"lifted_by": T + "gen/lift.py", "pack": "nope"}, {T + "gen/lift.py": lift, ROOT + "/a.cc": None})
    case("lifted root naming no external is red", any("names no external" in x for x in errs(nopack, lrec)), errs(nopack, lrec))
    for what, tool in (("an untracked file", T + "gen/none.py"), ("a file that is not a .py", T + "gen/lift.txt"),
                       ("a file of the product root", ROOT + "/lift.py")):
        bad = make_repo("liftby_" + what.replace(" ", "_"), lrec, {"lifted_by": tool, "pack": "pack"}, {T + "gen/lift.py": lift, T + "gen/lift.txt": lift, ROOT + "/lift.py": lift, ROOT + "/a.cc": None})
        case("lifted_by naming %s is red", any("is not a tracked .py of an admitted tool root" in x for x in errs(bad, lrec)), errs(bad, lrec), key=what)


if __name__ == "__main__":
    main()
    print("FAILED: %s" % FAILED if FAILED else "all passed")
    sys.exit(1 if FAILED else 0)
