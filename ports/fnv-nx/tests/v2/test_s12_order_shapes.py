#!/usr/bin/env python3
"""V2 adversarial cases for S12 92f3b4d (evaluation order per MSVC shape: object=msvc, operator=rtl, assign=msvc):
call shapes outside S11's recorded table, built with compile.sh's own flags (--print-flags; LP64 static for qemu:
evaluation order does not depend on the data model) and compared with MSVC 2008 SP1, /O2 and /Od (recorded below;
re-measured under Wine when FNV_MSVC9_ROOT and FNV_MSVC9_PREFIX are set).

    SSE2NEON=.. python3 tests/v2/test_s12_order_shapes.py     # aarch64-linux-gnu-g++ (static), qemu-aarch64

tests/v2/order/shapes.cpp prints the order of the object (o), callee (p, m) and argument (a, b) evaluations.
  P1  prvalue-virtual `make().f(a())`: MSVC oa (as a virtual call), the build ao (GCC sees a direct call)
  P2  fnptr-callee `fp()(a())`: MSVC evaluates the argument before the callee expression (ap), the build pa
  the other six (qualified virtual, through a reference, static member through an object, 2-argument direct and
  virtual, pointer to member) agree; ptm-callee is not counted (MSVC /O2 aom, /Od amo)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
SRC = os.path.join(HERE, 'order', 'shapes.cpp')
MSVC = {'qualified-virtual': 'ao', 'ref-virtual': 'oa', 'prvalue-virtual': 'oa', 'static-via-object': 'oa',
        'fnptr-callee': 'ap', 'direct-2args': 'bao', 'virtual-2args': 'oba', 'ptm-callee': None}
CASE = {'prvalue-virtual': 'P1', 'fnptr-callee': 'P2'}


def parse(text):
    return dict(l.split() for l in text.replace('\r', '').splitlines() if len(l.split()) == 2)


def main():
    work = tempfile.mkdtemp(prefix='v2-order-')   # raises when it cannot create the directory
    try:
        os.makedirs(os.path.join(work, 'tree', 'src'))
        r = subprocess.run(['bash', os.path.join(PORT, 'tools', 'link', 'compile.sh'), '--print-flags',
                            os.path.join(work, 'tree'), os.path.join(work, 'w')], capture_output=True, text=True)
        if r.returncode:
            print('skip: compile.sh --print-flags failed (%s)' % r.stderr.strip()[-200:])
            return 0
        flags = [f for f in r.stdout.split('\n') if f and f not in ('-mabi=ilp32', '-c', '-include', '-fPIC')
                 and not f.endswith('fnvnx_compat.h')]
        exe = os.path.join(work, 'q')
        r = subprocess.run(['aarch64-linux-gnu-g++'] + flags + ['-static', '-w', SRC, '-o', exe],
                           capture_output=True, text=True)
        if r.returncode:
            print('skip: build failed (%s)' % r.stderr.strip()[-200:])
            return 0
        gcc = parse(subprocess.run(['qemu-aarch64', exe], capture_output=True, text=True).stdout)
        msvc = dict(MSVC)
        if os.environ.get('FNV_MSVC9_ROOT') and os.environ.get('FNV_MSVC9_PREFIX'):
            tool = os.path.join(os.environ.get('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp')), 'tools', 'msvc.py')
            got = []
            for o in ('/O2', '/Od'):
                e = os.path.join(work, 'm%s.exe' % o[1:])
                subprocess.run([sys.executable, tool, '--compiler', 'sp1', '/nologo', '/MT', o, '/EHsc', '/Ob1', SRC,
                                '/Fe' + e, '/Fo' + e[:-4] + '.obj'], cwd=work, capture_output=True, timeout=600)
                got.append(parse(subprocess.run(['wine', e], capture_output=True, text=True, timeout=300,
                                                env=dict(os.environ, WINEDEBUG='-all')).stdout))
            msvc = {k: (got[0].get(k) if got[0].get(k) == got[1].get(k) else None) for k in MSVC}
            print('MSVC re-measured: %s' % msvc)
        fails = 0
        for k, want in msvc.items():
            if want is None:
                print('note %s: MSVC /O2 and /Od disagree, build %s' % (k, gcc.get(k)))
                continue
            ok = gcc.get(k) == want
            print('%s %s %s: build %s, MSVC %s' % ('ok' if ok else 'FAIL', CASE.get(k, '--'), k, gcc.get(k), want))
            fails += not ok
        print('ok' if not fails else 'FAILED (%d)' % fails)
        return 1 if fails else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
