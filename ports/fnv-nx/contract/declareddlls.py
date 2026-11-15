#!/usr/bin/env python3
"""declareddlls - the dll set of the declared machine (L1 CONTRACT): the dlls Wine 9.0 builds (CLAUDE.md "Wine 9.0 as an acceptable implementation").

    declareddlls.py --specs DIR --out declared_dlls.tsv

DIR is the `specs/` directory of the fnv-nx-build mirror `tools/wine-specs` (the dlls/<name>/<name>.spec files of the wine-9.0 tarball, flattened to
<name>.spec; pinned by commit and tree_sha256 in contract_inputs.json, external `wine`).  Rule: the declared machine has a dll NAME.dll iff the
directory holds a regular file NAME.spec.  Output rows `NAME.dll<TAB>sha256 of the spec`, sorted by name; a file in DIR that is not `*.spec`,
a name that is not lowercase ASCII [a-z0-9_.-], or an empty DIR is an error.  Nothing is taken from a name of a dll of the game."""
import argparse
import hashlib
import os
import re
import sys


def derive(specs):
    rows, bad = [], []
    for f in sorted(os.listdir(specs)):
        p = os.path.join(specs, f)
        if not f.endswith(".spec") or not os.path.isfile(p) or not re.fullmatch(r"[a-z0-9_.-]+\.spec", f):
            bad.append(f)
            continue
        with open(p, "rb") as fh:
            rows.append((f[:-5] + ".dll", hashlib.sha256(fh.read()).hexdigest()))
    if bad or not rows:
        raise ValueError("not a Wine spec directory: " + (", ".join(bad[:10]) or "empty"))
    return rows


def read(path):
    """-> {dll name} of a declared_dlls.tsv."""
    with open(path) as f:
        return {ln.split("\t")[0] for ln in f if ln.strip() and not ln.startswith("#")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--specs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        rows = derive(a.specs)
    except (ValueError, OSError) as e:
        sys.exit("declareddlls: %s" % e)
    with open(a.out, "w", newline="\n") as f:
        f.write("# name\tspec_sha256\t(Wine 9.0 dlls/*/*.spec; mirror fnv-nx-build tools/wine-specs 678c780e, tree_sha256 d21bd712...)\n")
        f.write("".join("%s\t%s\n" % r for r in rows))
    print("declared dlls: %d" % len(rows))


if __name__ == "__main__":
    main()
