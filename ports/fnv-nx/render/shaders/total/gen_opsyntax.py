#!/usr/bin/env python3
"""gen_opsyntax.py: op_syntax.tsv, the operand structure of every D3D9 shader-assembly instruction, read from the structured Syntax line of its
documentation page. One row per entry of sm_table.json "opcodes" (key stage:op).

    python3 -I gen_opsyntax.py PAGES_DIR SM_TABLE_JSON OUT_TSV [--check]

PAGES_DIR is the pinned external `msdocs-sm` (render/total/externals.json: tools/msdocs-sm, text pages of the D3D9 shader assembly reference;
the docs-d3d9 mirror holds no instruction page). The page of a row is named by the row's own `source` field in sm_table.json
("MS:dx9-graphics-reference-asm-instructions/<stem> (...)"), never by the opcode's name.

Rule (the whole class): the Syntax block of a page is the line(s) between the line "Syntax" and the next "where"/"Remarks" line; the first
non-empty line of it is `mnemonic[_mods] operand, operand ... |` (a trailing "|" is table residue). An operand token is classified by its
spelling in the page's own grammar: `dst`/`dest` (+ [.mask]) is a destination, `src`, `src0`.. a source, anything else is `other`
(it is kept verbatim in the `operands` column). Columns:
    key  opnum  models  n_dst  n_src  n_other  syntax  page  page_sha256  status
status is `ok`, or `unparsed:<reason>`: a page with no Syntax block, no mnemonic or an unreadable operand list is a RED row (exit 2). The
description text of a page is never read for a count.
Two runs over the same inputs are byte-equal (rows sorted by key, no clock, no path of the machine in the output).
"""
import hashlib, json, os, re, sys

HEADER = "key\topnum\tmodels\tn_dst\tn_src\tn_other\tsyntax\tpage\tpage_sha256\tstatus"
MN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\[[A-Za-z_]+\])?[A-Za-z0-9_]*$")


def syntax_line(text):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "Syntax":
            for ln2 in lines[i + 1:]:
                s = ln2.strip()
                if not s:
                    continue
                if re.match(r"^(where|remarks)\b", s, re.I):
                    return None
                return s
            return None
    return None


def parse(s):
    """(n_dst, n_src, n_other, operands) of a syntax line, or an error string."""
    s = re.sub(r"\s*\|\s*$", "", s.strip())
    parts = s.split(None, 1)
    if not parts:
        return "no mnemonic"
    if not MN.match(parts[0]):
        return "bad mnemonic %r" % parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest:
        return (0, 0, 0, "")
    ops = [o.strip() for o in rest.split(",")]
    if any(not o for o in ops):
        return "empty operand in %r" % rest
    d = sr = ot = 0
    for o in ops:
        base = re.sub(r"\[[^\]]*\]", "", o).strip()
        base = base.split(".")[0].strip()
        if re.fullmatch(r"(dst|dest)", base):
            d += 1
        elif re.fullmatch(r"src\d*", base):
            sr += 1
        else:
            ot += 1
    return (d, sr, ot, rest)


def rows(pages, table):
    out = []
    for key in sorted(table["opcodes"]):
        e = table["opcodes"][key]
        if e is None:
            continue
        m = re.search(r"asm-instructions/([A-Za-z0-9_\-]+)", e.get("source", ""))
        models = ",".join(e.get("models", []))
        if not m:
            out.append((key, e.get("opnum", ""), models, "", "", "", "", "", "", "unparsed:no page in source"))
            continue
        page = m.group(1) + ".txt"
        path = os.path.join(pages, page)
        if not os.path.isfile(path):
            out.append((key, e["opnum"], models, "", "", "", "", page, "", "unparsed:page missing"))
            continue
        raw = open(path, "rb").read()
        sha = hashlib.sha256(raw).hexdigest()
        sl = syntax_line(raw.decode("utf-8", "replace"))
        if sl is None:
            out.append((key, e["opnum"], models, "", "", "", "", page, sha, "unparsed:no Syntax line"))
            continue
        r = parse(sl)
        if isinstance(r, str):
            out.append((key, e["opnum"], models, "", "", "", sl, page, sha, "unparsed:" + r))
            continue
        d, sr, ot, ops = r
        out.append((key, e["opnum"], models, d, sr, ot, sl, page, sha, "ok"))
    return out


def main(argv):
    check = "--check" in argv
    a = [x for x in argv if not x.startswith("--")]
    if len(a) != 3:
        print(__doc__)
        return 1
    pages, table_path, out = a
    table = json.load(open(table_path))
    rs = rows(pages, table)
    text = HEADER + "\n" + "".join("\t".join(str(c) for c in r) + "\n" for r in rs)
    red = [r for r in rs if r[-1] != "ok"]
    if check:
        same = os.path.isfile(out) and open(out).read() == text
        print("op_syntax: %s (%d rows, %d red)" % ("byte-equal" if same else "DIFFERS", len(rs), len(red)))
        return 0 if same and not red else 2
    with open(out, "w") as f:
        f.write(text)
    print("op_syntax: %d rows, %d red" % (len(rs), len(red)))
    for r in red:
        print("  RED %s: %s" % (r[0], r[-1]))
    return 2 if red else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
