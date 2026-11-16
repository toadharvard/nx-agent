#!/usr/bin/env python3
"""Gate "selftests" (fnvnx-gate/1): every test file of the given directories passes.
    python3 -I gate_tests.py [--timeout-s N] [--jobs N] [--cache DIR] [--key-dirs D...] [--] [DIR...]
Run in the checkout root with env GATE_OUT; writes GATE_OUT/selftests.json (+ GATE_OUT/selftests.times.json).
Universe, by one rule over the tree: tests/test_*.py of every root of verify/admit.json (in root order, each root's files sorted by name; a new test file under an
admitted root is inside with no edit of any gate file); items are repo-relative paths. DIRs on the command line replace the admitted roots (a fixture's scan directories, never the
product gate: verify/gates.d/selftests.json names none). Each file runs as `python3 -I FILE` in the checkout with a per-file timeout
(default 300 s; a timeout kills the process group and is a failure). Certified: exit status 0. No skip logic: a test that
cannot run in this environment (root, namespaces, strace) is uncertified. Zero files: green, universe 0. Exit 0 iff green.
--jobs N (default 1): N test files run at once, each in its own process; results are reported in sorted universe order.
  A test must therefore keep its scratch under its own tempfile.mkdtemp directory (no fixed path). Each file runs with
  TMPDIR set to a fresh directory of its own, and the gate removes that directory (and kills what is left of the file's
  process group) when the file ends, passed, failed or timed out: no test leaves anything in the temporary directory. No test needs a serial
  group: sandbox namespaces are per process and every store/work directory is per run.
--cache DIR (default $GATE_CACHE, see verdict.py; none = no caching): ONE ENTRY PER TEST FILE, keyed by the content of what that file reads. A file passes
  once under `strace -f` (the call set of opentrace.py: opens, execs, stat family, access, readlink, getdents) and its READ SET is recorded: every path under the
  checkout that the run (and every process it started) opened, executed, stat-ed or listed, with the state at the end of the run (sha256 of the content; the
  target of a link; `absent`; for a listed directory the names in it, without __pycache__). KEY (the entry's directory under DIR/selftests/) = sha256 over: the
  file's path, the envcheck id of the env.json next to this script, this gate's argv without --cache/--jobs, the python version string, and the pin (commit,
  tree_sha256) of each declared input. A run is certified WITHOUT running iff an entry under its KEY has every recorded path in the same state now. Argument: the
  run is a deterministic function of its argv and environment (the KEY), the files it reads and what it sees of the directories; the recorded set is exactly
  what a pass read, and a run that follows the same reads through unchanged content reads the same set and gives the same result. A change of one file
  re-runs exactly the files whose read set holds it. Reads outside the checkout (the interpreter, system libraries, tools) are the declared environment
  (the env id); a file the run reads in its own TMPDIR is its own. A run that touched the checkout's git metadata (git ls-files, git show: the index and the
  objects are not files of the tree) is keyed by the tree hash of the commit as well (HEAD^{tree}): any change of the tree re-runs it. A failed file, a timeout
  and a leak are never recorded. No file is recorded without a trace (strace missing or a trace that cannot be read: the file is failed, never skipped).
  UNTRACED: a test that itself runs strace cannot run under it (one tracer per process). A file whose import closure over the tree reaches a module that runs strace (TRACERS) is such a file; it runs
  untraced and runs on its MOUNT only: a directory holding its import closure (the modules of the tree it imports, the scripts it names) and the files under the
  data roots it declares in `# reads: <path> <path>...` lines of its own text (repo-relative files or directories; a test runs the recipe and reads the toolchain,
  the pins, the real sources its fixtures copy). A read outside the mount fails (the file is not there), so a missing declaration is a red test, never a silent
  wide key; the entry is keyed by the content of the mount (path and sha256 of every file), so a change outside the mount does not run the file again.
  CUT: a test file whose import closure names a root (a recipe directory, or tests/<name> of class test) that verify/admit.json does not admit belongs to the cut
  of the branch: it is outside the universe (gate oracle judges the same list) and the detail lists `cut:test:<file> (root <r>)`. Detail of a green run: `N of M test files pass, K cached`.
A test that needs game data declares it in DIR/tests/inputs.json ({"format": "fnvnx-selftest-inputs/1", "tests": {"test_x.py": ["exe", {"root": R, "output": NAME}]}}); a name is an
external of verify/contract_inputs.json (commit + tree_sha256, fetched like a recipe's external). The test gets FNVNX_INPUT_<NAME> = the checked-out directory. If the input cannot be
fetched or its tree hash differs, the file's status is `blocked-env`: uncertified (the gate is red), never skipped.
A PRODUCED input {"root": R, "output": NAME} is the declared output NAME of admitted root R (the `outputs` of its recipe, the `uses` form of recipe.py): the gate builds R through the
recipe in GATE_CACHE (an unchanged root is a cache hit) and the test gets FNVNX_INPUT_<NAME> = the published output, whose hash (the output's tree hash) is the input's pin in the key.
Red (status `failed`, with the cause): R is not admitted; R does not declare NAME; R's build is red (failing stage and diagnostics); two inputs of one NAME.
Result extra key "files": [{"name","seconds","status"[,"reason"]}] (status passed|failed|cached; reason of a failed file: exit status or
timeout, names left in its TMPDIR, the exception/errno text and the tail of its output); same list in selftests.times.json.
TMPDIR rule: each test file runs with TMPDIR = a fresh directory of its own under the gate's temporary directory. The gate removes it
(and the log) after the test whatever the outcome, including a timeout kill, so no test can leave scratch behind; a test that
leaves entries in its TMPDIR at exit is uncertified ("leaked in TMPDIR"), so a test cleans up after itself. Scratch a test makes
outside TMPDIR (a fixed path) is not covered: tests use tempfile."""
import concurrent.futures
import contextlib
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import envcheck  # noqa: E402
import opentrace  # noqa: E402

CACHE_FORMAT = "fnvnx-selftests-cache/2"   # one entry per test file: its key, its read set (or the whole-directory trees of an untraced file)
KEEP = 8   # entries kept per key (the states of the read set seen on recent commits)

PINS = "ports/fnv-nx/verify/contract_inputs.json"   # the pins of the declared inputs (the one registry of content-pinned fnv-nx-build inputs)

def input_name(e):
    """The NAME of a declared input entry: a string (an external of contract_inputs.json) or {"root": R, "output": NAME} (a produced input: the declared output NAME of admitted root R)."""
    return e if isinstance(e, str) else e["output"]

def valid_entry(e):
    return (isinstance(e, str) and bool(e)) or (isinstance(e, dict) and set(e) == {"root", "output"} and all(isinstance(e[k], str) and e[k] for k in e))

def declared_inputs(path):
    """[input entries] of a test file from DIR/tests/inputs.json ("fnvnx-selftest-inputs/1": {"tests": {"test_x.py": ["exe", {"root": R, "output": NAME}]}}); [] if the file is absent
    or names none; None if the file is not valid (a bad entry, or two inputs of one NAME: they would share FNVNX_INPUT_<NAME>)."""
    try:
        doc = json.load(open(os.path.join(os.path.dirname(path), "inputs.json")))
        if doc["format"] != "fnvnx-selftest-inputs/1":
            return None
        ent = list(doc["tests"].get(os.path.basename(path), []))
        if not all(valid_entry(e) for e in ent) or len({input_name(e) for e in ent}) != len(ent):
            return None
        return ent
    except OSError:
        return []
    except (ValueError, KeyError, TypeError, AttributeError):
        return None

def produced_input(entry, cache, repo="."):
    """(host path, output sha256) of the produced input {"root": R, "output": NAME}, or a string: why not. R is built through the recipe in the GATE_CACHE store (gate_build.ensure: a
    green build is published once, an unchanged root is a cache hit) and its declared output NAME is taken from the published build. Red: R is not admitted, R does not declare NAME, R's
    build is red (its failing stage and diagnostics)."""
    import gate_build
    import recipe
    r, n = entry["root"], entry["output"]
    adm = admitted_roots()
    if adm is None or r not in adm:
        return "produced input %s of root %s: the root is not admitted (verify/admit.json)" % (n, r)
    try:
        roots = recipe.load_admit(repo)
        outs = recipe.root_outputs(roots, r, repo)[0]   # the one accessor of what a `uses` entry names: a recipe's `outputs`, or the implicit INCLUDE of a header root
    except (KeyError, OSError, ValueError, AttributeError, TypeError) as x:
        return "produced input %s of root %s: its recipe cannot be read: %s: %s" % (n, r, type(x).__name__, x)
    if n not in outs:
        return "produced input %s: root %s does not declare the output" % (n, r)
    if roots[r].get("class") == "header":   # no recipe and no stage: the output is the root's own directory, keyed by its tree hash
        d = os.path.abspath(os.path.join(repo, r))
        return d, cas.tree_hash(d, skip=("__pycache__",))
    try:
        res, _out = gate_build.ensure(r, cache, repo)
    except recipe.EnvBlocked as x:
        return "produced input %s of root %s: this machine is not the declared environment: %s" % (n, r, "; ".join(x.errors)[:300])
    except recipe.RecipeError as x:
        return "produced input %s of root %s: invalid recipe: %s" % (n, r, "; ".join(x.errors)[:300])
    if res["status"] != "green":
        f = res.get("failure") or {}
        return "produced input %s: root %s build is red: stage %s exit %s: %s" % (n, r, f.get("stage"), f.get("exit"), " | ".join(f.get("stderr", [])[-3:])[:300])
    try:
        return gate_build.published_output(cache, r, n, repo)
    except LookupError as x:
        return "produced input %s of root %s: %s" % (n, r, x)

TRACERS = ("envcheck", "opentrace", "oraclecheck")   # the modules of the tree that run strace (grep "strace" over the tree's non-test modules: the one source of the rule below)
_MODS = {}

def module_index(root):
    """{module name: [paths]} of every .py of the checkout's ports/fnv-nx (the tests' helper modules included) (a name in two directories names both: a superset is sound)."""
    if root not in _MODS:
        idx = {}
        for d, ds, fs in os.walk(os.path.join(root, "ports", "fnv-nx")):
            ds[:] = [x for x in ds if x not in ("__pycache__", ".git")]
            for f in fs:
                if f.endswith(".py"):
                    idx.setdefault(f[:-3], []).append(os.path.join(d, f))
        _MODS[root] = idx
    return _MODS[root]

def imports_of(path, scripts=False):
    """The module names a file refers to: every `import a, b.c` / `from a.b import c, d` statement line of its TEXT (a child script embedded in a string counts, as it runs), and
    for SCRIPTS (the test file itself) every `name.py` it names (a script run by subprocess or runpy)."""
    try:
        text = open(path).read()
    except OSError:
        return set()
    out = set(re.findall(r"([A-Za-z0-9_]+)\.py\b", text)) if scripts else set()
    for m in re.finditer(r"^[ \t;]*(?:import|from)[ \t]+([A-Za-z0-9_., ]+)", text, re.M):
        line = m.group(1)
        out |= set(re.findall(r"[A-Za-z0-9_]+", line.split(" import ")[0] if " import " in line else line))
        if " import " in line:
            out |= set(re.findall(r"[A-Za-z0-9_]+", line.split(" import ", 1)[1]))
    return out

def closure_of(path):
    """(module names, file paths) of the import closure of the test file PATH over the checkout's modules (imports_of: the file's own `name.py` references count as imports)."""
    root = os.path.realpath(os.getcwd())
    idx = module_index(root)
    seen, files, todo = set(), [path], sorted(imports_of(path, True))
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        for p in idx.get(m, []):
            files.append(p)
            todo += sorted(imports_of(p) - seen)
    return seen, files

def untraced_of(path):
    """True iff the test file can run strace itself (so it cannot run under it: one tracer per process): by the rule, its import closure over the checkout's modules
    reaches a module of TRACERS. Over-approximate and so sound (an untraced file is keyed by the whole directories, coarser, never wrong). No list."""
    return bool(closure_of(path)[0] & set(TRACERS))

ROOT_LIT = re.compile(r"ports/fnv-nx/[A-Za-z0-9_./-]*[A-Za-z0-9_]")
TEST_ROOT = re.compile(r"ports/fnv-nx/tests/[A-Za-z0-9_]+")   # the class test roots live at tests/<name> (admit.json: class test)

def is_recipe(path):
    try:
        return json.load(open(path)).get("format") == "fnvnx-recipe/1"
    except (OSError, ValueError, AttributeError):
        return False

def roots_of(path):
    """The roots a test file needs, from the file and its import closure (closure_of): a literal path that names a directory holding a recipe (build.json of format fnvnx-recipe/1: one
    rule over the tree), or a root of class test (ports/fnv-nx/tests/<name>, the place of those roots, whether or not the directory is in this tree). Over-approximate and so sound
    for the cut below; no list."""
    root = os.path.realpath(os.getcwd())
    out = set()
    for f in closure_of(path)[1]:
        try:
            text = open(f).read()
        except OSError:
            continue
        out |= set(TEST_ROOT.findall(text))
        for m in ROOT_LIT.findall(text):
            for q in (m, os.path.dirname(m)):   # `<root>/build.json`, `<root>/inc`: the root is the nearest ancestor with a recipe
                if is_recipe(os.path.join(root, q, "build.json")):
                    out.add(q)
                    break
    return sorted(out)

def cut_of(path, admitted):
    """The roots of a test file that are not admitted (verify/admit.json): when there is one, the test belongs to the CUT of this branch (its root is not part of it), reported as
    `cut:test:<file> (root <r>)` and never run: a test is certified against the roots it builds, and a root that is not admitted is not built."""
    return [r for r in roots_of(path) if r not in admitted]

READS = re.compile(r"^#[ \t]*reads:[ \t]*(.*)$", re.M)

def reads_of(path):
    """The data roots a test file declares (`# reads: <path> <path>...` lines of its text): repo-relative files or directories its run reads besides its import closure."""
    try:
        text = open(path).read()
    except OSError:
        return []
    return sorted({x for m in READS.findall(text) for x in m.replace(",", " ").split()})

def mount_files(path):
    """{repo-relative path: absolute path} of the MOUNT of an untraced test file: its import closure (closure_of) and every file under its declared `# reads:` roots (those of the test file and of every module of its closure, one rule: a file a module reads is part of its closure as an import is), without
    __pycache__ and .git. An untraced test runs on this and nothing else of the checkout (mount_run): a read outside it fails, so a missing declaration is a red test."""
    root = os.path.realpath(os.getcwd())
    out = {}
    for f in closure_of(path)[1]:
        try:
            text = open(f).read()
        except OSError:
            continue
        out |= set(TEST_ROOT.findall(text))
        for m in ROOT_LIT.findall(text):
            for q in (m, os.path.dirname(m)):   # `<root>/build.json`, `<root>/inc`: the root is the nearest ancestor with a recipe
                if is_recipe(os.path.join(root, q, "build.json")):
                    out.add(q)
                    break
    return sorted(out)

def cut_of(path, admitted):
    """The roots of a test file that are not admitted (verify/admit.json): when there is one, the test belongs to the CUT of this branch (its root is not part of it), reported as
    `cut:test:<file> (root <r>)` and never run: a test is certified against the roots it builds, and a root that is not admitted is not built."""
    return [r for r in roots_of(path) if r not in admitted]

READS = re.compile(r"^#[ \t]*reads:[ \t]*(.*)$", re.M)

def reads_of(path):
    """The data roots a test file declares (`# reads: <path> <path>...` lines of its text): repo-relative files or directories its run reads besides its import closure."""
    try:
        text = open(path).read()
    except OSError:
        return []
    return sorted({x for m in READS.findall(text) for x in m.replace(",", " ").split()})

def mount_files(path):
    """{repo-relative path: absolute path} of the MOUNT of an untraced test file: its import closure (closure_of), the declared environment verify/env.json (an input of every key) and every file under its declared `# reads:` roots, without
    __pycache__ and .git. An untraced test runs on this and nothing else of the checkout (mount_run): a read outside it fails, so a missing declaration is a red test."""
    root = os.path.realpath(os.getcwd())
    out = {}
    env_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")   # the declared environment is an input of every key (the envcheck id), so it is in every mount: a module of the closure reads it at import
    if os.path.realpath(env_json).startswith(root + os.sep) and os.path.isfile(env_json):
        out[os.path.relpath(os.path.realpath(env_json), root)] = os.path.realpath(env_json)
    for f in closure_of(path)[1]:
        a = os.path.realpath(f)
        if a.startswith(root + os.sep):
            out[a[len(root) + 1:]] = a
    mods = [f for f in closure_of(path)[1]]
    for r in sorted({x for p in [path] + mods for x in reads_of(p)}):
        base = os.path.join(root, r)
        if os.path.isfile(base):
            out[r] = base
        for d, ds, fs in os.walk(base):
            ds[:] = sorted(x for x in ds if x not in ("__pycache__", ".git"))
            for n in sorted(fs):
                a = os.path.join(d, n)
                out[os.path.relpath(a, root)] = a
    return out

def mount_digest(files, st):
    """sha256 over (path, content hash) of a mount: the key of an untraced file's result."""
    return hashlib.sha256(cas.canon(sorted((rel, st.of(rel)) for rel in files))).hexdigest()

def mount_make(files, dest):
    """The mount directory DEST: each file hard-linked (copied across devices) at its repo-relative path."""
    for rel, a in files.items():
        t = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(t), exist_ok=True)
        try:
            os.link(a, t)
        except OSError:
            shutil.copy2(a, t)

def fetch_inputs(names, cache, ws):
    """{FNVNX_INPUT_<NAME>: host dir} of the pinned externals NAMES (strings) (recipe.fetch_externals, commit + tree_sha256 checked), or a string: why not."""
    import recipe
    try:
        pins = {e["name"]: e for e in json.load(open(PINS))["external"]}
        ext = [pins[n] for n in names]
        got = recipe.fetch_externals(ext, os.path.join(cache or ws, "store"), recipe.ext_dir(cache or ws), ws)
    except recipe.BuildRed as x:
        return "%s: %s" % (x.args[0].get("stage"), "; ".join(x.args[0].get("stderr", [])[-3:]))
    except (OSError, ValueError, KeyError) as x:
        return "inputs %s: %s" % (",".join(names), x)
    return {"FNVNX_INPUT_" + n.upper(): got[n]["host"] for n in names}

GUEST_PIN = "ports/fnv-nx/verify/guest_compiler.json"   # the one record of the game's compiler (and the runtime that is the same tree): the external the recipe-running tests mount

def guest_externals():
    """The externals of the guest pin ([compiler, runtime] entries of verify/guest_compiler.json, deduplicated by commit), or [] when the checkout has none."""
    try:
        pin = json.load(open(GUEST_PIN))
        out, seen = [], set()
        for e in (pin["external"], pin["runtime"]):
            if e["commit"] not in seen:
                seen.add(e["commit"])
                out.append(e)
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return []

def fetch_guest(cache, ws):
    """{FNVNX_EXTCACHE: the cache dir of the externals, FNVNX_INPUT_GUEST: the compiler's checked-out tree} of the guest pin (recipe.fetch_externals, commit + tree_sha256 checked), or a
    string: why not. A test that runs the recipe finds the compiler in FNVNX_EXTCACHE (recipe.build's default external cache), so no test fetches it again."""
    import recipe
    exts = guest_externals()
    if not exts:
        return {}
    ec = recipe.ext_dir(cache or ws)
    try:
        got = recipe.fetch_externals(exts, os.path.join(cache or ws, "store"), ec, ws)
    except recipe.BuildRed as x:
        return "%s: %s" % (x.args[0].get("stage"), "; ".join(x.args[0].get("stderr", [])[-3:]))
    except OSError as x:
        return "guest external: %s" % x
    return {"FNVNX_EXTCACHE": ec, "FNVNX_INPUT_GUEST": got[exts[0]["name"]]["host"]}

def run_one(path, timeout, base, idx, extra=None, trace=None, cwd=None):
    """(passed, seconds, reason). The test runs with TMPDIR = its own directory BASE/tIDX, made here and removed here whatever the
    outcome (exit, failure, timeout kill, exception); EXTRA: the FNVNX_INPUT_<NAME> variables of the declared inputs: scratch cannot outlive a test. A test that leaves entries in it at exit is
    uncertified ("leaked"). reason: "" when passed, else the exit status, the leaked names and the tail of the test's output."""
    t0 = time.time()
    tdir, logp = os.path.join(base, "t%d" % idx), os.path.join(base, "t%d.log" % idx)
    os.mkdir(tdir)
    # a test file is not a gate: it does not see GATE_OUT / GATE_CACHE (a test that runs gates of its own
    # would otherwise share this gate's result cache and outputs)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
    env["TMPDIR"] = tdir
    why = []
    env.update(extra or {})
    try:
        with open(logp, "wb") as log:
            cmd = [sys.executable, "-I", path]
            if trace:
                cmd = ["strace", "-f", "--seccomp-bpf", "-y", "-qq", "-e", "trace=" + opentrace.TRACE_SET + "," + opentrace.READ_EXTRA, "-o", trace] + cmd
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, env=env, cwd=cwd)
            try:
                rc = p.wait(timeout=timeout)
                if rc < 0:
                    why.append("killed by signal %d (%s)" % (-rc, signal.Signals(-rc).name if -rc in {int(x) for x in signal.Signals} else "?"))
                elif rc:
                    why.append("exit %d" % rc)
            except subprocess.TimeoutExpired:
                why.append("timeout %gs" % timeout)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)   # whatever the file left running in its process group
            p.wait()
        try:
            left = sorted(os.listdir(tdir))
        except OSError as e:
            left = []
            why.append("TMPDIR unreadable: %s" % e)
        if left:
            why.append("leaked in TMPDIR: %s" % ", ".join(left[:8]) + (" ..." if len(left) > 8 else ""))
    except OSError as e:  # e.g. ENOSPC while starting the test: the cause is the reason
        why.append("%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tdir, ignore_errors=True)
        if os.path.exists(tdir):
            why.append("cannot remove its TMPDIR %s" % tdir)
    if why:
        try:
            with open(logp, "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 1500))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            tail = ""
        why.append("output tail: " + tail)
    with contextlib.suppress(OSError):
        os.unlink(logp)
    return not why, round(time.time() - t0, 3), "; ".join(why)

def parse(argv):
    o = {"timeout": 300.0, "jobs": 1, "cache": os.environ.get("GATE_CACHE") or None, "key_dirs": None}
    a, dirs, i = list(argv), [], 0
    while i < len(a):
        if a[i] == "--timeout-s" and i + 1 < len(a):
            o["timeout"], i = float(a[i + 1]), i + 2
        elif a[i] == "--jobs" and i + 1 < len(a):
            o["jobs"], i = max(1, int(a[i + 1])), i + 2
        elif a[i] == "--cache" and i + 1 < len(a):
            o["cache"], i = a[i + 1], i + 2
        elif a[i] == "--key-dirs":
            i += 1
            o["key_dirs"] = []
            while i < len(a) and not a[i].startswith("--"):
                o["key_dirs"].append(a[i])
                i += 1
        elif a[i] == "--":
            dirs += a[i + 1:]
            break
        else:
            dirs.append(a[i])
            i += 1
    return o, dirs

def env_id():
    env_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")
    try:
        return envcheck.env_id(json.load(open(env_json)))
    except (OSError, ValueError):
        return "none"

def strip_argv(argv):
    out, i = [], 0
    while i < len(argv):
        if argv[i] in ("--cache", "--jobs") and i + 1 < len(argv):
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out

def key_of(path, argv, pins, eid):
    """KEY of the doc comment for test file PATH: its path, the env id, this gate's argv, the python version and the pins of its declared inputs."""
    return hashlib.sha256(cas.canon({"format": CACHE_FORMAT, "test": path, "env": eid, "argv": argv, "python": sys.version, "pins": pins})).hexdigest()

class State:
    """The state of paths of the checkout, read once per gate run (sha256 of content by path)."""
    def __init__(self, root):
        self.root, self.memo, self.tree = root, {}, None

    def of(self, rel):
        if rel in self.memo:
            return self.memo[rel]
        p = os.path.join(self.root, rel)
        if os.path.islink(p):
            v = "link:" + os.readlink(p)
        elif os.path.isdir(p):
            v = "dir"
        elif os.path.isfile(p):
            with open(p, "rb") as fh:
                v = hashlib.sha256(fh.read()).hexdigest()
        else:
            v = "absent"
        self.memo[rel] = v
        return v

    def listing(self, rel):
        key = "list:" + rel
        if key not in self.memo:
            try:
                names = sorted(n for n in os.listdir(os.path.join(self.root, rel)) if n != "__pycache__")
            except OSError:
                names = None
            self.memo[key] = hashlib.sha256(json.dumps(names).encode()).hexdigest()
        return self.memo[key]

    def git_tree(self):
        if self.tree is None:
            r = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.root, capture_output=True, text=True)
            self.tree = r.stdout.strip() if r.returncode == 0 else "no-tree"
        return self.tree

    def whole(self, dirs):
        return {d: (cas.tree_hash(os.path.join(self.root, d), skip=("__pycache__",)) if os.path.exists(os.path.join(self.root, d)) else "absent") for d in dirs}

def gitdirs(root):
    """The git metadata of the checkout (a worktree's gitdir and the common dir): files a test reads that are not files of the tree."""
    out = {os.path.join(root, ".git")}
    for flag in ("--git-dir", "--git-common-dir"):
        r = subprocess.run(["git", "rev-parse", "--absolute-git-dir" if flag == "--git-dir" else flag], cwd=root, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            out.add(os.path.realpath(os.path.join(root, r.stdout.strip())))
    return sorted(out)

def read_set(lines, root, skip, gits):
    """(files {rel: None}, dirs {rel}, git bool) of an strace log: what the run read under ROOT, without the SKIP directories (its own TMPDIR) and __pycache__."""
    files, dirs, git = {}, set(), False
    for _pid, kind, path, fl in opentrace.parse(lines, root, extra=True):
        if kind == "open" and "O_WRONLY" in fl and "O_RDWR" not in fl:
            continue   # a file only written is not read: its content is no input of the run
        if any(opentrace.under(path, [g]) for g in gits):
            git = True
            continue
        if not opentrace.under(path, [root]) or path == root or any(opentrace.under(path, [x]) for x in skip) or "/__pycache__" in path + "/":
            continue
        rel = path[len(root) + 1:]
        (dirs.add(rel) if kind == "list" else files.__setitem__(rel, None))
    return files, dirs, git

def entry_dir(cache, key):
    return os.path.join(cache, "selftests", key)

def lookup(cache, key, st, whole_dirs, mount=None):
    """True iff an entry under KEY has the recorded state now (traced: every path of its read set; untraced: the trees of its directories)."""
    d = entry_dir(cache, key)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return False
    for n in names:
        try:
            e = json.load(open(os.path.join(d, n)))
            if e["format"] != CACHE_FORMAT or e["status"] != "passed":
                continue
            if e["untraced"]:
                if mount is not None and e.get("mount") == mount:
                    return True
                continue
            if all(st.of(r) == v for r, v in e["files"].items()) and all(st.listing(r) == v for r, v in e["dirs"].items()) and (not e["git"] or e["tree"] == st.git_tree()):
                return True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return False

def record(cache, key, st, entry):
    d = entry_dir(cache, key)
    os.makedirs(d, exist_ok=True)
    entry = dict(entry, format=CACHE_FORMAT, status="passed")
    h = hashlib.sha256(cas.canon(entry)).hexdigest()
    tmp = os.path.join(d, ".tmp%d-%s" % (os.getpid(), h[:8]))
    with open(tmp, "w") as fh:
        json.dump(entry, fh, sort_keys=True)
    os.replace(tmp, os.path.join(d, h + ".json"))
    old = sorted((os.path.getmtime(os.path.join(d, n)), n) for n in os.listdir(d) if n.endswith(".json"))
    for _, n in old[:-KEEP]:
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(d, n))

def admitted_roots():
    try:
        return {r["path"] for r in json.load(open(os.path.join("ports", "fnv-nx", "verify", "admit.json")))["roots"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None

def split_cut(files):
    """(files to run, [(file, [roots])] of the cut): the test files of the universe whose roots are all admitted, and those that name a root that is not (cut_of)."""
    adm = admitted_roots()
    if adm is None:
        return files, []
    keep, cut = [], []
    for f in files:
        c = cut_of(f, adm)
        (cut if c else keep).append((f, c) if c else f)
    return keep, cut

def scan_dirs(dirs):
    """The directories whose tests/test_*.py are the universe: DIRS when a fixture names them, else the admitted roots of verify/admit.json (admitted_roots; sorted). One rule over the
    tree: a new test file under an admitted root is inside without an edit of any gate file."""
    if dirs:
        return list(dirs)
    return sorted(admitted_roots() or [])

def listed(dirs):
    files = []
    for d in scan_dirs(dirs):
        files += sorted(os.path.normpath(f) for f in glob.glob(os.path.join(glob.escape(d), "tests", "test_*.py")))
    return files

def universe(dirs=()):
    """the test files of the doc comment's universe (gate "oracle" judges the same list): every tests/test_*.py of every admitted root minus the cut (split_cut)"""
    return split_cut(listed(dirs))[0]

def cut_lines(dirs=()):
    return ["cut:test:%s (root %s)" % (f, ", ".join(r)) for f, r in split_cut(listed(dirs))[1]]

def main(argv):
    o, dirs = parse(argv)
    files = universe(dirs)
    t0 = time.time()
    root = os.path.realpath(os.getcwd())
    st, eid, sargv = State(root), env_id(), strip_argv(argv)
    whole_dirs = o["key_dirs"] if o["key_dirs"] is not None else dirs
    try:
        pinned = {e["name"]: e for e in json.load(open(PINS))["external"]}
    except (OSError, ValueError, KeyError, TypeError):
        pinned = {}
    plan = {}   # file -> (key, untraced, entries, mount files, mount digest, produced {FNVNX_INPUT_<NAME>: path} or a string: why not)
    tmpc = None if o["cache"] else tempfile.mkdtemp(prefix="selftests-prod-")   # no cache: produced inputs are built into the gate's own scratch store
    built = {}
    for f in files:
        ents = declared_inputs(f)
        names = None if ents is None else [e for e in ents if isinstance(e, str)]
        pins = [[n, pinned.get(n, {}).get("commit"), pinned.get(n, {}).get("tree_sha256")] for n in (names or [])]
        prod = {}
        for e in (ents or []):
            if isinstance(e, dict):
                k = (e["root"], e["output"])
                if k not in built:
                    built[k] = produced_input(e, o["cache"] or tmpc)
                got = built[k]
                if isinstance(got, str):
                    prod = got
                    break
                prod["FNVNX_INPUT_" + e["output"].upper()] = got[0]
                pins.append(["output:%s:%s" % k, None, got[1]])   # the key holds the output's tree hash
        un = untraced_of(f)
        if un:
            pins += [["guest:" + e["name"], e["commit"], e["tree_sha256"]] for e in guest_externals()]
        mf = mount_files(f) if un else None
        plan[f] = (key_of(f, sargv, pins, eid), un, ents, mf, mount_digest(mf, st) if un else None, prod)
    hit = {f for f in files if o["cache"] and plan[f][2] is not None and not isinstance(plan[f][5], str) and lookup(o["cache"], plan[f][0], st, whole_dirs, plan[f][4])}
    todo = [f for f in files if f not in hit]
    res = {}
    if todo:
        base = tempfile.mkdtemp(prefix="selftests-")  # in the gate's own TMPDIR; removed below whatever happens
        ws = os.path.join(base, "inputs")
        os.mkdir(ws)
        gits = gitdirs(root)

        def one(a):
            idx, f = a
            key, untraced, ents, mfiles, mdig, prod = plan[f]
            if ents is None:
                return "failed", 0.0, "tests/inputs.json is not a valid fnvnx-selftest-inputs/1 file"
            if isinstance(prod, str):
                return "failed", 0.0, prod
            names = [e for e in ents if isinstance(e, str)]
            extra = None
            if untraced:   # a file that runs the recipe finds the game's compiler mounted (the guest pin), fetched once into the gate's cache
                got = fetch_guest(o["cache"], ws)
                if isinstance(got, str):
                    return "blocked-env", 0.0, got
                extra = got
            if names:
                got = fetch_inputs(names, o["cache"], ws)   # a test that needs game data declares it; no data here: blocked, never green, never skipped
                if isinstance(got, str):
                    return "blocked-env", 0.0, got
                extra = dict(extra or {}, **got)
            if prod:
                extra = dict(extra or {}, **prod)
            tracing = bool(o["cache"]) and not untraced
            if tracing and shutil.which("strace") is None:
                return "failed", 0.0, "strace is not installed: the read set of the file cannot be recorded"
            tr = os.path.join(base, "t%d.strace" % idx) if tracing else None
            cwd = None
            if untraced:   # the mount: the file's import closure and its declared reads, nothing else of the checkout
                cwd = os.path.join(base, "m%d" % idx)
                mount_make(mfiles, cwd)
            ok, sec, why = run_one(f, o["timeout"], base, idx, extra, tr, cwd)
            if cwd:
                shutil.rmtree(cwd, ignore_errors=True)
            if ok and o["cache"]:
                if untraced:
                    record(o["cache"], key, st, {"untraced": True, "mount": mdig, "files": {}, "dirs": {}, "git": False, "tree": None})
                else:
                    try:
                        lines = open(tr, errors="replace").read().splitlines()
                    except OSError as x:
                        return "failed", sec, "no trace of the run: %s" % x
                    fs, ds, git = read_set(lines, root, [base], gits)
                    record(o["cache"], key, st, {"untraced": False, "trees": {}, "files": {r: st.of(r) for r in sorted(fs)}, "dirs": {r: st.listing(r) for r in sorted(ds)},
                                                 "git": git, "tree": st.git_tree() if git else None})
                    with contextlib.suppress(OSError):
                        os.unlink(tr)
            return ("passed" if ok else "failed"), sec, why
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=o["jobs"]) as ex:
                for f, r in zip(todo, ex.map(one, [(i, f) for i, f in enumerate(todo)])):
                    res[f] = r
        finally:
            shutil.rmtree(base, ignore_errors=True)
    if tmpc:
        shutil.rmtree(tmpc, ignore_errors=True)
    rows = []
    for f in files:
        if f in hit:
            rows.append({"name": f, "seconds": 0.0, "status": "cached"})
        else:
            stt, sec, why = res[f]
            rows.append(dict({"name": f, "seconds": sec, "status": stt}, **({} if stt == "passed" else {"reason": why})))
    note = ", %d cached" % len(hit) if o["cache"] else ""
    secs = time.time() - t0
    unc = [r["name"] for r in rows if r["status"] in ("failed", "blocked-env")]
    blocked = [r["name"] for r in rows if r["status"] == "blocked-env"]
    n = len(files) - len(unc)
    status = "green" if not unc else "red"
    cuts = cut_lines(dirs)
    detail = ("%d of %d test files pass%s, %.1f s" % (n, len(files), note, secs)) if files else "no test files at this commit (nothing to certify yet)"
    if cuts:
        detail += "; %d in the cut: %s" % (len(cuts), "; ".join(cuts))
    if blocked:
        detail += "; blocked: env (declared input not available) for " + ", ".join(blocked)
    res = {"format": "fnvnx-gate/1", "name": "selftests", "status": status, "universe": len(files), "certified": n,
           "uncertified": unc[:50], "detail": detail, "files": rows,
           "reasons": {r["name"]: r["reason"] for r in rows if r["status"] in ("failed", "blocked-env") and r.get("reason") and r["name"] in unc[:50]}}
    out = os.environ["GATE_OUT"]
    with open(os.path.join(out, "selftests.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    with open(os.path.join(out, "selftests.times.json"), "w") as fh:
        json.dump(rows, fh)
    return 0 if status == "green" else 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
