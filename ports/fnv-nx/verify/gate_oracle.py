#!/usr/bin/env python3
"""Gate "oracle" (fnvnx-gate/1): every test CASE names where its expected values come from, and that source is not the
code under test (verify/VERDICT.md class 10).
    python3 -I gate_oracle.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/oracle.json

Universe: the test cases of the test files of gate "selftests" (the DIRs of its argv in verify/gates.d/selftests.json,
DIR/tests/test_*.py, found by gate_tests.universe: one list for both gates). An item is `FILE::CASE`, FILE repo-relative. The
cases of a file come from its syntax tree (case_names), by two forms only: (1) a module-level `def test_*` or a `test_*`
method of a unittest.TestCase subclass (CASE = NAME or Class.NAME); (2) a call `case("literal", ...)` of the one shared
function verify/testcase.py, resolved through the file's import of it (`from testcase import case`, or `import testcase` and
testcase.case), never by the bare name. Red: a file with neither form (item FILE); a case whose first argument is not a str
constant; a duplicate CASE in a file; any other binding of the name `case` (a local function named like the shared one). A
case in a loop is one static case with `key=` (testcase.py): the static name is the literal.
Declaration: DIR/tests/oracle.json ("fnvnx-oracle/1"):
  {"format": "fnvnx-oracle/1", "tests": {"test_x.py": {"subject": [PATH...], "refs": {ID: REF}, "cases": {CASE: [ID...]}}}}
  subject     the files under test (repo-relative).
  refs        the references of the file by id; cases maps EVERY case of the file (no more, no fewer) to the ids of its own
              references: a reference is owed per case, so a case without an entry is uncertified even when another case
              of the same file has one; an id no case uses is an error of the file.
  REF         one of (exactly these keys):
    {"kind": "wine-exe", "wine": "9.0", "exe_sha256": H64, "artifact": {"repo": "fnv-nx-build", "commit": H40,
     "tree_sha256": H64, "path": STR}}   a dump the game's exe produced under Wine 9.0: file `path` of that commit of
                                         the artifacts repository (an external, as in RECIPE.md)
    {"kind": "spec", "path": PATH, "anchor": STR}   cited text: a tracked file and a place in it. `anchor` is a line
                                         range "L<m>-L<n>" (1 <= m <= n <= number of lines) or a whole line of the file
                                         that starts with "#" (a heading) and occurs exactly once
    {"kind": "external", "name": NAME, "path": STR}   a file of an external that a recipe or the contract inputs pin (NAME is
                                         its `name`, pinned to one commit and tree_sha256; fetched like a wine-exe artifact)
    {"kind": "probe", "probe": {"external": NAME, "path": STR}, "platform": NAME, "dump_sha256": H64}   a probe program
                                         (a file of a pinned external) run on the declared platform (a pinned external) whose dump
                                         has this sha256
    {"kind": "contract", "path": PATH}     a contract table of the tree
    {"kind": "model", "path": PATH}        an independent model in the tree
Rule. A test case is certified iff ALL hold (2-6 for the references of that case, not of its file):
  1. its DIR's oracle.json is well formed and holds exactly one entry for its file (an entry without a test file, an unknown
     key or a wrong format is an error of the DIR: none of its tests is certified; a `cases` key without a case, or a case
     without a key, is an error);
  2. `subject` is not empty and every subject path is a tracked file inside a root of verify/admit.json;
  3. `references` is not empty and every REF has one of the forms above; `wine` is "9.0"; `exe_sha256` equals
     verify/oracle_pins.json "exe_sha256"; the artifact is fetched by its exact commit (`git fetch --depth 1
     $ARTIFACTS_URL <commit>`, cached in GATE_CACHE/external/<commit> like a recipe's external), its tree hash
     (cas.py) equals `tree_sha256` and `path` is a file of it; every `path` of a spec, contract or model is a tracked
     file inside a root; an external or probe names externals pinned by a recipe or the contract inputs, fetched to their
     tree_sha256, holding the path; a spec's anchor resolves in its file. A spec without a file and an anchor is not a reference;
  4. independence: no spec, contract or model `path` is in the import closure of the subject. The closure is computed
     from the syntax trees: a subject file, plus, for every `import a.b` / `from a.b import c` in a closure file, every
     tracked file of the tree named a/b.py, b.py, a/b/__init__.py, a/b/c.py or c.py at any depth (a superset of what
     python can load, whatever sys.path is; a closure file that does not parse makes the test uncertified);
  5. a subject file does not lie in a root of class ref (a ref root holds references only);
  6. product: when a subject file lies in a product root, some REF is wine-exe or spec, and no REF `path` lies in a
     product root.
Argument: an expected value "comes from the code under test" when its declared source is the subject or is computed
through it. Rules 2-5 leave four sources: a dump of the exe under the pinned Wine, text at a place that exists in a
file, a contract table and a model, the last three outside everything the subject can load, and for product code never
the product tree. The verdict is green only if certified == universe, so a case without such a source blocks the merge.
Assumption (checked by verification, not by this gate): the declaration is true - the subject list is complete and (ASM-042)
the test compares against the declared references. The after-merge verifier runs every test under verify/opentrace.py
and compares the tracked files it opened with subject + references (verify/oraclecheck.py)."""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import gate_admit  # noqa: E402
import gate_tests  # noqa: E402
import gatesdir  # noqa: E402

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
PINS = TREE + "/verify/oracle_pins.json"
FORMAT = "fnvnx-oracle/1"
HEX = set("0123456789abcdef")
TREE_KINDS = ("spec", "contract", "model")      # the kinds whose `path` is a tracked file of this tree


def is_hex(s, n):
    return isinstance(s, str) and len(s) == n and set(s) <= HEX


def text(s):
    return isinstance(s, str) and s.strip() != ""


def modules(src):
    """dotted names a source can import: (module, [imported names]) per import statement; raises SyntaxError"""
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            out += [(a.name, []) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append((n.module or "", [a.name for a in n.names]))
    return out


def closure(subject, tracked, read):
    """import closure of rule 4 -> (set of paths, None) or (None, reason)"""
    by_tail = {}
    for f in tracked:
        if f.endswith(".py"):
            parts = f[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                by_tail.setdefault("/".join(parts[i:]), set()).add(f)
    seen, todo = set(), [s for s in subject]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        if not f.endswith(".py"):
            continue
        try:
            mods = modules(read(f))
        except (SyntaxError, ValueError) as e:
            return None, "%s does not parse: %s" % (f, e)
        for mod, names in mods:
            parts = [p for p in mod.split(".") if p]
            tails = []
            for i in range(len(parts)):
                tails.append("/".join(parts[i:]))
            for nm in names:
                tails.append("/".join(parts + [nm]))
                tails.append(nm)
            for t in tails:
                todo += sorted(by_tail.get(t, ()))
    return seen, None


def root_of(path, roots):
    for r in roots:
        if any(path == t or path.startswith(t + "/") for t in gate_admit.trees(r)):
            return r
    return None


def anchor_ok(anchor, body):
    """a line range inside the file, or a heading line that occurs exactly once"""
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not isinstance(anchor, str):
        return False
    if anchor.startswith("L") and "-L" in anchor:
        a, b = anchor[1:].split("-L", 1)
        if a.isascii() and b.isascii() and a.isdigit() and b.isdigit() and str(int(a)) == a and str(int(b)) == b:
            return 1 <= int(a) <= int(b) <= len(lines)
        return False
    return anchor.startswith("#") and lines.count(anchor) == 1


def ext_reason(name, path, extpins, artifact):
    """-> reason or None: NAME is an external pinned by exactly one (commit, tree_sha256) in the recipes or the contract inputs, that
    commit fetches to that tree hash, and PATH (None: no path asked) is a file of it"""
    if not text(name):
        return "external name is not a non-empty string"
    pin = extpins.get(name)
    if not pin:
        return "external %r is not pinned by a recipe or the contract inputs" % name
    if len(pin) != 1:
        return "external %r is pinned to more than one commit or tree" % name
    (commit, tree_sha256), = pin
    tree, files = artifact(commit)
    if tree is None:
        return "external %r: commit %s cannot be fetched: %s" % (name, commit, files)
    if tree != tree_sha256:
        return "external %r: tree_sha256 of %s is %s, the pin says %s" % (name, commit, tree, tree_sha256)
    if path is not None and (not text(path) or path not in files):
        return "external %r: %r is not a file of %s" % (name, path, commit)
    return None


def judge_ref(ref, tracked, roots, pins, read, artifact, extpins=None):
    """-> reason or None (rule 3)"""
    if not isinstance(ref, dict):
        return "a reference is not an object"
    kind = ref.get("kind")
    keys = set(ref)
    if kind == "wine-exe":
        if keys != {"kind", "wine", "exe_sha256", "artifact"}:
            return "wine-exe: keys are not kind, wine, exe_sha256, artifact"
        if ref["wine"] != "9.0":
            return "wine-exe: wine is not 9.0"
        if not is_hex(pins.get("exe_sha256"), 64) or ref["exe_sha256"] != pins["exe_sha256"]:
            return "wine-exe: exe_sha256 is not the pinned exe"
        a = ref["artifact"]
        if not isinstance(a, dict) or set(a) != {"repo", "commit", "tree_sha256", "path"}:
            return "wine-exe: artifact keys are not repo, commit, tree_sha256, path"
        if a["repo"] != "fnv-nx-build" or not is_hex(a["commit"], 40) or not is_hex(a["tree_sha256"], 64) or not text(a["path"]):
            return "wine-exe: artifact is not fnv-nx-build, a 40 hex commit, a 64 hex tree_sha256 and a path"
        tree, files = artifact(a["commit"])
        if tree is None:
            return "wine-exe: commit %s cannot be fetched: %s" % (a["commit"], files)
        if tree != a["tree_sha256"]:
            return "wine-exe: tree_sha256 of %s is %s" % (a["commit"], tree)
        if a["path"] not in files:
            return "wine-exe: %s is not a file of %s" % (a["path"], a["commit"])
        return None
    if kind == "external":
        if keys != {"kind", "name", "path"}:
            return "external: keys are not kind, name, path"
        return ext_reason(ref["name"], ref["path"], extpins or {}, artifact)
    if kind == "probe":
        if keys != {"kind", "probe", "platform", "dump_sha256"}:
            return "probe: keys are not kind, probe, platform, dump_sha256"
        pr = ref["probe"]
        if not isinstance(pr, dict) or set(pr) != {"external", "path"}:
            return "probe: probe keys are not external, path"
        if not is_hex(ref["dump_sha256"], 64):
            return "probe: dump_sha256 is not 64 hex"
        return ext_reason(pr["external"], pr["path"], extpins or {}, artifact) or ext_reason(ref["platform"], None, extpins or {}, artifact)
    if kind == "spec":
        if keys != {"kind", "path", "anchor"}:
            return "spec: keys are not kind, path, anchor"
    elif kind in ("contract", "model"):
        if keys != {"kind", "path"}:
            return "%s: keys are not kind, path" % kind
    else:
        return "unknown reference kind %r" % (kind,)
    p = ref["path"]
    if not isinstance(p, str) or p not in tracked or root_of(p, roots) is None:
        return "%s: %r is not a tracked file inside a root" % (kind, p)
    if kind == "spec" and not anchor_ok(ref["anchor"], read(p)):
        return "spec: anchor %r is not a line range or a single heading line of %s" % (ref["anchor"], p)
    return None


def bindings_of_case(tree):
    """-> (names bound to the shared function, names bound to the shared module, reasons). Reasons: any other binding
    of the name `case` (a def, class, assignment, argument, loop or import target, any scope) is a local function
    named like the shared one"""
    fn, mod, why = set(), set(), []
    imports = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "testcase" and n.level == 0:
            for a in n.names:
                if a.name == "case":
                    fn.add(a.asname or "case")
                    imports.add(id(a))
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "testcase":
                    mod.add(a.asname or "testcase")
                    imports.add(id(a))
    own = fn | mod
    for n in ast.walk(tree):
        bound = []
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.append(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.append(n.id)
        elif isinstance(n, ast.arg):
            bound.append(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.append(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if id(a) not in imports:
                    bound.append((a.asname or a.name).split(".")[0])
        for b in bound:
            if b == "case" or b in own:
                why.append("line %d binds %r other than by the import of testcase" % (getattr(n, "lineno", 0), b))
    return fn, mod, why


def case_names(src):
    """The test cases of a test file (verify/testcase.py): -> (names in order, reason or None). A case is a module-level
    `def test_*`, a `test_*` method of a unittest.TestCase subclass (NAME or Class.NAME), or a call of the shared
    testcase.case, found through its import and never by the bare name; its first argument is a str constant."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError) as e:
        return [], "does not parse: %s" % e
    names = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"):
            names.append(n.name)
        elif isinstance(n, ast.ClassDef) and any(
                (b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", None)) == "TestCase" for b in n.bases):
            names += [n.name + "." + m.name for m in n.body if isinstance(m, ast.FunctionDef) and m.name.startswith("test_")]
    fn, mod, why = bindings_of_case(tree)
    if why:
        return names, why[0]
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Name) and n.func.id in fn) or
                (isinstance(n.func, ast.Attribute) and n.func.attr == "case" and isinstance(n.func.value, ast.Name)
                 and n.func.value.id in mod)):
            if not n.args or not (isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                return names, "line %d: a case name is not a string constant" % n.lineno
            names.append(n.args[0].value)
    dup = sorted(set(x for x in names if names.count(x) > 1))
    if dup:
        return names, "duplicate case name %r" % dup[0]
    if not names:
        return names, "no test case: neither a test_* function nor a testcase.case call"
    return names, None


def judge_entry(entry, names, tracked, roots, pins, read, artifact, extpins=None):
    """entry: {"subject": [...], "refs": {ID: REF}, "cases": {NAME: [ID...]}}; names: the derived cases -> (reason for the
    whole file or None, {case: reason}). Rules 2-6 hold per case: its own references, not its file's"""
    if not isinstance(entry, dict) or set(entry) != {"subject", "refs", "cases"}:
        return "entry keys are not subject, refs, cases", {}
    subject, refs, cases = entry["subject"], entry["refs"], entry["cases"]
    if not isinstance(subject, list) or not subject:
        return "subject is empty", {}
    for s in subject:
        if not isinstance(s, str) or s not in tracked or root_of(s, roots) is None:
            return "subject %r is not a tracked file inside a root" % (s,), {}
        if root_of(s, roots).get("class") == "ref":
            return "subject %r lies in a root of class ref" % (s,), {}
    if not isinstance(refs, dict) or not isinstance(cases, dict):
        return "refs and cases are not objects", {}
    extra = sorted(set(cases) - set(names))
    if extra:
        return "cases without a test case: %s" % ", ".join(extra), {}
    clo, why = closure(subject, tracked, read)
    if why:
        return why, {}
    product = any(root_of(s, roots).get("class") == "product" for s in subject)
    ref_bad = {}
    for i, ref in refs.items():
        w = judge_ref(ref, tracked, roots, pins, read, artifact, extpins)
        if not w and ref["kind"] in TREE_KINDS and ref["path"] in clo:
            w = "%s %s is in the import closure of the subject" % (ref["kind"], ref["path"])
        if not w and product and ref["kind"] in TREE_KINDS and root_of(ref["path"], roots).get("class") == "product":
            w = "reference %s of a product subject lies in a product root" % ref["path"]
        ref_bad[i] = w
    used = set()
    bad = {}
    for n in names:
        ids = cases.get(n)
        if ids is None:
            bad[n] = "no entry"
        elif not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            bad[n] = "references are empty"
        elif any(i not in refs for i in ids):
            bad[n] = "names a reference id that refs does not hold"
        else:
            used.update(ids)
            w = next((ref_bad[i] for i in ids if ref_bad[i]), None)
            if not w and product and not any(refs[i]["kind"] in ("wine-exe", "spec") for i in ids):
                w = "a product subject has no wine-exe or spec reference"
            if w:
                bad[n] = w
    if set(refs) - used:
        return "refs not used by any case: %s" % ", ".join(sorted(set(refs) - used)), {}
    return None, bad


def judge(files, decls, tracked, roots, pins, read, artifact, extpins=None):
    """files: the test files; decls: {DIR/tests: parsed oracle.json or None}; artifact(commit) -> (tree hash, set of file
    paths) or (None, reason) -> (universe, {item: reason} of the uncertified). An item is `FILE::CASE`; a file with no
    derivable case, or whose entry is wrong as a whole, is the item FILE; pure function of its arguments"""
    universe, bad = [], {}
    by_dir = {}
    for f in files:
        by_dir.setdefault(os.path.dirname(f), []).append(os.path.basename(f))
    for d, fnames in sorted(by_dir.items()):
        doc = decls.get(d)
        why = None
        if not isinstance(doc, dict) or set(doc) != {"format", "tests"} or doc["format"] != FORMAT \
                or not isinstance(doc["tests"], dict):
            why = "%s/oracle.json is missing or is not %s with keys format, tests" % (d, FORMAT)
        else:
            extra = sorted(set(doc["tests"]) - set(fnames))
            if extra:
                why = "%s/oracle.json has entries without a test file: %s" % (d, ", ".join(extra))
        for n in fnames:
            f = d + "/" + n
            names, w = case_names(read(f))
            if w:
                universe.append(f)
                bad[f] = w
                continue
            universe += [f + "::" + c for c in names]
            fw, cb = why, {}
            if fw is None and n not in doc["tests"]:
                fw = "no entry in %s/oracle.json" % d
            elif fw is None:
                fw, cb = judge_entry(doc["tests"][n], names, tracked, roots, pins, read, artifact, extpins)
            for c in names:
                if fw or c in cb:
                    bad[f + "::" + c] = fw or cb[c]
    return universe, bad


def pairs_unique(pairs):
    """json object_pairs_hook: a repeated key is an error (rule 1: exactly one entry)"""
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("key %r repeated" % k)
        d[k] = v
    return d


def load(path):
    try:
        with open(path) as fh:
            return json.load(fh, object_pairs_hook=pairs_unique)
    except (OSError, ValueError):
        return None


ARTIFACTS_URL = "https://github.com/toadharvard/fnv-nx-build"


def fetcher(cache):
    """artifact(commit) of judge: the commit of the artifacts repository as a tree hash and its file list.
    Bare fetches are kept in CACHE/<commit> (the layout recipe.py uses for externals); results are memoised per run."""
    url = os.environ.get("ARTIFACTS_URL", ARTIFACTS_URL)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    memo = {}

    def artifact(commit):
        if commit in memo:
            return memo[commit]
        bare = os.path.join(cache, commit)
        work = tempfile.mkdtemp(prefix="gate-oracle-")
        try:
            if not os.path.isdir(bare):
                tmp = "%s.tmp%d-%s" % (bare, os.getpid(), os.urandom(4).hex())
                os.makedirs(tmp)
                for cmd in (["init", "-q", "--bare"], ["fetch", "-q", "--depth", "1", url, commit]):
                    subprocess.run(["git", "-C", tmp] + cmd, env=env, check=True, capture_output=True)
                os.replace(tmp, bare)
            dest = os.path.join(work, "tree")
            os.makedirs(dest)
            ienv = dict(env, GIT_INDEX_FILE=os.path.join(work, "idx"))
            subprocess.run(["git", "--git-dir", bare, "read-tree", commit], env=ienv, check=True, capture_output=True)
            subprocess.run(["git", "--git-dir", bare, "--work-tree", dest, "checkout-index", "-a", "-f"], env=ienv, check=True,
                           capture_output=True)
            files = set()
            for d, _, names in os.walk(dest):
                for n in names:
                    f = os.path.join(d, n)
                    if os.path.isfile(f) and not os.path.islink(f):
                        files.add(os.path.relpath(f, dest))
            memo[commit] = (cas.tree_hash(dest), files)
        except (subprocess.CalledProcessError, OSError, cas.CasError) as x:
            err = (getattr(x, "stderr", b"") or str(x).encode()).decode(errors="replace").strip().splitlines()
            memo[commit] = (None, err[-1] if err else "git failed")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return memo[commit]
    return artifact


def external_pins(admit_roots):
    """{external name: set of (commit, tree_sha256)} of every pin the build knows: the externals of verify/contract_inputs.json and of the
    recipe of every product root (recipe.py's `external` entries)"""
    pins = {}

    def add(lst):
        for e in lst if isinstance(lst, list) else []:
            if isinstance(e, dict) and isinstance(e.get("name"), str) and is_hex(e.get("commit"), 40) and is_hex(e.get("tree_sha256"), 64):
                pins.setdefault(e["name"], set()).add((e["commit"], e["tree_sha256"]))
    add((load(TREE + "/verify/contract_inputs.json") or {}).get("external"))
    for r in admit_roots:
        if r.get("class") == "product" and isinstance(r.get("recipe"), str):
            add((load(r["recipe"]) or {}).get("external"))
    return pins


def main():
    out = os.environ["GATE_OUT"]
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    tracked = set(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)
    gates = gatesdir.load_dir_or_empty()
    argv = next((g["argv"] for g in gates.get("gates", []) if g.get("name") == "selftests"), None)
    errors = []
    files = []
    if argv is None:
        errors.append("verify/gates.d has no selftests gate: the universe is not defined")
    else:
        files = gate_tests.universe(gate_tests.parse(argv[argv.index(TREE + "/verify/gate_tests.py") + 1:])[1])
    admit = load(ADMIT) or {}
    roots = [r for r in admit.get("roots", []) if isinstance(r, dict) and isinstance(r.get("path"), str)]
    pins = load(PINS)
    if not isinstance(pins, dict):
        pins = {}
    decls = {d: load(d + "/oracle.json") for d in sorted(set(os.path.dirname(f) for f in files))}

    def read(p):
        with open(p, encoding="utf-8", errors="surrogateescape") as fh:
            return fh.read()
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-oracle-cache-")
    cache = os.path.join(os.environ.get("GATE_CACHE") or tmp, "external")
    os.makedirs(cache, exist_ok=True)
    try:
        universe, bad = judge(files, decls, tracked, roots, pins, read, fetcher(cache), external_pins(roots))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    n = len(universe) - len(bad)
    kinds = {}
    for d, doc in decls.items():
        for fn_, e in (doc or {}).get("tests", {}).items() if isinstance(doc, dict) and isinstance(doc.get("tests"), dict) else []:
            if isinstance(e, dict) and isinstance(e.get("refs"), dict):
                for ids in (e.get("cases") or {}).values() if isinstance(e.get("cases"), dict) else []:
                    for i in ids if isinstance(ids, list) else []:
                        r = e["refs"].get(i)
                        if isinstance(r, dict):
                            kinds[r.get("kind")] = kinds.get(r.get("kind"), 0) + 1
    status = "green" if not bad and not errors else "red"
    detail = "%d of %d test cases name a reference outside their subject" % (n, len(universe)) if universe else \
        "no test files at this commit (nothing to certify yet)"
    res = {"format": "fnvnx-gate/1", "name": "oracle", "status": status, "universe": len(universe), "certified": n,
           "uncertified": sorted(bad)[:50], "detail": "; ".join(errors + [detail]),
           "reasons": {f: bad[f] for f in sorted(bad)[:50]}, "references": kinds}
    with open(os.path.join(out, "oracle.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
