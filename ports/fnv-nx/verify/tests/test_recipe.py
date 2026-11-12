#!/usr/bin/env python3
"""Tests for verify/recipe.py, gate_build.py, gate_determinism.py on a toy product root in a temp git repo. Plain script: exit 0 on
pass, one PASS/FAIL line per case. Needs root (sandbox) and aarch64-linux-gnu-gcc-13/g++-13. Scratch: mkdtemp, removed at exit."""
import atexit
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

BASE = tempfile.mkdtemp(prefix="recipe-test-")
atexit.register(lambda: subprocess.Popen(["rm", "-rf", BASE], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))  # detached: the store holds ~100k hardlinks
FAILED = []
LOCK = threading.Lock()
T = "ports/fnv-nx/"
ROOT = T + "toy"
PY = [sys.executable, "-I"]
GEN = '''import os
@IMPORT@
n = len([l for l in open("/in/ports/fnv-nx/toy/constants.tsv") if l.strip()])
os.makedirs("/out/gen")
open("/out/gen/gen.h", "w").write("#define NCONST %d\\n#define STAMP %dL\\n" % (n, @EXTRA@))
'''
# The toy root is a guest ELF in the sense of RECIPE.md "Link": ILP32, position independent, linked with the product's own link/guest.ld, with
# the FNXG header of crt0 at the first segment + 8 and the entry at the first segment. The header is OS-layer code (os.S, an os_layer unit);
# HDR is the same as one assembler statement for the roots that have no os.S (MINI).
CRT0 = ".section .crt0,\"a\",%progbits\n.global __fnvnx_guest_start\n.align 2\n__fnvnx_guest_start:\n b f_a\n .word 0, 0x47584e46, 3, 0, 0, 0, 0\n"
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
    "verify/declared_machine.tsv": "# id\tcontract_name\tfact\tsource\tkind\tvalue\n",
    "toy/link.ld": open(os.path.join(VERIFY, "..", "link", "guest.ld")).read(),
}
LINK_TOOLS = ("link/ldcheck.py", "link/gateids.py", "contract/gatefile.py")   # the real files of the tree: the ldcheck stage mounts them


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


def make_repo(name, gen_extra="", rec=None, files=None):
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
        {"path": ROOT, "class": "product", "target": "host", "gates": [], "recipe": ROOT + "/build.json"},
        {"path": T + "gen", "class": "tool", "gates": []}, {"path": T + "verify", "class": "tool", "gates": []},
        {"path": T + "link", "class": "tool", "gates": []}, {"path": T + "contract", "class": "tool", "gates": []}]}))
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

    def later(fn, *a):
        futs.append(pool.submit(fn, *a))

    # --- gate scenarios (subprocesses), started now, checked as they finish
    def gate_cases():
        for n in ("build", "determinism"):
            empty = make_repo("empty-" + n)
            w(empty, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": T + "toy", "class": "doc", "gates": []}]}))
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
              and r1["failure"]["stage"] == "cc:b.c" and r1["elf"] is None and r1["objects"] == [], (fu, r1.get("failure")))
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
    bad_t = mut(external=[dict(ex, tree_sha256="0" * 64)], include=erec["include"])
    rt = recipe.build(make_repo("ext-t", rec=bad_t, files=ef), ROOT, SS, BASE + "/xo3", ext=ec)
    check("wrong tree_sha256: red naming the external", rt["status"] == "red" and rt["failure"]["stage"] == "external:tiny"
          and thash in " ".join(rt["failure"]["stderr"]), rt.get("failure"))
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

    print("INFO +%.1f s: externals done" % (time.time() - t_start))
    # --- the compiler as an external tree at /opt/tc (real ELF closure of the cross gcc, pinned by commit and tree hash)
    crepo = make_repo("tc", rec=crec)
    t0 = time.time()
    rc_x = recipe.build(crepo, ROOT, SS, BASE + "/tco", ext=ec)
    print("INFO toy build with the external compiler %.1f s, stages %s" % (time.time() - t0, rc_x.get("stages")))
    check("external compiler at /opt/tc: toy root builds green (4 objects + ELF)", rc_x["status"] == "green" and len(rc_x["objects"]) == 4 and rc_x["elf"], rc_x.get("failure"))
    check("external compiler: objects and ELF equal the system compiler's (same bytes of the tools, other path), other stage keys",
          [o["sha256"] for o in rc_x["objects"]] == [o["sha256"] for o in r1["objects"]] and rc_x["elf"]["sha256"] == r1["elf"]["sha256"]
          and set(rc_x["stage_keys"]).isdisjoint(r1["stage_keys"]), rc_x.get("failure"))
    rc_y = recipe.build(crepo, ROOT, SS, BASE + "/tco2", ext=ec)
    check("external compiler: second build is all cache hits", rc_y["stages"]["run"] == 0 and rc_y["stage_keys"] == rc_x["stage_keys"], rc_y["stages"])
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
    m = mut()
    m["generated"][0]["outputs"] = ["a.c"]
    check("generated output colliding with a tracked file rejected", any("also a tracked file" in e for e in errs(m)))
    check("contract_names that is no tracked file rejected, a tracked file accepted", any("contract_names" in e for e in errs(mut(contract_names="nofile.tsv")))
          and not errs(mut(contract_names="constants.tsv")))
    check("os_layer entry that is no unit rejected", any("not a unit" in e for e in errs(mut(os_layer=["toy.h"]))))
    m = mut()
    m["flags"]["c"] = ["-Ifoo", "-o"]
    check("flags that change a stage's job rejected, all problems listed", len([e for e in errs(m)]) >= 2)

    print("INFO +%.1f s: validation done, joining gate scenarios" % (time.time() - t_start))
    for f in futs:
        f.result()
    pool.shutdown()
    print("INFO times: toy cold %.1f s, cached %.1f s; whole file %.1f s" % (cold, warm, time.time() - t_start))
    print("%d FAILED" % len(FAILED) if FAILED else "all passed")
    return 1 if FAILED else 0


def check_tc_gate(repo, tcc, tiny):
    rc, g, td, txt = gate(repo, "determinism", BASE + "/gtc", BASE + "/tccache")
    print("INFO gate determinism, external compiler + external include tree (2 cold + warm) %.1f s: %s" % (td, g and g["detail"]))
    check("determinism gate with the compiler as an external tree at /opt/tc and a second external: green, accepted by verdict, each fetched once",
          rc == 0 and g["universe"] == 2 == g["certified"] and accepted("determinism", BASE + "/gtc")[0] == "green"
          and sorted(os.listdir(BASE + "/tccache/external")) == sorted([tcc, tiny]), (g, txt))


if __name__ == "__main__":
    sys.exit(main())
