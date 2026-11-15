#!/usr/bin/env python3
"""gen_wine_cites - every `wine-9.0:<path>:<function>` citation is checked against the pinned Wine 9.0 tree.

    gen_wine_cites.py <wine-9.0 tree> <out.tsv> <scanned file or directory>...

A citation is the token `wine-9.0:<path>:<function>` (path: [A-Za-z0-9_./+-]+, function: a C identifier) in a scanned file; a directory
is scanned for every regular file under it that is text, except the test files (`test_*`: their citations are the inputs of the test, not product claims). The tree is the external `wine-9.0` of the root (fnv-nx-build tools/wine-9.0,
pinned by commit and tree_sha256), mounted read-only. Checks, each an error (exit 1, the stage is red, the build is red):
  - the path is a regular file of the tree and does not leave it;
  - the function is DEFINED in that file: a line at column 0 that holds the name followed by `(` (Wine's definition style: the name
    starts the line, or it follows a return type on the same line), is not a statement (`;` before the name) and not inside a comment
    block; or a `#define NAME(`. A call, a prototype and a mention in a comment are not a definition. The finer forms are in definition(): an object-like macro, a typedef, a function a macro body defines (literal or pasted from an invocation's argument).
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


def _balanced(code, i):
    """index just after the `)` that closes the `(` before position I (depth 1 at I), or -1"""
    depth = 1
    while i < len(code) and depth:
        depth += {"(": 1, ")": -1}.get(code[i], 0)
        i += 1
    return i if depth == 0 else -1


def _macros(code):
    """-> [(macro name, [parameters], body, line of the #define)] of every `#define` of CODE (comments stripped); the body joins
    the `\\` continuation lines; a body keeps its line structure by line count only"""
    out = []
    for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_][A-Za-z0-9_]*)(?:\(([^)]*)\))?((?:\\\n|[^\n])*)", code, re.M):
        out.append((m.group(1), [p.strip() for p in (m.group(2) or "").split(",") if p.strip()], m.group(3).replace("\\\n", "\n"),
                    code.count("\n", 0, m.start()) + 1))
    return out


def _args(code, macro):
    """-> [[argument texts]] of every invocation `MACRO(a, b, ...)` of CODE (top-level commas)"""
    res = []
    for m in re.finditer(r"(?<![A-Za-z0-9_#])" + re.escape(macro) + r"[ \t]*\(", code):
        e = _balanced(code, m.end())
        if e < 0:
            continue
        parts, depth, cur = [], 0, ""
        for ch in code[m.end():e - 1]:
            if ch == "," and depth == 0:
                parts.append(cur.strip()); cur = ""
                continue
            depth += {"(": 1, ")": -1}.get(ch, 0)
            cur += ch
        parts.append(cur.strip())
        res.append(parts)
    return res


def definition(text, name, siblings=()):
    """-> line number (1-based) of the first definition of NAME in TEXT, or None. A definition is one of:
      - a function: NAME, after an optional return type on its line (no `;`, `=`, `(`, `{`, `}` before it), followed by a balanced parameter list
        and then `{` (a prototype ends in `;`, a call in an operator);
      - a macro: `#define NAME(` or `#define NAME` (object-like);
      - a type: `typedef ... NAME;` at column 0;
      - a function that a macro defines, in the macro's body: the same declarator rule, with NAME literal in the body; or pasted, `P ## SUFFIX(`
        with P a parameter of that macro, where NAME is V + SUFFIX for the value V that P has at an invocation of the macro in TEXT or in SIBLINGS
        (the other files of the directory, as text): the function the invocation instantiates. The line is the macro's `#define`."""
    code = strip_comments(text)
    pat = re.compile(r"^(?:[A-Za-z_][^;=(){}\n]*[ \t*])?" + re.escape(name) + r"[ \t]*\(", re.M)
    macro = re.compile(r"^[ \t]*#[ \t]*define[ \t]+" + re.escape(name) + r"(?:\(|[ \t]|\\|$)", re.M)
    tdef = re.compile(r"^typedef\b[^;{}]*[ \t*]" + re.escape(name) + r"[ \t]*;", re.M)
    for m in sorted(list(pat.finditer(code)) + list(macro.finditer(code)) + list(tdef.finditer(code)), key=lambda x: x.start()):
        if m.re is not pat:
            return code.count("\n", 0, m.start()) + 1
        e = _balanced(code, m.end())
        if e >= 0 and code[e:e + 200].lstrip().startswith("{"):
            return code.count("\n", 0, m.start()) + 1
    sib = [strip_comments(t) for t in siblings]
    for mname, params, body, line in _macros(code):
        lit = re.compile(r"(?<![A-Za-z0-9_#])" + re.escape(name) + r"[ \t]*\(")
        for m in lit.finditer(body):
            e = _balanced(body, m.end())
            if e >= 0 and body[e:e + 200].lstrip().startswith("{") and not re.match(r"[ \t]*[A-Za-z_]", body[max(0, m.start() - 1):m.start()]):
                return line
        for i, p in enumerate(params):
            for m in re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(p) + r"[ \t]*##[ \t]*([A-Za-z0-9_]+)[ \t]*\(", body):
                suffix = m.group(1)
                e = _balanced(body, m.end())
                if not (name.endswith(suffix) and len(name) > len(suffix) and e >= 0 and body[e:e + 200].lstrip().startswith("{")):
                    continue
                for src in [code] + sib:
                    if any(len(a) > i and a[i] == name[:-len(suffix)] for a in _args(src, mname)):
                        return line
    return None


def scanned_files(args):
    for a in args:
        if os.path.isdir(a):
            for d, ds, fs in os.walk(a):
                ds.sort()
                for f in sorted(fs):
                    if not f.startswith("test_"):   # a test file holds synthetic citations as its inputs; it is not a product source
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
        d = os.path.dirname(full)
        sibs = [open(os.path.join(d, x), encoding="utf-8", errors="replace").read() for x in sorted(os.listdir(d))
                if x != os.path.basename(full) and x.endswith((".c", ".h")) and os.path.isfile(os.path.join(d, x))]
        line = definition(cache[full], fn, sibs)
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
