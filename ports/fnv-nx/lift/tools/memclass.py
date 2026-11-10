#!/usr/bin/env python3
"""W10b: class of every memory operand of FalloutNV.exe (rule R9). Class (a) = plain load/store, by PROOF:
 rule 1  a frame none of whose addresses escapes is private: locals, incoming and outgoing argument slots are plain (total esp/ebp
         tracking in x86flow; funclets are analysed as part of their parent frame);
 rule 2  a frame address that only reaches direct calls whose parameter summary says "does not escape" (greatest fixed point over
         the call graph) contaminates nothing; otherwise it contaminates its object extent (up to the next directly accessed slot).
 ro-const (IAT, .rdata, jump tables) is plain; TLS stays ordered. Count-only stdout; per-entry TSV in /home/user/l3facts/memclass/."""
import os, sys, json, bisect, hashlib
from array import array
from collections import Counter, defaultdict
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x86flow as W
from x86flow import X, M32, mn, rn, base, memslot, Fn, FUNK, KINDS, THUNK, IATADDR, track, frame_events, load_universe, pmap

EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
OUT = '/home/user/l3facts/memclass'
STK = ('stack-local', 'stack-args-in', 'stack-args-out', 'stack-funclet', 'stack-escaped', 'stack-unk')
ORDER = STK + ('ro-iat', 'ro-rdata', 'ro-text', 'tls', 'tls-derived', 'global', 'heap/other')
PLAIN = ('stack-local', 'stack-args-in', 'stack-args-out', 'stack-funclet', 'ro-iat', 'ro-rdata', 'ro-text')
WID = (1, 2, 4, 8, 10, 16)
REGS = ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')
IMPL = frozenset('mul imul div idiv cdq cwde cwd cbw lodsb lodsw lodsd stosb stosw stosd scasb scasw scasd xchg cmpxchg xadd lahf sahf xlatb'.split())
BEFORE = {'stack-local': 44.6, 'stack-args-in': 5.3, 'stack-args-out': 9.8, 'stack-escaped': 7.6, 'stack-unk': 11.1}   # v1 run (recorded)


def absclass(a, store):
    for n, va, vs, ro, rs, ch in W.IMG.secs:
        if va <= a < va + max(vs, rs):
            if a in IATADDR and not store:
                return 'ro-iat'
            return 'global' if ch & 0x80000000 or store else 'ro-text' if ch & 0x20000000 else 'ro-rdata'
    return 'heap/other'


def tls_taint(F, i, m, o, r0, taint):
    """taint {reg: 'A' (fs:[0x2c] array pointer) | 'S' (element loaded through it)}; returns an offence if an 'A' value leaves in a register."""
    nm = lambda p: i.reg_name(p.reg) if p.type == X.X86_OP_REG else None
    if m == 'push' and o[0].type == X.X86_OP_MEM and o[0].mem.segment and o[0].mem.disp == 0x2c:
        return 'push fs:[2c]'
    if m in ('call', 'ret', 'retf'):
        bad = [r for r in ('eax', 'ecx', 'edx') if taint.get(r) == 'A'] if m == 'call' else ['eax'] if taint.get('eax') == 'A' else []
        for r in ('eax', 'ecx', 'edx'):
            taint.pop(r, None)
        return m + ' with tainted ' + bad[0] if bad else None
    new = None
    if m == 'mov' and r0 and o[1].type == X.X86_OP_MEM:
        mm = o[1].mem
        if mm.segment and i.reg_name(mm.segment) == 'fs' and not mm.base and not mm.index and mm.disp == 0x2c:
            new = 'A'
        elif (mm.base and taint.get(i.reg_name(mm.base)) == 'A') or (mm.index and taint.get(i.reg_name(mm.index)) == 'A'):
            new = 'S'
    elif m == 'mov' and r0 and o[1].type == X.X86_OP_REG and taint.get(nm(o[1])) == 'A':
        new = 'A'
    elif m == 'lea' and r0 and o[1].mem.base and taint.get(i.reg_name(o[1].mem.base)) == 'A':
        new = 'A'
    off = None
    for k, p in enumerate(o):
        if p.type == X.X86_OP_REG and taint.get(nm(p)) == 'A' and p.access & 1 and m not in ('cmp', 'test') and not (m == 'mov' and k == 1 and r0 and o[0].type == X.X86_OP_REG):
            if not (m in ('add', 'sub', 'inc', 'dec') and k == 0):
                off = off or '%s uses tainted %s' % (m, nm(p))
    if r0 and o[0].access & 2 and o[0].type == X.X86_OP_REG:
        taint.pop(r0, None)
        if new:
            taint[r0] = new
    return off


def argreach(F):
    """reach[x]: a call follows x on the fall-through chain (x = outgoing-argument push candidate); saved: entry register saves."""
    reach = {}
    for x in sorted(F.ins, reverse=True):
        i = F.ins[x]; m = mn(i)
        if m == 'call':
            reach[x] = True
        elif m in ('pop', 'ret', 'retf', 'jmp') or m[0] == 'j' or (m == 'add' and rn(i, i.operands[0]) == 'esp') or len(F.succ[x]) != 1:
            reach[x] = False
        else:
            reach[x] = reach.get(F.succ[x][0], False)
    saved, k = [], F.a
    while k in F.ins and len(F.succ[k]) == 1:
        i = F.ins[k]; m = mn(i)
        if m == 'push' and rn(i, i.operands[0]) in ('ebp', 'ebx', 'esi', 'edi'):
            saved.append(k)
        elif not (m == 'mov' and rn(i, i.operands[0]) == 'ebp') and not (m == 'sub' and rn(i, i.operands[0]) == 'esp'):
            break
        k = F.succ[k][0]
    return reach, frozenset(saved)


def target(F, x):
    """sink of a value passed at the call/tail jump x: ('A', callee) | ('I',) | ('M', import)."""
    o = F.ins[x].operands[0]
    if o.type == X.X86_OP_IMM:
        t = o.imm & M32
        return ('M', IATADDR.get(THUNK[t], '?')) if t in THUNK else ('A', t)
    if o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and (o.mem.disp & M32) in IATADDR:
        return ('M', IATADDR[o.mem.disp & M32])
    return ('I',)


def pusharg(F, x):
    """(call address, argument index) of the push at x, or None."""
    k = x
    for _ in range(40):
        k = F.succ[k][0] if len(F.succ.get(k, ())) == 1 else None
        if k is None:
            return None
        if mn(F.ins[k]) == 'call':
            a, b = F.sp.get(k), F.sp.get(x)
            if a is None or b is None or F.multi:
                a, b, j = 0, 0, x                        # fall back to the local chain: sum the known stack deltas from the push to the call
                while j != k:
                    q = F.ins[j]; mq = mn(q); cp = W.callpop(F, j) if mq == 'call' else None
                    d = -4 if mq == 'push' else 4 if mq == 'pop' else (q.operands[1].imm * (1 if mq == 'add' else -1) if mq in ('add', 'sub') and rn(q, q.operands[0]) == 'esp' and q.operands[1].type == X.X86_OP_IMM else None) if mq in ('add', 'sub') and rn(q, q.operands[0]) == 'esp' else 0
                    if d is None or (cp and cp[0] != 'c'):
                        return None
                    a += d + (cp[1] if cp else 0); j = F.succ[j][0]
            return (k, (b - a - 4) // 4) if b - a - 4 >= 0 else None
        if mn(F.ins[k]) in ('pop', 'ret', 'jmp') or mn(F.ins[k])[0] == 'j':
            return None
    return None


def sink(F, x, par):
    t = target(F, x)
    return t + (par,) if t[0] == 'A' else t


def fcoord(F, a, p):
    """entry-relative coordinate of a direct esp/ebp memory operand at a, or None."""
    i = F.ins[a]
    if p.type != X.X86_OP_MEM or not p.mem.base or p.mem.index or F.multi:
        return None
    bs = i.reg_name(p.mem.base)
    v = F.sp.get(a) if bs == 'esp' else F.bp.get(a) if bs == 'ebp' else None
    return None if v is None else v + p.mem.disp


def flow(F, x0, regs, saved, entry=False, spill=False, slots0=()):
    """sinks of the frame / parameter address held in regs after x0 (or at entry): set of ('S',) ('R',) ('I',) ('U',) ('M', n) ('A', callee, param)."""
    sinks, seen = set(), set()
    work = [(x0, frozenset(regs), frozenset(slots0), frozenset())] if entry else [(s, frozenset(regs), frozenset(slots0), frozenset()) for s in F.succ[x0]]
    while work:
        a, T, S, P = work.pop()
        if (a, T, S, P) in seen:
            continue
        seen.add((a, T, S, P))
        if len(seen) > 400:
            sinks.add(('U', 'limit')); break
        i = F.ins[a]; m = mn(i); o = i.operands; N = set(T); NS = set(S); NP = set(P)
        reg = lambda p: base(i.reg_name(p.reg)) if p.type == X.X86_OP_REG else None
        mc = [fcoord(F, a, p) for p in o]
        if S and m not in ('mov', 'push', 'cmp', 'test') and any(c in S for c in mc if c is not None):
            sinks.add(('U', 'slot-use'))
        if m == 'call' or (m == 'jmp' and (a in F.tails or not F.succ[a])):
            for r in sorted(T & set(REGS)):
                sinks.add(sink(F, a, r))
            direct = target(F, a)[0] == 'A'
            N = set() if m == 'jmp' else (N - {'eax', 'ecx', 'edx'}) | ({'eax'} if direct and (a in P or T & {'eax', 'ecx', 'edx'}) else set())   # a direct callee may return its argument
            NP.discard(a)
        elif m in ('ret', 'retf'):
            if 'eax' in T:
                sinks.add(('R',))
            N = set()
        elif m == 'push' and (reg(o[0]) in T or (mc[0] is not None and mc[0] in S)):
            pa = None if a in saved else pusharg(F, a)
            if a not in saved:
                sinks.add(('U', 'pusharg') if pa is None else sink(F, pa[0], 's%d' % pa[1]))
                if pa is not None:
                    NP.add(pa[0])
        elif m in ('mov', 'lea') and o[0].type == X.X86_OP_REG and base(i.reg_name(o[0].reg)) is not None:
            d = reg(o[0])
            src = reg(o[1]) if o[1].type == X.X86_OP_REG else None
            if m == 'mov' and o[1].type == X.X86_OP_REG and src in T:
                N.add(d)
            elif m == 'lea' and ((o[1].mem.base and base(i.reg_name(o[1].mem.base)) in T) or (o[1].mem.index and base(i.reg_name(o[1].mem.index)) in T)):
                N.add(d)
            elif m == 'mov' and mc[1] is not None and mc[1] in S:
                N.add(d)
            elif o[0].size == 4:
                N.discard(d)
        elif m == 'mov' and o[0].type == X.X86_OP_MEM and len(o) > 1 and reg(o[1]) in T:
            mm = o[0].mem
            if mm.segment and i.reg_name(mm.segment) == 'fs':
                pass
            elif spill and mc[0] is not None:
                NS.add(mc[0])
            elif mm.base and i.reg_name(mm.base) in ('esp', 'ebp') and not mm.index:
                sinks.add(('U', 'spill'))
            else:
                sinks.add(('S',))
        elif m in ('cmp', 'test'):
            pass
        elif m == 'mov' and o[0].type == X.X86_OP_MEM and mc[0] is not None and mc[0] in S:
            NS.discard(mc[0])
        else:
            for k, p in enumerate(o):
                r = reg(p)
                if r in T and p.access & 1 and not (k == 0 and m in ('add', 'sub', 'and', 'or', 'inc', 'dec', 'xor') and (len(o) < 2 or o[1].type == X.X86_OP_IMM)):
                    sinks.add(('U', 'use:' + m))
                elif r in T and p.access & 2 and k == 0 and not p.access & 1:
                    N.discard(r)
            if 'eax' in T and m in IMPL:
                sinks.add(('U', 'implicit-eax'))
        if N or NS or NP:
            work += [(s, frozenset(N), frozenset(NS), frozenset(NP)) for s in F.succ[a]]
    return sinks


def livein(F, saved):
    """registers possibly read (or passed on at a call / tail jump) before being written, from the entry."""
    seen, work, rd = set(), [(F.a, frozenset(REGS))], set()
    while work:
        a, T = work.pop()
        if (a, T) in seen or not T:
            continue
        seen.add((a, T))
        i = F.ins[a]; m = mn(i); o = i.operands; N = set(T)
        used = {base(i.reg_name(p.reg)) for p in o if p.type == X.X86_OP_REG and p.access & 1}
        used |= {base(i.reg_name(p.mem.base)) for p in o if p.type == X.X86_OP_MEM and p.mem.base} | {base(i.reg_name(p.mem.index)) for p in o if p.type == X.X86_OP_MEM and p.mem.index}
        if m in IMPL:
            used.add('eax')
        if m in ('xor', 'sub') and len(o) == 2 and o[0].type == o[1].type == X.X86_OP_REG and o[0].reg == o[1].reg:
            used = set()
        if m == 'call':
            used |= {'eax', 'ecx', 'edx'}
        if m == 'jmp' and a in F.tails:
            used |= set(REGS)
        if a in saved:
            used = set()
        rd |= used & T
        for p in o[:1]:
            if p.type == X.X86_OP_REG and p.access & 2 and not p.access & 1 and p.size == 4 and m != 'push':
                N.discard(base(i.reg_name(p.reg)))
        if m == 'call':
            N -= {'eax', 'ecx', 'edx'}
        work += [(s, frozenset(N)) for s in F.succ[a]]
    return sorted(rd)


def analyze(args):
    a, ext, kind = args
    F = Fn(a, ext, kind)
    if not F.ok:
        return a, None
    track(F)
    fun = kind in FUNK
    reach, saved = argreach(F)
    nf, fr, misc, ev, srcs, taint, prev, offs, cidx = [], [], [], [], [], {}, None, [], False
    allstack = F.multi and not fun
    aw = {'multi'} if allstack else set()
    for x in sorted(F.ins):
        i = F.ins[x]; m = mn(i); o = i.operands; s = F.sp.get(x); b = F.bp.get(x)
        if F.preds.get(x, []) != [prev]:
            taint = {}
        prev = x
        r0 = rn(i, o[0]) if o else None
        ok = s is not None and not F.multi
        if m in ('push', 'pushfd'):
            if fun or not ok:
                fr.append((x, 1, 4, 'u', 0))
            else:
                fr.append((x, 1, 4, 'o' if reach.get(x) and x not in saved else 'l', s - 4))
        elif m in ('pop', 'popfd'):
            fr.append((x, 0, 4, 'u' if fun or not ok else 'l', 0 if fun or not ok else s))
        elif m in ('call', 'ret', 'retf'):
            nf.append((x, 1 if m == 'call' else 0, 4, 'stack-local'))
        elif m == 'leave':
            fr.append((x, 0, 4, 'u' if fun or b is None or F.multi else 'r', 0 if fun or b is None or F.multi else b))
        if taint or any(p.type == X.X86_OP_MEM and p.mem.segment and p.mem.disp == 0x2c for p in o):
            td = tls_taint(F, i, m, o, r0, taint)
            if td:
                offs.append((x, td))
        if i.prefix[0] == 0xf0:
            misc.append((x, 'lock'))
        if m in ('xchg', 'cmpxchg', 'xadd') and any(p.type == X.X86_OP_MEM for p in o):
            misc.append((x, m))
        if m == 'call':
            t = target(F, x)
            if t[0] == 'M':
                misc.append((x, 'imp:' + t[1]))
        for p in o:
            if p.type != X.X86_OP_MEM or m in ('lea', 'nop') or m.startswith('prefetch'):
                continue
            ld, stt = bool(p.access & 1), bool(p.access & 2)
            if m[0] == 'f' and m not in ('fxsave', 'fxrstor'):
                stt = m.startswith(('fst', 'fist', 'fnst', 'fbst', 'fnsave', 'fsave')); ld = not stt
            if m in ('fxsave', 'stmxcsr'):
                stt, ld = True, False
            if m in ('call', 'jmp', 'push'):
                ld, stt = True, False
            if m == 'pop':
                ld, stt = False, True
            mm = p.mem; bs = i.reg_name(mm.base) if mm.base else None; sg = i.reg_name(mm.segment) if mm.segment else None
            ix = i.reg_name(mm.index) if mm.index else None
            dv = bool(taint) and (taint.get(bs) == 'A' or taint.get(ix) == 'A')
            for ls, on in ((0, ld), (1, stt)):
                if not on:
                    continue
                w = p.size
                if sg == 'fs':
                    nf.append((x, ls, w, 'tls:%x' % (mm.disp & M32) if bs is None and ix is None else 'tls:reg'))
                elif dv:
                    nf.append((x, ls, w, 'tls-derived'))
                elif bs in ('esp', 'ebp'):
                    v = s if bs == 'esp' else b
                    if ix is not None:
                        cidx = True
                        fr.append((x, ls, w, 'u', 0))
                    elif fun:
                        fr.append((x, ls, w, 'p' if bs == 'ebp' and v is not None else 'u', (v + mm.disp) if bs == 'ebp' and v is not None else 0))
                    elif v is None or F.multi:
                        fr.append((x, ls, w, 'u', 0))
                    else:
                        c = v + mm.disp
                        fr.append((x, ls, w, 'a' if c >= 4 else 'r' if c >= 0 else 'l', c))
                        if c >= 4 and ls == 0 and not fun and w == 4 and o[0].type == X.X86_OP_REG and m in ('mov', 'push') and p is o[-1]:
                            srcs.append((x, base(i.reg_name(o[0].reg)), 's%d' % ((c - 4) // 4)))
                        elif c >= 4 and ls == 0 and m == 'push':
                            srcs.append((x, None, 's%d' % ((c - 4) // 4)))
                elif bs is None and ix is None:
                    nf.append((x, ls, w, absclass(mm.disp & M32, bool(ls))))
                elif bs is None:
                    nf.append((x, ls, w, absclass(mm.disp & M32, bool(ls))))
                else:
                    nf.append((x, ls, w, 'heap/other'))
            if bs in ('esp', 'ebp') and not ok and not fun:
                allstack = allstack or ix is not None
                if ix is not None:
                    aw.add('indexed')
        if kind not in FUNK and (any(p.type == X.X86_OP_MEM and p.mem.base and i.reg_name(p.mem.base) in ('esp', 'ebp') and not p.mem.index and p.access & 3 and s is None and F.bp.get(x) is None for p in o)):
            allstack = True; aw.add('unresolved-esp')
    fe = frame_events(F)
    allstack = allstack or (not fun and any(c is not None and c >= 4 for x, c, idx, dst, sl in fe))
    if not fun and any(c is not None and c >= 4 for x, c, idx, dst, sl in fe):
        aw.add('argarea-address')
    evflows = lambda sp_: [(c, idx, sorted(flow(F, x, [dst], saved, False, sp_)) if dst else sorted(flow(F, x, [], saved, False, True, [sl])) if sl is not None and sp_ else [('U', 'spill' if sl is not None else 'push/store-esp')]) for x, c, idx, dst, sl in fe]
    events = evflows(False)
    base_ok = not fun and not F.multi and not cidx and not any(f[3] == 'u' for f in fr)
    if base_ok and any(sk for c, idx, sk in events) and all(all(t == ('U', 'spill') for t in sk) for c, idx, sk in events):
        events = evflows(True)               # closed under the assumption that frame slots are private; verified by the re-run
    summ = {}
    spill = base_ok and not any(e[2] for e in events)
    if not fun:
        for r in livein(F, saved):
            summ[r] = sorted(t for t in flow(F, F.a, [r], saved, True, spill) if t != ('R',))
        for x, d, par in srcs:
            if d is None:
                pa = pusharg(F, x)
                sk = [('U', 'pusharg')] if pa is None else [sink(F, pa[0], 's%d' % pa[1])]
            else:
                sk = sorted(t for t in flow(F, x, [d], saved, False, spill) if t != ('R',))
            summ[par] = sorted(set(summ.get(par, [])) | set(sk))
        for t in sorted({t for x in F.tails for t in F.tails[x] if mn(F.ins[x]) == 'jmp'}):
            summ.setdefault('fwd', []).append(t)
    bpv = sorted({v for v in F.bp.values() if v is not None})
    return a, dict(addrs=array('I', sorted(F.ins)).tobytes(), nf=nf, fr=fr, misc=misc, ev=events, summ=summ, allstack=allstack, aw=sorted(aw), cidx=cidx, multi=F.multi,
                   why=F.why, bpv=bpv[0] if len(bpv) == 1 else None, offs=offs[:5], undecoded=not F.ok)


def hx(v):
    return int(v, 16) if isinstance(v, str) else v


def parents_map(rows):
    par = {}
    for a, e, k in rows:
        for t in W.FLAGS.get(a, '').split(','):
            if t.startswith('parent='):
                par.setdefault(a, []).append(int(t[7:], 16))
    d = json.load(open(W.EHJ))
    for fi in d['funcinfo']:
        for st, u in fi['unwind']:
            par.setdefault(hx(u), []).extend(hx(o) for o in fi['owners'])
    for sc in d['scopes']:
        for rec in sc['records']:
            for v in rec[1:]:
                par.setdefault(hx(v), []).extend(hx(o) for o in sc['owners'])
    return {a: sorted(set(v)) for a, v in par.items()}


def main():
    raw, rows = load_universe(EXE)
    key = hashlib.sha256(open(EXE, 'rb').read()).hexdigest()[:16] + hashlib.md5(raw).hexdigest()[:16]
    print('memclass: exe sha256 %s  universe.tsv md5 %s  entries %d' % (key[:12], key[16:28], len(rows)))
    kind = {a: k for a, e, k in rows}
    with Pool(2) as p0:
        W.prepare(p0, rows, key)
    with Pool(2) as pool:
        out = pmap(pool, analyze, [(a, e, k) for a, e, k in rows], 100)
    R = {a: r for a, r in out if r}
    order = sorted(R, key=lambda a: (kind[a] != 'decomp', a))
    owner = {}
    for a in order:
        for x in array('I', R[a]['addrs']):
            owner.setdefault(x, a)
    par = parents_map(rows)
    # ---- parameter summaries: greatest fixed point (start non-escaping, remove on evidence)
    bad, rev, rf = {}, defaultdict(list), defaultdict(list)
    CAUSE = {'S': 'stored', 'R': 'returned', 'I': 'indirect', 'M': 'import', 'U': 'unknown'}
    for f in sorted(R):
        for p, sk in sorted(R[f]['summ'].items()):
            if p == 'fwd':
                for t in sk:
                    rf[t].append(f)
                continue
            for s_ in sk:
                if s_[0] != 'A':
                    bad.setdefault((f, p), CAUSE[s_[0]] if s_[0] != 'U' else 'unk:' + s_[1].split(':')[0])
                elif s_[1] not in R:
                    bad.setdefault((f, p), 'callee-undecoded')
                elif R[s_[1]]['allstack'] and s_[2][0] == 's':
                    bad.setdefault((f, p), 'callee-allstack')
                else:
                    rev[(s_[1], s_[2])].append((f, p))
        if R[f]['allstack']:
            for p in sorted(R[f]['summ']):
                if p[0] == 's':
                    bad.setdefault((f, p), 'allstack')
    work, rounds = sorted(bad), 0
    while work:
        rounds += 1; nxt = []
        for g, q in work:
            for d in rev.get((g, q), []) + [(f, q) for f in rf.get(g, [])]:
                if d not in bad:
                    bad[d] = bad[(g, q)]; nxt.append(d)
        work = sorted(nxt)

    def isbad(t, p, seen=()):
        return t not in R or (t, p) in bad or (R[t]['allstack'] and p[0] == 's') or any(isbad(u, p, seen + (t,)) for u in R[t]['summ'].get('fwd', []) if u not in seen and u != t)
    npar = Counter(('esc' if (f, p) in bad else 'ok') for f in sorted(R) for p in sorted(R[f]['summ']) if p != 'fwd')
    pcause = Counter(bad[k] for k in sorted(bad) if k[1] in R[k[0]]['summ'])
    # ---- frame contamination per owning function
    def verdict(sk):
        r = [s for s in sk if s[0] in 'SRIU'] + [s for s in sk if s[0] == 'M'] + [s for s in sk if s[0] == 'A' and isbad(s[1], s[2])]
        return sorted({'stored' if s[0] == 'S' else 'returned' if s[0] == 'R' else 'indirect' if s[0] == 'I' else 'unknown:' + s[1] if s[0] == 'U' else 'import:' + s[1] if s[0] == 'M' else 'callee' for s in r})
    fro = defaultdict(list)
    for f in sorted(R):
        for p in (par.get(f, []) if kind[f] in FUNK else [f]):
            if p in R and kind[p] not in FUNK:
                fro[p].append(f)
    frame = {}
    for g in sorted(R):
        evs, bnd, whole, esc = [], set(), False, Counter()
        for f in fro.get(g, []):
            r = R[f]; fun = f != g
            bp = R[g]['bpv']
            whole = whole or (r['multi'] and not fun) or r['cidx']
            for c, idx, sk in r['ev']:
                if fun:
                    c = None if c is None or bp is None else bp + c
                evs.append((c, idx, verdict(sk), bool(sk)))
            bnd.update(c if not fun else (bp + c if bp is not None else 0) for x, ls, w, role, c in r['fr'] if role in 'alorp')
        frame[g] = (sorted(bnd), evs, whole)
    res_t, per, lost = Counter(), {}, Counter()
    nescf, reasons, rule2, rulef, ureasons = 0, Counter(), Counter(), Counter(), Counter()
    for g in sorted(R):
        bnd, evs, whole = frame[g]
        anyv = [e for e in evs if e[2]]
        if anyv:
            nescf += 1
            reasons[(sorted(r for e in anyv for r in e[2])[0]).split(':')[0]] += 1
            for r in sorted({r for e in anyv for r in e[2] if r.startswith('unknown')}):
                ureasons[r[8:].split(':')[0]] += 1

    def contam(g, c, final):
        bnd, evs, whole = frame[g]
        hit = []
        for ec, idx, vd, anyk in evs:
            if not (vd if final else anyk):
                continue
            if ec is None or idx or whole:
                return vd or ['callee']
            j = bisect.bisect_right(bnd, ec)
            if ec <= c and (j >= len(bnd) or bnd[j] > c):
                hit += vd or ['callee']
        return sorted(set(hit))

    def anyesc(g, final):
        return any(e[2] if final else e[3] for e in frame[g][1])
    tot, ent, imp, fl = Counter(), defaultdict(Counter), Counter(), Counter()
    pending = []
    for a in order:
        r = R[a]; mine = lambda x: owner[x] == a
        for x, ls, w, cls in r['nf']:
            if mine(x):
                k = 'tls' if cls.startswith('tls:') else cls
                if cls.startswith('tls:'):
                    fl[cls] += 1
                tot[(k, 'S' if ls else 'L', w if w in WID else 'o')] += 1; ent[a][(k, ls)] += 1
        for x, kd in r['misc']:
            if mine(x):
                fl[kd] += 1
        for x, ls, w, role, c in r['fr']:
            if not mine(x):
                continue
            ps = [p for p in (par.get(a, []) if kind[a] in FUNK else [a]) if p in R and kind[p] not in FUNK]
            if not ps:
                cls = 'stack-unk'; res_t['funclet-parent-unknown'] += 1
            else:
                cl = {}
                for g in ps:
                    cc = c
                    if kind[a] in FUNK and role == 'p':
                        cc = None if R[g]['bpv'] is None else R[g]['bpv'] + c
                    if role in 'alorp' and cc is not None:
                        v = contam(g, cc, True); v1 = contam(g, cc, False)
                    else:
                        v = ['frame'] if anyesc(g, True) else []; v1 = ['frame'] if anyesc(g, False) else []
                    cl[g] = (v, v1)
                if any(v for v, v1 in cl.values()):
                    cls = 'stack-escaped'
                    for v, v1 in cl.values():
                        for t in v:
                            if t.startswith('import:'):
                                lost[t[7:]] += 1
                else:
                    cls = 'stack-funclet' if kind[a] in FUNK else 'stack-args-in' if role == 'a' else 'stack-args-out' if role == 'o' else 'stack-local'
                    if any(v1 for v, v1 in cl.values()):
                        rule2[role] += 1
                    elif role in 'up' and not kind[a] in FUNK:
                        rulef['unresolved-offset'] += 1
            tot[(cls, 'S' if ls else 'L', w if w in WID else 'o')] += 1; ent[a][(cls, ls)] += 1
    nacc = sum(tot.values())
    pc = {c: sum(v for k, v in tot.items() if k[0] == c) for c in ORDER}
    print('entries decoded %d (undecodable %d); instructions %d (each counted once); memory accesses %d (loads %d, stores %d)' % (
        len(R), len(rows) - len(R), len(owner), nacc, sum(v for k, v in tot.items() if k[1] == 'L'), sum(v for k, v in tot.items() if k[1] == 'S')))
    print('%-15s %9s %9s %6s %6s   widths 1/2/4/8/10/16/o (loads | stores)' % ('class', 'loads', 'stores', 'share', 'v1'))
    for c in ORDER:
        L, S = [sum(v for k, v in tot.items() if k[0] == c and k[1] == t) for t in 'LS']
        print('%-15s %9d %9d %5.1f%% %6s   %s | %s' % (c, L, S, 100.0 * (L + S) / max(nacc, 1), BEFORE.get(c, '-'), '/'.join(str(tot[(c, 'L', w)]) for w in WID + ('o',)), '/'.join(str(tot[(c, 'S', w)]) for w in WID + ('o',))))
    offs = [(x, w) for a in order for x, w in R[a]['offs'] if owner.get(x) == a]
    tlsp = pc['tls'] + pc['tls-derived']
    plain = sum(pc[c] for c in PLAIN)
    print('plain-eligible accesses %d of %d (%.1f %%)  [v1: 46.1 %%]; tls (%d) and tls-derived (%d) stay ordered: check %s (%d offending sites)' % (
        plain, nacc, 100.0 * plain / max(nacc, 1), pc['tls'], pc['tls-derived'], 'PASSED' if not offs else 'FAILED', len(offs)))
    print('contribution: rule1 locals %d, args-in %d, args-out %d, funclet frames %d; formerly stack-unk now plain (non-funclet unresolved offsets) %d; rule2 (summary-proved, formerly stack-escaped) %d (by role %s)' % (
        pc['stack-local'], pc['stack-args-in'], pc['stack-args-out'], pc['stack-funclet'], rulef['unresolved-offset'], sum(rule2.values()), dict(sorted(rule2.items()))))
    rs = Counter(w for a in order if kind[a] not in FUNK for w in R[a]['why'][:1])
    print('esp-tracking residue: entries with an unresolved instruction %d (non-funclet %d) by first reason: %s; funclets without a known parent frame: %d accesses' % (
        sum(1 for a in order if R[a]['why']), sum(1 for a in order if R[a]['why'] and kind[a] not in FUNK), dict(sorted(rs.items())), res_t['funclet-parent-unknown']))
    print('frames with an escaping address: %d of %d; first reason: %s; frames flagged whole-frame (computed offsets / epochs): %d; entries taking their arg-area address: %d' % (
        nescf, len(frame), dict(sorted(reasons.items())), sum(1 for g in frame if frame[g][2]), sum(1 for a in R if R[a]['allstack'])))
    print('  arg-area flag reasons (entries): ' + ' '.join('%s=%d' % kv for kv in sorted(Counter(w for a in R for w in R[a]['aw']).items())))
    print('  unknown-sink reasons (frames): ' + ' '.join('%s=%d' % kv for kv in sorted(ureasons.items(), key=lambda kv: (-kv[1], kv[0]))[:10]))
    print('parameter summaries: %d non-escaping, %d escaping (root causes: %s); fixed-point rounds %d' % (npar['ok'], npar['esc'], ' '.join('%s=%d' % kv for kv in sorted(pcause.items())), rounds))
    print('top imports by accesses lost: ' + ' '.join('%s=%d' % kv for kv in sorted(lost.items(), key=lambda kv: (-kv[1], kv[0]))[:15]))
    print('atomics: lock %d xchg-mem %d cmpxchg %d xadd %d; tls displacements: %s' % (fl['lock'], fl['xchg'], fl['cmpxchg'], fl['xadd'], ' '.join('%s=%d' % kv for kv in sorted(fl.items()) if kv[0].startswith('tls:'))[:150]))
    ck = lambda n: n.startswith('Interlocked') or n.endswith('CriticalSection') or n in ('TlsGetValue', 'TlsSetValue', 'TlsAlloc', 'CreateThread')
    print('import call sites: ' + ' '.join('%s=%d' % (k[4:], v) for k, v in sorted(fl.items()) if k.startswith('imp:') and ck(k[4:])))
    os.makedirs(OUT, exist_ok=True)
    with open(OUT + '/entries.tsv', 'w') as f:
        f.write('entry\tkind\tevents_escaping\twhole\tawhy\t' + '\t'.join(c.replace('/', '_') + '.L\t' + c.replace('/', '_') + '.S' for c in ORDER) + '\n')
        for a in sorted(R):
            f.write('%08x\t%s\t%d\t%d\t%s\t' % (a, kind[a], sum(1 for e in frame[a][1] if e[2]) if a in frame else 0, int(frame[a][2]) if a in frame else 0, ','.join(R[a]['why']) or '-')
                    + '\t'.join('%d\t%d' % (ent[a][(c, 0)], ent[a][(c, 1)]) for c in ORDER) + '\n')


if __name__ == '__main__':
    main()
