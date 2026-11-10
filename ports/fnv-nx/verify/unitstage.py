#!/usr/bin/env python3
"""unitstage (level L0 of the fast loop): one C++ translation unit as two hermetic stages (sandbox.py), so an object is never
recompiled when nothing it reads changed.

    unitstage.py build --store S --toolrt toolrt.json --cxx /abs/g++ --src-root HOST@/src --include HOST@/inc/0 ...
                       --unit REL.cpp ... --flags-file FLAGS.json --report R.json [--jobs N] [--cold]
    unitstage.py gcc-set --cxx /abs/g++       print the --exe/--tree arguments of `runtime.py tool` for that compiler
RULE: an object file is a function of exactly the compiler (content), the flags, the unit's source and the header files it includes.
  DEPS stage    inputs: the tool runtime (runtime.py tool), the whole source root at /src, the include roots at /inc/0.. ;
                ONE invocation: `g++ FLAGS -M -MT x -MF /out/deps.d /src/UNIT` (no -MG: a missing header is an error, rc != 0).
                parse_deps() turns deps.d into a sorted list: a pure function of the text (make rule syntax: backslash-newline
                continuations, `\\ ` and `\\#` escapes with doubled-backslash rules, `$$`), run by this runner, outside the sandbox.
  COMPILE stage inputs: the same tool runtime + ONLY the listed files, each at its original path (/src/.., /inc/N/..), as a filtered
                tree whose manifest is built from the CAS manifests (no file content is copied); `g++ FLAGS -c -o /out/unit.o`.
  The compile key changes iff a file the unit really reads (or its content, or the flags/compiler) changes.
Soundness: the DEPS stage sees the full trees, so its include resolution is the real one. Every file the real resolution opened is
  in the list; the COMPILE stage sees a subset with those files at the same paths. An include search probes directories in order:
  a probe that FAILED in the full tree (absent in an earlier include dir) fails in the subset too, and a file absent from the list was
  not opened, and cannot be opened by the subset run because it does not exist there. A new file earlier in the search path changes
  the DEPS result (so the list and the key). Symlinks inside a tree are followed inside the tree (link and target both kept).
OPEN GAP (measured, tests/test_unitstage.py): `__has_include("h")` reads the PRESENCE of h. GCC 13 `-M` does not list a header that is
  only probed, so the COMPILE stage sees it absent and takes the other branch: the object differs from a full-tree compile. No exact
  GCC mechanism was found (-H, -MD, -E list included files only; a text scan is not acceptable). Not closed here.
Flags appended by this tool: -I/inc/N (in order), -frandom-seed=<sha256 of the unit path>, -ffile-prefix-map for /src and each
  /inc/N (no host or sandbox path in the object), -Werror=date-time. Both stage documents must pass toolflags.check_doc (violation =
  error). Caller flags may not contain -I*, -o, -c, -E, -S, -M*. Sandbox paths are fixed, so keys are host-independent. Documents are
  kept at STORE/stage-docs/<aa>/<key>.json. --cold runs a stage again and fails if the result differs from the stored one.
Report "fnvnx-unitstage/1": per unit deps_key, deps_status (hit|run), deps_rc, deps_seconds, files (closure size), compile_key,
  compile_status (hit|run|skipped), compile_rc, compile_seconds, object (tree hash of /out), status (ok|deps-failed|compile-failed),
  error (stderr tail); totals. Exit 0 all ok, 2 some unit failed, 1 usage/flags error.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import posixpath
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import sandbox  # noqa: E402
import toolflags  # noqa: E402
FORMAT = "fnvnx-unitstage/1"
ENV = {"TZ": "UTC", "LC_ALL": "C", "PYTHONHASHSEED": "0", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80"}

class UnitError(Exception):
    pass
def parse_deps(text):
    """Sorted, normalized, unique prerequisites of the single rule `x: ...` in text (GCC -MT x output)."""
    toks, cur, have, closed, i, n = [], [], False, False, 0, len(text)

    def flush():
        nonlocal cur, have
        toks.extend(["".join(cur)] * have)
        cur, have = [], False

    def add(s):
        nonlocal have
        if closed:
            raise UnitError("deps.d: second rule or text after the rule")
        cur.append(s)
        have = True
    while i < n:
        c = text[i]
        if c == "\\":
            j = i
            while j < n and text[j] == "\\":
                j += 1
            k, nxt = j - i, text[j:j + 1]
            if nxt in (" ", "\t", "\n", "#"):
                if k // 2:
                    add("\\" * (k // 2))
                if k % 2 == 0:
                    i = j
                    continue
                if nxt == "\n":
                    flush()
                else:
                    add(nxt)
                i = j + 1
                continue
            add("\\" * k)
            i = j
        elif c == "$":
            if text[i + 1:i + 2] != "$":
                raise UnitError("deps.d: single $")
            add("$")
            i += 2
        elif c in " \t":
            flush()
            i += 1
        elif c == "\n":
            flush()
            closed = True
            i += 1
        elif c == "#":
            raise UnitError("deps.d: unescaped #")
        else:
            add(c)
            i += 1
    flush()
    if not toks or toks[0] != "x:":
        raise UnitError("deps.d: not a rule for target x")
    out = set()
    for t in toks[1:]:
        if not t.startswith("/"):
            raise UnitError("deps.d: relative path %r" % t)
        out.add(posixpath.normpath(t))
    return sorted(out)
def _out_file(store, res, name):
    return cas._obj(store, cas.manifest(res["outputs"]["/out"], store)["entries"][name])

def deps_of(store, deps_key):
    return parse_deps(open(_out_file(store, sandbox._load(store, deps_key), "deps.d")).read())

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
    return [norm(cxx), norm(q("-print-prog-name=as"))], trees

def _entry(store, root, path, cache):
    e = {"t": "d", "h": root}
    for p in path.split("/"):
        if e["t"] != "d" or p not in cache.setdefault(e["h"], cas.manifest(e["h"], store))["entries"]:
            raise UnitError("closure file %s is not in the ingested tree" % path)
        e = cache[e["h"]]["entries"][p]
    return e

def closure_paths(store, root, rels, cache):
    """Tree-relative files and symlinks the kernel reads to open RELS (links followed inside the tree)."""
    out, todo = set(), [(r, 0) for r in rels]
    while todo:
        rel, depth = todo.pop()
        cur, parts = "", rel.split("/")
        if depth > 16:
            raise UnitError("symlink loop at " + rel)
        for k, p in enumerate(parts):
            nxt = cur + "/" + p if cur else p
            e = _entry(store, root, nxt, cache)
            if e["t"] == "l":
                real = posixpath.normpath(posixpath.join(cur, e["to"]))
                if e["to"].startswith("/") or real.startswith(".."):
                    raise UnitError("symlink %s leaves the tree" % nxt)
                out.add(nxt)
                todo.append(("/".join([real] + parts[k + 1:]), depth + 1))
                break
            cur = nxt
        else:
            if e["t"] != "f":
                raise UnitError("%s is not a file" % rel)
            out.add(cur)
    return out

def subtree(store, root, rels, cache):
    """Hash of the tree holding only RELS of tree ROOT (entries copied from the manifests, no content is touched)."""
    paths = closure_paths(store, root, rels, cache)

    def build(prefix, names):
        entries = {}
        for name in sorted({p.split("/")[0] for p in names}):
            sub = [p.split("/", 1)[1] for p in names if p.startswith(name + "/")]
            full = (prefix + "/" if prefix else "") + name
            entries[name] = {"t": "d", "h": build(full, sub)} if sub else _entry(store, root, full, cache)
        data = cas.canon({"format": cas.FORMAT, "entries": entries})
        h = hashlib.sha256(data).hexdigest()
        cas._put(store, "tree", h + ".json", data)
        return h
    return build("", sorted(paths))

def run_stage(store, doc, cold):
    sandbox.check_doc(doc)
    key = sandbox.key_of(doc)
    cas._put(store, "stage-docs", key + ".json", cas.canon(doc) + b"\n")
    old = sandbox._load(store, key)
    if old is not None and not cold:
        return key, old, "hit", 0.0
    t0 = time.time()
    res = sandbox.execute(store, doc, key)
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
        self.rt = json.load(open(a.toolrt))
        if self.cxx not in self.rt["exes"]:
            raise UnitError("--cxx %s is not an exe of the tool runtime" % self.cxx)
        self.flags = json.load(open(a.flags_file))["argv"]
        bad = [f for f in self.flags if not isinstance(f, str) or f in ("-o", "-c", "-E", "-S") or f.startswith(("-M", "-I"))]
        if bad:
            raise UnitError("caller flags that change a stage's job (use --include for -I): %s" % bad)
        self.roots = []
        for spec, want in [(a.src_root, "/src")] + [(s, "/inc/%d" % n) for n, s in enumerate(a.include)]:
            host, _, at = spec.rpartition("@")
            if at != want:
                raise UnitError("%s: sandbox path must be %s" % (spec, want))
            self.roots.append((at, cas.ingest(host, self.store)))

    def doc(self, kind, rel, inputs, tail, out):
        seed = hashlib.sha256(rel.encode()).hexdigest()
        argv = [self.cxx] + ["-I" + at for at, _ in self.roots[1:]] + self.flags + ["-frandom-seed=" + seed, "-Werror=date-time"]
        argv += ["-ffile-prefix-map=%s=%s" % (at, at[1:]) for at, _ in self.roots] + tail + ["/src/" + rel]
        doc = {"format": "fnvnx-stage/1", "stage": "%s:%s" % (kind, rel), "argv": argv, "cwd": "/", "class": "build", "tool": "gcc",
               "env": dict(ENV, PATH=os.path.dirname(self.cxx)), "outputs": ["/out"],
               "inputs": sorted(self.rt["inputs"] + [{"at": at, "tree": t} for at, t in inputs], key=lambda i: i["at"])}
        v = toolflags.check_doc(doc)
        if v:
            raise UnitError("toolflags: " + "; ".join("%s %s" % (r, d) for r, d, _ in v))
        return doc

    def unit(self, rel):
        e = {"unit": rel, "status": "ok", "compile_status": "skipped", "object": None}
        d = self.doc("deps", rel, self.roots, ["-M", "-MT", "x", "-MF", "/out/deps.d"], "/out")
        e["deps_key"], res, e["deps_status"], e["deps_seconds"] = run_stage(self.store, d, self.cold)
        e["deps_rc"] = res["rc"]
        if res["rc"]:
            e["status"], e["error"] = "deps-failed", self.tail(res)
            return e
        deps = deps_of(self.store, e["deps_key"])
        e["files"] = len(deps)
        per = {at: [] for at, _ in self.roots}
        for p in deps:
            at = next((a for a in per if p.startswith(a + "/")), None)
            if at:
                per[at].append(p[len(at) + 1:])
            elif not any(p.startswith(i["at"].rstrip("/") + "/") or p == i["at"] for i in self.rt["inputs"]):
                raise UnitError("%s: dependency %s is in no declared input" % (rel, p))
        sub = [(at, subtree(self.store, t, per[at], self.cache)) for at, t in self.roots]
        c = self.doc("compile", rel, sub, ["-c", "-o", "/out/unit.o"], "/out")
        e["compile_key"], res, e["compile_status"], e["compile_seconds"] = run_stage(self.store, c, self.cold)
        e["compile_rc"] = res["rc"]
        if res["rc"]:
            e["status"], e["error"] = "compile-failed", self.tail(res)
        e["object"] = None if res["rc"] else res["outputs"]["/out"]
        return e

    def tail(self, res):
        return open(cas._obj(self.store, cas.manifest(res["stderr"], self.store))).read()[-300:]

def build_cmd(a):
    t0 = time.time()
    b = Build(a)
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        units = list(ex.map(b.unit, a.unit))
    tot = {"units": len(units), "ok": sum(u["status"] == "ok" for u in units), "failed": sum(u["status"] != "ok" for u in units),
           "deps_hit": sum(u["deps_status"] == "hit" for u in units), "deps_run": sum(u["deps_status"] == "run" for u in units),
           "compile_hit": sum(u["compile_status"] == "hit" for u in units), "compile_run": sum(u["compile_status"] == "run" for u in units),
           "seconds": round(time.time() - t0, 2)}
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
