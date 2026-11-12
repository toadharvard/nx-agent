#!/usr/bin/env python3
"""Gate "selftests" (fnvnx-gate/1): every test file of the given directories passes.
    python3 -I gate_tests.py [--timeout-s N] [--jobs N] [--cache DIR] [--key-dirs D...] [--] DIR...
Run in the checkout root with env GATE_OUT; writes GATE_OUT/selftests.json (+ GATE_OUT/selftests.times.json).
Universe: DIR/tests/test_*.py for each DIR given (a missing DIR contributes nothing), in argument order, each DIR's files
sorted by name; items are repo-relative paths. Each file runs as `python3 -I FILE` in the checkout with a per-file timeout
(default 300 s; a timeout kills the process group and is a failure). Certified: exit status 0. No skip logic: a test that
cannot run in this environment (root, namespaces, strace) is uncertified. Zero files: green, universe 0. Exit 0 iff green.
--jobs N (default 1): N test files run at once, each in its own process; results are reported in sorted universe order.
  A test must therefore keep its scratch under its own tempfile.mkdtemp directory (no fixed path). Each file runs with
  TMPDIR set to a fresh directory of its own, and the gate removes that directory (and kills what is left of the file's
  process group) when the file ends, passed, failed or timed out: no test leaves anything in the temporary directory. No test needs a serial
  group: sandbox namespaces are per process and every store/work directory is per run.
--cache DIR (default $GATE_CACHE, see verdict.py; none = no caching): the result of ONE test file is cached under its own key.
  KEY(test) = sha256 over: the test's repo-relative path, the declared environment (envcheck id of the env.json next to this
  script), the python version string, the per-file timeout, and the VIEW HASH. The VIEW of a test is the checkout (the cwd of the gate,
  .git and __pycache__ left out) WITHOUT the other test files (DIR/tests/test_*.py of the universe) that the test does not name: the
  test itself plus the test files in its closure, where the closure is taken over the AST: an imported module or a string constant
  that equals the basename (without .py) of a test file puts every test file of that name in, transitively (a superset of what the
  test can import; dynamic loading by a computed name is not in the view and fails the same way again, never silently).
  View hash = sha256 over the sorted (relative path, executable bit, sha256 of content) of all files of the view.
  (The result cache directory, if it lies inside the checkout, is not part of the view.)
  The view is a git repository of its own (git init, one commit of the view with fixed identity and dates), so what git says in
  it is a function of the view.
  Completeness by construction, not by guess: the test RUNS in a private copy of exactly that view (the other test files are not in
  the copy, nothing else is left out), so a read of any file is a read of a keyed file or fails the same way again, and its result
  is a function of the key. Inputs the checkout names but does not contain (the pinned externals, e.g. guest_compiler.json) are
  files of the view, so their pins are in the key; what they pin is content-addressed by that pin. Declared assumptions: the test is
  deterministic in (view, env, python) - a test that reads the clock, the network or a path outside the view is a defect of the test.
  A result is stored when the test ENDED (exit status 0 = passed, any other exit status = failed, with the last 2000 bytes of
  its output kept): a red result is cached and stays red with the same text. A timeout is not a result of the inputs (it is a
  property of the load) and is never stored. An unchanged test is never rerun; a changed test file reruns only itself; a changed
  other file of the view reruns every test (their views differ). Per-file cache path: DIR_CACHE/selftests/<KEY>.json.
Result extra keys: "files": [{"name","seconds","status","cached"}] (status passed|failed; cached true when not run; the same list in
selftests.times.json) and "recomputed": the number of test files actually run (0 on an unchanged tree)."""
import ast
import concurrent.futures
import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import envcheck  # noqa: E402

CACHE_FORMAT = "fnvnx-selftests-cache/2"
TAIL = 2000

def tree_files(root, exclude, skipdir=None):
    """(rel, exec bit, sha256) of every file under ROOT (sorted) except .git, __pycache__, the directory SKIPDIR (the result cache, when
    it lies inside the checkout) and the rel paths in EXCLUDE."""
    out = []
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in (".git", "__pycache__") and os.path.abspath(os.path.join(d, x)) != skipdir)
        for n in sorted(names):
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root)
            if rel in exclude or n == ".git":
                continue
            if os.path.islink(p):
                out.append((rel, "l", hashlib.sha256(os.readlink(p).encode()).hexdigest()))
            else:
                with open(p, "rb") as fh:
                    out.append((rel, "x" if os.access(p, os.X_OK) else "-", hashlib.sha256(fh.read()).hexdigest()))
    return sorted(out)

def test_closure(files):
    """{test file: set of test files (itself included) it names, transitively}: modules imported and string constants equal to a
    test file's basename, over the AST. Superset by construction: any name that could load a sibling test is a name here."""
    by = {}
    for f in files:
        by.setdefault(os.path.basename(f)[:-3], []).append(f)
    direct = {}
    for f in files:
        names = set()
        try:
            tree = ast.parse(open(f, "rb").read())
        except (OSError, SyntaxError, ValueError):
            tree = None
        for n in ast.walk(tree) if tree else ():
            if isinstance(n, ast.Import):
                names.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                names.add(n.module.split(".")[0])
                names.update(a.name for a in n.names)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                names.add(n.value[:-3] if n.value.endswith(".py") else n.value)
        direct[f] = {g for nm in names for g in by.get(nm, ())} | {f}
    out = {}
    for f in files:
        seen, todo = set(), [f]
        while todo:
            g = todo.pop()
            if g not in seen:
                seen.add(g)
                todo += list(direct[g])
        out[f] = seen
    return out

def view_hash(files, own):
    """files: tree_files(root, {}) minus the other tests; OWN: the test's rel path (it is in files)."""
    return hashlib.sha256(cas.canon({"view": files, "own": own})).hexdigest()

GITENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@invalid",
          "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z", "GIT_CONFIG_GLOBAL": "/dev/null",
          "GIT_CONFIG_SYSTEM": "/dev/null"}

def git_view(view):
    env = dict(os.environ, **GITENV)
    for a in (["init", "-q", "-b", "main"], ["add", "-A", "-f"], ["commit", "-q", "-m", "view"]):
        subprocess.run(["git", "-C", view] + a, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def make_view(root, dst, exclude, skipdir=None):
    """Private copy of the checkout without .git, __pycache__ and the EXCLUDE files: exactly the files tree_files hashes."""
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in (".git", "__pycache__") and os.path.abspath(os.path.join(d, x)) != skipdir)
        os.makedirs(os.path.join(dst, os.path.relpath(d, root)), exist_ok=True)
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, root)
            if rel in exclude or n == ".git":
                continue
            q = os.path.join(dst, rel)
            if os.path.islink(p):
                os.symlink(os.readlink(p), q)
            else:
                shutil.copy2(p, q)

def key_of(rel, files, timeout):
    env_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")
    try:
        env = envcheck.env_id(json.load(open(env_json)))
    except (OSError, ValueError):
        env = "none"
    return hashlib.sha256(cas.canon({"format": CACHE_FORMAT, "test": rel, "view": view_hash(files, rel), "env": env,
                                     "python": sys.version, "timeout": timeout})).hexdigest()

def run_one(root, rel, exclude, timeout, skipdir=None):
    """(status, seconds, tail, ended): status passed|failed, TAIL the last bytes of output, ENDED False for a timeout."""
    t0 = time.time()
    # a test file is not a gate: it does not see GATE_OUT / GATE_COMMIT / GATE_CACHE (a test that runs gates of its own
    # would otherwise share this gate's result cache and outputs)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
    # the file's scratch and its view: directories of its own, removed when the file ends, however it ends
    tmp = tempfile.mkdtemp(prefix="selftest-")
    env["TMPDIR"] = tmp
    view = os.path.join(tmp, "view")
    out = os.path.join(tmp, "out.txt")
    try:
        make_view(root, view, exclude, skipdir)
        git_view(view)
        with open(out, "wb") as ofh:
            p = subprocess.Popen([sys.executable, "-I", rel], cwd=view, stdin=subprocess.DEVNULL, stdout=ofh,
                                 stderr=subprocess.STDOUT, start_new_session=True, env=env)
            try:
                ok, ended = p.wait(timeout=timeout) == 0, True
            except subprocess.TimeoutExpired:
                ok, ended = False, False
            try:
                os.killpg(p.pid, signal.SIGKILL)   # whatever the file left running in its process group
            except ProcessLookupError:
                pass
            p.wait()
        with open(out, "rb") as fh:
            data = fh.read()
        tail = data[-TAIL:].decode("utf-8", "replace")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ("passed" if ok else "failed"), round(time.time() - t0, 3), tail, ended

def parse(argv):
    o = {"timeout": 300.0, "jobs": 1, "cache": os.environ.get("GATE_CACHE") or None}
    a, dirs, i = list(argv), [], 0
    while i < len(a):
        if a[i] == "--timeout-s" and i + 1 < len(a):
            o["timeout"], i = float(a[i + 1]), i + 2
        elif a[i] == "--jobs" and i + 1 < len(a):
            o["jobs"], i = max(1, int(a[i + 1])), i + 2
        elif a[i] == "--cache" and i + 1 < len(a):
            o["cache"], i = a[i + 1], i + 2
        elif a[i] == "--":
            dirs += a[i + 1:]
            break
        else:
            dirs.append(a[i])
            i += 1
    return o, dirs

def load_hit(path, key):
    try:
        doc = json.load(open(path))
        if doc["format"] == CACHE_FORMAT and doc["key"] == key and doc["status"] in ("passed", "failed") \
                and isinstance(doc["tail"], str) and isinstance(doc["seconds"], (int, float)):
            return doc
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None

def store(path, key, status, seconds, tail):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w") as fh:
        json.dump({"format": CACHE_FORMAT, "key": key, "status": status, "seconds": seconds, "tail": tail}, fh, sort_keys=True)
    os.replace(tmp, path)

def universe(dirs):
    """the test files of the doc comment's universe (gate "oracle" judges the same list)"""
    files = []
    for d in dirs:
        files += sorted(os.path.normpath(f) for f in glob.glob(os.path.join(glob.escape(d), "tests", "test_*.py")))
    return files

def main(argv):
    o, dirs = parse(argv)
    files = universe(dirs)
    clo = test_closure(files)
    t0 = time.time()
    root = os.getcwd()
    keys, cdir = {}, None
    skip = os.path.abspath(o["cache"]) if o["cache"] else None   # the cache is not an input; it may lie inside the checkout
    if o["cache"] and files:
        cdir = os.path.join(o["cache"], "selftests")
        allf = tree_files(root, set(), skip)
        for f in files:
            keys[f] = key_of(f, [r for r in allf if r[0] not in files or r[0] in clo[f]], o["timeout"])

    def one(f):
        if cdir:
            hit = load_hit(os.path.join(cdir, keys[f] + ".json"), keys[f])
            if hit:
                return {"name": f, "seconds": 0.0, "status": hit["status"], "cached": True, "tail": hit["tail"]}
        st, secs, tail, ended = run_one(root, f, set(files) - clo[f], o["timeout"], skip)
        if cdir and ended:
            store(os.path.join(cdir, keys[f] + ".json"), keys[f], st, secs, tail)
        return {"name": f, "seconds": secs, "status": st, "cached": False, "tail": tail}

    with concurrent.futures.ThreadPoolExecutor(max_workers=o["jobs"]) as ex:
        full = list(ex.map(one, files))
    rows = [{k: r[k] for k in ("name", "seconds", "status", "cached")} for r in full]
    ran = sum(1 for r in rows if not r["cached"])
    secs = time.time() - t0
    unc = [r["name"] for r in rows if r["status"] == "failed"]
    n = len(files) - len(unc)
    status = "green" if not unc else "red"
    note = ", %d run, %d cached" % (ran, len(rows) - ran)
    detail = ("%d of %d test files pass%s, %.1f s" % (n, len(files), note, secs)) if files else "no test files at this commit (nothing to certify yet)"
    if unc:
        detail += "; first failure %s: %s" % (unc[0], next(r["tail"] for r in full if r["name"] == unc[0]).strip()[-300:])
    res = {"format": "fnvnx-gate/1", "name": "selftests", "status": status, "universe": len(files), "certified": n,
           "uncertified": unc[:50], "detail": detail, "files": rows, "recomputed": ran}
    out = os.environ["GATE_OUT"]
    with open(os.path.join(out, "selftests.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    with open(os.path.join(out, "selftests.times.json"), "w") as fh:
        json.dump(rows, fh)
    return 0 if status == "green" else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
