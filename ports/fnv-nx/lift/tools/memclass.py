#!/usr/bin/env python3
"""W10-B: class of every memory operand of FalloutNV.exe (rule R9: plain load/store only if provably thread-local).
Classes: stack-local, stack-args-in, stack-args-out, stack-escaped, stack-unk, ro-iat, ro-rdata, ro-text, tls, tls-derived,
global, heap/other. Count-only stdout; per-entry TSV under /home/user/l3facts/memclass/. Reuses the decoder of fpmodes.py."""
import os, sys, struct, hashlib, time, bisect
from collections import Counter, defaultdict
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fpmodes as P
from fpmodes import X, M32, mn, IMG, decode, load_universe, KINDS

OUT = '/home/user/l3facts/memclass'
RETN, THUNK = {}, {}                                   # entry -> callee-popped bytes (None = ambiguous); entry -> IAT slot of a jmp thunk
STK = ('stack-local', 'stack-args-in', 'stack-args-out', 'stack-escaped', 'stack-unk')
ORDER = STK + ('ro-iat', 'ro-rdata', 'ro-text', 'tls', 'tls-derived', 'global', 'heap/other')
WID = (1, 2, 4, 8, 10, 16)
SECS, IAT = [], {}


def parse_pe():
    d = IMG.d; pe = struct.unpack_from('<I', d, 0x3c)[0]; n = struct.unpack_from('<H', d, pe + 6)[0]; o = pe + 24 + struct.unpack_from('<H', d, pe + 20)[0]
    for k in range(n):
        vs, va, rs, ro, _, _, _, _, ch = struct.unpack_from('<IIIIIIHHI', d, o + 8 + 40 * k)
        SECS.append((IMG.base + va, IMG.base + va + max(vs, rs), bool(ch & 0x80000000), bool(ch & 0x20000000)))
    SECS.sort()
    rva, _ = struct.unpack_from('<II', d, pe + 24 + 96 + 8)
    while rva:
        oft, _, _, nm, ft = struct.unpack_from('<IIIII', IMG.bytes(IMG.base + rva, 20), 0)
        if not nm:
            break
        for k in range(100000):
            v = IMG.u32(IMG.base + (oft or ft) + 4 * k)
            if not v:
                break
            nb = IMG.bytes(IMG.base + v + 2, 64).split(b'\0')[0].decode('latin1') if not v & 0x80000000 else '#%d' % (v & 0xffff)
            IAT[IMG.base + ft + 4 * k] = nb
        rva += 20


def absclass(a, store):
    k = bisect.bisect_right(SECS, (a, M32, 0, 0)) - 1
    if k < 0 or a >= SECS[k][1]:
        return 'heap/other'
    if a in IAT and not store:
        return 'ro-iat'
    if not SECS[k][2] and not store:
        return 'ro-text' if SECS[k][3] else 'ro-rdata'
    return 'global'


def rn(i, op):
    return i.reg_name(op.reg) if op.type == X.X86_OP_REG else None


def phase0(args):
    a, ext, kind = args
    F = P.Fn(a, ext, kind)
    if not F.ok:
        return a, None, None
    r = {(o[0].imm if o else 0) for i in F.ins.values() if mn(i) == 'ret' for o in [i.operands]}
    f = F.ins.get(a); th = None
    if f is not None and mn(f) == 'jmp' and f.operands[0].type == X.X86_OP_MEM and not f.operands[0].mem.base and not f.operands[0].mem.index:
        th = f.operands[0].mem.disp & M32
    return a, (r.pop() if len(r) == 1 else (0 if not r else None)), th


def spscan(F):
    """(sp, bp) before each insn (esp offset rel. entry esp; ebp offset or None), bad flag. Callee pops: direct = callee's ret N; indirect = pushed
    bytes since the last call unless an 'add esp' follows. Verified by: every ret at sp 0 and joins agree."""
    st = {F.a: (0, None, 0)}; work = [F.a]; bad = False
    while work:
        a = work.pop(); s, b, pb = st[a]; i = F.ins[a]; m = mn(i); o = i.operands
        r0 = rn(i, o[0]) if o else None; r1 = rn(i, o[1]) if len(o) > 1 else None
        if m in ('push', 'pushfd'):
            s -= 4; pb += 4
        elif m in ('pop', 'popfd'):
            s += 4; pb = max(0, pb - 4); b = None if r0 == 'ebp' else b
        elif m in ('pushal', 'popal', 'enter'):
            bad = True
        elif m == 'leave':
            bad = bad or b is None; s = (b or 0) + 4; b = None
        elif m == 'call':
            if o[0].type == X.X86_OP_IMM:
                pop = RETN.get(o[0].imm & M32, 0)
                bad = bad or pop is None
                s += pop or 0
            else:
                n = F.ins.get(a + i.size)
                s += 0 if n is not None and mn(n) == 'add' and rn(n, n.operands[0]) == 'esp' else pb
            pb = 0
        elif m == 'mov' and r0 == 'esp':
            bad = bad or r1 != 'ebp' or b is None; s = b or 0; pb = 0
        elif m == 'mov' and r0 == 'ebp':
            b = s if r1 == 'esp' else None
        elif m in ('add', 'sub') and r0 == 'esp':
            if o[1].type != X.X86_OP_IMM:
                bad = True
            else:
                s += o[1].imm if m == 'add' else -o[1].imm
            pb = 0
        elif m in ('ret', 'retf'):
            bad = bad or s != 0
        elif r0 == 'esp' and m not in ('cmp', 'test'):
            bad = True
        elif r0 == 'ebp' and m not in ('cmp', 'test', 'push'):
            b = None
        for t in F.succ[a]:
            if t not in st:
                st[t] = (s, b, pb); work.append(t)
            elif st[t][0] != s:
                bad = True
            elif st[t][1] != b:
                st[t] = (s, None, pb); work.append(t)
    return {a: v[0] for a, v in st.items()}, {a: v[1] for a, v in st.items()}, bad


def reg_escapes(F, x, r):
    """can the frame address loaded into register r at x leave the frame (stored to non-fs memory, pushed, left in eax/ecx/edx at a call,
    returned, copied, or still unresolved after 300 steps)? Use as a memory base/index only is not an escape."""
    seen, work = set(), list(F.succ[x])
    while work:
        a = work.pop()
        if a in seen:
            continue
        seen.add(a)
        if len(seen) > 300:
            return True
        i = F.ins[a]; m = mn(i); o = i.operands; fs = any(p.type == X.X86_OP_MEM and p.mem.segment and i.reg_name(p.mem.segment) == 'fs' for p in o)
        if m in ('call', 'ret', 'retf') or a in F.tails:
            if r in ('eax', 'ecx', 'edx') or (m != 'call' and r == 'eax'):
                return True
            if m != 'call':
                continue
        kill = False
        for k, p in enumerate(o):
            if p.type == X.X86_OP_REG and P.core.SUB.get(i.reg_name(p.reg), (0,))[0] == r:
                if p.access & 1 and not (m in ('cmp', 'test') or fs and m == 'mov' and k == 1 and False) and not (fs and k == 1 and m == 'mov'):
                    if not (m in ('xor', 'sub') and len(o) == 2 and o[0].reg == o[1].reg):
                        return True
                if p.access & 2 and not p.access & 1 and k == 0:
                    kill = True
        if not kill:
            work += F.succ[a]
    return False


def analyze(args):
    a, ext, kind = args
    F = P.Fn(a, ext, kind)
    if not F.ok:
        return a, None
    sp, bp, bad = spscan(F)
    ins = F.ins; C = Counter(); fl = Counter(); imp = Counter(); tlsd = Counter(); off = []
    acc = []                                               # (addr, store, width, kind, payload)
    tk, bnd, cidx, anyesc = [], [], False, False
    targets = {t for ts in F.succ.values() for t in ts}
    for x in sorted(ins):
        i = ins[x]; m = mn(i); o = i.operands; s = sp.get(x); b = bp.get(x)
        r0 = rn(i, o[0]) if o else None; r1 = rn(i, o[1]) if len(o) > 1 else None
        if m in ('push', 'pushfd'):
            acc.append((x, 1, 4, 'push', s - 4 if s is not None and not bad else None))
        elif m in ('pop', 'popfd'):
            acc.append((x, 0, 4, 'stk', s if s is not None and not bad else None))
        elif m == 'call':
            acc.append((x, 1, 4, 'ret', None))
        elif m in ('ret', 'retf'):
            acc.append((x, 0, 4, 'ret', None))
        elif m == 'leave':
            acc.append((x, 0, 4, 'ret', None))
        tx = None
        if m == 'lea' and o[1].mem.base and i.reg_name(o[1].mem.base) in ('esp', 'ebp'):
            tx = (i.reg_name(o[1].mem.base), o[1].mem.disp, bool(o[1].mem.index), r0)
        elif m == 'mov' and r1 in ('esp', 'ebp') and r0 not in ('esp', 'ebp') and o[0].type == X.X86_OP_REG:
            tx = (r1, 0, False, r0)
        elif m == 'push' and r0 in ('esp', 'ebp') and (r0 == 'esp' or bp.get(x) is not None):
            tx = (r0, 0, False, None)
        elif m == 'mov' and r1 in ('esp', 'ebp') and o[0].type == X.X86_OP_MEM:
            fsm = o[0].mem.segment and i.reg_name(o[0].mem.segment) == 'fs'
            tx = None if fsm else (r1, 0, False, None)
        if tx:
            base, d, idx, dst = tx
            v = (s if base == 'esp' else b)
            c = None if v is None or bad and base == 'esp' else v + d
            esc = True if dst is None else reg_escapes(F, x, dst)
            if esc:
                anyesc = True; tk.append((c, idx))
        if m.startswith('lock') or i.prefix[0] == 0xf0:
            fl['lock'] += 1
        if m in ('xchg', 'cmpxchg', 'xadd') and any(p.type == X.X86_OP_MEM for p in o):
            fl[m] += 1
        for p in o:
            if p.type != X.X86_OP_MEM or m in ('lea', 'nop') or m.startswith('prefetch'):
                continue
            ld, stt = bool(p.access & 1), bool(p.access & 2)
            if m[0] == 'f' and m not in ('fxsave', 'fxrstor'):
                stt = m.startswith(('fst', 'fist', 'fnst', 'fbst', 'fnsave', 'fsave')); ld = not stt
            if m in ('fxsave', 'stmxcsr'):
                stt = True; ld = False
            if m in ('call', 'jmp') or m == 'push':
                ld = True; stt = False
            if m == 'pop':
                ld = False; stt = True
            mm = p.mem; bs = i.reg_name(mm.base) if mm.base else None; sg = i.reg_name(mm.segment) if mm.segment else None
            for st_ in ((0, ld), (1, stt)):
                if st_[1]:
                    acc.append((x, st_[0], p.size, 'op', (bs, mm.disp, i.reg_name(mm.index) if mm.index else None, sg, m)))
            if bs in ('esp', 'ebp') and mm.index:
                cidx = True
            if bs in ('esp', 'ebp') and not mm.index and (stt or ld):
                v = s if bs == 'esp' else b
                if v is not None and not (bad and bs == 'esp'):
                    bnd.append(v + mm.disp)
            if m == 'call' and p.mem.base is None and not mm.index and (mm.disp & M32) in IAT:
                imp[IAT[mm.disp & M32]] += 1
        if m == 'call' and o[0].type == X.X86_OP_IMM:
            t = o[0].imm & M32
            if t in THUNK and THUNK[t] in IAT:
                imp[IAT[THUNK[t]]] += 1
        if m == 'push' or m == 'pop':
            if s is not None and not bad:
                bnd.append(s - 4 if m == 'push' else s)
    bnd = sorted(set(bnd)); T = sorted({c for c, _ in tk if c is not None}); tall = any(c is None or ix for c, ix in tk)
    fpass = not anyesc
    order = sorted(ins)
    taint = {}                                              # reg -> 'A' (TLS array ptr) / 'S' (slot value) within a basic block
    off = []
    for x in order:
        i = ins[x]; m = mn(i); o = i.operands
        if len(F.preds.get(x, ())) != 1 or x in targets and False:
            taint = {}
        r0 = rn(i, o[0]) if o else None
    # classification
    sprev = None
    for x, stt, w, k, pay in acc:
        wk = w if w in WID else 'o'
        ls = 'S' if stt else 'L'
        if k == 'ret':
            cls = 'stack-local'
        elif k in ('push', 'stk'):
            c = pay
            cls = 'stack-unk' if c is None else fcls(c, bnd, T, tall, cidx, fpass)
            if k == 'push' and c is not None and cls == 'stack-local' and is_arg_push(F, x):
                cls = 'stack-args-out'
        else:
            bs, disp, ix, sg, m = pay; s = sp.get(x); b = bp.get(x)
            if sg == 'fs':
                cls = 'tls'; tlsd['%x' % (disp & M32) if bs is None and ix is None else 'reg'] += 1
            elif bs in ('esp', 'ebp'):
                v = s if bs == 'esp' else b
                if ix is not None:
                    cls = 'stack-local' if fpass else 'stack-escaped'
                elif v is None or (bs == 'esp' and bad):
                    cls = 'stack-unk'
                else:
                    cls = fcls(v + disp, bnd, T, tall, cidx, fpass)
            elif bs is None and ix is None:
                cls = absclass(disp & M32, stt)
            elif bs is None:
                cls = absclass(disp & M32, stt)
            else:
                cls = 'heap/other'
        C[(cls, ls, wk)] += 1
    return a, dict(n=len(ins), C=C, fl=fl, imp=imp, tlsd=tlsd, fpass=fpass, bad=bad, addrs=sorted(ins), acc=len(acc))


def fcls(c, bnd, T, tall, cidx, fpass):
    if c >= 4:
        return 'stack-args-in'
    if c >= 0:
        return 'stack-local'
    if cidx or tall:
        return 'stack-local' if fpass else 'stack-escaped'
    k = bisect.bisect_right(T, c) - 1
    if k >= 0:
        t = T[k]; j = bisect.bisect_right(bnd, t)
        if j >= len(bnd) or bnd[j] > c:
            return 'stack-escaped'
    return 'stack-local'


def is_arg_push(F, x):
    """a push followed (straight line, no pop/add esp/ret/jump) by a call is an outgoing argument; prologue register saves are not."""
    k = x; first = True
    for _ in range(40):
        i = F.ins[k]; m = mn(i)
        if m == 'call':
            return True
        if m in ('pop', 'ret', 'retf', 'jmp') or m[0] == 'j' or (m == 'add' and rn(i, i.operands[0]) == 'esp') or len(F.succ[k]) != 1:
            return False
        k = F.succ[k][0]
    return False


def main():
    t0 = time.time()
    parse_pe()
    raw, rows = load_universe()
    print('memclass: exe sha256 %s  universe.tsv md5 %s  entries %d' % (hashlib.sha256(open(P.EXE, 'rb').read()).hexdigest()[:12], hashlib.md5(raw).hexdigest()[:12], len(rows)))
    kind = {a: k for a, e, k in rows}
    with Pool(3) as pool:
        for a, r, th in pool.imap(phase0, [(a, e, k) for a, e, k in rows], chunksize=300):
            if r is not None or th:
                RETN[a] = r
            if th:
                THUNK[a] = th
    res = {}
    with Pool(3) as pool:
        for a, r in pool.imap(analyze, [(a, e, k) for a, e, k in rows], chunksize=100):
            res[a] = r
    os.makedirs(OUT, exist_ok=True)
    good = {a: r for a, r in res.items() if r}
    tot = Counter(); fl = Counter(); imp = Counter(); nins = sum(r['n'] for r in good.values())
    allad = Counter()
    for a, r in good.items():
        tot.update(r['C']); fl.update(r['fl']); imp.update(r['imp'])
    uniq = len({x for r in good.values() for x in r['addrs']})
    nacc = sum(tot.values())
    print('entries decoded %d (undecodable %d); instructions %d (unique addresses %d); memory accesses %d (loads %d, stores %d)' % (
        len(good), len(res) - len(good), nins, uniq, nacc, sum(v for k, v in tot.items() if k[1] == 'L'), sum(v for k, v in tot.items() if k[1] == 'S')))
    print('%-15s %9s %9s %6s   widths 1/2/4/8/10/16/other (loads | stores)' % ('class', 'loads', 'stores', 'share'))
    pc = {}
    for c in ORDER:
        L = sum(v for k, v in tot.items() if k[0] == c and k[1] == 'L'); S = sum(v for k, v in tot.items() if k[0] == c and k[1] == 'S'); pc[c] = L + S
        wl = '/'.join(str(tot[(c, 'L', w)]) for w in WID + ('o',)); ws = '/'.join(str(tot[(c, 'S', w)]) for w in WID + ('o',))
        print('%-15s %9d %9d %5.1f%%   %s | %s' % (c, L, S, 100.0 * (L + S) / max(nacc, 1), wl, ws))
    stk = sum(pc[c] for c in STK)
    fp = [a for a, r in good.items() if r['fpass']]
    fpacc = sum(v for a in fp for k, v in good[a]['C'].items() if k[0] in STK)
    print('functions passing the function-level escape test (no frame address leaves): %d of %d; they contain %.1f%% of all stack accesses; esp tracking inconsistent (stack-unk) in %d entries' % (
        len(fp), len(good), 100.0 * fpacc / max(stk, 1), sum(1 for r in good.values() if r['bad'])))
    unkok = sum(v for a in fp for k, v in good[a]['C'].items() if k[0] == 'stack-unk')
    td = Counter()
    for r in good.values():
        td.update(r['tlsd'])
    print('tls displacements: ' + ' '.join('%s=%d' % kv for kv in sorted(td.items(), key=lambda kv: -kv[1])[:8]))
    print('atomics: lock-prefixed %d  xchg-mem %d  cmpxchg-mem %d  xadd-mem %d' % (fl['lock'], fl['xchg'], fl['cmpxchg'], fl['xadd']))
    ck = lambda n: n.startswith('Interlocked') or n.endswith('CriticalSection') or n.endswith('CriticalSectionAndSpinCount') or n in ('TlsGetValue', 'TlsSetValue', 'TlsAlloc', 'CreateThread')
    print('import call sites (direct + via thunk): ' + ' '.join('%s=%d' % kv for kv in sorted(imp.items()) if ck(kv[0])))
    plain = pc['stack-local'] + pc['ro-iat'] + pc['ro-rdata'] + pc['ro-text']
    print('plain-eligible accesses %d of %d (%.1f %%)  [stack-local %d + ro-const %d; tls (%d) and tls-derived (%d) excluded; +%d stack-unk in escape-free functions; +%d args-in/out]' % (
        plain, nacc, 100.0 * plain / max(nacc, 1), pc['stack-local'], plain - pc['stack-local'], pc['tls'], pc['tls-derived'], unkok, pc['stack-args-in'] + pc['stack-args-out']))
    print('files: %s  runtime %.0fs' % (OUT, time.time() - t0))
    with open(OUT + '/entries.tsv', 'w') as f:
        f.write('entry\tkind\tinsns\tfnpass\tspbad\t' + '\t'.join(c.replace('/', '_') + '.L\t' + c.replace('/', '_') + '.S' for c in ORDER) + '\n')
        for a in sorted(good):
            r = good[a]
            f.write('%08x\t%s\t%d\t%d\t%d\t' % (a, kind[a], r['n'], r['fpass'], r['bad']) + '\t'.join('%d\t%d' % (sum(v for k, v in r['C'].items() if k[0] == c and k[1] == 'L'), sum(v for k, v in r['C'].items() if k[0] == c and k[1] == 'S')) for c in ORDER) + '\n')


if __name__ == '__main__':
    main()
