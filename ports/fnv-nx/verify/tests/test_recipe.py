#!/usr/bin/env python3
"""Tests for verify/recipe.py, gate_build.py, gate_determinism.py on a toy product root in a temp git repo. Plain script: exit 0 on
pass, one PASS/FAIL line per case. Needs root (sandbox) and aarch64-linux-gnu-gcc-13/g++-13. Scratch: mkdtemp, removed at exit."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
import recipe  # noqa: E402
import unitstage  # noqa: E402
import verdict  # noqa: E402

import scratch  # noqa: E402

scratch.install()  # removed synchronously at exit (the stores are ~50 MB)
BASE = tempfile.mkdtemp(prefix="recipe-test-")
FAILED = []
LOCK = threading.Lock()
T = "ports/fnv-nx/"
ROOT = T + "toy"
PY = [sys.executable, "-I"]
GEN = '''import os
@IMPORT@
n = len([l for l in open("/in/ports/fnv-nx/toy/constants.tsv") if l.strip()])
open("/out/gen/gen.h", "w").write("#define NCONST %d\\n#define STAMP %dL\\n" % (n, @EXTRA@))
'''
GENLD = '''import os
n = len([l for l in open("/in/ports/fnv-nx/toy/ldin.tsv") if l.strip()])
open("/out/gen/link.ld", "w").write(open("/in/ports/fnv-nx/link/guest.ld").read() + "\\n/* generated for %d rows */\\n" % n)
'''
# The toy root is a guest ELF in the sense of RECIPE.md "Link": ILP32, position independent, linked with the product's own link/guest.ld and
# the product's own crt0 (rt/guest/crt0.s, read from the tree like guest.ld, so the fixture can never drift from the link contract): the
# FNXG header at the first segment + 8, MOD0 behind it, the entry at the first segment. crt0 branches to __fnvnx_guest_entry, the entry every
# guest program defines (in the product, runtime.c); the toy's program entry is f_a. The header is OS-layer code (os.S, an os_layer unit);
# HDR is the same as one assembler statement for the roots that have no os.S (MINI).
CRT0 = (open(os.path.join(VERIFY, "..", "rt", "guest", "crt0.s")).read()
        + "\n.text\n.global __fnvnx_guest_entry\n.set __fnvnx_guest_entry, f_a\n")
OS_NOLINK = ".text\n.globl os_svc\n.type os_svc,%function\nos_svc:\n svc #0\n ret\n.size os_svc,.-os_svc\n"
HDR = "__asm__(%s);\n" % json.dumps(CRT0 + ".text\n")
RECIPE = {"format": "fnvnx-recipe/1", "compiler": {"cc": "aarch64-linux-gnu-gcc-13", "cxx": "aarch64-linux-gnu-g++-13"},
          "flags": {"c": ["-mabi=ilp32", "-fPIE", "-O1", "-ffreestanding", "-fno-stack-protector", "-fno-asynchronous-unwind-tables"],
                    "cxx": ["-mabi=ilp32"], "asm": ["-mabi=ilp32"]},
          "include": [ROOT + "/inc", ROOT + "/gen"], "units": "all",
          "generated": [{"tool": T + "gen/gen.py", "args": [], "inputs": [ROOT + "/constants.tsv"], "outputs": ["gen/gen.h"]}],
          "os_layer": ["os.S"], "constants": "constants.tsv",
          "link": {"script": "link.ld", "output": "toy.elf", "flags": ["-m", "aarch64linux32"]}}
FILES = {
    "toy/a.c": '#include "toy.h"\n#include "gen.h"\nint f_b(void);\nint f_a(void) { return NCONST + f_b() + TOY + (int)(STAMP % 1000003L); }\n',
    "toy/b.c": '#include "toy.h"\nint f_b(void) { return TOY * 2; }\n',
    "toy/c.c": '#include "toy.h"\nint f_c(int x) { return x + TOY; }\n',
    "toy/os.S": CRT0 + ".text\n.globl os_svc\n.type os_svc,%function\nos_svc:\n svc #0\n ret\n.size os_svc,.-os_svc\n",
    "toy/inc/toy.h": "#define TOY 7\n",
    "toy/constants.tsv": "f_c\t1\nf_b\t2\n",
    "verify/wine_fixme.tsv": "# id\tcontract_name\twine_ref\tkind\tvalue\n",
    "verify/declared_machine.tsv": "# id\tcontract_name\tfact\tsource\tkind\tvalue\n# fact: no-network | time-zone | registry | no-galaxy (fixture); source: win-doc:<page>:<function> | wine-9.0:<path>:<function> | galaxy-sdk:<page>:<function>; kind void|u32\n",
    "toy/link.ld": open(os.path.join(VERIFY, "..", "link", "guest.ld")).read(),
}
LINK_TOOLS = ("link/ldcheck.py", "link/gateids.py", "contract/gatefile.py", "verify/objstub_plugin.cc")   # the real files of the tree: the ldcheck stage mounts them; objstub_plugin.cc is the source of the analysis plugin every root with C/C++ units builds


def check(name, ok, detail=""):
    with LOCK:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)[:600]) if detail and not ok else ""), flush=True)
        if not ok:
            FAILED.append(name)
    return ok


def w(repo, rel, text):
    p = os.path.join(repo, T + rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


def make_repo(name, gen_extra="", rec=None, files=None, extra_roots=()):
    repo = os.path.join(BASE, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    for k, v in dict(FILES, **(files or {})).items():
        if v is not None:
            w(repo, k, v)
    for f in LINK_TOOLS:
        w(repo, f, open(os.path.join(VERIFY, "..", f)).read())
    imp, _, ex = gen_extra.partition("|")
    w(repo, "gen/gen.py", GEN.replace("@IMPORT@", imp).replace("@EXTRA@", ex or "0"))
    w(repo, "toy/build.json", json.dumps(rec or RECIPE, indent=1))
    w(repo, "verify/env.json", open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(VERIFY))), recipe.ENV_JSON)).read())   # the declared environment
    w(repo, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": ROOT, "class": "product", "target": "host", "recipe": ROOT + "/build.json"},
        {"path": T + "gen", "class": "tool"}, {"path": T + "verify", "class": "tool"},
        {"path": T + "link", "class": "tool"}, {"path": T + "contract", "class": "tool"}] + list(extra_roots)}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo


def gate(repo, name, out, cache=None):
    env = dict(os.environ, GATE_OUT=out)
    env.pop("GATE_CACHE", None)
    if cache:
        env["GATE_CACHE"] = cache
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    p = subprocess.run(PY + [VERIFY + "/gate_%s.py" % name], cwd=repo, env=env, capture_output=True, text=True)
    r = json.load(open(out + "/%s.json" % name)) if os.path.exists(out + "/%s.json" % name) else None
    return p.returncode, r, time.time() - t0, p.stdout + p.stderr


def accepted(name, out):
    return verdict._gate_result({"name": name, "result": name + ".json"}, out, 0)


def errs(rec, **kw):
    repo = os.path.join(BASE, "ck")
    shutil.rmtree(repo, ignore_errors=True)
    r = make_repo("ck", rec=rec)
    roots, files = recipe.load_admit(r), recipe.tracked(r)
    return recipe.check(rec, ROOT, files, roots, None, r)


def mut(**kw):
    r = json.loads(json.dumps(RECIPE))
    r.update(kw)
    return r


def abi_rec(abi, rec=None):
    """Recipe REC (default RECIPE) for ABI "ilp32" (the toy's own ABI: unchanged) or "lp64": no -mabi=ilp32 and no ELF (a link is the
    ILP32 guest ELF, RECIPE.md "Link"; the LP64 objects exist to compare symbol tables)."""
    r = json.loads(json.dumps(rec or RECIPE))
    if abi == "lp64":
        r["flags"] = {k: [t for t in v if t != "-mabi=ilp32"] for k, v in r["flags"].items()}
        r["link"] = None
    return r


def elf_class(path):
    """32 or 64: the EI_CLASS of an ELF file."""
    return {1: 32, 2: 64}[open(path, "rb").read(5)[4]]


MINI = {"toy/b.c": None, "toy/c.c": None, "toy/os.S": None,
        "toy/a.c": HDR + '#include "toy.h"\n#include "gen.h"\nint f_a(void) { return NCONST + TOY + (int)(STAMP % 1000003L); }\n'}
MINI_ITEMS = {ROOT + "/a.c", ROOT + "/toy.elf"}
CC_DRV, CXX_DRV = "/usr/bin/aarch64-linux-gnu-gcc-13", "/usr/bin/aarch64-linux-gnu-g++-13"


def make_toolchain(dst):
    """The cross gcc as a tree: exactly what unitstage.gcc_set derives for the C driver, plus the linker the driver names, each at its
    own path below DST (the driver finds cc1, as, ld relative to itself). Libraries outside it (libc, libisl ...) stay host inputs."""
    exes, trees = map(set, unitstage.gcc_set(CC_DRV))
    ld = subprocess.run([CC_DRV, "-print-prog-name=ld"], capture_output=True, text=True).stdout.strip()
    exes.add(os.path.normpath(ld))
    exes.add(os.path.normpath(ld)[:-2] + "readelf")   # the ELF facts stage runs the readelf beside the linker
    keep = {"cc1", "collect2"}    # test economy: the C compiler proper and the driver's helper; not cc1plus, lto1, f951 ... (unused by C units)
    for t in sorted(trees):
        if t == "/usr/include":   # the host's own headers: not searched by a freestanding cross compile, 87 MB
            continue
        shutil.copytree(t, dst + t, symlinks=True, ignore=(lambda d, names: [n for n in names if n not in keep] if d.startswith("/usr/libexec/") else []))
    for e in sorted(exes):
        os.makedirs(dst + os.path.dirname(e), exist_ok=True)
        shutil.copy2(os.path.realpath(e), dst + e)


def publish(name, populate):
    """A work repo with one commit of the tree made by populate(dir), pushed to branch NAME of the bare artifacts repo (a stand-in for
    fnv-nx-build, ARTIFACTS_URL) -> (commit, tree_sha256 of the checked-out tree)."""
    d = os.path.join(BASE, "pub-" + name)
    os.makedirs(d)
    populate(d)
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", name)
    git(d, "push", "-q", BASE + "/artifacts.git", "HEAD:refs/heads/" + name)
    subprocess.run(["git", "clone", "-q", BASE + "/artifacts.git", d + "-co", "-b", name], check=True, capture_output=True)   # what a checkout holds (no empty dirs)
    return git(d, "rev-parse", "HEAD").strip(), cas.ingest(d + "-co", BASE + "/hs-" + name, [".git"])


def main():
    t_start = time.time()
    subprocess.run(["git", "init", "-q", "--bare", BASE + "/artifacts.git"], check=True)
    os.environ["ARTIFACTS_URL"] = BASE + "/artifacts.git"
    # Everything below that does not assert coldness shares ONE store (SS: the tool runtime is ingested once, stages are cached by
    # key) and one gate cache (GC). What asserts coldness runs in a fresh store or in the determinism gate (always two cold builds).
    SS, GC = BASE + "/store", BASE + "/cache"
    pool = ThreadPoolExecutor(max_workers=6)
    tc_fut = pool.submit(publish, "tc", lambda d: make_toolchain(d))
    repo = make_repo("toy")

    # externals: a tiny include tree, and the compiler itself
    def tiny(d):
        os.makedirs(d + "/inc")
        open(d + "/inc/ext.h", "w").write("#define EXTV 5\n")
    commit, thash = publish("tiny", tiny)
    ex = {"name": "tiny", "repo": "fnv-nx-build", "ref": "tiny", "commit": commit, "tree_sha256": thash, "at": "/ext/tiny"}
    erec = mut(external=[ex], include=RECIPE["include"] + ["/ext/tiny/inc"])
    ef = {"toy/b.c": '#include "toy.h"\n#include "ext.h"\nint f_b(void) { return TOY * 2 + EXTV; }\n'}
    futs = []
    deferred = []   # scenarios that patch a module global: run after every concurrent scenario has joined

    def later(fn, *a):
        futs.append(pool.submit(fn, *a))

    # --- gate scenarios (subprocesses), started now, checked as they finish
    def gate_cases():
        for n in ("build", "determinism"):
            empty = make_repo("empty-" + n)
            w(empty, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": T + "toy", "class": "doc"}]}))
            rc, g, _, txt = gate(empty, n, BASE + "/ge" + n)
            check("%s gate with no product root: universe 0, green" % n, rc == 0 and g["universe"] == 0 and accepted(n, BASE + "/ge" + n)[0] == "green", txt)

    def gate_bad():
        bad = make_repo("bad")
        w(bad, "toy/c.c", "int f_c(void) { return undeclared_thing; }\n")
        git(bad, "add", "-A")
        git(bad, "commit", "-q", "-m", "x")
        ob = BASE + "/gb"
        rc, g, _, txt = gate(bad, "build", ob, BASE + "/cache-bad")
        check("build gate red names the failing unit; accepted by verdict._gate_result", rc == 2 and g["uncertified"] == [ROOT + "/c.c", ROOT + "/toy.elf"]
              and g["universe"] == 5 and g["certified"] == 3 and accepted("build", ob)[0] == "red", (g, txt))
        rb = recipe.build(bad, ROOT, SS, BASE + "/outb")
        check("failing unit: build red, names stage and last stderr lines", rb["status"] == "red" and rb["failure"]["stage"] == "cc:c.c"
              and rb["failure"]["exit"] and any("undeclared_thing" in l for l in rb["failure"]["stderr"]) and [f["unit"] for f in rb["failed_units"]] == ["c.c"], rb.get("failure"))

        # 3 units (a.c ok, b.c CC error, c.c PP error): BOTH failures listed with their blocks, in unit order; two cold runs equal
        two = make_repo("two", rec=dict(RECIPE, os_layer=[]), files={"toy/os.S": None, "toy/b.c": "int f_b(void) { return undeclared_b; }\n",
                                                                       "toy/c.c": '#include "missing_c.h"\nint f_c(void) { return 1; }\n'})
        r1, r2 = (recipe.build(two, ROOT, BASE + "/two-store%d" % i, BASE + "/two-out%d" % i) for i in (1, 2))
        fu = r1.get("failed_units", [])
        check("two failing units of three: both listed with {unit, stage, exit, stderr}; failure = first in unit order",
              r1["status"] == "red" and [f["unit"] for f in fu] == ["b.c", "c.c"] and fu[0]["stage"] == "cc:b.c" and fu[1]["stage"] == "pp:c.c"
              and all(f["exit"] and 0 < len(f["stderr"]) <= 20 and set(f) == {"unit", "stage", "exit", "stderr"} for f in fu)
              and any("undeclared_b" in l for l in fu[0]["stderr"]) and any("missing_c.h" in l for l in fu[1]["stderr"])
              and r1["failure"]["stage"] == "cc:b.c" and r1["elf"] is None and [o["unit"] for o in r1["objects"]] == ["a.c"], (fu, r1.get("failure")))
        # the key of a generated stage holds the installed-files digest of EVERY python module pinned in env.json (and mounts them all)
        pm = recipe.pinned_modules(two)
        check("every module pinned in env.json is listed with its installed-files digest and top-level names",
              [m for m, _, _, _, _ in pm] == sorted(json.load(open(os.path.join(two, recipe.ENV_JSON)))["python"]["modules"])
              and all(len(d) == 64 and tops and paths for _, _, d, tops, paths in pm), pm)
        # the real pinned modules import inside a generate stage: gmpy2 and numpy keep their libraries in a sibling *.libs directory, which the mount
        # takes from the RECORD (envcheck.record_files), not from the package directory
        imp = "import gmpy2, numpy, z3, capstone, keystone, unicorn, pefile, elftools"
        gi = make_repo("gi", gen_extra=imp + "|gmpy2.mpz(3) * 4")
        rgi = recipe.build(gi, ROOT, BASE + "/gi-store", BASE + "/gi-out")
        check("every pinned python module (gmpy2 and numpy with their sibling *.libs) imports inside a generate stage", rgi["status"] == "green", rgi.get("failure"))
        def pm_keys():   # patches a module global: runs after every concurrent scenario has joined, never beside their builds
            base = recipe.build(two, ROOT, BASE + "/pm-store", BASE + "/pm-out0")
            real = recipe.envcheck.wheel_digest
            for target in [m for m, _, _, _, _ in pm]:
                recipe.envcheck.wheel_digest = lambda dist, _t=target: real(dist) + ("x" if dist == _t else "")
                try:
                    alt = recipe.build(two, ROOT, BASE + "/pm-store", BASE + "/pm-out1")
                finally:
                    recipe.envcheck.wheel_digest = real
                d1, d2 = set(base["stage_keys"]) - set(alt["stage_keys"]), set(alt["stage_keys"]) - set(base["stage_keys"])
                check("changing the pinned digest of %s changes the generated-stage key (and only that stage's)" % target, len(d1) == 1 and len(d2) == 1, (d1, d2))
        deferred.append(pm_keys)
        # KEEP GOING: a red generator does not stop the other stages. Its output unit is blocked by it (never built), every other unit is
        # built, the ELF is blocked, and the root is red naming the red stage.
        kg = make_repo("kg", rec=dict(RECIPE, os_layer=[], generated=RECIPE["generated"] + [
            {"tool": T + "gen/bad.py", "args": [], "inputs": [ROOT + "/constants.tsv"], "outputs": ["bad.c"]}]),
            files={"toy/os.S": None, "gen/bad.py": "import sys\nsys.stderr.write('generator went wrong\\n')\nsys.exit(3)\n"})
        rk = recipe.build(kg, ROOT, BASE + "/kg-store", BASE + "/kg-out")
        check("red generator: the other units are built, its output unit and the ELF are blocked by its stage, root red",
              rk["status"] == "red" and rk["failure"]["stage"] == "gen:%s:1" % ROOT and any("generator went wrong" in l for l in rk["failure"]["stderr"])
              and [o["unit"] for o in rk["objects"]] == ["a.c", "b.c", "c.c"] and rk["elf"] is None and rk["red_stages"] == ["gen:%s:1" % ROOT]
              and rk["blocked"] == [{"unit": "bad.c", "blocked_by": "gen:%s:1" % ROOT}, {"unit": "toy.elf", "blocked_by": "gen:%s:1" % ROOT}]
              and "bad.c" in rk["units"], (rk.get("failure"), rk.get("blocked"), [o["unit"] for o in rk["objects"]]))
        ok_ = recipe.build(kg, ROOT, BASE + "/kg-store", BASE + "/kg-out2")
        check("red generator: a second run is deterministic and the built objects are cache hits", ok_["blocked"] == rk["blocked"] and ok_["stages"]["run"] == 0, ok_["stages"])
        norm = lambda r: json.dumps({k: v for k, v in r.items() if k not in ("stages", "stage_keys")}, sort_keys=True).replace(BASE + "/two-out1", "O").replace(BASE + "/two-out2", "O")
        check("two cold runs of the failing root give the same objects.json", norm(r1) == norm(r2))

        # an environment mismatch blocks BEFORE any stage: exit 4 "blocked: env", nothing stored, so no red stage result can come from it
        env = make_repo("envbad")
        w(env, "toy/c.c", "int f_c(void) { return undeclared_thing; }\n")
        ej = json.load(open(os.path.join(env, recipe.ENV_JSON)))
        k = sorted(ej["packages"])[0]
        ej["packages"][k] = ej["packages"][k] + "+mismatch"
        w(env, "verify/env.json", json.dumps(ej))
        git(env, "add", "-A")
        git(env, "commit", "-q", "-m", "env mismatch")
        es = BASE + "/env-store"
        try:
            recipe.build(env, ROOT, es, BASE + "/env-out")
            raised = None
        except recipe.EnvBlocked as x:
            raised = x.errors
        check("recipe.build on a mismatched env raises EnvBlocked naming the mismatch, before any stage", bool(raised) and any(k in l for l in raised), raised)
        check("nothing is stored by a blocked build (no stage result, red or green, in the store; no objects.json)",
              not os.path.exists(es) or not any(files for _, _, files in os.walk(es)) and not os.path.exists(BASE + "/env-out/objects.json"))
        p = subprocess.run(PY + [VERIFY + "/recipe.py", "build", "--root", ROOT, "--store", BASE + "/env-store2", "--out", BASE + "/env-out2"],
                           cwd=env, capture_output=True, text=True)
        check("recipe.py build on a mismatched env prints `blocked: env` and exits 4, storing nothing",
              p.returncode == 4 and p.stdout.startswith("blocked: env") and not os.path.exists(BASE + "/env-store2"), (p.returncode, p.stdout[:200], p.stderr[:200]))

    def gate_toy():
        og = BASE + "/gg"
        rc, g, tb, txt = gate(repo, "build", og, GC)
        print("INFO gate build %.1f s" % tb)
        check("build gate green 5/5 (4 units + ELF), result accepted by verdict", rc == 0 and g["universe"] == 5 == g["certified"]
              and accepted("build", og) == ("green", 5, 5, ""), (g, txt))
        tree = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()
        check("objects.json handed over at GATE_CACHE/objects/<tree>/<root>/ and in GATE_OUT/objects/",
              os.path.exists("%s/objects/%s/%s/objects.json" % (GC, tree, ROOT.replace("/", "__"))) and os.path.exists(og + "/objects/%s.json" % ROOT.replace("/", "__")))
        rc, g, td, txt = gate(repo, "determinism", BASE + "/gd", GC)
        print("INFO gate determinism, toy root %.1f s (2 cold builds + warm check): %s" % (td, g and g["detail"]))
        check("determinism gate green on the toy root, accepted by verdict", rc == 0 and g["universe"] == 5 == g["certified"]
              and accepted("determinism", BASE + "/gd")[0] == "green", (g, txt))

    def gate_nd():
        for label, extra, red in (("wall clock (time.time_ns)", "import time|time.time_ns()", True),
                                  ("os.urandom and os.getpid", "import os|int.from_bytes(os.urandom(7), 'big') + os.getpid()", False)):
            nd = make_repo("nd" + str(red), extra, rec=mut(os_layer=[]), files=MINI)
            rc, g, _, txt = gate(nd, "determinism", BASE + "/gnd" + str(red))
            if red:
                check("generator embedding %s: determinism red, names the items that read it" % label,
                      rc == 2 and g["status"] == "red" and set(g["uncertified"]) == MINI_ITEMS, (g, txt))
            else:
                check("generator embedding %s: sandbox makes it constant, so two cold builds are equal (closed channel); without GATE_CACHE there is no warm build" % label,
                      rc == 0 and "no warm" in g["detail"] and g["universe"] == 2, (g, txt))

    for fn in (gate_toy, gate_bad, gate_nd, gate_cases):
        later(fn)

    # --- toy root, shared store: cold, then cached
    t0 = time.time()
    r1 = recipe.build(repo, ROOT, SS, BASE + "/out1")
    cold = time.time() - t0
    print("INFO toy cold build %.1f s, stages %s" % (cold, r1.get("stages")))
    check("toy root builds green with 4 objects and an ELF", r1["status"] == "green" and len(r1["objects"]) == 4 and r1["elf"]
          and r1["constants"], r1.get("failure"))
    check("os_layer flag only on os.S", {o["unit"] for o in r1["objects"] if o["os_layer"]} == {"os.S"})
    check("generated header reached the unit (a.c compiled against NCONST)", any(o["unit"] == "a.c" for o in r1["objects"]))
    t0 = time.time()
    r2 = recipe.build(repo, ROOT, SS, BASE + "/out2")
    warm = time.time() - t0
    print("INFO toy cached build %.1f s, stages %s" % (warm, r2.get("stages")))
    check("second build: every stage a cache hit", r2["stages"]["run"] == 0 and r2["stages"]["hit"] > 0, r2["stages"])
    check("warm == cold (objects, ELF)", [o["sha256"] for o in r1["objects"]] == [o["sha256"] for o in r2["objects"]]
          and r1["elf"]["sha256"] == r2["elf"]["sha256"] and r1["stage_keys"] == r2["stage_keys"])
    d, u = r1["defined"], r1["undefined"]
    check("objects.json: defined symbols from .symtab (struct), undefined list", {"symbol": "f_a", "bind": "GLOBAL", "type": "FUNC", "object": "a.c"} in d
          and any(x["symbol"] == "os_svc" and x["object"] == "os.S" for x in d) and {"f_a", "f_b"} <= set(u) and "f_c" not in u
          and d == sorted(d, key=lambda x: (x["symbol"], x["object"])) and u == sorted(u), (d, u))

    # --- link roots: the stored objects of a listed root are declared inputs of the link stage
    LIB = T + "lib"

    LRS = BASE + "/ss-lr"   # the link-roots scenarios run concurrently with the main flow and build other content of the same root path: a store of their own

    def link_roots():
        librec = {k: v for k, v in RECIPE.items() if k not in ("generated", "os_layer", "constants", "link")}
        librec["include"] = []
        a_c = "int f_lib(void);\nint f_a(void) { return f_lib(); }\n"
        lr = make_repo("lr", rec=dict(RECIPE, generated=[], constants=None, include=[], units="all",
                                      link=dict(RECIPE["link"], roots=[LIB])),
                       files={"toy/a.c": a_c, "toy/b.c": None, "toy/c.c": None, "toy/constants.tsv": None, "toy/inc/toy.h": None,
                              "lib/l.c": "int f_lib(void) { return 1; }\n", "lib/build.json": json.dumps(librec)})
        ad = json.load(open(os.path.join(lr, T + "verify/admit.json")))
        ad["roots"].append({"path": LIB, "class": "product", "target": "host", "recipe": LIB + "/build.json"})
        w(lr, "verify/admit.json", json.dumps(ad))
        git(lr, "add", "-A")
        git(lr, "commit", "-q", "-m", "lib")
        x1 = recipe.build(lr, ROOT, LRS, BASE + "/lr1")
        check("link roots: the image links the objects of the listed root (f_lib resolves)", x1["status"] == "green" and x1["elf"]
              and [r["root"] for r in x1.get("link_roots", [])] == [LIB], x1.get("failure"))
        w(lr, "lib/l.c", "int f_lib(void) { return 2; }\n")
        git(lr, "add", "-A")
        git(lr, "commit", "-q", "-m", "lib changed")
        x2 = recipe.build(lr, ROOT, LRS, BASE + "/lr2")
        check("link roots: a change in the listed root changes the link key and the ELF", x2["status"] == "green"
              and x2["elf"]["sha256"] != x1["elf"]["sha256"] and set(x2["stage_keys"]) != set(x1["stage_keys"]), (x1["stage_keys"], x2["stage_keys"]))
        nolink = dict(json.load(open(os.path.join(lr, ROOT, "build.json"))))
        nolink["link"] = dict(nolink["link"], roots=[])
        w(lr, "toy/build.json", json.dumps(nolink))
        git(lr, "add", "-A")
        git(lr, "commit", "-q", "-m", "no roots")
        x3 = recipe.build(lr, ROOT, LRS, BASE + "/lr3")
        check("link roots: without the root f_lib has no body and the link is red, never a silent zero", x3["status"] == "red", x3.get("failure"))
        ig = dict(nolink)
        ig["link"] = dict(nolink["link"], roots=[LIB], flags=["-m", "aarch64linux32", "--unresolved-symbols=ignore-all"])
        w(lr, "toy/build.json", json.dumps(ig))
        git(lr, "add", "-A")
        git(lr, "commit", "-q", "-m", "ignore-all")
        try:
            recipe.build(lr, ROOT, LRS, BASE + "/lr4")
            raised = None
        except recipe.RecipeError as x:
            raised = str(x)
        check("link roots: a test-style link with --unresolved-symbols=ignore-all is refused before any stage", raised and "refusal set R" in raised, raised)

    later(link_roots)

    def host_in_guest_link():
        """the plugin set is the target profile's (RECIPE.md "Plugins"): a host-profile object never enters a guest image, so a guest link.roots naming a host root is red"""
        hp = make_repo("hg")
        roots = dict(recipe.load_admit(hp))
        roots[ROOT] = dict(roots[ROOT], target="guest")
        roots[LIB] = {"path": LIB, "class": "product", "target": "host", "recipe": LIB + "/build.json"}
        e = recipe.check(mut(link=dict(RECIPE["link"], roots=[LIB])), ROOT, recipe.tracked(hp), roots, None, hp)
        check("target profile: a guest image whose link.roots names a root of the host profile is red", any("link.roots" in x and "'host'" in x for x in e), e)
        roots[ROOT] = dict(roots[ROOT], target="host")
        e = recipe.check(mut(link=dict(RECIPE["link"], roots=[LIB])), ROOT, recipe.tracked(hp), roots, None, hp)
        check("target profile: the same link of a host image has no target error", not any("link.roots" in x and "'host'" in x and "has target" in x for x in e), e)
    later(host_in_guest_link)

    def declared_outputs():
        """the build result lists each declared output as {path, sha256}: the file is in the out dir and its hash is the file's"""
        orec = mut(outputs={"genh": RECIPE["generated"][0]["outputs"][0]})
        orp = make_repo("do", rec=orec)
        r = recipe.build(orp, ROOT, BASE + "/ss-do", BASE + "/do1")
        o = r.get("outputs", {}).get("genh")
        check("result outputs: a declared output is {path, sha256} of a file in the out dir", r["status"] == "green" and o and os.path.isfile(o["path"])
              and hashlib.sha256(open(o["path"], "rb").read()).hexdigest() == o["sha256"] and o["path"].startswith(os.path.abspath(BASE + "/do1")), r.get("outputs"))
        check("result outputs: a root declaring none has an empty map", recipe.build(make_repo("do0"), ROOT, BASE + "/ss-do", BASE + "/do2").get("outputs") == {})
    later(declared_outputs)

    def include_rule():
        """RECIPE.md "Include": root C includes B's header dir, B's recipe includes A's: C's unit include path holds A's dir without C naming it"""
        d = tempfile.mkdtemp(prefix="inc-", dir=BASE)
        os.makedirs(d + "/b")
        json.dump({"include": [T + "a/include", "/ext/private/include"]}, open(d + "/b/build.json", "w"))
        os.makedirs(d + "/b2")
        json.dump({"include": [T + "b2/include", T + "a2/include"]}, open(d + "/b2/build.json", "w"))
        roots = {T + "c": {"class": "product", "target": "guest"}, T + "b": {"class": "product", "target": "guest", "recipe": "b/build.json"},
                 T + "b2": {"class": "product", "target": "guest", "recipe": "b2/build.json"}, T + "a": {"class": "header"}}
        inc = recipe.include_closure([T + "b/include"], T + "c", roots, d, "guest")
        check("include closure: the include dirs of the root that ships the header are carried, repo dirs only, after the consumer's own", inc == [T + "b/include", T + "a/include"], inc)
        inc = recipe.include_closure([T + "b2/include"], T + "c", roots, d, "guest")
        check("include closure: a dir already named is not added twice, and the order is the recipe's", inc == [T + "b2/include", T + "a2/include"], inc)
        check("include closure: a root's own include is not widened by itself", recipe.include_closure([T + "b/include"], T + "b", roots, d, "guest") == [T + "b/include"])
        check("include closure: a root with no recipe for the unit's target carries nothing", recipe.include_closure([T + "b/include"], T + "c", roots, d, "host") == [T + "b/include"])
    later(include_rule)
    LGS = BASE + "/ss-lg"

    def link_generated():
        """link.script from a generator of the root: the ld stage mounts the generated output, so a change of the generator's input changes the link key"""
        rec = dict(RECIPE, generated=RECIPE["generated"] + [{"tool": T + "gen/linkgen.py", "args": [], "inputs": [ROOT + "/ldin.tsv", T + "link/guest.ld"], "outputs": ["gen/link.ld"]}],
                   link=dict(RECIPE["link"], script="gen/link.ld"))
        lg = make_repo("lg", rec=rec, files={"toy/ldin.tsv": "a\n", "gen/linkgen.py": GENLD, "link/guest.ld": open(os.path.join(VERIFY, "..", "link", "guest.ld")).read(), "toy/link.ld": None})
        y1 = recipe.build(lg, ROOT, LGS, BASE + "/lg1")
        check("generated link.script: a root whose script is generated links", y1["status"] == "green" and y1["elf"], y1.get("failure"))
        w(lg, "toy/ldin.tsv", "a\nb\n")
        git(lg, "add", "-A")
        git(lg, "commit", "-q", "-m", "ldin changed")
        y2 = recipe.build(lg, ROOT, LGS, BASE + "/lg2")
        k1, k2 = set(y1["stage_keys"]), set(y2["stage_keys"])
        # the output of the generator changed (one more row): its own stage and every stage that mounts it (ld, ldcheck) have new keys
        w(lg, "toy/ldin.tsv", "a\nb\n\n")
        git(lg, "add", "-A")
        git(lg, "commit", "-q", "-m", "ldin: a blank line, the same generated script")
        y3 = recipe.build(lg, ROOT, LGS, BASE + "/lg3")
        k3 = set(y3["stage_keys"])
        # an input change that leaves the generated output equal moves the generator's key only: the link stage is keyed by the content it mounts
        check("generated link.script: a change of the generator input that changes the script changes the link key", y2["status"] == "green" and len(k2 - k1) > 1
             , (len(k2 - k1), y2.get("failure")))
        check("generated link.script: an input change with an equal script leaves the link key (the stage keys differ by the generator's alone)",
              y3["status"] == "green" and len(k3 - k2) == 1, (len(k3 - k2), y3.get("failure")))

    later(link_generated)

    # --- declared tests (RECIPE.md "tests"): linked from the root's whole product unit set + the test's own units; the stub gate's doubles rule
    trec = mut(tests=[{"name": "clean", "units": ["tests/t_clean.c"]}, {"name": "dbl", "units": ["tests/t_dbl.c"]}])
    tfiles = {"toy/tests/t_clean.c": "int f_b(void);\nint t_only(void) { return f_b() + 1; }\n", "toy/tests/t_dbl.c": "__attribute__((weak)) int f_b(void) { return 99; }\n"}   # weak: the link succeeds, only the symbol rule sees it
    rt = recipe.build(make_repo("tests", rec=trec, files=tfiles), ROOT, SS, BASE + "/outt")
    tl = {t["name"]: t for t in rt.get("tests", [])}
    check("tests: green; product units exclude the test units", rt["status"] == "green" and "tests/t_clean.c" not in rt["units"] and set(tl) == {"clean", "dbl"}, rt.get("failure"))
    check("tests: each test is linked from every product unit plus its own units and has its own ELF",
          all(set(t["linked"]) == set(rt["units"]) | set(t["units"]) and t["elf"] for t in tl.values())
          and not any(d["object"].startswith("tests/") for d in rt["defined"]), tl)
    import doubles
    items, finds, st = doubles.judge({ROOT: rt})
    check("tests + doubles.judge: the test that redefines product f_b is red; the clean one is not",
          [f[0] for f in finds] == [ROOT + ":test:dbl"] and "f_b" in finds[0][3] and st["tests_with_doubles"] == 1, (finds, st))

    print("INFO +%.1f s: toy builds done" % (time.time() - t_start))
    tcc, tct = tc_fut.result()
    tcx = {"name": "tc", "repo": "fnv-nx-build", "ref": "tc", "commit": tcc, "tree_sha256": tct, "at": "/opt/tc"}
    crec = mut(external=[tcx], compiler={"external": "tc", "cc": CC_DRV[1:], "cxx": CXX_DRV[1:]})   # cxx names a path of the tree; no C++ unit uses it
    both = dict(crec, external=[tcx, ex], include=RECIPE["include"] + ["/ext/tiny/inc"], os_layer=[])
    mini_a = HDR + '#include "toy.h"\n#include "ext.h"\n#include "gen.h"\nint f_a(void) { return NCONST + TOY + EXTV + (int)(STAMP % 1000003L); }\n'
    later(check_tc_gate, make_repo("tc-mini", rec=both, files=dict(MINI, **{"toy/a.c": mini_a})), tcc, commit)

    # --- externals (fetched by commit from the bare repo)
    ec = BASE + "/ecache"
    xrepo = make_repo("ext", rec=erec, files=ef)
    rx = recipe.build(xrepo, ROOT, SS, BASE + "/xo1", ext=ec)
    check("external at an exact commit: fetched, tree hash verified, include dir inside it (-I of the external path) used", rx["status"] == "green"
          and os.path.isdir(ec + "/" + commit), rx.get("failure"))
    ry = recipe.build(xrepo, ROOT, SS, BASE + "/xo2", ext=ec)
    check("external: second build is all cache hits with identical objects and stage keys (cross-store equality: the determinism gate below)",
          ry["status"] == "green" and ry["stages"]["run"] == 0 and rx["stage_keys"] == ry["stage_keys"]
          and [o["sha256"] for o in rx["objects"]] == [o["sha256"] for o in ry["objects"]], ry.get("failure"))
    # the verified (commit, tree_sha256) is a stored fact: a warm build ingests, walks and checks out nothing
    fdir = SS + "/external-facts"
    facts = sorted(os.listdir(fdir)) if os.path.isdir(fdir) else []
    check("external: one fact per (commit, tree_sha256) in the store, one checkout per commit in the external cache",
          facts == ["%s-%s.json" % (commit, thash)] and os.path.isdir(ec + "/" + commit + ".co"), (facts, os.listdir(ec)))
    ingests, runs = [], []
    real_ingest, real_run = recipe.cas.ingest, recipe.subprocess.run
    recipe.cas.ingest = lambda p, st, exclude=(): ingests.append(p) or real_ingest(p, st, exclude)
    recipe.subprocess.run = lambda cmd, *a_, **k: runs.append(cmd) or real_run(cmd, *a_, **k)
    try:
        # the build's own non-external work uses cas.ingest too; count only ingests of an external path
        rw = recipe.build(xrepo, ROOT, SS, BASE + "/xo2w", ext=ec)
    finally:
        recipe.cas.ingest, recipe.subprocess.run = real_ingest, real_run
    check("external: a warm build makes 0 ingests of the external and runs no git read-tree/checkout/fetch",
          rw["status"] == "green" and not [i for i in ingests if ec in i] and not [c for c in runs if c[:1] == ["git"] and ("checkout-index" in c or "read-tree" in c or "fetch" in c)],
          (rw.get("failure"), [i for i in ingests if ec in i]))
    rv = recipe.build(xrepo, ROOT, BASE + "/store-v", BASE + "/xo2v", ext=ec)   # another store, same external cache: ingests once, no checkout
    check("external: a new store with the same cache ingests the checkout once and stores its own fact", rv["status"] == "green"
          and os.path.isfile(BASE + "/store-v/external-facts/%s-%s.json" % (commit, thash)), rv.get("failure"))
    bad_t = mut(external=[dict(ex, tree_sha256="0" * 64)], include=erec["include"])
    rt = recipe.build(make_repo("ext-t", rec=bad_t, files=ef), ROOT, SS, BASE + "/xo3", ext=ec)
    check("wrong tree_sha256: red naming the external", rt["status"] == "red" and rt["failure"]["stage"] == "external:tiny"
          and thash in " ".join(rt["failure"]["stderr"]), rt.get("failure"))
    check("wrong tree_sha256: no fact is stored for it", not os.path.exists(SS + "/external-facts/%s-%s.json" % (commit, "0" * 64)))
    bad_c = mut(external=[dict(ex, commit="1" * 40)], include=erec["include"])
    rc_ = recipe.build(make_repo("ext-c", rec=bad_c, files=ef), ROOT, SS, BASE + "/xo4", ext=BASE + "/ec2")
    check("commit not in the repository: red naming the external", rc_["status"] == "red" and rc_["failure"]["stage"] == "external:tiny", rc_.get("failure"))
    for what, e2, want in (("unknown name referenced", mut(compiler={"external": "nope", "cc": "bin/gcc", "cxx": "bin/g++"}, external=[ex]), "names no external"),
                           ("duplicate names", mut(external=[ex, dict(ex, at="/ext/other")]), "duplicate name"),
                           ("non-absolute at", mut(external=[dict(ex, at="ext/tiny")]), "absolute"),
                           ("at overlapping another external", mut(external=[ex, dict(ex, name="t2", at="/ext/tiny/x")]), "overlaps another"),
                           ("at colliding with a path of the stages", mut(external=[dict(ex, at="/src/x")]), "overlaps a path"),
                           ("include outside roots and externals", mut(external=[ex], include=["/ext/other/inc"]), "include")):
        check("external validation: %s" % what, any(want in x for x in errs(e2)), errs(e2))

    # RULE (the compiler is named by reference): a build.json never restates the commit or tree_sha256 of the guest compiler or runtime; the profile is the reference
    gpin = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guest_compiler.json")))
    for what, k in (("the compiler", "external"), ("the runtime", "runtime")):
        rr = make_repo("ck-pin", rec=mut(external=[dict(gpin[k])]))
        e_ = recipe.check(mut(external=[dict(gpin[k])]), ROOT, recipe.tracked(rr), recipe.load_admit(rr), gpin, rr)
        check("pin by reference: a build.json that writes %s external of the pin is red, naming the profile" % what, any("restates the pin" in x and "profile" in x for x in e_), e_)
        shutil.rmtree(rr, ignore_errors=True)
    e_ = recipe.check(dict(mut(), profile="host", external=[]), ROOT, recipe.tracked(repo), recipe.load_admit(repo), gpin, repo)
    check("pin by reference: a recipe that names its profile has no restating error", not any("restates the pin" in x for x in e_), e_)

    # RULE (a stage is keyed by what it can read): PP mounts the compiler's external plus the externals the include path and flags name; a generated
    # stage mounts the externals its args and inputs name. An external only the generator names changes that stage's key and no PP key.
    def gdata(v):
        return lambda d: open(d + "/f.txt", "w").write("v%d\n" % v)
    gc1, gh1 = publish("gdata1", gdata(1))
    gc2, gh2 = publish("gdata2", gdata(2))
    gx = lambda c, h: {"name": "gdata", "repo": "fnv-nx-build", "ref": "gdata%d" % (1 if c == gc1 else 2), "commit": c, "tree_sha256": h, "at": "/ext/gdata"}  # noqa: E731
    gen_ = [dict(RECIPE["generated"][0], args=["/ext/gdata/f.txt"])]
    g1 = recipe.build(make_repo("gd1", rec=mut(external=[ex, gx(gc1, gh1)], include=erec["include"], generated=gen_), files=ef), ROOT, SS, BASE + "/gdo1", ext=ec)
    g2 = recipe.build(make_repo("gd2", rec=mut(external=[ex, gx(gc2, gh2)], include=erec["include"], generated=gen_), files=ef), ROOT, SS, BASE + "/gdo2", ext=ec)
    d1, d2 = set(g1["stage_keys"]) - set(g2["stage_keys"]), set(g2["stage_keys"]) - set(g1["stage_keys"])
    check("an external that only the generated stage names: changing it re-runs that stage and no PP/CC stage (1 key differs, 1 run)",
          g1["status"] == "green" and g2["status"] == "green" and len(d1) == 1 and len(d2) == 1 and g2["stages"]["run"] == 1, (g1.get("failure"), g2.get("failure"), d1, d2, g2.get("stages")))
    print("INFO +%.1f s: externals done" % (time.time() - t_start))
    # --- the compiler as an external tree at /opt/tc (real ELF closure of the cross gcc, pinned by commit and tree hash)
    crepo = make_repo("tc", rec=crec)
    t0 = time.time()
    rc_x = recipe.build(crepo, ROOT, SS, BASE + "/tco", ext=ec)
    print("INFO toy build with the external compiler %.1f s, stages %s" % (time.time() - t0, rc_x.get("stages")))
    check("external compiler at /opt/tc: toy root builds green (4 objects + ELF)", rc_x["status"] == "green" and len(rc_x["objects"]) == 4 and rc_x["elf"], rc_x.get("failure"))
    check("external compiler: objects and ELF equal the system compiler's (same bytes of the tools, other path), other stage keys (the compile and link stages mount the compiler's external; a generated stage that names none keeps its key)",
          [o["sha256"] for o in rc_x["objects"]] == [o["sha256"] for o in r1["objects"]] and rc_x["elf"]["sha256"] == r1["elf"]["sha256"]
          and set(rc_x["stage_keys"]) != set(r1["stage_keys"]), rc_x.get("failure"))
    ran = []
    _rs = recipe.unitstage.run_stage
    recipe.unitstage.run_stage = lambda store, doc, *a, **k: (lambda r: (ran.append((doc["stage"], r[0][:12], r[2])) if r[2] == "run" else None) or r)(_rs(store, doc, *a, **k))
    try:
        rc_y = recipe.build(crepo, ROOT, SS, BASE + "/tco2", ext=ec)
    finally:
        recipe.unitstage.run_stage = _rs
    check("external compiler: second build is all cache hits", rc_y["stages"]["run"] == 0 and rc_y["stage_keys"] == rc_x["stage_keys"], (rc_y["stages"], ran))
    print("INFO +%.1f s: external compiler builds done" % (time.time() - t_start))
    # --- the toy root in the LP64 ABI: ELF64 objects, no ELF (the link is the ILP32 guest ELF); the ILP32 toy (r1) has ELF32 objects and ELF
    t0 = time.time()
    r64 = recipe.build(make_repo("toy64", rec=abi_rec("lp64")), ROOT, SS, BASE + "/out64")
    print("INFO toy LP64 build %.1f s, stages %s" % (time.time() - t0, r64.get("stages")))
    check("toy root builds green: ILP32 objects and ELF are ELF32, the LP64 objects are ELF64 (and no ELF)",
          r64["status"] == "green" and len(r64["objects"]) == 4 and r64["elf"] is None
          and {elf_class(o["object"]) for o in r1["objects"]} | {elf_class(r1["elf"]["path"])} == {32}
          and {elf_class(o["object"]) for o in r64["objects"]} == {64}, r64.get("failure"))
    d64 = r64["defined"]
    check("objects.json: the same defined and undefined symbols for LP64 and ILP32 (ELF32/ELF64 .symtab read by struct)",
          [(x["symbol"], x["bind"], x["type"], x["object"]) for x in d64] == [(x["symbol"], x["bind"], x["type"], x["object"]) for x in d]
          and r64["undefined"] == u and [o["sha256"] for o in r64["objects"]] != [o["sha256"] for o in r1["objects"]], (d64, r64["undefined"]))

    # --- tool-file membership: the trees of an admitted tool root (admit.json trees), one definition for the ldcheck tools and link.script
    xf = {"link/guest.ld": open(os.path.join(VERIFY, "..", "link", "guest.ld")).read(), "other/x.ld": "/* under no root */\n"}

    def tree_errs(name, trees, rec):
        repo = os.path.join(BASE, name)
        r = make_repo(name, rec=rec, files=xf)
        am = json.load(open(os.path.join(repo, T + "verify/admit.json")))
        am["roots"] = [x for x in am["roots"] if x["path"] != T + "link"]
        for x in am["roots"]:
            if x["path"] == T + "contract" and trees:
                x["trees"] = [T + "contract", T + "link"]
        open(os.path.join(repo, T + "verify/admit.json"), "w").write(json.dumps(am))
        return recipe.check(rec, ROOT, recipe.tracked(r), recipe.load_admit(repo), None, repo)
    e1 = tree_errs("tr1", True, RECIPE)
    check("ldcheck tools in a tree (link/ of the tool root contract), not under a root path: green", e1 == [], e1)
    e2 = tree_errs("tr2", False, RECIPE)
    check("ldcheck tools under no admitted root nor tree: red", any("ldcheck stage needs" in e for e in e2), e2)
    gl = T + "link/guest.ld"
    ms = mut()
    ms["link"]["script"] = gl
    e3 = tree_errs("tr3", True, ms)
    check("link.script in a tool tree (link/guest.ld): green", e3 == [], e3)
    e4 = tree_errs("tr4", False, ms)
    check("link.script in a file no tree names: red", any("link must be" in e for e in e4), e4)
    mo = mut()
    mo["link"]["script"] = T + "other/x.ld"
    e5 = tree_errs("tr5", True, mo)
    check("link.script outside every tree and the root: red", any("link must be" in e for e in e5), e5)

    # --- link.script and link.version_script: also a generated output of the same root (`seen`, as constants and contract_names)
    glr = mut(generated=RECIPE["generated"] + [{"tool": T + "gen/linkgen.py", "args": [], "inputs": [ROOT + "/ldin.tsv", T + "link/guest.ld"], "outputs": ["gen/link.ld"]}])
    glr["link"] = dict(RECIPE["link"], script="gen/link.ld")
    gfiles = {"toy/ldin.tsv": "a\n", "gen/linkgen.py": GENLD, "link/guest.ld": open(os.path.join(VERIFY, "..", "link", "guest.ld")).read()}
    gr = make_repo("gl", rec=glr, files=gfiles)
    ge = recipe.check(glr, ROOT, recipe.tracked(gr), recipe.load_admit(gr), None, gr)
    check("link.script a generated output of the root: green at check", not [e for e in ge if "link" in e and "must be" in e], ge)
    gn = json.loads(json.dumps(glr))
    gn["link"]["script"] = "gen/never.ld"
    ge2 = recipe.check(gn, ROOT, recipe.tracked(gr), recipe.load_admit(gr), None, gr)
    check("link.script that no generator produces and no file holds: red", any("link must be" in e for e in ge2), ge2)
    gv = json.loads(json.dumps(glr))
    gv["link"] = dict(gv["link"], kind="shared", version_script="gen/link.ld")
    ge3 = recipe.check(gv, ROOT, recipe.tracked(gr), recipe.load_admit(gr), None, gr)
    check("link.version_script a generated output of the root: accepted by the link rule", not [e for e in ge3 if "link must be" in e], ge3)
    check("link_generated: a tracked file is not a generated output, a produced name is", not recipe.link_generated("link.ld", {"link.ld"}, {"link.ld"}) and recipe.link_generated("gen/link.ld", set(), {"gen/link.ld"}))

    # --- validation
    check("unknown key 'exclude' is a RecipeError", any("unknown key 'exclude'" in e for e in errs(mut(exclude=["a.c"]))))
    check("units other than all rejected", any("units" in e for e in errs(mut(units=["a.c"]))))
    for fl in (["--defsym=x=0"], ["-Wl,--defsym=x=0"], ["--unresolved-symbols=ignore-all"], ["-z", "muldefs"], ["-zundefs"],
               ["-Wl,-z,undefs"], ["--unresolved-sym=ignore-all"], ["-Xlinker", "--wrap=f"], ["--allow-multiple-definition"],
               ["--noinhibit-exec"], ["--warn-unresolved-symbols"], ["--allow-shlib-undefined"], ["-defsym=a=1"]):
        m = mut()
        m["link"]["flags"] = fl
        check("refusal set: link flags %s rejected" % fl, any("refusal set" in e for e in errs(m)))
    m = mut()
    m["flags"]["c"] = ["-Wl,--defsym=x=0"]
    check("refusal set also through compile flags -Wl,", any("refusal set" in e for e in errs(m)))
    check("legit ld options are not refused", not recipe.refused(["-e", "f_a", "--gc-sections", "-z", "noexecstack", "-Map=x", "--entry=x", "-Ttext=0"]))
    check("include dir outside admitted roots rejected", any("include" in e for e in errs(mut(include=["/etc", "../x"]))))
    # one home per header: the include dir lies in an admitted product root of the recipe root's own target
    rk = os.path.join(BASE, "ck")
    base_roots = recipe.load_admit(rk)
    own = base_roots[ROOT].get("target")
    other = "host" if own != "host" else "guest"
    extra = {T + "hdr_prod": {"path": T + "hdr_prod", "class": "product", "target": own},
             T + "hdr_other": {"path": T + "hdr_other", "class": "product", "target": other},
             T + "hdr_tool": {"path": T + "hdr_tool", "class": "tool"}, T + "hdr_doc": {"path": T + "hdr_doc", "class": "doc"},
             T + "hdr_head": {"path": T + "hdr_head", "class": "header"}}
    rr = dict(base_roots, **extra)
    rfiles = list(recipe.tracked(rk)) + [T + n + "/h.h" for n in ("hdr_prod", "hdr_other", "hdr_tool", "hdr_doc")] + [T + "hdr_head/hh.h"]

    def home(d):
        return [e for e in recipe.check(mut(include=RECIPE["include"] + [T + d]), ROOT, rfiles, rr, None, rk) if "lies in no admitted header root" in e]
    check("include dir in a product root of the same target accepted", not home("hdr_prod"), home("hdr_prod"))
    check("include dir in a product root of the other target rejected", len(home("hdr_other")) == 1, home("hdr_other"))
    check("include dir in a header-class root accepted from a root of its own target (guest includer or host includer alike)", not home("hdr_head"), home("hdr_head"))
    rr_o = dict(rr, **{ROOT: dict(rr[ROOT], target=other)})
    ho = [e for e in recipe.check(mut(include=RECIPE["include"] + [T + "hdr_head"]), ROOT, rfiles, rr_o, None, rk) if "lies in no admitted header root" in e]
    check("include dir in a header-class root accepted from a root of the other target too", not ho, ho)
    check("include dir in a tool root rejected", len(home("hdr_tool")) == 1, home("hdr_tool"))
    check("include dir in a doc root rejected", len(home("hdr_doc")) == 1, home("hdr_doc"))
    # one name, one file: two include dirs of one recipe holding a header of the same include-relative path are red
    rr2 = dict(rr, **{T + "hdr_prod2": {"path": T + "hdr_prod2", "class": "product", "target": own}})
    rf2 = rfiles + [T + "hdr_prod2/h.h", T + "hdr_prod2/g.h"]

    def dups(dirs, files_):
        return [e for e in recipe.check(mut(include=RECIPE["include"] + dirs), ROOT, files_, rr2, None, rk) if "one home per header" in e]
    check("one header in one include dir accepted", not dups([T + "hdr_prod"], rf2), dups([T + "hdr_prod"], rf2))
    check("two include dirs holding the same include-relative header rejected, naming both files",
          len(dups([T + "hdr_prod", T + "hdr_prod2"], rf2)) == 1 and T + "hdr_prod/h.h" in dups([T + "hdr_prod", T + "hdr_prod2"], rf2)[0]
          and T + "hdr_prod2/h.h" in dups([T + "hdr_prod", T + "hdr_prod2"], rf2)[0])
    check("two include dirs with different headers accepted", not dups([T + "hdr_prod", T + "hdr_prod2"], rfiles + [T + "hdr_prod2/g.h"]))
    # across the whole target: two admitted guest roots with their own include/ dirs holding fnvnx/x.h, neither in this recipe's include
    cr = dict(rr, **{T + "cr1": {"path": T + "cr1", "class": "product", "target": own}, T + "cr2": {"path": T + "cr2", "class": "product", "target": own},
                     T + "cr3": {"path": T + "cr3", "class": "product", "target": other}})
    crf = rfiles + [T + "cr1/include/fnvnx/x.h", T + "cr2/include/fnvnx/x.h", T + "cr3/include/fnvnx/x.h"]

    def cross(roots_, files_):
        return [e for e in recipe.check(mut(), ROOT, files_, roots_, None, rk) if "one home per header" in e]
    check("two admitted roots of one target with fnvnx/x.h in include/ are red (naming both)", len(cross(cr, crf)) == 1 and T + "cr1/include/fnvnx/x.h" in cross(cr, crf)[0]
          and T + "cr2/include/fnvnx/x.h" in cross(cr, crf)[0] and T + "cr3/include/fnvnx/x.h" not in cross(cr, crf)[0], cross(cr, crf))
    check("the same header in a root of the other target, or only once, is accepted", not cross(cr, [f for f in crf if "cr2" not in f]), cross(cr, [f for f in crf if "cr2" not in f]))
    m = mut()
    m["generated"][0]["outputs"] = ["a.c"]
    check("generated output colliding with a tracked file rejected", any("also a tracked file" in e for e in errs(m)))
    check("contract_names that is no tracked file rejected, a tracked file accepted", any("contract_names" in e for e in errs(mut(contract_names="nofile.tsv")))
          and not errs(mut(contract_names="constants.tsv")))
    check("os_layer entry that is no unit rejected", any("not a unit" in e for e in errs(mut(os_layer=["toy.h"]))))
    m = mut()
    m["flags"]["c"] = ["-Ifoo", "-o"]
    check("flags that change a stage's job rejected, all problems listed", len([e for e in errs(m)]) >= 2)

    import toolflags
    for st, mode, tail in (("drv:r", ["-pie"], ["-###", "/probe/x.cc", "-o", "/out/x"]), ("mld:r", [], ["-print-multi-directory"])):
        d = recipe.driver_doc(st, "/ext/a64/bin/g++", ["-mabi=ilp32"], mode, tail, [{"at": "/ext/a64", "tree": "t"}])
        check("driver stage %s: its argv passes toolflags (the one constructor carries PROBE_FLAGS)" % st, toolflags.check_doc(d) == [], toolflags.check_doc(d))
    # generated-only root (RECIPE.md "Generated-only roots"): no compiler, flags, include, units, link: valid with no unit, red with one
    GO = {"format": "fnvnx-recipe/1", "generated": [{"tool": T + "gen/gen.py", "args": [], "inputs": [ROOT + "/constants.tsv"], "outputs": ["gen/gen.h"]}]}
    check("a recipe of generated stages only (no compiler, flags, include, units) is valid when the root holds no translation unit",
          errs(dict(GO)) != [] and all("unit" in e for e in errs(dict(GO))) or errs(dict(GO)) == [], errs(dict(GO)))
    GOR = make_repo("goonly", rec=dict(GO), files={"toy/a.c": None, "toy/b.c": None, "toy/c.c": None, "toy/os.S": None})
    ego = recipe.check(dict(GO), ROOT, recipe.tracked(GOR), recipe.load_admit(GOR), None, GOR)
    check("generated-only root with no unit: recipe valid", ego == [], ego)
    ego2 = recipe.check(dict(GO), ROOT, recipe.tracked(make_repo("goonly2", rec=dict(GO), files={"toy/os.S": None})), recipe.load_admit(os.path.join(BASE, "goonly2")), None, os.path.join(BASE, "goonly2"))
    check("generated-only root holding translation units a.c b.c c.c: red, one error per unit", sorted(e.split()[2] for e in ego2 if "translation unit" in e) == ["a.c", "b.c", "c.c"], ego2)
    rgo = recipe.build(GOR, ROOT, BASE + "/go-store", BASE + "/go-out")
    check("generated-only root builds green through recipe.py: its output is produced, no object, no ELF", rgo["status"] == "green" and rgo["objects"] == [] and rgo["elf"] is None, rgo.get("failure"))
    check_gen_reads(SS)
    check_run(SS)
    print("INFO +%.1f s: validation done, joining gate scenarios" % (time.time() - t_start))
    for f in futs:
        f.result()
    pool.shutdown()
    for fn in deferred:
        fn()
    print("INFO times: toy cold %.1f s, cached %.1f s; whole file %.1f s" % (cold, warm, time.time() - t_start))
    print("%d FAILED" % len(FAILED) if FAILED else "all passed")
    return 1 if FAILED else 0


def check_gen_reads(SS):
    """A generator stage reads, read-only, exactly its declared inputs, tracked files or directories of any admitted root (RECIPE.md "generated").
    The stage key holds the content of each; an undeclared read, a write outside /out, an untracked file and `uses` on a plain file are red."""
    OTH = T + "other"
    ex = [{"path": OTH, "class": "doc"}]
    rd = 'import sys\nn = int(open("/in/%s/data.txt").read())\n' % OTH
    wr = 'open("/out/gen/gen.h", "w").write("#define NCONST 1\\n#define STAMP %dL\\n" % (n - n))\n'
    def rec_(ins=(OTH + "/data.txt",), **kw):
        return dict(RECIPE, generated=[{"tool": T + "gen/xr.py", "args": [], "inputs": [ROOT + "/constants.tsv"] + list(ins),
                                                      "outputs": ["gen/gen.h"]}], **kw)
    def mk(name, body, data="5\n", ins=(OTH + "/data.txt",), **kw):
        return make_repo(name, rec=rec_(ins, **kw), files={"gen/xr.py": "import os\n" + body, "other/data.txt": data,
                                                           "other/secret.txt": "9\n"}, extra_roots=ex)
    good = mk("xr", rd + wr)
    roots, files = recipe.load_admit(good), recipe.tracked(good)
    check("generator input in another admitted root (a doc root) is accepted by the check",
          not [e for e in recipe.check(rec_(), ROOT, files, roots, None, good) if "generated" in e], recipe.check(rec_(), ROOT, files, roots, None, good))
    check("generator input that lies in no admitted root is rejected",
          any("lies in no admitted root" in e for e in recipe.check(rec_((T + "stray/x.txt",)), ROOT, files + [T + "stray/x.txt"], roots, None, good)))
    r1 = recipe.build(good, ROOT, SS, BASE + "/xr-out1")
    check("a declared read of another root's file builds", r1["status"] == "green" and len(r1["objects"]) == 4 and r1["elf"], r1.get("failure"))
    g2 = mk("xr2", rd + wr, data="6\n")
    r2 = recipe.build(g2, ROOT, SS, BASE + "/xr-out2")
    k1, k2 = set(r1["stage_keys"]), set(r2["stage_keys"])
    check("the content of a declared read is in the stage key: one byte changed, exactly the generator's key changes (its output is the same)",
          r2["status"] == "green" and len(k1 - k2) == 1 == len(k2 - k1), (len(k1 - k2), len(k2 - k1)))
    r3 = recipe.build(good, ROOT, SS, BASE + "/xr-out3")
    check("the same content: the generator key is equal and the stage is a cache hit", set(r3["stage_keys"]) == k1 and r3["stages"]["run"] == 0, r3["stages"])
    bad = mk("xr3", 'n = int(open("/in/%s/secret.txt").read())\n' % OTH + wr)
    rb = recipe.build(bad, ROOT, SS, BASE + "/xr-out4")
    check("an undeclared read is red, naming the stage and the path", rb["status"] == "red" and rb["failure"]["stage"] == "gen:%s:0" % ROOT
          and any("secret.txt" in l for l in rb["failure"]["stderr"]), rb.get("failure"))
    for i, (label, target) in enumerate((("a declared input", "/in/%s/new.txt" % OTH), ("the tool tree", "/tool/new.txt"), ("the work dir", "/work/new.txt"), ("tmp", "/tmp/new.txt"))):
        wb = mk("xw%d" % i, rd + 'open("%s", "w").write("x")\n' % target + wr)
        rw = recipe.build(wb, ROOT, SS, BASE + "/xw-out%d" % i)
        check("a write to %s (outside the stage's /out) is red, naming the stage" % label,
              rw["status"] == "red" and rw["failure"]["stage"] == "gen:%s:0" % ROOT, rw.get("failure"))
    # a directory ref expands to its tracked files only
    dr = mk("xd", 'names = sorted(os.listdir("/in/%s"))\nn = len(names)\nif names != ["data.txt", "secret.txt"]:\n    sys.stderr.write("saw %%r\\n" %% names)\n    sys.exit(7)\n' % OTH + wr, ins=(OTH,))
    with open(os.path.join(dr, T + "other/untracked.txt"), "w") as f:
        f.write("u\n")
    rd_ = recipe.build(dr, ROOT, SS, BASE + "/xd-out")
    check("an untracked file in a declared directory is not an input", rd_["status"] == "green", rd_.get("failure"))
    # `uses` stays for the generated outputs of a root: a plain file of a root is not an output it declares
    us = mk("xu", rd + wr, uses=[{"root": OTH, "output": "data.txt"}])
    ur, uf = recipe.load_admit(us), recipe.tracked(us)
    check("`uses` that names a plain (not generated) file is red",
          any("uses" in e for e in recipe.check(rec_(uses=[{"root": OTH, "output": "data.txt"}]), ROOT, uf, ur, None, us)))


HX_C = r"""
static long sc3(long n, long a, long b, long c) { long r; __asm__ volatile("syscall" : "=a"(r) : "a"(n), "D"(a), "S"(b), "d"(c) : "rcx", "r11", "memory"); return r; }
static int eq(const char *a, const char *b) { while (*a && *a == *b) { a++; b++; } return *a == *b; }
static void put(const char *path, const char *s, long n) { long fd = sc3(2, (long)path, 0x241, 0644); sc3(1, fd, (long)s, n); sc3(3, fd, 0, 0); }
static void copy(const char *from, const char *to) { char b[512]; long fd = sc3(2, (long)from, 0, 0); long n = fd < 0 ? 0 : sc3(0, fd, (long)b, 512); put(to, b, n < 0 ? 0 : n); }
int main(int argc, char **argv) {
  const char *m = argc > 1 ? argv[1] : "ok";
  if (eq(m, "ok")) put("/out/o.txt", "hello\n", 6);
  else if (eq(m, "copy")) copy(argv[2], "/out/o.txt");
  else if (eq(m, "bad")) sc3(231, 1, 0, 0);
  else if (eq(m, "extra")) { put("/out/o.txt", "a\n", 2); put("/out/x.txt", "b\n", 2); }
  sc3(231, 0, 0, 0);
  return 0;
}
"""


def check_run(SS):
    """`run` stages (RECIPE.md "Run stages"): the artifact of an admitted host root is executed in the sandbox with declared inputs, declared debs
    and /out; outputs are declared paths. A nonzero exit, an undeclared and a missing output are red; {stage, path} refs read a declared output."""
    HX, OTH = T + "hx", T + "other"
    hrec = {"format": "fnvnx-recipe/1", "compiler": {"cc": "gcc-13", "cxx": "g++-13"}, "flags": {"c": ["-fPIE", "-O1", "-ffreestanding", "-fno-stack-protector", "-fno-asynchronous-unwind-tables"], "cxx": [], "asm": []},
            "include": [], "units": "all", "link": {"kind": "exe", "output": "hx.exe", "flags": ["-m", "elf_x86_64"], "libs": {"system": ["/lib/x86_64-linux-gnu/libc.so.6"]}}}
    ex = [{"path": HX, "class": "product", "target": "host", "recipe": HX + "/build.json"}, {"path": OTH, "class": "doc"}]

    def rec_(args=("ok",), ins=(), debs=(), outs=("o.txt",), art=HX + ":hx.exe"):
        return dict(RECIPE, run=[{"name": "r", "artifact": art, "args": list(args), "inputs": list(ins), "debs": ["base-files", "libc6"] + list(debs), "outputs": list(outs)}])

    def mk(name, body=HX_C, data="5\n", **kw):
        return make_repo(name, rec=rec_(**kw), files={"hx/hx.c": body, "hx/build.json": json.dumps(hrec), "other/data.txt": data}, extra_roots=ex)
    sid = "run:%s:r" % ROOT

    def build(repo, tag):
        return recipe.build(repo, ROOT, SS, BASE + "/rn-" + tag)
    good = mk("rn")
    roots, files = recipe.load_admit(good), recipe.tracked(good)
    check("run stage: check accepts a declared artifact, inputs and debs", not [e for e in recipe.check(rec_(ins=(OTH + "/data.txt",), debs=("autotools-dev",)), ROOT, files, roots, None, good) if "run" in e],
          recipe.check(rec_(ins=(OTH + "/data.txt",), debs=("autotools-dev",)), ROOT, files, roots, None, good))
    check("run stage: a deb that env.json does not declare is red at check", any("not declared in env.json" in e for e in recipe.check(rec_(debs=("no-such-package",)), ROOT, files, roots, None, good)))
    check("run stage: an artifact that names no link output of an admitted root is red at check", any("artifact" in e for e in recipe.check(rec_(art=HX + ":nope.exe"), ROOT, files, roots, None, good)))
    r1 = build(good, "1")
    txt1 = " ".join((r1.get("failure") or {}).get("stderr", []))
    check("run stage: a PT_INTERP that no declared deb provides is red by rule, naming the path (hx.exe: the link rule's /lib/ld64.so.1 until w12h item 1 lands)",
          r1["status"] == "green" or ("PT_INTERP" in txt1 and "no declared deb provides" in txt1), txt1)
    cpu = {"vendor_id": None, "family": None, "model": None, "stepping": None, "microcode": None, "model_name": None}
    cur = {}
    for line in open("/proc/cpuinfo").read().splitlines():
        k, _, v = line.partition(":")
        if not line.strip():
            break
        cur[k.strip()] = v.strip()
    cpu = {"vendor_id": cur["vendor_id"], "family": int(cur["cpu family"]), "model": int(cur["model"]), "stepping": int(cur["stepping"]), "microcode": int(cur["microcode"], 0), "model_name": cur["model name"]}
    check("host_cpu: the pin equal to this host's /proc/cpuinfo matches", recipe.cpu_mismatch(cpu) == [], recipe.cpu_mismatch(cpu))
    for f in cpu:
        alt = dict(cpu, **{f: (cpu[f] + 1) if isinstance(cpu[f], int) else cpu[f] + "x"})
        check("host_cpu: an altered %s is a mismatch (red)" % f, len(recipe.cpu_mismatch(alt)) >= 1, recipe.cpu_mismatch(alt))
    vm = "processor\t: 0\nvendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 207\nmodel name\t: Intel(R) Xeon(R) Processor @ 2.10GHz\nstepping\t: 2\n"
    vpin = dict(cpu, microcode=None, **{k: cpu[k] for k in ()})
    vpin.update({"vendor_id": "GenuineIntel", "family": 6, "model": 207, "stepping": 2, "model_name": "Intel(R) Xeon(R) Processor @ 2.10GHz"})
    check("host_cpu: pin microcode null matches a VM cpuinfo with no microcode line", recipe.cpu_mismatch(vpin, vm) == [], recipe.cpu_mismatch(vpin, vm))
    check("host_cpu: pin microcode null is NOT a wildcard: a host with a microcode line is a mismatch", len(recipe.cpu_mismatch(vpin, vm.replace("stepping", "microcode\t: 0x1\nstepping"))) == 1, recipe.cpu_mismatch(vpin, vm.replace("stepping", "microcode\t: 0x1\nstepping")))
    check("host_cpu: a pin with a microcode value is a mismatch on a host that has no microcode line", len(recipe.cpu_mismatch(dict(vpin, microcode=1), vm)) == 1)
    jb = recipe.Job(good, ROOT, SS, 1, os.path.join(SS, "external"))
    ver = lambda n: {"version": subprocess.run(["dpkg-query", "-W", "-f=${Version}", n], capture_output=True, text=True).stdout}   # noqa: E731
    dtmp = BASE + "/un-debs"
    os.makedirs(dtmp)
    for n in ("base-files", "libc6"):
        jb._deb_tree(dtmp, n, ver(n))
    check("PT_INTERP /lib64/ld-linux-x86-64.so.2 resolves through base-files' ./lib64 -> usr/lib64 and then libc6's own link", recipe.deb_resolve("/lib64/ld-linux-x86-64.so.2", dtmp, ["base-files", "libc6"]) is not None)
    check("without base-files in the declared set the same PT_INTERP is red (libc6 alone has no ./lib64)", recipe.deb_resolve("/lib64/ld-linux-x86-64.so.2", dtmp, ["libc6"]) is None)
    check("the link-free path /usr/lib64/ld-linux-x86-64.so.2 resolves in libc6 alone (its own link)", recipe.deb_resolve("/usr/lib64/ld-linux-x86-64.so.2", dtmp, ["libc6"]) is not None)
    check("/lib/ld64.so.1 (what the link rule writes today) resolves in no declared deb", recipe.deb_resolve("/lib/ld64.so.1", dtmp, ["base-files", "libc6"]) is None)
    hc = dict(rec_(), run=[dict(rec_()["run"][0], host_cpu="cpuref")])
    check("host_cpu: the key needs the tracked pin file verify/cpuref.json", any("cpuref" in e for e in recipe.check(hc, ROOT, files, roots, None, good)))
    check("host_cpu: with the pin file tracked the check accepts it", not [e for e in recipe.check(hc, ROOT, files + [T + "verify/cpuref.json"], roots, None, good) if "host_cpu" in e])
    vl = lambda libs: dict(hrec, variants={"ubsan": {"flags": hrec["flags"], "link": dict(hrec["link"], libs={"system": libs})}})   # noqa: E731
    vr = recipe.load_admit(good)
    check("variant ubsan: its own link.libs.system (an absolute path of the pinned libubsan1) is accepted", not recipe.check(vl(["/usr/lib/x86_64-linux-gnu/libubsan.so.1"]), HX, files, roots, None, good), recipe.check(vl(["/usr/lib/x86_64-linux-gnu/libubsan.so.1"]), HX, files, roots, None, good))
    check("variant ubsan: a relative link.libs.system path is red at check", any("link.libs.system" in e for e in recipe.check(vl(["libubsan.so.1"]), HX, files, roots, None, good)))
    if r1["status"] != "green":
        check("run stage execution checks (13: green run, keys, red cases, stage ref, debs, host_cpu run): PT_INTERP of link kind exe not runnable: w12h item 1", False,
              "hx.exe's PT_INTERP %s is provided by no declared deb" % (txt1[:200],))
        return
    cache = BASE + "/rc"
    os.makedirs(cache + "/store", exist_ok=True)
    ref = {"stage": "%s:run:r" % ROOT, "path": "o.txt"}
    try:
        host, ident = recipe.resolve_input(ROOT, ref, good, cache)
        got = open(host).read()
    except ValueError as e:
        host, ident, got = None, None, str(e)
    check("stage ref {stage, path} resolves to the declared output, content addressed", got == "hello\n" and ident and ident.startswith("stage:%s:run:r:o.txt:" % ROOT), got)
    try:
        recipe.resolve_input(ROOT, {"stage": ref["stage"], "path": "other.txt"}, good, cache)
        undecl = None
    except ValueError as e:
        undecl = str(e)
    check("stage ref to an output the stage does not declare is red", undecl and "does not declare" in undecl, undecl)
    k1 = set(r1["stage_keys"])
    r2 = build(good, "2")
    check("same artifact, inputs and argv: every key equal, all cache hits", set(r2["stage_keys"]) == k1 and r2["stages"]["run"] == 0, r2["stages"])
    ch = mk("rn3", body=HX_C.replace('"hello\\n", 6', '"hellO\\n", 6'))
    r3 = build(ch, "3")
    k3 = set(r3["stage_keys"])
    check("changing the artifact changes the run key (exactly one key differs)", r3["status"] == "green" and len(k1 - k3) == 1 == len(k3 - k1), (len(k1 - k3), len(k3 - k1)))
    for tag, args, outs, want in (("bad", ["bad"], ("o.txt",), "1"), ("none", ["none"], ("o.txt",), "differ"), ("extra", ["extra"], ("o.txt",), "differ")):
        rr = build(mk("rn-" + tag, args=args, outs=outs), tag)
        f = rr.get("failure") or (rr.get("failed_units") or [{}])[0]
        txt = " ".join(map(str, [rr["status"], f.get("stage"), f.get("exit"), " ".join(f.get("stderr") or [])]))
        check("run stage: %s is red naming the stage" % {"bad": "a nonzero exit", "none": "a missing declared output", "extra": "an undeclared output"}[tag],
              rr["status"] == "red" and sid in txt and (want in txt), txt)
    di = mk("rn-in", args=["copy", "/in/1"], ins=(OTH + "/data.txt",))
    ri = build(di, "in")
    ref_in = {"stage": "%s:run:r" % ROOT, "path": "o.txt"}
    got_in = open(recipe.resolve_input(ROOT, ref_in, di, BASE + "/rc2")[0]).read() if os.makedirs(BASE + "/rc2/store", exist_ok=True) is None else None
    check("run stage: a declared input is visible at /in/N and its content is read", ri["status"] == "green" and got_in == "5\n", (ri.get("failure"), got_in))
    ri2 = build(mk("rn-in2", args=["copy", "/in/1"], ins=(OTH + "/data.txt",), data="6\n"), "in2")
    check("the content of a declared input is in the run key (one byte changed: exactly one key differs)", ri2["status"] == "green" and len(set(ri["stage_keys"]) - set(ri2["stage_keys"])) == 1, ri2.get("failure"))
    dd = mk("rn-deb", args=["copy", "/deb/autotools-dev/usr/bin/dh_autotools-dev_restoreconfig"], debs=("autotools-dev",))
    rdb = build(dd, "deb")
    os.makedirs(BASE + "/rc3/store", exist_ok=True)
    got_d = open(recipe.resolve_input(ROOT, ref_in, dd, BASE + "/rc3")[0], "rb").read() if rdb["status"] == "green" else b""
    check("run stage: a declared deb's installed files are visible at /deb/<name>/<path>", rdb["status"] == "green" and got_d == open("/usr/bin/dh_autotools-dev_restoreconfig", "rb").read()[:512], rdb.get("failure"))
    nd = mk("rn-nodeb", args=["copy", "/deb/autotools-dev/usr/bin/dh_autotools-dev_restoreconfig"])
    rnd = build(nd, "nodeb")
    got_n = open(recipe.resolve_input(ROOT, ref_in, nd, BASE + "/rc4")[0], "rb").read() if os.makedirs(BASE + "/rc4/store", exist_ok=True) is None and rnd["status"] == "green" else None
    check("run stage: a deb that is not declared is not in the sandbox (the file is not there: empty copy)", rnd["status"] == "green" and got_n == b"", (rnd["status"], got_n))


def check_tc_gate(repo, tcc, tiny):
    rc, g, td, txt = gate(repo, "determinism", BASE + "/gtc", BASE + "/tccache")
    if rc:
        for dp, _, fs in os.walk(BASE + "/gtc"):
            for fn in fs:
                if fn.endswith(".json"):
                    t = open(os.path.join(dp, fn), errors="replace").read()
                    if "stderr" in t:
                        print("DIAG", fn, t[:1500])
    print("INFO gate determinism, external compiler + external include tree (2 cold + warm) %.1f s: %s" % (td, g and g["detail"]))
    check("determinism gate with the compiler as an external tree at /opt/tc and a second external: green, accepted by verdict, each fetched once",
          rc == 0 and g["universe"] == 2 == g["certified"] and accepted("determinism", BASE + "/gtc")[0] == "green"
          and sorted(os.listdir(BASE + "/tccache/external")) == sorted([tcc, tiny, tcc + ".co", tiny + ".co"]), (g, txt))


if __name__ == "__main__":
    sys.exit(main())
