#!/usr/bin/env python3
"""gen_wine_cites - every `wine-9.0:<path>:<function>` citation is checked against the pinned Wine 9.0 tree.

    gen_wine_cites.py <wine-9.0 tree> <out.tsv> <scanned file or directory>...

A citation is the token `wine-9.0:<path>:<function>` (path: [A-Za-z0-9_./+-]+, function: a C identifier) in a scanned file; a directory
is scanned for every regular file under it that is text. The tree is the external `wine-9.0` of the root (fnv-nx-build tools/wine-9.0,
pinned by commit and tree_sha256), mounted read-only. Checks, each an error (exit 1, the stage is red, the build is red):
  - the path is a regular file of the tree and does not leave it;
  - the function is DEFINED in that file: a line at column 0 that holds the name followed by `(` (Wine's definition style: the name
    starts the line, or it follows a return type on the same line), is not a statement (`;` before the name) and not inside a comment
    block; or a `#define NAME(`. A call, a prototype and a mention in a comment are not a definition.
Output (TSV, sorted, one row per distinct citation): citation, the file:line of the definition in the tree, the scanned files that cite it.
The output is a pure function of the tree and the scanned files; python 3 standard library only."""
import os
import re
import sys

CITE = re.compile(r"wine-9\.0:([A-Za-z0-9_./+-]+):([A-Za-z_][A-Za-z0-9_@]*)")


def strip_comments(text):
    """TEXT with every /* */ and // comment and every string or character literal replaced by spaces (newlines kept), so line numbers hold"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
        else:
            out.append(c)
            i += 1
            continue
        out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
        i = j
    return "".join(out)


def definition(text, name):
    """-> line number (1-based) of the first definition of NAME in TEXT, or None. A definition: NAME, after an optional return type on its line
    (no `;`, `=`, `(`, `{`, `}` before it), followed by a balanced parameter list and then `{` (a prototype ends in `;`, a call in an operator);
    or `#define NAME(`."""
    code = strip_comments(text)
    pat = re.compile(r"^(?:[A-Za-z_][^;=(){}\n]*[ \t*])?" + re.escape(name) + r"[ \t]*\(", re.M)
    macro = re.compile(r"^[ \t]*#[ \t]*define[ \t]+" + re.escape(name) + r"\(", re.M)
    for m in sorted(list(pat.finditer(code)) + list(macro.finditer(code)), key=lambda x: x.start()):
        if m.re is macro:
            return code.count("\n", 0, m.start()) + 1
        depth, i = 1, m.end()
        while i < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[i], 0)
            i += 1
        rest = code[i:i + 200].lstrip()
        if depth == 0 and rest.startswith("{"):
            return code.count("\n", 0, m.start()) + 1
    return None


def scanned_files(args):
    for a in args:
        if os.path.isdir(a):
            for d, ds, fs in os.walk(a):
                ds.sort()
                for f in sorted(fs):
                    yield os.path.join(d, f)
        else:
            yield a


def main():
    tree, out, scan = sys.argv[1], sys.argv[2], sys.argv[3:]
    root = os.path.realpath(tree)
    cites, errors = {}, []
    for f in scanned_files(scan):
        try:
            text = open(f, encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        for m in CITE.finditer(text):
            cites.setdefault((m.group(1), m.group(2)), set()).add(f)
    rows, cache = [], {}
    for (p, fn), users in sorted(cites.items()):
        full = os.path.realpath(os.path.join(tree, p))
        if ".." in p.split("/") or not full.startswith(root + os.sep) or not os.path.isfile(full):
            errors.append("wine-9.0:%s:%s: the path is not a file of the pinned tree (cited by %s)" % (p, fn, ", ".join(sorted(users))))
            continue
        if full not in cache:
            cache[full] = open(full, encoding="utf-8", errors="replace").read()
        line = definition(cache[full], fn)
        if line is None:
            errors.append("wine-9.0:%s:%s: no definition of %s in %s (cited by %s)" % (p, fn, fn, p, ", ".join(sorted(users))))
            continue
        rows.append("wine-9.0:%s:%s\t%s:%d\t%s" % (p, fn, p, line, " ".join(sorted(users))))
    if errors:
        sys.stderr.write("\n".join(errors) + "\n%d of %d citations failed\n" % (len(errors), len(cites)))
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as fh:
        fh.write("# checked %d citations against wine-9.0 (every one resolved)\n" % len(rows))
        fh.write("\n".join(rows) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
