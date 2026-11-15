#!/usr/bin/env python3
"""gen_sysmetrics - the GetSystemMetrics table, from Wine 9.0's get_system_metrics.

    gen_sysmetrics.py <wine-9.0 tree> <display.cpp> <out.inc>

The rule: our GetSystemMetrics answers exactly the indices that `get_system_metrics` of dlls/win32u/sysparams.c (Wine 9.0) has a `case SM_*:`
label for, and every other index answers 0, Wine's `default: return 0`. Labels, ids and citations come from the pinned tree:
  - the labels: every `case SM_X:` at the top level of the switch in get_system_metrics (comments removed, braces matched);
  - the id of SM_X: its `#define` in include/winuser.h of the tree;
  - the value of a case: a function `static int sm_SM_X(void) { return <expression>; }` of <display.cpp>, bound by name. The expression is code
    (behaviour); a quantity of the declared machine is read from its one source there, never a literal.
  - where the reference's case is a plain literal (`return <integer or SM_name>;`, a group of stacked labels sharing it), the build checks that
    our body is the same literal.
Errors (exit 1, the stage is red): a label with no id or no handler; a handler `sm_SM_*` that is not a label (an answer the reference does not
give: its default answers 0); a literal case whose handler returns another value; a name defined twice; two labels with one id.
Output <out.inc>: forward declarations of the handlers and `k_rules[]` {index, handler, citation}, sorted by index; the citation is
`wine-9.0:dlls/win32u/sysparams.c:get_system_metrics case SM_X (line N)`. Python 3 standard library only."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_wine_cites import strip_comments  # noqa: E402

FILE = "dlls/win32u/sysparams.c"
FUNC = "get_system_metrics"
IDS = "include/winuser.h"
HANDLER = re.compile(r"^static int (sm_(SM_[A-Z0-9_]+))\(void\)[ \t]*\{(.*)\}[ \t]*$", re.M)
LITERAL = re.compile(r"return\s*(-?[0-9]+|SM_[A-Z0-9_]+)\s*;")


def groups(tree):
    """-> [([SM_X, ...], line of the first label, literal or None)] of the switch of get_system_metrics, in source order; a literal is the text
    of `return <integer or SM_name>;` when that is the whole statement list of the group"""
    text = strip_comments(open(os.path.join(tree, FILE), encoding="utf-8", errors="replace").read())
    m = re.search(r"^int[ \t]+" + FUNC + r"[ \t]*\(", text, re.M)
    if not m:
        raise ValueError("%s: no definition of %s" % (FILE, FUNC))
    sw = re.compile(r"\bswitch\s*\(\s*index\s*\)\s*\{").search(text, m.end())
    pos, depth, marks = sw.end(), 1, []   # marks: (kind, start, end, name)
    while depth:
        c = text[pos]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 1:
            lm = re.compile(r"(case[ \t\n]+(SM_[A-Z0-9_]+)[ \t\n]*:|default[ \t\n]*:)").match(text, pos)
            if lm and (pos == 0 or not (text[pos - 1].isalnum() or text[pos - 1] == "_")):
                marks.append((lm.group(2), lm.start(), lm.end()))
                pos = lm.end()
                continue
        pos += 1
    end = pos
    out, i = [], 0
    while i < len(marks):
        j, names = i, []
        while j < len(marks) and marks[j][0] is not None and (j == i or not text[marks[j - 1][2]:marks[j][1]].strip()):
            names.append(marks[j][0])
            j += 1
        if not names:   # `default:`
            i += 1
            continue
        stop = marks[j][1] if j < len(marks) else end - 1
        body = text[marks[j - 1][2]:stop]
        lit = LITERAL.fullmatch(body.strip())
        out.append((names, text.count("\n", 0, marks[i][1]) + 1, lit.group(1) if lit else None, [text.count("\n", 0, marks[k][1]) + 1 for k in range(i, j)]))
        i = j
    return out


def main():
    tree, cpp, out = sys.argv[1:4]
    errors = []
    defs = {}
    for m in re.finditer(r"^#[ \t]*define[ \t]+(SM_[A-Z0-9_]+)[ \t]+(0x[0-9A-Fa-f]+|[0-9]+|SM_[A-Z0-9_]+)\b", open(os.path.join(tree, IDS), encoding="utf-8", errors="replace").read(), re.M):
        defs.setdefault(m.group(1), m.group(2))

    def ident(n):
        v, hops = defs.get(n), 0
        while v in defs and hops < 8:
            v, hops = defs[v], hops + 1
        return int(v, 0) if v and re.fullmatch(r"0x[0-9A-Fa-f]+|[0-9]+", v) else None

    handlers = {}
    for m in HANDLER.finditer(open(cpp, encoding="utf-8").read()):
        if m.group(2) in handlers:
            errors.append("handler %s is defined twice" % m.group(1))
        handlers[m.group(2)] = (m.group(1), m.group(3).strip())
    rows, names = [], set()
    for labs, _, lit, lines in groups(tree):
        for n, line in zip(labs, lines):
            names.add(n)
            idx = ident(n)
            if idx is None:
                errors.append("%s (%s:%d): no #define in %s" % (n, FILE, line, IDS))
                continue
            if n not in handlers:
                errors.append("%s (%s:%d): no handler sm_%s in %s (an answer the reference gives, with no implementation)" % (n, FILE, line, n, os.path.basename(cpp)))
                continue
            if lit is not None:
                want = ident(lit) if lit.startswith("SM_") else int(lit)
                mine = re.fullmatch(r"return\s*(-?[0-9]+|SM_[A-Z0-9_]+)\s*;", handlers[n][1])
                got = None if not mine else (ident(mine.group(1)) if mine.group(1).startswith("SM_") else int(mine.group(1)))
                if got is None or got != want:
                    errors.append("%s (%s:%d): the reference returns the literal %s; %s is `%s`" % (n, FILE, line, lit, handlers[n][0], handlers[n][1]))
            rows.append((idx, n, line))
    for n in sorted(set(handlers) - names):
        errors.append("sm_%s: %s of %s has no `case %s:`; the reference's default answers 0 for it" % (n, FUNC, FILE, n))
    seen = {}
    for idx, n, _ in rows:
        if idx in seen:
            errors.append("%s and %s have the index %d" % (seen[idx], n, idx))
        seen[idx] = n
    if errors:
        sys.stderr.write("\n".join(errors) + "\n%d errors\n" % len(errors))
        return 1
    rows.sort()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as fh:
        fh.write("/* generated by manifest/gen_sysmetrics.py from wine-9.0 %s:%s (%d case labels); do not edit */\n" % (FILE, FUNC, len(rows)))
        for _, n, _ in rows:
            fh.write("static int %s(void);\n" % handlers[n][0])
        fh.write("struct rule { int idx; int (*fn)(void); const char *src; };\n")
        fh.write("const rule k_rules[] = {\n")
        for idx, n, line in rows:
            fh.write('    { %d, %s, "wine-9.0:%s:%s case %s (line %d)" },\n' % (idx, handlers[n][0], FILE, FUNC, n, line))
        fh.write("};\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
