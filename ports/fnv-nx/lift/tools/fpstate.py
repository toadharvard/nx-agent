#!/usr/bin/env python3
"""C4a: whole-program facts about the x87 / MMX / SSE control state, and the invariants that certify the lifter's no-effect bodies.

    fpstate.py scan    <pe image> <facts dir>             one module: writes <facts dir>/fpstate/{fcw_loads,mxcsr_loads,readers,a3,scan}.tsv, prints counts only
    fpstate.py combine <facts dir> [<facts dir> ...]      the PROGRAM invariants = AND over the modules; writes <facts dir>/fpstate/invariants.tsv into every directory

Deterministic: a pure function of the PE image and <facts dir>/universe.tsv (every iteration order is sorted, no clocks). Exe-derived outputs live only under the facts directories.
Decoder, CFG, esp tracking and the value-set walk are the shared ones (x86flow.py, fpmodes.py).

A1  FCW / MXCSR LOAD SITES.  A load is `fldcw m16`, `ldmxcsr m32`, `fninit`/`finit` (0x037F), `fldenv`/`frstor` (image: unknown), `fxrstor` (unknown), and the imports _controlfp/_control87/_fpreset.
    Value of a memory operand = fpmodes.val (backward walk over the CFG to the defining stores): a constant C, or a TRANSFORM (A,O) of the current word (`fnstcw [a]; ...; and/or; fldcw [a]`:
    new = (cur & A) | O), or unknown (data dependent, aliased, argument of an entry, produced by a call).  REACHABLE SET R of control words: the least fixed point of
    R = {start} U {constants} U {t(r) : r in R, t a transform}; sound because a transform only ever applies to a value `fnstcw` stored earlier, which was the control word at that time
    and so is in R (induction on the number of loads).  Fields that can leave their start value are read off R.  An unknown load makes R unbounded: the facts say `unproven`.
    Start values: thread entry (rt/enter.cc: FCW 0x027F, MXCSR 0x1F80, assumption ASM-327: every guest thread starts there).
A2  READERS of sticky status: `fnstsw ax` (fpmodes.statclass: forward walk of the value through eax/ah/flags; cc-only = only C0-C3/TOP can influence anything), `fnstsw m16`, `fnstenv`,
    `fnsave`, `stmxcsr`, `fxsave` (the value escapes to memory: all bits observed), the imports _clearfp/_statusfp/_status87/_controlfp.  `fnclex` sites are listed.
A3  OCCURRENCES: wait forms, MMX instructions, instructions whose CPUID feature the model (rt/cpumodel.c) does not have (fnvlift/f_cpufeat.py), with the handler that would take them.
Assumptions: ASM-327 above; ASM-003: an imported system function changes the x87/SSE control state only if it is one of the control imports above (the runtime implements the Win32/CRT
surface, rt/imp.h); ASM-328: the CONTEXT image the runtime hands to guest SEH handlers is only read back by the runtime's own restore, which restores a snapshot of a state that was reachable."""
import os, sys, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
if __name__ == '__main__' and len(sys.argv) >= 4 and sys.argv[1] == 'scan':
    os.environ['FNVNX_FACTS'] = os.path.abspath(sys.argv[3])          # x86flow reads its facts root at import
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from collections import Counter, defaultdict
from multiprocessing import Pool
import x86flow as W
import fpmodes as FP
from x86flow import X, M32, mn, Fn, load_universe
from fnvlift import f_cpufeat, f_sys, f_sse, core

FCW_START, MXCSR_START = 0x027F, 0x1F80
FCW_FIELDS = (('masks', 0x003F), ('PC', 0x0300), ('RC', 0x0C00), ('IC', 0x1000))
MX_FIELDS = (('masks', 0x1F80), ('RC', 0x6000), ('FTZ', 0x8000), ('DAZ', 0x0040))
IMP_CTL = ('_controlfp', '_control87', '_fpreset')
IMP_STAT = ('_clearfp', '_statusfp', '_status87')
WSTORE_NAMES = ('fnstsw', 'fstsw')


# ---- the MSVC CRT encoding of _controlfp(new, mask) as a transform of the x87 word / MXCSR ----
def crt_to_x87(v):
    r = 0
    for crt, x in ((0x10, 1), (0x80000, 2), (0x08, 4), (0x04, 8), (0x02, 16), (0x01, 32)):
        r |= x if v & crt else 0
    return r | ((v >> 8) & 3) << 10 | {2: 0, 1: 2, 0: 3, 3: 1}[(v >> 16) & 3] << 8 | (0x1000 if v & 0x40000 else 0)


def crt_mask_x87(m):
    r = crt_to_x87(m) & 0x1c3f
    r |= 0x0300 if m & 0x30000 else 0
    return r


def crt_to_mx(v, m):
    """(A, O) for MXCSR: EM -> mask bits 7-12 (same order as x87 0-5), RC -> 13-14, DN (0x03000000) -> FTZ / DAZ"""
    xm, xv = crt_mask_x87(m) & 0x3f, crt_to_x87(v) & 0x3f
    am = ~((xm << 7) | ((m >> 8 & 3) << 13) | (0x8040 if m & 0x03000000 else 0)) & M32
    om = (xv & xm) << 7 | ((v >> 8) & 3 & (m >> 8 & 3)) << 13 | (0x8040 if (m & 0x03000000 and v & 0x03000000) else 0)
    return am, om


def crt_to_cw(v, m):
    xm = crt_mask_x87(m)
    return (~xm) & M32, crt_to_x87(v) & xm


# ---- per entry ----
def push_consts(F, x, n):
    """constants pushed for the call at x, top of stack first (a pred walk through single-predecessor straight-line code; None = not a constant)"""
    out, pos = [], x
    while len(out) < n:
        ps = F.preds.get(pos, [])
        if len(ps) != 1:
            break
        q = F.ins[ps[0]]
        if mn(q) == 'push':
            v = FP.srcval(F, ps[0], q, q.operands[0], 0)
            out.append(next(iter(v))[1] if len(v) == 1 and next(iter(v))[0] == 'c' else None)
        elif FP.espd(q) != 0 or mn(q) == 'call':
            break
        pos = ps[0]
    return (out + [None] * n)[:n]


def effects(vs):
    """value set of fpmodes.val -> list of ('c', v) | ('t', A, O) | ('u', reason)"""
    r = []
    for v in sorted(vs, key=repr):
        if v[0] == 'c':
            r.append(('c', v[1]))
        elif v[0] == 't':
            r.append(('t', v[1], v[2]))
        elif v[0] == 'arg':
            r.append(('u', 'arg'))
        elif v[0] == 'glob':
            r.append(('u', 'global'))
        else:
            r.append(('u', v[1] if len(v) > 1 else '?'))
    return r


def a3_tags(ins, m):
    t = []
    if m in ('wait', 'fwait'):
        t.append('wait')
    elif ins.bytes[0] == 0x9b and m.startswith('f'):
        t.append('wait-prefix:' + m)
    ops = ins.operands
    miss = f_cpufeat.missing(ins)
    if not miss and (m == 'emms' or any(o.type == X.X86_OP_REG and ins.reg_name(o.reg).startswith('mm') for o in ops)):      # an MMX instruction of the model (3DNow! forms, which also use mm registers, are counted under absent:3DNOW)
        t.append('mmx:' + m)
    for f in miss:
        enc = f_sys.encoding(ins)[0]
        if enc in ('vex', 'evex', 'xop', 'map38', 'map3a') or m in f_sys.SSE3_SSE4A:
            how = 'covered:h_absent_ext'
        elif m in core.OPS or m in f_sse.SSE or 0xd8 <= ins.opcode[0] <= 0xdf:
            how = 'NOT-covered:handler-wins'
        elif f_sys._is_simd(type('C', (), {'ops': ins.operands})(), ins):
            how = 'NOT-covered:h_sse-lift-error'          # an mm / xmm operand: f_sys.h_default stands aside, f_sse.h_sse raises Unsupported('sse <m>')
        else:
            how = 'covered:h_default'
        t.append('absent:%s:%s:%s' % (f, m, how))
    return t


def scan1(args):
    a, ext, kind = args
    F = Fn(a, ext, kind)
    if not F.ok:
        return a, None
    r = dict(a3=[], fcw=[], mx=[], rd=[], x87=0)
    r['eax'] = FP.eaxscan(F)
    for x in sorted(F.ins):
        i = F.ins[x]; m = mn(i); o = i.operands
        for t in a3_tags(i, m):
            r['a3'].append((x, t))
        if m in ('fldcw', 'ldmxcsr'):
            mx = m == 'ldmxcsr'; F.w = 4 if mx else 2
            l = FP.memslot(i, o[0], F.w)
            if l is None:
                eff = [('u', 'ptr')]
            elif l[0] == 'g':
                eff = [('u', 'global:%08x' % l[1])]
            else:
                eff = effects(FP.val(F, x, l))
            r['mx' if mx else 'fcw'].append((x, m, eff))
        elif m in ('fninit', 'finit'):
            r['fcw'].append((x, m, [('c', 0x37f)]))
        elif m in ('frstor', 'fldenv', 'fxrstor'):
            r['mx' if m == 'fxrstor' else 'fcw'].append((x, m, [('u', 'env-load')]))
            if m == 'fxrstor':
                r['fcw'].append((x, m, [('u', 'env-load')]))
        elif m == 'call' and o[0].type == X.X86_OP_MEM and not o[0].mem.base and not o[0].mem.index and not o[0].mem.segment:
            nm = W.IATADDR.get(o[0].mem.disp & M32)
            if nm in IMP_CTL:
                a1, a2 = push_consts(F, x, 2)
                if nm == '_fpreset':
                    e1 = e2 = [('u', 'import:_fpreset')]
                elif a1 is None or a2 is None:
                    e1 = e2 = [('u', 'import:%s:arg' % nm)]
                else:
                    e1, e2 = [('t',) + crt_to_cw(a1, a2)], [('t',) + crt_to_mx(a1, a2)]
                r['fcw'].append((x, 'imp:' + nm, e1)); r['mx'].append((x, 'imp:' + nm, e2))
            elif nm in IMP_STAT:
                r['rd'].append((x, 'imp:' + nm, 'escapes', 'import-result'))
        elif m in ('fnstsw', 'fstsw'):
            r['rd'].append((x, m, 'pending', ''))
        elif m in ('fnstenv', 'fstenv', 'fnsave', 'fsave', 'fxsave', 'stmxcsr', 'fnclex', 'fclex'):
            r['rd'].append((x, m, 'listed' if 'clex' in m else 'escapes', 'image-to-memory' if 'clex' not in m else ''))
    return a, r


def statscan(args):
    a, ext, kind, xs = args
    F = Fn(a, ext, kind)
    if not F.ok:
        return [(x, 'escapes', 'undecodable') for x in xs]
    out = []
    for x in xs:
        c, why = FP.statclass(F, x)
        out.append((x, 'cc-only' if c == 'cc-only' else 'escapes', why))
    return out


# ---- reachable control words ----
def closure(start, consts, trans, width=16):
    mask = (1 << width) - 1
    R = {start} | {c & mask for c in consts}
    work = sorted(R)
    while work:
        v = work.pop()
        for A, O in sorted(trans):
            n = ((v & A) | O) & mask
            if n not in R:
                R.add(n); work.append(n)
    return R


def fields(R, start, fl):
    out = {}
    for name, bits in fl:
        out[name] = sorted({v & bits for v in R})
    return out


def analyse(sites, start, fl, entries_of, kinds):
    """-> dict with class counts, unknown sites, reachable set and per-field value sets"""
    consts, trans, unk = set(), set(), []
    cls = Counter()
    for x, m, eff in sites:
        c = 'constant' if all(e[0] == 'c' for e in eff) else 'transform' if all(e[0] in 'ct' for e in eff) else 'unknown'
        if any(e[0] == 'u' for e in eff):
            c = 'unknown'
        cls['%s/%s' % (m, c)] += 1
        for e in eff:
            if e[0] == 'c':
                consts.add(e[1])
            elif e[0] == 't':
                trans.add((e[1], e[2]))
            else:
                unk.append((x, m, e[1]))
    R = closure(start, consts, trans)
    return dict(cls=cls, consts=sorted(consts), trans=sorted(trans), unk=sorted(set(unk)), R=R, fields=fields(R, start, fl))


def do_scan(exe, facts):
    raw, rows = load_universe(exe)
    out = os.path.join(facts, 'fpstate')
    os.makedirs(out, exist_ok=True)
    key = hashlib.sha256(open(exe, 'rb').read()).hexdigest()[:16] + hashlib.md5(raw).hexdigest()[:16]
    with Pool(2) as p0:
        W.prepare(p0, rows, key)
    kind = {a: k for a, e, k in rows}; ext = {a: e for a, e, k in rows}
    res = {}
    with Pool(2) as pool:
        for a, r in pool.imap(scan1, [(a, e, k) for a, e, k in rows], chunksize=100):
            res[a] = r
    bad = sorted(a for a in res if res[a] is None)
    # distinct instruction addresses: entries overlap (inner entries and tails share code); the owner is the lowest entry containing it
    fcw, mx, rd, a3 = {}, {}, {}, {}
    ents3 = defaultdict(set)
    for a in sorted(res):
        r = res[a]
        if not r:
            continue
        for x, m, eff in r['fcw']:
            fcw.setdefault((x, m), (eff, a))
        for x, m, eff in r['mx']:
            mx.setdefault((x, m), (eff, a))
        for x, m, c, why in r['rd']:
            rd.setdefault((x, m), (c, why, a))
        for x, t in r['a3']:
            a3.setdefault((x, t), a); ents3[t].add(a)
    # status readers: forward value walk for fnstsw ax
    # callee-reads-eax facts (as fpmodes.main): a call with the status word live in eax is an escape only if the callee may read eax at entry (LTCG register arguments)
    FP.KNOWN.clear(); FP.EAXLIVE.clear()
    FP.KNOWN.update(a for a in res if res[a])
    live = {a for a, r in res.items() if r and r['eax'][0]}
    for _ in range(50):
        nl = {a for a, r in res.items() if r and a not in live and any(c in live or c not in FP.KNOWN for c in r['eax'][1])}
        if not nl:
            break
        live |= nl
    FP.EAXLIVE.update(live | set(bad))
    pend = defaultdict(list)
    for (x, m), (c, why, a) in sorted(rd.items()):
        if c == 'pending':
            pend[a].append(x)
    with Pool(2) as pst:
        stat = {x: (c, why) for lst in pst.imap(statscan, [(a, ext[a], kind[a], xs) for a, xs in sorted(pend.items())], chunksize=20) for x, c, why in lst}
    for (x, m), (c, why, a) in list(rd.items()):
        if c == 'pending':
            rd[(x, m)] = stat.get(x, ('escapes', 'undecided')) + (a,)
    sites_f = [(x, m, e) for (x, m), (e, a) in sorted(fcw.items())]
    sites_m = [(x, m, e) for (x, m), (e, a) in sorted(mx.items())]
    A1 = analyse(sites_f, FCW_START, FCW_FIELDS, None, kind)
    A1m = analyse(sites_m, MXCSR_START, MX_FIELDS, None, kind)
    with open(out + '/fcw_loads.tsv', 'w') as f:
        for (x, m), (eff, a) in sorted(fcw.items()):
            f.write('%08x\t%s\tentry=%08x\t%s\n' % (x, m, a, ' '.join(':'.join(str(v) if not isinstance(v, int) else '%x' % v for v in e) for e in eff)))
    with open(out + '/mxcsr_loads.tsv', 'w') as f:
        for (x, m), (eff, a) in sorted(mx.items()):
            f.write('%08x\t%s\tentry=%08x\t%s\n' % (x, m, a, ' '.join(':'.join(str(v) if not isinstance(v, int) else '%x' % v for v in e) for e in eff)))
    with open(out + '/readers.tsv', 'w') as f:
        for (x, m), (c, why, a) in sorted(rd.items()):
            f.write('%08x\t%s\t%s\t%s\tentry=%08x\n' % (x, m, c, why, a))
    with open(out + '/a3.tsv', 'w') as f:
        for (x, t), a in sorted(a3.items()):
            f.write('%08x\t%s\tentry=%08x\n' % (x, t, a))
    # summary counts
    L = []
    L.append('module %s sha256 %s entries %d undecodable %d' % (os.path.basename(exe), hashlib.sha256(open(exe, 'rb').read()).hexdigest()[:12], len(rows), len(bad)))
    for nm, A in (('FCW', A1), ('MXCSR', A1m)):
        L.append('A1 %s load sites: %s' % (nm, ' '.join('%s=%d' % kv for kv in sorted(A['cls'].items())) or 'none'))
        L.append('A1 %s constants: %s  transforms (and,or): %s' % (nm, ' '.join('%x' % c for c in A['consts']), ' '.join('(%x,%x)' % t for t in A['trans'])))
        L.append('A1 %s unknown loads: %d  reasons: %s' % (nm, len(A['unk']), ' '.join('%s=%d' % kv for kv in sorted(Counter(re_ for _, _, re_ in A['unk']).items()))))
        L.append('A1 %s reachable words (from start, resolved loads only): %d; field values: %s' % (nm, len(A['R']), ' '.join('%s={%s}' % (k, ','.join('%x' % v for v in vs)) for k, vs in A['fields'].items())))
    rc = Counter(); sc = Counter()
    for (x, m), (c, why, a) in rd.items():
        rc[(m, c)] += 1
        if m in ('fnstsw', 'fstsw') and c == 'escapes':
            sc[why.split('-')[0] + ('-' + why.split('-')[1] if why.startswith('use') else '')] += 1
    L.append('A2 readers: ' + ' '.join('%s/%s=%d' % (m, c, n) for (m, c), n in sorted(rc.items())))
    L.append('A2 fnstsw escape reasons: ' + ' '.join('%s=%d' % kv for kv in sorted(sc.items(), key=lambda kv: (-kv[1], kv[0]))))
    tc = Counter(); te = defaultdict(set)
    for (x, t), a in a3.items():
        k = t.split(':')[0] if not t.startswith('absent') else ':'.join(t.split(':')[:3] + t.split(':')[3:4])
        tc[k] += 1; te[k].add(a)
    for k in sorted(tc):
        L.append('A3 %s: instructions=%d entries=%d' % (k, tc[k], len(te[k])))
    # invariants of this module
    unk_f = len(A1['unk']); unk_m = len(A1m['unk'])
    cleared = sorted({b for v in A1['R'] for b in range(6) if not v >> b & 1} | {b for v in A1m['R'] for b in range(7, 13) if not v >> b & 1})
    mask_ok = unk_f == 0 and unk_m == 0 and not cleared
    nobs = sum(1 for (x, m), (c, why, a) in rd.items() if c == 'escapes')
    stat_ok = nobs == 0
    with open(out + '/scan.tsv', 'w') as f:
        f.write('module\t%s\nexe_sha256\t%s\nuniverse_md5\t%s\nI-FPMASK\t%s\tunknown_fcw=%d unknown_mxcsr=%d cleared_mask_bits=%s\nI-FPSTICKY\t%s\tobservers=%d\n' % (
            os.path.basename(exe), hashlib.sha256(open(exe, 'rb').read()).hexdigest(), hashlib.md5(raw).hexdigest(), 'holds' if mask_ok else 'unproven', unk_f, unk_m,
            ','.join(map(str, cleared)) or '-', 'holds' if stat_ok else 'unproven', nobs))
    L.append('module invariants: ASM-305 %s (unknown loads fcw %d mxcsr %d, mask bits clearable %s)  ASM-306 %s (observers %d)' % ('holds' if mask_ok else 'unproven', unk_f, unk_m, cleared or '-', 'holds' if stat_ok else 'unproven', nobs))
    print('\n'.join(L))


def do_combine(dirs):
    rows = []
    for d in dirs:
        kv = {}
        for ln in open(os.path.join(d, 'fpstate', 'scan.tsv')):
            f = ln.rstrip('\n').split('\t')
            kv[f[0]] = f[1:]
        rows.append((d, kv))
    prog = {}
    for inv in ('ASM-305', 'ASM-306'):
        holds = all(kv[inv][0] == 'holds' for d, kv in rows)
        prog[inv] = 'holds' if holds else 'unproven'
    for d, kv in rows:
        with open(os.path.join(d, 'fpstate', 'invariants.tsv'), 'w') as f:
            f.write('# program-level invariants = AND over the modules below (tools/fpstate.py combine); status holds | unproven\n')
            for inv in sorted(prog):
                f.write('%s\t%s\t%s\n' % (inv, prog[inv], ' '.join('%s:%s' % (kv2['module'][0], kv2[inv][0]) for d2, kv2 in rows)))
    print('program: ' + ' '.join('%s=%s' % kv for kv in sorted(prog.items())) + '  modules: ' + ' '.join(kv['module'][0] for d, kv in rows))


if __name__ == '__main__':
    if len(sys.argv) >= 4 and sys.argv[1] == 'scan':
        do_scan(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 3 and sys.argv[1] == 'combine':
        do_combine(sys.argv[2:])
    else:
        sys.exit(__doc__)
