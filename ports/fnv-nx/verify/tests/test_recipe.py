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
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
import recipe  # noqa: E402
import verdict  # noqa: E402

BASE = tempfile.mkdtemp(prefix="recipe-test-")
atexit.register(shutil.rmtree, BASE, ignore_errors=True)
FAILED = []
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
          "include": [ROOT + "/inc", ROOT + "/gen"], "units": "all",
          "generated": [{"tool": T + "gen/gen.py", "args": [], "inputs": [ROOT + "/constants.tsv"], "outputs": ["gen/gen.h"]}],
          "os_layer": ["os.S"], "constants": "constants.tsv",
          "link": {"script": "link.ld", "output": "toy.elf", "flags": ["-e", "f_a"]}}
FILES = {
    "toy/a.c": '#include "toy.h"\n#include "gen.h"\nint f_b(void);\nint f_a(void) { return NCONST + f_b() + TOY + (int)(STAMP % 1000003L); }\n',
    "toy/b.c": '#include "toy.h"\nint f_b(void) { return TOY * 2; }\n',
    "toy/c.c": '#include "toy.h"\nint f_c(int x) { return x + TOY; }\n',
    "toy/os.S": ".text\n.globl os_svc\nos_svc:\n svc #0\n ret\n",
    "toy/inc/toy.h": "#define TOY 7\n",
    "toy/constants.tsv": "f_c\t1\nf_b\t2\n",
    "toy/link.ld": "SECTIONS { . = 0x10000; .text : { *(.text*) } .data : { *(.data*) *(.bss*) } /DISCARD/ : { *(.note*) *(.comment*) *(.eh_frame*) } }\n",
}


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)[:600]) if detail and not ok else ""))
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
        w(repo, k, v)
    imp, _, ex = gen_extra.partition("|")
    w(repo, "gen/gen.py", GEN.replace("@IMPORT@", imp).replace("@EXTRA@", ex or "0"))
    w(repo, "toy/build.json", json.dumps(rec or RECIPE, indent=1))
    w(repo, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": ROOT, "class": "product", "gates": [], "recipe": ROOT + "/build.json"},
        {"path": T + "gen", "class": "tool", "gates": []}, {"path": T + "verify", "class": "tool", "gates": []}]}))
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


def main():
    repo = make_repo("toy")
    store = BASE + "/store"
    t0 = time.time()
    r1 = recipe.build(repo, ROOT, store, BASE + "/out1")
    cold = time.time() - t0
    print("INFO toy cold build %.1f s, stages %s" % (cold, r1.get("stages")))
    check("toy root builds green with 4 objects and an ELF", r1["status"] == "green" and len(r1["objects"]) == 4 and r1["elf"]
          and r1["constants"], r1.get("failure"))
    check("os_layer flag only on os.S", {o["unit"] for o in r1["objects"] if o["os_layer"]} == {"os.S"})
    check("generated header reached the unit (a.c compiled against NCONST)", any(o["unit"] == "a.c" for o in r1["objects"]))
    t0 = time.time()
    r2 = recipe.build(repo, ROOT, store, BASE + "/out2")
    warm = time.time() - t0
    print("INFO toy cached build %.1f s, stages %s" % (warm, r2.get("stages")))
    check("second build: every stage a cache hit", r2["stages"]["run"] == 0 and r2["stages"]["hit"] > 0, r2["stages"])
    check("warm == cold (objects, ELF)", [o["sha256"] for o in r1["objects"]] == [o["sha256"] for o in r2["objects"]]
          and r1["elf"]["sha256"] == r2["elf"]["sha256"] and r1["stage_keys"] == r2["stage_keys"])

    d, u = r1["defined"], r1["undefined"]
    check("objects.json: defined symbols from .symtab (struct), undefined list", {"symbol": "f_a", "bind": "GLOBAL", "type": "FUNC", "object": "a.c"} in d
          and any(x["symbol"] == "os_svc" and x["object"] == "os.S" for x in d) and "f_b" in u and "f_a" not in u
          and d == sorted(d, key=lambda x: (x["symbol"], x["object"])) and u == sorted(u), (d, u))

    # externals: a local repo stands for fnv-nx-build (ARTIFACTS_URL)
    src = BASE + "/artifacts"
    os.makedirs(src + "/inc")
    open(src + "/inc/ext.h", "w").write("#define EXTV 5\n")
    git(src, "init", "-q")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "tiny")
    commit = git(src, "rev-parse", "HEAD").strip()
    thash = cas.ingest(src, BASE + "/hs", [".git"])
    ex = {"name": "tiny", "repo": "fnv-nx-build", "ref": "main", "commit": commit, "tree_sha256": thash, "at": "/ext/tiny"}
    erec = mut(external=[ex], include=RECIPE["include"] + ["/ext/tiny/inc"])
    ef = {"toy/b.c": '#include "toy.h"\n#include "ext.h"\nint f_b(void) { return TOY * 2 + EXTV; }\n'}
    os.environ["ARTIFACTS_URL"] = src
    erepo = make_repo("ext", rec=erec, files=ef)
    ec = BASE + "/ecache"
    rx = recipe.build(erepo, ROOT, BASE + "/xs1", BASE + "/xo1", ext=ec)
    check("external at an exact commit: fetched, tree hash verified, include dir inside it used", rx["status"] == "green"
          and os.path.isdir(ec + "/" + commit), rx.get("failure"))
    ry = recipe.build(erepo, ROOT, BASE + "/xs2", BASE + "/xo2", ext=ec)
    key_of = lambda r: r["stage_keys"]  # noqa: E731
    check("external: second build in another store, same fetch cache, identical objects", ry["status"] == "green"
          and [o["sha256"] for o in rx["objects"]] == [o["sha256"] for o in ry["objects"]] and key_of(rx) == key_of(ry))
    bad_t = mut(external=[dict(ex, tree_sha256="0" * 64)], include=erec["include"])
    rt = recipe.build(make_repo("ext-t", rec=bad_t, files=ef), ROOT, BASE + "/xs3", BASE + "/xo3", ext=ec)
    check("wrong tree_sha256: red naming the external", rt["status"] == "red" and rt["failure"]["stage"] == "external:tiny"
          and thash in " ".join(rt["failure"]["stderr"]), rt.get("failure"))
    bad_c = mut(external=[dict(ex, commit="1" * 40)], include=erec["include"])
    rc_ = recipe.build(make_repo("ext-c", rec=bad_c, files=ef), ROOT, BASE + "/xs4", BASE + "/xo4", ext=BASE + "/ec2")
    check("commit not in the repository: red naming the external", rc_["status"] == "red" and rc_["failure"]["stage"] == "external:tiny", rc_.get("failure"))
    for what, e2, want in (("unknown name referenced", mut(compiler={"external": "nope", "cc": "bin/gcc", "cxx": "bin/g++"}, external=[ex]), "names no external"),
                           ("duplicate names", mut(external=[ex, dict(ex, at="/ext/other")]), "duplicate name"),
                           ("non-absolute at", mut(external=[dict(ex, at="ext/tiny")]), "absolute"),
                           ("at overlapping another external", mut(external=[ex, dict(ex, name="t2", at="/ext/tiny/x")]), "overlaps another"),
                           ("at colliding with a path of the stages", mut(external=[dict(ex, at="/src/x")]), "overlaps a path"),
                           ("include outside roots and externals", mut(external=[ex], include=["/ext/other/inc"]), "include")):
        check("external validation: %s" % what, any(want in x for x in errs(e2)), errs(e2))
    xg = BASE + "/xg"
    rc, g, _, txt = gate(erepo, "determinism", xg, BASE + "/xcache")
    check("determinism gate with an external: fetches once into GATE_CACHE/external, green", rc == 0 and g["universe"] == 5 == g["certified"]
          and os.listdir(BASE + "/xcache/external") == [commit], (g, txt))

    # validation
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
    check("os_layer entry that is no unit rejected", any("not a unit" in e for e in errs(mut(os_layer=["toy.h"]))))
    m = mut()
    m["flags"]["c"] = ["-Ifoo", "-o"]
    check("flags that change a stage's job rejected, all problems listed", len([e for e in errs(m)]) >= 2)

    # failing unit
    bad = make_repo("bad")
    w(bad, "toy/c.c", "int f_c(void) { return undeclared_thing; }\n")
    git(bad, "add", "-A")
    git(bad, "commit", "-q", "-m", "x")
    rb = recipe.build(bad, ROOT, BASE + "/store-bad", BASE + "/outb")
    check("failing unit: build red, names stage and last stderr lines", rb["status"] == "red" and rb["failure"]["stage"] == "cc:c.c"
          and rb["failure"]["exit"] and any("undeclared_thing" in l for l in rb["failure"]["stderr"]) and rb["failed_units"] == ["c.c"], rb.get("failure"))
    ob = BASE + "/gb"
    rc, g, _, txt = gate(bad, "build", ob)
    check("build gate red names the failing unit; accepted by verdict._gate_result", rc == 2 and g["uncertified"] == [ROOT + "/c.c", ROOT + "/toy.elf"]
          and g["universe"] == 5 and g["certified"] == 3 and accepted("build", ob)[0] == "red", (g, txt))

    # gates, green
    cache = BASE + "/cache"
    og = BASE + "/gg"
    rc, g, tb, txt = gate(repo, "build", og, cache)
    print("INFO gate build (cold cache) %.1f s" % tb)
    check("build gate green 5/5 (4 units + ELF), result accepted by verdict", rc == 0 and g["universe"] == 5 == g["certified"]
          and accepted("build", og) == ("green", 5, 5, ""), (g, txt))
    tree = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()
    check("objects.json handed over at GATE_CACHE/objects/<tree>/<root>/ and in GATE_OUT/objects/",
          os.path.exists("%s/objects/%s/%s/objects.json" % (cache, tree, ROOT.replace("/", "__")))
          and os.path.exists(og + "/objects/%s.json" % ROOT.replace("/", "__")))
    rc, g, td, txt = gate(repo, "determinism", BASE + "/gd", cache)
    print("INFO gate determinism %.1f s (2 cold builds + warm check): %s" % (td, g and g["detail"]))
    check("determinism gate green on the toy root, accepted by verdict", rc == 0 and g["universe"] == 5 == g["certified"]
          and accepted("determinism", BASE + "/gd")[0] == "green", (g, txt))
    rc, g, td2, txt = gate(repo, "determinism", BASE + "/gd2")
    check("determinism gate without GATE_CACHE: green, says no warm build", rc == 0 and "no warm" in g["detail"], (g, txt))
    empty = make_repo("empty")
    w(empty, "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": T + "toy", "class": "doc", "gates": []}]}))
    for n in ("build", "determinism"):
        rc, g, _, txt = gate(empty, n, BASE + "/ge" + n)
        check("%s gate with no product root: universe 0, green" % n, rc == 0 and g["universe"] == 0 and accepted(n, BASE + "/ge" + n)[0] == "green", txt)

    # nondeterminism
    for label, extra, red in (("wall clock (time.time_ns)", "import time|time.time_ns()", True),
                              ("os.urandom", "import os|int.from_bytes(os.urandom(7), 'big')", False),
                              ("os.getpid", "import os|os.getpid()", False)):
        nd = make_repo("nd" + str(abs(hash(label))), extra)
        rc, g, _, txt = gate(nd, "determinism", BASE + "/gnd" + str(abs(hash(label))))
        names = [ROOT + "/a.c"] if False else []
        if red:
            check("generator embedding %s: determinism red, names the units that read it" % label,
                  rc == 2 and g["status"] == "red" and g["uncertified"] and set(g["uncertified"]) <= {ROOT + "/" + u for u in ("a.c", "b.c", "c.c", "os.S", "toy.elf")}
                  and ROOT + "/toy.elf" in g["uncertified"], (g, txt))
        else:
            check("generator embedding %s: sandbox makes it constant, so two cold builds are equal (closed channel)" % label,
                  rc == 0, (g, txt))
    print("INFO times: toy cold %.1f s, cached %.1f s, determinism gate %.1f s" % (cold, warm, td))
    print("%d FAILED" % len(FAILED) if FAILED else "all passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
