#!/usr/bin/env python3
"""Synthetic check of globext.py (invented rows, no exe data): format, sort, overlap error, probes, CLI."""
import json, os, subprocess, sys, tempfile

C = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, C)
import globext

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def raises(what, rows, needle):
    try:
        globext.build(rows)
    except ValueError as e:
        if needle not in str(e):
            fails.append('%s: message %r lacks %r' % (what, str(e), needle))
        return
    fails.append(what + ': no error')


def row(va, size, sym, image='exe', gates=('layout', 'namegate', 'abi')):
    return {'image': image, 'va': va, 'size': size, 'certified_by': list(gates), 'type': 'T' + sym, 'symbol': sym, 'unit': 'u'}


rows = [row(0x2000, 8, 'b'), row(0x1000, 16, 'a'), row(0x10, 4, 'z', image='dll'), row(0x2000, 8, 'b')]
objs = globext.build(rows)
check('sorted', [(o[0], o[1]) for o in objs], [('dll', 0x10), ('exe', 0x1000), ('exe', 0x2000)])
check('fact', globext.fact_text(objs), 'dll 0x10 4 abi+layout+namegate\nexe 0x1000 16 abi+layout+namegate\nexe 0x2000 8 abi+layout+namegate\n')
check('audit', globext.audit_text(objs).splitlines()[1], 'exe 0x1000 16 Ta a u')
check('count', globext.count_in(objs, 'exe', 0x1000, 0x2008), 2)
check('count partial', globext.count_in(objs, 'exe', 0x1000, 0x2004), 1)
check('probe in', globext.containing(objs, 'exe', 0x100f)[5], 'a')
check('probe end', globext.containing(objs, 'exe', 0x1010), None)
check('adjacent ok', len(globext.build([row(0, 4, 'p'), row(4, 4, 'q')])), 2)
check('other image no overlap', len(globext.build([row(0, 4, 'p'), row(0, 4, 'q', image='dll')])), 2)
raises('overlap', [row(0x1000, 16, 'a'), row(0x1008, 8, 'c')], 'a')
raises('overlap names both', [row(0x1000, 16, 'a'), row(0x1008, 8, 'c')], 'c')
raises('size 0', [row(1, 0, 'a')], 'bad va/size')
raises('no gates', [row(1, 1, 'a', gates=())], 'certified_by')
raises('space in gate', [row(1, 1, 'a', gates=('a b',))], 'certified_by')
raises('missing', [{'image': 'exe'}], 'missing')
check('hex va', globext.build([row('0x20', 4, 'h')])[0][1], 0x20)

with tempfile.TemporaryDirectory() as d:
    rp, fp = os.path.join(d, 'r.json'), os.path.join(d, 'f.txt')
    json.dump(rows, open(rp, 'w'))
    p = subprocess.run([sys.executable, '-I', os.path.join(C, 'globext.py'), '--rows', rp, '--out', fp,
                        '--range', '1000', '3000', '--probe', '1004', '5000'], capture_output=True, text=True)
    check('cli rc', p.returncode, 0)
    check('cli out', p.stdout.splitlines(), ['objects 3', 'range 0x1000..0x3000 certified objects 2',
                                             '1004 a [0x1000,0x1010)', '5000 none certified'])
    check('cli file', open(fp).read(), globext.fact_text(objs))

if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('globext: ok')
