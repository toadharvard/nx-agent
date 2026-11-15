#!/usr/bin/env python3
"""rtasm census over archives: python3 -I farm/rtasm_census.py OUTDIR ARCHIVE...  .  Every member of every archive that is assembled or has no DWARF compile unit
is judged by link/a64order.scan_asm_rules (the gate's own scan); class per member: EMPTY (no code), KC (only svc, no access), or the set of certifying rules
O=AT (ordered form), F/D=D1 (frame / unshared), L=RO (read-only data or linker slot); RED lists every uncertified item with its reason."""
import collections, json, os, subprocess, sys, tempfile
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
for d in ('verify', 'link', 'contract'): sys.path.insert(0, os.path.join(H, d))
import a64order, gate_rtasm
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
count = collections.Counter(); red = []; rows = []
for arc in sys.argv[2:]:
    d = tempfile.mkdtemp(prefix='rt-', dir=out)
    subprocess.run(['ar', 'x', os.path.abspath(arc)], cwd=d, check=True)
    for m in sorted(os.listdir(d)):
        p = os.path.join(d, m); oid = '%s(%s)' % (os.path.basename(arc), m)
        try: org = gate_rtasm.origin(p)
        except Exception as e: org = 'unreadable'
        if org == 'other': continue                      # a C/C++ object: judged by gate order
        try:
            r = a64order.scan_asm_rules(p)
        except Exception as e:
            count['RED:scan'] += 1; red.append((oid, 'scan failed: %s' % str(e)[:100])); continue
        bad = [(i, w) for i, w, rule in r if not rule]
        if bad:
            count['RED'] += 1; red += [(oid + ':' + i, w) for i, w in bad]; continue
        if r:
            cls = '+'.join(sorted({'AT' if x[2] == 'O' else 'RO' if x[2] == 'L' else 'D1' for x in r}))
        else:
            o = a64order.a64obj.read(p)
            ex = [x for x in o.sections if x.flags & a64order.a64obj.SHF_EXECINSTR and x.type == 'SHT_PROGBITS']
            if not sum(x.size for x in ex): cls = 'EMPTY'
            elif any(i.id == a64order.K.ARM64_INS_SVC for x in ex for i in a64order.decode_range(x.data, 0, x.size - x.size % 4) if getattr(i, 'ok', False)): cls = 'KC'
            else: cls = 'NOACCESS'            # code with no memory access and no svc: a class of its own, reported
        count[org + ':' + cls] += 1; rows.append((oid, org, cls))
json.dump({'counts': count, 'red': red, 'members': rows}, open(os.path.join(out, 'rtasm_census.json'), 'w'), indent=1)
for k, v in sorted(count.items()): print(v, k)
print('RED items:', len(red))
for r in red[:60]: print(' ', r)
