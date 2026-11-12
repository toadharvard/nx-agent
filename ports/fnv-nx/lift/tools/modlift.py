#!/usr/bin/env python3
"""Module universe under the "mapped read-only image + dispatcher" model (`universe.py --module NAME`, NAME or path of a file listed in modules.tsv).
The module is processed AT THE BASE THE LOADER ASSIGNED (L1's modules.tsv, assigned_base), as if the Windows loader had applied the module's own relocations:
every address (universe, codemap, ranges) is an absolute VA there, the one dispatcher of the process being keyed by image address. The rules are the exe's:
R3: EVERY relocated dword whose value lies in an executable section is an entry (no pointer is classified as code or data); the PE entry point, every code export of
the export directory, the TLS callbacks and the EH/SEH targets are entries; rule C: the entry set is closed under the direct call targets and the direct jump targets
that leave the lifter's range of each entry (the lifter's own fnvlift.core.explore over [entry, entry + extent), as universe.py does for the exe); a target outside every
executable section is no entry (the lifter sends it through the dispatcher). Each entry has its own recursive descent; entries may overlap. A path ends at ret /
indirect jmp / another entry / `ud` (bytes that do not decode: the lifted body raises #UD) / the end of the executable span. Provenance: `proven` = reachable by direct
flow from entry point, exports, TLS, EH tables or from a pointer whose consumer transfers control (vptr-store run, jump table); `candidate` = known only as a relocated
pointer target. Byte accounting: every byte of the executable sections is exactly one class (entry body, PE-directory data, padding, relocation slot, dead gap) or is
reported UNEXPLAINED by reason; the gate is red then. Output: $FNVNX_FACTS_OUT (default /home/user/l3facts2)/<module>/{universe,codemap,ranges,noreturn,outside,imports}.tsv, ehtables.json, gate_codemap.json."""
import os, re, sys, json, bisect, hashlib
from array import array
from itertools import groupby
from collections import Counter, deque
from types import SimpleNamespace
import capstone
from capstone import x86 as X
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pemod, ehtables, codemap, residue
from fnvlift import Image as LImage; from fnvlift import core as LCore
TERM = {'ret', 'retf', 'int3', 'hlt', 'ud2', 'iret', 'iretd'}
PRI = ['entry', 'tls', 'export', 'call', 'tail', 'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'seh-frame', 'eh-throw', 'ptr']
CN = {1: 'entry-nondecomp', 2: 'entry-nondecomp', 3: 'data-pe-header', 4: 'padding', 5: 'data-reloc', 6: 'dead-gap', 9: 'data-unreached', 8: 'UNEXPLAINED'}      # the exe's class names (codemap.py CLS)
M32 = 0xffffffff

def run(arg):
    path, mrow = pemod.locate(arg)      # modules.tsv row (missing = KeyError) and the file (sha256 + size checked = ValueError)
    name = os.path.splitext(mrow.name)[0]; outd = os.environ.get('FNVNX_FACTS_OUT', '/home/user/l3facts2') + '/' + name; os.makedirs(outd, exist_ok=True)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    M = pemod.load(path, mrow.base); u32 = M.u32; RL = pemod.relocs(M); relocs = sorted(RL or []); rset = set(relocs); imports = pemod.imports(M)
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
    XC, XFW, XN = pemod.exports(M); XD = [a for a in XC if not intext(a)]; XC = [a for a in XC if intext(a)]      # exported data is not code
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
    # ---- per-entry descents under NORETURN (a path ends at another entry, ret, indirect jmp, ud, the span end, or a call to a never-returning callee)
    cnt = {}; udinfo = {}; RET = ('ret', 'retf', 'iret', 'iretd'); nrcall, nrjmp, nrname = codemap.nr_sites(X_, imports)
    def trav(e, N):
        ext = []; seen = set(); deps = set(); ends = set(); n_ud = n_xf = n_mf = 0; uds = []; st = [(e, 0)]
        while st:
            a, prev = st.pop()
            while True:
                if a in seen: break
                if a != e and a in ent: deps.add(a); ends.add(a); break
                if a in ud or a in xf or a not in dec:
                    ends.add('ud'); n_ud += a in ud; n_xf += a in xf; uds.append((a, prev)); break
                seen.add(a); ext.append(a); di = dec[a]; n_mf += a in misfit; mn = di[1]
                if mn in RET: ends.add('ret'); break
                ct = calltg.get(a)
                if ct is not None: deps.add(ct)
                if a in nrcall or (ct is not None and ct in N): ends.add('callN'); break
                if mn == 'jmp' and not di[4]: ends.add('nrimp' if a in nrjmp else 'bad'); break
                st.extend((t, a) for t in di[4] if intext(t))
                if not di[3]:
                    if not di[4]: ends.add('fault')
                    break
                prev = a; a += di[0]
        cnt[e] = (n_ud, n_xf, n_mf); udinfo[e] = uds
        return ext, deps, ends
    # ---- rule C: the entry set is closed under the direct control transfers of EVERY entry (proven or candidate), computed with the lifter's own explore: the body of entry e is
    # the descent from e over the range [e, e + span(e)) (span = the universe.tsv extent column: up to the end of the last instruction e reaches), a direct call target is an entry (the lifter emits L_<target>() for every
    # direct call), a direct jmp/jcc/loop target outside the range is an entry; a target outside every executable section is no entry (the lifter sends it through the dispatcher).
    TRACE = os.environ.get('FNVNX_CLOSURE_TRACE')
    limg = LImage(path, M.base); lcache = {}; clos = {}; closure_new = Counter(); rounds = 0; n_before = 0; JMP = ('loop', 'loope', 'loopne')
    def lc_targets(e, ext):
        k = (e, ext)
        if k not in lcache:
            lo, hi = e, e + ext; fh = hi; ins, tabs, tk, bad = LCore.explore(limg, lo, hi, limg.bytes(lo, hi - lo + 16), e, (), fh); cs = {}; ts = {}; jx = set(); ft = set()
            for a_, g in sorted(ins.items()):
                m = g.mnemonic.split(' ')[-1]; o = g.operands
                if m == 'call' and o[0].type == X.X86_OP_IMM: cs.setdefault(o[0].imm & M32, a_)
                elif (m == 'jmp' or (m[0] == 'j' and m != 'jecxz') or m in JMP) and o[0].type == X.X86_OP_IMM:
                    t = o[0].imm & M32
                    if not lo <= t < hi: ts.setdefault(t, a_)
                elif m == 'jecxz' and not lo <= (o[0].imm & M32) < hi: jx.add(a_); ts.setdefault(o[0].imm & M32, a_)      # jecxz leaving the range: a direct jump out (f_sys, f_flow.jump_out)
                if m not in ('jmp', 'ret', 'retf', 'hlt', 'ud2') and not (m == 'int3' and a_ + g.size >= hi) and a_ + g.size not in ins and a_ + g.size not in bad:      # explore's own stop rule: the path falls out of what was decoded
                    ts.setdefault(a_ + g.size, a_); ft.add(a_ + g.size)         # a fall-through is a control transfer to its successor, like `jmp nxt` (f_flow.jump_out)
            lcache[k] = (cs, ts, jx, ft)
        return lcache[k]
    def span(e, xs):        # the lifter range of entry e is [e, e + span): the smallest interval from e that holds every instruction e reaches (a reached instruction below e is outside: a jump to it leaves the range)
        return max([a + dec[a][0] - e for a in xs if a >= e] or [0])
    while True:
        while True:
            while queue: descend(queue.popleft())
            ch = tail_pass() + eh.scan()
            if not queue and not ch: break
        E = es(); NR, res = codemap.noreturn_lfp(E, trav, {eh.thrower} & set(ent)); exts = {e: res[e][0] for e in E}; edges = {e: res[e][1] for e in E}
        rounds += 1; n_before = n_before or len(E); viol = {}; outside = {}; jxs = set(); ftall = set()
        for e in E:
            cs, ts, jx, ft = lc_targets(e, span(e, exts[e])); jxs |= {(e, x) for x in jx}; ftall |= ft
            for kd, T in (('call', cs), ('tail', ts)):
                for t, a_ in T.items():
                    if t in ent: continue
                    if intext(t):
                        viol.setdefault(t, kd)
                        if TRACE: print('closure-trace round %d: %08x %s from entry %08x ext %d at %08x' % (rounds, t, kd, e, span(e, exts[e]), a_), file=sys.stderr, flush=True)
                    else: outside[e, a_, t] = kd
        if not viol: break      # terminates: every round adds at least one entry and entries are addresses of the executable sections
        for t, k in sorted(viol.items()):
            closure_new['fall-through' if t in ftall else 'first-ud' if t in ud or t in xf else 'mid-instruction' if cov[t - T0] == 2 else 'other'] += 1; clos[t] = k; add(t, k)
    seedn = {eh.thrower} & set(ent)
    outside = sorted(outside)
    # ---- provenance: proven roots + control-transfer consumers (jump tables, vptr-store runs) found in proven code
    proven = set(); pset = set(); stored = {}; vptr = set()
    for r in sorted(r for r in relocs if intext(r)):             # data addresses that decoded code stores into memory (`mov [m], imm32`)
        v = u32(r)
        if v not in rset: continue
        for k in range(1, 10):
            a = r - k
            if a in dec and a + dec[a][0] > r:
                i = next(md.disasm(text[a - T0:a - T0 + 15], a, 1))
                if i.mnemonic == 'mov' and i.operands[0].type == X.X86_OP_MEM and i.operands[1].type == X.X86_OP_IMM and a + i.imm_offset == r:
                    stored.setdefault(v, []).append(a)
                    if i.operands[0].mem.disp == 0 and i.operands[0].mem.base and not i.operands[0].mem.index: vptr.add(v)      # `mov [reg], imm32`: an object's first dword
                break
    def runs(t, bound):
        """slots of a vptr-store / jump-table run: relocated dwords up to the next stored address, cut at the first slot that cannot be a code pointer
        (value outside the executable spans, or its descent hits ud / a relocation misfit)."""
        s = []; q = t
        while q in rset and (q == t or not bound(q)):
            w = u32(q)
            if not (intext(w) and w in ent and not cnt[w][0] and not cnt[w][2]): break
            s.append(q); q += 4
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
            if v not in donevt and v in vptr and any(a in pset for a in sites):
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
    cand_only = len(cand_insn - pset); mcand = Counter(dec[a][1] for a in cand_insn - pset)
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
    # ---- universe.tsv
    out = []; kc = Counter()
    for e in E:
        pk = next(k for k in PRI if k in ent[e]); n_ud, n_xf, n_mf = cnt[e]; kc[pk] += 1
        fl = ['proven' if e in proven else 'candidate'] + sorted(ent[e] - {pk}) + (['first-ud'] if e in ud else []) + ['%s=%d' % kv for kv in (('ud', n_ud), ('xf', n_xf), ('reloc-misfit', n_mf)) if kv[1]]
        out.append('%08x\t%s\t%d\t%s' % (e, pk, span(e, exts[e]), ','.join(fl)))
    open(outd + '/universe.tsv', 'w').write('# base=0x%08x: addresses are absolute VAs at the assigned load base (not RVAs; preferred base 0x%08x); columns: entry, kind, extent, flags; extent is the SPAN [entry, entry + extent) that holds every instruction the entry reaches at or above it (the range the lifter takes; the bytes reached: ranges.tsv)\n' % (M.base, M.pref) + '\n'.join(out) + '\n')
    hd = '# base=0x%08x: absolute VAs at the assigned load base (not RVAs)\n' % M.base; bad = 0; rg = []
    for e, row in zip(E, out):
        r = codemap.ranges(exts[e], lambda a: dec[a][0]); bad += max([b - e for a, b in r if b > e] or [0]) != int(row.split('\t')[2]); rg.append('%08x\t%s' % (e, codemap.rng_str(r)))
    open(outd + '/ranges.tsv', 'w').write(hd.rstrip() + '; columns: entry, half-open ranges start-end (hex) of the instruction bytes reached\n' + '\n'.join(rg) + '\n')
    nrr = codemap.nr_rows(NR, res, nrname, seedn)
    open(outd + '/noreturn.tsv', 'w').write(hd.rstrip() + '; columns: entry, reason\n' + ''.join('%08x\t%s\n' % kv for kv in nrr))
    open(outd + '/outside.tsv', 'w').write(hd.rstrip() + '; columns: entry, instruction, target outside the executable spans (the lifted jump faults)\n' + ''.join('%08x\t%08x\t%08x\n' % x for x in outside))
    unent = sorted({v for r, v in rvals if v not in ent})        # rule R3 check: every relocated in-span value is an entry
    ins_ = [r for r, v in rvals if intext(r)]; outr = sum(1 for r in ins_ if not (covP[r - T0] or covC[r - T0]))
    print('rule 1: relocated in-span values: %d sites, %d distinct, not entries %d (before = after = %d entries from pointers); reloc sites lying in the span: %d (inside reached ranges %d, outside every range %d)' % (
        len(rvals), len({v for r, v in rvals}), len(unent), len({v for r, v in rvals}), len(ins_), len(ins_) - outr, outr))
    print('closure (rule C, lifter explore): entries %d -> %d in %d rounds; new by cause %s; direct targets outside the executable sections (via the dispatcher, no entry) %d refs / %d distinct targets (outside.tsv); jecxz leaving its range %d' % (n_before, len(E), rounds, dict(sorted(closure_new.items())), len(outside), len({t for e, a, t in outside}), len(jxs)))
    print('ranges.tsv %d rows, noreturn.tsv %d rows, range-span vs extent mismatches %d' % (len(rg), len(nrr), bad))
    # ---- the residue is settled HERE, after universe.tsv / ranges.tsv exist and after the pcsrc facts of this run (pcsrc_s4.json) have been written: residue.closure reads them (tools/residue.py)
    # (the call that runs pcsrc.run_facts(outd, ...) must come before this point)
    # residue (valid executable bytes no entry body reaches and no PE directory names), cut by rule into: padding (nop / int3 instructions at the run's ends), dead gap (the run decodes
    # completely from its first byte, ends at a terminator or at the next reached instruction, and has relocated dwords only at operand fields), relocation slots (the bytes of a
    # HIGHLOW fixup site: a relocated pointer slot, e.g. a jump table), UNEXPLAINED (reason named). Nothing else is ever called data or dead.
    # residual rule: tools/residue.py (one implementation for modules and the exe)
    CLO = residue.closure(rvals, ent, outd); unent0, s4_status, s4_viol = CLO.unent, CLO.s4_status, CLO.s4_viol; closure_reason, closure_complete = CLO.reason, CLO.complete
    brk = Counter()
    TERMS = {'ret', 'retf', 'jmp', 'int3', 'ud2', 'iret', 'iretd'}; rsl = bytearray(TN); unx = []; rslot = array('I', bytes(4 * TN))
    for r in relocs:
        if T0 <= r < T1:
            for k in range(max(r, T0), min(r + 4, T1)): rsl[k - T0] = 1; rslot[k - T0] = r
    def residual(p, q):
        """[p, q) offsets of one residue run: tools/residue.py (closure complete -> data, else UNEXPLAINED with the closure reason); the sweep is a breakdown, it decides nothing"""
        brk[residue.sweep(md, text, T0, TN, relocs, rsl, cov, p, q)] += q - p
        if closure_complete: cl[p:q] = bytes([residue.DATA]) * (q - p)
        else: cl[p:q] = b'\x08' * (q - p); unx.append((T0 + p, q - p, closure_reason))
    for m in re.finditer(rb'\x00+', bytes(cl)):
        s, e = m.span(); p = s
        while p < e and padlen(p, e): p += padlen(p, e)
        q = e
        while q > p and text[q - 1] in (0xCC, 0x90): q -= 1
        for k in range(max(p, q - 15), q):
            x = k
            while x < q and padlen(x, q): x += padlen(x, q)
            if x == q: q = k; break
        cl[s:p] = b'\4' * (p - s); cl[q:e] = b'\4' * (e - q)
        if p < q: residual(p, q)
    cc = Counter(cl); rows = []
    for c in (1, 2, 3, 4, 5, 6, 8, 9):
        for m in re.finditer(bytes([c]) + b'+', bytes(cl)):
            s, e = m.span()
            if c in (1, 2):
                x = s
                for k, g in groupby(own[s:e]):
                    n = len(list(g)); rows.append((x, n, 1, 'entry:%08x' % k)); x += n
            elif c == 5:
                x = s
                for y in range(s + 1, e + 1):
                    if y == e or rslot[y] != rslot[x]: rows.append((x, y - x, c, 'reloc:%08x' % rslot[x])); x = y
            else: rows.append((s, e - s, c, '-' if c != 3 else 'pe-header'))
    rows.sort(); open(outd + '/codemap.tsv', 'w').write(''.join('%08x\t%d\t%s\t%s\n' % (T0 + s, n, CN[c], o) for s, n, c, o in rows))
    XCs, ENs, TLs = residue.pe_sources(M, u32, intext)
    residue.write_sources(os.path.dirname(outd) + '/' + name, residue.source_rows(CLO, outd, set(ent), XCs, ENs, TLs, {t for t, ks in ent.items() if ks & residue.EH_KINDS}))
    residue.combine(os.path.dirname(outd))
    nunx = cc[8]; unxr = Counter()
    print('UNEXPLAINED runs %d, bytes %d; first: %s' % (len(unx), cc[8], ' '.join('%08x+%d:%s:%s' % (x, n, r, text[x - T0:x - T0 + 6].hex()) for x, n, r in unx[:8])))
    for a_, n_, r_ in unx: unxr[r_] += n_
    # ---- totality gate
    targets = {v for r, v in rvals} | set(XC) | ({M.base + M.entry} if M.entry else set()) | {t for t in callers if intext(t)} | set(tailsrc) | set(clos) | {t for t, ks in ent.items() if ks & {'tls', 'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'seh-frame', 'eh-throw'}}
    cert = sum(1 for t in targets if t in ent and (t in dec or t in ud or t in xf))
    lv = [(a, t) for a, t in sorted(leaves)]; lvp = [x for x in lv if x[0] in pset]
    img = lambda t: M.sec(t) is not None
    why = []
    if RL is None: why.append('no .reloc: pointers into executable bytes cannot be enumerated')
    if cert != len(targets): why.append('%d targets without a completed descent' % (len(targets) - cert))
    if unent: why.append('%d relocated in-span values are not entries' % len(unent))
    if nunx: why.append('%d unexplained bytes (%s)' % (nunx, ', '.join('%s %d' % kv for kv in sorted(unxr.items()))))
    print('residual rule: closure %s (relocated in-span values not entries %d, s4 %s%s); data-unreached runs %d, bytes %d; informational breakdown (decides nothing): %s' % (
        'complete' if closure_complete else 'OPEN: ' + closure_reason, len(unent0), s4_status, '' if s4_viol is None else ' (%d violations)' % s4_viol, sum(1 for m in re.finditer(rb'\x09+', bytes(cl))), cc[9], ', '.join('%s %d' % kv for kv in sorted(brk.items()))))
    if mrow.size != len(M.d) or mrow.sizeimg != M.sizeimg or mrow.pref != M.pref or mrow.entry != M.entry or mrow.tls != (1 if M.dirent(9)[0] else 0): why.append('modules.tsv row disagrees with the PE header')
    if len(targets) != len(E): why.append('gate universe %d != universe.tsv rows %d' % (len(targets), len(E)))
    if viol: why.append('%d in-span direct targets are not entries (closure not reached)' % len(viol))
    if bad: why.append('%d entries whose ranges do not sum to the extent' % bad)
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
        # ---- exports against L1's list, imports by DLL, delay imports
    l1 = {}; l1f = os.path.join(os.path.dirname(os.environ.get('FNVNX_MODULES', pemod.MODULES)), 'exports', mrow.name + '.tsv')
    for ln in (open(l1f) if os.path.exists(l1f) else ()):
        f = ln.rstrip('\n').split('\t')
        if len(f) > 1 and not f[2]: l1[f[0]] = int(f[1], 16)
    xl = set(l1.values()); xp = set(XC) | set(XD)
    xdiff = (sorted(xp - xl), sorted(xl - xp)) if os.path.exists(l1f) else None
    dl = Counter(n.split('!')[0] for n in imports.values()); dd = pemod.delay_imports(M)
    open(outd + '/imports.tsv', 'w').write('# dll, imported name or #ordinal, IAT slot VA (delay imports: slot -)\n' + ''.join('%s\t%s\t%08x\n' % (n.split('!')[0], n.split('!', 1)[1], v) for v, n in sorted(imports.items())) + ''.join('%s\t%s\t-\n' % x for x in dd))
    print('exports: PE code %d, PE data %d (not code: no entry), forwarders %d; every code export is an entry: %s; L1 exports.tsv %s; entry point %08x is an entry: %s; TLS callbacks %d' % (
        len(XC), len(XD), XFW, all(a in ent for a in XC), 'ABSENT' if xdiff is None else 'differs: PE-only %d L1-only %d' % (len(xdiff[0]), len(xdiff[1])), M.base + M.entry, (M.base + M.entry) in ent if M.entry else 'n/a', sum(1 for ks in ent.values() if 'tls' in ks)))
    print('imports %d by DLL: %s; delay imports %d' % (len(imports), ' '.join('%s=%d' % kv for kv in sorted(dl.items())), len(dd)))
    print('%s: sections %d (executable %d: %s), reloc %s (%d fixups), imports %d, exports code %d (data %d) forwarders %d, entry rva %x' % (os.path.basename(path), len(M.secs), len(exs), ' '.join(exs[:4]) + (' ...' if len(exs) > 4 else ''), 'yes' if RL is not None else 'NO', len(relocs), len(imports), len(XC), len(XD), XFW, M.entry))
    print('\n'.join(fi[:3]))
    print('README: addresses in universe.tsv/codemap.tsv are absolute VAs at the assigned load base 0x%08x (preferred base 0x%08x; RVA = VA - base)' % (M.base, M.pref))
    nri = sorted({n.split('!')[-1] for n in imports.values()} & codemap.NRIMP)
    print('noreturn entries %d (seeds: thrower %s; never-returning imports present: %s; call sites %d, jmp sites %d)' % (len(NR), 'yes' if eh.thrower in ent else 'no', ' '.join(nri) or 'none', len(nrcall), len(nrjmp)))
    pu = [e for e in E if e in proven and cnt[e][0]]; why_ud = Counter()
    def udwhy(u, p):
        if p == 0: return 'entry-itself'
        d = dec.get(p); k = 'fallthrough' if p + d[0] == u else 'branch-target'
        return k + '-of-' + d[1] if k == 'branch-target' else k + '-after-' + d[1]
    for e in pu:
        for u, p in udinfo[e]: why_ud[udwhy(u, p)] += 1
    print('proven entries still with ud: %d; ud paths by cause: %s; first: %s' % (len(pu), dict(sorted(why_ud.items())), ' '.join('%08x@%08x:%s' % (e, udinfo[e][0][0], udwhy(*udinfo[e][0])) for e in pu[:10])))
    tc = Counter()
    for e in E:
        ss = set(exts[e]); pv = 'proven' if e in proven else 'candidate'
        for a in exts[e]:
            for t in dec[a][4]:
                if t in ent: tc[pv, 'entry'] += 1
                elif not intext(t): tc[pv, 'outside-spans'] += 1
                else: tc[pv, 'label-in-own-extent' if t in ss else 'target-undecodable'] += 1
    print('direct jmp/jcc targets of reached insns: %s' % '; '.join('%s %s=%d' % (k[0], k[1], n) for k, n in sorted(tc.items())))
    print('entries %d (proven %d, candidate %d): %s' % (len(E), len(proven), len(E) - len(proven), dict(sorted(kc.items()))))
    print('entries with ud %d (candidate %d), candidate entries whose first instruction is ud %d, with xf %d, with reloc-misfit %d' % (
        sum(1 for e in E if cnt[e][0]), sum(1 for e in E if cnt[e][0] and e not in proven), sum(1 for e in E if e in ud and e not in proven), sum(1 for e in E if cnt[e][1]), sum(1 for e in E if cnt[e][2])))
    print('instructions: proven %d, candidate-only %d; ud sites %d, xf sites %d, misfit %d; vtable runs proven %d, jump tables proven %d' % (len(pset), cand_only, len(ud), len(xf), len(misfit), len(donevt), len(donejt)))
    print('bytes of %d: %s' % (TN - cc[7], ', '.join('%s %d' % (CN[c] + ('-proven' if c == 1 else '-candidate' if c == 2 else ''), cc[c]) for c in sorted(CN))))
    print('pointer targets %d: (a) proven code %d, (b) data-read or PE-directory %d, (c) other %d; direct targets leaving spans: proven %d, candidate %d (in image %d)' % (len(pt), na, nb, nc, len(lvp), len(lv) - len(lvp), sum(1 for a, t in lv if img(t))))
    cm = {k: n for k, n in mcand.items() if k not in mp}
    print('mnemonics only in candidate-only code: %d mnemonics, %d instructions (of %d candidate-only); top 30: %s' % (len(cm), sum(cm.values()), cand_only, ' '.join('%s=%d' % kv for kv in sorted(cm.items(), key=lambda kv: (-kv[1], kv[0]))[:30])))
    print('mnemonics in proven code not in the exe universe (%d%s): %s' % (len([k for k in mp if k not in exe_mn]), '' if exe_mn else ', exe list missing', ' '.join('%s=%d' % kv for kv in new_mn)))
    open(outd + '/gate_codemap.json', 'w').write(json.dumps({'schema': 'fnvnx-gate/1', 'name': 'l3.codemap.' + name, 'status': 'green' if green else 'red', 'universe': TN - cc[7], 'certified': TN - cc[7] - nunx, 'uncertified': nunx, 'targets': len(targets), 'targets_certified': cert}) + '\n')
    import pcsrc; pcsrc_red, pcsrc_res = pcsrc.run_facts(outd, name, X_.text, X_.T0); print('gate %s red=%d sources=%d rows=%d' % (pcsrc_res['name'], pcsrc_red, pcsrc_res['sources'], pcsrc_res['rows']))
    print('gate l3.codemap.%s: %s (bytes %d, unexplained %d; targets %d, certified %d)%s' % (name, 'green' if green else 'red', TN - cc[7], nunx, len(targets), cert, ''.join('; RED: ' + w for w in why)))
    return 0 if green else 1
