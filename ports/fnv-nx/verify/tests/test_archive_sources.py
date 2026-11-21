#!/usr/bin/env python3
"""Two recipe mechanisms (RECIPE.md "Generated sources of a run step: <step>:<dir>" and "Archive inputs"):
  1. a `sources` line `<run step>:<dir>`: the units are the unit-extension files of a declared `dirs` output of a run step of the SAME root. Red: a step of another root or an
     unknown name, a directory that is no declared dirs output of the step (a path outside it), an empty unit set, a file name that is also a tracked unit.
  2. a run-step input {"archive": ROOT}: ROOT's objects as one archive (recipe.ar_bytes), keyed by content. Red: a malformed ref, the root itself, a non-root, a root that is not
     `uses`d, another compiler record, no units, a CC/generated step (tool steps only). Green: a toy lib/consumer pair: the consumer's archive changes when a lib unit changes.
Run: FNVNX_ARTIFACTS=<fnv-nx-build checkout> python3 -I <this file> from anywhere (git, gcc, root, the sandbox)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from verify import recipe  # noqa: E402

fails = 0
T = "ports/fnv-nx/"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:500]))
    fails += 0 if ok else 1


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout.strip()


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def make(td, lib_cc="gcc-13", con_cc="gcc-13"):
    repo = os.path.join(td, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    step = {"name": "gen", "exec": "/usr/bin/bash", "args": ["/in/1"], "inputs": [T + "con/gen.sh"], "debs": {"profile": "compiler-build"}, "outputs": ["units.list"], "dirs": ["u"]}
    lib = {"format": "fnvnx-recipe/1", "compiler": {"cc": lib_cc, "cxx": "g++-13"}, "units": "all", "include": [], "flags": {"c": ["-O2"], "cxx": [], "asm": []}, "outputs": {"l": "l.c"}}
    con = {"format": "fnvnx-recipe/1", "compiler": {"cc": con_cc, "cxx": "g++-13"}, "units": "all", "include": [], "flags": {"c": ["-O2"], "cxx": [], "asm": []}, "outputs": {"c": "c.c"},
           "uses": [{"root": T + "lib", "output": "l"}], "sources": "units.list", "run": [step]}
    w(repo, T + "lib/build.json", json.dumps(lib))
    w(repo, T + "lib/l.c", "int l(void){return 1;}\n")
    w(repo, T + "con/build.json", json.dumps(con))
    w(repo, T + "con/gen.sh", "true\n")
    w(repo, T + "con/c.c", "int c(void){return 2;}\n")
    w(repo, T + "con/units.list", "gen:u\n")
    w(repo, T + "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": T + "lib", "class": "product", "target": "host", "recipe": T + "lib/build.json"},
        {"path": T + "con", "class": "product", "target": "host", "recipe": T + "con/build.json"}]}))
    w(repo, T + "verify/env.json", open(os.path.join(HERE, "..", "env.json")).read())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo


def job(repo, rec_edit=None):
    j = recipe.Job(repo, T + "con", os.path.join(repo, "..", "store"), 1, os.path.join(repo, "..", "ext"))
    if rec_edit:
        j.rec = rec_edit(dict(j.rec))
    return j


def sources_red(j, ws, line):
    w(ws, "root/units.list", line + "\n")
    try:
        j.generated_sources(ws)
        return None
    except recipe.BuildRed as r:
        return " ".join(r.args[0]["stderr"])


with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
    repo = make(os.path.join(td, "a"))
    j = job(repo)
    ws = os.path.join(td, "ws")
    os.makedirs(os.path.join(ws, "root", "u", "mpn"))
    w(ws, "root/u/a.c", "int a;\n")
    w(ws, "root/u/mpn/b.c", "int b;\n")
    w(ws, "root/u/mpn/skip.h", "int h;\n")
    w(ws, "root/units.list", "gen:u\n")
    got = j.generated_sources(ws)
    check("sources `<step>:<dir>`: the sorted unit files of the directory, headers are no unit", got == ["u/a.c", "u/mpn/b.c"], got)
    r = sources_red(j, ws, "other:u")
    check("a step name that is no step of this root (another root's step) is red", r and "names no external" in r, r)
    r = sources_red(j, ws, "gen:v")
    check("a directory that is no declared dirs output of the step is red", r and "not a declared dirs output" in r, r)
    r = sources_red(j, ws, "gen:../u")
    check("a path outside the directory is red (the line grammar or the declared-name rule)", r and ("not a declared dirs output" in r or "is not <external or root>" in r), r)
    r = sources_red(j, ws, "gen:u/mpn")
    check("a subdirectory of the output is no declared output either (the rule is the declared name)", r and "not a declared dirs output" in r, r)
    os.makedirs(os.path.join(td, "ws2", "root", "u"))
    r = sources_red(j, os.path.join(td, "ws2"), "gen:u")
    check("an empty unit set is red", r and "holds no unit" in r, r)
    clash = os.path.join(td, "ws3")
    w(clash, "root/u/x.c", "int x;\n")
    j3 = job(repo, lambda rec: dict(rec))
    j3.files = list(j3.files) + [T + "con/u/x.c"]
    r = sources_red(j3, clash, "gen:u")
    check("a file name that is also a tracked unit is red", r and "tracked unit" in r, r)

    # --- archive input: validation
    roots = recipe.load_admit(repo)
    rec = recipe.load_recipe(repo, roots, T + "con")
    ae = lambda ref, r=rec, ro=roots, rt=repo: recipe.archive_errors("t", ref, T + "con", r, ro, rt)   # noqa: E731
    check("archive of a used root with the same compiler: green", ae({"archive": T + "lib"}) == [], ae({"archive": T + "lib"}))
    check("archive of the root itself is red", any("itself" in e for e in ae({"archive": T + "con"})))
    check("archive of a non-root is red", any("not an admitted root" in e for e in ae({"archive": T + "ghost"})))
    check("archive of a root this recipe does not `uses` is red", any("`uses`" in e for e in ae({"archive": T + "lib"}, dict(rec, uses=[]))))
    check("a malformed archive ref is red", any("is not {archive" in e for e in ae({"archive": 1})) and not recipe.is_archive({"archive": "x", "k": "y"}))
    repo_b = make(os.path.join(td, "b"), lib_cc="g++-13")
    rb = recipe.load_admit(repo_b)
    check("an archive of a root with another compiler record is red", any("another compiler" in e for e in recipe.archive_errors("t", {"archive": T + "lib"}, T + "con", recipe.load_recipe(repo_b, rb, T + "con"), rb, repo_b)))
    # tool steps only: the same ref in a non-tool run step is red through recipe.check
    recipe.env_blocked = lambda r: None
    bad = dict(rec, run=[dict(rec["run"][0], inputs=[{"archive": T + "lib"}])])
    bad["run"][0].pop("exec")
    bad["run"][0].pop("dirs")
    bad["run"][0]["debs"] = []
    bad["run"][0]["artifact"] = T + "lib:l"
    e = recipe.check(bad, T + "con", recipe.tracked(repo), roots, None, repo)
    check("an archive input of a step that is no tool step (exec) is red", any("archive input is for a tool step" in x for x in e), e)

    # --- the toy pair: the archive of the lib, built from two real objects, and the key (the tree) changes with a lib unit
    def obj(text, name):
        c = os.path.join(td, name + ".c")
        o = os.path.join(td, name + ".o")
        open(c, "w").write(text)
        subprocess.run(["gcc-13", "-c", "-O0", c, "-o", o], check=True)
        return o

    def tree(objs, store):
        os.makedirs(store, exist_ok=True)
        jj = recipe.Job(repo, T + "con", store, 1, os.path.join(td, "ext"))
        jj.archives = {}
        jj.archives[T + "lib"] = {"status": "green", "objects": [{"unit": u, "object": o} for u, o in objs], "stage_keys": []}
        tmp = tempfile.mkdtemp(dir=td)
        return jj.archive_tree(tmp, tmp, 1, {"archive": T + "lib"})
    o1, o2, o2b = obj("int f(void){return 1;}\n", "f1"), obj("int g(void){return 2;}\n", "g"), obj("int g(void){return 3;}\n", "g2")
    t1 = tree([("u/f.c", o1), ("u/g.c", o2)], os.path.join(td, "s1"))
    t2 = tree([("u/g.c", o2), ("u/f.c", o1)], os.path.join(td, "s2"))
    t3 = tree([("u/f.c", o1), ("u/g.c", o2b)], os.path.join(td, "s3"))
    check("the archive tree is a function of the objects' content (order of the list aside)", t1 == t2, (t1, t2))
    check("a changed lib unit is a changed archive: the consumer's stage key changes", t1 != t3, (t1, t3))
    import glob
    mt = os.path.join(td, "mt")
    recipe.cas.materialize(t1, os.path.join(td, "s1"), mt)
    check("an archive mount holds only the archive (no directory outputs of the root)", os.listdir(mt) == ["liblib.a"], os.listdir(mt))
    red = recipe.Job(repo, T + "con", os.path.join(td, "s4"), 1, os.path.join(td, "ext"))
    red.archives = {}
    red.archives[T + "lib"] = {"status": "red", "failure": {"stage": "cc:u/f.c", "exit": 1, "stderr": ["boom"]}, "objects": [], "stage_keys": []}
    try:
        t = tempfile.mkdtemp(dir=td)
        red.archive_tree(t, t, 1, {"archive": T + "lib"})
        check("a red root is a red archive input (never an empty archive)", False)
    except recipe.BuildRed as x:
        check("a red root is a red archive input (never an empty archive)", "is red" in " ".join(x.args[0]["stderr"]), x.args)

# --- behavioural: a run step reads another root's header directory through include_uses (and only through it)
def toy_uses(td, with_iu):
    r = os.path.join(td, "u%d" % with_iu)
    os.makedirs(r)
    git(r, "init", "-q")
    lib = {"format": "fnvnx-recipe/1", "outputs": {"INC": "h"}}
    step = {"name": "rd", "exec": "/usr/bin/bash", "args": ["/in/1"], "inputs": [T + "con/rd.sh"], "debs": {"profile": "compiler-build"}, "outputs": ["o.txt"]}
    con = {"format": "fnvnx-recipe/1", "outputs": {"c": "o.txt"}, "uses": [{"root": T + "lib", "output": "INC"}], "run": [step]}
    if with_iu:
        con["include_uses"] = [{"root": T + "lib", "output": "INC"}]
    w(r, T + "lib/build.json", json.dumps(lib))
    w(r, T + "lib/h/x.h", "#define TOY 42\n")
    w(r, T + "con/build.json", json.dumps(con))
    w(r, T + "con/rd.sh", "cp /uses/%slib/INC/x.h /out/o.txt\n" % T)
    w(r, T + "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": T + "lib", "class": "product", "target": "host", "recipe": T + "lib/build.json"},
        {"path": T + "con", "class": "product", "target": "host", "recipe": T + "con/build.json"}]}))
    w(r, T + "verify/env.json", open(os.path.join(HERE, "..", "env.json")).read())
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "toy")
    return r

def run_toy(repo):
    jj = recipe.Job(repo, T + "con", os.path.join(os.path.dirname(repo), "bstore"), 1, os.path.join(os.path.dirname(repo), "bext"))
    ws = tempfile.mkdtemp(dir=os.path.dirname(repo))
    jj.run_all(ws)
    out = os.path.join(ws, "root", "o.txt")
    return jj, (open(out).read() if os.path.exists(out) else None)

with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td2:
    os.makedirs(os.path.join(td2, "bstore"))
    recipe.set_artifacts(recipe.ext_dir(td2), os.environ.get("FNVNX_ARTIFACTS", ""))
    jj, got = run_toy(toy_uses(td2, 1))
    check("a run step reads the header dir of a used root through include_uses", got == "#define TOY 42\n" and not jj.red, (got, jj.red[:1]))
    jj, got = run_toy(toy_uses(td2, 0))
    check("without include_uses the step does not see the header: the step is red", got is None and jj.red, (got, jj.red[:1]))
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
