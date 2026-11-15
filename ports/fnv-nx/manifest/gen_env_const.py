#!/usr/bin/env python3
"""gen_env_const - bodies of the imports whose documented result is one constant in this environment.

    gen_env_const.py env_const.tsv out.inc [--only PREFIX] [--probe] [--declared verify/declared_machine.tsv]

A row of manifest/env_const.tsv states a fact of the contract, not code: for every argument, in the
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
tree, ../verify/declared_machine.tsv; rows `id contract_name fact source kind value`) is read; a symbol that has a row there (contract_name ==
symbol) gets `FNVNX_CITE(<symbol>, "<id>");` after its body (hostabi/include/fnvnx/cite.h: one record in the non-alloc section .fnvnx.cite, no call), and its row's kind/value must equal the row's constant; the symbol must have C linkage (the record names the assembler symbol)
(false, NULL and 0 are 0). A row whose symbol has no body in this table is cited by a hand-written body (FNVNX_CITE there); the gate matches rows to records. A value `identity:<k>` means the function returns its argument k unchanged (body `return <name of parameter k>;`, the parameter named in the params column). One rule for every row: no list of functions here.
Checks, each an error: a row without a source of one of the two forms, an empty value, a void return, a symbol
given twice for one file, a column count other than 8. The output is a pure function of the table (rows in table order).
--probe writes the probe form instead (the recipe's `probe` stage compiles the includer with it): the same bodies, and after them one record per row
in the non-alloc section `.fnvnx.const`: u32 row id (the line of env_const.tsv), u32 byte length, the value bytes (the row's expression evaluated by the
compiler of the includer's own file, headers and flags, as an object of the row's type), padded to 4, little endian.
"""
import os
import re
import sys

# One record per row in the section .fnvnx.const (--probe): u32 row id (the line of env_const.tsv), u32 byte length, the value (the row's expression
# evaluated by the compiler of the includer's own scope, as an object of the row's type), padded to 4 (little endian). The probe object is compiled by
# the recipe's probe stage and read, never linked: its sections are plain.
RECORD = (
    '__attribute__((section(".fnvnx.const"), used, aligned(4))) static const struct __attribute__((packed)) { unsigned id; unsigned len; %(ret)s v; }\n'
    '    fnvnx_const_%(n)d = { %(n)d, (unsigned)sizeof(%(ret)s), (%(value)s) };\n'
    'FNVNX_SA(sizeof(fnvnx_const_%(n)d) == (8 + sizeof(%(ret)s)), "row %(n)d: the record is header + value");\n')
SA_DEF = '#ifdef __cplusplus\n#define FNVNX_SA static_assert\n#else\n#define FNVNX_SA _Static_assert\n#endif\n\n'
# An identity row (value `identity:<k>`): the function returns its argument k unchanged. Its probe record is in the non-alloc section .fnvnx.ident:
# u32 row id, u32 k (little endian); the row has no .fnvnx.const record.
IDENT_RECORD = ('__attribute__((section(".fnvnx.ident"), used, aligned(4))) static const struct __attribute__((packed)) { unsigned id; unsigned k; }\n'
                '    fnvnx_ident_%(n)d = { %(n)d, %(k)d };\n')
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


IDENT = re.compile(r"identity:(\d+)$")


def param_name(params, k):
    """the name of parameter k of a plain C parameter list (the last identifier of the k-th comma-separated declaration); None when there is none"""
    ps = [x.strip() for x in params.split(",")] if params.strip() not in ("", "void") else []
    if k >= len(ps):
        return None
    m = re.fullmatch(r"[\w\s\*&:<>]*?[\s\*&](\w+)", ps[k])
    return m[1] if m else None


def const_value(v):
    v = v.strip()
    if v in ("false", "NULL", "nullptr"):
        return 0
    return int(v, 0) if re.fullmatch(r"-?(0[xX][0-9a-fA-F]+|\d+)[uUlL]*", v) else None


def main(argv):
    only = None
    dpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "verify", "declared_machine.tsv")
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
            if row["linkage"] != "C":
                errors.append("line %d: %s: a declared-machine row names a C-linkage symbol (the citation record names the assembler symbol)" % (n, row["symbol"]))
            cite = 'FNVNX_CITE(%s, "dm:%d");\n' % (row["symbol"], did)
            used.add(row["symbol"])
        idm = IDENT.match(row["value"].strip())
        if idm:
            pn = param_name(row["params"], int(idm[1]))
            if pn is None:
                errors.append("line %d: %s: identity:%s names no named parameter of `%s`" % (n, row["symbol"], idm[1], row["params"]))
                continue
            out.append("/* %s */\n%s%s %s(%s)%s\n{\n    return %s;\n}\n%s" % (
                row["source"], head, row["ret"], row["symbol"], row["params"], quals, pn, cite))
            consts.append(IDENT_RECORD % {"n": n, "k": int(idm[1])})
            continue
        out.append("/* %s */\n%s%s %s(%s)%s\n{\n    return %s;\n}\n%s" % (
            row["source"], head, row["ret"], row["symbol"], row["params"], quals, row["value"], cite))
        consts.append(RECORD % {"n": n, "ret": row["ret"], "value": row["value"]})
    if errors:
        for e in errors:
            sys.stderr.write("env_const: %s\n" % e)
        return 1
    with open(argv[2], "w", encoding="utf-8") as f:
        f.write("/* generated by manifest/gen_env_const.py from env_const.tsv - not a source file */\n\n")
        if used:
            f.write('#include <fnvnx/cite.h>\n\n')
        f.write("\n".join(out))
        if probe:
            f.write(SA_DEF + "".join(consts))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
