#!/usr/bin/env python3
"""Gate "hostapi" (fnvnx-gate/1): the host services declared in fnvnx/hostapi.h are exactly the services the product calls.
    python3 -I gate_hostapi.py [--header PATH]     cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/hostapi.json
Rule (owner order 2026-11-10/11: nothing that the product does not need, no placeholder behind a number): for every admitted product root
that includes hostapi.h, {service numbers declared in the header} == {service numbers expanded by at least one unit of the root}.
Declared: the header's own text (PATH, default ports/fnv-nx/hostabi/include/fnvnx/hostapi.h, its one home; a missing file is an error): every `#define FNVNX_PLAT_<NAME> <integer>` directive (a preprocessor directive read by its token grammar: #, define,
identifier, one pp-number). A service is a NUMBER; names with the same number are aliases of one service.
Used: the evidence is the PP stage result unit.ii of every unit of the root (found by pp_key in objects.json, as gate_objstub does). The PP
stage runs `-dU` (unitstage.PP_TAIL): GCC then writes `#define NAME VALUE` for exactly the macros that the unit EXPANDS (or tests with
defined/#ifdef); a name written in a comment, a string or an unexpanded #define is not there. A root includes hostapi.h iff some unit.ii has
a line marker (`# N "path"`) whose file name is hostapi.h. A used FNVNX_PLAT_ macro whose number is not declared, or whose value differs from
the header's, is a red item as well (the unit saw another header).
Universe: one item per declared service number of each root that includes hostapi.h, plus one per undeclared use. Certified: declared and
used. No product root, or none that includes hostapi.h: universe 0, green, no exception list (gate_build.quantify).
Stale names: a name of verify/hostapi_holes.json (a deleted slot, service or marker) in any tracked file of an admitted root (journal/ and JOURNAL.md, the history, excepted) is a red item (`NAME in PATH`).
Wrapper rule (class): a PREFIX identifier may appear only in the `#define PREFIX_X <integer>` lines of hostapi.h and in unit files (.c/.cc/.cpp/.S)
of the root; any other file that can be #included (non-unit files under the root, the recipe's include directories, external include trees) that names
one is a red item ("wrapper use"), and so is a non-define use in hostapi.h. So "expanded by a unit" means a call site of a unit and nothing a header
expands, and the -dU position of a define does not matter.
Limit (stated, not hidden): a use inside a function that no code reaches still counts; reachability is the job of the gates reach / reachlink.
The module ids (FNVNX_EXT_*) are not services and are not items: a module id names a host module, a service number names an entry of module 11,
which is the only table of the header. Models of machines below the product boundary (deko3d, libnx, kernel) are modules, not services."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402
import cas  # noqa: E402
import unitstage  # noqa: E402

PREFIX = "FNVNX_PLAT_"
HEADER = "hostapi.h"
HOSTAPI_HEADER = "ports/fnv-nx/hostabi/include/fnvnx/hostapi.h"   # the one home (root hostabi/): a missing file is an error


def number(tok):
    """A pp-number that is an integer constant: decimal/hex/octal digits with u/l suffixes -> int, else None."""
    t = tok.lower().rstrip("ul")
    if not t or not (t.isdigit() or (t.startswith("0x") and len(t) > 2 and all(c in "0123456789abcdef" for c in t[2:]))):
        return None
    return int(t, 16) if t.startswith("0x") else (int(t, 8) if t.startswith("0") and len(t) > 1 and t.isdigit() else int(t))


def defines(text):
    """-> [(name, value int)] of `# define NAME INTEGER` directives that start with PREFIX (a trailing comment is dropped)."""
    out = []
    for line in text.splitlines():
        t = line.strip()
        if not t.startswith("#"):
            continue
        toks = t[1:].split("/*")[0].split("//")[0].split()
        if len(toks) == 3 and toks[0] == "define" and toks[1].startswith(PREFIX) and number(toks[2]) is not None:
            out.append((toks[1], number(toks[2])))
    return out


def declared(header_text):
    """-> {service number: sorted names}"""
    d = {}
    for n, v in defines(header_text):
        d.setdefault(v, []).append(n)
    return {v: sorted(ns) for v, ns in d.items()}


def strip_comments(text):
    """text without /* */ and // comments and string literals (newlines kept)"""
    import re
    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"', lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def wrapper_uses(text):
    """-> [(line, name)] of PREFIX identifiers that occur outside a `#define PREFIX_X <integer>` directive. RULE (vacuity closed): the header
    may only DECLARE service numbers. A body in a header that expands a service macro (static inline wrapper, macro-defined function) would make
    the -dU evidence say "used" for every unit that merely includes the header, whether or not the unit calls it: such a use is red."""
    import re
    out = []
    for i, line in enumerate(strip_comments(text).splitlines(), 1):
        names = re.findall(r"\b(%s\w*)" % PREFIX, line)
        if names and not (len(defines(line)) == 1 and line.strip().startswith("#")):
            out += [(i, n) for n in names]
    return out


def foreign_uses(repo, rec, root, store):
    """-> [str] of files of an admitted root that may be #included and name a PREFIX service. RULE (class closed): a PREFIX identifier may appear
    only (a) in the `#define PREFIX_X <integer>` lines of hostapi.h and (b) in unit files (recipe.is_unit) of the root. Scanned: every tracked
    non-unit file under the root and under each repo include directory of the recipe, and every non-unit file of an external tree that
    holds an include directory of the recipe (cas listing of its tree_sha256 in the store). A header that expands a service macro in a body
    (shim, static inline wrapper) is red, so the -dU evidence is exactly the call sites of units."""
    bad, key = [], PREFIX.encode()
    dirs = [d for d in rec.get("include", [])]
    for f in recipe.tracked(repo):
        if recipe.is_unit(f) or not (recipe.under(f, root) or any(recipe.under(f, d) for d in dirs)):
            continue
        t = open(os.path.join(repo, f), "rb").read()
        if key in t and (os.path.basename(f) != HEADER or wrapper_uses(t.decode("utf-8", "replace"))):
            bad.append("wrapper use in %s" % f)
    for e in rec.get("external", []):
        if not any(recipe.under(d, e["at"]) for d in dirs):
            continue
        for rel, ent in cas.listing(e["tree_sha256"], store):
            if ent["t"] == "f" and not recipe.is_unit(rel):
                t = open(cas._obj(store, ent), "rb").read()
                if key in t and (os.path.basename(rel) != HEADER or wrapper_uses(t.decode("utf-8", "replace"))):
                    bad.append("wrapper use in %s:%s" % (e["name"], rel))
    return sorted(set(bad))


HOLES = "ports/fnv-nx/verify/hostapi_holes.json"
HISTORY = ("ports/fnv-nx/journal", "ports/fnv-nx/JOURNAL.md", HOLES)   # records of what happened, and the registry itself


def stale_names(repo):
    """-> [str] "NAME in PATH" for every name of HOLES (deleted slots/services/markers) that occurs in a tracked file of an admitted root
    outside HISTORY. RULE: a deleted name is gone from the tree, so no stale name survives its slot; the names are matched case-insensitively
    where the neighbours are not letters or digits (so FNVNX_PLAT_NAME and h_name match)."""
    import re
    names = json.load(open(os.path.join(repo, HOLES)))["names"]
    rx = re.compile(rb"(?<![A-Za-z0-9])(%s)(?![A-Za-z0-9])" % b"|".join(re.escape(n.encode()) for n in names), re.I)
    admitted = [r["path"] for r in json.load(open(os.path.join(repo, recipe.ADMIT)))["roots"]]
    out = set()
    for f in recipe.tracked(repo):
        if any(recipe.under(f, h) for h in HISTORY) or not any(recipe.under(f, a) for a in admitted):
            continue
        try:
            t = open(os.path.join(repo, f), "rb").read()
        except OSError:
            continue
        out |= {"%s in %s" % (m.group(1).decode(), f) for m in rx.finditer(t)}
    return sorted(out)


def includes_header(ii_text):
    """True iff the preprocessed text has a line marker for a file named hostapi.h"""
    for line in ii_text.splitlines():
        if line.startswith("# ") and line.count('"') >= 2 and os.path.basename(line.split('"')[1]) == HEADER:
            return True
    return False


def used(ii_texts):
    """-> {name: value} of the PREFIX macros that the units expanded (-dU output)"""
    u = {}
    for t in ii_texts:
        u.update(defines(t))
    return u


def judge(decl, use):
    """-> (universe, certified, ids [str]). DECL {number: [names]}, USE {name: value} (both of one root)."""
    dnames = {n: v for v, ns in decl.items() for n in ns}
    used_nums = {v for n, v in use.items() if dnames.get(n) == v}
    bad = ["undeclared %s=%d" % (n, v) for n, v in sorted(use.items()) if dnames.get(n) != v]
    items = ["service %d (%s)" % (v, "/".join(decl[v])) for v in sorted(decl)]
    miss = ["unused service %d (%s)" % (v, "/".join(decl[v])) for v in sorted(decl) if v not in used_nums]
    return len(items) + len(bad), len(items) - len(miss), miss + bad


def header_text(repo, explicit):
    return open(os.path.join(repo, explicit or HOSTAPI_HEADER)).read()


def root_judge(root, repo, cache, header):
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return 1, 0, [root + ":recipe-invalid"], "; ".join(e.errors)[:200]
    if res["status"] == "red":
        return 1, 0, [root + ":build-red"], "stage %s" % res["failure"]["stage"]
    store = os.path.join(cache, "store")
    iis = [open(unitstage.result_file(store, k, "unit.ii"), errors="replace").read() for _, k in sorted(res["pp_keys"].items())]
    if not any(includes_header(t) for t in iis):
        return 0, 0, [], "does not include %s" % HEADER
    u, c, ids = judge(declared(header), used(iis))
    w = ["wrapper use %s line %d" % (n, l) for l, n in wrapper_uses(header)]
    w += foreign_uses(repo, recipe.load_recipe(repo, recipe.load_admit(repo), root), root, store)
    u, ids = u + len(w), ids + w
    return u, c, [root + ":" + i for i in ids], "; ".join(ids)[:300]


def main(argv):
    explicit = argv[argv.index("--header") + 1] if "--header" in argv else None
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-hostapi-")
    cache, repo = os.environ.get("GATE_CACHE") or tmp, "."
    try:
        roots = gate_build.product_roots(repo)
        header = header_text(repo, explicit) if roots else ""
        u, c, ids, detail = gate_build.quantify(repo, lambda r: root_judge(r, repo, cache, header))
        st = stale_names(repo) if os.path.isfile(os.path.join(repo, HOLES)) else []
        u, ids = u + len(st), st + ids
        status = "green" if u == c else "red"
    except (OSError, ValueError, KeyError) as e:
        u, c, ids, detail, status = 1, 0, ["input"], "input error: %s" % (str(e)[:300] or type(e).__name__), "red"
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    res = {"format": "fnvnx-gate/1", "name": "hostapi", "status": status, "universe": u, "certified": c, "uncertified": ids[:50], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "hostapi.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("hostapi: %s %d/%d %s" % (status, c, u, detail[:200]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
