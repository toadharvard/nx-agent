#!/usr/bin/env python3
"""test_weak_interlocked.py - V1 (D14) against S15-threads 35fe02d / 258d777: S12's litmus.cpp defines
fnvnx_interlocked_slow weak so that S15's mtqemu guest links it beside platform/win32/sync.cpp. The weak stub
returns 0 without doing the operation, so it must never be the one the guest runs.

    python3 -I tests/v1/test_weak_interlocked.py      (needs aarch64-linux-gnu-gcc/g++/ld; SSE2NEON optional)

W1 sync.cpp's object defines it strong (T), each litmus object weak (W), and guest.elf's definition has
   sync.cpp's size, not the stub's.
W2 the litmus objects call it through a relocation (no inlined stub: GCC must not inline a weak definition).
W3 no merge conflict markers in the J-0148 hand-merged files (sync.cpp, both fnvnx_win32.h).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
NM, OBJDUMP = 'aarch64-linux-gnu-nm', 'aarch64-linux-gnu-objdump'
BAD = 0


def case(cid, ok, why):
    global BAD
    print('%s %s' % (cid, 'ok' if ok else 'DEFECT: ' + why))
    BAD += not ok


def sym(path, name):
    out = subprocess.run([NM, '-S', path], capture_output=True, text=True).stdout
    return [ln.split() for ln in out.splitlines() if ln.endswith(' ' + name)]


def main():
    files = ['platform/win32/sync.cpp', 'shim/include/fnvnx_win32.h', 'tools/equiv/msvc/fnvnx_win32.h']
    marks = [f for f in files if re.search(r'^(<<<<<<<|=======|>>>>>>>)( |$)', open(os.path.join(PORT, f)).read(), re.M)]
    case('W3', not marks, 'conflict markers in %s' % marks)
    if not shutil.which('aarch64-linux-gnu-g++'):
        print('skip W1/W2: no aarch64 toolchain')
        return 1 if BAD else 0
    out = tempfile.mkdtemp(prefix='v1-weak-')
    try:
        r = subprocess.run(['bash', os.path.join(PORT, 'tests', 's15', 'mtqemu', 'build.sh'), out],
                           capture_output=True, text=True, timeout=500)
        if r.returncode:
            print('build failed: ' + r.stderr[-500:])
            return 2
        s = sym(os.path.join(out, 'sync.cpp.o'), 'fnvnx_interlocked_slow')
        lits = [os.path.join(out, f) for f in ('litmus_order.o', 'litmus_plain.o') if os.path.exists(os.path.join(out, f))]
        weak = [sym(p, 'fnvnx_interlocked_slow') for p in lits]
        g = sym(os.path.join(out, 'guest.elf'), 'fnvnx_interlocked_slow')
        ok = (len(s) == 1 and s[0][2] == 'T' and all(len(w) == 1 and w[0][2] == 'W' for w in weak)
              and len(g) == 1 and g[0][2] == 'T' and int(g[0][1], 16) == int(s[0][1], 16)
              and all(int(w[0][1], 16) != int(s[0][1], 16) for w in weak))
        case('W1', ok, 'sync %s, litmus %s, guest %s' % (s, weak, g))
        calls = True
        for p in lits:
            d = subprocess.run([OBJDUMP, '-dr', p], capture_output=True, text=True).stdout
            calls &= 'CALL26\tfnvnx_interlocked_slow' in d
        case('W2', not lits or calls, 'a litmus object calls the stub without a relocation')
        if not lits:
            print('note: no litmus objects (fnvnx_order plugin missing): W1 checked sync.cpp and the guest only')
    finally:
        shutil.rmtree(out, ignore_errors=True)
    print('%d defects' % BAD)
    return 1 if BAD else 0


if __name__ == '__main__':
    sys.exit(main())
