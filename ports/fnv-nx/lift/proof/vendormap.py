#!/usr/bin/env python3
"""vendormap: the vendor (AMD) opcode map cells that name a CPUID feature, and the feature -> CPUID bit table, both read from the pinned AMD64 APM Vol. 3 (fnv-nx-build tools/amd-apm, pins.json "external").
    features(pdf, txt)          -> [(mnemonic, leaf, reg, bit, citation)]   APM Vol. 3 Table 3-1 "Instruction Support Indicated by CPUID Feature Bits" (cells of the PDF table)
    subsets(pdf, txt, feats)    -> {mnemonic: (feature, cite)}       Appendix D Table D-1
    mapcells(pdf, txt, names)   -> {(opcode, prefix): (feature, citation)}  cells of the two-byte opcode maps (Tables A-3, A-4) whose first line is a feature mnemonic of Table 3-1
    table(sdm_lines, apm)       -> {name: [(leaf, sub, reg, bit)]}          sdmfeat.flag_table  U  the APM features (one table that the partition reads)
The rule that a cell belongs to a feature is only: its first line, trademark signs removed, equals a feature mnemonic of Table 3-1. No feature name is written here."""
import re
import sys

LEAF = re.compile(r'([0-9A-F]{4})_([0-9A-F]{4})h')


def norm(s):
    return ' '.join((s or '').replace('™', '').replace('®', '').split())


def page_of(txt_path, pat, also=None):
    pages = open(txt_path, encoding='utf-8', errors='replace').read().split('\x0c')
    return [i for i, p in enumerate(pages) if re.search(pat, p) and (also is None or also in p)]


def features(pdf, txt):
    """-> [(mnemonic, leaf, reg, bit, citation)]; a row's blank cells continue the row above (Table 3-1 prints several features under one instruction)"""
    out = []; leaf = None
    for pi in page_of(txt, r'Table 3-1\.\s+Instruction Support Indicated by CPUID Feature Bits', 'CMPXCHG8B'):
        for t in pdf.pages[pi].find_tables():
            rows = t.extract()
            if [norm(c) for c in rows[0]] != ['Instruction', 'Register[Bit]', 'Feature Mnemonic', 'CPUID Function(s)']:
                continue
            for r in rows[1:]:
                if norm(r[3]):
                    leaf = [int(a + b, 16) for a, b in LEAF.findall(norm(r[3]))]
                m = re.fullmatch(r'(EAX|EBX|ECX|EDX)\[(\d+)\]', norm(r[1]))
                if not m or not norm(r[2]):
                    continue
                name = norm(r[2])
                for lf in leaf:
                    out.append((name, lf, m.group(1), int(m.group(2)), 'APM Vol. 3 Table 3-1 (PDF page index %d): %s %s[%s] CPUID %08Xh' % (pi, name, m.group(1), m.group(2), lf)))
    return out


def subsets(pdf, txt, feats):
    """APM Appendix D Table D-1 "Instruction Subsets and CPUID Feature Sets": {instruction mnemonic: (feature mnemonic, citation)} for every instruction whose subset cells (columns General-Purpose .. System) name exactly one
    feature mnemonic of Table 3-1 (a cell may list several sets: `X87, CMOVcc`; names that are not a Table 3-1 mnemonic, such as X87, are subsets and name no feature)"""
    out = {}
    for pi in page_of(txt, r'Table D-1\.\s+Instruction Subsets and CPUID Feature Sets'):
        for t in pdf.pages[pi].find_tables():
            for r in t.extract():
                if len(r) < 8 or not norm(r[0]) or norm(r[0]) == 'Mnemonic':
                    continue
                toks = {norm(x) for c in r[3:8] for x in re.split(r'[,\n]', c or '') if norm(x)}
                hit = sorted(toks & feats)
                if len(hit) == 1:
                    out[norm(r[0])] = (hit[0], 'APM Vol. 3 Appendix D Table D-1 (PDF page index %d): %s is in the %s set' % (pi, norm(r[0]), hit[0]))
    return out


def mapcells(pdf, txt, names):
    """-> {(opcode byte after 0F, prefix): (feature mnemonic, citation)} for the two-byte maps of Appendix A (Tables A-3, A-4: columns are the low nibble, rows the high nibble, a prefix column beside).
    names: {cell first line: feature mnemonic}; a cell that is itself a feature mnemonic (`3DNow!`) or an instruction mnemonic whose Table D-1 row names one feature (`FEMMS`)"""
    out = {}
    for pi in page_of(txt, r'Table A-[34]\.\s+Second Byte of Two-Byte Opcodes, Low Nibble'):
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


def table(flags, apm_features):
    out = {k: list(v) for k, v in flags.items()}
    for name, leaf, reg, bit, _ in apm_features:
        out.setdefault(name, []).append((leaf, 0, reg, bit))
    return out


def dump(feats):
    return ''.join('%s\t%08X\t%s\t%d\t%s\n' % f for f in sorted(feats))


if __name__ == '__main__':
    import pdfplumber
    pdf = pdfplumber.open(sys.argv[1]); f = features(pdf, sys.argv[2])
    sys.stdout.write(dump(f))
    feats = {x[0] for x in f}; sub = subsets(pdf, sys.argv[2], feats)
    for k, v in sorted(mapcells(pdf, sys.argv[2], dict({x: x for x in feats}, **{m: v[0] for m, v in sub.items()})).items()):
        sys.stdout.write('cell\t0F%02X\t%s\t%s\n' % (k[0], k[1], v[0]))
