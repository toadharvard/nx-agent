#!/usr/bin/env python3
"""W10-A: whole-program static analysis of the floating-point control state of FalloutNV.exe (x87 CW, MXCSR, status
readers, x87 stack depth). Count-only stdout; site lists under /home/user/l3facts/fpmodes/. Sound = over-approximate:
anything not decided is reported as unknown. Inputs: FNVNX_EXE, /home/user/l3facts/universe.tsv (read-only). Shared decoder,
CFG and esp tracking: x86flow.py."""
import os, sys, hashlib
from collections import Counter, defaultdict
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x86flow as W
from x86flow import X, M32, SUB, mn, rn, memslot, Fn, UNC, KINDS, isx87, depth, track, frame_events, load_universe, pmap

EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
OUT = '/home/user/l3facts/fpmodes'
T = {}                      # entry -> net x87 depth change at return (nonzero only), set before each wave
KNOWN = set()
EAXLIVE = set()             # entries that may read eax at entry (LTCG register args), set before the status pass
FSAFE = ('fld', 'fild', 'fxch', 'fst', 'fstp', 'fist', 'fistp', 'fisttp', 'fld1', 'fldz', 'wait', 'fwait', 'fnop')
WSTORE = ('fst', 'fist', 'fnst', 'fbst', 'stmxcsr', 'fisttp', 'fxsave', 'fnsave', 'fsave')


def espd(i):
    m = mn(i); o = i.operands
    if m == 'push' or m == 'pushfd':
        return -4
    if m == 'pop' or m == 'popfd':
        return 4
    if m in ('add', 'sub') and o[0].type == X.X86_OP_REG and i.reg_name(o[0].reg) == 'esp':
        return None if o[1].type != X.X86_OP_IMM else (o[1].imm if m == 'add' else -o[1].imm)
    return None if 'esp' in [i.reg_name(r) for r in i.regs_access()[1]] else 0      # regs_access raises capstone.CsError only without detail (never here): a failure propagates


def apply(vs, A, O):
    r = set()
    for v in vs:
        if v[0] == 'c':
            r.add(('c', ((v[1] & A) | O) & M32))
        elif v[0] == 't':
            r.add(('t', v[1] & A, ((v[2] & A) | O) & M32, v[3]))
        else:
            r.add(v)
    return r


def comb(a, b):
    return {('arg',)} if any(v[0] == 'arg' for v in a | b) else {('?', 'calc')}


def entryval(F, loc):
    if F.kind in ('inner', 'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler'):
        return {('?', 'funclet')}
    if loc[0] == 's':
        return {('arg',)} if loc[1] >= 4 else {('?', 'uninit')}
    return {('arg',)} if loc[0] == 'r' else {('?', 'uninit')}


def val(F, addr, loc, dep=0):
    """value set of loc just before the instruction at addr (iterative over pass-through steps)."""
    key = (addr, loc)
    if key in F.memo:
        return F.memo[key]
    F.memo[key] = set()
    res, seen, work = set(), set(), [(addr, loc)]
    while work:
        a, l = work.pop()
        if (a, l) in seen:
            continue
        seen.add((a, l)); F.steps += 1
        if F.steps > 300000 or dep > 12:
            res.add(('?', 'budget')); break
        ps = F.preds.get(a, ())
        if a == F.a:
            res |= entryval(F, l)
        elif not ps:
            res.add(('?', 'noreach'))
        for p in ps:
            r = step(F, p, l, dep + 1)
            if type(r) is tuple:
                work.append(r[1:])
            else:
                res |= r
    F.memo[key] = res
    return res


def srcval(F, p, i, o, dep):
    if o.type == X.X86_OP_IMM:
        return {('c', o.imm & M32)}
    if o.type == X.X86_OP_REG:
        b, sh, bits = SUB.get(i.reg_name(o.reg), (None, 0, 0))
        return val(F, p, ('r', b), dep) if b and sh == 0 else {('?', 'hireg')}
    l = memslot(i, o, min(o.size, F.w))
    if l is None:
        return {('?', 'ptr')}
    return {('glob', l[1])} if l[0] == 'g' else val(F, p, l, dep)


def step(F, p, loc, dep):
    """value of loc just after the instruction at p."""
    i = F.ins[p]; m = mn(i); o = i.operands; k = loc[0]
    if m == 'call':
        if k == 'r':
            return {('?', 'call')} if loc[1] in ('eax', 'ecx', 'edx') else ('go', p, loc)
        cc = coord(F, p, loc, 0)
        if cc is not None and F.sp.get(p) is not None and cc >= F.sp[p] and not aliased(F, cc, loc[2]):
            return ('go', p, loc)
        return {('?', 'call')}
    if k == 'b' and m == 'mov' and o[0].type == X.X86_OP_REG and i.reg_name(o[0].reg) == 'ebp' and o[1].type == X.X86_OP_REG and i.reg_name(o[1].reg) == 'esp':
        return ('go', p, ('s',) + loc[1:])
    W = {SUB[i.reg_name(r)][0] for r in i.regs_access()[1] if i.reg_name(r) in SUB}
    if k == 'r':
        if loc[1] not in W:
            return ('go', p, loc)
        if m == 'pop':
            return ('go', p, ('s', 0, 4))
        if o[0].type != X.X86_OP_REG or SUB.get(i.reg_name(o[0].reg), (0,))[0] != loc[1] or len(o) > 2:
            return {('?', 'def:' + m)}
        _, sh, bits = SUB[i.reg_name(o[0].reg)]
        msk = ((1 << bits) - 1) << sh
        if m in ('xor', 'sub') and o[1].type == X.X86_OP_REG and o[1].reg == o[0].reg:
            return {('c', 0)}
        if m in ('mov', 'movzx') and (bits == 32 or (bits == 16 and sh == 0)):
            return srcval(F, p, i, o[1], dep)
        if m == 'mov' and o[1].type == X.X86_OP_IMM:
            return apply(val(F, p, loc, dep), M32 & ~msk, (o[1].imm << sh) & msk)
        if m in ('or', 'and') and len(o) == 2 and o[1].type == X.X86_OP_IMM:
            im = (o[1].imm << sh) & msk
            return apply(val(F, p, loc, dep), M32 if m == 'or' else (M32 & ~msk) | im, im if m == 'or' else 0)
        if m in ('mov', 'movzx', 'movsx', 'lea') or len(o) < 2:
            return {('?', 'def:' + m)} if m != 'not' and m != 'neg' else comb(val(F, p, loc, dep), set())
        return comb(val(F, p, loc, dep), srcval(F, p, i, o[1], dep))
    # frame / global slot
    if m == 'pushfd' or m == 'push':
        d = -4
    else:
        d = espd(i)
    if d is None or (k == 'b' and 'ebp' in W):
        return {('?', 'espebp')}
    kb = loc[1] + d if k == 's' else loc[1]
    nl = (k, kb, loc[2])
    if m == 'push':                                   # writes [esp_after+0 .. +4)
        if k == 's' and loc[1] < 4 and loc[1] + loc[2] > 0:
            return srcval(F, p, i, o[0], dep) if loc[1] == 0 else {('?', 'partial')}
        return ('go', p, nl)
    if m == 'pushfd':
        return {('?', 'pushf')} if (k == 's' and loc[1] < 4) else ('go', p, nl)
    cc = coord(F, p, loc, d)
    for j, op in enumerate(o):
        if op.type != X.X86_OP_MEM or not ((op.access & 2) or m.startswith(WSTORE) or m.startswith(('movs', 'stos'))):
            continue
        l2 = memslot(i, op, op.size)
        sz = op.size or 108
        if l2 and l2[0] == 'g':
            continue
        if not (l2 and l2[0] == k):                       # other base / indexed / pointer store
            bs = i.reg_name(op.mem.base) if op.mem.base else None
            if bs in ('esp', 'ebp') and not op.mem.index:
                c2 = coord(F, p, (bs[1], op.mem.disp, sz), 0, True)
                if cc is None or c2 is None or (c2 + sz > cc and cc + loc[2] > c2):
                    return {('?', 'alias')}
            elif bs in ('esp', 'ebp') or cc is None or (cc is not None and aliased(F, cc, loc[2])):
                return {('?', 'alias')}
            continue
        if l2[1] + sz <= kb or kb + loc[2] <= l2[1]:
            continue
        if l2[1] != kb or sz < loc[2] or j != 0:
            return {('?', 'partial')}
        if m in ('fnstcw', 'stmxcsr', 'fstcw'):
            return {('t', M32, 0, p)}
        if m == 'mov':
            return srcval(F, p, i, o[1], dep)
        if m in ('or', 'and') and o[1].type == X.X86_OP_IMM:
            return apply(val(F, p, nl, dep), M32 if m == 'or' else o[1].imm & M32, o[1].imm & M32 if m == 'or' else 0)
        if m in ('or', 'and', 'xor', 'add', 'sub'):
            return comb(val(F, p, nl, dep), srcval(F, p, i, o[1], dep))
        return {('?', 'st:' + m)}
    return ('go', p, nl)


def coord(F, p, loc, d, before=False):
    """frame coordinate (esp-at-entry relative) of an esp/ebp slot; loc is relative to the state after p unless before."""
    track(F)
    v = F.sp.get(p) if loc[0] == 's' else F.bp.get(p)
    return None if F.multi or v is None else v + (d if loc[0] == 's' and not before else 0) + loc[1]


def aliased(F, c, sz):
    if not hasattr(F, 'tc'):
        ev = frame_events(F)
        F.tc, F.tall = [e[1] for e in ev if e[1] is not None], any(e[2] or e[1] is None for e in ev)
    return F.multi or F.tall or any(c <= t < c + sz for t in F.tc)


def forced(A, O):
    """x87 CW transform -> (rc or None, pc_touched)"""
    r = 0
    for b in (10, 11):
        if O >> b & 1:
            r |= 1 << (b - 10)
        elif A >> b & 1:
            return None, bool(O & 0x300 or (A & 0x300) != 0x300)
    return r, bool(O & 0x300 or (A & 0x300) != 0x300)


def classify(vs, mx):
    cs = sorted(v for v in vs if v[0] == 'c')
    ts = sorted(set(v[1:3] for v in vs if v[0] == 't'))
    other = [v for v in vs if v[0] not in 'ct']
    if len(cs) == len(vs) and cs:
        return 'constant', ','.join('%04x' % (v[1] & (M32 if mx else 0xffff)) for v in cs), []
    if len(ts) == 1 and len(vs) == 1 or (ts and len({t for t in ts}) == 1 and not cs and not other):
        A, O = ts[0]
        if A == M32 and O == 0:
            return 'bracket-restore', '', []
        if not mx:
            rc, pc = forced(A, O)
            if rc is not None:
                return 'bracket-set-RC(%d)' % rc, 'pc-touched' if pc else 'pc-kept', []
        return 'bracket-set-other', 'and=%x,or=%x' % (A & M32, O), []
    return 'unknown', ','.join(sorted({v[0] if v[0] != '?' else v[1] for v in vs})), []


def between(F, x):
    """x87 mnemonics/calls between a fldcw at x and the next fldcw reached (forward over the CFG)."""
    seen, work, kinds = set(), list(F.succ[x]), set()
    while work:
        a = work.pop()
        if a in seen:
            continue
        seen.add(a); i = F.ins[a]; m = mn(i)
        if m == 'fldcw':
            kinds.add('restore'); continue
        if m == 'call':
            kinds.add('call')
        elif isx87(m) and m not in FSAFE:
            kinds.add('arith')
        elif m in ('fist', 'fistp', 'fisttp'):
            kinds.add('int')
        if m in ('fist', 'fistp', 'fisttp'):
            kinds.add('int')
        work += F.succ[a]
    return 'int-only' if 'int' in kinds and not kinds & {'arith', 'call'} and 'restore' in kinds else 'arith-or-unproven'


SAHF_OK = {'b', 'ae', 'e', 'ne', 'be', 'a', 'p', 'np'}
TESTM = X.X86_EFLAGS_TEST_AF | X.X86_EFLAGS_TEST_CF | X.X86_EFLAGS_TEST_DF | X.X86_EFLAGS_TEST_OF | X.X86_EFLAGS_TEST_PF | X.X86_EFLAGS_TEST_SF | X.X86_EFLAGS_TEST_ZF
MODM = X.X86_EFLAGS_MODIFY_CF | X.X86_EFLAGS_MODIFY_ZF | X.X86_EFLAGS_MODIFY_PF


def statclass(F, x):
    i = F.ins[x]
    if i.operands[0].type != X.X86_OP_REG:
        return 'status-escapes', 'mem'
    seen, work = set(), [(s, (1, 1, 0)) for s in F.succ[x]]      # state: (low byte live, ah raw 1/cc 2, flags raw)
    if not work:
        return 'status-escapes', 'no-succ'
    while work:
        a, st = work.pop()
        if (a, st) in seen:
            continue
        seen.add((a, st))
        if len(seen) > 600:
            return 'status-escapes', 'walk-limit'
        i = F.ins[a]; m = mn(i); o = i.operands; lo, ah, fl = st
        nm = [i.reg_name(op.reg) if op.type == X.X86_OP_REG else None for op in o]
        rd = {SUB[i.reg_name(r)][0] for r in i.regs_access()[0] if i.reg_name(r) in SUB}
        if m == 'call':
            if (lo or ah == 1) and o[0].type == X.X86_OP_IMM and ((o[0].imm & M32) in EAXLIVE or (o[0].imm & M32) not in KNOWN):
                return 'status-escapes', 'call-reads-eax'
            lo = ah = 0                               # ABI assumption: a call clobbers eax (callee does not read it)
        elif m in ('ret', 'retf') or a in F.tails:
            if lo or ah == 1:
                return 'status-escapes', 'ret/tail'
            continue
        if fl:                                            # flags loaded raw from ah by sahf
            if m[0] == 'j' and m != 'jmp' or m.startswith(('set', 'cmov')):
                c = m[1:] if m[0] == 'j' else m[3:] if m.startswith('set') else m[4:]
                if c not in SAHF_OK:
                    return 'status-escapes', 'sahf-cond-' + c
            elif i.eflags & TESTM:
                return 'status-escapes', 'sahf-flagread-' + m
            elif i.eflags & MODM == MODM:
                fl = 0
        if m == 'sahf':
            fl = 1 if ah == 1 else fl
        elif 'eax' in rd and (lo or ah):
            r0 = nm[0]
            imm = o[1].imm & M32 if len(o) > 1 and o[1].type == X.X86_OP_IMM else None
            fam = {'ah': 0x7f00, 'al': 0xff, 'ax': 0xffff, 'eax': 0xffff}
            if m in ('xor', 'sub') and nm[0] == nm[1] and r0 in fam:
                lo = ah = 0
            elif m == 'test' and imm is not None and (r0 in fam) and ((r0 == 'ah' and ah == 2) or not (((imm << 8) if r0 == 'ah' else imm) & 0xffff) & ~0x7f00):
                pass
            elif m in ('and', 'or', 'xor') and imm is not None and r0 == 'ah':
                if m == 'and' and imm & ~0x47 == 0:
                    ah = 2
            elif m == 'and' and imm is not None and r0 in ('ax', 'eax') and not imm & 0xff:
                lo = 0
                if (imm >> 8) & 0xff & ~0x47 == 0:
                    ah = 2
            elif m == 'cmp' and imm is not None and r0 == 'ah' and ah == 2:
                pass
            else:
                return 'status-escapes', 'use-' + m
        wr = [i.reg_name(r) for r in i.regs_access()[1]]
        for w in wr:
            if w in ('eax', 'ax'):
                lo = ah = 0
            elif w == 'al':
                lo = 0
            elif w == 'ah':
                ah = 0
        if ah == 2 and not lo:
            fl = 0
        if not lo and not ah and not fl:
            continue
        for s in F.succ[a]:
            work.append((s, (lo, ah, fl)))
        if not F.succ[a] and (lo or ah == 1) and m not in ('ret', 'retf'):
            return 'status-escapes', 'end/tail'
    return 'cc-only', ''


IMPL = {'mul', 'imul', 'div', 'idiv', 'cdq', 'cwde', 'cwd', 'cbw', 'lodsb', 'lodsw', 'lodsd', 'stosb', 'stosw', 'stosd', 'scasb', 'scasw', 'scasd',
        'xchg', 'cmpxchg', 'xadd', 'lahf', 'sahf', 'xlatb', 'cpuid', 'rdtsc', 'in', 'out', 'fnstsw', 'aaa', 'aas', 'daa', 'das', 'aam', 'aad'}


def eaxscan(F):
    """(reads eax before writing it on some path, direct callees/tails reached with eax unwritten, unknown)"""
    seen, work, rd, cl = set(), [F.a], False, set()
    while work and not rd:
        a = work.pop()
        if a in seen:
            continue
        seen.add(a); i = F.ins[a]; m = mn(i); o = i.operands
        if m == 'call' or m == 'jmp' and a in F.tails:
            t = o[0]
            if t.type == X.X86_OP_IMM:
                cl.add(t.imm & M32)
            elif m == 'jmp' and not (t.type == X.X86_OP_MEM and not t.mem.base and not t.mem.index):
                rd = True
            if m == 'call' or a in F.tails and not F.succ[a]:
                continue
        if m in ('ret', 'retf'):
            continue
        if m in IMPL or 'ax' in i.op_str or 'al' in i.op_str or 'ah' in i.op_str:
            r, w = [{i.reg_name(x) for x in g} for g in i.regs_access()[:2]]
            if r & {'eax', 'ax', 'al', 'ah'} and not (m in ('xor', 'sub') and len(o) == 2 and o[0].type == o[1].type == X.X86_OP_REG and o[0].reg == o[1].reg):
                rd = True; break
            if 'eax' in w or 'ax' in w and 0:
                continue
        work += F.succ[a]
    return rd, sorted(cl)


def work(args):
    a, ext, kind, mode = args
    F = Fn(a, ext, kind)
    if not F.ok:
        return a, None
    if mode == 'depth':
        return a, dict(depth(F, T), calls=None)
    r = dict(sites=[], calls=set(), absw=[], dep=None)
    gotx87 = False
    for x in sorted(F.ins):
        i = F.ins[x]; m = mn(i); o = i.operands
        if m == 'call' and o[0].type == X.X86_OP_IMM:
            r['calls'].add(o[0].imm & M32)
        if isx87(m) or m in ('ldmxcsr', 'stmxcsr', 'fxrstor', 'fxsave'):
            gotx87 = True
        for op in o:
            if op.type == X.X86_OP_MEM and not op.mem.base and not op.mem.index and not op.mem.segment and (op.access & 2 or m.startswith(WSTORE)):
                r['absw'].append((op.mem.disp & M32, x, o[1].imm & M32 if len(o) > 1 and m == 'mov' and o[1].type == X.X86_OP_IMM else -1))
        if m in ('fldcw', 'ldmxcsr'):
            mx = m == 'ldmxcsr'; F.w = 4 if mx else 2; l = memslot(i, o[0], F.w)
            if l is None:
                r['sites'].append((m, x, 'unknown', 'ptr', '')); continue
            if l[0] == 'g':
                r['sites'].append((m, x, 'global', '%08x' % l[1], '')); continue
            c, det, cv = classify(val(F, x, l), mx)
            ex = ''
            if c.startswith('bracket-set') and not mx:
                ex = between(F, x)
            r['sites'].append((m, x, c, det, ex))
        elif m in ('fninit', 'finit'):
            r['sites'].append((m, x, 'constant', '037f', ''))
        elif m in ('frstor', 'fldenv', 'fxrstor'):
            r['sites'].append((m, x, 'unknown', 'env-load', ''))
        elif m in ('fnstsw', 'fstsw'):
            r['sites'].append((m, x, 'pending', '', ''))
        elif m in ('fnstenv', 'fstenv', 'fnsave', 'fsave', 'fxsave', 'fnclex', 'fclex', 'stmxcsr', 'wait', 'fwait'):
            r['sites'].append((m, x, 'seen', '', ''))
    if gotx87 or r['calls'] or F.tails:
        r['calls'] |= {t for ts in F.tails.values() for t in ts}
        r['dep'] = depth(F, T)
    r['calls'] = sorted(r['calls'])
    r['eax'] = eaxscan(F)
    if any(s[3] == 'arg' or s[3].startswith('arg') for s in r['sites']):
        pass
    return a, r


def statpass(args):
    a, ext, kind, xs = args
    F = Fn(a, ext, kind)
    return [(x,) + statclass(F, x) for x in xs] if F.ok else []


def callargs(args):
    a, ext, kind, targets = args
    F = Fn(a, ext, kind); res = []
    if not F.ok:
        return res
    for x in sorted(F.ins):
        i = F.ins[x]; o = i.operands
        if mn(i) == 'call' and o[0].type == X.X86_OP_IMM and (o[0].imm & M32) in targets:
            a1, pos, got = [], x, 0
            while len(a1) < 2:
                ps = F.preds.get(pos, [])
                if len(ps) != 1:
                    break
                p = ps[0]; q = F.ins[p]
                if mn(q) == 'push':
                    v = srcval(F, p, q, q.operands[0], 0)
                    a1.append(next(iter(v))[1] if len(v) == 1 and next(iter(v))[0] == 'c' else None)
                elif espd(q) != 0 or mn(q) == 'call':
                    break
                pos = p
            res.append((o[0].imm & M32, x, a, (a1 + [None, None])[:2]))
    return res


def main():
    raw, rows = load_universe(EXE)
    print('fpmodes: exe sha256 %s  universe.tsv md5 %s  entries %d' % (hashlib.sha256(open(EXE, 'rb').read()).hexdigest()[:12], hashlib.md5(raw).hexdigest()[:12], len(rows)))
    kind = {a: k for a, e, k in rows}; ext = {a: e for a, e, k in rows}
    res = {}
    with Pool(2) as p0:
        W.prepare(p0, rows, hashlib.sha256(open(EXE, 'rb').read()).hexdigest()[:16] + hashlib.md5(raw).hexdigest()[:16])
    with Pool(2) as pool:
        for a, r in pool.imap(work, [(a, e, k, 'all') for a, e, k in rows], chunksize=200):
            res[a] = r
    bad = sorted(a for a in res if res[a] is None)
    KNOWN.update(a for a in res if res[a])
    live = {a for a, r in res.items() if r and r['eax'][0]}
    for _ in range(50):
        nl = {a for a, r in res.items() if r and a not in live and any(c in live or c not in KNOWN for c in r['eax'][1])}
        if not nl:
            break
        live |= nl
    EAXLIVE.update(live | set(bad))
    pend = defaultdict(list)
    for a in sorted(res):
        for sdef in (res[a]['sites'] if res[a] else ()):
            if sdef[2] == 'pending':
                pend[a].append(sdef[1])
    with Pool(2) as pst:
        stat = {x: (c, why) for lst in pst.imap(statpass, [(a, ext[a], kind[a], xs) for a, xs in sorted(pend.items())], chunksize=20) for x, c, why in lst}
    callers = defaultdict(set)
    for a, r in res.items():
        for t in (r['calls'] if r else ()):
            callers[t].add(a)
    dep = {a: r['dep'] for a, r in res.items() if r and r['dep']}
    stable = False
    for wave in range(10):                       # fixed point of callee return depth
        new = {a: d['ret'] for a, d in dep.items() if d['ret'] and not d['status'].startswith('conflict') and not d['nodepth']}
        newu = {a for a, d in dep.items() if d['status'].startswith('conflict') or d['nodepth']}
        if new == T and newu == UNC and wave:
            stable = True; break
        ch = sorted({a for a in sorted(set(new) | set(T)) if new.get(a, 0) != T.get(a, 0)} | (newu ^ UNC))
        T.clear(); T.update(new); UNC.clear(); UNC.update(newu)
        todo = sorted({c for a in ch for c in callers.get(a, ()) if c in res and res[c]})
        with Pool(2) as p2:
            for a, d in p2.imap(work, [(a, ext[a], kind[a], 'depth') for a in todo], chunksize=50):
                if d:
                    dep[a] = d
    os.makedirs(OUT, exist_ok=True)
    sites = {}
    for a in sorted(res):
        for s in (res[a]['sites'] if res[a] else ()):
            if s[2] == 'pending':
                s = (s[0], s[1]) + stat.get(s[1], ('status-escapes', 'undecided')) + ('',)
            sites.setdefault(s[1], (s, a))
    crt = lambda a: kind[a] not in ('decomp', 'inner')
    L = []
    cnt = Counter(s[0] for s, a in sites.values())
    L.append('x87/SSE control writers (sites): ' + ' '.join('%s=%d' % (k, cnt[k]) for k in ('fldcw', 'fninit', 'finit', 'frstor', 'fldenv', 'fxrstor', 'ldmxcsr')))
    absw = defaultdict(list)
    for a in sorted(res):
        for w in (res[a]['absw'] if res[a] else ()):
            absw[w[0]].append(w[1:] + (a,))
    bl = []; cw = Counter(); consts = set(); mxc = Counter(); mconsts = set(); br = Counter(); pcc = Counter(); gl = {}; unk = defaultdict(list); setters = set()
    for s, a in sites.values():
        m, x, c, det, ex = s
        mxm = m == 'ldmxcsr'
        cl = c if c in ('global', 'unknown') else c.split('(')[0] if c.startswith('constant') else c
        if m not in ('fldcw', 'ldmxcsr', 'fninit', 'finit', 'frstor', 'fldenv', 'fxrstor'):
            continue
        (mxc if mxm else cw)[cl] += 1
        if c == 'constant':
            (mconsts if mxm else consts).update(int(v, 16) for v in det.split(','))
        if c.startswith('bracket') and not mxm:
            br[ex or '-'] += 1
            pcc[det] += 1 if c.startswith('bracket-set') else 0
        if c == 'global':
            gl.setdefault(int(det, 16), []).append(x)
        if c == 'unknown':
            unk[(mxm, det)].append(x)
            if not mxm and det in ('alias', 'partial', 'budget'):
                bl.append(x)
            if 'arg' in det.split(','):
                setters.add(a)
    L.append('fldcw classes: ' + ' '.join('%s=%d' % kv for kv in sorted(cw.items())))
    L.append('fldcw constant control words: ' + ' '.join('%04x' % v for v in sorted(consts)) + '  (+ fninit 037f)')
    L.append('bracket between-fldcw: ' + ' '.join('%s=%d' % kv for kv in sorted(br.items())) + '   pc: ' + ' '.join('%s=%d' % kv for kv in sorted(pcc.items()) if kv[1]))
    for g, xs in sorted(gl.items()):
        w = absw.get(g, [])
        L.append('fldcw global %08x: %d site(s); direct writers %d (consts %s; any non-const %s)' % (g, len(xs), len(w), sorted({'%x' % x[1] for x in w if x[1] >= 0}), any(x[1] < 0 for x in w)))
    for (mxm, why), xs in sorted(unk.items()):
        L.append('unknown %s [%s]: %d  e.g. %s' % ('ldmxcsr' if mxm else 'fldcw', why, len(xs), ' '.join('%08x' % x for x in xs[:5])))
    L.append('  of the unknown fldcw: %d are alias/partial/budget cases (pattern fnstcw..fistp..fldcw present in their function, unproven)' % len(bl))
    L.append('MXCSR ldmxcsr classes: ' + ' '.join('%s=%d' % kv for kv in sorted(mxc.items())) + '  constants: ' + ' '.join('%08x' % v for v in sorted(mconsts)))
    # CRT setters / readers and their callers
    K = set(setters) | {a for s, a in sites.values() if s[0] in ('fnclex', 'fclex', 'fninit', 'finit') and crt(a)} | {a for s, a in sites.values() if s[2] == 'status-escapes' and crt(a) and ext[a] < 400}
    L.append('CRT control/status entries (arg-derived fldcw/ldmxcsr, or CRT-range fnclex/fninit/escaping fnstsw, <400B): %d: %s' % (len(K), ' '.join('%08x' % a for a in sorted(K)[:12])))
    cs = []
    todo = sorted({c for k in K for c in callers.get(k, ())})
    with Pool(2) as p3:
        for r in p3.imap(callargs, [(a, ext[a], kind[a], K) for a in todo]):
            cs += r
    cs.sort()
    argset = Counter((x[3][0], x[3][1]) for x in cs if x[0] in setters)
    L.append('calls to CRT entries: %d sites (%d to arg-setting entries; const (a1,a2) pairs: %d, with non-const arg: %d)' % (len(cs), sum(argset.values()), sum(1 for k in argset if None not in k), sum(v for k, v in argset.items() if None in k)))
    L.append('  arg pairs (a1,a2 as pushed, top first): ' + ' '.join('%s:%d' % (('%x,%x' % k) if None not in k else '?', v) for k, v in sorted(argset.items(), key=lambda kv: (-kv[1], str(kv[0])))[:8]))
    sc = Counter(); esc = []; reasons = Counter(); misc = defaultdict(list)
    for s, a in sites.values():
        if s[0] in ('fnstsw', 'fstsw'):
            sc[s[2]] += 1
            if s[2] == 'status-escapes':
                esc.append((s[1], a, crt(a))); reasons[s[3].split('-')[0] + ('-' + s[3].split('-')[1] if s[3].startswith('use') else '')] += 1
        elif s[2] == 'seen':
            misc[s[0].replace('fn', 'f').replace('fwait', 'wait')].append((s[1], a))
    L.append('fnstsw/fstsw sites: %d  cc-only=%d status-escapes=%d (in CRT range %d, decomp %d)' % (sum(sc.values()), sc['cc-only'], sc['status-escapes'], sum(1 for e in esc if e[2]), sum(1 for e in esc if not e[2])))
    L.append('  escape reasons: ' + ' '.join('%s=%d' % kv for kv in sorted(reasons.items(), key=lambda kv: -kv[1])[:10]))
    L.append('  escape examples: ' + ' '.join('%08x(%08x,%s)' % (x, a, 'crt' if c else 'game') for x, a, c in esc[:5]))
    for k in ('fstenv', 'fsave', 'fxsave', 'fclex', 'wait', 'stmxcsr'):
        L.append('%s sites: %d in %d entries  e.g. %s' % (k, len(misc[k]), len({a for _, a in misc[k]}), ' '.join('%08x(%08x)' % v for v in misc[k][:3])))
    ds = {a: d for a, d in dep.items() if d['x87']}
    sc2 = Counter(d['status'] for d in ds.values())
    cf = sorted(a for a, d in ds.items() if d['status'].startswith('conflict') or d['nodepth'])
    L.append('x87 depth: entries using x87=%d; static (all unknown call results 0)=%d; static only after inferring unknown-callee result class=%d (via indirect call %d); '
             'conflict=%d (undecided %d); frstor/fldenv unknown=%d; undecodable entries=%d' % (len(ds), sc2['static'], sc2['inferred'], sum(1 for d in ds.values() if d['status'] == 'inferred' and d['ind']),
             sc2['conflict'] + sc2['conflict-undecided'], sc2['conflict-undecided'], sum(d['nodepth'] for d in ds.values()), len(bad)))
    L.append('  conflicted entries by kind: ' + ' '.join('%s=%d' % kv for kv in sorted(Counter(kind[a] for a in cf).items())) + '; first10: ' + ' '.join('%08x' % a for a in cf[:10]))
    L.append('  max depth (relative to entry)=%d  entries>8=%d  entries consuming st inputs (min<0)=%d  net ret depth histogram: %s' % (
        max([d['mx'] for d in ds.values()] + [0]), sum(1 for d in ds.values() if d['mx'] > 8), sum(1 for d in ds.values() if d['mi'] < 0), dict(sorted(Counter(d['ret'] for d in dep.values()).items()))))
    L.append('  x87 entries with an indirect (non-IAT) call=%d; indirect call immediately followed by x87 insn=%d entries; ret-depth fixpoint stable=%s' % (
        sum(1 for d in ds.values() if d['ind']), sum(1 for d in ds.values() if d['inddep']), stable))
    L.append('files: ' + OUT)
    print('\n'.join(L))
    with open(OUT + '/sites.tsv', 'w') as f:
        for x in sorted(sites):
            s, a = sites[x]
            f.write('%08x\t%s\t%s\t%s\t%s\t%s\tentry=%08x\t%s\n' % (x, s[0], s[2], s[3], s[4], 'crt' if crt(a) else 'game', a, kind[a]))
    with open(OUT + '/depth.tsv', 'w') as f:
        for a in sorted(ds):
            d = ds[a]
            f.write('%08x\t%s\tmax=%d\tmin=%d\tret=%d\tconf=%d\tind=%d\t%s\n' % (a, d['status'], d['mx'], d['mi'], d['ret'], d['nconf'], d['ind'], ' '.join('%08x:%s/%s' % c for c in d['conf'])))
    with open(OUT + '/crt_calls.tsv', 'w') as f:
        for tgt, x, a, ar in cs:
            f.write('%08x\t%08x\tentry=%08x\ta1=%s\ta2=%s\n' % (tgt, x, a, *['?' if v is None else '%x' % v for v in ar]))
    with open(OUT + '/undecodable.tsv', 'w') as f:
        f.write(''.join('%08x\n' % a for a in bad))


if __name__ == '__main__':
    main()
