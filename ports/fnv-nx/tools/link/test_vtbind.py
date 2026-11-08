#!/usr/bin/env python3
"""tools/link/vtbind.py: which exe slots a short / null vtgate row names (the D26 minus one, the exe table's end, a
word without a relocation is no slot), the classification (native, adjustor thunk to a native, needs a recon,
deleting destructor shape), and check against a compiled object's symbols. Our own data only."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vtbind  # noqa: E402

fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:400]))
    fails += not cond


def main():
    VT = 0x01010000
    slots = {VT: [0x00401000, 0x00402000, 0x00403000, 0x00404000, 0x00444146],   # last: string bytes, no reloc
             VT + 0x100: [0x00405000, 0x00406000, 0x00407000]}
    rel = {VT + 4 * i for i in range(4)} | {VT + 0x100 + 4 * i for i in range(3)}
    rows = [(VT, 3, 'short'), (VT + 0x100, 1, 'null')]
    got = vtbind.wanted(rows, slots.get, lambda va: va in rel)
    ok('short: from the slot minus one to the end; null: the slot and the one before; no word without a reloc',
       got == {0x00403000: 1, 0x00404000: 1, 0x00405000: 1, 0x00406000: 1}, {hex(k): v for k, v in got.items()})
    got = vtbind.wanted(rows + [(VT, 2, 'short')], slots.get, lambda va: va in rel)
    ok('an address named by two tables counts 2', got[0x00403000] == 2 and got[0x00404000] == 2, got)
    ok('a table the exe scan does not know names nothing', vtbind.wanted([(0x0, 1, 'short')], slots.get) == {})

    natives = {0x00403000: '_ZN1A1fEv', 0x00500000: '_ZN1B1gEv'}
    thunks = {0x00404000: (24, 0x00500000), 0x00405000: (8, 0x00600000)}
    bind, needs = vtbind.classify({0x00403000: 1, 0x00404000: 2, 0x00405000: 1, 0x00406000: 1}, natives,
                                  thunks.get, lambda a: a == 0x00406000)
    ok('native -> fn', ('00403000', 'fn', '_ZN1A1fEv', '', '0', '1') in bind, bind)
    ok('adjustor thunk to a native -> adj with n and target',
       ('00404000', 'adj', '__fnvnx_vadj_00404000', '_ZN1B1gEv', '24', '2') in bind, bind)
    ok('a thunk to no native and a deleting destructor shape need a recon',
       needs == [('00405000', 'function', '1'), ('00406000', 'deldtor', '1')], needs)

    cc, nm = shutil.which('aarch64-linux-gnu-gcc'), shutil.which('aarch64-linux-gnu-nm')
    if not (cc and nm):
        print('SKIP check: aarch64-linux-gnu-gcc / nm missing')
    else:
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'a.c')
            open(src, 'w').write('void have_fn(void) {}\nvoid have_target(void) {}\n')
            subprocess.run([cc, '-mabi=ilp32', '-c', src, '-o', os.path.join(d, 'a.o')], check=True)
            bp = os.path.join(d, 'bind.tsv')
            open(bp, 'w').write('# fnvnx-vtbind/1\n00401000\tfn\thave_fn\t\t0\t1\n'
                                '00402000\tadj\t__fnvnx_vadj_00402000\thave_target\t8\t1\n'
                                '00403000\tfn\tmissing_fn\t\t0\t1\n')
            miss = vtbind.check(bp, vtbind.defined_symbols(d, nm))
            ok('check: defined fn and adj target pass, the missing fn is listed (adj symbols are VTFILL\'s)',
               miss == [('00403000', 'fn', 'missing_fn')], miss)
            r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'vtbind.py'), 'check', bp, '--objects', d,
                                '--nm', nm], capture_output=True, text=True)
            ok('check exits 1 with a missing symbol', r.returncode == 1 and 'missing\t00403000' in r.stdout, r.stdout)
    print('test_vtbind: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
