"""preserves.py ADDR|SYMBOL...: for each function, is the incoming ecx (and edx) still in ecx (edx) at every return?
Uses the pristine MSVC object; calls inside count as clobbering (conservative)."""
import json, os, sys
D = os.environ['DECOMP'] + '/'
sys.path.insert(0, os.environ['PORT'] + '/tools/verify/v1_link')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coff_x86, ecxflow

units = json.load(open(D + 'build/units.json'))['units']
idx = {}
for i, u in enumerate(units):
    for f in u['functions']:
        idx.setdefault(int(f['address'], 16), (i, f['symbol'], u['source']))
        idx.setdefault(f['symbol'], (i, f['symbol'], u['source']))
for key in sys.argv[1:]:
    k = int(key, 16) if not key.startswith('?') else key
    if k not in idx:
        print(key, 'no definition'); continue
    i, sym, src = idx[k]
    co = coff_x86.Coff('%s/obj/pristine/u%d.obj' % (os.environ['S11'], i))
    code, rel = co.func(sym)
    for reg in ('ecx', 'edx'):
        insns, st = ecxflow.flow(code, entry=frozenset({reg}))
        rets = [a for a, ins in insns.items() if ins.mnemonic in ('ret', 'retn')]
        tails = [a for a, ins in insns.items() if ins.mnemonic == 'jmp' and any(a <= o < a + ins.size for o in rel)]
        ok = all(a in st and reg in st[a][0] for a in rets)
        print('%s %s %s: %s at all %d returns%s' % (key, sym, reg, 'kept' if ok else 'NOT kept', len(rets),
                                                   ' (tail jumps: %d)' % len(tails) if tails else ''))
