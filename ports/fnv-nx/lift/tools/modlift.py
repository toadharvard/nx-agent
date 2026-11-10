#!/usr/bin/env python3
"""Module universe under the "mapped read-only image + dispatcher" model (used by `universe.py --module PATH`). No pointer is classified
as code or data: EVERY relocated dword whose value lies in an executable section is an entry, as are the PE entry point, code exports, TLS
callbacks, EH/SEH targets and the closure under direct call / tail-jump targets. Each entry has its own recursive descent; entries may
overlap. A path ends at ret / indirect jmp / another entry / `ud` (bytes that do not decode: the lifted body raises #UD) / the end of the
executable span. Provenance: `proven` = reachable by direct flow from entry point, exports, TLS, EH tables or from a pointer whose consumer
transfers control (vptr-store run, jump table); `candidate` = known only as a relocated pointer target. Gate: totality of targets.
Output: /home/user/l3facts/<module>/{universe.tsv,codemap.tsv,ehtables.json,gate_codemap.json}."""
import os, re, sys, json, bisect
from array import array
from itertools import groupby
from collections import Counter, deque
from types import SimpleNamespace
import capstone
from capstone import x86 as X
import pemod, ehtables
TERM = {'ret', 'retf', 'int3', 'hlt', 'ud2', 'iret', 'iretd'}
PRI = ['entry', 'tls', 'export', 'call', 'tail', 'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'seh-frame', 'eh-throw', 'ptr']
CN = {1: 'code-proven', 2: 'code-candidate', 3: 'pe-header-data', 4: 'padding', 5: 'non-code'}
M32 = 0xffffffff

def run(path):
    name = os.path.splitext(os.path.basename(path))[0].lower(); outd = '/home/user/l3facts/' + name; os.makedirs(outd, exist_ok=True)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    M = pemod.load(path); u32 = M.u32; RL = pemod.relocs(M); relocs = sorted(RL or []); rset = set(relocs); imports = pemod.imports(M)
    T0 = min(a for n, a, z, ro in M.xs); T1 = max(a + z for n, a, z, ro in M.xs); TN = T1 - T0
    V = bytearray(TN); tx = bytearray(b'\xcc' * TN)
    for n, a, z, ro in M.xs: V[a - T0:a - T0 + z] = b'\1' * z; tx[a - T0:a - T0 + z] = M.d[ro:ro + z]
    text = bytes(tx)
    def intext(a): return T0 <= a < T1 and V[a - T0] == 1
    rvals = [(r, v) for r, v in ((r, u32(r)) for r in relocs) if intext(v)]
    ent = {}; roots = set(); queue = deque(); dec = {}; cov = bytearray(TN); ud = set(); xf = set(); misfit = set(); callers = {}; calltg = {}; leaves = set(); jtab = {}; jins = []
    def add(a, kind, proven=False):
        if not intext(a): return False
        if a not in ent: ent[a] = set(); queue.append(a)
        ent[a].add(kind)
        if proven: roots.add(a)
        return True
    def conflict(a, ins):
        """a relocated dword must lie wholly inside an operand field of one instruction or wholly outside all instructions (flag only)."""
        i = bisect.bisect_left(relocs, a - 3); fs = (a + ins.disp_offset if ins.disp_size == 4 else -1, a + ins.imm_offset if ins.imm_size == 4 else -1)
        while i < len(relocs) and relocs[i] < a + ins.size:
            if relocs[i] not in fs: return True
            i += 1
        return False
    def descend(start):
        work = [start]
        while work:
            a = work.pop()
            while True:
                if a in dec or a in ud or a in xf: break
                if not (T0 <= a < T1) or not V[a - T0]: xf.add(a); break
                o = a - T0; stop = False
                for ins in md.disasm(text[o:o + 192], a):
                    a = ins.address
                    if a in dec: stop = True; break
                    if not V[a - T0]: xf.add(a); stop = True; break
                    mn = ins.mnemonic.split()[-1]; ops = ins.operands; tg = (); fall = True
                    if mn == 'jmp':
                        fall = False; op = ops[0]
                        if op.type == X.X86_OP_IMM: tg = (op.imm & M32,)
                        elif op.type == X.X86_OP_MEM and op.mem.scale == 4 and op.mem.index and (a + ins.disp_offset) in rset: jtab[a] = op.mem.disp & M32
                    elif mn in TERM: fall = False
                    elif mn[0] == 'j' or mn in ('loop', 'loope', 'loopne'): tg = (ops[0].imm & M32,)
                    elif mn == 'call' and ops[0].type == X.X86_OP_IMM:
                        t = ops[0].imm & M32; callers.setdefault(t, []).append(a); calltg[a] = t
                        if not add(t, 'call'): leaves.add((a, t))
                    dec[a] = (ins.size, mn, 0, fall, tg); o = a - T0; cov[o] = 1
                    for j in range(o + 1, o + ins.size):
                        if not cov[j]: cov[j] = 2
                    if tg: jins.append(a)
                    if conflict(a, ins): misfit.add(a)
                    for t in tg:
                        if intext(t): work.append(t)
                        else: leaves.add((a, t))
                    a += ins.size
                    if not fall: stop = True; break
                if stop: break
                if a >= T1 or not V[a - T0]: xf.add(a); break
                if next(md.disasm(text[a - T0:a - T0 + 15], a, 1), None) is None: ud.add(a); break
    es_c = [0, []]
    def es():
        if es_c[0] != len(ent): es_c[0] = len(ent); es_c[1] = sorted(ent)
        return es_c[1]
    def entry_of(a): L = es(); return L[bisect.bisect_right(L, a) - 1]
    def extent_insns(a):
        seen = []; ss = set(); st = [a]
        while st and len(seen) < 4000:
            x = st.pop()
            if x in ss or x not in dec: continue
            ss.add(x); seen.append(x); sz, mn, ft, fall, tg = dec[x]; st.extend(tg)
            if fall: st.append(x + sz)
        return seen
    X_ = SimpleNamespace(d=M.d, secs=M.secs, text=text, T0=T0, T1=T1, cov=cov, insns=dec, rset=rset, u32=u32, sec=M.sec, intext=intext, callers=callers, isdata=lambda a: M.sec(a) is not None,
                         indec=lambda a: None, emit=lambda t, k, s: add(t, k, True), relocs=relocs, extent_insns=extent_insns, rvals=rvals, fsize={}, mark=lambda *a: None, imports=imports,
                         outdir=outd, cstr=M.cstr, strict=True, entry_of=entry_of)
    eh = ehtables.EH(X_)
    if M.entry: add(M.base + M.entry, 'entry', True)
    XC, XFW, XN = pemod.exports(M)
    for a in XC: add(a, 'export', True)
    tls = M.dirent(9)
    if tls[0]:
        p = u32(M.base + tls[0] + 12)
        while intext(u32(p)): add(u32(p), 'tls', True); p += 4
    for r, v in rvals: add(v, 'ptr')
    tailsrc = {}
    def tail_pass():
        L = es(); n = 0
        for a in sorted(jins):
            i = bisect.bisect_right(L, a) - 1; o = L[i] if i >= 0 else T0; nx = L[i + 1] if i + 1 < len(L) else T1
            for t in dec[a][4]:
                if intext(t) and t not in ent and not (o <= t < nx): add(t, 'tail'); tailsrc[t] = a; n += 1
        return n
    while True:
        while queue: descend(queue.popleft())
        ch = tail_pass() + eh.scan()
        if not queue and not ch: break
    # ---- per-entry extents (a path ends at another entry, ret, indirect jmp, ud or the span end)
    E = es(); exts = {}; edges = {}; cnt = {}
    for e in E:
        ext = []; seen = set(); ed = set(); n_ud = n_xf = n_mf = 0; st = [e]
        while st:
            a = st.pop()
            while True:
                if a in seen: break
                if a != e and a in ent: ed.add(a); break
                if a in ud: n_ud += 1; break
                if a in xf: n_xf += 1; break
                di = dec.get(a)
                if di is None: break
                seen.add(a); ext.append(a); n_mf += a in misfit
                if a in calltg: ed.add(calltg[a])
                st.extend(t for t in di[4] if intext(t))
                if not di[3]: break
                a += di[0]
        exts[e] = ext; edges[e] = ed; cnt[e] = (n_ud, n_xf, n_mf)
    # ---- provenance: proven roots + control-transfer consumers (jump tables, vptr-store runs) found in proven code
    proven = set(); pset = set(); stored = {}
    for r in sorted(r for r in relocs if intext(r)):             # data addresses that decoded code stores into memory (`mov [m], imm32`)
        v = u32(r)
        if v not in rset: continue
        for k in range(1, 10):
            a = r - k
            if a in dec and a + dec[a][0] > r:
                i = next(md.disasm(text[a - T0:a - T0 + 15], a, 1))
                if i.mnemonic == 'mov' and i.operands[0].type == X.X86_OP_MEM and i.operands[1].type == X.X86_OP_IMM and a + i.imm_offset == r: stored.setdefault(v, []).append(a)
                break
    def runs(t, bound):
        s = []; q = t
        while q in rset and (q == t or not bound(q)): s.append(q); q += 4
        return s
    work = sorted(roots); donejt = set(); donevt = set()
    while True:
        while work:
            e = work.pop()
            if e in proven or e not in ent: continue
            proven.add(e); pset.update(exts[e]); work.extend(edges[e])
        new = []
        for a, T in sorted(jtab.items()):
            if a in pset and T not in donejt:
                donejt.add(T); new += [u32(q) for q in runs(T, lambda q: q in stored) if intext(u32(q))]
        for v, sites in sorted(stored.items()):
            if v not in donevt and any(a in pset for a in sites):
                donevt.add(v); new += [u32(q) for q in runs(v, lambda q: q in stored) if intext(u32(q))]
        new = [w for w in new if w in ent and w not in proven]
        if not new: break
        work = new
    # ---- coverage, classes, ownership
    covP = bytearray(TN); covC = bytearray(TN); own = array('I', bytes(4 * TN)); cand_insn = set()
    for e in E:
        pv = e in proven
        for a in exts[e]:
            sz = dec[a][0]; o = a - T0
            if pv: covP[o:o + sz] = b'\1' * sz
            else: covC[o:o + sz] = b'\1' * sz; cand_insn.add(a)
            if not own[o]: own[o:o + sz] = array('I', [e]) * sz
    cand_only = len(cand_insn - pset)
    pe = bytearray(TN)
    for va, ln, lab in pemod.pe_ranges(M):
        if max(va, T0) < min(va + ln, T1): pe[max(va, T0) - T0:min(va + ln, T1) - T0] = b'\1' * (min(va + ln, T1) - max(va, T0))
    ONES = int.from_bytes(b'\1' * TN, 'little'); P, C, PE, Vi = (int.from_bytes(bytes(x), 'little') for x in (covP, covC, pe, V))
    cl = bytearray((P | ((C & (P ^ ONES)) << 1) | ((PE & (P ^ ONES) & (C ^ ONES)) * 3)).to_bytes(TN, 'little'))
    cl = bytearray(((int.from_bytes(bytes(cl), 'little') & (Vi * 255)) | ((Vi ^ ONES) * 7)).to_bytes(TN, 'little'))
    def padlen(p, e):
        """length of the padding instruction at p inside [p, e), or 0."""
        if text[p] in (0xCC, 0x90): return 1
        g = next(md.disasm(text[p:p + 15], T0 + p, 1), None)
        if g is None or p + g.size > e: return 0
        o = g.operands
        if g.mnemonic == 'nop' or (g.mnemonic == 'lea' and len(o) == 2 and o[1].mem.index == 0 and o[1].mem.base == o[0].reg and o[1].mem.disp == 0) or (
                g.mnemonic == 'mov' and len(o) == 2 and o[0].type == o[1].type == X.X86_OP_REG and o[0].reg == o[1].reg): return g.size
        return 0
    for m in re.finditer(rb'\x00+', bytes(cl)):
        s, e = m.span(); p = s
        while s > 0 and cl[s - 1] == 1 and p < e and padlen(p, e): p += padlen(p, e)
        q = e
        if e < TN and cl[e] == 1:
            while q > p and text[q - 1] in (0xCC, 0x90): q -= 1
            for k in range(max(p, q - 15), q):
                x = k
                while x < q and padlen(x, q): x += padlen(x, q)
                if x == q: q = k; break
        cl[s:p] = b'\4' * (p - s); cl[q:e] = b'\4' * (e - q); cl[p:q] = b'\5' * (q - p)
    cc = Counter(cl); rows = []
    for c in (1, 2, 3, 4, 5):
        for m in re.finditer(bytes([c]) + b'+', bytes(cl)):
            s, e = m.span()
            if c in (1, 2):
                x = s
                for k, g in groupby(own[s:e]):
                    n = len(list(g)); rows.append((x, n, c, 'entry:%08x' % k)); x += n
            else: rows.append((s, e - s, c, '-'))
    rows.sort(); open(outd + '/codemap.tsv', 'w').write(''.join('%08x\t%d\t%s\t%s\n' % (T0 + s, n, CN[c], o) for s, n, c, o in rows))
    # ---- universe.tsv
    out = []; kc = Counter()
    for e in E:
        pk = next(k for k in PRI if k in ent[e]); n_ud, n_xf, n_mf = cnt[e]; kc[pk] += 1
        fl = ['proven' if e in proven else 'candidate'] + sorted(ent[e] - {pk}) + (['first-ud'] if e in ud else []) + ['%s=%d' % kv for kv in (('ud', n_ud), ('xf', n_xf), ('reloc-misfit', n_mf)) if kv[1]]
        out.append('%08x\t%s\t%d\t%s' % (e, pk, sum(dec[a][0] for a in exts[e]), ','.join(fl)))
    open(outd + '/universe.tsv', 'w').write('\n'.join(out) + '\n')
    # ---- totality gate
    targets = {v for r, v in rvals} | set(XC) | ({M.base + M.entry} if M.entry else set()) | {t for t in callers if intext(t)} | set(tailsrc) | {t for t, ks in ent.items() if ks & {'tls', 'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'seh-frame', 'eh-throw'}}
    cert = sum(1 for t in targets if t in ent and (t in dec or t in ud or t in xf))
    lv = [(a, t) for a, t in sorted(leaves)]; lvp = [x for x in lv if x[0] in pset]
    img = lambda t: M.sec(t) is not None
    why = []
    if RL is None: why.append('no .reloc: pointers into executable bytes cannot be enumerated')
    if cert != len(targets): why.append('%d targets without a completed descent' % (len(targets) - cert))
    if lvp: why.append('%d direct branches of proven code leave the executable spans' % len(lvp))
    if eh.unknown: why.append('%d frame-handler stubs with unknown FuncInfo magic' % eh.unknown)
    # ---- information: pointer targets by old classes, mnemonics
    pt = sorted({v for r, v in rvals}); dread = set()
    for r, v in rvals:
        for k in range(1, 9):
            a = r - k
            if a in dec and a + dec[a][0] > r:
                i = next(md.disasm(text[a - T0:a - T0 + 15], a, 1))
                if i.disp_size == 4 and a + i.disp_offset == r and i.mnemonic not in ('lea', 'call', 'jmp') and any(o.type == X.X86_OP_MEM for o in i.operands): dread.add(v)
                break
    na = sum(1 for v in pt if v in proven); nb = sum(1 for v in pt if v not in proven and (pe[v - T0] or v in dread)); nc = len(pt) - na - nb
    exe_mn = {}
    if os.path.exists('/home/user/l3facts/exe_mnemonics.tsv'):
        for ln in open('/home/user/l3facts/exe_mnemonics.tsv'): k, n = ln.split(); exe_mn[k] = int(n)
    mp = Counter(dec[a][1] for a in pset); new_mn = sorted(((k, n) for k, n in mp.items() if k not in exe_mn), key=lambda kv: (-kv[1], kv[0]))[:60]
    fi = eh.finish(ent)
    exs = [n for n, a, z, ro in M.xs]; green = not why
    print('%s: sections %d (executable %d: %s), reloc %s (%d fixups), imports %d, exports code %d forwarders %d, entry rva %x' % (os.path.basename(path), len(M.secs), len(exs), ' '.join(exs[:4]) + (' ...' if len(exs) > 4 else ''), 'yes' if RL is not None else 'NO', len(relocs), len(imports), len(XC), XFW, M.entry))
    print('\n'.join(fi[:3]))
    print('entries %d (proven %d, candidate %d): %s' % (len(E), len(proven), len(E) - len(proven), dict(sorted(kc.items()))))
    print('entries with ud %d (candidate %d), candidate entries whose first instruction is ud %d, with xf %d, with reloc-misfit %d' % (
        sum(1 for e in E if cnt[e][0]), sum(1 for e in E if cnt[e][0] and e not in proven), sum(1 for e in E if e in ud and e not in proven), sum(1 for e in E if cnt[e][1]), sum(1 for e in E if cnt[e][2])))
    print('instructions: proven %d, candidate-only %d; ud sites %d, xf sites %d, misfit %d; vtable runs proven %d, jump tables proven %d' % (len(pset), cand_only, len(ud), len(xf), len(misfit), len(donevt), len(donejt)))
    print('bytes of %d: %s' % (TN - cc[7], ', '.join('%s %d' % (CN[c], cc[c]) for c in sorted(CN))))
    print('pointer targets %d: (a) proven code %d, (b) data-read or PE-directory %d, (c) other %d; direct targets leaving spans: proven %d, candidate %d (in image %d)' % (len(pt), na, nb, nc, len(lvp), len(lv) - len(lvp), sum(1 for a, t in lv if img(t))))
    print('mnemonics in proven code not in the exe universe (%d%s): %s' % (len([k for k in mp if k not in exe_mn]), '' if exe_mn else ', exe list missing', ' '.join('%s=%d' % kv for kv in new_mn)))
    open(outd + '/gate_codemap.json', 'w').write(json.dumps({'schema': 'fnvnx-gate/1', 'name': 'l3.codemap.' + name, 'status': 'green' if green else 'red', 'universe': len(targets), 'certified': cert, 'uncertified': len(targets) - cert}) + '\n')
    print('gate l3.codemap.%s: %s (targets %d, certified %d)%s' % (name, 'green' if green else 'red', len(targets), cert, ''.join('; RED: ' + w for w in why)))
    return 0 if green else 1
