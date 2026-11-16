#!/usr/bin/env python3
"""recipe.ROOTFS_MEMO: the per-process memo of a deb set's root layout (Job._rootfs) hands every step COPIES of its lists, so a step that appends its own inputs
(`/in/N`, the python runtime) never changes the memo or another step's mounts. Two tool steps of one process with the same deb set and different inputs:
  - each step's mount list is the layout plus exactly its own /in/N entries; the second never holds the first's inputs;
  - the lists a step gets are not the memo's lists (a mutation of one does not reach the memo);
  - the memo entry is equal before and after both steps (and holds no /in/ entry).
The layout comes from the real Job._rootfs over a deb tree that ships a file; no function of recipe is replaced except the deb download (Job._deb_tree: the unpack
of a pinned deb, an input of the boundary).
Run: python3 -I <this file>."""
import copy
import json
import os
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


recipe.deb_pins = lambda: {"base-files": {"version": "1", "sha256": "1" * 64}}


def toy_tree(self, tmp, name, pin):
    d = os.path.join(tmp, "deb-" + name, "usr", "share")
    os.makedirs(d)
    open(os.path.join(d, "f"), "w").write("layout\n")
    os.symlink("usr/share", os.path.join(tmp, "deb-" + name, "lib"))
    return None


recipe.Job._deb_tree = toy_tree


def main():
    with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
        repo = os.path.join(td, "repo")
        os.makedirs(os.path.join(repo, R))
        os.makedirs(os.path.join(repo, T + "verify"))
        git(repo, "init", "-q")
        open(os.path.join(repo, R, "build.json"), "w").write(json.dumps({"format": "fnvnx-recipe/1", "run": [], "outputs": {}}))
        open(os.path.join(repo, T + "verify/admit.json"), "w").write(json.dumps({"format": "fnvnx-admit/1", "roots": [
            {"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}]}))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "toy")
        store = os.path.join(td, "store")
        j = recipe.Job(repo, R, store, 2, recipe.ext_of_store(store))
        g = {"name": "s", "debs": ["base-files"]}
        recipe.ROOTFS_MEMO.clear()
        t1, t2 = tempfile.mkdtemp(prefix="s1", dir=td), tempfile.mkdtemp(prefix="s2", dir=td)
        ins1, links1 = j._rootfs(t1, g)
        n_rootfs = len(ins1)
        mkey = next(iter(recipe.ROOTFS_MEMO))
        before = copy.deepcopy(recipe.ROOTFS_MEMO[mkey][:2])
        check("the first step builds the layout: /usr is an input and /lib a link", any(i["at"] == "/usr" for i in ins1) and any(l["at"] == "/lib" for l in links1), (ins1, links1))
        ins1.append({"at": "/in/1", "tree": "own-one"})   # what run_one does after _rootfs: its own inputs
        links1.append({"at": "/x1", "to": "y"})
        ins2, links2 = j._rootfs(t2, g)
        check("the second step, same debs, takes the memo: its list is the layout alone, none of the first step's inputs or links",
              [i["at"] for i in ins2] == [i["at"] for i in ins1[:n_rootfs]] and len(ins2) == n_rootfs and not any(i["at"].startswith("/in/") for i in ins2)
              and all(l["at"] != "/x1" for l in links2), (ins2, links2))
        ins2.append({"at": "/in/1", "tree": "own-two"})
        check("each step's mounts are the layout plus exactly its own /in/N entry",
              [i for i in ins1 if i["at"] == "/in/1"] == [{"at": "/in/1", "tree": "own-one"}] and [i for i in ins2 if i["at"] == "/in/1"] == [{"at": "/in/1", "tree": "own-two"}]
              and len(ins1) == len(ins2) == n_rootfs + 1)
        check("the lists of a step are not the memo's lists", ins2 is not recipe.ROOTFS_MEMO[mkey][0] and links2 is not recipe.ROOTFS_MEMO[mkey][1]
              and ins1 is not recipe.ROOTFS_MEMO[mkey][0])
        check("the memo entry is unchanged after both steps and holds no /in/ entry", recipe.ROOTFS_MEMO[mkey][:2] == before
              and not any(i["at"].startswith("/in/") for i in recipe.ROOTFS_MEMO[mkey][0]), (recipe.ROOTFS_MEMO[mkey][:2], before))
        check("the second step finds the unpacked deb tree linked under its own tmp (the memo's home)", os.path.islink(os.path.join(t2, "deb-base-files")))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
