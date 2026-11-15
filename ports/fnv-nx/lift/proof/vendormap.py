#!/usr/bin/env python3
"""vendormap: the vendor (AMD) opcode map cells that name a CPUID feature, and the feature -> CPUID bit table, both read from the pinned AMD64 APM Vol. 3 (fnv-nx-build tools/amd-apm, pins.json "external").
    cpuid_rows(txt)             -> [(name, leaf, sub, reg, bit, citation)]  every one-bit field of the CPUID function tables of APM Vol. 3 Appendix E (the one source of the feature -> bit rows)
    table31(txt)                -> {flag: {(reg, bit)}}                      APM Vol. 3 Table 3-1 "Instruction Support Indicated by CPUID Feature Bits": which features gate instructions
    subsets(pdf, txt, feats)    -> {mnemonic: (feature, cite)}       Appendix D Table D-1
    mapcells(pdf, txt, names)   -> {(opcode, prefix): (feature, citation)}  cells of the two-byte opcode maps (Tables A-3, A-4) whose first line is a feature mnemonic of Table 3-1
The rule that a cell belongs to a feature is only: its first line, trademark signs removed, equals a feature mnemonic of Table 3-1 that Appendix E defines (gating). No feature name is written here."""
import re
import sys

LEAF = re.compile(r'([0-9A-F]{4})_([0-9A-F]{4})h')


def norm(s):
    return ' '.join((s or '').replace('™', '').replace('®', '').replace('“', '').replace('”', '').split())


def page_of(txt_path, pat, also=None):
    pages = open(txt_path, encoding='utf-8', errors='replace').read().split('\x0c')
    return [i for i, p in enumerate(pages) if re.search(pat, p) and (also is None or also in p)]


CPUID_HEAD = re.compile(r'^CPUID Fn([0-9A-F]{4})_([0-9A-F]{4})_(E[ABCD]X)(_x[0-9A-F]+)?(?=\s|$)')
CPUID_BITS = re.compile(r'^ {0,6}(\d{1,2})(:\d{1,2})?\s{2,}(\w[\w\.\-]*)')


def cpuid_rows(txt):
    """-> [(name, leaf, sub, reg, bit, citation)]: every one-bit named field of the CPUID function tables of APM Vol. 3 Appendix E ("Obtaining Processor Information Via the CPUID Instruction"; a section `CPUID FnXXXX_XXXX_REG[_xS] title`,
    its table rows `bit  FieldName  description`).  One rule over the whole appendix: a row is a feature bit iff its bit column is a single bit, its name is not the reserved dash, and its function has a fixed leaf and sub-leaf (`_xN` of a literal N; `_x[N:0]` is not an address).
    The first text line of the appendix after the table of contents is its heading; it ends at the next Appendix heading.  The citation names the PDF page index and the section."""
    pages = open(txt, encoding='utf-8', errors='replace').read().split('\x0c')
    start = [i for i, p in enumerate(pages) if re.search(r'^Appendix E Obtaining Processor Information Via\s*$', p, re.M)]
    if not start:
        return []                                      # a volume without the CPUID appendix defines no bit rows
    assert len(start) == 1, 'Appendix E heading must occur once outside the table of contents'
    end = next((i for i in range(start[0] + 1, len(pages)) if re.search(r'^Appendix [F-Z] ', pages[i], re.M)), len(pages))
    out = []; cur = None
    for pi in range(start[0], end):
        for ln in pages[pi].split('\n'):
            if ln.startswith('CPUID Fn'):
                m = CPUID_HEAD.match(ln)
                cur = None if not m or (m.group(4) is None and ln[m.end():m.end() + 1] == '[') else (int(m.group(1) + m.group(2), 16), int(m.group(4)[2:], 16) if m.group(4) else 0, m.group(3), ln.strip())
                continue
            m = CPUID_BITS.match(ln)
            if m and cur and not m.group(2) and m.group(3) != '\u2014':
                out.append((m.group(3), cur[0], cur[1], cur[2], int(m.group(1)),
                            'APM Vol. 3 Appendix E (PDF page index %d), "%s": bit %s %s' % (pi, cur[3], m.group(1), m.group(3))))
    return out


def table31(txt):
    """-> {feature flag mnemonic: {(reg, bit)}}: the `Feature Flag` column of APM Vol. 3 Table 3-1 "Instruction Support Indicated by CPUID Feature Bits" (rows `... REG[bit]  FLAG`, the tail of a row's line; the first-column instruction mnemonics are the Table 3-1 rows' left cells).
    Table 3-1 names which features gate instructions; the bit of each is defined by Appendix E (cpuid_rows)."""
    pages = open(txt, encoding='utf-8', errors='replace').read().split('\x0c')
    out = {}
    for p in pages:
        if not re.search(r'Table 3-1\.\s+Instruction Support Indicated by CPUID Feature Bits', p) or 'Feature Flag' not in p:
            continue
        for ln in p.split('\n'):
            m = re.search(r'(EAX|EBX|ECX|EDX)\[(\d+)\]\s+(\S+)\s*$', ln)
            if m:
                out.setdefault(m.group(3), set()).add((m.group(1), int(m.group(2))))
    return out


def subsets(pdf, txt, feats):
    """APM Appendix D Table D-1 "Feature Flags for Instruction / Instruction Subset Support": {instruction mnemonic: (feature, citation)} for every mnemonic that the `Instruction or Subset` cells of the rows of exactly one Table 3-1 feature list (the Feature Flag column is the row's feature;
    a cell lists instructions and subsets separated by `,` `/` or lines; a piece that is one token without spaces is a mnemonic, e.g. `LZCNT`, `3DNow!`; a mnemonic listed under two features names none)"""
    found = {}
    for pi in page_of(txt, r'Table D-1\.\s+Feature Flags for Instruction / Instruction Subset Support'):
        for t in pdf.pages[pi].find_tables():
            for r in t.extract():
                if len(r) < 4 or norm(r[0]) not in feats:
                    continue
                for piece in re.split(r'[,/\n]', r[1] or ''):
                    p = norm(piece)
                    if re.fullmatch(r'[A-Za-z0-9][\w\.!]*', p):
                        found.setdefault(p, {})[norm(r[0])] = pi
    return {m: (next(iter(d)), 'APM Vol. 3 Appendix D Table D-1 (PDF page index %d): %s is listed under feature flag %s' % (next(iter(d.values())), m, next(iter(d)))) for m, d in found.items() if len(d) == 1}


def mapcells(pdf, txt, names):
    """-> {(opcode byte after 0F, prefix): (feature mnemonic, citation)} for the two-byte maps of Appendix A (Tables A-3, A-4: columns are the low nibble, rows the high nibble, a prefix column beside).
    names: {cell first line: feature mnemonic}; a cell that is itself a feature mnemonic (`3DNow!`) or an instruction mnemonic whose Table D-1 row names one feature (`FEMMS`)"""
    out = {}
    for pi in page_of(txt, r'Table A-[34]\.\s+Secondary Opcode Map \(Two-byte Opcodes\), Low Nibble'):
        for t in pdf.pages[pi].find_tables():
            rows = t.extract()
            hdr = [norm(c) for c in rows[0]]
            if hdr[:2] != ['Prefix', 'Nibble1']:
                continue
            cols = {j: int(h, 16) for j, h in enumerate(hdr) if re.fullmatch(r'[0-9A-F]', h)}
            row = pfx = None
            for r in rows[1:]:
                if norm(r[0]):
                    pfx = norm(r[0])
                if re.fullmatch(r'[0-9A-F]', norm(r[1])):
                    row = int(norm(r[1]), 16)
                for j, col in cols.items():
                    first = norm((r[j] or '').split('\n')[0])
                    if row is not None and first in names:
                        out[(row * 16 + col, pfx)] = (names[first], 'APM Vol. 3 Appendix A two-byte opcode map (PDF page index %d): 0F %02X cell = "%s"%s' % (pi, row * 16 + col, first, '' if names[first] == first else ' (feature from Table D-1)'))
    return out


SUPPORT = re.compile(r'Support\s+for\s+(?:this|the\s+\S+)\s+instruction(?:\s+(?:sub)?set)?\s+is\s+indicated\s+by\s+CPUID\s+Fn[0-9A-F_]+_E[ABCD]X\[(\w+)\]')
OPROW = re.compile(r'^\s*\S+(?:\s+[^\s]+){0,4}?\s{2,}((?:(?:66|F2|F3)\s+)?)0F\s+([0-9A-F]{2})(?=\s|$)')


def instrcells(txt, known):
    """-> {(opcode byte after 0F, prefix): (feature, citation)}: the instruction reference of an AMD volume that defines instructions (Vol. 5; the same rule reads any such volume).  A table `Mnemonic  Opcode  Description` is preceded by its instruction's text; the feature of
    its rows is the one that text names in `Support for this instruction [sub]set is indicated by CPUID FnXXXX_XXXX_REG[Feature]` when Appendix E defines that name (known: the names of cpuid_rows).  A row whose opcode is `[66|F2|F3] 0F xx` is the two-byte cell (xx, prefix; `n/a` for none).
    A section with no such sentence, or a name Appendix E does not define, gives no cell; rows of other maps (x87, 0F 38, 0F 3A) are not two-byte cells."""
    pages = open(txt, encoding='utf-8', errors='replace').read().split('\x0c')
    out = {}
    for pi, p in enumerate(pages):
        lines = p.split('\n')
        for j, ln in enumerate(lines):
            if not re.match(r'^Mnemonic\s+Opcode\s+Description', ln):
                continue
            # the text before the table: back to the previous `rFLAGS Affected` / `Related Instructions` marker, across pages
            ctx = []; k = j; q = pi; back = list(lines[:j])
            while True:
                for t in reversed(back):
                    if re.match(r'^(rFLAGS Affected|Related Instructions)\s*$', t):
                        break
                    ctx.append(t)
                else:
                    if q > 0 and len(ctx) < 400:
                        q -= 1; back = pages[q].split('\n'); continue
                break
            m = SUPPORT.findall(' '.join(reversed(ctx)))
            feats = {f for f in m if f in known}
            if len(feats) != 1:
                continue
            f = next(iter(feats))
            for t in lines[j + 1:j + 80]:
                if re.match(r'^(rFLAGS Affected|Related Instructions)\s*$', t):
                    break
                m = OPROW.match(t)
                if m and m.group(2) not in ('38', '3A'):
                    byte = int(m.group(2), 16); pfx = m.group(1).strip() or 'n/a'
                    out[(byte, pfx)] = (f, 'APM Vol. 5 instruction reference (PDF page index %d): `%s` is a %s instruction (Support ... is indicated by CPUID ...[%s])' % (pi, t.strip()[:60], f, f))
    return out


def gating(txt, rows):
    """the feature names of the vendor layer: the Table 3-1 flags that Appendix E defines as a bit under the same name (a flag that Table 3-1 prints under another spelling is not a name here; its bit is in the table by its Appendix E name)"""
    t31 = table31(txt); known = {r[0] for r in rows}
    return {n for n in t31 if n in known}


def dump(rows):
    return ''.join('%s\t%08X\t%d\t%s\t%d\t%s\n' % f for f in sorted(rows))


def layer(volumes):
    """volumes: [(pdf path, txt path)] of every pinned AMD volume that defines bits or instructions (pins.json `external`, Vol. 3 and Vol. 5).  -> (rows, cells): the Appendix E CPUID bit rows of all volumes, and the vendor map cells that name a feature:
    the union over the volumes of mapcells (Appendix A maps, names from Table 3-1 and Table D-1) and instrcells (instruction references).  A cell that two volumes give different features is an error, not a choice."""
    import pdfplumber
    rows = []
    for _, txt in volumes:
        rows += cpuid_rows(txt)
    known = {r[0] for r in rows}; cells = {}
    for pdf, txt in volumes:
        got = {}
        feats = gating(txt, rows)
        if feats:
            p = pdfplumber.open(pdf); sub = subsets(p, txt, feats)
            got.update(mapcells(p, txt, dict({x: x for x in feats}, **{m: v[0] for m, v in sub.items()})))
        for k, v in instrcells(txt, known).items():
            got.setdefault(k, v)
        for k, v in got.items():
            if cells.setdefault(k, v)[0] != v[0]:
                raise SystemExit('vendor cell 0F %02X %s: %s and %s' % (k[0], k[1], cells[k][0], v[0]))
    return rows, cells


if __name__ == '__main__':
    import pdfplumber
    vols = [(a, a[:-4] + '.txt') for a in sys.argv[1:]]            # one pdf per pinned AMD volume, txt beside it; the first holds Table 3-1 / D-1
    rows, cells = layer(vols)
    sys.stdout.write(dump(rows))
    for k, v in sorted(cells.items()):
        sys.stdout.write('cell\t0F%02X\t%s\t%s\n' % (k[0], k[1], v[0]))
    feats = gating(vols[0][1], rows)
    for m, v in sorted(subsets(pdfplumber.open(vols[0][0]), vols[0][1], feats).items()):
        sys.stdout.write('subset\t%s\t%s\n' % (m, v[0]))
