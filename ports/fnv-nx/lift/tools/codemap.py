#!/usr/bin/env python3
"""R3 byte accounting of .text (used by universe.py; U is the universe module). Every byte is exactly one class:
1 instruction of a decomp function, 2 instruction of a non-decomp entry, 3 table/data named by a relocation, 4 padding,
5 dead code inside a decomp function, 6 dead code in a gap (no entry, no relocation names it), 8 data named by a PE data directory,
7 hole between executable sections (not part of the universe), 0 unexplained (build error).
Dead = a run no path reaches that decodes completely from its first byte (no bytes left over), ends at a terminator or at the
next reached byte, has relocated fields only at instruction operand fields, and has no relocated dword (from a live site) and no
call pointing into it. Entries made by rule R3 alone (kind ptr) or by the closure under direct targets (kind call/tail; universe.r3: a value whose bytes no rule above reached as an instruction start) are bodies re-read
from bytes that are already classified exactly once here; they add no class of their own and take no owner cut in codemap.tsv. Where such a body overlaps
data named by a relocation (class 3) or instructions of another entry, the byte keeps its class (instruction of a committed entry, else relocation-named
data): class precedence is for accounting only, the entry still exists and its undecodable bytes raise #UD at run time.
Output: <outdir>/codemap.tsv (start, length, class, owner entry or referencing relocation)."""
import os, re, bisect
from collections import Counter
CLS = {7: 'hole', 8: 'data-pe-header', 1: 'entry-decomp', 2: 'entry-nondecomp', 3: 'data-reloc', 4: 'padding', 9: 'data-unreached', 0: 'UNEXPLAINED'}
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
        elif not (ins[-1].mnemonic.split(' ')[-1] in TERM or (e < TN and U.cov[e] == 1)): r = 'no-end'
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
    """final accounting -> (stdout lines, unexplained bytes, classes). The residue runs are settled by tools/residue.py (the one residual rule); the per-run sweep reason is printed for information."""
    import residue
    cls = U.classify(); runs = split_runs(U, U.pad_split(cls)); res, ops = analyse(U, runs); bad = []
    CLO = residue.closure(U.vals, U.entries, U.outdir); brk = Counter()
    for s, e, st in res:
        brk[st if st != 'dead' else 'decodes-and-ends'] += e - s
        if CLO.complete: cls[s:e] = bytes([residue.DATA]) * (e - s)
        else: bad.append((s, e, CLO.reason))
    XCs, ENs, TLs = residue.pe_sources(U.M, U.u32, U.intext)
    residue.write_sources(os.path.join(U.outdir, 'FalloutNV'), residue.source_rows(CLO, U.outdir, set(U.entries), XCs, ENs, TLs, {t for t, ks in U.kinds.items() if ks & residue.EH_KINDS}))
    residue.combine(U.outdir)
    cc = Counter(cls); L = []
    L.append('R3 bytes of executable sections %d: entry-decomp %d, entry-nondecomp %d, data-by-reloc %d, data-pe-header %d, padding %d, data-unreached %d, UNEXPLAINED %d' % (
        U.TN - cc[7], cc[1], cc[2], cc[3], cc[8], cc[4], cc[9], cc[0]))
    L.append(residue.line(CLO, cc[9], len(res) if CLO.complete else 0) + '; residue runs %d, bytes %d; informational sweep of the residue runs (decides nothing): %s' % (len(res), sum(e - s for s, e, st in res), ', '.join('%s %d' % kv for kv in sorted(brk.items()))))
    for s, e, st in bad[:20]: L.append('  UNEXPLAINED %08x len %d: %s; bytes %s' % (U.T0 + s, e - s, st, U.text[s:s + 8].hex()))
    # ---- codemap.tsv
    OWNL = sorted(a for a in U.entries if a not in U.parent and a not in U.r3); rows = []
    for c in (1, 2, 3, 8, 4, 9, 0):
        for m in re.finditer(bytes([c]) + b'+', bytes(cls)):
            s, e = m.span()
            if c in (1, 2):
                cuts = [s] + [a - U.T0 for a in OWNL[bisect.bisect_right(OWNL, U.T0 + s):bisect.bisect_left(OWNL, U.T0 + e)]] + [e]
                for x, y in zip(cuts, cuts[1:]):
                    o = None
                    i = bisect.bisect_right(OWNL, U.T0 + x) - 1
                    rows.append((x, y - x, c, 'entry:%08x' % (o[0] if o else OWNL[i] if i >= 0 else 0)))
            elif c == 3:
                x = s
                for y in range(s + 1, e + 1):
                    if y == e or U.tref[y] != U.tref[x]: rows.append((x, y - x, 3, 'reloc:%08x' % U.tref[x])); x = y
            else: rows.append((s, e - s, c, '-' if c != 8 else 'pe-header'))
    rows.sort()
    open(U.outdir + '/codemap.tsv', 'w').write(''.join('%08x\t%d\t%s\t%s\n' % (U.T0 + s, n, CLS[c], o) for s, n, c, o in rows))
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
    po = [a for a in sorted(U.entries) if U.kinds[a] == {'ptr'} and a not in U.fsize and a not in U.r3]; cl = {a: [scat(r) for r in U.psites.get(a, ())] for a in po}
    ctl = [a for a in po if any(k.startswith(('ctl', 'data')) for k in cl[a])]; cs_ = Counter(k for a in ctl for k in sorted({k for k in cl[a] if k.startswith('ctl')})[:1])
    rs = Counter(sorted(k for k in cl[a] if k.startswith('res'))[0] for a in po if a not in set(ctl))
    L.append('ptr-only entries %d: consumer transfers control %d (%s); RESIDUAL consumer unknown %d (%s)' % (len(po), len(ctl), ', '.join('%s %d' % kv for kv in sorted(cs_.items())), len(po) - len(ctl), ', '.join('%s %d' % kv for kv in sorted(rs.items()))))
    L.append('  residual class: entry-nondecomp (must decode; checked: %d undecodable, %d bad-decode, %d mid-insn => build error if any)' % (sum(1 for k, r in U.rejects.values() if k == 'undecodable'), len(U.bad_entries), sum(1 for k, r in U.rejects.values() if k != 'undecodable')))
    return L

# ---- consumer rules moved out of universe.py
def set_dataformat(U, q):
    """call address if the reloc site q is `push imm32` whose call (within 12 insns) is `call reg`, reg loaded from [m+0x2c]
    (IDirectInputDevice::SetDataFormat, method 11); False if not; None if the site is not reached yet."""
    o = q - U.T0; X = U.X
    if not U.cov[o]: return None
    while U.cov[o] != 1: o -= 1
    ins = next(U.md.disasm(U.text[o:o + 15], U.T0 + o, 1))
    if ins.mnemonic != 'push' or U.T0 + o + ins.imm_offset != q: return False
    ld = set()
    for g in U.md.disasm(U.text[o + ins.size:o + ins.size + 120], U.T0 + o + ins.size):
        op = g.operands
        if g.mnemonic == 'mov' and len(op) == 2 and op[0].type == X.X86_OP_REG and op[1].type == X.X86_OP_MEM and op[1].mem.disp == 0x2c: ld.add(op[0].reg)
        elif g.mnemonic == 'call': return g.address if op[0].type == X.X86_OP_REG and op[0].reg in ld else False
        elif g.mnemonic in ('ret', 'jmp') or g.mnemonic[0] == 'j': return False
    return False
def dfmt(U, r, v, final):
    """r is the rgodf field of a DIDATAFORMAT record (dwSize 0x18, dwObjSize 0x10, [dwFlags, dwDataSize, dwNumObjs, rgodf]) whose every code
    reference is `push rec` for IDirectInputDevice::SetDataFormat: DirectInput reads DIOBJECTDATAFORMAT[dwNumObjs] through rgodf (data).
    -> True proven (object marked class 3); 'wait' consumer not yet reached; None rule does not apply."""
    rec = r - 0x14; rs = U.sec(rec); u32 = U.u32
    if rs in (None, '.text') or u32(rec) != 0x18 or u32(rec + 4) != 0x10 or not U.intext(v) or rec not in U.cref: return None
    ds, n = u32(rec + 12), u32(rec + 16); ln = 16 * n
    if not 0 < n <= 0x100 or v + ln > U.T1 or any(U.cov[v - U.T0:v - U.T0 + ln]): return None
    for k in range(n):
        g, ofs = u32(v + 16 * k), u32(v + 16 * k + 4)
        if (g and (v + 16 * k not in U.rset or U.sec(g) not in ('.rdata', '.data'))) or ofs >= ds: return None
    cs = [set_dataformat(U, q) for q in U.cref[rec]]
    if None in cs: return None if final else 'wait'
    if not all(cs): return None
    U.mark(v, ln, r); U.dinfo['didataformat'] += 1; ng = sum(1 for q in U.relocs if v <= q < v + ln)
    U.datarec.append((rec, v, n, ln, sorted(cs), ng, r)); return True

# ---- strict (module) pointer classification: a pointer into executable bytes is code only on control-transfer evidence, data only on
# data-read evidence; everything else is undetermined and never becomes an entry.
STRF = {n: a for a, ns in (((0,), 'LoadLibraryA GetModuleHandleA OutputDebugStringA CreateFileA lstrlenA strlen _strdup atoi atof fopen_s'), ((1,), 'GetProcAddress'),
        ((0, 1), 'strcmp _stricmp stricmp strncmp _strnicmp lstrcmpA lstrcmpiA lstrcpyA lstrcatA strcpy strcat strstr strchr sprintf wsprintfA printf fopen sscanf'),
        ((1, 2), 'MessageBoxA'), ((2,), 'sprintf_s')) for n in ns.split()}
WIDE = {'LoadLibraryW': (0,), 'GetModuleHandleW': (0,), 'OutputDebugStringW': (0,), 'lstrcmpW': (0, 1), 'wcscmp': (0, 1), '_wcsicmp': (0, 1), 'wcslen': (0,), 'wcscpy': (0, 1)}
def callee(U, o, limit=16):
    """(n pushes before the first call after text offset o, import name or kind of that call)."""
    X = U.X; n = 0
    for g in U.md.disasm(U.text[o:o + 160], U.T0 + o):
        limit -= 1
        if g.mnemonic == 'push': n += 1
        elif g.mnemonic == 'call':
            op = g.operands[0]
            if op.type == X.X86_OP_MEM and not op.mem.base and not op.mem.index: return n, U.imports.get(op.mem.disp & 0xffffffff, 'indirect-abs')
            return n, 'direct' if op.type == X.X86_OP_IMM else 'indirect'
        elif g.mnemonic in ('ret', 'jmp') or g.mnemonic[0] == 'j' or limit < 0: return n, 'none'
    return n, 'none'
def string_arg(U, r, ins, v):
    """size in bytes of the string at v if `push v` is argument k of a call to an import that reads that argument as a string."""
    if ins.mnemonic != 'push' or U.cov[v - U.T0]: return None
    n, c = callee(U, r - U.T0 - ins.imm_offset + ins.size); nm = c.split('!')[-1]
    wide = n in WIDE.get(nm, ()); 
    if not (wide or n in STRF.get(nm, ())): return None
    e = v - U.T0
    while e + 1 < U.TN and U.V[e] and (U.text[e:e + 2] != b'\0\0' if wide else U.text[e]): e += 2 if wide else 1
    return e - (v - U.T0) + (2 if wide else 1)
def vstore(U, t):
    """True if code stores the data address t into memory (`mov [m], imm32`): t is an object's vptr, its slots are called through."""
    if t in U.vok: return True
    for q in U.cref.get(t, ()):
        o = q - U.T0
        if U.cov[o] != 2: continue
        while U.cov[o] != 1: o -= 1
        i = next(U.md.disasm(U.text[o:o + 15], U.T0 + o, 1)); X = U.X
        if i.mnemonic == 'mov' and i.operands[0].type == X.X86_OP_MEM and i.operands[1].type == X.X86_OP_IMM and U.T0 + o + i.imm_offset == q: U.vok.add(t); return True
    return False
def rtti(U, w, ref):
    """w is an MSVC RTTI Complete Object Locator (signature 0, relocated pTypeDescriptor and pClassHierarchyDescriptor): mark it and what it names as data."""
    u = U.u32; rs = U.rset
    if not (U.intext(w) and u(w) == 0 and w + 12 in rs and w + 16 in rs and U.intext(u(w + 12)) and U.intext(u(w + 16)) and u(u(w + 16)) == 0): return False
    U.mark(w, 20, ref); td, ch = u(w + 12), u(w + 16); U.mark(td, 9 + len(U.cstr(td + 8)), w + 12)
    n, ba = u(ch + 8), u(ch + 12); U.mark(ch, 16, w + 16)
    if n <= 0x100 and ba + 4 * n and U.intext(ba): U.mark(ba, 4 * n, ch + 12)
    for k in range(n if n <= 0x100 else 0):
        b = u(ba + 4 * k)
        if U.intext(b): U.mark(b, 24, ba + 4 * k)
    U.dinfo['rtti_col'] += 1; return True
def ptr_run(U, t):
    """the relocated-dword run started at t is a vtable (it ends at the next vptr-installed address): every slot into executable bytes is a code
    entry unless it is an RTTI locator; the run is class-3 data when it lies in .text."""
    s = []; q = t
    while q in U.rset and (q == t or not vstore(U, q)): s.append(q); q += 4
    if U.intext(t) and (t - 4) in U.rset and rtti(U, U.u32(t - 4), t - 4): U.mark(t - 4, 4, t - 4)
    for k, q in enumerate(s):
        w = U.u32(q)
        if U.intext(w) and rtti(U, w, q): U.mark(q, 4, q); U.settled.add(q); continue
        if U.intext(w):
            U.ptr_target(w, q)
            if U.rejects.get(w, (0, 0))[1] == q: U.rejects.pop(w); U.dinfo['vtable_run_ends_at_non_code'] += 1; s = s[:k]; break    # a slot that cannot be code ends the run
        if U.intext(t): U.mark(q, 4, t)
        U.settled.add(q); U.vsites.add(q)
    U.tstarts.add(t); U.dinfo['vtable_runs'] += 1
    return len(s)
def strict_pass(U, final, eh):
    new = 0
    for r, v in U.rvals:
        if r in U.settled: continue
        if r in eh.sites or r in U.tslots: U.settled.add(r); continue
        k, ins = U.site_kind(r)
        if k == 'tbl': U.settled.add(r); continue
        if k == 'other': U.dinfo['other_site'] += 1; U.rejects[v] = ('other-site', r); U.settled.add(r); continue
        if k == 'disp' and ins.mnemonic != 'lea':
            U.disp_ref(r, v, ins, final); U.settled.add(r); new += 1; continue
        if k == 'imm':
            n = string_arg(U, r, ins, v)
            if n: U.mark(v, n, r); U.dinfo['string_arg'] += 1; U.settled.add(r); new += 1; continue
        if k == 'slot':
            if not final: continue
            if r in U.deadops: U.dinfo['slot_in_dead_operand'] += 1; U.settled.add(r); continue
        t = U.cont.get(r) if k in ('data', 'slot') else None
        if t is not None and vstore(U, t): new += ptr_run(U, t) > 0
        elif U.cov[v - U.T0] == 1 or U.tbl[v - U.T0]: U.settled.add(r)
    return new
def shape(U, ins):
    X = U.X; return ','.join({X.X86_OP_REG: 'r', X.X86_OP_MEM: 'm', X.X86_OP_IMM: 'i'}.get(o.type, '?') for o in ins.operands)
def ptrclass(U):
    """pointer targets (distinct relocated values into executable bytes): (a) code, (b) data, (c) undetermined, with the 10 largest (c) groups."""
    sites = {}
    for r, v in U.rvals: sites.setdefault(v, []).append(r)
    cnt = Counter(); grp = Counter(); why = Counter()
    for v in sorted(sites):
        ss = sites[v]; ctl = [r for r in ss if r in U.vsites or r in U.eh_sites or r in U.tslots]
        if ctl or v in U.entries: cnt['a'] += 1; why['a:control-consumer' if ctl else 'a:entry'] += 1
        elif U.tbl[v - U.T0] or U.pedat[v - U.T0]: cnt['b'] += 1; why['b:data-extent'] += 1
        elif U.cov[v - U.T0] == 1: cnt['a'] += 1; why['a:direct-flow'] += 1
        else:
            cnt['c'] += 1; r = ss[0]; k, ins = U.site_kind(r)
            if k == 'tbl': p = 'table-slot loaded by ' + U.tcons.get(r, 'data')
            elif ins is None: p = '%s-site %s' % (k, 'in-vtable-run-unproven' if r in U.cont else 'no-container')
            else:
                o = r - U.T0 - (ins.imm_offset if k == 'imm' else ins.disp_offset) + ins.size
                p = '%s %s >%s' % (ins.mnemonic, shape(U, ins), callee(U, o)[1] if ins.mnemonic == 'push' else '-')
            grp[p] += 1
    L = ['pointer targets %d: (a) code %d [%s], (b) data %d, (c) UNDETERMINED %d' % (len(sites), cnt['a'], ', '.join('%s %d' % kv for kv in sorted(why.items()) if kv[0][0] == 'a'), cnt['b'], cnt['c'])]
    L += ['  (c) %6d  %s' % (n, p) for p, n in sorted(grp.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
    return L, cnt['c']

# ---- NORETURN: least fixed point over entries
NRIMP = {'ExitProcess', 'ExitThread', '_exit', 'exit', 'abort', '_amsg_exit', '_CxxThrowException', 'longjmp'}      # imports documented as never returning
def nr_sites(U, imports):
    """({`call [iat]` insn addresses}, {`jmp [iat]` insn addresses}) whose IAT slot imports a documented never-returning function."""
    slots = {a: n.split('!')[-1] for a, n in imports.items() if n.split('!')[-1] in NRIMP}; calls = set(); jmps = set(); names = {}
    for r in U.relocs:
        if U.intext(r) and U.u32(r) in slots and U.intext(r - 2):
            b = U.text[r - 2 - U.T0:r - U.T0]
            if b == b'\xff\x15': calls.add(r - 2); names[r - 2] = slots[U.u32(r)]
            elif b == b'\xff\x25': jmps.add(r - 2); names[r - 2] = slots[U.u32(r)]
    return calls, jmps, names
def nr_rows(N, res, names, seeds, T0=0):
    """[(entry, reason)] sorted: import:<names> | calls-to-noreturn | ud | fault | infinite-loop | seed:thrower."""
    out = []
    for e in sorted(N):
        ext, deps, ends = res[e]
        imp = sorted({names[a] for a in ext if a in names})
        if e in seeds: r = 'seed:thrower'
        elif 'nrimp' in ends: r = 'import:' + ','.join(imp)
        elif 'callN' in ends or any(isinstance(x, int) for x in ends): r = 'calls-to-noreturn'
        elif 'ud' in ends: r = 'ud'
        elif 'fault' in ends: r = 'fault'
        else: r = 'infinite-loop'
        out.append((e, r))
    return out
def ranges(ext, size):
    """merged half-open byte ranges of an extent (instruction granular): [(start, end)]."""
    out = []
    for a in sorted(ext):
        e = a + size(a)
        if out and out[-1][1] == a: out[-1][1] = e
        else: out.append([a, e])
    return out
def rng_str(r): return ','.join('%x-%x' % (a, b) for a, b in r)
def noreturn_lfp(E, trav, seeds):
    """N = least set such that an entry is in N iff no path of its descent reaches a ret, every path ends in a call to a member of N, a call/jmp to a
    never-returning import, `ud`, a fault, an infinite loop or a tail jump (or fall-through) into a member of N, and no path ends in an unknown
    indirect jump. trav(e, N) -> (extent, deps, ends): ends holds 'ret' | 'bad' | 'ud' | 'fault' | 'nrimp' | 'callN' | entry addresses (tail edges).
    Returns (N, {entry: (extent, deps, ends)} computed with the final N)."""
    N = set(seeds); res = {}; uses = {}; dirty = set(E)
    while dirty:
        new = []
        for e in sorted(dirty):
            ext, deps, ends = trav(e, N); res[e] = (ext, deps, ends)
            for x in deps: uses.setdefault(x, set()).add(e)
            if e not in N and 'ret' not in ends and 'bad' not in ends and all(x in N for x in ends if isinstance(x, int)): new.append(e)
        dirty = set()
        for e in new: N.add(e)
        for e in new: dirty |= uses.get(e, set())
    return N, res
def exe_noreturn(U, thrower):
    """exe pipeline: compute N over the converged entry set, then unreach the instructions that only flow after a call to a member of N (entries are unchanged).
    -> (|N|, removed instructions, removed bytes)."""
    ins = U.insns; ent = U.entries; fs = U.fsize; T0, T1 = U.T0, U.T1; RET = ('ret', 'retf', 'iret', 'iretd')
    calltg = {a: t for t, ss in sorted(U.callers.items()) for a in ss}; nrcall, nrjmp, nrname = nr_sites(U, U.imports)
    def trav(e, N):
        f = U.indec(e); lo, hi = (f[0], f[0] + f[1]) if f else (T0, T1); dec = e in fs
        ext = []; seen = set(); deps = set(); ends = set(); st = [e]
        while st:
            a = st.pop()
            while True:
                if a in seen: break
                if not (lo <= a < hi) or (a != e and not dec and a in ent):
                    if a in ent: deps.add(a); ends.add(a)
                    else: ends.add('bad')
                    break
                di = ins.get(a)
                if di is None: ends.add('bad'); break
                seen.add(a); ext.append(a); mn = di[1]
                if mn in RET: ends.add('ret'); break
                ct = calltg.get(a)
                if ct is not None: deps.add(ct)
                if a in nrcall or (ct is not None and ct in N): ends.add('callN'); break
                if mn == 'jmp' and not di[4]: ends.add('nrimp' if a in nrjmp else 'bad'); break
                st.extend(di[4])
                if not di[3]:
                    if not di[4]: ends.add('fault')
                    break
                a += di[0]
        return ext, deps, ends
    E = sorted(ent); seeds = {thrower} & set(ent); N, res = noreturn_lfp(E, trav, seeds); keep = bytearray(U.TN)
    for e in E:
        for a in res[e][0]: keep[a - T0] = 1
    gone = [a for a in sorted(ins) if not keep[a - T0]]; nb = nbd = 0
    for a in gone:
        sz = ins[a][0]; nb += sz; nbd += sz * U.dmap[a - T0]; U.cov[a - T0:a - T0 + sz] = bytes(sz); del ins[a]
    return len(N), len(gone), (nb, nbd), nr_rows(N, res, nrname, seeds)
