#!/usr/bin/env python3
"""W51: __fnvnx_m1k_ranges lists the minus-one tables whose destructor pair sits at k outside {0, 4}.

    python3 tools/link/test_w51.py     # needs aarch64-linux-gnu-as / readelf

* m1k_note keeps a minus-one table at k = 6 and drops k = 0, k = 4 and exe-order tables; FNVNX_M1K=0 keeps none;
* emit_m1k_ranges writes {sym + 8, sym + 4 x words, k} per table and the count, and assembles (ILP32);
* no table: one zero row, count 0.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink  # noqa: E402

AS = os.environ.get('AS', 'aarch64-linux-gnu-as')
FAILS = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


def table(sym, n):
    return '\n'.join(['// %s' % sym, '    .section .data.rel.ro.fnvnx_vtext.%s,"aw"' % sym, '    .p2align 3',
                      '    .globl %s' % sym, '%s:' % sym, '    .word 0x0', '    .word 0'] +
                     ['    .word 0x%x' % i for i in range(n)] + ['    .size %s, .-%s' % (sym, sym), '    .text']) + '\n'


def main():
    fnvlink.M1K.clear()
    br = {'zzvtext_A': table('A', 9), 'zzvtext_B': table('B', 5), 'zzvtext_C': table('C', 5),
          'zzvtext_D': table('D', 5)}
    fnvlink.m1k_note('A', 'zzvtext_A', True, 6)
    fnvlink.m1k_note('B', 'zzvtext_B', True, 0)
    fnvlink.m1k_note('C', 'zzvtext_C', True, 4)
    fnvlink.m1k_note('D', 'zzvtext_D', False, 3)
    check(set(fnvlink.M1K) == {'A'}, 'm1k_note: only the minus-one table at k = 6: %s' % sorted(fnvlink.M1K))
    st = {}
    text = fnvlink.emit_m1k_ranges(br, st)
    check('    .word A + 8, A + 44, 6' in text and '    .word 1' in text and st['m1k-ranges'] == 1,
          'emit: A + 8 .. A + 4 x 11, k 6; count 1')
    os.environ['FNVNX_M1K'] = '0'
    try:
        fnvlink.M1K.clear()
        fnvlink.m1k_note('A', 'zzvtext_A', True, 6)
        check(not fnvlink.M1K, 'FNVNX_M1K=0: none')
    finally:
        del os.environ['FNVNX_M1K']
    empty = fnvlink.emit_m1k_ranges({}, {})
    check('    .word 0, 0, 0' in empty and '__fnvnx_m1k_count:\n    .word 0' in empty, 'no table: a zero row, count 0')
    if shutil.which(AS):
        t = tempfile.mkdtemp(prefix='w51-')
        sp = os.path.join(t, 'b.S')
        open(sp, 'w').write(table('A', 9) + text)
        r = subprocess.run([AS, '-mabi=ilp32', sp, '-o', os.path.join(t, 'b.o')], capture_output=True, text=True)
        check(r.returncode == 0, 'assembles (ILP32): %s' % r.stderr.strip()[:200])
        rel = subprocess.run(['aarch64-linux-gnu-readelf', '-rsW', os.path.join(t, 'b.o')], capture_output=True,
                             text=True).stdout
        check('__fnvnx_m1k_ranges' in rel and '__fnvnx_m1k_count' in rel and rel.count('P32_ABS32') >= 2,
              'symbols and ABS32 relocations')
        shutil.rmtree(t, ignore_errors=True)
    else:
        print('skip assemble: no %s' % AS)
    fnvlink.M1K.clear()
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
