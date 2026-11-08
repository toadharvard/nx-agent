"""undefnames.py (R2, orchestrator 2026-11-08) LINKDIR GUEST.elf CAUSES.tsv TRAPS.txt OUT.tsv : the trial link's undefined names (referenced by some
fixed/*.o, defined by none), with what the guest does with each (platform-provided / trap) and S2's cause/owner row."""
import os, subprocess, sys, collections
L, elf, causes, trapf, out = sys.argv[1:6]
objs = sorted(os.path.join(L, 'fixed', f) for f in os.listdir(os.path.join(L, 'fixed')) if f.endswith('.o'))
und, dfn, who = set(), set(), collections.defaultdict(set)
for i in range(0, len(objs), 400):
    r = subprocess.run(['aarch64-linux-gnu-nm', '-A'] + objs[i:i + 400], capture_output=True, text=True).stdout
    for line in r.splitlines():
        p = line.rsplit(' ', 2)
        if len(p) < 3: continue
        o, t, n = p[0].rsplit(':', 1)[0], p[1], p[2]
        if t == 'U': und.add(n); who[n].add(os.path.basename(o))
        elif t not in ('U', 'w', 'v'): dfn.add(n)
gd = set(l.split()[-1] for l in subprocess.run(['aarch64-linux-gnu-nm', '--defined-only', elf], capture_output=True, text=True).stdout.splitlines() if l.strip())
traps = set(x.strip() for x in open(trapf))
crow = {}
for line in open(causes, errors='surrogateescape'):
    if not line.startswith('#'):
        c = line.rstrip('\n').split('\t'); crow.setdefault(c[0], c)
import re
al = set()
ap = os.path.join(L, 'out', 'aliases.ld')
if os.path.exists(ap):   # the trial link defines these by address (link-by-address aliases)
    al = set(re.findall(r'^\s*(?:PROVIDE\()?\s*([A-Za-z_.$][\w.$@?]*)\s*=', open(ap).read(), re.M))
res = sorted(und - dfn - al)
with open(out, 'w') as f:
    f.write('#name\tguest\towner\tcause\tdemangled\treferencing objects (n)\n')
    for n in res:
        g = 'trap' if n in traps else ('platform' if n in gd else 'undefined (weak 0 in the guest?)')
        c = crow.get(n)
        owner = c[7] if c else ('S4' if g == 'platform' else 's2-bodies')
        cause = c[2] if c else ('provided by the guest platform/runtime' if g == 'platform' else '?')
        dem = c[1] if c else ''
        f.write('%s\t%s\t%s\t%s\t%s\t%d\n' % (n, g, owner, cause, dem, len(who[n])))
cnt = collections.Counter(l.split('\t')[1] for l in open(out) if not l.startswith('#'))
print(len(res), 'undefined names;', dict(cnt))
