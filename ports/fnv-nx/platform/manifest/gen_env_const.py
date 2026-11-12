#!/usr/bin/env python3
"""gen_env_const - bodies of the imports whose documented result is one constant in this environment.

    gen_env_const.py env_const.tsv out.inc [--only PREFIX] [--probe] [--declared verify/declared_machine.tsv]

A row of platform/manifest/env_const.tsv states a fact of the contract, not code: for every argument, in the
environment of ENV_TRUTH.md, the function returns `value`. Columns (TAB separated, header line required):
    symbol   the function: a C name, or Class::method for a member defined out of class
    linkage  C (extern "C") | C++
    ret      return type as C text
    params   parameter list as C text (`void` for none)
    value    the constant expression returned
    source   ENV:<row id of ENV_TRUTH.md> or MSDN:<function or method> - then the words of the rule
    quals    optional: text after the parameter list of a member (const, noexcept)
    file     the source file (relative to platform/) that includes the generated bodies; --only selects by prefix
             (a .c file gets plain C definitions)
Declared-machine citation (verify/VERDICT.md "Declared-machine bodies"): the table verify/declared_machine.tsv (default: next to this
tree, ../../verify/declared_machine.tsv; rows `id contract_name fact source kind value`) is read; a symbol that has a row there (contract_name ==
symbol) gets `fnvnx_declared_machine(<id>);` as the first statement of its body, and its row's kind/value must equal the row's constant
(false, NULL and 0 are 0). One rule for every row: no list of functions here.
Checks, each an error: a row without a source of one of the two forms, an empty value, a void return, a symbol
given twice for one file, a column count other than 8. The output is a pure function of the table (rows in table order).
--probe writes the probe form read by env_const_export.py instead: the same bodies, each with one asm marker (a relocation
in section .fnvnx_row from the row's table line number to the function's own section, so the compiler names the symbol)
and, after the bodies, `__attribute__((used)) const <ret> fnvnx_probe_<line> = (<value>);` - the value, evaluated by the compiler of the
function's own source file with its headers and flags.
"""
import os
import re
import sys

COLS = ["symbol", "linkage", "ret", "params", "value", "source", "quals", "file"]


def declared_rows(path):
    """{contract_name: (id, value)} of verify/declared_machine.tsv; a malformed row is an error."""
    rows = {}
    if not os.path.exists(path):
        return rows
    for n, line in enumerate(open(path, encoding="utf-8").read().split("\n"), 1):
        if not line or line.startswith("#"):
            continue
        r = line.split("\t")
        if len(r) != 6 or not r[0].isdigit():
            raise SystemExit("env_const: %s:%d: a declared-machine row has 6 columns and a decimal id" % (path, n))
        if r[1] in rows:
            raise SystemExit("env_const: %s:%d: %s has two rows" % (path, n, r[1]))
        rows[r[1]] = (int(r[0]), r[4], r[5])
    return rows


def const_value(v):
    v = v.strip()
    if v in ("false", "NULL", "nullptr"):
        return 0
    return int(v, 0) if re.fullmatch(r"-?(0[xX][0-9a-fA-F]+|\d+)[uUlL]*", v) else None


def main(argv):
    only = None
    dpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify", "declared_machine.tsv")
    if "--declared" in argv:
        i = argv.index("--declared")
        dpath = argv[i + 1]
        del argv[i:i + 2]
    decl = declared_rows(dpath)
    probe = "--probe" in argv
    if probe:
        argv.remove("--probe")
    consts = []
    if "--only" in argv:
        i = argv.index("--only")
        only = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    lines = open(argv[1], encoding="utf-8").read().split("\n")
    if lines[0].split("\t") != COLS:
        sys.stderr.write("env_const: header must be %s\n" % "\t".join(COLS))
        return 1
    out, seen, errors, used = [], set(), [], set()
    for n, line in enumerate(lines[1:], 2):
        if not line:
            continue
        r = line.split("\t")
        if len(r) != len(COLS):
            errors.append("line %d: %d columns" % (n, len(r)))
            continue
        row = dict(zip(COLS, r))
        if (row["symbol"], row["file"]) in seen:
            errors.append("line %d: %s given twice" % (n, row["symbol"]))
        seen.add((row["symbol"], row["file"]))
        if not re.match(r"(ENV:[A-Z]\d+|MSDN:\S+) \S", row["source"]):
            errors.append("line %d: %s: source must be `ENV:<row> words` or `MSDN:<name> words`" % (n, row["symbol"]))
        if not row["value"].strip() or row["ret"].strip() == "void":
            errors.append("line %d: %s: a constant result needs a value and a non-void type" % (n, row["symbol"]))
        if row["linkage"] not in ("C", "C++"):
            errors.append("line %d: %s: linkage %r" % (n, row["symbol"], row["linkage"]))
        if only is not None and not row["file"].startswith(only):
            continue
        head = 'extern "C" ' if row["linkage"] == "C" and not row["file"].endswith(".c") else ""   # a .c file is C already
        quals = (" " + row["quals"]) if row["quals"] else ""
        cite = ""
        if row["symbol"] in decl:
            did, dkind, dval = decl[row["symbol"]]
            if dkind not in ("u32", "u64") or const_value(dval) is None or const_value(dval) != const_value(row["value"]):
                errors.append("line %d: %s: its declared_machine.tsv row (%s %s) differs from the constant %s" % (n, row["symbol"], dkind, dval, row["value"]))
            cite = "    fnvnx_declared_machine(%d);\n" % did
            used.add(row["symbol"])
        mark = ('    __asm__ volatile("1:\\n\\t.pushsection .fnvnx_row,\\"a\\"\\n\\t.4byte %%c0\\n\\t.4byte 1b\\n\\t.popsection" :: "i"(%d));\n' % n
                if probe else "")
        out.append("/* %s */\n%s%s %s(%s)%s\n{\n%s%s    return %s;\n}\n" % (
            row["source"], head, row["ret"], row["symbol"], row["params"], quals, mark, cite, row["value"]))
        consts.append("__attribute__((used)) const %s fnvnx_probe_%d __asm__(\"fnvnx_probe_%d\") = (%s);\n" % (row["ret"], n, n, row["value"]))
    if only is None:
        for sym in sorted(set(decl) - used):
            errors.append("declared_machine.tsv row %s has no body in this table" % sym)
    if errors:
        for e in errors:
            sys.stderr.write("env_const: %s\n" % e)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(argv[2])), exist_ok=True)
    with open(argv[2], "w", encoding="utf-8") as f:
        f.write("/* generated by platform/manifest/gen_env_const.py from env_const.tsv - not a source file */\n\n")
        if used:
            f.write('#ifdef __cplusplus\nextern "C"\n#endif\nvoid fnvnx_declared_machine(unsigned id);   /* platstat.cpp: writes nothing, returns */\n\n')
        f.write("\n".join(out))
        if probe:
            f.write("\n" + "".join(consts))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
