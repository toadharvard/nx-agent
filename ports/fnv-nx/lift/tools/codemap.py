#!/usr/bin/env python3
"""R3 byte accounting of .text (used by universe.py; U is the universe module). Every byte is exactly one class:
1 instruction of a decomp function, 2 instruction of a non-decomp entry, 3 table/data named by a relocation, 4 padding,
5 dead code inside a decomp function, 6 dead code in a gap (no entry, no relocation names it), 0 unexplained (build error).
Dead = a run no path reaches that decodes completely from its first byte (no bytes left over), ends at a terminator or at the
next reached byte, has relocated fields only at instruction operand fields, and has no relocated dword (from a live site) and no
call pointing into it. Output: /home/user/l3facts/codemap.tsv (start, length, class, owner entry or referencing relocation)."""
import re, bisect
from collections import Counter
OUT = '/home/user/l3facts/codemap.tsv'
CLS = {1: 'entry-decomp', 2: 'entry-nondecomp', 3: 'data-reloc', 4: 'padding', 5: 'dead-decomp', 6: 'dead-gap', 0: 'UNEXPLAINED'}
TERM = {'ret', 'retf', 'jmp', 'int3', 'ud2', 'iret', 'iretd'}

def split_runs(U, runs):
    """cut unexplained runs at decomp-range boundaries."""
    out = []
    for s, e in runs:
        while s < e:
            k = U.dmap[s]; t = s + 1
            while t < e and U.dmap[t] == k: t += 1
            out.append((s, t)); s = t
    return out

def analyse(U, runs):
    """-> (res [(s, e, status)], opsites): status 'dead' or a failure reason; opsites = relocated sites inside operand fields of dead runs."""
    T0, TN = U.T0, U.TN; info = []; fields = []
    for s, e in runs:
        ins = list(U.md.disasm(U.text[s:e], T0 + s)); n = sum(i.size for i in ins); f = set()
        for i in ins:
            if i.disp_size == 4: f.add(i.address + i.disp_offset)
            if i.imm_size == 4: f.add(i.address + i.imm_offset)
        r = None
        if not ins or n != e - s: r = 'undecodable' if n == 0 else 'leftover%d' % (e - s - n)
        elif not (ins[-1].mnemonic in TERM or (e < TN and U.cov[e] == 1)): r = 'no-end'
        else:
            lo = bisect.bisect_left(U.relocs, T0 + s); hi = bisect.bisect_left(U.relocs, T0 + e)
            if any(x not in f for x in U.relocs[lo:hi]): r = 'reloc-not-operand'
        info.append(r); fields.append(f)
    starts = [T0 + s for s, e in runs]; inb = []
    for r, v in U.rvals:                                  # relocated dwords whose value lands in a candidate run
        k = bisect.bisect_right(starts, v) - 1
        if k >= 0 and v < T0 + runs[k][1]: inb.append((r, k))
    for t, cs in sorted(U.callers.items()):
        k = bisect.bisect_right(starts, t) - 1
        if k >= 0 and t < T0 + runs[k][1]: info[k] = info[k] or 'called'
    def site_run(r):
        k = bisect.bisect_right(starts, r) - 1
        return k if k >= 0 and r < T0 + runs[k][1] else None
    ch = True
    while ch:
        ch = False
        for r, k in inb:
            if info[k]: continue
            j = site_run(r)
            if j is None or info[j]: info[k] = 'reloc@%x' % r; ch = True
    ops = set()
    for k, (s, e) in enumerate(runs):
        if not info[k]: ops.update(x for x in U.relocs[bisect.bisect_left(U.relocs, T0 + s):bisect.bisect_left(U.relocs, T0 + e)])
    return [(s, e, info[k] or 'dead') for k, (s, e) in enumerate(runs)], ops

def finish(U):
    """final accounting -> (stdout lines, unexplained bytes)."""
    cls = U.classify(); runs = split_runs(U, U.pad_split(cls)); res, ops = analyse(U, runs); bad = []
    for s, e, st in res:
        if st == 'dead': cls[s:e] = (b'\5' if U.dmap[s] else b'\6') * (e - s)
        else: bad.append((s, e, st))
    cc = Counter(cls); L = []
    L.append('R3 bytes of .text %d: entry-decomp %d, entry-nondecomp %d, data-by-reloc %d, padding %d, dead-decomp %d, dead-gap %d, UNEXPLAINED %d' % (
        U.TN, cc[1], cc[2], cc[3], cc[4], cc[5], cc[6], cc[0]))
    dr = Counter(); db = Counter()
    for s, e, st in res:
        k = 'decomp' if U.dmap[s] else 'gap'; dr[k, st == 'dead'] += 1; db[k, st == 'dead'] += e - s
    L.append('dead runs/bytes: decomp %d/%d (checked clean+no reloc into: %d runs failed), gap %d/%d (%d failed); decomp funcs with dead code %d' % (
        dr['decomp', True], db['decomp', True], dr['decomp', False], dr['gap', True], db['gap', True], dr['gap', False],
        len({U.indec(U.T0 + s)[0] for s, e, st in res if st == 'dead' and U.dmap[s]})))
    for s, e, st in bad: L.append('  UNEXPLAINED %08x len %d: %s; bytes %s; relocs-into %s' % (U.T0 + s, e - s, st, U.text[s:s + 8].hex(),
        ' '.join('%x' % r for r, v in U.rvals if U.T0 + s <= v < U.T0 + e)[:40] or '-'))
    # ---- codemap.tsv
    OWNL = sorted(a for a in U.entries if a not in U.parent); rows = []
    for c in (1, 2, 5, 3, 4, 6, 0):
        for m in re.finditer(bytes([c]) + b'+', bytes(cls)):
            s, e = m.span()
            if c in (1, 2, 5):
                cuts = [s] + [a - U.T0 for a in OWNL[bisect.bisect_right(OWNL, U.T0 + s):bisect.bisect_left(OWNL, U.T0 + e)]] + [e]
                for x, y in zip(cuts, cuts[1:]):
                    o = U.indec(U.T0 + x) if c == 5 else None
                    i = bisect.bisect_right(OWNL, U.T0 + x) - 1
                    rows.append((x, y - x, c, 'entry:%08x' % (o[0] if o else OWNL[i] if i >= 0 else 0)))
            elif c == 3:
                x = s
                for y in range(s + 1, e + 1):
                    if y == e or U.tref[y] != U.tref[x]: rows.append((x, y - x, 3, 'reloc:%08x' % U.tref[x])); x = y
            else: rows.append((s, e - s, c, '-'))
    rows.sort()
    open(OUT, 'w').write(''.join('%08x\t%d\t%s\t%s\n' % (U.T0 + s, n, CLS[c], o) for s, n, c, o in rows))
    L.append('codemap.tsv: %d runs' % len(rows))
    return L, cc[0], cls

def consumers(U, eh):
    """consumer class of each relocated pointer stored in a DATA section that points into .text."""
    X86 = U.X; sec = U.sec; store = set(); dsites = [(r, v) for r, v in U.rvals if not U.intext(r)]; dfs = {d[6] for d in U.datarec}
    for v, ss in U.cref.items():                 # data addresses that code installs as an object's first dword (vptr store)
        if sec(v) in (None, '.text'): continue
        for q in ss:
            o = q - U.T0
            if U.cov[o] != 2: continue
            while U.cov[o] != 1: o -= 1
            i = next(U.md.disasm(U.text[o:o + 15], U.T0 + o, 1))
            if i.mnemonic == 'mov' and i.operands[0].type == X86.X86_OP_MEM and i.operands[1].type == X86.X86_OP_IMM and U.T0 + o + i.imm_offset == q: store.add(v); break
    cont = {}
    for t in sorted(v for v in U.cref if sec(v) not in (None, '.text')):
        q = t
        while q in U.rset: cont[q] = t; q += 4
    def cat(r):
        if r in dfs: return 'data:typed-record'
        if r in eh.sites: return 'ctl:eh-table'
        if r in U.tslots: return 'ctl:jump-table'
        t = cont.get(r)
        return 'res:no-container-run' if t is None else 'ctl:vtable-store' if t in store else 'res:container-not-vptr'
    sc = {r: cat(r) for r, v in dsites}; tot = Counter(sc.values()); L = []
    L.append('consumer of %d relocated data-section pointers into .text: %s' % (len(dsites), ', '.join('%s %d' % kv for kv in sorted(tot.items()))))
    for rec, v, n, ln, cs, ng, r in sorted(U.datarec):
        L.append('  DIDATAFORMAT %08x rgodf=%08x: %d x16 = %d bytes read as data; consumer IDirectInputDevice::SetDataFormat (call [vtbl+0x2c]) at %s; %d relocated pguid fields inside' % (
            rec, v, n, ln, ' '.join('%x' % c for c in cs), ng))
    def scat(r):                                 # consumer category of one pointer site
        if r in sc: return sc[r]
        m = U.tcons.get(r)
        return 'ctl:indexed-' + m if m in ('call', 'jmp') else 'res:indexed-load' if m else 'res:address-taken-by-code' if U.cov[r - U.T0] == 2 else 'res:slot-in-unreached-text'
    po = [a for a in sorted(U.entries) if U.kinds[a] == {'ptr'} and a not in U.fsize]; cl = {a: [scat(r) for r in U.psites.get(a, ())] for a in po}
    ctl = [a for a in po if any(k.startswith(('ctl', 'data')) for k in cl[a])]; cs_ = Counter(k for a in ctl for k in sorted({k for k in cl[a] if k.startswith('ctl')})[:1])
    rs = Counter(sorted(k for k in cl[a] if k.startswith('res'))[0] for a in po if a not in set(ctl))
    L.append('ptr-only entries %d: consumer transfers control %d (%s); RESIDUAL consumer unknown %d (%s)' % (len(po), len(ctl), ', '.join('%s %d' % kv for kv in sorted(cs_.items())), len(po) - len(ctl), ', '.join('%s %d' % kv for kv in sorted(rs.items()))))
    L.append('  residual class: entry-nondecomp (must decode; checked: %d undecodable, %d bad-decode, %d mid-insn => build error if any)' % (sum(1 for k, r in U.rejects.values() if k == 'undecodable'), len(U.bad_entries), sum(1 for k, r in U.rejects.values() if k != 'undecodable')))
    return L
