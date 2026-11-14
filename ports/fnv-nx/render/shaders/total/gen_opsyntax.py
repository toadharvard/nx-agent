#!/usr/bin/env python3
"""gen_opsyntax.py: op_syntax.tsv, the operand structure of every D3D9 shader-assembly instruction, read from the structured Syntax line of its
documentation page. One row per entry of sm_table.json "opcodes" (key stage:op).

    python3 -I gen_opsyntax.py PAGES_DIR SM_TABLE_JSON WINE_9_0_TREE OUT_TSV [--check]

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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sm_xref import MODELS, in_range

HEADER = ("key\topnum\tmodels\tn_dst\tn_src\tn_other\tsyntax\tpage\tpage_sha256\tstatus"
          "\tsrc2\tw_dst\tw_src\tw_models\tagree")
WINE_FILE = "dlls/wined3d/shader_sm1.c"
WROW = re.compile(r"\{WINED3D_SM1_OP_(\w+),\s*(\d+),\s*(\d+),\s*\w+,\s*(WINED3D_SHADER_VERSION\(\d,\s*\d\)|0),\s*(WINED3D_SHADER_VERSION\(\d,\s*\d\)|-1|0)\s*\}")
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


def wine_rows(wine):
    """{(stage, opnum): [(dst, src, lo, hi, line)]} of the opcode tables of wined3d shader_sm1.c (the structured source: dst count, param count,
    min/max version). src = param count - dst count. 0 / -1 for a version = unbounded."""
    text = open(os.path.join(wine, WINE_FILE)).read()
    en = {m.group(1): int(m.group(2), 16) for m in re.finditer(r"WINED3D_SM1_OP_(\w+)\s*=\s*(0x[0-9a-fA-F]+)", text)}
    out = {}
    for st, tab in (("vs", "vs_opcode_table"), ("ps", "ps_opcode_table")):
        a = text.index(tab + "[]")
        b = text.index("};", a)
        for m in WROW.finditer(text, a, b):
            def ver(x):
                k = re.match(r"WINED3D_SHADER_VERSION\((\d),\s*(\d)\)", x.strip())
                return (int(k.group(1)), int(k.group(2))) if k else None
            ln = text.count("\n", 0, m.start()) + 1
            out.setdefault((st, en[m.group(1)]), []).append((int(m.group(2)), int(m.group(3)) - int(m.group(2)), ver(m.group(4)), ver(m.group(5)), ln))
    return out


def page_rows(pages, table):
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


def rows(pages, table, wine):
    """page rows + the wined3d record. Rule: both present -> counts (dst; src = page src + other operands) and the model range must agree, else the
    row is red (status `disagree`); page without Syntax line -> the wined3d row is the record (status `wine-only`); neither -> red (`unparsed:`)."""
    prs = page_rows(pages, table)
    wr = wine_rows(wine)
    by_op = {}                      # (stage, opnum) -> models union of the sm_table keys
    for r in prs:
        st = r[0].split(":")[0]
        by_op.setdefault((st, r[1]), set()).update(m for m in r[2].split(",") if m)
    out = []
    for r in prs:
        key, opnum, models = r[0], r[1], r[2]
        st = key.split(":")[0]
        ents = wr.get((st, opnum), [])
        K = [m for m in models.split(",") if m]
        # the model range wined3d gives for this opcode, and the (dst, src) per model of this row
        # an entry with min = max = 0 carries no version claim (wined3d's own spelling of "unbounded"); the range is compared only where
        # some entry of the opcode has a bound
        bounded = [e for e in ents if e[2] is not None or e[3] is not None]
        W = {m for m in MODELS if m[:2] == st and any(in_range(m, lo, hi) for _, _, lo, hi, _ in (bounded or ents))}
        per = {m: sorted({(d, sr) for d, sr, lo, hi, _ in ents if in_range(m, lo, hi)}) for m in K}
        lines = sorted({ln for *_, ln in ents})
        src2 = ",".join("%s:%d" % (WINE_FILE, ln) for ln in lines)
        if not ents:
            w = ("", "", "", "")
            out.append(tuple(r) + ("", "", "", "", "-"))
            continue
        union = by_op[(st, opnum)]
        models_ok = (set(K) <= W and union == W) if bounded else True
        counts = {c for m in K for c in per[m]}
        wmod = ",".join(sorted(W, key=MODELS.index))
        if len(counts) == 1 and models_ok:
            (wd, ws), = counts
        else:
            wd = ws = ""
        wtxt = lambda: "|".join("%s:%s" % (m, "+".join("%d/%d" % c for c in per[m]) or "-") for m in K)
        if r[-1] == "ok":
            page_c = (r[3], r[4] + r[5])
            agree = "yes" if models_ok and counts == {page_c} else "no"
            st2 = r[-1] if agree == "yes" else "disagree"
            out.append(tuple(r[:-1]) + (st2, src2, wd if wd != "" else wtxt(), ws, wmod, agree))
        else:
            if models_ok and len(counts) == 1:
                out.append(tuple(r[:3]) + (wd, ws, 0) + tuple(r[6:-1]) + ("wine-only", src2, wd, ws, wmod, "-"))
            else:
                out.append(tuple(r[:-1]) + ("unparsed:%s; wine row not unique (%s)" % (r[-1][len("unparsed:"):], wtxt()), src2, "", "", wmod, "-"))
    # rows without a wined3d entry keep their page status
    return out


def main(argv):
    check = "--check" in argv
    a = [x for x in argv if not x.startswith("--")]
    if len(a) != 4:
        print(__doc__)
        return 1
    pages, table_path, wine, out = a
    table = json.load(open(table_path))
    rs = rows(pages, table, wine)
    text = HEADER + "\n" + "".join("\t".join(str(c) for c in r) + "\n" for r in rs)
    red = [r for r in rs if r[9] not in ("ok", "wine-only")]
    if check:
        same = os.path.isfile(out) and open(out).read() == text
        print("op_syntax: %s (%d rows, %d red)" % ("byte-equal" if same else "DIFFERS", len(rs), len(red)))
        return 0 if same and not red else 2
    with open(out, "w") as f:
        f.write(text)
    print("op_syntax: %d rows, %d red" % (len(rs), len(red)))
    from collections import Counter
    print("  agree: %s" % dict(sorted(Counter(r[14] for r in rs).items())))
    print("  status: %s" % dict(sorted(Counter(r[9].split(":")[0].split(";")[0] for r in rs).items())))
    for r in red:
        print("  RED %s: %s [%s] page=%s/%s/%s wine=%s/%s models=%s wine_models=%s" % (r[0], r[9], r[10], r[3], r[4], r[5], r[11], r[12], r[2], r[13]))
    return 2 if red else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
