#!/usr/bin/env python3
"""Hunt unsound 'private' verdicts: classes (a)-(f) over all entries they call private, plus the 40-sample listing."""
import csv, collections, sys
sys.path.insert(0, '/home/user/nx-agent/ports/fnv-nx/lift/verify_r9')
import frameesc2 as F
F.load_pe()
F.RANGES = F.parse_ranges(F.FACTS + 'ranges.tsv')
mine = {r['entry']: r for r in csv.DictReader(open('/home/user/l3work/verify_r9/verdicts.tsv'), delimiter='\t')}
rows = {r['entry']: r for r in csv.DictReader(open(F.FACTS + 'memclass/entries.tsv'), delimiter='\t')}
priv = lambda r: r['whole'] == '0' and r['events_escaping'] == '0'
def evs(m):
    out = []
    for e in m['first_events'].split(';'):
        if e != '-':
            c, t, a, w, en = e.split('/')
            out.append((int(c), t, int(a, 16), w, en))
    return out
def ins_at(a):
    return next(F.CS.disasm(F.read(a, 15), a))
cls = collections.defaultdict(list)
cand = []
for k, r in sorted(rows.items()):
    m = mine.get(k)
    if not m or not priv(r) or m['priv'] == '1':
        continue
    ev = evs(m)
    root_priv = priv(rows[m['root']]) if m['root'] in rows else False
    for c, t, a, w, en in ev:
        if t in ('store-ptr', 'store-global'):
            cls['a'].append(k)
        if t == 'ret':
            cls['b'].append(k)
        if t == 'call-reg-indirect':
            cls['c'].append(k)
        if t == 'push':
            i = ins_at(a)
            ent = F.decode(F.RANGES[int(en, 16)])
            nxt = [x for x in sorted(ent) if x > a and ent[x].mnemonic == 'call']
            if nxt and ent[nxt[0]].operands[0].type != F.X.X86_OP_IMM:
                cls['c'].append(k)
        if w == 'F' and c == 1 and root_priv:
            cls['d'].append(k)
        if t == 'jmpreg':
            cls['e'].append(k)
        if t in ('slot-push', 'call-slots', 'ptr-store-with-slots', 'ptr-mem-with-slots', 'string-op-with-slots', 'slot-read-nonmov', 'untrackable-esp'):
            cls['f'].append(k)
    good = [x for x in ev if x[0] in (1, 2) and not (x[1] == 'copy-esp' and ins_at(x[2]).mnemonic == 'xor')]
    if good:
        cand.append((k, good[0]))
for c in 'abcdef':
    u = sorted(set(cls[c]))
    print(c, len(u), u[:5])
print('candidates cond1/2 excluding xor-cookie', len(cand), collections.Counter(g[1] for k, g in cand).most_common(12))
step = max(1, len(cand) // 40)
for k, (c, t, a, w, en) in cand[::step][:40]:
    ent = sorted(F.decode(F.RANGES[int(en, 16)]).items())
    idx = [x[0] for x in ent].index(a)
    print('== %s fire %x %s (%s) kind=%s' % (k, a, t, w, rows[k]['kind']))
    for ad, i in ent[max(0, idx - 4):idx + 5]:
        print('  %x%s %s %s' % (ad, '*' if ad == a else ' ', i.mnemonic, i.op_str))
