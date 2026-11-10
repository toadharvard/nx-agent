#!/usr/bin/env python3
"""Code universe of FalloutNV.exe: recursive descent from decomp starts, entry point, call/tail targets, reloc'd
pointers; jump tables; byte classification of .text. Output: /home/user/l3facts/universe.tsv + count-only stdout."""
import os, re, sys, struct, bisect, capstone
from collections import Counter, deque
from capstone import x86 as X
EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
FUNCS = os.environ.get('FNVNX_FUNCS', '/home/user/l3facts/funcs.tsv')
OUT = '/home/user/l3facts/universe.tsv'
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
d = open(EXE, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]; nsec = struct.unpack_from('<H', d, pe + 6)[0]
base = struct.unpack_from('<I', d, pe + 52)[0]; secs = []; o = pe + 24 + struct.unpack_from('<H', d, pe + 20)[0]
for i in range(nsec):
    nm = d[o:o + 8].rstrip(b'\0').decode(); vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8); secs.append((nm, base + va, vs, ro, rs)); o += 40
def off(a):
    for nm, va, vs, ro, rs in secs:
        if va <= a < va + vs and a - va < rs: return ro + a - va
def u32(a):
    x = off(a); return struct.unpack_from('<I', d, x)[0] if x is not None else 0
def dirent(i): return struct.unpack_from('<II', d, pe + 24 + 96 + 8 * i)
rr, rsz = dirent(5); p = off(base + rr); pend = p + rsz; relocs = []
while p < pend:
    page, bs = struct.unpack_from('<II', d, p)
    if not bs: break
    for i in range((bs - 8) // 2):
        e = struct.unpack_from('<H', d, p + 8 + 2 * i)[0]
        if e >> 12 == 3: relocs.append(base + page + (e & 0xfff))
    p += bs
rset = set(relocs); relocs.sort()
T0, TV, TRO, TRS = next((va, vs, ro, rs) for nm, va, vs, ro, rs in secs if nm == '.text')
TN = min(TV, TRS); T1 = T0 + TN; text = d[TRO:TRO + TN]
def intext(a): return T0 <= a < T1
rvals = [(r, u32(r)) for r in relocs]; rvals = [(r, v) for r, v in rvals if intext(v)]
funcs = []
for ln in open(FUNCS):
    if ln.strip() and not ln.startswith('#'):
        a, s = ln.split()[:2]; a = int(a, 16); s = int(s, 16) if s.startswith('0x') else int(s)
        if intext(a): funcs.append((a, min(s, T1 - a)))
funcs.sort(); fstarts = [a for a, s in funcs]; fsize = dict(funcs)
dmap = bytearray(TN)                      # 1 = inside decomp range
for a, s in funcs: dmap[a - T0:a - T0 + s] = b'\1' * s
cov = bytearray(TN)                       # 1 = insn start, 2 = insn interior
tbl = bytearray(TN)                       # 1 = jump/index table data
insns = {}                                # addr -> (size, mnem, feat, fall, targets)
entries = {}; kinds = {}; PRI = ['decomp', 'entry', 'tls', 'export', 'call', 'tail', 'ptr', 'orphan']
tslots = set(); tstarts = set(); dinfo = Counter(); midsrc = set(); midcall = set(); midptr = set()
dataintext = set(); bad_entries = set(); queue = deque()
FEATS = ['ijmp_reg', 'ijmp_mem', 'fs', 'sys', 'lock', 'rep', 'x87', 'sse', 'mmx', 'in', 'out', 'cli', 'sti', 'rdtsc', 'cpuid', 'exotic']
EXO = {'hlt', 'rdmsr', 'wrmsr', 'lgdt', 'lidt', 'sgdt', 'sidt', 'ltr', 'str', 'invd', 'wbinvd', 'lmsw', 'smsw', 'clts', 'rdpmc', 'invlpg', 'ud2', 'iret', 'iretd', 'syscall', 'sysret', 'sysexit', 'bound', 'arpl', 'into', 'sldt', 'lldt', 'verr', 'verw'}
SSEG = {148, 149, 150, 151, 152, 153, 154, 132, 133, 134}

def indec(a):
    i = bisect.bisect_right(fstarts, a) - 1
    return funcs[i] if i >= 0 and a < funcs[i][0] + funcs[i][1] else None

def add_entry(a, kind, src=None):
    if not intext(a): return
    f = indec(a)
    if f and f[0] != a:
        if kind == 'call': midcall.add(a)
        elif kind == 'tail': midsrc.add((a, src))
        else: midptr.add(a)
        return
    if not f and a not in entries and cov[a - T0] == 2: dinfo['overlap_' + kind] += 1; return
    if a not in entries: entries[a] = None; kinds[a] = set(); queue.append(a)
    kinds[a].add(kind)

def walk(start, lo, hi, dec):
    """descent from start within [lo,hi) -> (ok, new insns, refs, tables)."""
    out = {}; refs = []; tabs = []; ok = True; work = [start]
    while work:
        a = work.pop(); trail = []
        while True:
            if a in insns or a in out or not (lo <= a < hi): break
            if not dec:
                if a != start and a in entries: refs.append(('tail', a)); break
                if dmap[a - T0]:
                    if a in fsize: refs.append(('tail', a))
                    else: midsrc.add((a, start))
                    break
                if cov[a - T0] == 2: break
            n = 0; stop = False; o = a - T0
            for ins in md.disasm(text[o:min(o + 192, hi - T0)], a):
                a = ins.address; m = ins.mnemonic; mn = m.split()[-1]; nxt = a + ins.size; n += ins.size; ops = ins.operands
                feat = 0; tg = (); fall = True; px = ins.prefix
                if px[1] == 0x64: feat |= 4
                if px[0] == 0xf0: feat |= 16
                if px[0] in (0xf2, 0xf3) and m.startswith('rep') and ' ' in m: feat |= 32
                if mn[0] == 'f' or mn == 'wait': feat |= 64
                gs = ins.groups
                if SSEG.intersection(gs): feat |= 128
                if 143 in gs: feat |= 256
                if mn in ('in', 'insb', 'insw', 'insd'): feat |= 512
                elif mn in ('out', 'outsb', 'outsw', 'outsd'): feat |= 1024
                elif mn == 'cli': feat |= 2048
                elif mn == 'sti': feat |= 4096
                elif mn == 'rdtsc': feat |= 8192
                elif mn == 'cpuid': feat |= 16384
                elif mn in EXO: feat |= 32768
                if mn in ('sysenter', 'syscall') or (mn == 'int' and ops[0].imm in (0x2e, 0x80)): feat |= 8
                if mn == 'jmp':
                    fall = False; op = ops[0]
                    if op.type == X.X86_OP_IMM: tg = (op.imm & 0xffffffff,)
                    elif op.type == X.X86_OP_MEM and op.mem.scale == 4 and op.mem.index and (a + ins.disp_offset) in rset:
                        tg = table(ins, trail, lo, hi, dec, tabs)
                        if not tg: feat |= 2
                    else: feat |= 1 if op.type == X.X86_OP_REG else 2
                elif mn in ('ret', 'retf', 'int3', 'hlt', 'ud2', 'iret', 'iretd'): fall = False
                elif mn[0] == 'j' or mn in ('loop', 'loope', 'loopne'): tg = (ops[0].imm & 0xffffffff,)
                elif mn == 'call' and ops[0].type == X.X86_OP_IMM: refs.append(('call', ops[0].imm & 0xffffffff))
                out[a] = (ins.size, mn, feat, fall, tg)
                for t in tg:
                    if dec and not (lo <= t < hi): refs.append(('tail', t, start))
                    else: work.append(t)
                trail.append(ins); trail = trail[-8:]
                a = nxt
                if not fall: stop = True; break
                if a in insns or a in out or not (lo <= a < hi) or (not dec and (a in entries or dmap[a - T0])): break
            else:
                if n >= 150 and a < hi: continue          # chunk boundary
                if a < hi: ok = False                    # undecodable bytes
                break
            if stop: break
    return ok, out, refs, tabs

def table(ins, trail, lo, hi, dec, tabs):
    a = ins.address; T = ins.operands[0].mem.disp & 0xffffffff; D = None; nidx = 0
    ok = lambda v: (lo <= v < hi) if dec else intext(v)
    for p in reversed(trail):
        o = p.operands
        if D is None and p.mnemonic == 'movzx' and len(o) == 2 and o[1].type == X.X86_OP_MEM and o[1].size == 1 and (p.address + p.disp_offset) in rset: D = o[1].mem.disp & 0xffffffff
        if not nidx and p.mnemonic == 'cmp' and len(o) == 2 and o[1].type == X.X86_OP_IMM and 0 <= o[1].imm < 256: nidx = o[1].imm + 1
    ent = []
    if D is not None:
        if not nidx and D < T and T - D <= 256: nidx = T - D
        x = off(D); idx = d[x:x + nidx] if x is not None else b''
        if not idx: return ()
        for k in range(max(idx) + 1):
            v = u32(T + 4 * k)
            if (T + 4 * k) not in rset or not ok(v): return ()
            ent.append(v)
        tabs.append((D, nidx)); tstarts.add(D)
    else:
        t = T
        while t in rset and ok(u32(t)) and not (intext(t) and cov[t - T0]):
            ent.append(u32(t)); t += 4
        if not ent: return ()
    tabs.append((T, 4 * len(ent))); tstarts.add(T); tslots.update(T + 4 * k for k in range(len(ent)))
    return tuple(ent)

def commit(res):
    ok, out, refs, tabs = res
    for a, v in out.items():
        insns[a] = v; o = a - T0; cov[o] = 1; cov[o + 1:o + v[0]] = b'\2' * (v[0] - 1)
    for t, n in tabs:
        if intext(t): n = min(n, T1 - t); tbl[t - T0:t - T0 + n] = b'\1' * n
    for r in refs: add_entry(r[1], r[0], r[2] if len(r) > 2 else None)

def drain():
    while queue:
        a = queue.popleft(); res = walk(a, T0, T1, False); commit(res)
        if not res[0]: bad_entries.add(a)

def ptr_pass():
    new = 0
    for r, v in rvals:
        if r in tslots or v in tstarts or v in dataintext: continue
        if v in entries: kinds[v].add('ptr'); continue
        if tbl[v - T0]: continue
        f = indec(v)
        if f: midptr.add(v); continue
        if cov[v - T0] == 2: dataintext.add(v); continue
        if cov[v - T0] == 1: entries[v] = None; kinds[v] = {'ptr'}; new += 1; continue
        res = walk(v, T0, T1, False)
        if not res[0]: dataintext.add(v); continue
        entries[v] = None; kinds[v] = {'ptr'}; commit(res); new += 1; drain()
    return new

def classify():
    cls = bytearray(dmap)
    for a, v in insns.items():
        if not dmap[a - T0]: cls[a - T0:a - T0 + v[0]] = b'\2' * v[0]
    for m in re.finditer(rb'\x01+', bytes(tbl)): cls[m.start():m.end()] = b'\3' * (m.end() - m.start())
    return cls

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

def orphan_pass(runs):
    n = 0
    for s, e in runs:
        tot = 0; last = None
        for ins in md.disasm(text[s:e], T0 + s): tot += ins.size; last = ins
        if tot == e - s and last.mnemonic in ('ret', 'retf', 'jmp') and (T0 + s) not in entries:
            res = walk(T0 + s, T0, T1, False)
            if res[1]: entries[T0 + s] = None; kinds[T0 + s] = {'orphan'}; commit(res); n += 1
    return n

# ---- seeds and fixed point
for a, s in funcs: entries[a] = None; kinds[a] = {'decomp'}
add_entry(base + struct.unpack_from('<I', d, pe + 40)[0], 'entry')
tls = dirent(9)
if tls[0]:
    p = u32(base + tls[0] + 12)
    while intext(u32(p)): add_entry(u32(p), 'tls'); p += 4
ex = dirent(0)
if ex[0]:
    ea = base + ex[0]; nf = u32(ea + 20); fa = u32(ea + 28)
    for i in range(nf): add_entry(u32(base + fa + 4 * i) + base, 'export')
for a, s in funcs:
    res = walk(a, a, a + s, True); commit(res); dinfo['dec_bad'] += not res[0]
drain()
for rnd in range(10):
    ch = ptr_pass(); cls = classify(); n = orphan_pass(pad_split(cls)); drain()
    if not ch and not n: break
cls = classify(); runs = pad_split(cls)

# ---- extents / features / output
def extent(a):
    f = fsize.get(a); lo, hi = (a, a + f) if f else (T0, T1); seen = set(); st = [a]; tot = 0; ft = 0
    while st:
        x = st.pop()
        if x in seen or x not in insns or not (lo <= x < hi): continue
        if not f and x != a and (x in entries or dmap[x - T0]): continue
        seen.add(x); sz, mn, feat, fall, tg = insns[x]; tot += sz; ft |= feat; st.extend(tg)
        if fall: st.append(x + sz)
    return tot, ft
rows = []; kc = Counter(); fe = {True: Counter(), False: Counter()}; extsum = 0
for a in sorted(entries):
    pk = next(k for k in PRI if k in kinds[a]); isd = a in fsize; ex, ft = extent(a); kc[pk] += 1
    if not isd: extsum += ex
    for i, nm in enumerate(FEATS):
        if ft >> i & 1: fe[isd][nm] += 1
    fl = sorted(kinds[a] - {pk}) + [nm for i, nm in enumerate(FEATS) if ft >> i & 1] + (['baddecode'] if a in bad_entries else [])
    rows.append('%08x\t%s\t%d\t%s' % (a, pk, fsize.get(a, ex), ','.join(fl) or '-'))
os.makedirs(os.path.dirname(OUT), exist_ok=True); open(OUT, 'w').write('\n'.join(rows) + '\n')
uf = ub = upf = upb = 0
for a, s in funcs:
    z = [i for i in range(a - T0, a - T0 + s) if not cov[i] and not tbl[i]]
    if z:
        uf += 1; ub += len(z)
        if all(text[i] in (0xCC, 0x90) for i in z): upf += 1; upb += len(z)
midf = len({s for t, s in midsrc if s is not None and indec(s)})
dc = Counter(); nc = Counter(); fi = {True: Counter(), False: Counter()}
for a, v in insns.items():
    isd = bool(dmap[a - T0]); (dc if isd else nc)[v[1]] += 1
    for i, nm in enumerate(FEATS):
        if v[2] >> i & 1: fi[isd][nm] += 1
cc = Counter(cls); hist = Counter()
for s, e in runs: hist[next(b for b in (1, 2, 4, 8, 16, 32, 64, 256, 1024, 1 << 30) if e - s <= b)] += 1
nonde = [a for a in entries if a not in fsize]
print('text %d bytes (%08x-%08x), decomp funcs %d, HIGHLOW relocs %d' % (TN, T0, T1, len(funcs), len(relocs)))
print('bytes: decomp %d, nondecomp-code %d, table %d, padding %d, unexplained %d' % (cc[1], cc[2], cc[3], cc[4], cc[0]))
print('unexplained after orphan pass: %d runs, %d bytes; size hist (<=N): %s' % (len(runs), sum(e - s for s, e in runs), dict(sorted(hist.items()))))
print('entries %d; per primary kind: %s' % (len(entries), dict(kc)))
print('non-decomp entries %d; sum of extents %d; unique non-decomp code bytes %d; entries with bad decode %d' % (len(nonde), extsum, cc[2], len(bad_entries)))
print('data-in-text ptrs %d; ptrs into middle of decomp %d; rejected (mid-insn) %s; decomp walks with bad decode %d' % (len(dataintext), len(midptr), {k: v for k, v in dinfo.items() if k != 'dec_bad'}, dinfo['dec_bad']))
print('decomp unreached (excl tables): %d funcs %d bytes (cc/90-only: %d funcs %d bytes)' % (uf, ub, upf, upb))
print('decomp funcs jumping into middle of another decomp: %d; call targets mid-decomp: %d' % (midf, len(midcall)))
print('top40 mnemonics non-decomp: ' + ' '.join('%s=%d' % kv for kv in nc.most_common(40)))
print('only in non-decomp: ' + (' '.join('%s=%d' % (k, v) for k, v in sorted(nc.items()) if k not in dc) or '-'))
for nm in FEATS: print('%-9s entries decomp/non %d/%d insns decomp/non %d/%d' % (nm, fe[True][nm], fe[False][nm], fi[True][nm], fi[False][nm]))
