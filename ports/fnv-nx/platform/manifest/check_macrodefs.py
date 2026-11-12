#!/usr/bin/env python3
"""Class check: no macro name is defined with two different values across the shared headers.

  check_macrodefs.py [root]        (run with python3 -I; root = ports/fnv-nx, default: the repository this file is in)

Scope (rule of the L5 lead): every git-tracked header (.h .hpp .inc) under shim/include, toolchain/include and platform. A definition is a
`#define NAME body` (object-like or function-like, line continuations joined, comments and surrounding space removed). Two definitions of
one name with different (parameter list, body) are a finding; the same definition repeated is not. A definition is not counted when it cannot
be live together with the other one: it sits under `#ifndef NAME` / `#if !defined(NAME)` of its own name (the first definition wins by
construction), follows an `#undef NAME` in the same file, or the two sit in different branches of one #if/#elif/#else group of one file. Exit 1 with the findings, 0 and
"0 found" when none. Output is a measurement of the whole scope: the count of definitions, names and findings.
"""
import os, re, subprocess, sys

DEF = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(\([^)]*\))?(.*)$")


def strip(s):
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"//.*", "", s)
    return " ".join(s.split())


def main(root):
    files = subprocess.check_output(["git", "-C", root, "ls-files", "shim/include", "toolchain/include", "platform"], text=True).split()
    defs, n = {}, 0
    for f in files:
        if not f.endswith((".h", ".hpp", ".inc")):
            continue
        text = open(os.path.join(root, f), errors="replace").read()
        text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
        text = re.sub(r"\\\n", " ", text)
        stack, gid, undef = [], 0, set()  # stack of [group, branch, guarded-names]
        for i, ln in enumerate(text.split("\n"), 1):
            c = re.match(r"^[ \t]*#[ \t]*(ifdef|ifndef|if|elif|else|endif|undef)\b(.*)$", ln)
            if c:
                d, rest = c.group(1), c.group(2)
                if d in ("if", "ifdef", "ifndef"):
                    gid += 1
                    g = set()
                    if d == "ifndef":
                        g = set(re.findall(r"[A-Za-z_]\w*", rest)[:1])
                    elif d == "if":
                        g = set(re.findall(r"^\s*!\s*defined\s*\(?\s*([A-Za-z_]\w*)\s*\)?\s*$", rest))
                    stack.append([gid, 0, g])
                elif d in ("elif", "else") and stack:
                    stack[-1][1] += 1
                    stack[-1][2] = set()
                elif d == "endif" and stack:
                    stack.pop()
                elif d == "undef":
                    undef.add(c.group(2).strip())
                continue
            m = DEF.match(ln)
            if m:
                n += 1
                name = m.group(1)
                if name in undef or any(name in e[2] for e in stack):
                    continue
                path = frozenset((e[0], e[1]) for e in stack)
                defs.setdefault(name, []).append(((m.group(2) or "").replace(" ", ""), strip(m.group(3)), "%s:%d" % (f, i), f, path))
    def exclusive(a, b):
        return a[3] == b[3] and any(g == h and x != y for g, x in a[4] for h, y in b[4])
    bad = {}
    for k, v in defs.items():
        live = [(a, b) for a in v for b in v if a is not b and (a[0], a[1]) != (b[0], b[1]) and not exclusive(a, b)]
        if live:
            bad[k] = {x for pr in live for x in pr}
    for k in sorted(bad):
        print("%s:" % k)
        for a, b, w, _, _ in sorted(bad[k], key=lambda x: x[2]):
            print("   %s  %s%s" % (w, a, " " + b))
    print("%d definitions, %d names, %d found" % (n, len(defs), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
