#!/usr/bin/env python3
"""The unit-set patch of the FreeType source tree (rule A of verify/RECIPE.md: every unit-named file of the tree is a unit, there is no exclude list).

    gen_delpatch.py TREE PATCHDIR OUT

TREE = the pinned mirror (fnv-nx-build mirror/freetype-src), PATCHDIR = the tracked patches. The tree is patched with every `.patch` of PATCHDIR
except OUT's own name (file-name order) and units.py derives the unit set U (modules.cfg + rules.mk). Three classes of unit-named files
(*.c *.cc *.cpp *.cxx *.s *.S, the recipe's unit rule) are then closed by rule:
  U  the units: untouched.
  R  the unit-named files that a file of the include closure of U names in a quoted `#include` (the part files an aggregator such as autofit.c
     includes: afangles.c, ft-hb.c ...). They must be present for the include and must not be units: each is renamed to `<name>.inc` and every quoted
     include of it in the closure is rewritten to the new name (the same text rule: the directive's path + ".inc").
  D  every other unit-named file: deleted.
The include closure is textual: start at U, scan every quoted `#include "p"` of a file, resolve p relative to that file's directory (the
preprocessor's own rule for the includer's directory), follow it when it is a file of the tree. A directive under `#if` is followed too (a superset
of what the preprocessor reaches, so no file it reaches is deleted). Output: git-style hunks sorted by path (renames as delete + create), bytes as in
the tree, no timestamps: the same tree gives the same file. check_units.py proves the result against the same derivation."""
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import units

UNIT_EXT = (".c", ".cc", ".cpp", ".cxx", ".s", ".S")
INCLUDE = re.compile(rb'^([ \t]*#[ \t]*include[ \t]+")([^"\n]+)(")', re.M)


def all_files(work):
    out = []
    for d, _, fs in os.walk(work):
        for f in fs:
            out.append(os.path.relpath(os.path.join(d, f), work).replace(os.sep, "/"))
    return sorted(out)


def unit_files(work):
    return [p for p in all_files(work) if p.endswith(UNIT_EXT)]


def resolve(work, includer, name):
    """The tree path a quoted include NAME of file INCLUDER names (relative to the includer's directory), or None."""
    p = os.path.normpath(os.path.join(os.path.dirname(includer), name)).replace(os.sep, "/")
    return p if not p.startswith("..") and os.path.isfile(os.path.join(work, p)) else None


def closure(work, keep):
    """Every file of the tree reachable from KEEP through quoted includes (textual): (reachable set, {file: [(name, target)]})."""
    seen, todo, edges = set(keep), sorted(keep), {}
    while todo:
        f = todo.pop()
        edges[f] = []
        for m in INCLUDE.finditer(open(os.path.join(work, f), "rb").read()):
            name = m.group(2).decode("latin-1")
            t = resolve(work, f, name)
            if t:
                edges[f].append((name, t))
                if t not in seen:
                    seen.add(t)
                    todo.append(t)
    return seen, edges


def hunk(path, data, new):
    lines = data.split(b"\n")
    tail = lines.pop()
    body = [l + b"\n" for l in lines] + ([tail] if tail else [])
    mode = b"100644"
    out = b"diff --git a/%s b/%s\n" % (path.encode(), path.encode())
    out += (b"new file mode %s\n--- /dev/null\n+++ b/%s\n" if new else b"deleted file mode %s\n--- a/%s\n+++ /dev/null\n") % (mode, path.encode())
    if body:
        out += (b"@@ -0,0 +1,%d @@\n" if new else b"@@ -1,%d +0,0 @@\n") % len(body)
        for i, l in enumerate(body):
            out += (b"+" if new else b"-") + l
            if i == len(body) - 1 and not l.endswith(b"\n"):
                out += b"\n\\ No newline at end of file\n"
    return out


def udiff(old, new, la, lb):
    """`diff -u` body (---/+++ and hunks) of two byte strings, or b"" when equal."""
    if old == new:
        return b""
    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        open(a, "wb").write(old)
        open(b, "wb").write(new)
        return subprocess.run(["diff", "-u", "--label", la, "--label", lb, a, b], capture_output=True).stdout


def rename_patch(work, p, new):
    """git-style rename of P to P.inc with the new content (the rewritten includes): similarity header plus the hunks of the change."""
    old = open(os.path.join(work, p), "rb").read()
    ol, nl = old.count(b"\n"), new.count(b"\n")
    changed = sum(1 for x, y in zip(old.split(b"\n"), new.split(b"\n")) if x != y)
    sim = 100 if old == new else max(1, 100 - 100 * changed // max(1, max(ol, nl)))
    out = b"diff --git a/%s b/%s.inc\nsimilarity index %d%%\nrename from %s\nrename to %s.inc\n" % ((p.encode(),) * 2 + (sim,) + (p.encode(),) * 2)
    return out + udiff(old, new, "a/" + p, "b/" + p + ".inc")


def rewritten(work, f, rename):
    """The content of file F with every quoted include of a renamed file given the new name."""
    def sub(m):
        t = resolve(work, f, m.group(2).decode("latin-1"))
        return m.group(1) + m.group(2) + (b".inc" if t in rename else b"") + m.group(3)
    return INCLUDE.sub(sub, open(os.path.join(work, f), "rb").read())


def plan(work):
    """-> (U, R, D, closure files): the three classes of the unit-named files and the include closure of U."""
    u = set(units.derive_tree(work))
    reach, _ = closure(work, u)
    allu = unit_files(work)
    r = sorted(p for p in allu if p in reach and p not in u)
    d = sorted(p for p in allu if p not in reach)
    return sorted(u), r, d, reach


def patch_bytes(work):
    u, r, d, reach = plan(work)
    rename = set(r)
    for p in r:
        if os.path.exists(os.path.join(work, p + ".inc")):
            raise SystemExit("gen_delpatch: %s.inc exists in the tree" % p)
    parts = {}
    for p in d:
        parts[p] = hunk(p, open(os.path.join(work, p), "rb").read(), False)
    for p in r:
        parts[p] = rename_patch(work, p, rewritten(work, p, rename))
    with tempfile.TemporaryDirectory() as tmp:
        for f in sorted(reach - rename - set(d)):
            new = rewritten(work, f, rename)
            old = open(os.path.join(work, f), "rb").read()
            if new != old:
                parts[f] = b"diff --git a/%s b/%s\n" % (f.encode(), f.encode()) + udiff(old, new, "a/" + f, "b/" + f)
    return b"".join(parts[k] for k in sorted(parts))


def main(a):
    tree, pdir, outp = a
    mine = os.path.basename(outp)
    with tempfile.TemporaryDirectory() as tmp:
        work = units.patched(tree, [os.path.join(pdir, p) for p in sorted(os.listdir(pdir)) if p.endswith(".patch") and p != mine], tmp)
        data = patch_bytes(work)
    open(outp, "wb").write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
