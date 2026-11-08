#!/usr/bin/env python3
"""Self-test of the platform baseline (D25 #4): tools/perf/plat_wine.py on a synthetic fnvtrace log of the original
(kernel32!CreateFileA calls: from the exe image and from Wine itself, failing and succeeding), then
tools/perf/plat.py --original on synthetic port logs: an open that fails on both sides is listed separately and left
out of the metric, one that fails only on the port is counted, metric_all keeps both. Our own paths, no game data.

    python3 -I tests/s15/test_plat_wine.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
WINE = os.path.join(P, 'tools', 'perf', 'plat_wine.py')
PLAT = os.path.join(P, 'tools', 'perf', 'plat.py')
sys.path.insert(0, os.path.join(P, 'tools', 'perf'))
import plat_wine  # noqa: E402

fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:600]))
    fails += not cond


def call(seq, ret, name='CreateFileA'):
    # call seq tid depth name addr ecx edx s0..s7 esp ret
    return 'call\t%d\t7\t0\t%s\t7b800000\t0\t0\t%s\t0x33fe00\t%#x\n' % (seq, name, '\t'.join(['0x1000'] * 8), ret)


def arg(seq, s):
    return 'arg\t%d\ts0\t%s\n' % (seq, (s.encode() + b'\0' * 8).hex())


def ret(seq, eax, name='CreateFileA'):
    return 'ret\t%d\t7\t0\t%s\t%#x\t0\n' % (seq, name, eax)


def main():
    ok('path class like platstat', [plat_wine.path_class(x.encode()) for x in
                                   ('Data\\A.BSA', 'x.esp', 'My Games\\Saves\\s.fos', 'Test.INI', 'Plugins.txt')]
       == ['bsa', 'esm', 'save', 'ini', 'other'])
    ok('FNV-1a like platstat', plat_wine.fnv1a(b'Plugins.txt') == '8c42ed6d', plat_wine.fnv1a(b'Plugins.txt'))
    with tempfile.TemporaryDirectory() as d:
        tsv = os.path.join(d, 'wine.tsv')
        with open(tsv, 'w') as f:
            f.write('# fnvtrace: 1 functions listed, 1 armed\n')
            f.write(call(1, 0x00ec1234) + arg(1, 'Plugins.txt') + ret(1, 0xffffffff))          # game, fails
            f.write(call(2, 0x00ec1234) + arg(2, 'Fallout_default.ini') + ret(2, 0x44))        # game, opens
            f.write(call(3, 0x7b812345) + arg(3, 'C:\\windows\\system.ini') + ret(3, 0xffffffff))  # Wine's own
            f.write(call(4, 0x00ec1234) + arg(4, 'Data\\Missing.bsa') + ret(4, 0xffffffff))    # game, fails
            f.write(call(5, 0x00ec1234) + arg(5, 'Plugins.txt') + ret(5, 0xffffffff))          # again
        out = os.path.join(d, 'orig.json')
        r = subprocess.run([sys.executable, '-I', WINE, tsv, '--out', out], capture_output=True, text=True)
        j = json.load(open(out)) if os.path.isfile(out) else {}
        ok('plat_wine runs', r.returncode == 0 and j.get('schema') == 'fnvnx-plat-original/1', r.stdout + r.stderr)
        ok('the game\'s opens only (Wine\'s own left out)', j.get('opens') == 4, j)
        fo = {(x['class'], x['hash']): x['count'] for x in j.get('failed_opens', [])}
        ok('failed opens keyed by class and hash, counted',
           fo == {('other', '8c42ed6d'): 2, ('bsa', plat_wine.fnv1a(b'Data\\Missing.bsa')): 1}, fo)
        ok('no path text in the baseline', 'Plugins' not in open(out).read() and 'Missing' not in open(out).read())
        empty = os.path.join(d, 'empty.tsv')
        open(empty, 'w').write('# fnvtrace: 0 functions listed\n')
        r = subprocess.run([sys.executable, '-I', WINE, empty, '--out', os.path.join(d, 'e.json')], capture_output=True)
        ok('no CreateFileA in the trace: exit 3', r.returncode == 3)

        # the port: Plugins.txt fails (also in the original), Custom.ini fails (only on the port)
        log = os.path.join(d, 'emu.log')
        hci = plat_wine.fnv1a(b'Custom.ini')
        open(log, 'w').write('   plat: open _open caller 0x20001000 other err 2 ci no path 8c42ed6d\n'
                             '   plat: open fopen caller 0x20002000 ini err 2 ci no path %s\n'
                             '   plat: unimpl SomeEntry caller 0x20003000\n' % hci)
        rep = os.path.join(d, 'plat.md')
        r = subprocess.run([sys.executable, '-I', PLAT, log, '--original', out, '--out', rep], capture_output=True,
                           text=True)
        pj = json.load(open(rep[:-3] + '.json'))
        md = open(rep).read()
        ok('plat --original runs', r.returncode == 0, r.stdout + r.stderr)
        ok('the shared failure is left out of the metric', pj['metric'] == 2 and pj['parts']['open'] == 1, pj)
        ok('metric_all keeps it', pj['metric_all'] == 3 and pj['open_in_original'] == 1, pj)
        ok('listed separately', 'also fail in the original (not counted)' in md and '| 8c42ed6d | other |' in md
           and 'without the baseline 3' in md, md)
        ok('per open in the json', {x['path']: x['in_original'] for x in pj['open_list']} == {'8c42ed6d': True, hci: False},
           pj['open_list'])
        r = subprocess.run([sys.executable, '-I', PLAT, log, '--out', rep], capture_output=True, text=True)
        pj = json.load(open(rep[:-3] + '.json'))
        ok('without --original nothing is subtracted', pj['metric'] == 3 and pj['open_in_original'] == 0, pj)
    print('test_plat_wine: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
