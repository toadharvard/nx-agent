#!/usr/bin/env python3
"""Gate "oracle" (fnvnx-gate/1): every test names where its expected values come from, and that source is not the
code under test (verify/VERDICT.md class 10).
    python3 -I gate_oracle.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/oracle.json

Universe: the test files of gate "selftests" (the DIRs of its argv in verify/gates.json, DIR/tests/test_*.py, found by
gate_tests.universe: one list for both gates). An item is a repo-relative path.
Declaration: DIR/tests/oracle.json ("fnvnx-oracle/1"):
  {"format": "fnvnx-oracle/1", "tests": {"test_x.py": {"subject": [PATH...], "references": [REF...]}}}
  subject     the files under test (repo-relative).
  REF         one of (exactly these keys):
    {"kind": "wine-exe", "wine": "9.0", "exe_sha256": H64, "artifact": {"repo": "fnv-nx-build", "commit": H40,
     "path": STR, "sha256": H64}}      a dump the game's exe produced under Wine 9.0, stored in the artifacts repository
    {"kind": "spec", "document": STR, "section": STR, "path": PATH}    cited text held in a tracked file
    {"kind": "spec", "document": STR, "section": STR, "url": STR}      cited text of an outside document
    {"kind": "contract", "path": PATH}     a contract table of the tree
    {"kind": "model", "path": PATH}        an independent model in the tree
Rule. A test file is certified iff ALL hold:
  1. its DIR's oracle.json is well formed and holds exactly one entry for it (an entry without a test file, an unknown
     key or a wrong format is an error of the DIR: none of its tests is certified);
  2. `subject` is not empty and every subject path is a tracked file inside a root of verify/admit.json;
  3. `references` is not empty and every REF has one of the forms above; `wine` is "9.0"; `exe_sha256` equals
     verify/oracle_pins.json "exe_sha256"; every `path` of a REF is a tracked file inside a root; for a spec with
     `path`, `section` occurs literally in that file (the citation exists); `document`, `section`, `url` are not empty;
  4. independence: no contract or model `path` is in the import closure of the subject. The closure is computed from the
     syntax trees: a subject file, plus, for every `import a.b` / `from a.b import c` in a closure file, every tracked
     file of the tree named a/b.py, b.py, a/b/__init__.py, a/b/c.py or c.py at any depth (a superset of what python
     can load, whatever sys.path is; a closure file that does not parse makes the test uncertified);
  5. product: when a subject file lies in a product root, some REF is wine-exe or spec, and no REF `path` lies in a
     product root.
A spec whose `path` is a subject file itself is allowed (rule 4 covers contract and model only): a tool's own stated
rule is the reference of the tool's test; for a product subject rule 5 forbids it.
Argument: an expected value "comes from the code under test" when its declared source is the subject or is computed
through it. Rules 2-5 leave four sources: a dump of the exe under the pinned Wine, text cited from a document, a
contract table and a model, the last two outside everything the subject can load, and for product code never the
product tree. The verdict is green only if certified == universe, so a test without such a source blocks the merge.
Assumptions (checked by verification, not by this gate): the declaration is true - the subject list is complete and
the test compares against the declared references (the after-merge verifier runs the tests under verify/opentrace.py
and compares the tracked files opened with subject + references); a wine-exe artifact exists at its commit with its
sha256 (fetched and checked where it is used); the text of a `url` spec says what the test expects."""
import ast
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_tests  # noqa: E402

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
GATES = TREE + "/verify/gates.json"
PINS = TREE + "/verify/oracle_pins.json"
FORMAT = "fnvnx-oracle/1"
HEX = set("0123456789abcdef")


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
        if path == r["path"] or path.startswith(r["path"] + "/"):
            return r
    return None


def judge_ref(ref, tracked, roots, pins, read):
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
        if not isinstance(a, dict) or set(a) != {"repo", "commit", "path", "sha256"}:
            return "wine-exe: artifact keys are not repo, commit, path, sha256"
        if a["repo"] != "fnv-nx-build" or not is_hex(a["commit"], 40) or not is_hex(a["sha256"], 64) or not text(a["path"]):
            return "wine-exe: artifact is not fnv-nx-build, a 40 hex commit, a path and a 64 hex sha256"
        return None
    if kind == "spec":
        if keys not in ({"kind", "document", "section", "path"}, {"kind", "document", "section", "url"}):
            return "spec: keys are not kind, document, section and one of path, url"
        if not text(ref["document"]) or not text(ref["section"]):
            return "spec: document or section is empty"
        if "url" in ref:
            return None if text(ref["url"]) else "spec: url is empty"
    elif kind in ("contract", "model"):
        if keys != {"kind", "path"}:
            return "%s: keys are not kind, path" % kind
    else:
        return "unknown reference kind %r" % (kind,)
    p = ref["path"]
    if not isinstance(p, str) or p not in tracked or root_of(p, roots) is None:
        return "%s: %r is not a tracked file inside a root" % (kind, p)
    if kind == "spec" and ref["section"] not in read(p):
        return "spec: section %r does not occur in %s" % (ref["section"], p)
    return None


def judge_test(entry, tracked, roots, pins, read):
    """-> reason or None (rules 2-5)"""
    if not isinstance(entry, dict) or set(entry) != {"subject", "references"}:
        return "entry keys are not subject, references"
    subject, refs = entry["subject"], entry["references"]
    if not isinstance(subject, list) or not subject:
        return "subject is empty"
    for s in subject:
        if not isinstance(s, str) or s not in tracked or root_of(s, roots) is None:
            return "subject %r is not a tracked file inside a root" % (s,)
    if not isinstance(refs, list) or not refs:
        return "references is empty"
    for ref in refs:
        why = judge_ref(ref, tracked, roots, pins, read)
        if why:
            return why
    clo, why = closure(subject, tracked, read)
    if why:
        return why
    for ref in refs:
        if ref["kind"] in ("contract", "model") and ref["path"] in clo:
            return "%s %s is in the import closure of the subject" % (ref["kind"], ref["path"])
    if any(root_of(s, roots)["class"] == "product" for s in subject):
        if not any(ref["kind"] in ("wine-exe", "spec") for ref in refs):
            return "a product subject has no wine-exe or spec reference"
        for ref in refs:
            if "path" in ref and root_of(ref["path"], roots)["class"] == "product":
                return "reference %s of a product subject lies in a product root" % ref["path"]
    return None


def judge(files, decls, tracked, roots, pins, read):
    """files: the universe; decls: {DIR/tests: parsed oracle.json or None} -> {path: reason} of the uncertified;
    pure function of its arguments"""
    bad = {}
    by_dir = {}
    for f in files:
        by_dir.setdefault(os.path.dirname(f), []).append(os.path.basename(f))
    for d, names in sorted(by_dir.items()):
        doc = decls.get(d)
        why = None
        if not isinstance(doc, dict) or set(doc) != {"format", "tests"} or doc["format"] != FORMAT \
                or not isinstance(doc["tests"], dict):
            why = "%s/oracle.json is missing or is not %s with keys format, tests" % (d, FORMAT)
        else:
            extra = sorted(set(doc["tests"]) - set(names))
            if extra:
                why = "%s/oracle.json has entries without a test file: %s" % (d, ", ".join(extra))
        for n in names:
            f = d + "/" + n
            if why:
                bad[f] = why
            elif n not in doc["tests"]:
                bad[f] = "no entry in %s/oracle.json" % d
            else:
                w = judge_test(doc["tests"][n], tracked, roots, pins, read)
                if w:
                    bad[f] = w
    return bad


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


def main():
    out = os.environ["GATE_OUT"]
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    tracked = set(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)
    gates = load(GATES) or {}
    argv = next((g["argv"] for g in gates.get("gates", []) if g.get("name") == "selftests"), None)
    errors = []
    files = []
    if argv is None:
        errors.append("gates.json has no selftests gate: the universe is not defined")
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
    bad = judge(files, decls, tracked, roots, pins, read)
    kinds = {}
    for d, doc in decls.items():
        for n, e in (doc or {}).get("tests", {}).items() if isinstance(doc, dict) and isinstance(doc.get("tests"), dict) else []:
            if d + "/" + n not in bad:
                for ref in e["references"]:
                    kinds[ref["kind"]] = kinds.get(ref["kind"], 0) + 1
    n = len(files) - len(bad)
    status = "green" if not bad and not errors else "red"
    detail = "%d of %d test files name a reference outside their subject" % (n, len(files)) if files else \
        "no test files at this commit (nothing to certify yet)"
    res = {"format": "fnvnx-gate/1", "name": "oracle", "status": status, "universe": len(files), "certified": n,
           "uncertified": sorted(bad)[:50], "detail": "; ".join(errors + [detail]),
           "reasons": {f: bad[f] for f in sorted(bad)[:50]}, "references": kinds}
    with open(os.path.join(out, "oracle.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
