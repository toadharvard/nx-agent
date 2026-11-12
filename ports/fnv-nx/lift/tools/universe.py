#!/usr/bin/env python3
"""Code universe of FalloutNV.exe: recursive descent from decomp starts, entry point, call/tail targets, reloc'd
pointers (rule R3: EVERY relocated value into an executable section is an entry; the consumer rules below only classify bytes
for the accounting and never exclude an entry); jump tables; R3 byte accounting of .text (codemap.py), entry-set
comparison (reconcile.py). Output: $FNVNX_FACTS_OUT (default /home/user/l3facts)/{universe,codemap}.tsv + count-only stdout; exit 1 if any byte is unexplained."""
import os, re, sys, json, struct, bisect, capstone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))      # the tool root: a tool imports its siblings from its own root (the generated stages run it under python3 -I, which has no script directory on the path)
from types import SimpleNamespace
import ehtables, codemap, reconcile, pemod, residue, jtab as JT
from array import array
from collections import Counter, deque
from capstone import x86 as X
import cli
USAGE = 'usage: universe.py [--module NAME]   code universe of the exe (FNVNX_EXE) or of the module NAME (a name or file of modules.tsv); env FNVNX_FACTS_OUT, FNVNX_MODULES, FNVNX_MODDIRS'
if __name__ == '__main__': cli.parse(USAGE, sys.argv[1:], (0, 0), {'--module': True})
ARGV = sys.argv[1:]; MOD = ARGV[ARGV.index('--module') + 1] if '--module' in ARGV else None; STRICT = MOD is not None   # module mode: pointers are code/data only on evidence
if not STRICT and 'FNVNX_ROUND' not in os.environ:       # driver: reloc-consistency refusals to their fixed point (each round is a whole run; R_{k+1} = R_k + the pointer-only entries with a misfit; refusals only shrink the flow, so R grows monotonically)
    import subprocess; _od = os.environ.get('FNVNX_FACTS_OUT', '/home/user/l3facts'); os.makedirs(_od, exist_ok=True); _rf = _od + '/refused.tsv'; _R = set()
    for _k in range(12):
        open(_rf, 'w').write(''.join('%08x\n' % a for a in sorted(_R))); _env = dict(os.environ, FNVNX_ROUND=str(_k), FNVNX_REFUSED=_rf)
        _p = subprocess.run([sys.executable, '-I', os.path.abspath(__file__)] + ARGV, env=_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        _nx = set(int(l.split()[0], 16) for l in open(_od + '/refused_next.tsv') if l.strip()) if os.path.exists(_od + '/refused_next.tsv') else set()
        if _nx <= _R:
            sys.stdout.write(_p.stdout); print('reloc-consistency fixed point after %d rounds: %d entries refused' % (_k + 1, len(_R)))
            open(_rf, 'w').write('# columns: entry; unproven pointer-only entries refused by reloc-consistency (the bytes go to data, the relocated pointer is a data fact)\n' + ''.join('%08x\n' % a for a in sorted(_R))); sys.exit(_p.returncode)
        _R |= _nx
    raise RuntimeError('reloc-consistency: no fixed point in 12 rounds')
if STRICT: import modlift; sys.exit(modlift.run(MOD))      # module mode: separate engine (every relocated pointer into executable bytes is an entry)
EXE = MOD or os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
FUNCS = os.environ.get('FNVNX_FUNCS', None if STRICT else '/home/user/l3facts/funcs.tsv')
NAME = os.path.splitext(os.path.basename(MOD))[0].lower() if STRICT else ''
OUTD = os.environ.get('FNVNX_FACTS_OUT', '/home/user/l3facts') + ('/' + NAME if STRICT else ''); OUT = OUTD + '/universe.tsv'; os.makedirs(OUTD, exist_ok=True)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
M = pemod.load(EXE); d, pe, base, secs, loc, off, sec, u32, dirent = M.d, M.pe, M.base, M.secs, M.loc, M.off, M.sec, M.u32, M.dirent
RL = pemod.relocs(M); relocs = sorted(RL or []); rset = set(relocs); imports = pemod.imports(M)
T0 = min(a for n, a, z, ro in M.xs); T1 = max(a + z for n, a, z, ro in M.xs); TN = T1 - T0; SINGLE = len(M.xs) == 1
V = bytearray(TN); tx = bytearray(b'\xcc' * TN)                   # V: byte belongs to an executable section; the holes between sections are filled with int3
for n, a, z, ro in M.xs: V[a - T0:a - T0 + z] = b'\1' * z; tx[a - T0:a - T0 + z] = d[ro:ro + z]
text = bytes(tx)
def intext(a): return T0 <= a < T1 and (SINGLE or V[a - T0] == 1)
REFD = set(int(l.split()[0], 16) for l in open(os.environ['FNVNX_REFUSED']) if l.strip() and l[0] != '#') if os.environ.get('FNVNX_REFUSED') and os.path.exists(os.environ['FNVNX_REFUSED']) else set()      # reloc-consistency (residue.misfit_next): pointer-only entries refused in an earlier round, their values are data facts
rvals = [(r, u32(r)) for r in relocs]; rvals = [(r, v) for r, v in rvals if intext(v) and v not in REFD]
funcs = []
for ln in (open(FUNCS) if FUNCS and os.path.exists(FUNCS) else ()):
    if ln.strip() and not ln.startswith('#'):
        a, s = ln.split()[:2]; a = int(a, 16); s = int(s, 16) if s.startswith('0x') else int(s)
        if intext(a): funcs.append((a, min(s, T1 - a)))
funcs.sort(); fstarts = [a for a, s in funcs]; fsize = dict(funcs)
dmap = bytearray(TN)                      # 1 = inside decomp range
for a, s in funcs: dmap[a - T0:a - T0 + s] = b'\1' * s
cov = bytearray(TN)                       # 1 = insn start, 2 = insn interior
tbl = bytearray(TN)                       # 1 = jump/index table data
pedat = bytearray(TN)                     # 1 = named by a PE data directory
tref = array('I', bytes(4 * TN))          # relocation (site) that names each data byte
insns = {}                                # addr -> (size, mnem, feat, fall, targets)
entries = {}; kinds = {}; PRI = ['decomp', 'entry', 'tls', 'export', 'call', 'tail', 'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'seh-frame', 'eh-throw', 'inner', 'ptr']
tslots = set(); tstarts = set(); dinfo = Counter(); midsrc = set(); midcall = set(); bad_entries = set(); queue = deque()
r3set = set(); vsites = set(); vok = set(); jtab = set(); tcons = {}; straddle = set(); deadops = set(); datarec = []; arrays = []; callers = {}; parent = {}; esite = {}; psites = {}; rejects = {}; settled = set()
FEATS = ['ijmp_reg', 'ijmp_mem', 'fs', 'sys', 'lock', 'rep', 'x87', 'sse', 'mmx', 'in', 'out', 'cli', 'sti', 'rdtsc', 'cpuid', 'exotic']
EXOTIC = set('aaa aas daa das aam aad arpl bound in out insb insd outsb outsd sldt hlt lcall ljmp popal pushal iretd retf into salc'.split())
EXO = {'hlt', 'rdmsr', 'wrmsr', 'lgdt', 'lidt', 'sgdt', 'sidt', 'ltr', 'str', 'invd', 'wbinvd', 'lmsw', 'smsw', 'clts', 'rdpmc', 'invlpg', 'ud2', 'iret', 'iretd', 'syscall', 'sysret', 'sysexit', 'bound', 'arpl', 'into', 'sldt', 'lldt', 'verr', 'verw'}
FM = {'in': 512, 'insb': 512, 'insw': 512, 'insd': 512, 'out': 1024, 'outsb': 1024, 'outsw': 1024, 'outsd': 1024, 'cli': 2048, 'sti': 4096, 'rdtsc': 8192, 'cpuid': 16384}
SSEG = {148, 149, 150, 151, 152, 153, 154, 132, 133, 134}
def mark(a, n, ref):
    """class-3 bytes [a, a+n) named by the relocation at site ref."""
    n = min(n, T1 - a); tbl[a - T0:a - T0 + n] = b'\1' * n; tref[a - T0:a - T0 + n] = array('I', [ref]) * n
def indec(a):
    i = bisect.bisect_right(fstarts, a) - 1
    return funcs[i] if i >= 0 and a < funcs[i][0] + funcs[i][1] else None
def add_entry(a, kind, src=None):
    if not intext(a): return
    f = indec(a)
    if f and f[0] != a:
        if kind == 'call': midcall.add(a); return
        if kind == 'tail': midsrc.add((a, src)); return
        if cov[a - T0] == 2: dinfo['overlap_' + kind] += 1; return
        parent[a] = f[0]
        if a not in entries: entries[a] = None; kinds[a] = set(); queue.append(a)
        kinds[a].update(('inner', kind)); esite.setdefault(a, src); return
    if not f and a not in entries and cov[a - T0] == 2: dinfo['overlap_' + kind] += 1; return
    if a not in entries: entries[a] = None; kinds[a] = set(); queue.append(a)
    kinds[a].add(kind)
    if src is not None and kind != 'tail': esite.setdefault(a, src)
def reloc_conflict(a, ins):
    """a relocated dword must lie wholly inside an operand field of one instruction or wholly outside all instructions."""
    i = bisect.bisect_left(relocs, a - 3); fs = (a + ins.disp_offset if ins.disp_size == 4 else -1, a + ins.imm_offset if ins.imm_size == 4 else -1)
    while i < len(relocs) and relocs[i] < a + ins.size:
        if relocs[i] not in fs: return True
        i += 1
    return False
def walk(start, lo, hi, dec, r3=False):
    """descent from start within [lo,hi) -> (ok, new insns, refs, tables). r3: the body of an R3 pointer entry, read from its first byte whatever
    covers it (no interior/table stop; an indexed jmp is an indirect jmp, its relocated targets are entries by R3); the result is never committed."""
    out = {}; refs = []; tabs = []; ok = True; work = [start]
    while work:
        a = work.pop()
        while True:
            if a in insns or a in out or not (lo <= a < hi) or not V[a - T0] or (STRICT and (tbl[a - T0] or pedat[a - T0])): break
            if not dec:
                if a != start and a in entries: refs.append(('tail', a)); break
                if dmap[a - T0]:
                    if a in fsize: refs.append(('tail', a))
                    else: midsrc.add((a, start))
                    break
                if cov[a - T0] == 2 and not r3: break
            n = 0; stop = False; o = a - T0
            for ins in md.disasm(text[o:min(o + 192, hi - T0 + (14 if dec else 0), TN)], a):   # an insn that starts inside a decomp range belongs to it in full
                a = ins.address; m = ins.mnemonic; mn = m.split()[-1]; nxt = a + ins.size; n += ins.size; ops = ins.operands
                if a >= hi: break
                if nxt > hi and dec and not r3: dmap[hi - T0:nxt - T0] = b'\1' * (nxt - hi); straddle.add(a)
                if STRICT and reloc_conflict(a, ins): dinfo['reloc_conflict'] += 1; stop = True; break         # decoding ran into relocated data
                feat = 0; tg = (); fall = True; px = ins.prefix
                if px[1] == 0x64: feat |= 4
                if px[0] == 0xf0: feat |= 16
                if px[0] in (0xf2, 0xf3) and m.startswith('rep') and ' ' in m: feat |= 32
                if mn[0] == 'f' or mn == 'wait': feat |= 64
                gs = ins.groups
                if SSEG.intersection(gs): feat |= 128
                if 143 in gs: feat |= 256
                feat |= FM.get(mn, 32768 if mn in EXO else 0)
                if mn in ('sysenter', 'syscall') or (mn == 'int' and ops[0].imm in (0x2e, 0x80)): feat |= 8
                if mn == 'jmp':
                    fall = False; op = ops[0]
                    if op.type == X.X86_OP_IMM: tg = (op.imm & 0xffffffff,)
                    elif not r3 and op.type == X.X86_OP_MEM and op.mem.scale == 4 and op.mem.index and (a + ins.disp_offset) in rset:
                        jtab.add(a); feat |= 2                                  # edges come from jt_pass (tools/jtab.py), never from a pattern at the site
                    else: feat |= 1 if op.type == X.X86_OP_REG else 2
                elif mn in ('ret', 'retf', 'int3', 'hlt', 'ud2', 'iret', 'iretd'): fall = False
                elif mn[0] == 'j' or mn in ('loop', 'loope', 'loopne'): tg = (ops[0].imm & 0xffffffff,)
                elif mn == 'call' and ops[0].type == X.X86_OP_IMM: refs.append(('call', ops[0].imm & 0xffffffff, a))
                out[a] = (ins.size, mn, feat, fall, tg)
                for t in tg:
                    if dec and not (lo <= t < hi): refs.append(('tail', t, start))
                    else: work.append(t)
                a = nxt
                if not fall: stop = True; break
                if a in insns or a in out or not (lo <= a < hi) or not V[a - T0] or (not dec and (a in entries or dmap[a - T0])): break
            else:
                if n >= 150 and a < hi: continue          # chunk boundary
                if a < hi: ok = False                    # undecodable bytes
                break
            if stop: break
    return ok, out, refs, tabs
def commit(res):
    ok, out, refs, tabs = res
    for a, v in out.items():
        insns[a] = v; o = a - T0; cov[o] = 1; cov[o + 1:o + v[0]] = b'\2' * (v[0] - 1)
    for t, n, rf in tabs:
        if intext(t): mark(t, n, rf)
    for r in refs:
        if r[0] == 'call': callers.setdefault(r[1], []).append(r[2])
        add_entry(r[1], r[0], r[2] if len(r) > 2 else None)
def tail_pass():
    """direct jmp/jcc targets (not table jumps) that leave the containing entry extent [entry, next entry) and land on an insn start become `tail` entries."""
    es = sorted(entries); n = 0
    for a in sorted(a for a, v in insns.items() if v[4] and not dmap[a - T0] and a not in jtab):
        o = es[bisect.bisect_right(es, a) - 1]
        for t in insns[a][4]:
            if intext(t) and t in insns and t not in entries and not dmap[t - T0] and es[bisect.bisect_right(es, t) - 1] != o: add_entry(t, 'tail', a); n += 1
    return n
jt_red = {}; jt_ok = {}; svals = sorted((u32(r), r) for r in relocs); vrefs = {}
for r, v in rvals: vrefs.setdefault(v, []).append(r)
def jt_pass():
    """jump-table edges (tools/jtab.py): every `jmp [T + r*4]` site is evaluated on the CURRENT decode; a site that resolves gets its table words as edges (descended, the words marked as table data), a site that does not is red with the reason."""
    pm = {}
    for a, (sz, mn, ft, fall, tg) in insns.items():
        for t in tg: pm.setdefault(t, set()).add(a)
        if fall: pm.setdefault(a + sz, set()).add(a)
    def ins_at(a): return next(md.disasm(text[a - T0:a - T0 + 15], a, 1), None) if T0 <= a < T1 else None
    def u8(a):
        if any(v <= a < v + z for v, z in M.wr): return None           # a byte table is read only from a read-only section (a writable one is not a fact)
        try: return u32(a) & 0xff
        except Exception: return None
    ex = JT.exclusive(jt_ok, svals)
    def seeded(x):
        if x in callers: return True
        if x in entries and (kinds[x] - {'ptr', 'tail', 'inner'}): return True
        return any(r not in ex for r in vrefs.get(x, ()))           # a relocated reference other than an exclusive table word
    n = 0
    for a in sorted(jtab):
        if a not in insns: continue
        tg, why = JT.resolve(a, insns, ins_at, lambda x: pm.get(x, ()), seeded, rset, u32, u8, intext, TN // 4)
        if tg is None: jt_red[a] = why; continue
        jt_red.pop(a, None)
        if jt_ok.get(a) == tg: continue
        jt_ok[a] = tg; sz, mn, ft, fall, old = insns[a]; insns[a] = (sz, mn, ft & ~2, False, tuple(tg)); n += 1
        lo = min(tg.addrs); hi = max(tg.addrs) + 4
        if intext(lo): mark(lo, hi - lo, a)
        tstarts.add(lo); tslots.update(tg.addrs)
        for t in tg:
            if t in insns: continue
            f = indec(t); res = walk(t, f[0], f[0] + f[1], True) if f else walk(t, T0, T1, False); commit(res)
            if not res[0]: bad_entries.add(t)
    return n
def drain():
    while queue:
        a = queue.popleft(); f = indec(a)
        res = walk(a, f[0], f[0] + f[1], True) if f else walk(a, T0, T1, False); commit(res)
        if not res[0]: bad_entries.add(a)
def site_kind(r):
    if not intext(r): return 'data', None
    o = r - T0
    if not cov[o]: return ('tbl' if tbl[o] else 'slot'), None
    s0 = o
    while cov[s0] != 1: s0 -= 1
    ins = next(md.disasm(text[s0:s0 + 15], T0 + s0, 1))
    if T0 + s0 + ins.disp_offset == r and ins.disp_size == 4: return 'disp', ins
    if T0 + s0 + ins.imm_offset == r and ins.imm_size == 4: return 'imm', ins
    return 'other', ins
def ptr_target(v, r):
    if v in tstarts or tbl[v - T0]: return 0
    psites.setdefault(v, []).append(r)
    if v in entries: kinds[v].add('ptr'); return 0
    if indec(v): add_entry(v, 'ptr', r); return 1
    if cov[v - T0] == 2: rejects[v] = ('mid-insn', r); return 0
    if cov[v - T0] == 1: entries[v] = None; kinds[v] = {'ptr'}; esite[v] = r; return 1
    res = walk(v, T0, T1, False)
    if not res[0]: rejects[v] = ('undecodable', r); return 0
    entries[v] = None; kinds[v] = {'ptr'}; esite[v] = r; commit(res); drain(); return 1
def disp_ref(r, v, ins, final):
    op = next(o for o in ins.operands if o.type == X.X86_OP_MEM and o.mem.disp & 0xffffffff == v)
    if cov[v - T0]: dinfo['disp_into_code'] += 1; return 1
    ctl = not STRICT or ins.mnemonic in ('call', 'jmp')               # module mode: a table loaded (not called) is data; its targets stay undetermined
    if ins.mnemonic == 'jmp' and op.mem.index and op.mem.scale == 4 and not op.mem.base:
        dinfo['disp_jtab'] += 1; return 1                                   # a table jump: its words are the edges jt_pass proves (tools/jtab.py), never a run read off the relocations
    if op.mem.index and op.mem.scale == 4:
        t = v; ent = []
        while t in rset and intext(u32(t)) and not cov[t - T0] and (t == v or t not in tstarts): ent.append(u32(t)); t += 4
        if ent:
            mark(v, 4 * len(ent), r); tstarts.add(v); dinfo['disp_tables'] += 1
            if ctl: tslots.update(v + 4 * k for k in range(len(ent)))
            for k, e in enumerate(ent):
                tcons.setdefault(v + 4 * k, ins.mnemonic)
                if ctl and cov[e - T0] != 2: ptr_target(e, v + 4 * k)
            return 1
    n = min(max(op.size, 1), T1 - v); mark(v, n, r); dinfo['disp_data'] += 1
    if (op.mem.index or op.mem.base) and not STRICT: arrays.append((v, n, r))
    return 1
cref = {}                                 # value -> relocated sites in .text that load it
for r in relocs:
    if intext(r): cref.setdefault(u32(r), []).append(r)
def ptr_pass(data, final, eh):
    new = 0
    for r, v in rvals:
        if r in settled or intext(r) == data: continue
        if data and r in eh.sites: settled.add(r); continue
        if data:
            g = codemap.dfmt(U, r, v, final)
            if g == 'wait': continue
            if g: settled.add(r); new += 1; continue
        k, ins = site_kind(r)
        if k == 'slot' and not final: continue
        if k == 'slot':
            dinfo['slot_table'] += 1; mark(r, 4, r)                                          # otherwise a relocated pointer slot in unreached bytes
        if k in ('data', 'imm', 'slot'): new += ptr_target(v, r); settled.add(r)
        elif k == 'tbl': settled.add(r)
        elif k == 'disp': disp_ref(r, v, ins, final); settled.add(r); new += 1
        else: dinfo['other_site'] += 1; rejects[v] = ('other-site', r); settled.add(r)
    return new
ONES = int.from_bytes(b'\1' * TN, 'little'); TAB = bytes([0] + [1] * 255)
def classify():
    """per-byte class: 1 insn of a decomp function, 2 insn of another entry, 3 relocation-named data, 8 PE-directory data, 7 hole; 0 = not (yet) explained."""
    D, M_, T, P, Vi = (int.from_bytes(bytes(x), 'little') for x in (dmap, bytes(cov).translate(TAB), tbl, pedat, V))
    dinfo['table_over_insn'] = bin(M_ & T).count('1')
    c = (D & M_) | ((M_ & (D ^ ONES)) << 1) | ((T & (M_ ^ ONES)) * 3) | ((P & (M_ ^ ONES) & (T ^ ONES)) * 8)
    return bytearray(((c & (Vi * 255)) | ((Vi ^ ONES) * 7)).to_bytes(TN, 'little'))
def padrun(x, e):
    """True if text[x:e] is entirely padding instructions."""
    while x < e:
        if text[x] in (0xCC, 0x90): x += 1; continue
        g = next(md.disasm(text[x:x + 15], T0 + x, 1), None)
        o = g.operands if g else ()
        if g and (g.mnemonic == 'nop' or (g.mnemonic == 'lea' and len(o) == 2 and o[1].mem.index == 0 and o[1].mem.base == o[0].reg and o[1].mem.disp == 0)
                  or (g.mnemonic == 'mov' and len(o) == 2 and o[0].type == o[1].type == X.X86_OP_REG and o[0].reg == o[1].reg)) and x + g.size <= e: x += g.size
        else: return False
    return True
def pad_split(cls):
    """mark padding (4) at gap edges; -> unexplained runs [(start, end)] (offsets)."""
    runs = []
    for m in re.finditer(rb'\x00+', bytes(cls)):
        s, e = m.span(); p = s
        while p < e:                                       # leading pad
            if text[p] in (0xCC, 0x90): p += 1; continue
            g = next(md.disasm(text[p:p + 15], T0 + p, 1), None)
            if g and g.size <= e - p and padrun(p, p + g.size) : p += g.size
            else: break
        q = e
        while q > p and text[q - 1] in (0xCC, 0x90): q -= 1
        for k in range(max(p, q - 15), q):                 # trailing multi-byte nop forms
            if padrun(k, q): q = k; break
        cls[s:p] = b'\4' * (p - s); cls[q:e] = b'\4' * (e - q)
        if p < q: runs.append((p, q))
    return runs
# ---- seeds and fixed point
for a, s in funcs: entries[a] = None; kinds[a] = {'decomp'}
if M.entry: add_entry(base + M.entry, 'entry')
tls = dirent(9)
if tls[0]:
    p = u32(base + tls[0] + 12)
    while intext(u32(p)): add_entry(u32(p), 'tls'); p += 4
XC, XFW, XN = pemod.exports(M)            # export directory: code VAs (by name and ordinal), forwarders are not code
for a in XC: add_entry(a, 'export')
for va, ln, lab in pemod.pe_ranges(M):
    if max(va, T0) < min(va + ln, T1): pedat[max(va, T0) - T0:min(va + ln, T1) - T0] = b'\1' * (min(va + ln, T1) - max(va, T0))
for a, s in funcs:
    res = walk(a, a, a + s, True); commit(res); dinfo['dec_bad'] += not res[0]
drain()
def extent_insns(a):
    f = indec(a); lo, hi = (f[0], f[0] + f[1]) if f else (T0, T1); seen = []; st = [a]; ss = set()
    while st:
        x = st.pop()
        if x in ss or x not in insns or not (lo <= x < hi) or (x != a and a not in fsize and (x in entries or (not f and dmap[x - T0]))): continue
        ss.add(x); seen.append(x); sz, mn, feat, fall, tg = insns[x]; st.extend(tg)
        if fall: st.append(x + sz)
    return seen
def isdata(a): return sec(a) is not None and not (intext(a) and cov[a - T0])      # an address that is not known code
X_ = SimpleNamespace(d=d, secs=secs, text=text, T0=T0, T1=T1, cov=cov, insns=insns, rset=rset, u32=u32, sec=sec, intext=intext, callers=callers, isdata=isdata,
                     jtab=jtab, jtab_red=jt_red, jtab_ok=jt_ok, indec=indec, emit=add_entry, relocs=relocs, extent_insns=extent_insns, rvals=rvals, fsize=fsize, mark=mark, imports=imports, outdir=OUTD, cstr=M.cstr, strict=STRICT)
def entry_of(a):
    f = indec(a)
    if f: return f[0]
    i = bisect.bisect_right(OWN, a) - 1; return OWN[i]
OWN = []
X_.entry_of = entry_of
eh = ehtables.EH(X_)
cont = {}                                 # strict mode: relocated-dword runs started by a code-referenced data address (vtable candidates)
for t in sorted(cref) if STRICT else ():
    q = t
    while q in rset: cont[q] = t; q += 4
U = SimpleNamespace(M=M, insns=insns, PRI=PRI, T0=T0, T1=T1, TN=TN, X=X, V=V, bad_entries=bad_entries, callers=callers, classify=classify, cov=cov, cref=cref, datarec=datarec, dmap=dmap,
                    entries=entries, fsize=fsize, funcs=funcs, indec=indec, intext=intext, kinds=kinds, md=md, pad_split=pad_split, parent=parent, psites=psites,
                    rejects=rejects, relocs=relocs, rset=rset, rvals=rvals, sec=sec, tcons=tcons, text=text, tref=tref, tslots=tslots, tbl=tbl, pedat=pedat, mark=mark,
                    dinfo=dinfo, settled=settled, site_kind=site_kind, disp_ref=disp_ref, ptr_target=ptr_target, deadops=deadops, u32=u32, imports=imports, cont=cont,
                    cstr=M.cstr, r3=r3set, vsites=vsites, vok=vok, tstarts=tstarts, eh_sites=eh.sites, outdir=OUTD)    # state shared with codemap (containers mutate in place)
fin = False
for rnd in range(30):
    OWN[:] = sorted(entries); n0 = (len(entries), len(insns), len(tslots), len(tstarts))
    ch = 0 if STRICT else ptr_pass(False, fin, eh); drain()
    OWN[:] = sorted(entries); ch += eh.scan(); drain()
    ch += codemap.strict_pass(U, fin, eh) if STRICT else ptr_pass(True, fin, eh); drain()
    ch += tail_pass(); drain()
    ch += jt_pass(); drain()
    if (len(entries), len(insns), len(tslots), len(tstarts)) == n0 and not ch:
        if fin: break
        fin = True
OWN[:] = sorted(entries)
for v, n, rf in arrays:           # an array read through an index register extends over the uncovered bytes that follow its first element
    e = v + n
    while e < T1 and not cov[e - T0] and not tbl[e - T0]: e += 1
    while e > v + n and text[e - T0 - 1] in (0xCC, 0x90): e -= 1
    if e > v + n: mark(v + n, e - v - n, rf); dinfo['array_ext_bytes'] += e - v - n
print('JTAB_AFTER sites=%d resolved=%d red=%d' % (len(jtab), len(jt_ok), len(jt_red)), flush=True)
for a in sorted(jt_red): print('JTAB_RED %08x %s' % (a, jt_red[a]))
NRN, NRI, NRB, NRR = codemap.exe_noreturn(U, eh.thrower)        # NORETURN: unreach what only flows after a call that never returns
# ---- extents / features / output
for v, ss in psites.items():
    if v in kinds and 'ptr' in kinds[v] and len(kinds[v]) > 1 and all(x in eh.sites for x in ss): kinds[v].discard('ptr')
# ---- R3: EVERY relocated value into executable bytes is an entry (kind ptr); no consumer rule excludes one. Whether the pointer is code or data is never decided:
# a data pointer's entry is simply never called. The body of an entry is the descent from its address; bytes that do not decode end a path (#UD at run time, flag
# `ud`), they are not a build error. Entries made here are not committed to insns/cov: their bodies are re-readings of bytes that byte accounting (codemap.py) has
# already classified exactly once, so accounting stays total; an address inside a decomp function gets parent= like every inner entry.
scx = {}; udset = set(); r3call = Counter(); r3site = {}
# R3 refined (residue.disp_only): a relocation that is only the displacement of a non-lea memory operand of committed flow is a data address, not a value; the value sites alone make entries
vals = [(r, v) for r, v in rvals if not residue.disp_only(md, text, T0, cov, r)]; U.vals = vals
print('R3 refinement: relocated in-span sites %d, displacement-only (not entry sites) %d, value sites %d' % (len(rvals), len(rvals) - len(vals), len(vals)))
for r, v in vals: r3site.setdefault(v, r)
for v in sorted(r3site):
    if v in entries: continue
    f = indec(v); entries[v] = None; kinds[v] = {'ptr'}; esite[v] = r3site[v]; r3set.add(v)
    if f: parent[v] = f[0]; kinds[v].add('inner')
    if cov[v - T0] == 1: continue                                   # an instruction start of already decoded code: its extent is that code's descent
    ok, out, refs, tabs = walk(v, f[0] if f else T0, f[0] + f[1] if f else T1, bool(f), True); scx[v] = out
    if not ok: udset.add(v)
    for rf in refs: r3call[rf[0], rf[1] in entries] += 1
def uni(I):
    """merged half-open byte ranges of a (possibly self-overlapping) instruction set."""
    o = []
    for a in sorted(I):
        e = a + I[a][0]
        if o and a <= o[-1][1]: o[-1][1] = max(o[-1][1], e)
        else: o.append([a, e])
    return o
# ---- closure under direct targets (rule C): the entry set is the least fixed point of R3 and of "every direct call target, and every direct jmp/jcc/loop target that leaves
# the lifter's range [lo,hi), of the body of every entry is an entry". The body and the range are the lifter's own (fnvlift.core.explore from the entry over the range
# entry_args gives: [parent, parent+size) for an entry with parent=, else [entry, entry+extent)); a call target is an entry even inside the range (the lifter emits
# L_<target>() for every direct call). A target inside a decomp function gets parent= (kind inner), like every inner entry. Added entries are R3-style entries: their bodies
# are kept in scx (not in insns/cov), so byte accounting is unchanged. A target outside every executable section is not an entry (counted: lifter needs a rule there).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fnvlift import Image as LImage; from fnvlift import core as LCore
limg = LImage(EXE); cl_cache = {}; cl_ft = set(); cl_out = Counter(); cl_outex = {}; cl_outt = {}; cl_outsrc = Counter(); cl_add = Counter(); cl_jx = set(); cl_rounds = 0
def span_of(a):
    """the universe.tsv extent of entry a: residue.span over the instructions it reaches (R3 bodies: scx, committed code: extent_insns)"""
    I = scx.get(a)
    return residue.span(a, ((x, I[x][0]) for x in I) if I is not None else ((x, insns[x][0]) for x in extent_insns(a)))
def cl_range(a):
    p = parent.get(a)
    if p is not None and p in entries: return p, p + fsize[p]
    return a, a + span_of(a)
def cl_targets(a, kids):
    lo, hi = cl_range(a); own = tuple(x for x in kids.get(lo, ()) if lo <= x < hi)
    key = (lo, hi, a, own)
    if key not in cl_cache:
        fh = hi; ins, tabs, tk, bad = LCore.explore(limg, lo, hi, limg.bytes(lo, hi - lo + 16), a, own if a == lo else (), fh); cs = set(); ts = set(); jx = set()
        for x, g in ins.items():
            m = g.mnemonic.split(' ')[-1]; o = g.operands      # the instruction under its prefix words (core.explore)
            if m == 'call' and o[0].type == X.X86_OP_IMM: cs.add(o[0].imm & 0xffffffff)
            elif (m == 'jmp' or (m[0] == 'j' and m != 'jecxz') or m in ('loop', 'loope', 'loopne')) and o[0].type == X.X86_OP_IMM:
                t = o[0].imm & 0xffffffff
                if not lo <= t < hi: ts.add(t)
            elif m == 'jecxz' and not lo <= (o[0].imm & 0xffffffff) < hi: jx.add(x); ts.add(o[0].imm & 0xffffffff)      # jecxz leaving the range: a direct jump out (f_sys, f_flow.jump_out)
            if m not in ('jmp', 'ret', 'retf', 'hlt', 'ud2') and not (m == 'int3' and x + g.size >= hi) and x + g.size not in ins and x + g.size not in bad:       # explore's own stop rule: a fall-out is a control transfer to the successor (f_flow.jump_out)
                ts.add(x + g.size); cl_ft.add((a, x + g.size))
        cl_cache[key] = (cs, ts, jx, residue.body_facts(ins, tabs, None, intext, imports), frozenset(ins))
    return cl_cache[key]
while True:
    kids = {}
    for x, q in parent.items(): kids.setdefault(q, []).append(x)
    miss = {}
    for a in sorted(entries):
        cs, ts, jx, _rf, _ia = cl_targets(a, kids); cl_jx |= {(a, x) for x in jx}
        for kd, T in (('call', cs), ('tail', ts)):
            for t in T:
                if t in entries: continue
                if not intext(t): cl_out[kd, sec(t)] += 1; cl_outex.setdefault((kd, sec(t)), t); cl_outt.setdefault(t, a); cl_outsrc['R3/closure body' if a in r3set else 'decomp' if a in fsize else 'other'] += 1; continue
                miss.setdefault(t, (set(), a))[0].add(kd)
    if not miss: break
    cl_rounds += 1
    for t in sorted(miss):
        kds, a = miss[t]; f = indec(t); entries[t] = None; kinds[t] = set(kds); esite[t] = a; r3set.add(t); cl_add['+'.join(sorted(kds)), 'inner' if f else 'free'] += 1
        if f: parent[t] = f[0]; kinds[t].add('inner')
        if cov[t - T0] == 1: continue
        ok, out, refs, tabs = walk(t, f[0] if f else T0, f[0] + f[1] if f else T1, bool(f), True); scx[t] = out
        if not ok: udset.add(t)
# ---- reloc-consistency, exe class (weak: an exe without DYNAMIC_BASE is never rebased): an entry that only a relocated pointer names (kinds == {ptr}, no flow reaches it, no decomp row) whose decoding has a relocation-misfit
# is refused (round input of the next run; the fixed point is reached by the driver below); a misfit in the decoding of any other entry is a red contradiction, never a refusal.
def misfit_addrs(xs):
    n = []
    for x in xs:
        g = next(md.disasm(text[x - T0:x - T0 + 15], x, 1), None)
        if g is not None and reloc_conflict(x, g): n.append(x)
    return n
REFN = {}; PMFX = {}
for a in sorted(entries):
    xs_ = sorted(scx[a]) if a in scx else extent_insns(a); m_ = misfit_addrs(xs_)
    if not m_: continue
    if a in fsize or a in parent or kinds[a] - {'ptr'}: PMFX[a] = m_
    else: REFN[a] = m_
open(OUTD + '/refused_next.tsv', 'w').write(''.join('%08x\t%d\treloc-misfit\n' % (a, len(m)) for a, m in sorted(REFN.items())))
print('reloc-consistency (exe, weak): refused-so-far %d, newly refusable pointer-only entries %d, entries other than pointer-only with a misfit instruction %d (%d instructions)' % (len(REFD), len(REFN), len(PMFX), sum(len(m) for m in PMFX.values())))
rows = []; rgs = []; rbad = rdec = eclo = 0; kc = Counter(); kany = Counter(); fe = Counter(); extsum = 0
for a in sorted(entries):
    pk = next(k for k in PRI if k in kinds[a]); isd = a in fsize; I = scx.get(a, insns); xs = sorted(I) if a in scx else extent_insns(a); ft = 0
    ex = sum(y - x for x, y in uni(I)) if a in scx else sum(insns[x][0] for x in xs)
    for x in xs: ft |= I[x][2]
    kc[pk] += 1; kany.update(kinds[a])
    if not isd and a not in parent: extsum += ex
    fts = [nm for i, nm in enumerate(FEATS) if ft >> i & 1]; fe.update((nm, isd) for nm in fts)
    fl = sorted(kinds[a] - {pk}) + fts + (['baddecode'] if a in bad_entries else []) + (['ud'] if a in udset else []) + (['parent=%08x' % parent[a]] if a in parent else [])
    rows.append('%08x\t%s\t%d\t%s' % (a, pk, fsize.get(a, span_of(a)), ','.join(fl) or '-'))
    if not isd and a not in scx: ss_ = set(xs); eclo += sum(1 for x in xs for t in insns[x][4] if intext(t) and t not in entries and t not in ss_)       # closure check (report only)
    rg = uni(I) if a in scx else codemap.ranges(xs, lambda x: insns[x][0]); rgs.append('%08x\t%s' % (a, codemap.rng_str(rg))); s_ = sum(y - x for x, y in rg); rbad += (s_ != ex) if not isd else (s_ > fsize[a] + 15); rdec += isd
kids = {}
for x, q in parent.items(): kids.setdefault(q, []).append(x)
RI = {}; LOX = {}; NRS = {a for a, _ in NRR}                                      # retn.tsv: how each entry returns (residue.retn_rows)
for a in sorted(entries):
    cs_, ts_, jx_, rf_, ia_ = cl_targets(a, kids); LOX[a] = len(ia_ - (set(scx[a]) if a in scx else set(extent_insns(a))))
    RI[a] = dict(rets=rf_['rets'], far=rf_['far'], open=rf_['open'], tails={t for t in ts_ if t in entries}, outside=any(t not in entries for t in ts_), noreturn=a in NRS)
os.makedirs(os.path.dirname(OUT), exist_ok=True); HD = '# base=0x%08x: absolute VAs at the preferred image base (not RVAs)' % base
open(OUT, 'w').write(HD + '; columns: entry, kind, extent, flags; extent is the SPAN [entry, entry + extent) that holds every instruction the entry reaches at or above it (the range the lifter takes; decomp rows: the declared function size; the bytes reached: ranges.tsv)\n' + '\n'.join(rows) + '\n')
open(OUTD + '/ranges.tsv', 'w').write(HD + '; columns: entry, half-open ranges start-end (hex) of the instruction bytes reached\n' + '\n'.join(rgs) + '\n')
rc = residue.write_retn(OUTD, HD, residue.retn_rows(RI)); print('retn.tsv %d rows: %s' % (len(RI), dict(sorted(rc.items()))))
print('invariant ranges: instructions the lifter reaches outside the entry ranges %d (entries %d of %d)' % (sum(LOX.values()), sum(1 for v in LOX.values() if v), len(LOX)))
open(OUTD + '/noreturn.tsv', 'w').write(HD + '; columns: entry, reason\n' + ''.join('%08x\t%s\n' % kv for kv in NRR))
print('R3 pointer entries: %d new (%d insn-start of decoded code, %d bodies re-read from their first byte, %d of them hit undecodable bytes #UD, %d inside decomp functions); direct targets of those bodies: %s' % (
    len(r3set), len(r3set) - len(scx), len(scx), len(udset), sum(1 for a in r3set if a in parent), dict(sorted(r3call.items()))))
print('closure check (old, report only): direct jmp/jcc targets of non-decomp entries inside the span that are neither entries nor in the entry\'s own extent: %d' % eclo)
print('closure under direct targets: %d rounds, entries added %s; targets outside executable sections (not entries) %d refs / %d distinct targets, by source entry %s, by kind and section %s; first sources %s; jecxz leaving range %d' % (
    cl_rounds, dict(sorted(cl_add.items())), sum(cl_out.values()), len(cl_outt), dict(cl_outsrc), {'%s %s' % k: '%d e.g. %08x' % (v, cl_outex[k]) for k, v in sorted(cl_out.items(), key=str)}, ' '.join('%08x<-%08x' % (t, a) for t, a in sorted(cl_outt.items())[:4]), len(cl_jx)))
print('fall-through successors of lifter bodies (entry, successor) %d, distinct successors %d' % (len(cl_ft), len({t for e, t in cl_ft})))
print('ranges.tsv %d rows, noreturn.tsv %d rows; range-sum vs extent mismatches %d (non-decomp rows: equality; %d decomp rows: sum <= declared size)' % (len(rgs), len(NRR), rbad, rdec))
def gate(green, TNu, why):
    import pcsrc; pc_red, pc_res = pcsrc.run_facts(OUTD, NAME if STRICT else 'exe', text, T0, u32, rset); print('gate %s red=%d sources=%d rows=%d' % (pc_res['name'], pc_red, pc_res['sources'], pc_res['rows']))
    open(OUTD + '/gate_codemap.json', 'w').write(json.dumps({'schema': 'fnvnx-gate/1', 'name': 'l3.codemap' + ('.' + NAME if STRICT else ''), 'status': 'green' if green else 'red', 'universe': TNu, 'certified': TNu - unexpl, 'uncertified': unexpl}) + '\n')
    print('gate l3.codemap%s: %s (certified %d of %d, uncertified %d, rejected pointers %d, bad-decode entries %d)%s' % ('.' + NAME if STRICT else '', 'green' if green else 'red', TNu - unexpl, TNu, unexpl, len(rejects), len(bad_entries), ''.join('; RED: ' + w for w in why)))
    bad = residue.entries_on_data(OUTD); print('invariant entries on class-3 data bytes: %d%s' % (len(bad), ' RED: ' + ' '.join('%08x' % a for a in bad[:8]) if bad else ''))
    sys.exit(0 if green and not bad else 1)
CL, unexpl, cls = codemap.finish(U); cc = Counter(cls)
if not STRICT: print('noreturn: %d entries never return (seeds: thrower %s); %d instructions / %d bytes (decomp %d, non-decomp %d) only flowed after such calls and are no longer reached; entry set unchanged (%d)' % (NRN, 'yes' if eh.thrower in entries else 'no', NRI, NRB[0], NRB[1], NRB[0] - NRB[1], len(entries)))
if not STRICT:
    midf = len({s for t, s in midsrc if s is not None and indec(s)}); nonde = [a for a in entries if a not in fsize]
    exo = sorted(a for a, v in insns.items() if v[1] in EXOTIC)
    print('\n'.join(eh.finish(entries)))
    print('text %d bytes (%08x-%08x), decomp funcs %d, relocs %d; mid-decomp jumps %d, calls %d' % (TN, T0, T1, len(funcs), len(relocs), midf, len(midcall)))
    print('\n'.join(CL))
    print('entries %d; per primary kind: %s' % (len(entries), dict(kc)))
    print('entries carrying kind (any): %s' % {k: kany[k] for k in PRI if k in kany})
    print('non-decomp entries %d (inner %d); sum of extents %d; unique non-decomp code bytes %d; bad-decode entries %d' % (len(nonde), len(parent), extsum, cc[2], len(bad_entries)))
    print('use sites: %s; insns straddling a decomp size %s; rejected pointers %s' % (dict(sorted(dinfo.items())), ' '.join('%x' % a for a in sorted(straddle)) or 0, dict(sorted(Counter(k for k, r in rejects.values()).items()))))
    print('entries with feature (decomp/non): ' + ' '.join('%s=%d/%d' % (nm, fe[nm, True], fe[nm, False]) for nm in FEATS if fe[nm, True] or fe[nm, False]))
    print('exotic mnemonics in reached code: %s' % (dict(sorted(Counter(insns[a][1] for a in exo).items())) or 0))
    for x in exo[:6]:
        e = next((e for e in reversed(OWN[max(0, bisect.bisect_right(OWN, x) - 40):bisect.bisect_right(OWN, x)]) if x in set(extent_insns(e))), None)
        print('  %08x %s in entry %s kind %s via site@%s' % (x, insns[x][1], '%08x' % e if e else '-', next((k for k in PRI if e and k in kinds[e]), '-'), '%08x' % esite[e] if e in esite and esite[e] else '-'))
    print('\n'.join(codemap.consumers(U, eh)))
    print('\n'.join(reconcile.run(U)))
    pts = sorted({v for r, v in rvals}); ne = [v for v in pts if v not in entries]; dfr = [(x[1], x[1] + x[3]) for x in datarec]
    print('relocated pointer targets in .text: %d, entries %d, NOT entries %d: %s' % (len(pts), len(pts) - len(ne), len(ne), dict(sorted(Counter(
        'didataformat-blob' if any(x <= v < y for x, y in dfr) else ('data-by-reloc@object-start' if not tbl[v - T0 - 1] else 'data-by-reloc@object-interior') if tbl[v - T0] else 'inside-other-insn' if cov[v - T0] == 2 else 'insn-start-not-entry' if cov[v - T0] == 1 else 'unreached' for v in ne).items()))))
    if '--mnemonics' in ARGV: open(OUTD + '/exe_mnemonics.tsv', 'w').write(''.join('%s\t%d\n' % kv for kv in sorted(Counter(v[1] for v in insns.values()).items())))
    gate(not unexpl and not bad_entries and not rejects and not rbad and not sum(LOX.values()) and not PMFX, TN, [])
# ---- module report
midf = 0; why = []; TNu = TN - cc[7]; exs = [n for n, a, z, ro in M.xs]
PL, nc = codemap.ptrclass(U)
if RL is None:
    ab = 0
    for a in sorted(insns):
        g = next(md.disasm(text[a - T0:a - T0 + 15], a, 1))
        ab += sum(1 for sz, o in ((g.disp_size, g.disp_offset), (g.imm_size, g.imm_offset)) if sz == 4 and base <= int.from_bytes(text[a - T0 + o:a - T0 + o + 4], 'little') < base + M.sizeimg)
    why.append('no .reloc: pointers cannot be classified by relocation (%d absolute-looking immediates/displacements in reached code)' % ab)
if eh.unknown: why.append('%d frame-handler stubs reference FuncInfo with an unknown magic/version' % eh.unknown)
if dinfo['table_over_insn']: why.append('%d bytes are both reached as instructions and named as data' % dinfo['table_over_insn'])
if rejects: why.append('%d rejected pointers (%s)' % (len(rejects), ', '.join(sorted({k for k, r in rejects.values()}))))
if bad_entries: why.append('%d bad-decode entries' % len(bad_entries))
if nc: why.append('%d undetermined pointer targets' % nc)
if unexpl: why.append('%d unexplained bytes' % unexpl)
print('%s: sections %d (executable %d: %s), reloc %s (%d fixups), imports %d, exports code %d forwarders %d (ordinal base %d, funcs %d, names %d), entry rva %x' % (
    os.path.basename(EXE), len(secs), len(exs), ' '.join(exs[:4]) + (' ...' if len(exs) > 4 else ''), 'yes' if RL is not None else 'NO', len(relocs), len(imports), len(XC), XFW, *XN, M.entry))
print('\n'.join(eh.finish(entries)[:3] + CL)); print('entries %d: %s; use sites %s' % (len(entries), dict(sorted(kc.items())), dict(sorted(dinfo.items()))))
print('\n'.join(PL)); gate(not why and not bad_entries and not rejects, TNu, why)
