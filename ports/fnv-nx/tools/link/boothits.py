"""boothits.py OUT.tsv LOG... [--manual FILE] (R2): trap hits of boots, as topdown.py's hits input.
Reads emu.log / emu_stub.log files (named <link12>_*.log or any name): `trap-table` rows (key, hits per key: the
row's hit count), `stubbed-fault` rows that name a "virtual function defined nowhere", and `fnvnx boot: trap #N:
unresolved` lines. Per log the maximum per key; summed over logs. --manual adds `name<TAB>count` rows (hits reported
by message, e.g. S5-boot). Output: name, hits, boots, kind, boot names."""
import collections, os, re, sys
args = sys.argv[1:]; manual = None
if '--manual' in args:
    i = args.index('--manual'); manual = args[i + 1]; del args[i:i + 2]
out, logs = args[0], args[1:]
tot = collections.Counter(); boots = collections.defaultdict(set); kind = {}
for f in logs:
    tag = os.path.basename(f).split('_')[0]; per = collections.Counter()
    for l in open(f, errors='replace'):
        p = l.rstrip('\n').split('\t')
        if p[0] == 'trap-table' and len(p) > 5:
            per[p[5]] = max(per[p[5]], int(p[2])); kind[p[5]] = 'trap-table'
        elif p[0] == 'stubbed-fault':
            m = re.search(r'virtual function defined nowhere: (\S+)', l)
            if m:
                per[m.group(1)] = max(per[m.group(1)], int(p[1])); kind.setdefault(m.group(1), 'vslot-fault')
        else:
            m = re.search(r'fnvnx boot: trap #\d+: unresolved (\S+)', l)
            if m:
                per[m.group(1)] = max(per[m.group(1)], 1); kind.setdefault(m.group(1), 'trap-line')
    for k, v in per.items():
        tot[k] += v; boots[k].add(tag)
if manual and os.path.isfile(manual):
    for l in open(manual):
        p = l.rstrip('\n').split('\t')
        if len(p) >= 2 and p[0] and not p[0].startswith('#'):
            tot[p[0]] += int(p[1]); boots[p[0]].add('manual'); kind.setdefault(p[0], 'reported')
with open(out, 'w') as o:
    for k, v in tot.most_common():
        o.write('%s\t%d\t%d\t%s\t%s\n' % (k, v, len(boots[k]), kind[k], ','.join(sorted(boots[k]))))
print(len(tot), 'hit keys from', len(logs), 'logs')
