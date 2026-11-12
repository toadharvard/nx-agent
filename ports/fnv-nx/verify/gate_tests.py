#!/usr/bin/env python3
"""Gate "selftests" (fnvnx-gate/1): every test file of the given directories passes.
    python3 -I gate_tests.py [--timeout-s N] [--jobs N] [--cache DIR]
Run in the checkout root with env GATE_OUT; writes GATE_OUT/selftests.json (+ GATE_OUT/selftests.times.json).
Universe: DERIVED from verify/admit.json, no list in argv: DIR/tests/test_*.py for every tree DIR (gate_admit.trees) of every admitted root whose
gates include "selftests", in admit order, each DIR's files sorted by name; items are repo-relative paths. A root that declares selftests and has no
test file under any of its trees is an uncertified item (named `ROOT (declares selftests, no tests/test_*.py)`): the gate is red. A missing or
malformed admit.json is red. Each file runs as `python3 -I FILE` in the checkout with a per-file timeout
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
  test itself plus the test files its record in DIR/tests/oracle.json declares as "uses" (repo-relative paths of test files). A file the
  test needs and did not declare is missing from its view, the test fails, and that is red: declarations are enforced, never
  guessed. The record's "net": true says the test fetches content-pinned externals (commit + tree hash) and may use the network.
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
A test that needs game data declares it in DIR/tests/inputs.json ({"format": "fnvnx-selftest-inputs/1", "tests": {"test_x.py": ["exe"]}}); the
  names are externals of verify/contract_inputs.json (commit + tree_sha256, fetched like a recipe's external). The test gets FNVNX_INPUT_<NAME> =
  the checked-out directory (a host path, readable in the run's namespaces). The pins are files of the view (inputs.json and contract_inputs.json),
  so they are in the key. If the input cannot be fetched or its tree hash differs, the file's status is `blocked-env`: uncertified (the gate is
  red), never skipped and never cached.
TMPDIR rule: a test that leaves entries in its scratch (its TMPDIR) at exit is uncertified ("leaked in TMPDIR"); the scratch is removed in every case.
Result extra keys: "reasons": {test: why} for the uncertified files; "files": [{"name","seconds","status","cached"[,"reason"]}] (status passed|failed|blocked-env; cached true when not run; the same list in
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

CACHE_FORMAT = "fnvnx-selftests-cache/3"
TAIL = 2000

PINS = "ports/fnv-nx/verify/contract_inputs.json"   # the pins of the declared inputs (the one registry of content-pinned fnv-nx-build inputs)

def declared_inputs(path):
    """[input names] of a test from DIR/tests/inputs.json ("fnvnx-selftest-inputs/1": {"tests": {"test_x.py": ["exe"]}}); [] if the file is absent; None if invalid."""
    try:
        doc = json.load(open(os.path.join(os.path.dirname(path), "inputs.json")))
        return {n: list(v) for n, v in doc["tests"].items()}.get(os.path.basename(path), []) if doc["format"] == "fnvnx-selftest-inputs/1" else None
    except OSError:
        return []
    except (ValueError, KeyError, TypeError, AttributeError):
        return None

def fetch_inputs(names, cache, ws):
    """{FNVNX_INPUT_<NAME>: host dir} of the pinned externals NAMES (recipe.fetch_externals, commit + tree_sha256 checked), or a string: why not."""
    import recipe
    try:
        pins = {e["name"]: e for e in json.load(open(PINS))["external"]}
        ext = [pins[n] for n in names]
        got = recipe.fetch_externals(ext, os.path.join(cache or ws, "store"), os.path.join(cache or ws, "external"), ws)
    except recipe.BuildRed as x:
        return "%s: %s" % (x.args[0].get("stage"), "; ".join(x.args[0].get("stderr", [])[-3:]))
    except (OSError, ValueError, KeyError) as x:
        return "inputs %s: %s" % (",".join(names), x)
    return {"FNVNX_INPUT_" + n.upper(): got[n]["host"] for n in names}

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

def declared_reads(files):
    """{test file: (set of files, set of dirs) of its declared reads} from DIR/tests/reads.json ("fnvnx-selftest-reads/1": {"tests": {"test_x.py":
    ["path", "dir/", ...]}}: a path without a trailing slash is a file of the view, with one an (at least empty) directory); a test without a record is
    absent from the result and gets the whole checkout view. The record is written by tracereads.py and ENFORCED by the view: a file the test
    reads and the record does not list is not there."""
    out, docs = {}, {}
    for f in files:
        d = os.path.dirname(f)
        if d not in docs:
            try:
                doc = json.load(open(os.path.join(d, "reads.json")))
                docs[d] = doc["tests"] if doc["format"] == "fnvnx-selftest-reads/1" else {}
            except (OSError, ValueError, KeyError, TypeError):
                docs[d] = {}
        rec = docs[d].get(os.path.basename(f))
        if isinstance(rec, list):
            rec = [os.path.normpath(x) for x in rec if isinstance(x, str)]
            out[f] = ({x for x in rec if x not in ("", ".")}, {os.path.normpath(x) for x in map(str, docs[d][os.path.basename(f)]) if x.endswith("/")})
    return out

def declared(files):
    """{test file: (set of test files in its view, itself included, net flag)} from DIR/tests/oracle.json "uses" / "net".
    A record without them means: no other test file, no network."""
    out, docs = {}, {}
    for f in files:
        d = os.path.dirname(f)
        if d not in docs:
            try:
                docs[d] = json.load(open(os.path.join(d, "oracle.json")))["tests"]
            except (OSError, ValueError, KeyError, TypeError):
                docs[d] = {}
        rec = docs[d].get(os.path.basename(f))
        rec = rec if isinstance(rec, dict) else {}
        uses = rec.get("uses", [])
        uses = [u for u in uses if isinstance(u, str)] if isinstance(uses, list) else []
        out[f] = ({f} | {os.path.normpath(u) for u in uses}, rec.get("net") is True)
    return out

def view_hash(files, own, dirs=()):
    """files: tree_files(root, {}) minus the other tests (or only the declared reads); OWN: the test's rel path (it is in files); DIRS: the declared directories."""
    return hashlib.sha256(cas.canon({"view": files, "own": own, "dirs": sorted(dirs)})).hexdigest()

GITENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@invalid",
          "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z", "GIT_CONFIG_GLOBAL": "/dev/null",
          "GIT_CONFIG_SYSTEM": "/dev/null"}

def git_view(view):
    env = dict(os.environ, **GITENV)
    for a in (["init", "-q", "-b", "main"], ["add", "-A", "-f"], ["commit", "-q", "-m", "view"]):
        subprocess.run(["git", "-C", view] + a, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def make_view(root, dst, exclude, skipdir=None, keep=None):
    """Private copy of the checkout without .git, __pycache__ and the EXCLUDE files: exactly the files tree_files hashes. KEEP = (files, dirs):
    only those files, plus the (empty at least) directories."""
    if keep is not None:
        for rel in sorted(keep[0]):
            p = os.path.join(root, rel)
            if os.path.isfile(p) or os.path.islink(p):
                os.makedirs(os.path.join(dst, os.path.dirname(rel)), exist_ok=True)
                if os.path.islink(p):
                    os.symlink(os.readlink(p), os.path.join(dst, rel))
                else:
                    shutil.copy2(p, os.path.join(dst, rel))
        for rel in sorted(keep[1]):
            os.makedirs(os.path.join(dst, rel), exist_ok=True)
        return
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

def key_of(rel, files, timeout, net, dirs=()):
    env_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")
    try:
        env = envcheck.env_id(json.load(open(env_json)))
    except (OSError, ValueError):
        env = "none"
    return hashlib.sha256(cas.canon({"format": CACHE_FORMAT, "test": rel, "view": view_hash(files, rel, dirs), "env": env,
                                     "python": sys.version, "timeout": timeout, "net": net, "run": RUN_ENV})).hexdigest()

# The fixed machine of a test run (part of every key): the test sees the view at VIEWDIR (fixed cwd) and a scratch at SCRATCH, in new
# mount/pid/uts/ipc namespaces and, unless its record says "net", a new network namespace with only loopback. Environment: exactly this.
ROOTDIR = "/nxroot"   # a tmpfs of the run: the parent of the view and of the scratch, so a test that writes to ".." writes there
VIEWDIR, SCRATCH = ROOTDIR + "/view", ROOTDIR + "/tmp"
RUN_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TZ": "UTC", "LC_ALL": "C", "LANG": "C",
           "PYTHONHASHSEED": "0", "HOME": SCRATCH, "TMPDIR": SCRATCH,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@invalid"}
NET_PASS = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "GIT_SSL_CAINFO", "CURL_CA_BUNDLE",
            "ARTIFACTS_URL")   # the channel of a net test, which fetches by exact commit and checks the tree hash

# runs inside the new namespaces, as the test's parent: fixed mounts, loopback up (net namespace), fixed cwd, then the test
LAUNCH = r"""
import ctypes, fcntl, os, socket, struct, sys
view, scratch, rel, net = sys.argv[1:5]
trace = sys.argv[5] if len(sys.argv) > 5 else ""
libc = ctypes.CDLL(None, use_errno=True)
os.makedirs("%s", exist_ok=True)
if not os.path.ismount("%s") and libc.mount(b"tmpfs", b"%s", b"tmpfs", ctypes.c_ulong(0), None):   # nested in a run: its tmpfs is there
    sys.exit("mount tmpfs: %%s" %% os.strerror(ctypes.get_errno()))
for src, dst in ((view, "%s"), (scratch, "%s")):
    os.makedirs(dst, exist_ok=True)
    if libc.mount(src.encode(), dst.encode(), None, ctypes.c_ulong(4096 | 16384), None):
        sys.exit("mount %%s: %%s" %% (dst, os.strerror(ctypes.get_errno())))
if net == "0":
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fcntl.ioctl(s, 0x8914, struct.pack("16sH14x", b"lo", 0) )  # placeholder replaced below
os.chdir("%s")
if trace:   # tracereads.py: the same run under strace
    os.execv("/usr/bin/strace", ["strace", "-f", "-y", "-qq", "-s", "400", "-o", trace, "-e", "trace=openat,open,stat,lstat,newfstatat,statx,access,faccessat,faccessat2,readlink,readlinkat,execve,chdir", sys.executable, "-I", rel])
os.execv(sys.executable, [sys.executable, "-I", rel])
""" % (ROOTDIR, ROOTDIR, ROOTDIR, VIEWDIR, SCRATCH, VIEWDIR)
LAUNCH = LAUNCH.replace('fcntl.ioctl(s, 0x8914, struct.pack("16sH14x", b"lo", 0) )  # placeholder replaced below',
                        'fl = struct.unpack("16sH14x", fcntl.ioctl(s, 0x8913, struct.pack("16sH14x", b"lo", 0)))[1]\n'
                        '    fcntl.ioctl(s, 0x8914, struct.pack("16sH14x", b"lo", fl | 1))   # SIOCGIFFLAGS, SIOCSIFFLAGS: IFF_UP')

def record_digest(data):
    """sha256 of a test's RECORD: its PASS/FAIL case lines (the tests' protocol: one line per case, "PASS name" or "FAIL name  detail";
    the detail after two blanks is diagnostics and is left out). The rest of the output (timings, scratch names, entropy
    readings the tests print as information) is not the test's record."""
    lines = []
    for ln in data.decode("utf-8", "replace").splitlines():
        if ln.startswith(("PASS ", "FAIL ")):
            lines.append(ln.split("  ")[0] if ln.startswith("FAIL ") else ln)
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()   # a multiset: parallel cases print in any order

def run_one(root, rel, exclude, timeout, skipdir=None, net=False, extra=None, keep=None, trace=None):
    """(status, seconds, tail, ended, digest): status passed|failed, TAIL the last bytes of output, DIGEST sha256 of its record (record_digest), ENDED
    False for a timeout."""
    t0 = time.time()
    # a test file is not a gate: it sees none of the gate's environment (GATE_OUT / GATE_COMMIT / GATE_CACHE included)
    env = dict(RUN_ENV)
    if net:
        env.update({k: os.environ[k] for k in NET_PASS if k in os.environ})
    env.update(extra or {})
    # the file's scratch and its view: directories of its own, removed when the file ends, however it ends
    tmp = tempfile.mkdtemp(prefix="selftest-")
    view, scratch, out = os.path.join(tmp, "view"), os.path.join(tmp, "scratch"), os.path.join(tmp, "out.txt")
    os.mkdir(scratch)
    cmd = ["unshare", "-m", "-p", "-u", "-i", "-f", "--kill-child"] + ([] if net else ["-n"]) + [
        "--", sys.executable, "-I", "-c", LAUNCH, view, scratch, rel, "1" if net else "0"] + ([os.path.join(tmp, "trace.log")] if trace else [])
    try:
        make_view(root, view, exclude, skipdir, keep)
        git_view(view)
        with open(out, "wb") as ofh:
            p = subprocess.Popen(cmd, cwd=view, stdin=subprocess.DEVNULL, stdout=ofh, stderr=subprocess.STDOUT,
                                 start_new_session=True, env=env)
            try:
                rc = p.wait(timeout=timeout)
                ok, ended, why = rc == 0, True, ("exit %d" % rc if rc else "")
            except subprocess.TimeoutExpired:
                ok, ended, why = False, False, "timeout %gs" % timeout
            try:
                os.killpg(p.pid, signal.SIGKILL)   # whatever the file left running in its process group
            except ProcessLookupError:
                pass
            p.wait()
        with open(out, "rb") as fh:
            data = fh.read()
        if why:   # the reason of a failed file: exit status or timeout, then the output tail
            data += ("\n" + why + "\n").encode()
        left = sorted(os.listdir(scratch))
        if left:   # TMPDIR rule: a test cleans up after itself
            ok = False
            data += ("\nFAIL leaked in TMPDIR  %s\n" % ", ".join(left[:8])).encode()
        text = data.decode("utf-8", "replace")
        fails = "\n".join(ln[:300] for ln in text.splitlines() if ln.startswith("FAIL "))[:TAIL // 2]
        tail = (fails + "\n--- end of output ---\n" if fails else "") + text[-(TAIL - len(fails)) :] if fails else text[-TAIL:]
    finally:
        if trace:
            shutil.copy(os.path.join(tmp, "trace.log"), trace)
        shutil.rmtree(tmp, ignore_errors=True)
    return ("passed" if ok else "failed"), round(time.time() - t0, 3), tail, ended, record_digest(data)

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

ADMIT = "ports/fnv-nx/verify/admit.json"

def admit_universe(path=ADMIT):
    """(files, empty roots, error): the test files of the admitted selftests roots (the gate's universe, one definition for gates selftests and oracle)."""
    import gate_admit
    try:
        roots = json.load(open(path))["roots"]
        sel = [r for r in roots if isinstance(r, dict) and "selftests" in (r.get("gates") or [])]
        files, empty = [], []
        for r in sel:
            mine = []
            for t in gate_admit.trees(r):
                mine += [f for f in universe([t]) if f not in files and f not in mine]
            files += mine
            if not mine:
                empty.append(r["path"])
        return files, empty, None
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [], [], "%s: %s" % (path, e)

def universe(dirs):
    """the test files of the doc comment's universe (gate "oracle" judges the same list)"""
    files = []
    for d in dirs:
        files += sorted(os.path.normpath(f) for f in glob.glob(os.path.join(glob.escape(d), "tests", "test_*.py")))
    return files

def main(argv):
    keep_of = lambda f: ({f} | dec[f][0] | rds[f][0], rds[f][1])   # noqa: E731  the view of a test with declared reads: those, its own file and its `uses`
    o, dirs = parse(argv)
    if dirs:
        print("gate_tests.py takes no directory: the universe is the admitted selftests roots (verify/admit.json)", file=sys.stderr)
        return 1
    files, empty, err = admit_universe()
    dec = declared(files)
    rds = declared_reads(files)
    t0 = time.time()
    root = os.getcwd()
    keys, cdir = {}, None
    skip = os.path.abspath(o["cache"]) if o["cache"] else None   # the cache is not an input; it may lie inside the checkout
    if o["cache"] and files:
        cdir = os.path.join(o["cache"], "selftests")
        allf = tree_files(root, set(), skip)
        for f in files:
            keys[f] = key_of(f, [r for r in allf if (r[0] in keep_of(f)[0] if f in rds else r[0] not in files or r[0] in dec[f][0])],
                             o["timeout"], dec[f][1], keep_of(f)[1] if f in rds else ())

    base = tempfile.mkdtemp(prefix="selftests-")   # in the gate's own TMPDIR; removed below whatever happens
    ws = os.path.join(base, "inputs")
    os.mkdir(ws)

    def one(f):
        if cdir:
            hit = load_hit(os.path.join(cdir, keys[f] + ".json"), keys[f])
            if hit:
                return {"name": f, "seconds": 0.0, "status": hit["status"], "cached": True, "tail": hit["tail"], "det": False}
        extra = None
        names = declared_inputs(f)
        if names is None:
            return {"name": f, "seconds": 0.0, "status": "failed", "cached": False, "tail": "tests/inputs.json is not a valid fnvnx-selftest-inputs/1 file", "det": False}
        if names:
            got = fetch_inputs(names, o["cache"], ws)   # no data here: blocked, never green, never skipped, never cached
            if isinstance(got, str):
                return {"name": f, "seconds": 0.0, "status": "blocked-env", "cached": False, "tail": got, "det": False}
            extra = got
        # testdet: a cold test is executed twice, at once, in fresh views; a difference in end status or record digest (PASS/FAIL case lines) is red
        args = (root, f, set(files) - dec[f][0], o["timeout"], skip, dec[f][1], extra, keep_of(f) if f in rds else None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pair:
            r1, r2 = list(pair.map(lambda _: run_one(*args), (0, 1)))
        (st, secs, tail, ended, dg), (st2, _, tail2, ended2, dg2) = r1, r2
        if ended and ended2 and (st, dg) != (st2, dg2):
            diff = "run 1: %s %s; run 2: %s %s\n--- run 1 ---\n%s\n--- run 2 ---\n%s" % (st, dg[:12], st2, dg2[:12], tail[-400:], tail2[-400:])
            return {"name": f, "seconds": secs, "status": "failed", "cached": False, "tail": "nondeterministic (testdet): " + diff, "det": True}
        if cdir and ended and ended2:
            store(os.path.join(cdir, keys[f] + ".json"), keys[f], st, secs, tail)
        return {"name": f, "seconds": secs, "status": st if ended and ended2 else "failed", "cached": False, "tail": tail, "det": False}

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=o["jobs"]) as ex:
            full = list(ex.map(one, files))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    rows = [dict({k: r[k] for k in ("name", "seconds", "status", "cached")}, **({} if r["status"] == "passed" else {"reason": r["tail"]})) for r in full]
    ran = sum(1 for r in rows if not r["cached"])
    nondet = [r["name"] for r in full if r["det"]]
    secs = time.time() - t0
    unc = [r["name"] for r in rows if r["status"] in ("failed", "blocked-env")]
    noroot = ["%s (declares selftests, no tests/test_*.py)" % r for r in empty] + (["admit.json: " + err] if err else [])
    n = len(files) - len(unc)
    status = "green" if not unc and not noroot else "red"
    unc = unc + noroot
    note = ", %d run, %d cached" % (ran, len(rows) - ran)
    detail = ("%d of %d test files pass%s, %.1f s" % (n, len(files), note, secs)) if files else "no test files at this commit (nothing to certify yet)"
    if noroot:
        detail += "; red: " + "; ".join(noroot)
    blocked = [r["name"] for r in rows if r["status"] == "blocked-env"]
    if blocked:
        detail += "; blocked: env (declared input not available) for " + ", ".join(blocked)
    if nondet:
        detail += "; nondeterministic: " + ",".join(nondet[:5])
    if unc:
        detail += "; first failure %s: %s" % (unc[0], next((r["tail"] for r in full if r["name"] == unc[0]), "").strip()[-300:])
    res = {"format": "fnvnx-gate/1", "name": "selftests", "status": status, "universe": len(files) + len(noroot), "certified": n,
           "uncertified": unc[:50], "detail": detail, "files": rows, "recomputed": ran,
           "reasons": {r["name"]: r["reason"][-1500:] for r in rows if r["name"] in unc[:50] and r.get("reason")},
           "testdet": {"executed_twice": ran, "nondeterministic": nondet}}
    out = os.environ["GATE_OUT"]
    with open(os.path.join(out, "selftests.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    with open(os.path.join(out, "selftests.times.json"), "w") as fh:
        json.dump(rows, fh)
    return 0 if status == "green" else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
