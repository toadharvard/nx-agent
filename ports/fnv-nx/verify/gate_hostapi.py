#!/usr/bin/env python3
"""Gate "hostapi" (fnvnx-gate/1): the host services declared in fnvnx/hostapi.h are exactly the services the product calls.
    python3 -I gate_hostapi.py [--header PATH]     cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/hostapi.json
Rule (owner order 2026-11-10/11: nothing that the product does not need, no placeholder behind a number): for every admitted product root
that includes hostapi.h, {service numbers declared in the header} == {service numbers expanded by at least one unit of the root}.
Declared: the header's own text (PATH, default ports/fnv-nx/toolchain/include/fnvnx/hostapi.h, else tests/bootguest/hostapi.h of the
trunk's fixture): every `#define FNVNX_PLAT_<NAME> <integer>` directive (a preprocessor directive read by its token grammar: #, define,
identifier, one pp-number). A service is a NUMBER; names with the same number are aliases of one service (a range marker such as
SOCK_FIRST / SOCK_LAST aliases the service it equals, so it adds no item and its own use counts as a use of that service number).
Used: the evidence is the PP stage result unit.ii of every unit of the root (found by pp_key in objects.json, as gate_objstub does). The PP
stage runs `-dU` (unitstage.PP_TAIL): GCC then writes `#define NAME VALUE` for exactly the macros that the unit EXPANDS (or tests with
defined/#ifdef); a name written in a comment, a string or an unexpanded #define is not there. A root includes hostapi.h iff some unit.ii has
a line marker (`# N "path"`) whose file name is hostapi.h. A used FNVNX_PLAT_ macro whose number is not declared, or whose value differs from
the header's, is a red item as well (the unit saw another header).
Universe: one item per declared service number of each root that includes hostapi.h, plus one per undeclared use. Certified: declared and
used. No product root, or none that includes hostapi.h: universe 0, green, no exception list (gate_build.quantify).
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
import unitstage  # noqa: E402

PREFIX = "FNVNX_PLAT_"
HEADER = "hostapi.h"
DEFAULT_HEADERS = ("ports/fnv-nx/toolchain/include/fnvnx/hostapi.h", "ports/fnv-nx/tests/bootguest/hostapi.h")


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
    for p in ([explicit] if explicit else list(DEFAULT_HEADERS)):
        if os.path.isfile(os.path.join(repo, p)):
            return open(os.path.join(repo, p)).read()
    raise OSError("no hostapi.h at %s" % (explicit or " or ".join(DEFAULT_HEADERS)))


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
    return u, c, [root + ":" + i for i in ids], "; ".join(ids)[:300]


def main(argv):
    explicit = argv[argv.index("--header") + 1] if "--header" in argv else None
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-hostapi-")
    cache, repo = os.environ.get("GATE_CACHE") or tmp, "."
    try:
        roots = gate_build.product_roots(repo)
        header = header_text(repo, explicit) if roots else ""
        u, c, ids, detail = gate_build.quantify(repo, lambda r: root_judge(r, repo, cache, header))
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
