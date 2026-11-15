#!/usr/bin/env python3
"""The CPUID feature flag of every instruction form of the pack, read from the Intel SDM and from nothing else (OWNER 10, ASM-009: the x86 ISA as documented in the SDM).

    python3 -I sdmfeat.py rows SDM.txt SITES.tsv OUT_ROWS.tsv [PUBLIC_ROWS.tsv]   # the per-form feature rows; OUT_ROWS lists the shapes of each form (exe-derived: private), PUBLIC_ROWS (lift/proof/tests/sdm_cpuid_feature_rows.tsv) only their number
    python3 -I sdmfeat.py partition OUT_ROWS.tsv SITES.tsv OUT.tsv  # the cleared sites, from the rows file ONLY

SDM.txt: `pdftotext -layout` of the Intel(R) 64 and IA-32 Architectures SDM, combined volumes (its sha256 is in every output header; the page numbers cited are those of that edition).
SITES.tsv: packuniverse.py's sites file (`module:address<TAB>shape key<TAB>family`).

Source of each fact:
 * the opcode forms and their `CPUID Feature Flag`: the opcode tables of the instruction reference (Vol. 2A-2D).  A table with a `CPUID Feature Flag` column gives the flag in that column.  A table without that
   column (the older layout: `Opcode / Instruction / Op/En / 64-Bit Mode / Compat/Leg Mode`) names no flag in the row; the flags such a form needs are those its section's Exceptions text names as
   `#UD If CPUID.<leaf>:<reg>.<NAME>[Bit n] = 0` (collected per section as `exc_flags`).  A section that names none has no feature flag: the form is base.
 * the flag name -> (leaf, register, bit): the SDM's own CPUID chapter tables (Vol. 1 Ch. 21, `Leaf 01H ... Returned in ECX/EDX`, `Leaf 07H.00H ...`, `Leaf 80000001H ...`: rows `REG[n] NAME`).
 * the declared CPU: lift/rt/cpumodel.c compiled and called (fnvl_cpuid): a flag is present iff its leaf is <= the max leaf of its range and its bit is set in the answer (CLAUDE.md "The declared CPU").
 * a form with no row in the SDM opcode tables is an undefined opcode: #UD on every CPU (the 3DNow! 0F 0F family is AMD's; the Intel SDM defines no 0F 0F).  Its row is `undefined`.
 * the SDM states that a few forms execute as another legal instruction on a CPU without the flag (TZCNT/LZCNT as BSF/BSR, PREFETCHW as a hint).  Each such statement is cited in the row's `exec_as` column.
The capstone groups are not read here.  Rows are matched to a decoded form by (mandatory prefix, opcode bytes, ModRM.reg or fixed ModRM byte); an ambiguity (two rows with different flags) is an error, not a choice."""
import collections
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
RT = os.path.join(os.path.dirname(HERE), 'rt')

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


def flag_table(lines):
    """NAME -> [(leaf, sub, reg, bit)]: the rows `REG[n]   NAME` of the SDM's CPUID-leaf tables (Vol. 1 chapter 21)"""
    out = collections.defaultdict(list); leaf = None
    for ln in lines[20000:30000]:
        m = re.match(r'\s*Table 21-\d+\.\s+Leaf ([0-9A-F]+)H(?:\.([0-9A-F]+)H)?\s', ln)
        if m:
            leaf = (int(m.group(1), 16), int(m.group(2), 16) if m.group(2) else 0)
            continue
        m = re.match(r'\s*(EAX|EBX|ECX|EDX)\[(\d+)\]\s+([A-Za-z0-9_\.]+)\s', ln)
        if m and leaf:
            out[m.group(3)].append((leaf[0], leaf[1], m.group(1), int(m.group(2))))
    return out


def declared_cpu():
    """{(leaf, reg): value} of the declared CPU (lift/rt/cpumodel.c compiled and called), plus the two max leaves"""
    import ctypes
    import subprocess
    import tempfile
    d = tempfile.mkdtemp(); c = os.path.join(d, 'm.c'); so = os.path.join(d, 'm.so')
    with open(c, 'w') as fh:
        fh.write('#include "%s/cpumodel.c"\nunsigned long long fnvl_host_ticks_ns(void) { return 0; }\n' % RT)
    subprocess.check_call(['gcc', '-shared', '-fPIC', '-O1', '-w', '-o', so, c])
    lib = ctypes.CDLL(so); out = {}
    for leaf, sub in ((0, 0), (1, 0), (7, 0), (0x80000000, 0), (0x80000001, 0)):
        o = (ctypes.c_uint32 * 4)(); lib.fnvl_cpuid(ctypes.c_uint32(leaf), ctypes.c_uint32(sub), o)
        for r, v in zip(('EAX', 'EBX', 'ECX', 'EDX'), o): out[(leaf, r)] = v
    return out


def present(name, table, cpu):
    """a flag is present iff its leaf is within the declared max of its range and its bit is set in the declared answer"""
    for leaf, sub, reg, bit in table.get(name, ()):
        mx = cpu[(0, 'EAX')] if leaf < 0x80000000 else cpu[(0x80000000, 'EAX')]
        if leaf <= mx and sub == 0 and (cpu.get((leaf, reg), 0) >> bit) & 1:
            return True
    return False


def eval_flag(text, table, cpu):
    """text: a `CPUID Feature Flag` cell: names joined by AND / OR and parentheses (empty: no flag).  Returns (value, unknown names)"""
    if not text:
        return True, []
    unknown = []
    toks = re.findall(r'\(|\)|[A-Za-z0-9_\.]+', text)
    pos = [0]

    def expr():
        v = term()
        while pos[0] < len(toks) and toks[pos[0]] == 'OR':
            pos[0] += 1; w = term(); v = v or w
        return v

    def term():
        v = atom()
        while pos[0] < len(toks) and toks[pos[0]] == 'AND':
            pos[0] += 1; w = atom(); v = v and w
        return v

    def atom():
        t = toks[pos[0]]; pos[0] += 1
        if t == '(':
            v = expr(); pos[0] += 1
            return v
        if t not in table: unknown.append(t)
        return present(t, table, cpu)
    v = expr()
    return v, unknown


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


REGOP = re.compile(r'^(x?mm\d?|ymm\d?|zmm\d?|r(8|16|32|64)|imm\d+|CR\d?(–CR\d)?|DR\d?(–DR\d)?|Sreg|AL|AX|EAX|CL|1)$')


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


FLAGTOK = re.compile(r'^(\(|\)|AND|OR|[A-Za-z0-9_\.]+)$')


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


def vexkey(d):
    return d


def decode(code, md, addr=0x1000):
    return next(iter(md.disasm(code, addr, 1)), None)


LEGPFX = frozenset((0xf0, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0x66, 0x67))


def form_of(ins):
    """(mandatory prefix or None, body bytes, prefix count) of a decoded legacy-encoded instruction: the leading legacy prefix bytes of its encoding are stripped (an SSE mandatory prefix is part of the opcode)"""
    b = bytes(ins.bytes); pos = 0
    while pos < len(b) and b[pos] in LEGPFX: pos += 1
    pre = set(b[:pos]); mand = None
    if 0xf3 in pre: mand = 'F3'
    elif 0xf2 in pre: mand = 'F2'
    elif 0x66 in pre: mand = '66'
    return mand, b[pos:], pos


VEXROW = re.compile(r'(?<![A-Z])VEX\.(128|256|L0|L1|LZ|LIG)\.(?:(66|F2|F3)\.)?(0F|0F38|0F3A)\.(\w+)\s+([0-9A-F]{2})')
LENBIT = {'128': 0, 'L0': 0, 'LZ': 0, '256': 1, 'L1': 1}
MAPS = {'0F': (0x0f,), '0F38': (0x0f, 0x38), '0F3A': (0x0f, 0x3a)}


def match_rows(mand, body, rows, vexbody=None):
    """the SDM rows (parsed) that a form matches: the most specific by prefix (a row with the form's mandatory prefix beats a row without one)"""
    cand = []
    if body[:1] in (b'\xc4', b'\xc5') and len(body) >= (3 if body[0] == 0xc5 else 4):          # VEX: pp / map / opcode of the form against the VEX rows
        if body[0] == 0xc5:
            pp, mp, op, lb = body[1] & 3, (0x0f,), body[2], (body[1] >> 2) & 1
        else:
            pp, mp, op, lb = body[2] & 3, {1: (0x0f,), 2: (0x0f, 0x38), 3: (0x0f, 0x3a)}.get(body[1] & 31, ()), body[3], (body[2] >> 2) & 1
        pfx = (None, '66', 'F3', 'F2')[pp]
        for r in rows:
            m = VEXROW.search(r['op'])
            if m and (m.group(2) or None) == pfx and MAPS[m.group(3)] == mp and int(m.group(5), 16) == op and LENBIT.get(m.group(1), lb) == lb:
                cand.append(r)
        return cand, 'vex'
    for r in rows:
        d = r['p']
        if d is None or d['vex'] or d['rexw']:
            continue
        B = d['bytes']
        if d['plus_r']:
            if not (len(body) >= len(B) and body[:len(B) - 1] == bytes(B[:-1]) and (body[len(B) - 1] & 0xf8) == B[-1]): continue
        else:
            if body[:len(B)] != bytes(B): continue
        if d['digit'] is not None:
            if len(body) <= len(B) or ((body[len(B)] >> 3) & 7) != d['digit']: continue
        if d['modrm'] and r.get('modc', 'any') != 'any':
            if len(body) <= len(B): continue
            mod = body[len(B)] >> 6
            if (r['modc'] == 'mem') != (mod != 3): continue
            # (modc 'reg' needs mod == 3 and 'mem' needs mod != 3: the test above is exactly that)
        if d['pfx'] in ('66', 'F2', 'F3') and d['pfx'] != mand: continue
        if d['pfx'] == 'NP' and mand: continue
        cand.append(r)
    if cand:
        best = max(len(r['p']['bytes']) for r in cand)                    # the longest opcode match (a fixed ModRM byte beats the group row)
        cand = [r for r in cand if len(r['p']['bytes']) == best]
        if any(r['p']['pfx'] == mand and mand for r in cand):
            cand = [r for r in cand if r['p']['pfx'] == mand]
        elif any(r['p']['pfx'] is None for r in cand) and not mand:
            pass
    return cand, 'legacy'


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


def form_id(r):
    d = r['p']
    if d is None or d['vex']:
        return 'vex:' + r['op']
    return '%s|%s%s|%s' % (d['pfx'] or '-', ' '.join('%02X' % b for b in d['bytes']), '+r' if d['plus_r'] else '', '/%d' % d['digit'] if d['digit'] is not None else '-')


def read_sites(path):
    out = []
    for ln in open(path):
        if ln.startswith('#') or not ln.strip(): continue
        a, k, f = ln.rstrip('\n').split('\t')
        out.append((a, k, f))
    return out


# Forms with no row in the opcode tables of the instruction reference that the SDM still defines (so they are lifted with SDM semantics, not #UD): (name, matcher on (mandatory prefix, body bytes), the SDM text that
# defines them: the line is searched in the SDM text and its page is cited).  Everything else without a row is SDM-undefined (class B: #UD on the declared machine).
OPMAP = (
    ('82 /n', lambda m, b: b[:1] == b'\x82', '     80-83          1     mem, 11B', 'Table A-6: Group 1 covers opcodes 80-83, so 82 /n is the Group 1 instruction of 80 /n'),
    ('C4 /r LES', lambda m, b: b[:1] == b'\xc4', 'LESi64', 'Table A-2: C4 is LES (invalid only in 64-bit mode)'),
    ('C5 /r LDS', lambda m, b: b[:1] == b'\xc5', 'LDSi64', 'Table A-2: C5 is LDS (invalid only in 64-bit mode)'),
    ('0F 1E / 0F 1F Reserved-NOP', lambda m, b: b[:2] in (b'\x0f\x1e', b'\x0f\x1f'), 'are also reserved but may behave as NOP', 'Appendix A: "Reserved-NOP" opcodes may behave as NOP'),
)


def opmap_cite(lines, text):
    for i, l in enumerate(lines):
        if text in l and i > 140000:
            return page_after(lines, i)
    raise SystemExit('SDM text not found: %r' % text)


def cmd_rows(sdm, sites, out, public=None):
    from proof import shape
    lines = load(sdm); table = flag_table(lines); cpu = declared_cpu()
    allrows = sdm_rows(lines, table)
    nopflags = set(re.findall(r'are NOP on processors that report CPUID\.\w+H?\.\w+H?:\w+\.(\w+)\[', ' '.join(l.strip() for l in lines)))     # SDM Vol. 1 ch. 17: all MPX instructions are NOP without the flag
    byfirst = collections.defaultdict(list); vex = []
    for r in allrows:
        d = r['p']
        if d is None or d['vex']:
            vex.append(r)
        else:
            byfirst[d['bytes'][0] & (0xf8 if d['plus_r'] and len(d['bytes']) == 1 else 0xff)].append(r)
    sv = read_sites(sites); shapes = collections.OrderedDict(); count = collections.Counter(); fam = {}
    for a, k, f in sv:
        count[k] += 1; fam[k] = f
    md = shape.decoder(); forms = collections.OrderedDict(); problems = []
    for k in sorted(count):
        ins = decode(shape.rep_bytes(k), md)
        mand, body, pos = form_of(ins)
        if body[:1] in (b'\xc4', b'\xc5') and ins.mnemonic.startswith('v'):
            cand, kind = match_rows(mand, body, vex)
        else:
            rows = []
            b0 = body[0] if body else 0
            rows = byfirst.get(b0, []) + byfirst.get(b0 & 0xf8, [])
            cand, kind = match_rows(mand, body, rows)
        if not cand and not (body[:1] in (b'\xc4', b'\xc5') and ins.mnemonic.startswith('v')):
            mp = next((o for o in OPMAP if o[1](mand, body)), None)
        else:
            mp = None
        if not cand and mp:
            key = 'map|%s|%s' % (mp[0], ' '.join('%02X' % b for b in body[:3]))
            e = forms.setdefault(key, {'rows': [], 'shapes': [], 'fam': set(), 'map': mp})
        elif not cand:
            key = 'undefined|%s|%s' % (mand or '-', ' '.join('%02X' % b for b in body[:3]))
            if kind == 'legacy' and False: pass
            e = forms.setdefault(key, {'rows': [], 'shapes': [], 'fam': set()})
        else:
            fids = sorted({form_id(r) for r in cand})
            key = fids[0] if len(fids) == 1 else ' ; '.join(fids)
            e = forms.setdefault(key, {'rows': cand, 'shapes': [], 'fam': set()})
        e['shapes'].append(k); e['fam'].add(fam[k])
    nsites = collections.Counter(k for a, k, f in sv)
    outs = [(out, True)] + ([(public, False)] if public else [])
    fhs = [(open(o, 'w', newline='\n'), full) for o, full in outs]

    class Tee:
        def write(self, t):
            for h, full in fhs: h.write(t)

        def row(self, f):                       # the public copy names the number of shapes of a form instead of the (exe-derived) shape list
            for h, full in fhs: h.write('\t'.join(f if full else f[:9] + ['n=%d' % len(f[9].split(','))] + f[10:]) + '\n')
    fh = Tee()
    if True:
        fh.write('# fnvnx-sdm-cpuid-feature-rows/1 sdm_sha256=%s cpumodel_sha256=%s forms=%d shapes=%d sites=%d\n' % (sha_file(sdm.replace('.txt', '.pdf')) if os.path.exists(sdm.replace('.txt', '.pdf')) else sha_file(sdm), sha_file(os.path.join(RT, 'cpumodel.c')), len(forms), len(count), len(sv)))
        fh.write('# source: Intel 64 and IA-32 Architectures SDM combined volumes (pdftotext -layout), opcode tables of Vol. 2A-2D; flag names and bits: Vol. 1 ch. 21 CPUID leaf tables; declared CPU: lift/rt/cpumodel.c\n')
        fh.write('# form\tfamily\tstatus\tneeds\tvalue\texec_as\tsdm_opcode\tsdm_instr\tcitation\tshapes\tnsites\n')
        for key in sorted(forms):
            e = forms[key]; rs = e['rows']
            if not rs and e.get('map'):
                mp = e['map']
                st, needs, val, ea, op, ins, cit = 'defined-by-opcode-map', '-', 'none', '-', mp[0], '-', 'SDM %s (%s)' % (mp[3], opmap_cite(lines, mp[2]))
            elif not rs:
                st, needs, val, ea, op, ins, cit = 'undefined', '-', 'none', '-', '-', '-', 'no row in the SDM opcode tables for this opcode form (undefined opcode: #UD, SDM Vol. 2 ch. 3 "Interrupt 6 - Invalid Opcode")'
            else:
                needset = set()
                for r in rs:
                    if r['fmt'] == 'A' and r['flag']: needset.add(r['flag'])
                    elif r['fmt'] == 'A': needset.add(' AND '.join(r['exc']) or '-')
                    else: needset.add(' AND '.join(r['exc']) or '-')
                if len(needset) > 1:
                    problems.append((key, sorted(needset)))
                st = 'found' if len(needset) == 1 else 'ambiguous'
                needs = ' | '.join(sorted(needset))
                vals = set()
                for n in needset:
                    if n == '-': vals.add('none'); continue
                    ok, unk = eval_flag(n.replace(' AND ', ' AND '), table, cpu)
                    vals.add('present' if ok else 'absent')
                    if unk: problems.append((key, 'unknown flag %s' % unk))
                val = 'none' if vals == {'none'} else ('absent' if 'absent' in vals else 'present')
                ea = ','.join(sorted({x for r in rs for x in r['exec_as']} | {'NOP' for n in needset for t in re.findall(r'[A-Za-z0-9_\.]+', n) if t in nopflags})) or '-'
                op = rs[0]['op']; ins = rs[0]['instr']; cit = 'SDM %s %s %s' % (rs[0]['sec'], rs[0]['page'], ('opcode table row "%s"' % rs[0]['op']) + ('' if rs[0]['fmt'] == 'A' else ' (no flag column; flags from the #UD CPUID lines of the section)'))
            fh.row([key, ','.join(sorted(e['fam'])), st, needs, val, ea, op, ins, cit, ','.join(e['shapes']), str(sum(nsites[k] for k in e['shapes']))])
    for h, full in fhs: h.close()
    sys.stdout.write('forms %d shapes %d sites %d problems %d\n' % (len(forms), len(count), len(sv), len(problems)))
    for p in problems[:40]: sys.stdout.write('  PROBLEM %s\n' % (p,))
    return 1 if problems else 0


def cmd_partition(rows, sites, out):
    by = {}; hdr = ''
    for ln in open(rows):
        if ln.startswith('#'):
            if not hdr: hdr = ln.strip()
            continue
        f = ln.rstrip('\n').split('\t')
        form, fam, st, needs, val, ea, op, ins, cit, shp, n = f
        if st == 'ambiguous':
            raise SystemExit('ambiguous row %s' % form)
        cleared = st == 'undefined' or (val == 'absent' and ea == '-')               # class B: #UD on the declared machine
        for k in shp.split(','): by[k] = (cleared, form, st, needs, ea)
    cnt = collections.Counter(); total = 0
    with open(out, 'w', newline='\n') as fh:
        fh.write('# fnvnx-sdm-cleared-sites/1 from %s\n# module:address\tshape\tform\treason\n' % hdr[:200])
        for a, k, f in read_sites(sites):
            total += 1
            c, form, st, needs, ea = by[k]
            if c:
                why = 'undefined opcode' if st == 'undefined' else 'needs %s' % needs
                cnt[why] += 1
                fh.write('%s\t%s\t%s\t%s\n' % (a, k, form, why))
    sys.stdout.write('sites %d cleared %d: %s\n' % (total, sum(cnt.values()), dict(cnt)))
    return 0


def main(argv):
    if len(argv) in (4, 5) and argv[0] == 'rows': return cmd_rows(*argv[1:])
    if len(argv) == 4 and argv[0] == 'partition': return cmd_partition(*argv[1:])
    sys.stderr.write(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
