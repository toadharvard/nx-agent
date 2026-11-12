#!/usr/bin/env python3
"""The deletion patch of the FreeType source tree (rule A of verify/RECIPE.md: no exclude list, an upstream file that must not be built is deleted).

    gen_delpatch.py TREE PATCHDIR OUT

TREE = the pinned mirror (fnv-nx-build mirror/freetype-src), PATCHDIR = the tracked patches. The tree is patched with every `.patch` of PATCHDIR
except OUT's own name (file-name order), the unit set of units.py (modules.cfg + rules.mk of that tree) is derived, and OUT deletes every
unit-named file (*.c *.cc *.cpp *.cxx *.s *.S, the recipe's unit rule) of the tree that is not in the set. Output: git-style deletion hunks, sorted by path,
bytes as in the tree (no timestamps): the same tree gives the same file. check_units.py proves the result against the same derivation."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import units

UNIT_EXT = (".c", ".cc", ".cpp", ".cxx", ".s", ".S")


def unit_files(work):
    out = []
    for d, _, fs in os.walk(work):
        for f in fs:
            if f.endswith(UNIT_EXT):
                out.append(os.path.relpath(os.path.join(d, f), work).replace(os.sep, "/"))
    return sorted(out)


def deletion(work, keep):
    out = bytearray()
    for p in unit_files(work):
        if p in keep:
            continue
        full = os.path.join(work, p)
        data = open(full, "rb").read()
        lines = data.split(b"\n")
        tail = lines.pop()   # b"" when the file ends with a newline, else the last unterminated line
        body = [l + b"\n" for l in lines] + ([tail] if tail else [])
        mode = "100755" if os.stat(full).st_mode & 0o111 else "100644"
        out += b"diff --git a/%s b/%s\ndeleted file mode %s\n--- a/%s\n+++ /dev/null\n" % (p.encode(), p.encode(), mode.encode(), p.encode())
        if body:
            out += b"@@ -1,%d +0,0 @@\n" % len(body)
            for i, l in enumerate(body):
                out += b"-" + l
                if i == len(body) - 1 and not l.endswith(b"\n"):
                    out += b"\n\\ No newline at end of file\n"
    return bytes(out)


def main(a):
    tree, pdir, outp = a
    mine = os.path.basename(outp)
    with tempfile.TemporaryDirectory() as tmp:
        work = units.patched(tree, [os.path.join(pdir, p) for p in sorted(os.listdir(pdir)) if p.endswith(".patch") and p != mine], tmp)
        keep = set(units.derive_tree(work))
        data = deletion(work, keep)
    open(outp, "wb").write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
