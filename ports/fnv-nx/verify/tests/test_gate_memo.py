#!/usr/bin/env python3
"""Gate items as results of the verdict's store (gate_build.memo: GATE_CACHE/memo/<gate>/<key>.json, the key holds everything the item reads).
memo itself: a miss makes and publishes, a hit reads and does not make, another key makes again, a value that KEEP refuses is not stored, no cache means
make every time, two processes on one key leave one complete file. exepin on a toy git tree: a second run is a hit (one memo file, under 5 s), one changed
source is a miss (a second file), the changed tree gives the verdict of its own content, the unchanged one still hits."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import gate_build  # noqa: E402

FAILS = 0


def check(name, ok, detail=""):
    global FAILS
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    FAILS += 0 if ok else 1


def files(cache, gate):
    d = os.path.join(cache, "memo", gate)
    return sorted(os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs) if os.path.isdir(d) else []


def main():
    tmp = tempfile.mkdtemp(prefix="gate-memo-")
    __import__('atexit').register(__import__('shutil').rmtree, tmp, True)   # a test leaves no scratch behind
    c = os.path.join(tmp, "cache")
    made = []

    def mk(v):
        def f():
            made.append(v)
            return v
        return f
    v, hit = gate_build.memo(c, "g", {"a": 1}, mk({"x": [1, 2]}))
    check("a miss makes and publishes", v == {"x": [1, 2]} and not hit and made == [{"x": [1, 2]}] and len(files(c, "g")) == 1)
    v, hit = gate_build.memo(c, "g", {"a": 1}, mk({"x": "other"}))
    check("a hit reads the stored value and makes nothing", v == {"x": [1, 2]} and hit and len(made) == 1)
    v, hit = gate_build.memo(c, "g", {"a": 2}, mk(7))
    check("another key makes again", v == 7 and not hit and len(files(c, "g")) == 2)
    v, hit = gate_build.memo(c, "g", {"a": 3}, mk({"bad": 1}), keep=lambda x: False)
    check("a value KEEP refuses is returned and not stored", v == {"bad": 1} and not hit and len(files(c, "g")) == 2)
    v, hit = gate_build.memo(None, "g", {"a": 1}, mk(5))
    check("no cache: make every time", v == 5 and not hit)
    check("no temporary name is left", all(".tmp" not in f for f in files(c, "g")))
    code = ("import sys, json; sys.path.insert(0, %r); import gate_build; v, h = gate_build.memo(sys.argv[1], 'p', {'k': 1}, lambda: {'v': 'x' * 200000}); print(json.dumps(len(v['v'])))" % VERIFY)
    ps = [subprocess.Popen([sys.executable, "-I", "-c", code, c], stdout=subprocess.PIPE, text=True) for _ in range(4)]
    outs = [p.communicate()[0].strip() for p in ps]
    check("four processes on one key: all answer, one complete file", outs == ["200000"] * 4 and len(files(c, "p")) == 1 and
          json.load(open(files(c, "p")[0]))["v"] == "x" * 200000, (outs, files(c, "p")))
    # exepin on a toy git tree
    t = os.path.join(tmp, "toy")
    os.makedirs(t + "/ports/fnv-nx")
    open(t + "/ports/fnv-nx/a.py", "w").write("import os\nx = 1\n")
    open(t + "/ports/fnv-nx/b.py", "w").write("y = 2\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run("git init -q && git add -A && git commit -qm x", shell=True, cwd=t, env=env, check=True)
    out = os.path.join(tmp, "out")
    os.makedirs(out)

    def run():
        t0 = time.time()
        r = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "gate_exepin.py")], cwd=t, env=dict(env, GATE_OUT=out, GATE_CACHE=c), capture_output=True, text=True)
        return r, time.time() - t0
    r, s1 = run()
    check("exepin cold: green on the toy tree", r.returncode == 0 and "2/2" in r.stdout, (r.stdout, r.stderr[-300:]))
    n1 = len(files(c, "exepin"))
    r, s2 = run()
    check("exepin warm: same verdict from one memo file, under 5 s", r.returncode == 0 and "2/2" in r.stdout and len(files(c, "exepin")) == n1 == 1 and s2 < 5, (n1, s2))
    open(t + "/ports/fnv-nx/b.py", "w").write("import sys\ny = open(sys.argv[1])\n")
    subprocess.run("git add -A && git commit -qm y", shell=True, cwd=t, env=env, check=True)
    r, _ = run()
    check("exepin: a changed source is another key (a second memo file)", r.returncode == 0 and len(files(c, "exepin")) == 2)
    open(t + "/ports/fnv-nx/b.py", "w").write("y = 2\n")
    subprocess.run("git add -A && git commit -qm z", shell=True, cwd=t, env=env, check=True)
    r, _ = run()
    check("exepin: the first content again hits its own file (still two files)", r.returncode == 0 and len(files(c, "exepin")) == 2)
    print("all passed" if not FAILS else "%d failed" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
