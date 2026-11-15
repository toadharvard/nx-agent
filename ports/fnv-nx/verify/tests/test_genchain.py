#!/usr/bin/env python3
"""Generator stages are hermetic and chain (recipe.Job.generate_one): a 3-stage toy chain whose stage 2 reads stage 1's output and stage 3 reads stage 2's file and
stage 1's DIRECTORY output (`dirs`), all by declared stage refs.
  - the chain is green and its outputs are the tools' (a directory output is ingested whole, files inside it at any depth);
  - a stage that opens a path outside its mounts (an absolute host path, a tracked file it did not declare) fails, and what needs it is blocked;
  - a change of the stage-1 input re-keys stages 1, 2 and 3; a change that leaves stage 1's outputs equal re-keys stage 1 only;
  - a directory output that is empty, or a file outside the declared outputs and directories, is red.
Run: python3 -I <this file>. Needs the declared environment (the recipe refuses to run on another machine)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import recipe  # noqa: E402

fails = 0
T = "ports/fnv-nx/"
R = T + "chain"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def stage(n):
    return {"stage": "%s:gen:%d" % (R, n)}


TOOL1 = 'import os\nd = open("/in/%s/in.txt").read()\nopen("/out/a.txt", "w").write(d.upper())\nos.makedirs("/out/pack/sub", exist_ok=True)\nopen("/out/pack/x.txt", "w").write("x" + d)\nopen("/out/pack/sub/y.txt", "w").write("y")\n' % R
TOOL2 = 'open("/out/b.txt", "w").write(open("/stage/a.txt").read() + "2")\n'
TOOL3 = 'import os\nn = sum(len(f) for _, _, f in os.walk("/stage/pack"))\nopen("/out/c.txt", "w").write(open("/stage/b.txt").read() + str(n))\n'
BAD_ABS = 'import sys\nopen("/out/b.txt", "w").write(open(%r).read())\n'
BAD_TRACKED = 'open("/out/b.txt", "w").write(open("/in/%s/other.txt").read())\n' % R


def make(td, name, in_txt="abc\n", tool2=TOOL2, extra=None, dirs=("pack",), out1=("a.txt",), tool1=TOOL1, noise="n\n"):
    repo = os.path.join(td, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    for i, t in enumerate((tool1, tool2, TOOL3)):
        w(repo, T + "t/s%d.py" % (i + 1), t)
    w(repo, R + "/in.txt", in_txt)
    w(repo, R + "/other.txt", "secret\n")
    w(repo, R + "/noise.txt", noise)
    gens = [{"tool": T + "t/s1.py", "args": [], "inputs": [R + "/in.txt", R + "/noise.txt"], "outputs": list(out1), "dirs": list(dirs)},
            {"tool": T + "t/s2.py", "args": [], "inputs": [dict(stage(0), path="a.txt")], "outputs": ["b.txt"]},
            {"tool": T + "t/s3.py", "args": [], "inputs": [dict(stage(1), path="b.txt"), dict(stage(0), path="pack")], "outputs": ["c.txt"]}]
    w(repo, R + "/build.json", json.dumps({"format": "fnvnx-recipe/1", "generated": gens, "outputs": {"c": "c.txt", "pack": "pack"}}, indent=1))
    w(repo, "ports/fnv-nx/verify/env.json", open(os.path.join(VERIFY, "env.json")).read())
    w(repo, "ports/fnv-nx/verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}, {"path": T + "t", "class": "tool"}, {"path": T + "verify", "class": "tool"}] + (extra or [])}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo


def build(repo, td, tag):
    return recipe.build(repo, R, os.path.join(td, "store"), os.path.join(td, "out-" + tag), 2)


def produced(repo, td, tag):
    """The root's generated outputs as the stages left them: the directory the consumers read (ws/root), after the same externals + generate steps a build runs."""
    j = recipe.Job(repo, R, os.path.join(td, "store"), 2, os.path.join(td, "store", "external"))
    ws = tempfile.mkdtemp(prefix="gc-" + tag, dir=td)
    recipe._copy(repo, [f for f in j.files if recipe.under(f, R)], R, os.path.join(ws, "root"))
    j.externals(ws)
    j.generate(ws)
    return j, os.path.join(ws, "root")


with tempfile.TemporaryDirectory() as td:
    r1 = build(make(td, "r1"), td, "1")
    check("the 3-stage chain is green", r1["status"] == "green", r1.get("failure"))
    j1, root1 = produced(make(td, "p1"), td, "1")
    pk = os.path.join(root1, "pack")
    check("a directory output is ingested whole, files at any depth", os.path.isfile(os.path.join(pk, "x.txt")) and os.path.isfile(os.path.join(pk, "sub", "y.txt")), pk)
    check("stage 3 reads stage 1's directory output (2 files) and stage 2's file", open(os.path.join(root1, "c.txt")).read() == "ABC\n22", os.listdir(root1))
    k1 = set(r1["stage_keys"])
    check("one key per stage", len(k1) == 3, k1)
    r2 = build(make(td, "r2", in_txt="abd\n"), td, "2")
    k2 = set(r2["stage_keys"])
    check("a change of the stage-1 input re-keys all three stages", len(k2 - k1) == 3, (len(k1 & k2)))
    r3 = build(make(td, "r3", noise="other\n"), td, "3")
    k3 = set(r3["stage_keys"])
    check("a stage-1 input that its outputs do not depend on, changed, re-keys stage 1 only", len(k3 - k1) == 1, len(k3 - k1))
    # a path outside the mounts
    outside = os.path.join(td, "outside.txt")
    open(outside, "w").write("host file\n")
    rb = build(make(td, "bad1", tool2=BAD_ABS % outside), td, "b1")
    check("a stage that opens an absolute host path fails (the path is not mounted)", rb["status"] == "red" and any("gen:" in f.get("stage", "") for f in [rb["failure"]]), rb.get("failure"))
    rb = build(make(td, "bad2", tool2=BAD_TRACKED), td, "b2")
    check("a stage that opens a tracked file it did not declare fails", rb["status"] == "red", rb.get("failure"))
    jb, rootb = produced(make(td, "bad2b", tool2=BAD_TRACKED), td, "b2")
    check("what needs the failed stage is blocked, and its output is absent", "b.txt" in jb.blocked and "c.txt" in jb.blocked and not os.path.exists(os.path.join(rootb, "c.txt")), (dict(jb.blocked), os.listdir(rootb)))
    # directory output rules
    rb = build(make(td, "bad3", dirs=("pack", "never")), td, "b3")
    check("a declared directory the generator does not write is red", rb["status"] == "red" and "never/" in json.dumps(rb["failure"]), rb.get("failure"))
    rb = build(make(td, "bad4", tool1=TOOL1 + 'open("/out/stray.txt", "w").write("s")\n'), td, "b4")
    check("a file outside the declared outputs and directories is red", rb["status"] == "red" and "unexpected" in json.dumps(rb["failure"]), rb.get("failure"))
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
