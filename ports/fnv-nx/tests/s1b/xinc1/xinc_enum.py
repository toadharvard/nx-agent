"""xinc_enum.py ABI_TSV OUT_TSV: every x86-inconsistent reference of a link run (fnvlink abi.tsv) -> its calling
functions in the pristine MSVC object, whether an S1b XINC1 patch covers that caller, and V1's ecx-use bucket when
V1_C3 names V1's c3_pairs.tsv (tools/verify/v1_link/c3_ecx.py). Environment: DECOMP, S11 (pristine objects in
$S11/obj/pristine), PORT. Output is decomp-derived: scratch only.

Pipeline (docs/PATCHES.md, round 2): xinc_enum.py -> analyze.py (ecx at each reference, context files) -> gen.py
(candidate patches for the mechanical shapes A/B/C, the rest to manual.tsv) -> tests/s1b/test_patches.py and
msvc_check.py --patches."""
import collections, json, os, sys
D = os.environ['DECOMP'] + '/'
S11 = os.environ['S11']
PORT = os.environ['PORT']
sys.path.insert(0, PORT + '/tools/equiv')
sys.path.insert(0, PORT + '/tools/rules')
import equiv
equiv.decomp_tools(D)
from compare import Coff
import s1b_patches as sp

abi, out = sys.argv[1], sys.argv[2]
units = json.load(open(D + 'build/units.json'))['units']
by_src = collections.defaultdict(list)
for i, u in enumerate(units):
    by_src[u['source']].append(i)
patched = {(s.path, s.addr) for s in sp.SITES if s.rule == 'XINC1'}
v1 = {}
for line in (open(os.environ['V1_C3']) if os.environ.get('V1_C3') else []):
    if line.startswith('#'): continue
    f = line.rstrip('\n').split('\t')
    v1[(f[0].replace('#vtables', ''), f[2])] = (f[3], f[6])
rows = collections.OrderedDict()
for line in open(abi):
    if line.startswith('#'): continue
    f = line.rstrip('\n').split('\t')
    if len(f) < 11 or f[5] != 'x86-inconsistent': continue
    src = f[0].replace('#vtables', '')
    rows.setdefault((src, f[2], f[3]), f)
coffs = {}
res = []
for (src, addr, refms), f in rows.items():
    a = int(addr, 16)
    for i in by_src.get(src, []):
        u = units[i]
        names = {n for n, x in u['symbols'].items() if int(x, 16) == a}
        if refms in names:
            names = {refms}
        obj = '%s/obj/pristine/u%d.obj' % (S11, i)
        if i not in coffs:
            coffs[i] = Coff(obj)
        coff = coffs[i]
        callers = []
        for fn in u['functions']:
            try:
                s = coff.named(fn['symbol'])
            except ValueError:
                continue
            sec = coff.sections[s['section']]
            st = s['value']
            later = [x['value'] for x in coff.symbols.values() if x['section'] == s['section'] and x['type'] & 0x20 and x['value'] > st]
            en = min(later) if later else len(sec['data'])
            hit = [o - st for o, ix, _ in sec['relocs'] if st <= o < en and coff.symbols[ix]['name'] in names]
            if hit:
                callers.append((fn['address'], fn['symbol'], hit))
        b = v1.get((src, addr), ('', ''))
        if not callers:
            res.append((src, str(i), addr, f[6], '-', '-', '-', 'nocaller', b[0], b[1], f[3], f[4], f[9], f[10]))
        for ca, cs, hit in callers:
            st = 'patched' if (src, int(ca, 16)) in patched else 'open'
            res.append((src, str(i), addr, f[6], ca, cs, ','.join(map(str, hit)), st, b[0], b[1], f[3], f[4], f[9], f[10]))
with open(out, 'w') as fo:
    fo.write('#src\tunit\tcallee\tclasses\tcaller\tcaller_sym\toffs\tstatus\tv1\tecx\tref_ms\tdef_ms\tref\tdef\n')
    for r in res:
        fo.write('\t'.join(r) + '\n')
c = collections.Counter(r[7] for r in res)
print(len(rows), 'pairs', len(res), 'caller rows', dict(c))
