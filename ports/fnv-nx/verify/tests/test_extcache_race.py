#!/usr/bin/env python3
"""recipe.publish_dir / fetch_externals under concurrency (spec: verify/RECIPE.md "Externals in a shared store"). Real git repository, real fetch, real processes:
N concurrent fetch_externals of one external into one cache and store all succeed and exactly one bare fetch and one checkout exist; an interrupted extract
leaves only a temporary directory that the next run ignores; the loser of a race deletes its own copy; any other rename error is raised."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import cas  # noqa: E402
import recipe  # noqa: E402

FAILS = 0


def check(name, ok, detail=""):
    global FAILS
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    FAILS += 0 if ok else 1


def git(*a, cwd=None):
    return subprocess.run(["git"] + list(a), cwd=cwd, check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")).stdout.strip()


CHILD = """
import os, sys, time
sys.path.insert(0, %r)
import recipe
while not os.path.exists(sys.argv[1]):
    time.sleep(0.001)
e = {"name": "x", "repo": "r", "ref": "r", "commit": sys.argv[2], "tree_sha256": sys.argv[3], "at": "/ext/x"}
got = recipe.fetch_externals([e], sys.argv[4], sys.argv[5], sys.argv[6])
print(got["x"]["host"])
"""


def main():
    base = tempfile.mkdtemp(prefix="extrace-")
    try:
        src = os.path.join(base, "src")
        os.makedirs(os.path.join(src, "bin"))
        for i in range(40):
            open(os.path.join(src, "bin", "f%d" % i), "w").write("content %d\n" % i * 2000)
        git("init", "-q", cwd=src)
        git("add", "-A", cwd=src)
        git("commit", "-q", "-m", "x", cwd=src)
        git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=src)
        commit = git("rev-parse", "HEAD", cwd=src)
        tree = cas.ingest(src, os.path.join(base, "probe-store"), exclude=[".git"]) if False else None
        # the tree of the checkout (no .git): ingest a copy of the checked-out files
        co = os.path.join(base, "co")
        os.makedirs(co)
        git("--work-tree", co, "checkout", "-f", "HEAD", "--", ".", cwd=src)
        tree = cas.ingest(co, os.path.join(base, "probe-store"))
        os.environ["ARTIFACTS_URL"] = src
        store, cache = os.path.join(base, "store"), os.path.join(base, "cache")
        start = os.path.join(base, "go")
        n = 8
        ps = [subprocess.Popen([sys.executable, "-I", "-c", CHILD % os.path.dirname(HERE), start, commit, tree, store, cache, os.path.join(base, "ws%d" % i)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ)) for i in range(n)]
        open(start, "w").close()
        outs = [p.communicate() for p in ps]
        rcs = [p.returncode for p in ps]
        check("%d concurrent fetch_externals of one external all succeed" % n, rcs == [0] * n, [o[1][-300:] for o, r in zip(outs, rcs) if r])
        names = sorted(os.listdir(cache))
        check("exactly one bare fetch and one checkout exist, no temporary directory remains", names == [commit, commit + ".co"], names)
        check("all processes got the same published checkout", len({o[0].strip() for o in outs}) == 1, [o[0] for o in outs])
        # an interrupted extract: only a temporary directory, never a partial target; the next run ignores it
        shutil.rmtree(os.path.join(cache, commit + ".co"))
        os.makedirs(os.path.join(cache, commit + ".co.tmp1-dead", "bin"))
        open(os.path.join(cache, commit + ".co.tmp1-dead", "bin", "f0"), "w").write("partial")
        e = {"name": "x", "repo": "r", "ref": "r", "commit": commit, "tree_sha256": tree, "at": "/ext/x"}
        shutil.rmtree(os.path.join(store, "external-facts"))
        got = recipe.fetch_externals([e], store, cache, os.path.join(base, "ws-again"))
        check("an interrupted extract leaves only its temporary directory, and the next run ignores it", got["x"]["host"] == os.path.join(cache, commit + ".co")
              and cas.ingest(got["x"]["host"], os.path.join(base, "probe-store")) == tree and os.path.isdir(os.path.join(cache, commit + ".co.tmp1-dead")), got)
        # publish_dir: the loser deletes its copy and uses the published one; another error is raised
        fin, tmp = os.path.join(base, "fin"), os.path.join(base, "fin.tmp")
        os.makedirs(os.path.join(fin, "a"))
        os.makedirs(os.path.join(tmp, "b"))
        recipe.publish_dir(tmp, fin)
        check("the loser of a race deletes its own copy and keeps the published one", not os.path.exists(tmp) and os.path.isdir(os.path.join(fin, "a")))
        open(os.path.join(base, "isfile"), "w").close()
        os.makedirs(os.path.join(base, "t2", "c"))
        try:
            recipe.publish_dir(os.path.join(base, "t2"), os.path.join(base, "isfile"))
            raised = False
        except OSError:
            raised = True
        check("a rename error that is not a lost race is raised and its copy deleted", raised and not os.path.exists(os.path.join(base, "t2")))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("fails: %d" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
