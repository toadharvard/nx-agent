#!/usr/bin/env python3
"""The m1 range (S1 / orchestrator 2026-11-08): minus-one vtables go into .data.rel.ro.fnvnx_m1.*, which
toolchain/guest/guest.ld places between __fnvnx_m1_start and __fnvnx_m1_end.

    python3 tools/link/test_m1.py     # needs aarch64-linux-gnu-g++ / objcopy / ld
"""
import os
import shutil
import subprocess
import sys
import tempfile
import types
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink   # noqa: E402
import elfsyms   # noqa: E402

CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FAILS = []
SRC = r'''
struct WithDtor { virtual ~WithDtor() {} virtual int f() { return 1; } int x; };
struct NoDtor { virtual int g() { return 2; } int y; };
WithDtor *mk1() { return new WithDtor; }
NoDtor *mk2() { return new NoDtor; }
'''


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


def main():
    check(fnvlink.m1_section(True) == 'fnvnx_m1' and fnvlink.m1_section(False) == 'fnvnx_vtext' and
          fnvlink.m1_section(True, 'fnvnx_vmo') == 'fnvnx_m1.vmo' and fnvlink.m1_section(False, 'fnvnx_vmo') == 'fnvnx_vmo',
          'm1_section: minus-one tables in the m1 range, the others where they were')
    if not shutil.which(CXX):
        print('skip: no %s' % CXX)
        return 0
    t = tempfile.mkdtemp(prefix='m1-')
    cp, op = os.path.join(t, 'a.cpp'), os.path.join(t, 'a.o')
    open(cp, 'w').write(SRC)
    subprocess.run([CXX, '-mabi=ilp32', '-O1', '-c', cp, '-o', op], check=True)
    out = os.path.join(t, 'out')
    os.makedirs(os.path.join(out, 'objcopy'))
    open(os.path.join(out, 'objcopy', 'a.o.args'), 'w').write('\n')
    u = types.SimpleNamespace(source='src/a.cpp', obj=op)
    n = fnvlink.m1_compiled_renames([u], {u.source: elfsyms.symbols(op)}, out, collections.Counter())
    args = open(os.path.join(out, 'objcopy', 'a.o.args')).read()
    check(n == 1 and ('=.data.rel.ro.fnvnx_m1._ZTV8WithDtor' in args or '=.data.rel.ro.fnvnx_m1.local._ZTV8WithDtor' in args) and
          'NoDtor' not in args, 'compiled groups: the class with a destructor pair is renamed, the other is not: %r' % args)
    # objcopy takes the argument, and a script like guest.ld's brackets the table
    dst = os.path.join(t, 'b.o')
    subprocess.run(['aarch64-linux-gnu-objcopy', '@' + os.path.join(out, 'objcopy', 'a.o.args'), op, dst], check=True)
    ld = os.path.join(t, 's.ld')
    open(ld, 'w').write('SECTIONS { .data.rel.ro : { __fnvnx_m1_start = .; KEEP(*(.data.rel.ro.fnvnx_m1 '
                        '.data.rel.ro.fnvnx_m1.*)) __fnvnx_m1_end = .; *(.data.rel.ro .data.rel.ro.*) } }\n')
    elf = os.path.join(t, 'r.o')
    r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-T', ld, '--unresolved-symbols=ignore-all',
                        '-e', '0', dst, '-o', elf],
                       capture_output=True, text=True)
    if r.returncode == 0:
        nm = subprocess.run(['aarch64-linux-gnu-nm', elf], capture_output=True, text=True).stdout
        sy = {l.split()[-1]: int(l.split()[0], 16) for l in nm.splitlines() if len(l.split()) == 3}
        check(sy.get('__fnvnx_m1_start', -1) <= sy.get('_ZTV8WithDtor', -2) < sy.get('__fnvnx_m1_end', -3) and
              not (sy['__fnvnx_m1_start'] <= sy.get('_ZTV6NoDtor', 0) < sy['__fnvnx_m1_end']),
              'the linker script brackets WithDtor\'s table and leaves NoDtor\'s out')
    else:
        print('skip link check: %s' % r.stderr.strip()[:200])
    os.environ['FNVNX_M1'] = '0'
    try:
        check(fnvlink.m1_section(True) == 'fnvnx_vtext' and
              fnvlink.m1_compiled_renames([u], {u.source: elfsyms.symbols(op)}, out, collections.Counter()) == 0,
              'FNVNX_M1=0: nothing moves')
    finally:
        del os.environ['FNVNX_M1']
    shutil.rmtree(t, ignore_errors=True)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
