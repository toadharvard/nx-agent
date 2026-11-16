#!/usr/bin/env python3
"""Tool steps (`run` with `exec`, RECIPE.md "Tool steps"): a program of the declared debs runs in their root layout (top-level directories mounted at their own
paths, top-level symlinks as sandbox links), with a scratch tmpfs, the job count and the time limit outside the key, and a directory output.
The debs here are a toy package (a static busybox) through the two seams recipe.deb_pins and Job._deb_tree; the sandbox, the recipe check and the key are the real ones.
  - the step is green; its file and directory outputs and the scratch work are as the script wrote them; JOBS reaches the script;
  - the job count does not change the stage key; a changed input or script does;
  - a later step reads an earlier step's directory output through a stage ref (mounted at /in/N);
  - a step with timeout_s is red at check time (a time limit would decide red or green).
Run: python3 -I <this file>. Needs root, a static busybox, the declared environment (the recipe refuses to run on another machine)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import recipe  # noqa: E402

fails = 0
T = "ports/fnv-nx/"
R = T + "tools"


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


def toy_tree(self, tmp, name, pin):
    """_deb_tree of the toy package: /bin -> usr/bin (a top-level link, like base-files), /usr/bin/busybox and /usr/bin/sh -> busybox, /etc (a second top-level dir)."""
    d = os.path.join(tmp, "deb-" + name)
    if name != "toybb":   # the machine's layout deb (envcheck.RUN_LAYOUT_DEB, named by every step) ships nothing here: the toy package carries the layout
        os.makedirs(d)
        return None
    os.makedirs(os.path.join(d, "usr/bin"))
    shutil.copy(shutil.which("busybox"), os.path.join(d, "usr/bin/busybox"))
    for a in ("sh", "cp", "mkdir", "echo", "cat", "ls", "sleep"):
        os.symlink("busybox", os.path.join(d, "usr/bin", a))
    os.symlink("usr/bin", os.path.join(d, "bin"))
    os.makedirs(os.path.join(d, "etc"))
    open(os.path.join(d, "etc/marker"), "w").write("toy\n")
    return None


recipe.deb_pins = lambda: {"toybb": {"version": "1", "sha256": "0" * 64}, "base-files": {"version": "1", "sha256": "1" * 64}}
recipe.Job._deb_tree = toy_tree
recipe.env_blocked = lambda repo: None   # this machine's pinned packages are not the point of the test


def make(td, name, script, timeout=None, second=True):
    repo = os.path.join(td, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    w(repo, R + "/s1.sh", script)
    w(repo, R + "/s2.sh", "cat /in/1/f > /out/second.txt\ncat /etc/marker >> /out/second.txt\n")
    w(repo, T + "t/g.py", "open('/out/g.txt','w').write('g')\n")
    step1 = {"name": "one", "exec": "/bin/sh", "args": ["/in/1"], "inputs": [R + "/s1.sh"], "debs": ["toybb"], "outputs": ["o.txt"], "dirs": ["d"], "scratch": ["/scratch"]}
    if timeout:
        step1["timeout_s"] = timeout
    step2 = {"name": "two", "exec": "/usr/bin/sh", "args": ["/in/2"], "debs": ["toybb"], "outputs": ["second.txt"],
             "inputs": [{"stage": R + ":run:one", "path": "d"}, R + "/s2.sh"]}
    rec = {"format": "fnvnx-recipe/1", "generated": [{"tool": T + "t/g.py", "args": [], "inputs": [], "outputs": ["g.txt"]}],
           "run": [step1] + ([step2] if second else []), "outputs": {"o": "o.txt", "d": "d"}}
    w(repo, R + "/build.json", json.dumps(rec, indent=1))
    w(repo, "ports/fnv-nx/verify/env.json", open(os.path.join(VERIFY, "env.json")).read())
    w(repo, "ports/fnv-nx/verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}, {"path": T + "t", "class": "tool"}, {"path": T + "verify", "class": "tool"}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo


def produced(repo, td, tag, jobs=2):
    j = recipe.Job(repo, R, os.path.join(td, "store"), jobs, recipe.ext_of_store(os.path.join(td, "store")))
    ws = tempfile.mkdtemp(prefix="ts-" + tag, dir=td)
    recipe._copy(repo, [f for f in j.files if recipe.under(f, R)], R, os.path.join(ws, "root"))
    j.externals(ws)
    j.generate(ws)
    j.run_all(ws)
    return j, os.path.join(ws, "root")


S1 = 'echo $JOBS > /out/o.txt\nmkdir -p /scratch/x\necho hi > /scratch/x/f\ncp -r /scratch/x /out/d\nls / > /out/d/ls.txt\n'
with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
    repo = make(td, "r1", S1)
    errs = recipe.check(json.load(open(os.path.join(repo, R, "build.json"))), R, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    check("the recipe with tool steps is valid", not errs, errs)
    j1, root1 = produced(repo, td, "1", jobs=2)
    rd = lambda p: open(os.path.join(root1, p)).read()   # noqa: E731
    check("a tool step is green", not j1.red, j1.red)
    if not j1.red:
        check("the file output holds the unkeyed JOBS; the directory output holds the scratch work", rd("o.txt") == "2\n" and rd("d/f") == "hi\n", (rd("o.txt"), os.listdir(root1)))
        check("the root layout is the debs' (the /bin link resolved, /usr and /etc mounted, nothing else)", set(rd("d/ls.txt").split()) >= {"bin", "etc", "usr", "scratch", "in", "out"}, rd("d/ls.txt"))
        check("a later step reads the directory output (/in/1) and the debs' /etc", rd("second.txt") == "hi\ntoy\n", rd("second.txt"))
        j2, _ = produced(repo, td, "2", jobs=5)
        check("the job count is not in any stage key", j2.keys == j1.keys and len(j1.keys) >= 3, (len(j1.keys), len(j2.keys)))
    j3, _ = produced(make(td, "r3", S1.replace("echo hi > /scratch/x/f", "echo hi > /scratch/x/f; echo more > /scratch/x/y")), td, "3")
    check("a changed script re-keys the step and what reads it", len(j3.keys - j1.keys) >= 2, len(j3.keys - j1.keys))
    r4 = make(td, "r4", "true\n", timeout=1, second=False)
    j4 = recipe.Job(r4, R, os.path.join(td, "store"), 2, recipe.ext_of_store(os.path.join(td, "store")))
    errs4 = recipe.check(j4.rec, R, j4.files, j4.roots, repo=r4)
    check("a step with timeout_s is red at check time", any("timeout_s" in e for e in errs4), errs4)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
