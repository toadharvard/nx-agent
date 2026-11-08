"""topdown.py (R2, owner order 2026-11-08) GUEST.elf CAUSES.tsv TRAPNAMES.txt HITS.tsv OUT.tsv : rank every trap stub by reachability from
__fnvnx_guest_entry in the guest's static reference graph (code: bl/b imm26 and adrp+add/ldr; data: .rela.dyn
RELATIVE pointers inside data objects). Rank: trap hits in stub boots first (most hits first), then BFS depth,
then BFS discovery order (call order in each function's code). Unreachable traps last (depth -1)."""
import bisect, collections, struct, subprocess, sys
elf, causes, trapf, hitsf, out = sys.argv[1:6]
d = open(elf, 'rb').read()
shoff, = struct.unpack_from('<I', d, 0x20); shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x2e)
secs = []
for i in range(shnum):
    secs.append(struct.unpack_from('<IIIIIIIIII', d, shoff + i * shentsize))
strtab = secs[shstrndx]
def sname(s):
    o = strtab[4] + s[0]; return d[o:d.index(b'\0', o)].decode()
S = {sname(s): s for s in secs}
# symbols with sizes
syms = []
for line in subprocess.run(['aarch64-linux-gnu-nm', '-S', '--defined-only', elf], capture_output=True, text=True).stdout.splitlines():
    p = line.split(' ', 3)
    if len(p) == 4 and p[2] in 'TtWwDdRrBbVv':
        a, z = int(p[0], 16), int(p[1], 16)
        if z: syms.append((a, z, p[3], p[2] in 'TtWw'))
    elif len(p) == 3 and p[1] in 'TtWw':
        syms.append((int(p[0], 16), 4, p[2], True))
syms.sort()
starts = [s[0] for s in syms]
first = {}
for a, z, n, f in syms: first.setdefault(a, n)
def owner(a):
    i = bisect.bisect_right(starts, a) - 1
    while i >= 0 and syms[i][0] + syms[i][1] <= a:
        i -= 1
        if i >= 0 and a - syms[i][0] > 0x100000: return None
    return first[syms[i][0]] if i >= 0 and a < syms[i][0] + syms[i][1] else None
edges = collections.defaultdict(list)
_r = S['.rela.dyn']; slot = {}
for _k in range(_r[5] // 12):
    _o, _i, _a = struct.unpack_from('<IIi', d, _r[4] + 12 * _k)
    if _i & 0xff: slot[_o] = _a & 0xffffffff
def _word(va):
    for _s in secs:
        if _s[1] != 8 and _s[3] and _s[3] <= va < _s[3] + _s[5]: return struct.unpack_from('<I', d, _s[4] + va - _s[3])[0]
_rr = S.get('.relr.dyn'); _where = 0
if _rr:
    for _k in range(_rr[5] // 4):
        e, = struct.unpack_from('<I', d, _rr[4] + 4 * _k)
        if e & 1 == 0:
            _where = e; slot[_where] = _word(_where); _where += 4
        else:
            b = e >> 1
            for j in range(31):
                if b >> j & 1:
                    va = _where + 4 * j; slot[va] = _word(va)
            _where += 31 * 4
slot = {k: v for k, v in slot.items() if v is not None}
def add(src, dst):
    if dst and dst != src: edges[src].append(dst)
# code scan
t = S['.text']; base, off, size = t[3], t[4], t[5]
words = struct.unpack_from('<%dI' % (size // 4), d, off)
cur = None; ci = 0
fn_at = {s[0]: first[s[0]] for s in syms if s[3]}
fstarts = sorted(fn_at)
fn_at_names = {n: 1 for n in fn_at.values()}
adrp = {}
for i, w in enumerate(words):
    a = base + 4 * i
    if a in fn_at: cur = fn_at[a]; adrp = {}
    if cur is None: continue
    top = w & 0xfc000000
    _rd = None if (w & 0x9f000000) == 0x90000000 else w & 31
    if top in (0x94000000, 0x14000000):
        imm = w & 0x3ffffff
        if imm & 0x2000000: imm -= 0x4000000
        tgt = a + imm * 4
        if top == 0x94000000 or tgt in fn_at: add(cur, fn_at.get(tgt) or owner(tgt))
    elif (w & 0x9f000000) == 0x90000000:
        rd = w & 31; imm = ((w >> 29) & 3) | (((w >> 5) & 0x7ffff) << 2)
        if imm & 0x100000: imm -= 0x200000
        adrp[rd] = (a & ~0xfff) + (imm << 12)
    elif (w & 0xff800000) == 0x11000000:   # add imm (32-bit, ILP32)
        rn = (w >> 5) & 31
        if rn in adrp: add(cur, owner(adrp[rn] + ((w >> 10) & 0xfff) * (4096 if (w >> 22) & 1 else 1)))
    elif (w & 0xff000000) == 0x91000000:   # add imm 64-bit
        rn = (w >> 5) & 31
        if rn in adrp: add(cur, owner(adrp[rn] + ((w >> 10) & 0xfff)))
    elif (w & 0x3b000000) == 0x39000000 and ((w >> 22) & 3) == 1:   # ldr unsigned imm
        rn = (w >> 5) & 31; sz = w >> 30
        if rn in adrp:
            ta = adrp[rn] + ((w >> 10) & 0xfff) * (1 << sz)
            add(cur, owner(slot[ta]) if ta in slot and owner(ta) is None else owner(ta))
    if top == 0x94000000: adrp.clear()
    elif _rd is not None and (w & 0x3b000000) != 0x39000000 or (_rd is not None and (w >> 22) & 1):
        adrp.pop(_rd, None)   # rd written (stores, ldr/str unsigned with L=0, keep the base)
# data pointers
for ro, tv in sorted(slot.items()):
    src = owner(ro); dst = owner(tv)
    if src and dst: add(src, dst)
print('pointer slots', len(slot), file=sys.stderr)
ia = S.get('.init_array')   # its entries are roots too (edges from the entry, below)
traps = set(x.strip() for x in open(trapf) if x.strip())
crow = {}
for line in open(causes, errors='surrogateescape'):
    if line.startswith('#'): continue
    c = line.rstrip('\n').split('\t'); crow.setdefault(c[0], c)
hits = collections.Counter()
try:
    for line in open(hitsf):
        p = line.rstrip('\n').split('\t')
        if len(p) >= 2 and p[0]: hits[p[0]] += int(p[1])
except FileNotFoundError: pass
big = sorted(((len(set(v)), u) for u, v in edges.items()), reverse=True)[:25]
print('largest out-degree:', ', '.join('%s %d' % (u, n) for n, u in big), file=sys.stderr)
HUB = 1500   # registries (address maps, code-pointer tables) list everything: not a call path
for n_, u_ in big:
    if n_ > HUB and not fn_at_names.get(u_): edges[u_] = []
root = '__fnvnx_guest_entry'
if ia:
    for k in range(ia[5] // 4):
        f_ = owner(slot.get(ia[3] + 4 * k) or 0)
        if f_: add(root, f_)
depth = {root: 0}; order = {root: 0}; q = collections.deque([root]); parent = {}
while q:
    u = q.popleft()
    if u in traps: continue   # a trap stub calls nothing real
    for v in edges.get(u, ()):
        if v not in depth:
            depth[v] = depth[u] + 1; order[v] = len(order); parent[v] = u; q.append(v)
def path(n):
    p = []
    while n in parent and len(p) < 6: n = parent[n]; p.append(n)
    return ' < '.join(p)
indeg = collections.Counter(v for u in edges for v in set(edges[u]))
rows = []
for n in traps:
    dep = depth.get(n, -1)
    rows.append((-hits[n], 0 if dep >= 0 else 1, dep if dep >= 0 else 0, order.get(n, 0) if dep >= 0 else -indeg[n], n))
rows.sort()
with open(out, 'w') as f:
    f.write('#rank\tdepth\thits\tname\towner\tcause\tdetail\tdemangled\thome unit\tvia (BFS parents)\n')
    for i, (_, _, _, _, n) in enumerate(rows, 1):
        c = crow.get(n, [n, '', '?', '', '', '', '', '?', '', ''])
        f.write('%d\t%d\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' % (i, depth.get(n, -1), hits[n], n, c[7], c[2], c[3], c[1], c[9] if len(c) > 9 else '', path(n) if n in depth else 'unreached; referenced by %d' % indeg[n]))
    for k, v in hits.most_common():
        if k not in traps:
            f.write('# hit in stub boots (%d), not a trap stub in this link (closed since, or a vtable-slot trap): %s\n' % (v, k))
reach = sum(1 for n in traps if n in depth)
print('functions/objects', len(fn_at), 'edges', sum(len(v) for v in edges.values()), 'reached', len(depth),
      'traps', len(traps), 'reachable traps', reach, 'hit traps', sum(1 for n in traps if hits[n]))
