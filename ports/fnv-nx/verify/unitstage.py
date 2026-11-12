#!/usr/bin/env python3
"""unitstage (level L0 of the fast loop): one C++ translation unit as two hermetic stages (sandbox.py), so an object is never
recompiled when nothing it reads changed.

    unitstage.py build --store S --toolrt toolrt.json --cxx /abs/g++ --src-root HOST@/src --include HOST@/inc/0 ...
                       --unit REL.cpp ... --flags-file FLAGS.json --report R.json [--jobs N] [--cold]
    unitstage.py gcc-set --cxx /abs/g++       print the --exe/--tree arguments of `runtime.py tool` for that compiler
RULE: what a preprocessor run reads is exactly (1) the CONTENT of the files it opens and (2) the EXISTENCE answers for the paths it
  probes (#include search, __has_include, __has_include_next). A stage is given both, exactly.
  PP stage  `g++ FLAGS -E -dU -MD -MT x -MF /out/deps.d -o /out/unit.ii /src/UNIT`, inputs: the tool runtime (runtime.py tool) and VIEWS
            of the source root (/src) and the include roots (/inc/0..), see unitview.py: every name of the real tree, real content
            only for the files of a set C (the closure), every other file a placeholder `#error fnvnx: file outside the declared
            closure`. The key is f(name trees, content of C, compiler, flags).
  CC stage  `g++ FLAGS -fpreprocessed -c -o /out/unit.o /in/unit.ii`, inputs: the tool runtime and unit.ii. The key is f(preprocessed
            text, compiler, flags): a comment-only header edit re-runs PP for the units that read it, CC is a hit.
Closure discovery (this runner, outside the sandbox, a pure function of stage results): C0 = a hint (STORE/hints/<sha256 unit>.json,
  the last accepted closure of that unit path) or, without one, every file (the view is the real tree). Run PP. Failure: if the
  compiler printed the placeholder's own #error for files not in C (parsed from the `FILE:L:C: error:` prefix of that message), add
  them to C and run again (each pass adds >= 1 file: terminates); any other failure is the unit's real error, reported, nothing
  stored as success (no hint). Success: D = files of deps.d (resolved like the kernel does, symlinks followed, `..` on the real
  directory). If D != C run again with C = D (the minimal view) and require deps == D and the SAME unit.ii hash (else exit 1 with both
  hashes); the accepted result is the run on the view of D. ARGUMENT that the hint never changes the result: the accepted run opened
  only files of D, all real, and saw the true name tree, so every include search and probe got the answer it gets on the real tree and
  every opened file had its real content; its output equals the output on the real trees. A different hint only changes how many runs
  lead to D. ASSUMED about GCC: it reads the content of a file only by lexing it (then the file is in deps.d) or it fails; (a
  #pragma-once identity check on a file that is not included would not be caught by the placeholder, only by the equality assert).
Languages by suffix (LANG): .c gets `-x c` (PP) and `-x c -fpreprocessed` (CC); .S gets `-x assembler-with-cpp` and `-x assembler` (CC on the
  preprocessed text, the same two stages); .s has no PP stage (its CC stage reads the unit alone, a `.include` fails). `--include` may also be
  an absolute sandbox path inside an input of the tool runtime (an external tree): it is passed as `-I<path>` in the given order; its content is
  covered by the runtime's input hash (not by the closure).
Flags appended by this tool: -I/inc/N (PP only), -frandom-seed=<sha256 of the unit path>, -ffile-prefix-map for /src and each /inc/N,
  -Werror=date-time (the macros expand at PP). Both stage documents must pass toolflags.check_doc. Caller flags may not contain -I*,
  -o, -c, -E, -S, -M*. Sandbox paths are fixed, so keys are host-independent; line markers of unit.ii hold only /src, /inc/N paths.
  Documents are kept at STORE/stage-docs/<aa>/<key>.json. --cold runs a stage again and fails if the result differs.
Report "fnvnx-unitstage/2": per unit status (ok|pp-failed|cc-failed), closure (sandbox paths of real files), files, pp_key, pp_status
  (hit if every iteration hit), pp_seconds, pp_iterations, ii_bytes, cc_key, cc_status (hit|run|skipped), cc_seconds, object (tree hash
  of /out), name_tree_id (hash of the placeholder-only views = the pure name trees), error (the last 20 lines of the failing stage's stderr); totals. Exit 0 ok, 2 a unit failed, 1 usage.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import posixpath
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import sandbox  # noqa: E402
import toolflags  # noqa: E402
import unitview  # noqa: E402
from unitview import UnitError, parse_deps  # noqa: E402,F401
FORMAT = "fnvnx-unitstage/2"
ENV = {"TZ": "UTC", "LC_ALL": "C", "PYTHONHASHSEED": "0", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80"}
# language options by the unit's suffix (what the driver itself does by suffix, made explicit so that the stage's input name does not matter):
# (PP stage, CC stage). .s has no preprocessing: its CC stage reads the unit alone (a `.include` there fails: use .S and #include).
LANG = {".c": (["-x", "c"], ["-x", "c", "-fpreprocessed"]), ".S": (["-x", "assembler-with-cpp"], ["-x", "assembler"]),
        ".s": ([], ["-x", "assembler"])}
PP_TAIL = ["-E", "-dU", "-MD", "-MT", "x", "-MF", "/out/deps.d", "-o", "/out/unit.ii"]


def result_file(store, key, name):
    return cas._obj(store, cas.manifest(sandbox._load(store, key)["outputs"]["/out"], store)["entries"][name])


def hint_path(store, rel):
    return os.path.join(store, "hints", hashlib.sha256(rel.encode()).hexdigest() + ".json")


def write_hint(store, rel, paths):
    p = hint_path(store, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.tmp-%d-%s" % (p, os.getpid(), os.urandom(4).hex())
    open(tmp, "w").write(json.dumps(sorted(paths)))
    os.replace(tmp, p)


def gcc_set(cxx):
    """(exes, trees) for runtime.py tool, defined by the compiler's own answers: -print-prog-name=cc1plus (libexec dir),
    -print-prog-name=as, -print-search-dirs (`install:` dir = headers/specs) and `echo | g++ -x c++ -E -v -` (built-in include
    dirs). Trees nested in another tree are dropped."""
    env = dict(os.environ, LC_ALL="C")

    def q(*a, inp=None):
        p = subprocess.run([cxx] + list(a), input=inp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return p.stdout
    def norm(p):
        return posixpath.normpath(p.strip())
    cands = [os.path.dirname(norm(q("-print-prog-name=cc1plus")))]
    cands += [norm(ln[len("install:"):]) for ln in q("-print-search-dirs").splitlines() if ln.startswith("install:")]
    inc = q("-x", "c++", "-E", "-v", "-", inp="").split("search starts here:")[-1].split("End of search list.")[0]
    cands += [norm(ln) for ln in inc.splitlines() if ln.startswith(" /")]
    trees = sorted({t for t in cands if os.path.isdir(t)})
    trees = [t for t in trees if not any(u != t and (t + "/").startswith(u.rstrip("/") + "/") for u in trees)]
    return [norm(cxx), prog(norm(q("-print-prog-name=as")))], trees


def prog(p):
    """A program name the compiler answered with: the answer of a native compiler for a tool it finds on PATH is the bare name, which is
    resolved on the host PATH the way the compiler would exec it (a cross compiler answers its absolute path)."""
    return p if os.path.isabs(p) else (shutil.which(p) or p)

def run_stage(store, doc, cold):
    sandbox.check_doc(doc)
    key = sandbox.key_of(doc)
    cas._put(store, "stage-docs", key + ".json", cas.canon(doc) + b"\n")
    old = sandbox._load(store, key)
    if old is not None and not cold:
        return key, old, "hit", 0.0
    t0 = time.time()
    try:
        res = sandbox.execute(store, doc, key)
    except OSError as e:   # EMLINK, ENOSPC, EXDEV ... of the store or the sandbox set-up: a red stage naming the stage, never a crash
        x = sandbox.SbxError("stage %s: %s (errno %s)" % (doc["stage"], e.strerror or e, e.errno))
        x.stage = doc["stage"]
        raise x
    sec = time.time() - t0
    if old is None:
        sandbox._store_result(store, key, res)
        return key, res, "run", sec
    if any(res[k] != old[k] for k in ("rc", "stdout", "stderr", "outputs")):
        raise UnitError("NOT A FUNCTION OF THE KEY: stage %s differs between two runs" % doc["stage"])
    return key, old, "run", sec

class Build:
    def __init__(self, a):
        self.store, self.cold, self.cxx, self.cache = a.store, a.cold, a.cxx, {}
        self.variant = ""   # recipe variant: its name is in the env, so in every stage key ("" adds nothing)
        self.cc_drop = set()   # `at` of tool-runtime inputs the CC stage does not mount (externals other than the compiler's)
        self.cc_flags, self.cc_inputs = [], []   # plugins: flags and declared inputs [(at, tree)] of the CC stage only (a plugin is part of its key)
        self.rt = json.load(open(a.toolrt))
        if self.cxx not in self.rt["exes"]:
            raise UnitError("--cxx %s is not an exe of the tool runtime" % self.cxx)
        self.flags = json.load(open(a.flags_file))["argv"]
        bad = [f for f in self.flags if not isinstance(f, str) or f in ("-o", "-c", "-E", "-S") or f.startswith(("-M", "-I"))]
        if bad:
            raise UnitError("caller flags that change a stage's job (use --include for -I): %s" % bad)
        self.roots, self.iargs = [], []
        for spec in [a.src_root] + a.include:
            host, sep, at = spec.rpartition("@")
            if not sep and spec is not a.src_root:   # -I of a sandbox path inside a declared input of the tool runtime (an external)
                if not any(spec == i["at"] or spec.startswith(i["at"].rstrip("/") + "/") for i in self.rt["inputs"]):
                    raise UnitError("--include %s: not HOST@/inc/N and inside no input of the tool runtime" % spec)
                self.iargs.append("-I" + spec)
                continue
            want = "/src" if spec is a.src_root else "/inc/%d" % (len(self.roots) - 1)
            if at != want:
                raise UnitError("%s: sandbox path must be %s" % (spec, want))
            self.roots.append((at, cas.ingest(host, self.store)))
            if want != "/src":
                self.iargs.append("-I" + at)
        names = [(at, unitview.view(self.store, t, (), self.cache)) for at, t in self.roots]
        self.name_tree_id = hashlib.sha256(cas.canon(names)).hexdigest()

    def doc(self, kind, rel, inputs, tail, src, inc=True):
        seed = hashlib.sha256(rel.encode()).hexdigest()
        argv = [self.cxx] + (self.iargs if inc else []) + self.flags + (self.cc_flags if kind == "cc" else []) + ["-frandom-seed=" + seed, "-Werror=date-time"]
        argv += ["-ffile-prefix-map=%s=%s" % (at, at[1:]) for at, _ in self.roots] + tail + [src]
        doc = {"format": "fnvnx-stage/1", "stage": "%s:%s" % (kind, rel), "argv": argv, "cwd": "/", "class": "build", "tool": "gcc",
               "env": dict(ENV, PATH=os.path.dirname(self.cxx), **({"FNVNX_VARIANT": self.variant} if self.variant else {})), "outputs": ["/out"],
               "inputs": sorted([i for i in self.rt["inputs"] if kind != "cc" or i["at"] not in self.cc_drop] +
                                [{"at": at, "tree": t} for at, t in inputs + (self.cc_inputs if kind == "cc" else [])],
                                key=lambda i: i["at"])}
        v = toolflags.check_doc(doc)
        if v:
            raise UnitError("toolflags: " + "; ".join("%s %s" % (r, d) for r, d, _ in v))
        return doc

    def real(self, path, strict=True):
        """Sandbox path of the regular file the kernel opens for sandbox path PATH, or None (not in a root / not resolvable)."""
        for at, t in self.roots:
            if path.startswith(at + "/"):
                try:
                    return at + "/" + unitview.resolve(self.store, t, path[len(at) + 1:], self.cache)
                except (UnitError, KeyError):
                    if strict:
                        raise
        return None

    def pp(self, rel, files):
        """One PP run on the views of FILES (set of sandbox paths of real files; None = the real trees)."""
        ins = [(at, t if files is None else unitview.view(self.store, t, [p[len(at) + 1:] for p in files if p.startswith(at + "/")],
                                                          self.cache)) for at, t in self.roots]
        return run_stage(self.store, self.doc("pp", rel, ins, self.lang(rel)[0] + PP_TAIL, "/src/" + rel), self.cold)

    def stderr(self, res):
        return open(cas._obj(self.store, cas.manifest(res["stderr"], self.store)), errors="replace").read()

    def last(self, res):
        """The failure record: the last 20 lines of the stage's stderr."""
        return "\n".join(self.stderr(res).splitlines()[-20:])

    def deps(self, key):
        out = set()
        for p in parse_deps(open(result_file(self.store, key, "deps.d")).read()):
            r = self.real(p)
            if r:
                out.add(r)
            elif not any(p.startswith(i["at"].rstrip("/") + "/") or p == i["at"] for i in self.rt["inputs"]):
                raise UnitError("dependency %s is in no declared input" % p)
        return out

    def unit(self, rel):
        e = {"unit": rel, "status": "ok", "cc_status": "skipped", "object": None, "name_tree_id": self.name_tree_id, "pp_iterations": 0,
             "pp_seconds": 0.0}
        if rel.endswith(".s"):
            return self.assemble(e, rel)
        files, expect, ran = None, None, False
        try:
            hint = json.load(open(hint_path(self.store, rel)))
            files = {r for r in (self.real(p, False) for p in hint) if r} | {"/src/" + rel}
        except (OSError, ValueError, TypeError):
            pass
        while True:
            key, res, st, sec = self.pp(rel, files)
            e["pp_iterations"] += 1
            e["pp_seconds"] = round(e["pp_seconds"] + sec, 3)
            ran = ran or st == "run"
            e["pp_key"], e["pp_rc"], e["pp_status"] = key, res["rc"], "run" if ran else "hit"
            if res["rc"]:
                if expect:
                    raise UnitError("internal: minimal view of %s fails after the run it was derived from: %s" % (rel, self.last(res)))
                miss = {r for r in (self.real(p, False) for p in unitview.placeholder_paths(self.stderr(res))) if r}
                if files is None or not miss or miss <= files:
                    e["status"], e["error"] = "pp-failed", self.last(res)
                    return e
                files = files | miss
                continue
            d, h = self.deps(key), cas.manifest(res["outputs"]["/out"], self.store)["entries"]["unit.ii"]["h"]
            if expect and (d, h) != expect:
                raise UnitError("internal: %s: minimal view differs from the first run (deps %d vs %d, unit.ii %s vs %s)"
                                % (rel, len(d), len(expect[0]), h, expect[1]))
            if d == files:
                break
            expect, files = (d, h), d
        e["closure"], e["files"] = sorted(files), len(files)
        ii = cas.manifest(res["outputs"]["/out"], self.store)["entries"]["unit.ii"]
        e["ii_bytes"] = os.path.getsize(cas._obj(self.store, ii))
        tdata = cas.canon({"format": cas.FORMAT, "entries": {"unit.ii": dict(ii, x=0)}})
        tree = hashlib.sha256(tdata).hexdigest()
        cas._put(self.store, "tree", tree + ".json", tdata)
        c = self.doc("cc", rel, [("/in", tree)], self.lang(rel)[1] + ["-c", "-o", "/out/unit.o"], "/in/unit.ii", inc=False)
        return self.compile(e, rel, c, files)

    def lang(self, rel):
        return LANG.get(posixpath.splitext(rel)[1], ([], ["-fpreprocessed"]))

    def compile(self, e, rel, c, files):
        e["cc_key"], res, e["cc_status"], e["cc_seconds"] = run_stage(self.store, c, self.cold)
        e["cc_rc"] = res["rc"]
        if res["rc"]:
            e["status"], e["error"] = "cc-failed", self.last(res)
            return e
        e["object"] = res["outputs"]["/out"]
        if files:
            write_hint(self.store, rel, files)
        return e

    def assemble(self, e, rel):
        """A .s unit: no preprocessing, one stage on the /src view that holds this file alone (every other name is a placeholder)."""
        real = unitview.resolve(self.store, self.roots[0][1], rel, self.cache)
        v = unitview.view(self.store, self.roots[0][1], [real], self.cache)
        e["closure"], e["files"] = ["/src/" + real], 1
        return self.compile(e, rel, self.doc("cc", rel, [("/src", v)], self.lang(rel)[1] + ["-c", "-o", "/out/unit.o"], "/src/" + rel, inc=False), set())


def build_cmd(a):
    t0 = time.time()
    b = Build(a)
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        units = list(ex.map(b.unit, a.unit))
    n = lambda k, v: sum(u.get(k) == v for u in units)  # noqa: E731
    tot = {"units": len(units), "ok": n("status", "ok"), "failed": sum(u["status"] != "ok" for u in units), "pp_hit": n("pp_status", "hit"),
           "pp_run": n("pp_status", "run"), "cc_hit": n("cc_status", "hit"), "cc_run": n("cc_status", "run"),
           "pp_iterations": sum(u["pp_iterations"] for u in units), "ii_bytes": sum(u.get("ii_bytes", 0) for u in units),
           "name_tree_id": b.name_tree_id, "seconds": round(time.time() - t0, 2)}
    rep = {"format": FORMAT, "units": {u["unit"]: u for u in units}, "totals": tot}
    open(a.report, "w").write(json.dumps(rep, sort_keys=True, indent=1) + "\n")
    print(json.dumps(tot, sort_keys=True))
    return 0 if not tot["failed"] else 2


def main(argv):
    try:
        p = argparse.ArgumentParser(prog="unitstage.py")
        p.add_argument("cmd", choices=["build", "gcc-set"])
        p.add_argument("--cxx")
        if argv[:1] == ["build"]:
            [p.add_argument("--" + n, required=True) for n in ("store", "toolrt", "src-root", "flags-file", "report")]
            p.add_argument("--include", action="append", default=[])
            p.add_argument("--unit", action="append", required=True)
            p.add_argument("--jobs", type=int, default=1)
            p.add_argument("--cold", action="store_true")
        a = p.parse_args(argv)
        if a.cmd == "build":
            return build_cmd(a)
        exes, trees = gcc_set(a.cxx)
        print(" ".join(["--exe %s" % e for e in exes] + ["--tree %s" % t for t in trees]))
        return 0
    except (UnitError, sandbox.SbxError, cas.CasError, OSError, ValueError, KeyError) as e:
        print("unitstage: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
