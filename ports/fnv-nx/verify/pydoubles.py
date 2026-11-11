#!/usr/bin/env python3
"""pydoubles: the rule "no doubles in tests" (CLAUDE.md, owner order of 2026-11-11 night) for python tests, a pure function over source texts
(ast only; no test is imported or run). Called by gate_objstub.py: part of the stub gate.
A python test is a .py file below a directory named `tests` of the tree. A project module is a module whose name is the stem of a tracked .py file
(or a directory of one), imported by a plain `import`, `from pkg import mod`, or bound from importlib.import_module / __import__ / module_from_spec /
imp.load_* (such a variable is a module alias), also through an alias assignment `a = b`. A test is red when it does any of:
  - assigns, augment-assigns, annotates or deletes an attribute of a project module alias (`mod.f = ...`, `mod.a, mod.b = ...`, `del mod.f`);
  - calls setattr or delattr on a project module alias, or writes `mod.__dict__[k]` / `vars(mod)[k]`;
  - writes sys.modules (subscript assignment or deletion, setdefault, pop, update, clear);
  - imports unittest.mock or mock, or names monkeypatch (a pytest fixture or object).
Each (module, attribute) or (sys.modules, key) is a replaced name. Argument: a module attribute of the product that a test rebinds is a double of
that attribute (the test then runs against something other than the real file), and the only ways python gives to rebind one from outside are the
syntax forms above, the setattr family, the module dict, sys.modules, and the mock/monkeypatch libraries, which are those forms. The test passes real
inputs to the real function instead. Aliasing beyond a plain `a = b` between module-valued names (for example through containers or return
values of project functions) is not followed: assigning an attribute of an object that a project function returns is not an attribute of a project module.
Depends only on the accepted OWNER item 1 (the compiler is correct): python's ast is the grammar."""
import ast
import os

MODULE_MAKERS = {"import_module", "__import__", "module_from_spec", "load_source", "load_module", "load_compiled", "load_dynamic"}


def is_test_path(path):
    parts = path.split("/")
    return path.endswith(".py") and "tests" in parts[:-1]


def project_modules(tracked):
    """names a project module can be imported by: stem of every tracked .py file and every directory that holds one"""
    out = set()
    for p in tracked:
        if p.endswith(".py"):
            parts = p.split("/")
            out.add(parts[-1][:-3])
            out.update(parts[:-1])
    return out


def _root(e):
    while isinstance(e, ast.Attribute):
        e = e.value
    return e.id if isinstance(e, ast.Name) else None


def analyse(src, proj):
    """-> set of replaced names [(module, attribute)] of one python source; raises SyntaxError"""
    t = ast.parse(src)
    mods, mockish, found = {}, False, set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "mock" or a.name.startswith("unittest.mock"):
                    mockish = True
                    found.add(("mock", a.name))
                elif a.name.split(".")[0] in proj or a.name.split(".")[-1] in proj:
                    mods[a.asname or a.name.split(".")[0]] = a.name.split(".")[-1] if a.asname else a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom):
            m = n.module or ""
            if m == "mock" or m.startswith("unittest.mock") or (m == "unittest" and any(a.name == "mock" for a in n.names)):
                found.add(("mock", m or "mock"))
            elif m.split(".")[0] in proj or n.level:
                for a in n.names:
                    if a.name in proj:
                        mods[a.asname or a.name] = a.name
    for n in ast.walk(t):   # module-valued variables: importlib results and plain aliases (to a fixpoint)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            f = n.value.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)) in MODULE_MAKERS:
                for x in n.targets:
                    if isinstance(x, ast.Name):
                        mods[x.id] = "<%s>" % x.id
    for _ in range(4):
        for n in ast.walk(t):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in mods:
                for x in n.targets:
                    if isinstance(x, ast.Name):
                        mods.setdefault(x.id, mods[n.value.id])

    def target(e):
        if isinstance(e, ast.Attribute):
            r = _root(e.value)
            if r in mods:
                found.add((mods[r], ast.unparse(e)[len(r) + 1:]))
        elif isinstance(e, ast.Subscript):
            v = e.value
            if isinstance(v, ast.Attribute) and v.attr == "modules" and _root(v) == "sys":
                found.add(("sys.modules", ast.unparse(e.slice)))
            elif isinstance(v, ast.Attribute) and v.attr == "__dict__" and isinstance(v.value, ast.Name) and v.value.id in mods:
                found.add((mods[v.value.id], "__dict__[%s]" % ast.unparse(e.slice)))
            elif isinstance(v, ast.Call) and getattr(v.func, "id", None) == "vars" and v.args and isinstance(v.args[0], ast.Name) and v.args[0].id in mods:
                found.add((mods[v.args[0].id], "vars[%s]" % ast.unparse(e.slice)))
        elif isinstance(e, (ast.Tuple, ast.List)):
            for x in e.elts:
                target(x)
        elif isinstance(e, ast.Starred):
            target(e.value)
    for n in ast.walk(t):
        if isinstance(n, ast.Assign):
            for e in n.targets:
                target(e)
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            target(n.target)
        elif isinstance(n, ast.Delete):
            for e in n.targets:
                target(e)
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            target(n.target)
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            target(n.optional_vars)
        elif isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in ("setattr", "delattr") and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id in mods:
                found.add((mods[n.args[0].id], ast.unparse(n.args[1]) if len(n.args) > 1 else "?"))
            if isinstance(f, ast.Attribute) and f.attr in ("setdefault", "pop", "update", "clear") and isinstance(f.value, ast.Attribute) \
                    and f.value.attr == "modules" and _root(f.value) == "sys":
                found.add(("sys.modules", f.attr))
        elif isinstance(n, ast.Name) and n.id == "monkeypatch":
            found.add(("monkeypatch", "monkeypatch"))
        elif isinstance(n, ast.arg) and n.arg == "monkeypatch":
            found.add(("monkeypatch", "monkeypatch"))
    return found


def judge(sources, tracked):
    """SOURCES {path: text} of the python test files, TRACKED the list of tracked paths of the tree
    -> (items [(path, "pytest")], findings [(path, "pytest", "py-double"|"py-unparsable", detail)], stats {py_tests_total, py_tests_with_doubles, py_double_names})"""
    proj = project_modules(tracked)
    items, finds, with_d, names = [], [], 0, 0
    for p in sorted(sources):
        items.append((p, "pytest"))
        try:
            f = analyse(sources[p], proj)
        except (SyntaxError, ValueError) as e:
            finds.append((p, "pytest", "py-unparsable", str(e)[:100]))
            continue
        if f:
            with_d += 1
            names += len(f)
            finds.append((p, "pytest", "py-double", "replaces " + ", ".join("%s.%s" % x if x[0] not in ("mock", "monkeypatch") else x[0] for x in sorted(f))))
    return items, finds, {"py_tests_total": len(sources), "py_tests_with_doubles": with_d, "py_double_names": names}
