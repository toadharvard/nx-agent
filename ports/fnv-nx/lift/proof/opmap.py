#!/usr/bin/env python3
"""opmap: the opcode maps of Intel SDM Vol. 2 Appendix A (Tables A-2, A-3 and the group table A-6), read from the PDF's own table cells (pdfplumber), not from text anchors.
    lookup(maps, mandatory_prefix, body) -> (name, citation) | None
A form that the instruction pages do not list is 'defined by the opcode map' iff its cell is non-empty:
  - one-byte / two-byte (0F xx) opcode with no row in Table A-6: the cell of Table A-2 / A-3 at (opcode, prefix row) is non-empty and is not an escape, a group reference or a VEX/EVEX entry;
  - an opcode that Table A-6 lists (80-83, C0,C1, F6,F7, 0F 00, ...): the A-6 cell of (opcode, ModRM.reg) is non-empty in some row whose pfx is blank or equal to the form's mandatory prefix.
Cells of Table A-2 / A-3 are addressed by the header digit of their column and the hex digit of the row (the band of stacked pfx rows, np/66/F3/F2, belongs to the row digit of its first line)."""
import re

NONCELL = re.compile(r'escape|ESC|Grp|VEX|EVEX|3DNow', re.I)


def pages_of(txt_path):
    pages = open(txt_path, encoding='utf-8', errors='replace').read().split('\x0c')
    pick = lambda pat: [i for i, p in enumerate(pages) if re.search(pat, p)]
    return pick(r'Table A-2\. One-byte Opcode Map'), pick(r'Table A-3\. Two-byte Opcode Map'), pick(r'Table A-6\. Opcode Extensions for One- and Two-byte')


def cell(c): return ' '.join((c or '').split())


def lines_of(c): return [' '.join(x.split()) for x in (c or '').split('\n') if x.strip()]


def load(pdf_path, txt_path):
    import pdfplumber
    a2, a3, a6 = pages_of(txt_path); pdf = pdfplumber.open(pdf_path); one, two, groups = {}, {}, []
    tabs = [(pi, t.extract()) for pi in a2 + a3 for t in pdf.pages[pi].find_tables()]
    for pi, rows in tabs:
        hdr = rows[0]
        if not any(re.fullmatch(r'[0-9A-F]', cell(h)) for h in hdr): continue
        haspfx = 'pfx' in hdr; cols = {j: int(h, 16) for j, h in enumerate(hdr) if re.fullmatch(r'[0-9A-F]', cell(h))}
        row = None
        for r in rows[1:]:
            if re.fullmatch(r'[0-9A-F]', cell(r[0])): row = int(cell(r[0]), 16)
            pfx = cell(r[1]) if haspfx else ''
            for j, col in cols.items():
                t = lines_of(r[j])
                if t and row is not None: (two if haspfx else one)[(row * 16 + col, pfx)] = (t, pi)
    for pi in a6:
        for r in pdf.pages[pi].find_tables()[0].extract():
            if cell(r[0]) in ('Opcode', '') and cell(r[1]) in ('Group', ''): 
                if cell(r[0]) == '' and groups and cell(r[1]) == '':
                    groups.append((groups[-1][0], groups[-1][1], cell(r[3]), [cell(c) for c in r[4:12]], pi))
                continue
            lab = cell(r[0]); two_b = lab.startswith('0F ')
            ops = [int(x, 16) for x in re.findall(r'\b([0-9A-F]{2})\b', lab[3:] if two_b else lab)]
            if re.search(r'\b([0-9A-F]{2})-([0-9A-F]{2})\b', lab):
                a, b = re.search(r'\b([0-9A-F]{2})-([0-9A-F]{2})\b', lab).groups(); ops = list(range(int(a, 16), int(b, 16) + 1))
            groups.append(((two_b, tuple(ops)), cell(r[1]), cell(r[3]), [cell(c) for c in r[4:12]], pi))
    return {'one': one, 'two': two, 'groups': groups}


def lookup(maps, mand, body):
    two_b = body[:1] == b'\x0f'; ob = body[1] if two_b else body[0]; rest = body[2:] if two_b else body[1:]
    rows = [g for g in maps['groups'] if g[0][0] == two_b and ob in g[0][1]]
    if rows:
        reg = (rest[0] >> 3) & 7 if rest else None
        for g in rows:
            if reg is not None and g[3][reg] and g[2] in ('', mand or ''):
                return 'Table A-6 Group %s' % g[1], 'Table A-6 (PDF page index %d): %s /%d = %s' % (g[4], ' '.join('%02X' % b for b in body[:1 + two_b]), reg, g[3][reg])
        return None
    t = (maps['two'] if two_b else maps['one']).get((ob, mand or ''))
    real = [x for x in t[0] if not NONCELL.search(x)] if t else []
    if real:
        return 'Table A-%d' % (3 if two_b else 2), 'Table A-%d (PDF page index %d): %s = %s' % (3 if two_b else 2, t[1], ' '.join('%02X' % b for b in body[:1 + two_b]), ' '.join(real))
    return None
