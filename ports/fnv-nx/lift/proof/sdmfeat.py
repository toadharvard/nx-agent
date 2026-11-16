#!/usr/bin/env python3
"""The CPUID feature-bit table (`features`), read from the Intel SDM (OWNER 10, ASM-009: the x86 ISA as documented in the SDM) and, for a form the SDM does not define, from the pinned AMD APM Vol. 3 (vendormap.py: a map cell that names a feature of its Table 3-1 that Appendix E defines).

    python3 -I sdmfeat.py features SDM.txt OUT.tsv --apm APM.pdf [--apm ...]   # the feature -> CPUID leaf/reg/bit table (SDM U APM, a page cite per row)

SDM.txt: `pdftotext -layout` of the Intel(R) 64 and IA-32 Architectures SDM, combined volumes (its sha256 is in every output header; the page numbers cited are those of that edition).

Source of each fact:
 * the opcode forms and their `CPUID Feature Flag`: the opcode tables of the instruction reference (Vol. 2A-2D).  A table with a `CPUID Feature Flag` column gives the flag in that column.  A table without that
   column (the older layout: `Opcode / Instruction / Op/En / 64-Bit Mode / Compat/Leg Mode`) names no flag in the row; the flags such a form needs are those its section's Exceptions text names as
   `#UD If CPUID.<leaf>:<reg>.<NAME>[Bit n] = 0` (collected per section as `exc_flags`).  A section that names none has no feature flag: the form is base.
 * the flag name -> (leaf, register, bit): the SDM's own CPUID chapter tables (Vol. 1 Ch. 21, `Leaf 01H ... Returned in ECX/EDX`, `Leaf 07H.00H ...`, `Leaf 80000001H ...`: rows `REG[n] NAME`).
 * the declared CPU: the declared CPU of verify/cpuid_model.json (cpumodel_bits.model_words, as rt/cpumodel.c answers it): a flag is present iff its leaf is <= the max leaf of its range and its bit is set in the answer (CLAUDE.md "The declared CPU").
 * a form with no row in the SDM opcode tables is an undefined opcode: #UD on every CPU (the 3DNow! 0F 0F family is AMD's; the Intel SDM defines no 0F 0F).  Its row is `undefined`.
 * the SDM states that a few forms execute as another legal instruction on a CPU without the flag (TZCNT/LZCNT as BSF/BSR, PREFETCHW as a hint).  Each such statement is cited in the row's `exec_as` column.
The capstone groups are not read here.  Rows are matched to a decoded form by (mandatory prefix, opcode bytes, ModRM.reg or fixed ModRM byte); an ambiguity (two rows with different flags) is an error, not a choice."""
import collections
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

PAGE = re.compile(r'Vol\. (\d[A-D]?) (\d+-\d+)\s*$')
HEAD = re.compile(r'^([A-Z][A-Za-z0-9/,\.\* \-]*?)—(.+?)\s*$')
HEXB = re.compile(r'^[0-9A-F]{2}$')
CPUIDREF = re.compile(r'CPUID\.(?:\(EAX=)?([0-9A-F]+)H?(?:,ECX=\d+H?\))?:?\s*(EAX|EBX|ECX|EDX)\.([A-Za-z0-9_\.]+)\s*\[\s*(?:[Bb]it\s*)?(\d+)\]')


def sha_file(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def load(path):
    return open(path, encoding='utf-8', errors='replace').read().replace('\x0c', '').split('\n')


def region(lines):
    """the instruction reference: from the first real AAA heading to the end of the text"""
    a = next(i for i, ln in enumerate(lines) if ln.startswith('AAA—ASCII Adjust After Addition') and i > 20000)
    return a


def title_leaf(title):
    """(leaf, sub) addressed by the title of a Table 21-N of the SDM's CPUID chapter (`Leaf 07H.01H ...`, `Leaf 07H Sub-Leaf (ECX=2) ...`, `CPUID.07H.02H ...`, `Leaf 01H ...` = sub 0), or None when the title names no fixed address
    (`Leaf 0DH.SUB-LEAVES`, `Leaf 18H.ECX >= 1`: a variable sub-leaf has no address, so none of its rows is a feature bit)"""
    m = re.match(r'(?:Leaf\s+|CPUID\.)([0-9A-F]+)H(?=[\s\.])(?:\.([0-9A-F]+)H(?=\s))?', title)
    if not m:
        return None
    if m.group(2):
        return int(m.group(1), 16), int(m.group(2), 16)
    if title[m.end():m.end() + 1] == '.':
        return None
    e = re.search(r'\(ECX=(\d+)\)', title)
    return int(m.group(1), 16), int(e.group(1)) if e else 0


def flag_rows(lines):
    """[(NAME, leaf, sub, reg, bit, citation)]: the rows `REG[n]   NAME` of the Intel SDM's CPUID-leaf tables (Vol. 1 ch. 21), each under the address of the Table 21-N title it sits under; a Table 21-N whose title names no fixed address
    resets the address (a row is never read under the address of an earlier table)"""
    out = []; leaf = None; title = ''
    for j, ln in enumerate(lines[20000:30000], 20000):
        m = re.match(r'\s*(Table 21-\d+)\.\s+(.*)', ln)
        if m:
            leaf = title_leaf(m.group(2).strip()); title = m.group(1)
            continue
        m = re.match(r'\s*(EAX|EBX|ECX|EDX)\[(\d+)\]\s+([A-Za-z0-9_\.]+)\s', ln)
        if m and leaf:
            out.append((m.group(3), leaf[0], leaf[1], m.group(1), int(m.group(2)), 'Intel SDM %s, %s: %s[%s] %s' % (title, page_after(lines, j), m.group(1), m.group(2), m.group(3))))
    return out


def unify(sdm_rows, apm_rows, mirrors=()):
    """-> [record]: ONE record per CPUID address (leaf, sub, reg, bit), {canonical, leaf, sub, reg, bit, sdm_name, apm_name, sdm_cite, apm_cite, mirror_of}.
    The canonical name is the Intel SDM's name where the SDM defines the bit, the APM's otherwise (the precedence of the opcode maps).  A vendor that gives one address two names is an error.
    `mirrors` are the APM's own statements (vendormap.cpuid_mirrors: the description `Same as CPUID FnXXXX_XXXX_REG[NAME]`) that a bit is the very bit of another function: the mirroring record takes the canonical name of its target and records `mirror_of`; the statement must
    name the target's APM name and the same bit, else it is an error.  Any other name that labels several addresses is a different CPUID fact (an XCR0 or XSS state bit that shares a feature's name, a bit repeated in another leaf) and gets the canonical `NAME@LEAF.SUB.REG[bit]`.
    Whether two vendors mean the same by one address is the architecture's own definition (the address); a spelling difference is not a disagreement."""
    by = collections.defaultdict(lambda: {'sdm': {}, 'apm': {}})
    for src, rows in (('sdm', sdm_rows), ('apm', apm_rows)):
        for n, l, s_, r, b, c in rows:
            d = by[(l, s_, r, b)][src]
            d.setdefault(n, c)
    recs = {}
    for (l, s_, r, b), v in sorted(by.items()):
        for src in ('sdm', 'apm'):
            if len(v[src]) > 1:
                raise SystemExit('%s defines two names at %08X.%d %s[%d]: %s' % (src.upper(), l, s_, r, b, sorted(v[src])))
        sn, sc = next(iter(v['sdm'].items()), ('', ''))
        an, ac = next(iter(v['apm'].items()), ('', ''))
        recs[(l, s_, r, b)] = {'canonical': sn or an, 'leaf': l, 'sub': s_, 'reg': r, 'bit': b, 'sdm_name': sn, 'apm_name': an, 'sdm_cite': sc, 'apm_cite': ac, 'mirror_of': ''}
    root = {}
    for l, s_, r, b, tl, ts, tr, tn in mirrors:
        a, t = recs.get((l, s_, r, b)), recs.get((tl, ts, tr, b))
        if not a or not t or t['apm_name'] != tn or a['apm_name'] != tn:
            raise SystemExit('APM mirror statement %08X.%d %s[%d] -> %08X.%d %s[%s] does not match the Appendix E rows' % (l, s_, r, b, tl, ts, tr, tn))
        a['mirror_of'] = '%08X.%d.%s[%d]' % (tl, ts, tr, b); a['canonical'] = t['canonical']
        root[(l, s_, r, b)] = (tl, ts, tr, b)
    labels = collections.defaultdict(set)
    for k, rec in recs.items():
        labels[rec['canonical']].add(root.get(k, k))                            # a mirror is the same fact as its target
    for k, rec in recs.items():
        g = root.get(k, k)
        if len(labels[rec['canonical']]) > 1:
            rec['canonical'] = '%s@%08X.%d.%s[%d]' % ((rec['canonical'],) + g[:3] + g[3:])
    return list(recs.values())




def cmd_features(sdm, apm, out):
    """the feature -> CPUID bit table of the partition: Intel SDM Vol. 1 ch. 21 leaf tables U AMD APM Vol. 3 Appendix E CPUID function tables, ONE row per address (leaf, sub, reg, bit) with the canonical name, both vendor names and both citations"""
    from proof import vendormap
    apm = apm if isinstance(apm, list) else [apm]
    arows = []; mirrors = []
    for a in apm:
        arows += vendormap.cpuid_rows(a.replace('.pdf', '.txt')); mirrors += vendormap.cpuid_mirrors(a.replace('.pdf', '.txt'))
    recs = unify(flag_rows(load(sdm)), arows, mirrors)
    with open(out, 'w', newline='\n') as fh:
        fh.write('# fnvnx-cpuid-feature-bits/2 sdm_sha256=%s apm_sha256=%s\n# canonical\tleaf\tsub\treg\tbit\tsdm_name\tapm_name\tsdm_citation\tapm_citation\tmirror_of\n' % (sha_file(sdm.replace('.txt', '.pdf')), ','.join(sha_file(a) for a in apm)))
        for r in recs:
            fh.write('%s\t%08X\t%d\t%s\t%d\t%s\t%s\t%s\t%s\t%s\n' % (r['canonical'], r['leaf'], r['sub'], r['reg'], r['bit'], r['sdm_name'], r['apm_name'], r['sdm_cite'], r['apm_cite'], r['mirror_of']))
    sys.stdout.write('features %d rows\n' % len(recs))
    return 0


def declared_cpu(bits_py=None, model_json=None):
    """{(leaf, reg): value} of the declared CPU, plus the two max leaves: the feature words are cpumodel_bits.model_words of verify/cpuid_model.json, the one derivation that tools/gen_cpuref.py
    (the generator of rt/cpumodel_tab.inc and fnvl_cpuref.h) and the lifter's feature gate call; the max leaves are that record's too.  Nothing is compiled.  Only the fields a feature flag can
    sit in are given (leaf 1 ECX/EDX, 0x80000001 ECX/EDX, the max leaves); a leaf above its range's max is absent (`present` tests it), and fields the platform owns (leaf 1 EBX, leaf 4 EAX) are not features."""
    import importlib.machinery, importlib.util
    spec = importlib.util.spec_from_loader('cpumodel_bits', importlib.machinery.SourceFileLoader('cpumodel_bits', bits_py or os.path.join(os.path.dirname(HERE), 'fnvlift', 'cpumodel_bits.py')))   # BITS_PY / MODEL_JSON: explicit files (the sailmodel root mounts them); the defaults are the tree's own
    cpumodel_bits = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cpumodel_bits)
    mod = json.load(open(model_json or os.path.join(HERE, '..', '..', 'verify', 'cpuid_model.json')))
    w = cpumodel_bits.model_words(mod); I = cpumodel_bits.I
    return {(0, 'EAX'): mod['max_basic'], (0x80000000, 'EAX'): I(mod['max_ext']), (1, 'ECX'): w['leaf1_ecx'], (1, 'EDX'): w['leaf1_edx'],
            (0x80000001, 'ECX'): w['ext1_ecx'], (0x80000001, 'EDX'): w['ext1_edx']}






def parse_opcode(text):
    """the opcode cell of a row -> dict(vex, pfx, bytes, plus_r, digit) or None"""
    t = text.strip()
    d = {'vex': bool(re.match(r'(VEX|EVEX|XOP)', t)), 'pfx': None, 'bytes': [], 'plus_r': False, 'digit': None, 'rexw': 'REX.W' in t, 'modrm': False}
    if d['vex']:
        return d
    t = t.replace(',', ' '); t = re.sub(r'(?<=[0-9A-F]{2})(?=/)', ' ', t)
    t = re.sub(r'REX\.W\s*\+?', '', t); t = re.sub(r'REX\s*\+?', '', t)
    if re.search(r'\+\s*(r[bwdo]|i)\b', t):
        d['plus_r'] = True
        t = re.sub(r'\+\s*(r[bwdo]|i)\b', ' ', t)
    t = t.replace('+', ' ')
    toks = t.split()
    if toks and toks[0] in ('NP', '66', 'F2', 'F3'):
        d['pfx'] = toks.pop(0)
    elif toks and toks[0] == 'NFx':
        toks.pop(0)
    for i, x in enumerate(toks):
        if HEXB.match(x):
            d['bytes'].append(int(x, 16))
        elif x in ('+rb', '+rw', '+rd', '+ro', '+i') or x.startswith('+r') or x.startswith('+i'):
            d['plus_r'] = True
        elif re.match(r'^/[0-7]$', x):
            d['digit'] = int(x[1]); d['modrm'] = True
            break
        elif x == '/r':
            d['modrm'] = True
            break
        else:
            break
    return d if d['bytes'] else None




def modclass(instr):
    """the ModRM.mod condition that the operand text of a row states:
    'mem': an operand is memory-only (m, m16int, m128, mem, mib ...) and none is an `r/m` / `x/m` operand;
    ('reg' is not inferred: the instruction cell of a table can be cut by its column width, and an operand list read as register-only from a cut text would be a guess);
    'any': everything else, including every text that may be cut by the table's column width (an operand cell that wraps is not read as register-only)."""
    t = instr.strip()
    if ' ' not in t or t.endswith(','):
        return 'any'
    ops = [re.sub(r'\{.*?\}', '', o).strip() for o in t.split(None, 1)[1].split(',')]
    if any('/' in o for o in ops):
        return 'any'
    if any(re.match(r'^m(?!m\d?$)\w*(&\d+)?$', o) for o in ops):
        return 'mem'
    return 'any'


def sections(lines):
    """[(heading, start line, end line)] of the instruction reference"""
    a = region(lines); out = []
    for i in range(a, len(lines)):
        ln = lines[i]
        if PAGE.search(ln):
            continue
        m = HEAD.match(ln)
        if m and not ln.startswith(' ') and 'Vol.' not in ln and len(ln) < 160 and re.match(r'^[A-Za-z][A-Za-z0-9/,\.\* \-]*—[A-Z]', ln) and not ln.rstrip().endswith(('.', '”')):
            out.append([ln.strip(), i, None])
    for k in range(len(out)):
        out[k][2] = out[k + 1][1] if k + 1 < len(out) else len(lines)
    return out


ROWSTART = re.compile(r'^(NP\b|NFx\b|66\b|F2\b|F3\b|VEX\.|EVEX\.|XOP\.|REX\b|[0-9A-F]{2}(?![A-Za-z0-9])|\*)')
STOP = ('Instruction Operand', 'Description', 'NOTE', 'Op/En', 'Opcode', 'Table', 'Operation', 'Flags Affected', 'Op /')


def rows_of(lines, sec):
    """[(opcode cell, instruction cell, flag cell, first line, fmt)] of the opcode tables of a section.  fmt A: the table has a `CPUID Feature Flag` column (the flag cell is its text); fmt B: it has none (flag cell '')."""
    out = []; i = sec[1]
    while i < sec[2]:
        if not lines[i].lstrip().startswith('Opcode') or lines[i].lstrip().startswith('Opcode Ext'):
            i += 1; continue
        hdr = [lines[i]]
        for x in lines[i + 1:i + 5]:
            if ROWSTART.match(x.strip()) and not x.strip().startswith(('Instruction', 'Opcode')): break
            hdr.append(x)
        htxt = ' '.join(hdr)
        fmt = 'A' if re.search(r'CPUID', htxt) and 'Description' in htxt else ('B' if 'Instruction' in htxt and ('Compat' in htxt or '64' in htxt) else None)
        if fmt is None:
            i += 1; continue
        h0 = hdr[0]
        if fmt == 'B' and h0.lstrip().startswith('Opcode/'):
            fmt = 'C'                                                  # stacked Opcode/Instruction cell, no CPUID flag column
        if fmt in ('A', 'C'):
            cf = next((h.index('CPUID') for h in hdr if 'CPUID' in h), None)
            cd = next(h.index('Description') for h in hdr if 'Description' in h)
            cos = [m.start() for h in hdr for m in [re.search(r'\bOp\b|\bOp/', h)] if m]
            if not cos or (fmt == 'A' and cf is None):
                i += 1; continue
            co = min(cos)
        else:
            co = next(h.index('Instruction') for h in hdr if 'Instruction' in h); cf = cd = None
            ie = min([m.start() for h in hdr for m in [re.search(r'\bOp\b|\bOp/|64-Bit|64/32', h)] if m and m.start() > co] or [co + 40])
        j = i + 1
        while j < sec[2] and not ROWSTART.match(lines[j].strip()):
            if j > i + 8: break
            j += 1
        cur = None
        while j < sec[2]:
            l2 = lines[j]; st = l2.strip()
            if PAGE.search(l2) or HEAD.match(l2) and not l2.startswith(' ') and 'Vol.' in l2:
                j += 1; continue
            if not st:
                k = j + 1
                while k < sec[2] and (not lines[k].strip() or PAGE.search(lines[k])): k += 1
                if k < sec[2] and ROWSTART.match(lines[k].strip()) and not lines[k].lstrip().startswith(STOP):
                    j = k; continue
                if k < sec[2] and fmt in ('A', 'C') and cur is not None and lines[k].startswith(' ') and not lines[k].lstrip().startswith(STOP) and not HEAD.match(lines[k]):
                    j = k; continue
                break
            if st.startswith(STOP):
                break
            first = l2[:co].strip()
            if ROWSTART.match(first) and first and l2[:1] in (' ',) or ROWSTART.match(first) and first == st[:len(first)] and l2[0] != ' ':
                if cur: out.append(tuple(cur) + (fmt,))
                if fmt in ('A', 'C'):
                    cur = [first, '', l2[cf:cd].strip() if cf is not None and len(l2) > cf else '', j]
                else:
                    cur = [first, re.split(r'\s{3,}', l2[co:ie].strip())[0], '', j]
                    out.append(tuple(cur) + (fmt,)); cur = None
            elif cur is not None and fmt in ('A', 'C'):
                if not cur[1]: cur[1] = first
                if cf is not None and len(l2) > cf and l2[cf:cd].strip(): cur[2] = (cur[2] + ' ' + l2[cf:cd].strip()).strip()
            j += 1
        if cur: out.append(tuple(cur) + (fmt,))
        i = j + 1
    return out


MODEHEAD = re.compile(r'^(Real-Address Mode|Real Address Mode|Virtual-8086 Mode|Virtual 8086 Mode|Protected Mode|Compatibility Mode|64-Bit Mode|Exceptions \(All|Other Exceptions|Numeric Exceptions|SIMD Floating-Point Exceptions|Intel C/C\+\+|Flags Affected|Operation|Description|Opcode|Instruction Operand)')


def exc_flags(lines, sec):
    """the CPUID flags that the exceptions of the section name in a `#UD ... CPUID.. = 0` line, for the modes the guest executes in (protected / compatibility / all-modes blocks; the 64-Bit Mode, Real-Address and Virtual-8086 blocks are not read: the declared machine runs 32-bit protected-mode code)"""
    blocks = []; cur = None
    for l in lines[sec[1]:sec[2]]:
        m = MODEHEAD.match(l)
        if m:
            cur = [m.group(1), []]; blocks.append(cur)
        elif cur is not None:
            cur[1].append(l.strip())
    flags = set()
    for name, ls in blocks:
        if not name.startswith(('Protected Mode', 'Compatibility Mode', 'Exceptions (All', 'Other Exceptions', 'SIMD Floating-Point Exceptions', 'Numeric Exceptions')):
            continue
        txt = ' '.join(ls)
        for m in re.finditer(r'#UD[^#]{0,260}?CPUID\.[^#]{0,80}', txt):
            for r in CPUIDREF.finditer(m.group(0)):
                flags.add(r.group(3))
    return sorted(flags)


def page_after(lines, j):
    for k in range(j, min(j + 1500, len(lines))):
        m = PAGE.search(lines[k])
        if m:
            return 'Vol. %s %s' % (m.group(1), m.group(2))
    return '?'




def flag_expr(cell, table):
    """the leading part of a flag cell that is a boolean expression over known flag names (the rest is the Description column bleeding into it)"""
    out = []; toks = re.findall(r'\(|\)|[A-Za-z0-9_\.\/]+', cell); depth = 0
    for t in toks:
        if t in ('AND', 'OR') and out: out.append(t); continue
        if t == '(' : out.append(t); depth += 1; continue
        if t == ')' and depth: out.append(t); depth -= 1; continue
        if t in table: out.append(t); continue
        break
    while out and out[-1] in ('AND', 'OR', '('): out.pop()
    return ' '.join(out)














EXECAS = re.compile(r'On processors that do not support (\w+), the instruction byte encoding is executed as (\w+)')


def sdm_rows(lines, table):
    """every opcode-table row of the instruction reference, with its section facts"""
    out = []
    for sec in sections(lines):
        rs = rows_of(lines, sec)
        if not rs:
            continue
        exc = exc_flags(lines, sec)
        txt = ' '.join(l.strip() for l in lines[sec[1]:sec[2]])
        ea = sorted({m.group(2) for m in EXECAS.finditer(txt)})
        for opc, ins, fl, ln, fmt in rs:
            fl = flag_expr(fl, table) if fmt == 'A' else ''
            out.append({'op': opc, 'modc': modclass(ins.strip()) if ins else 'any', 'p': parse_opcode(opc), 'instr': ins.strip(), 'flag': fl, 'fmt': fmt, 'sec': sec[0].split('—')[0], 'page': page_after(lines, ln),
                        'line': ln + 1, 'exc': exc, 'exec_as': ea})
    return out






# Forms with no row in the opcode tables of the instruction reference that the SDM still defines (so they are lifted with SDM semantics, not #UD): (name, matcher on (mandatory prefix, body bytes), the SDM text that
# defines them: the line is searched in the SDM text and its page is cited).  Everything else without a row is SDM-undefined (class B: #UD on the declared machine).






def main(argv):
    apm = []
    while '--apm' in argv:                                                   # one `--apm VOL.pdf` per pinned AMD volume (VOL.txt beside it)
        i = argv.index('--apm'); apm.append(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    if apm and len(argv) == 3 and argv[0] == 'features': return cmd_features(argv[1], apm, argv[2])
    if len(argv) == 4 and argv[0] == 'features': return cmd_features(*argv[1:])
    sys.stderr.write(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
