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
RECIPE = {"format": "fnvnx-recipe/1", "compiler": {"cc": "aarch64-linux-gnu-gcc-13", "cxx": "aarch64-linux-gnu-g++-13"},
          "flags": {"c": ["-O1", "-ffreestanding", "-fno-stack-protector", "-fno-asynchronous-unwind-tables"], "cxx": [], "asm": []},
          "include": [], "units": "all",
          "generated": [{"tool": T + "gen/gen.py", "args": [], "inputs": [ROOT + "/constants.tsv"], "outputs": ["gen/gen.h"]}],
          "os_layer": ["os.S"], "constants": "constants.tsv",
          "link": {"script": "link.ld", "output": "toy.elf", "flags": ["-e", "f_a"]}}
FILES = {
    "toy/a.c": '#include "inc/toy.h"\n#include "gen/gen.h"\nint f_b(void);\nint f_a(void) { return NCONST + f_b() + TOY + (int)(STAMP % 1000003L); }\n',
    "toy/b.c": '#include "inc/toy.h"\nint f_b(void) { return TOY * 2; }\n',
    "toy/c.c": '#include "inc/toy.h"\nint f_c(int x) { return x + TOY; }\n',
    "toy/os.S": ".text\n.globl os_svc\n.type os_svc,%function\nos_svc:\n svc #0\n ret\n.size os_svc,.-os_svc\n",
    "toy/inc/toy.h": "#define TOY 7\n",
    "toy/constants.tsv": "f_c\t1\nf_b\t2\n",
    "verify/wine_fixme.tsv": "# id\tcontract_name\twine_ref\tkind\tvalue\n",
    "verify/declared_machine.tsv": "# id\tcontract_name\tfact\tsource\tkind\tvalue\n",
    "toy/link.ld": "SECTIONS { . = 0x10000; .text : { *(.text*) } .data : { *(.data*) *(.bss*) } /DISCARD/ : { *(.note*) *(.comment*) *(.eh_frame*) } }\n",
}


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


def make_repo(name, gen_extra="", rec=None, files=None, roots=()):
    repo = os.path.join(BASE, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    for k, v in dict(FILES, **(files or {})).items():
        if v is not None:
            w(repo, k, v)
    imp, _, ex = gen_extra.partition("|")
    w(repo, "gen/gen.py", GEN.replace("@IMPORT@", imp).replace("@EXTRA@", ex or "0"))
    w(repo, "toy/build.json", json.dumps(rec or RECIPE, indent=1))
    w(repo, "verify/patchtree.py", open(VERIFY + "/patchtree.py").read())
    w(repo, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": ROOT, "class": "product", "target": "host", "gates": [], "recipe": ROOT + "/build.json"},
        {"path": T + "gen", "class": "tool", "gates": []}, {"path": T + "verify", "class": "tool", "gates": []}] + list(roots)}))
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
    return recipe.check(rec, ROOT, files, roots)


def mut(**kw):
    r = json.loads(json.dumps(RECIPE))
    r.update(kw)
    return r


def abi_rec(abi, rec=None):
    """Recipe REC (default RECIPE) for ABI "lp64" (unchanged) or "ilp32": -mabi=ilp32 for every compile and assemble flag list, and the
    linker emulation the cross gcc driver itself passes for it (-maarch64linux32, see `gcc -mabi=ilp32 -###`) first among the link flags."""
    r = json.loads(json.dumps(rec or RECIPE))
    if abi == "ilp32":
        r["flags"] = {k: ["-mabi=ilp32"] + v for k, v in r["flags"].items()}
        if r.get("link"):
            r["link"]["flags"] = ["-m", "aarch64linux32"] + r["link"]["flags"]
    return r


def elf_class(path):
    """32 or 64: the EI_CLASS of an ELF file."""
    return {1: 32, 2: 64}[open(path, "rb").read(5)[4]]


MINI = {"toy/b.c": None, "toy/c.c": None, "toy/os.S": None,
        "toy/a.c": '#include "inc/toy.h"\n#include "gen/gen.h"\nint f_a(void) { return NCONST + TOY + (int)(STAMP % 1000003L); }\n'}
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
    ef = {"toy/b.c": '#include "inc/toy.h"\n#include "ext.h"\nint f_b(void) { return TOY * 2 + EXTV; }\n'}
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
              and rb["failure"]["exit"] and any("undeclared_thing" in l for l in rb["failure"]["stderr"]) and rb["failed_units"] == ["c.c"], rb.get("failure"))

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

    def gate_toy32():
        r32repo = make_repo("toy32g", rec=abi_rec("ilp32"))
        og = BASE + "/gg32"
        rc, g, tb, txt = gate(r32repo, "build", og, GC)
        print("INFO ILP32 gate build cold-store %.1f s" % tb)
        check("ILP32 toy root: build gate green 5/5, accepted by verdict", rc == 0 and g["universe"] == 5 == g["certified"]
              and accepted("build", og) == ("green", 5, 5, ""), (g, txt))
        rc, g, tw, txt = gate(r32repo, "build", BASE + "/gg32w", GC)
        print("INFO ILP32 gate build warm %.1f s" % tw)
        rc, g, td, txt = gate(r32repo, "determinism", BASE + "/gd32", GC)
        print("INFO ILP32 gate determinism (2 cold builds + warm check) %.1f s: %s" % (td, g and g["detail"]))
        check("ILP32 toy root: determinism gate green 5/5, accepted by verdict", rc == 0 and g["universe"] == 5 == g["certified"]
              and accepted("determinism", BASE + "/gd32")[0] == "green", (g, txt))

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

    for fn in (gate_toy, gate_toy32, gate_bad, gate_nd, gate_cases):
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
          and any(x["symbol"] == "os_svc" and x["object"] == "os.S" for x in d) and "f_b" in u and "f_a" not in u
          and d == sorted(d, key=lambda x: (x["symbol"], x["object"])) and u == sorted(u), (d, u))

    print("INFO +%.1f s: toy builds done" % (time.time() - t_start))
    tcc, tct = tc_fut.result()
    tcx = {"name": "tc", "repo": "fnv-nx-build", "ref": "tc", "commit": tcc, "tree_sha256": tct, "at": "/opt/tc"}
    crec = mut(external=[tcx], compiler={"external": "tc", "cc": CC_DRV[1:], "cxx": CXX_DRV[1:]})   # cxx names a path of the tree; no C++ unit uses it
    both = dict(crec, external=[tcx, ex], include=RECIPE["include"] + ["/ext/tiny/inc"], os_layer=[])
    mini_a = '#include "inc/toy.h"\n#include "ext.h"\n#include "gen/gen.h"\nint f_a(void) { return NCONST + TOY + EXTV + (int)(STAMP % 1000003L); }\n'
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
    # --- the same toy root in the ILP32 ABI: ELF32 objects and ELF, symbol tables, determinism
    t0 = time.time()
    r32 = recipe.build(make_repo("toy32", rec=abi_rec("ilp32")), ROOT, SS, BASE + "/out32")
    print("INFO toy ILP32 build %.1f s, stages %s" % (time.time() - t0, r32.get("stages")))
    check("ILP32 toy root builds green: 4 objects and the ELF are ELF32, the LP64 ones are ELF64",
          r32["status"] == "green" and len(r32["objects"]) == 4 and r32["elf"]
          and {elf_class(o["object"]) for o in r32["objects"]} | {elf_class(r32["elf"]["path"])} == {32}
          and {elf_class(o["object"]) for o in r1["objects"]} | {elf_class(r1["elf"]["path"])} == {64}, r32.get("failure"))
    d32 = r32["defined"]
    check("ILP32 objects.json: the same defined and undefined symbols as LP64 (ELF32 .symtab read by struct)",
          [(x["symbol"], x["bind"], x["type"], x["object"]) for x in d32] == [(x["symbol"], x["bind"], x["type"], x["object"]) for x in d]
          and r32["undefined"] == u and [o["sha256"] for o in r32["objects"]] != [o["sha256"] for o in r1["objects"]], (d32, r32["undefined"]))
    r32b = recipe.build(make_repo("toy32b", rec=abi_rec("ilp32")), ROOT, SS, BASE + "/out32b")
    check("ILP32: a second build is all cache hits with identical objects and ELF", r32b["stages"]["run"] == 0
          and r32b["elf"]["sha256"] == r32["elf"]["sha256"] and r32b["stage_keys"] == r32["stage_keys"], r32b.get("stages"))

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

    later(tool_cases, SS, GC, ec)
    later(rule_a_cases, SS, ec)
    print("INFO +%.1f s: validation done, joining gate scenarios" % (time.time() - t_start))
    for f in futs:
        f.result()
    pool.shutdown()
    print("INFO times: toy cold %.1f s, cached %.1f s; whole file %.1f s" % (cold, warm, time.time() - t_start))
    print("%d FAILED" % len(FAILED) if FAILED else "all passed")
    return 1 if FAILED else 0


TS = {"src/main.c": '#include <stdio.h>\n#include <sys/stat.h>\n#include "cfg.h"\nint helper(void);\n'
      'int main(int argc, char **argv) {\n  FILE *f;\n  mkdir("/out/gen", 0755);\n  f = fopen(argv[1], "w");\n  if (!f) return 3;\n'
      '  fprintf(f, "#define FROM_TOOL %d\\n", VALUE + helper());\n  fclose(f);\n'
      '  if (argc > 2) { f = fopen(argv[2], "w"); fputs("x", f); fclose(f); }\n  return 0;\n}\n',
      "src/cfg.h": "#define VALUE 40\n", "src/helper.c": "int helper(void) { return 2; }\n", "src/gone.c": "int helper(void) { return 99; }\n"}
P_OK = ("--- a/src/cfg.h\n+++ b/src/cfg.h\n@@ -1 +1 @@\n-#define VALUE 40\n+#define VALUE 41\n"
        "--- a/src/gone.c\n+++ /dev/null\n@@ -1 +0,0 @@\n-int helper(void) { return 99; }\n")
P_BAD = "--- a/src/cfg.h\n+++ b/src/cfg.h\n@@ -1 +1 @@\n-#define VALUE 39\n+#define VALUE 41\n"
HT = T + "htool"
HOSTC = {"cc": "x86_64-linux-gnu-gcc-13", "cxx": "x86_64-linux-gnu-g++-13"}


def tool_rec(ext, patch="p1.patch"):
    return {"format": "fnvnx-recipe/1", "compiler": HOSTC, "flags": {"c": ["-O1"], "cxx": [], "asm": []}, "include": [], "units": "all",
            "sources": [{"external": "ts", "dir": "src", "patches": [patch], "include": ["src"]}], "external": [ext],
            "link": {"output": "mkhdr", "flags": []}}


def gen_rec(args=("/out/gen/v.h",), outputs=("gen/v.h",), tool=None):
    r = json.loads(json.dumps(RECIPE))
    r.update(generated=[{"tool": tool or {"root": HT, "output": "mkhdr"}, "args": list(args), "inputs": [], "outputs": list(outputs)}], os_layer=[],
             constants=None, link=None)
    r.pop("constants")
    r.pop("link")
    return r


def tool_cases(SS, GC, ext_cache):
    """Rule A (sources + patch stage) and rule B (a generated step that runs an executable that a root of the same build made)."""
    commit, thash = publish("ts", lambda d: [(os.makedirs(os.path.dirname(os.path.join(d, k)), exist_ok=True), open(os.path.join(d, k), "w").write(v))
                                              for k, v in TS.items()])
    ts = {"name": "ts", "repo": "fnv-nx-build", "ref": "ts", "commit": commit, "tree_sha256": thash, "at": "/ext/ts"}
    hroot = {"path": HT, "class": "tool", "target": "host", "gates": [], "recipe": HT + "/build.json"}
    a_unit = {"toy/a.c": '#include "gen/v.h"\n_Static_assert(FROM_TOOL == 43, "patched cfg.h + helper.c without gone.c");\nint f_a(void) { return FROM_TOOL; }\n',
              "toy/b.c": None, "toy/c.c": None, "toy/os.S": None, "toy/constants.tsv": None, "toy/link.ld": None,
              "htool/build.json": json.dumps(tool_rec(ts)), "htool/p1.patch": P_OK}

    def mk(name, rec, files=None, trec=None):
        f = dict(a_unit, **(files or {}))
        if trec is not None:
            f["htool/build.json"] = json.dumps(trec)
        return make_repo(name, rec=rec, files=f, roots=[hroot])
    ok = mk("rb", gen_rec())
    # (a) rule A: the tool root alone: the patched tree is the source view (gone.c deleted by the patch, cfg.h changed), ELF64 x86-64 executable
    ra = recipe.build(ok, HT, SS, BASE + "/ta", ext=ext_cache)
    check("rule A: tool root with sources+patch builds a host executable from the patched external tree (units main.c, helper.c; gone.c deleted)",
          ra["status"] == "green" and ra["units"] == ["ts:src/helper.c", "ts:src/main.c"] and ra["elf"]
          and open(ra["elf"]["path"], "rb").read(20)[4:6] == b"\x02\x01" and open(ra["elf"]["path"], "rb").read(20)[18] == 62, ra.get("failure"))
    ra2 = recipe.build(ok, HT, SS, BASE + "/ta2", ext=ext_cache)
    check("rule A: second build is all cache hits with the same executable and stage keys", ra2["stages"]["run"] == 0
          and ra2["elf"]["sha256"] == ra["elf"]["sha256"] and ra2["stage_keys"] == ra["stage_keys"], ra2.get("stages"))
    # (b) a patch that does not apply: red at patch:<root>
    rb = recipe.build(mk("rbad", gen_rec(), {"htool/p1.patch": P_BAD}), HT, SS, BASE + "/tb", ext=ext_cache)
    check("rule A: a patch that does not apply is red at stage patch:<root>", rb["status"] == "red" and rb["failure"]["stage"] == "patch:" + HT
          and rb["failure"]["exit"], rb.get("failure"))
    rbn = recipe.build(mk("rnopatch", gen_rec(), {"htool/p1.patch": ""}), HT, SS, BASE + "/tb2", ext=ext_cache)
    check("rule A: without the deleting patch the upstream file stays a unit (no exclude list): duplicate helper() is red at the link",
          rbn["status"] == "red" and rbn["failure"]["stage"] == "ld:" + HT and "ts:src/gone.c" in rbn["units"], rbn.get("failure"))
    # (c) rule B: a product root runs (a)'s executable to produce a header that its unit includes
    rc = recipe.build(ok, ROOT, SS, BASE + "/tc", ext=ext_cache)
    check("rule B: generated step runs the executable of another root; its header reaches the unit (_Static_assert FROM_TOOL == 43)",
          rc["status"] == "green" and [o["unit"] for o in rc["objects"]] == ["a.c"], rc.get("failure"))
    rc2 = recipe.build(ok, ROOT, SS, BASE + "/tc2", ext=ext_cache)
    check("rule B: second build is all cache hits", rc2["status"] == "green" and rc2["stages"]["run"] == 0 and rc2["stage_keys"] == rc["stage_keys"], rc2.get("stages"))
    # (d) a generated tool that writes an extra file
    rd = recipe.build(mk("rextra", gen_rec(args=("/out/gen/v.h", "/out/extra.txt"))), ROOT, SS, BASE + "/td", ext=ext_cache)
    check("rule B: the tool writes a file that is not a declared output: red, names the sets", rd["status"] == "red"
          and rd["failure"]["stage"] == "gen:%s:0" % ROOT and "extra.txt" in " ".join(rd["failure"]["stderr"]), rd.get("failure"))
    # cycles and validation
    cyc = mk("rcyc", gen_rec(), trec=dict(tool_rec(ts), generated=[{"tool": {"root": ROOT, "output": "x"}, "args": [], "inputs": [], "outputs": ["g.c"]}]))
    try:
        recipe.build(cyc, ROOT, SS, BASE + "/te", ext=ext_cache)
        cerr = []
    except recipe.RecipeError as e:
        cerr = e.errors
    check("rule B: a cycle between roots is an error", any("cycle" in e or "link.output" in e for e in cerr), cerr)
    roots, files = recipe.load_admit(ok), recipe.tracked(ok)
    t0 = tool_rec(ts)
    for what, rec, want in (("unknown sources key", dict(t0, sources=[dict(t0["sources"][0], x=1)]), "needs exactly"),
                            ("patch that is not tracked", dict(t0, sources=[dict(t0["sources"][0], patches=["nope.patch"])]), "patches must be"),
                            ("sources.external unknown", dict(t0, sources=[dict(t0["sources"][0], external="zz")]), "names no external"),
                            ("include outside dir", dict(t0, sources=[dict(t0["sources"][0], include=["other"])]), "include must be"),
                            ("tool root with plugins", dict(t0, plugin_cxx="x"), "not allowed"),
                            ("tool link with a script", dict(t0, link={"script": "l.ld", "output": "mkhdr", "flags": []}), "link of a tool root"),
                            ("tool root with a target compiler", dict(t0, compiler={"cc": "aarch64-linux-gnu-gcc-13", "cxx": "aarch64-linux-gnu-g++-13"}), "host driver")):
        check("validation: %s" % what, any(want in e for e in recipe.check(rec, HT, files, roots)), recipe.check(rec, HT, files, roots))
    check("validation: generated tool naming a link.output that the root lacks", any("link.output" in e for e in recipe.check(
        gen_rec(tool={"root": HT, "output": "nope"}), ROOT, files, roots, None, ok)))
    # (e) determinism gate over (c): two cold builds (the tool root included) and a warm one
    rc_, g, td, txt = gate(ok, "determinism", BASE + "/tg", GC)
    print("INFO gate determinism over rule B toy (2 cold + warm) %.1f s: %s" % (td, g and g["detail"]))
    check("rule B: determinism gate green over the generated header and the object (tool built cold twice)", rc_ == 0 and g["status"] == "green"
          and g["universe"] == g["certified"] and g["universe"] >= 1, (g, txt))


def rule_a_cases(SS, ext_cache):
    """Rule A on a guest-style ILP32 root (own units + an external C library, the env.json aarch64 compiler: the private guest-compiler pin
    is not available to every machine), -isystem for third-party headers, the program lookup of the host driver, the include-inside-root rule."""
    def tree(files):
        return lambda d: [(os.makedirs(os.path.dirname(os.path.join(d, k)), exist_ok=True), open(os.path.join(d, k), "w").write(v)) for k, v in files.items()]
    c1, h1 = publish("clib", tree({"src/lib.c": '#include <lib.h>\nint lib_add(int a, int b) { return a + b; }\n', "include/lib.h": "int lib_add(int a, int b);\n",
                                   "src/skip.c": "#error not built here\n"}))
    cl = {"name": "clib", "repo": "fnv-nx-build", "ref": "clib", "commit": c1, "tree_sha256": h1, "at": "/ext/clib"}
    only_c = {"toy/a.c": None, "toy/b.c": None, "toy/c.c": None, "toy/os.S": None, "toy/constants.tsv": None, "toy/inc/toy.h": None}
    rec = abi_rec("ilp32", dict(RECIPE, include=["/ext/clib/include"], external=[cl], os_layer=[], constants=None,
                                sources=[{"external": "clib", "dir": "src", "patches": [], "include": []}],
                                link={"script": "link.ld", "output": "own.elf", "flags": ["-e", "f_own"]}))
    rec.pop("constants")
    rec.pop("generated")
    files = dict(only_c, **{"toy/own.c": '#include <lib.h>\nint f_own(void) { return lib_add(1, 2); }\n'})
    # a file in the library tree is a unit by the rule "all" (no exclude list): the build is red, naming it, until a patch deletes it
    rr = recipe.build(make_repo("ra-all", rec=rec, files=files), ROOT, SS, BASE + "/ra0", ext=ext_cache)
    check("rule A: every source of the external tree is a unit (no exclude list): the unbuildable skip.c makes the build red", rr["status"] == "red"
          and "clib:src/skip.c" in rr["failed_units"] and "clib:src/lib.c" not in rr["failed_units"], rr.get("failure"))
    c2, h2 = publish("clib2", tree({"src/lib.c": '#include <lib.h>\nint lib_add(int a, int b) { return a + b; }\n', "include/lib.h": "int lib_add(int a, int b);\n"}))
    cl2 = dict(cl, name="clib2", ref="clib2", commit=c2, tree_sha256=h2, at="/ext/clib2")
    rec2 = json.loads(json.dumps(rec).replace("clib", "clib2").replace("clib22", "clib2"))
    rec2["external"] = [cl2]
    rg = recipe.build(make_repo("ra-ilp32", rec=rec2, files=files), ROOT, SS, BASE + "/ra1", ext=ext_cache)
    check("rule A: ILP32 root with own units + an external C library: green, units own.c and clib2:src/lib.c, ELF32 objects and image",
          rg["status"] == "green" and rg["units"] == ["clib2:src/lib.c", "own.c"] and rg["elf"] and {elf_class(o["object"]) for o in rg["objects"]} | {elf_class(rg["elf"]["path"])} == {32}
          and any(x["symbol"] == "lib_add" and x["object"] == "clib2:src/lib.c" for x in rg["defined"]), rg.get("failure"))
    rg2 = recipe.build(make_repo("ra-ilp32b", rec=rec2, files=files), ROOT, SS, BASE + "/ra2", ext=ext_cache)
    check("rule A: guest-style root, second build is all cache hits, same stage keys", rg2["stages"]["run"] == 0 and rg2["stage_keys"] == rg["stage_keys"], rg2.get("stages"))
    # -isystem: a warning in a third-party header under -Werror
    c3, h3 = publish("wt", tree({"inc/w.h": "static inline int wf(int a) { return 0; }\n"}))
    wt = {"name": "wt", "repo": "fnv-nx-build", "ref": "wt", "commit": c3, "tree_sha256": h3, "at": "/ext/wt"}
    wfl = {"c": RECIPE["flags"]["c"] + ["-Wall", "-Wextra", "-Werror"], "cxx": [], "asm": []}
    wf = {"toy/a.c": '#include <w.h>\nint f_a(void) { return wf(1); }\n'}
    base = dict(RECIPE, external=[wt], flags=wfl, os_layer=[], generated=[], constants=None, link=None)
    base.pop("constants")
    base.pop("link")
    rs = recipe.build(make_repo("rsys", rec=dict(base, isystem=["/ext/wt/inc"]), files=dict(only_c, **wf)), ROOT, SS, BASE + "/rs1", ext=ext_cache)
    ri = recipe.build(make_repo("rinc", rec=dict(base, include=["/ext/wt/inc"]), files=dict(only_c, **wf)), ROOT, SS, BASE + "/rs2", ext=ext_cache)
    check("isystem: -Werror with a warning in an external header is green under isystem and red under include", rs["status"] == "green"
          and ri["status"] == "red" and ri["failure"]["stage"] == "cc:a.c" and any("unused parameter" in l for l in ri["failure"]["stderr"]), (rs["status"], rs.get("failure"), ri["status"], ri.get("failure")))
    rk = make_repo("risys", rec=dict(base, isystem=["/ext/wt/inc", ROOT + "/inc"]), files=dict(only_c, **wf))
    check("isystem: a path that is not inside an external's at (a tracked path) is refused", any("isystem" in e for e in recipe.check(
        dict(base, isystem=[ROOT + "/inc"]), ROOT, recipe.tracked(rk), recipe.load_admit(rk))))
    # a root's own directory as include: refused (one path per file: /src; #pragma once identity)
    e1 = errs(mut(include=[ROOT + "/inc"]))
    check("include inside the root is refused: use a quoted relative include", any("include %s/inc is inside the root; use a quoted relative include" % ROOT in e for e in e1), e1)
    # the program lookup of the host driver: a bare `as` is found as the driver finds it; a missing program is an error naming it
    hd = "/usr/bin/x86_64-linux-gnu-g++-13"
    p = unitstage.prog(hd, "as")
    check("host driver: bare `-print-prog-name=as` is resolved (programs: list, then PATH of the stage) to an executable file", os.path.isabs(p) and os.access(p, os.X_OK)
          and os.path.basename(p) == "as" and unitstage.gcc_set(hd)[0][1] == p, p)
    try:
        unitstage.prog(hd, "no-such-program-xyz")
        err = ""
    except unitstage.UnitError as x:
        err = str(x)
    check("host driver: a program found nowhere is an error naming it", "no-such-program-xyz" in err, err)


def check_tc_gate(repo, tcc, tiny):
    rc, g, td, txt = gate(repo, "determinism", BASE + "/gtc", BASE + "/tccache")
    print("INFO gate determinism, external compiler + external include tree (2 cold + warm) %.1f s: %s" % (td, g and g["detail"]))
    check("determinism gate with the compiler as an external tree at /opt/tc and a second external: green, accepted by verdict, each fetched once",
          rc == 0 and g["universe"] == 2 == g["certified"] and accepted("determinism", BASE + "/gtc")[0] == "green"
          and sorted(os.listdir(BASE + "/tccache/external")) == sorted([tcc, tiny]), (g, txt))


if __name__ == "__main__":
    sys.exit(main())
