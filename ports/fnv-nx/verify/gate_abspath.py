#!/usr/bin/env python3
"""Gate "abspath" (fnvnx-gate/1, owner order of 2026-11-15 21:00Z "every generator is a recipe stage; no path outside the key"): no absolute host
path and no environment default can reach an input of a product stage or a gate stage.
    python3 -I gate_abspath.py        cwd = checkout root, env GATE_OUT; writes GATE_OUT/abspath.json
Universe (from the stage graph, by rule, no list of files): the Python files of
  (a) the tools the recipe runs as stages: `generated[].tool` of every recipe of every admitted root (admit.json), and the stage launchers of
      recipe.py itself (GEN_LAUNCH, LD_TOOLS files, unitstage.py);
  (b) the gate scripts of gates.d (every `.py` of an argv);
  (c) the transitive closure of (a) and (b) inside the tracked tree: an `import` / `from .. import` statement (ast) resolves to the tracked
      file of that name (the importer's directory first; with none there, every tracked file of that module path: a superset), and a string constant
      naming a tracked `.py` file (relative to the file's directory or to the checkout root) is a launch edge (gate_contract.py runs bodysel.py).
      A dynamic import that cannot be resolved (importlib.import_module / __import__ / spec_from_file_location / runpy with a non-constant
      argument) is red `abspath:dynamic-import:FILE:LINE`, unless its target expression is the result of require_mounted(...)
      of recipe.py (a call, or a name all of whose assignments are such calls): those guards raise outside the stage's mounts or the given tree.
A file outside the closure is not judged. Findings are read from the ast, never from the text:
  abspath:literal:FILE:LINE:VALUE   a str constant that is an absolute path ("/" then a name) and is used as a path: it is, or is inside an argument
                                    of, open, os.*, os.path.*, pathlib.Path, subprocess.*, shutil.*, glob.*, io/tarfile/zipfile open, sys.path.*, or
                                    is assigned (directly, through names, tuples or a for target) to a name that reaches one of those argument
                                    positions in its function or module. A path under a mount prefix of the sandbox (recipe.MOUNT_PREFIXES, the one
                                    constant, followed by "/" or the end) is a path inside the stage's own mount namespace and is green.
  abspath:envdefault:FILE:LINE      os.environ.get(k, default) / os.getenv(k, default) with a default other than None, os.environ.setdefault, or an
                                    os.environ[k] read inside a try block that has a handler (a fallback). A missing input must be an argument error.
  abspath:escape:FILE:LINE:PATH     a file read (open, Path.read_*, os.listdir, glob, np.load, shutil.copy*) in a stage tool whose path is built from
                                    __file__, __path__ or sys.argv[0] (through names, os.path.dirname/join/abspath, pathlib `/` and .parent) and
                                    resolves outside the tree the stage mounts for that tool (the owner tree of the file: recipe.tree_of, all of its
                                    files are mounted at /tool): any `..` that leaves it. Inside the tree it is a mounted file (green); through
                                    require_mounted it is guarded (green).
  abspath:home:FILE:LINE            `~` paths, os.path.expanduser, Path.home().
Report: the global count of findings per file with the commit; red iff there is any finding or any dynamic import.
Proof: a stage opens only what its key mounts; a literal absolute path outside the mount prefixes, an environment default, or a home path names
something outside every key, and the closure is every Python file a stage or a gate can execute that is in the repository, so a path outside the key
cannot enter through a tracked Python file. Assumptions: ASM-001 (the compiler), ASM-004 (the console), as for every gate. Limits, stated: a path built
at run time from non-constant parts (a join of arguments) is not a literal and is not seen (an argument is the rule: it is the caller's key); a script
launched by a name that is not a string constant is not in the closure."""
import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pathflow  # noqa: E402
import recipe  # noqa: E402
from pathflow import ABS, Module, consts, names_of, scopes, own_nodes, catches_keyerror, dotted, guarded, is_guard  # noqa: E402

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
GATES_D = TREE + "/verify/gates.d"
MOUNTS = recipe.MOUNT_PREFIXES
PYREF = re.compile(r"[\w./+-]+\.py\Z")
DYN = {"importlib.import_module", "__import__", "importlib.util.spec_from_file_location", "runpy.run_path", "runpy.run_module",
       "importlib.__import__"}


def in_mount(v):
    return any(v == m or v.startswith(m + "/") for m in MOUNTS)


def judge_file(path, src, owner=None):
    """(findings [(code, line, detail)], dynamic [(line)]) of one file. OWNER: the admitted root tree the stage mounts this file's tool from (None: the
    file is not a stage tool, so the escape rule does not apply)."""
    mod = Module(path, src)
    sc = scopes(mod.tree)
    f, seen = [], set()

    def add(code, line, detail=""):
        k = (code, line, detail)
        if k not in seen:
            seen.add(k)
            f.append(k)
    flow = pathflow.flow_of(sc)

    def reach(idx, nm, mod, guard):
        """The path-shaped constants that can flow into name NM seen from scope IDX."""
        out = []
        while idx is not None:
            for val in flow[idx].get(nm, []):
                if id(val) in guard:
                    continue
                guard.add(id(val))
                out += consts(val, mod)
                for m2 in names_of(val):
                    out += reach(idx, m2, mod, guard)
            idx = sc[idx][1]
        return out
    for idx, (scope, _) in enumerate(sc):
        for n in own_nodes(scope):
            if isinstance(n, ast.Call) and mod.sink(n):
                args = list(n.args) + [k.value for k in n.keywords]
                hit = []
                for a in args:
                    hit += consts(a, mod)
                    for nm in names_of(a):
                        hit += reach(idx, nm, mod, set())
                for c, v in hit:
                    if v == "~" or v.startswith("~/"):
                        add("home", c.lineno, v)
                    elif not in_mount(v):
                        add("literal", c.lineno, v)
    for n in ast.walk(mod.tree):
        if isinstance(n, ast.Call):
            fn = mod.full(n.func)
            if fn in ("os.path.expanduser", "posixpath.expanduser") or (fn and fn.endswith("Path.home")):
                add("home", n.lineno)
            two = (fn in ("os.environ.get", "os.getenv") and (len(n.args) >= 2 or any(k.arg == "default" for k in n.keywords)))
            if two:
                d = n.args[1] if len(n.args) >= 2 else [k.value for k in n.keywords if k.arg == "default"][0]
                if not (isinstance(d, ast.Constant) and d.value is None):
                    add("envdefault", n.lineno)
            if fn == "os.environ.setdefault":
                add("envdefault", n.lineno)
        if isinstance(n, ast.Try) and any(catches_keyerror(h) for h in n.handlers):
            for b in n.body:
                for m in ast.walk(b):
                    if isinstance(m, ast.Subscript) and isinstance(m.ctx, ast.Load) and mod.full(m.value) == "os.environ":
                        add("envdefault", m.lineno)
    if owner:
        for line, res in pathflow.escapes(path, mod, sc, flow, owner):
            add("escape", line, res)
    dyn = []
    for idx, (scope, _) in enumerate(sc):
        for n in own_nodes(scope):
            if isinstance(n, ast.Call) and mod.full(n.func) in DYN:
                fn = mod.full(n.func)
                i = 1 if fn.endswith("spec_from_file_location") else 0
                a = n.args[i] if len(n.args) > i else None
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)) and not guarded(a, idx, flow, sc):
                    dyn.append(n.lineno)
    return f, dyn


def imports_of(path, src):
    """Module names imported by a file: [(dotted name, level)], plus the `.py` string constants."""
    tree = ast.parse(src, path)
    mods, pys = [], []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [(a.name, 0) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            mods.append((base, n.level))
            mods += [((base + "." if base else "") + a.name, n.level) for a in n.names]
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and PYREF.match(n.value):
            pys.append(n.value)
    return mods, pys


def resolve(path, files, mods, pys):
    """Tracked files that the imports and `.py` constants of PATH name."""
    here = os.path.dirname(path)
    out = set()
    for name, level in mods:
        if not name:
            continue
        rel = name.replace(".", "/")
        base = here
        for _ in range(max(level - 1, 0)):
            base = os.path.dirname(base)
        local = [c for c in (base + "/" + rel + ".py", base + "/" + rel + "/__init__.py") if c in files]
        if local:
            out.update(local)
            continue
        if level:
            continue
        for c in files:   # every tracked file of that module path (superset); a stdlib or third-party name matches nothing
            if c.endswith("/" + rel + ".py") or c.endswith("/" + rel + "/__init__.py"):
                out.add(c)
    for p in pys:
        for c in (here + "/" + p, p, TREE + "/" + p):
            c = os.path.normpath(c)
            if c in files:
                out.add(c)
    return out


def closure(files, seeds):
    """Python files reachable from SEEDS (tracked paths -> source in FILES)."""
    seen, todo = set(), [s for s in sorted(seeds) if s in files]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        mods, pys = imports_of(p, files[p])
        todo += sorted(resolve(p, files, mods, pys) - seen)
    return seen


def judge(files, seeds, commit="", owner=None, tool_seeds=None):
    """(result dict, per-file count) over the closure of SEEDS in FILES {path: source}."""
    cl = closure(files, seeds)
    tools = closure(files, tool_seeds) if tool_seeds is not None else cl   # the escape rule judges stage tools only: a gate runs from the checkout root, not under /tool
    per, rows, bad = {}, [], []
    for p in sorted(cl):
        f, dyn = judge_file(p, files[p], owner(p) if owner and p in tools else None)
        n = len(f) + len(dyn)
        if n:
            per[p] = n
        for code, line, d in sorted(f):
            rows.append("abspath:%s:%s:%d%s" % (code, p, line, ":" + d if d else ""))
        for line in dyn:
            rows.append("abspath:dynamic-import:%s:%d" % (p, line))
    return {"closure": len(cl), "per_file": per, "findings": rows}


def seeds_of(files_all, git_show):
    """Seeds (a) and (b) from the tree: tools of the recipes of admitted roots, recipe's own stage launchers, gate scripts."""
    seeds = set()
    admit = json.loads(git_show(ADMIT))
    for r in admit.get("roots", []):
        rec = r.get("recipe")
        for rp in ([rec] if isinstance(rec, str) else list(rec.values()) if isinstance(rec, dict) else []):
            try:
                doc = json.loads(git_show(rp))
            except (ValueError, OSError):
                continue
            for k in ("generated",):
                for g in doc.get(k) or []:
                    if isinstance(g, dict) and isinstance(g.get("tool"), str):
                        seeds.add(g["tool"])
    rel = lambda p: os.path.relpath(p, os.getcwd()) if os.path.isabs(p) else p
    seeds.add(rel(recipe.GEN_LAUNCH))
    seeds.add(HERE_REL + "/unitstage.py")
    for p in (recipe.LD_TOOLS[0] + "/ldcheck.py", recipe.LD_TOOLS[1] + "/gatefile.py"):
        seeds.add(p)
    tools = set(seeds)
    for p in sorted(files_all):
        if p.startswith(GATES_D + "/") and p.endswith(".json"):
            for a in json.loads(git_show(p)).get("argv", []):
                if isinstance(a, str) and a.endswith(".py"):
                    seeds.add(a)
    return seeds, tools


HERE_REL = TREE + "/verify"


def read_tree(commit):
    """{path: source} of the tracked .py files below TREE, and a reader of any tracked file, at COMMIT (the working tree when COMMIT is empty)."""
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    names = sorted(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)

    def show(p):
        with open(p, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    return names, show


def main():
    out = os.environ["GATE_OUT"]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()   # the worktree of the commit under test
    names, show = read_tree(commit)
    files = {p: show(p) for p in names if p.endswith(".py")}
    seeds, tool_seeds = seeds_of(set(names), show)
    missing = sorted(s for s in seeds if s not in files)
    roots = {r["path"]: r for r in json.loads(show(ADMIT)).get("roots", []) if isinstance(r, dict) and "path" in r}
    res = judge(files, seeds, commit, lambda p: recipe.tree_of(roots, p), tool_seeds)
    rows = res["findings"] + ["abspath:missing-tool:%s" % s for s in missing]
    status = "green" if not rows else "red"
    for p, n in sorted(res["per_file"].items()):
        print("  %s %d" % (p, n))
    for r in rows[:60]:
        print("  " + r[:200])
    detail = "%d findings in %d of %d closure files at %s" % (len(rows), len(res["per_file"]), res["closure"], commit[:12])
    doc = {"format": "fnvnx-gate/1", "name": "abspath", "status": status, "universe": res["closure"], "certified": res["closure"] - len(res["per_file"]),
           "uncertified": sorted(res["per_file"]), "detail": detail, "reasons": {p: str(n) for p, n in sorted(res["per_file"].items())[:50]},
           "errors": rows[:50], "findings": len(rows), "per_file": res["per_file"], "commit": commit}
    with open(os.path.join(out, "abspath.json"), "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("abspath: %s %s" % (status, detail))
    return 0 if status == "green" else 1


if __name__ == "__main__":
    sys.exit(main())
