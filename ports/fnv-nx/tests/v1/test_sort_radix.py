#!/usr/bin/env python3
"""test_sort_radix.py - V1 (D14) against S6n 1b0665e2 (J-0149): "SortPackets as an 8-bit-digit LSD radix: all eight
histograms in one pass, constant digits skipped, ping-pong buffers, stable; equal to std::stable_sort".

SortPackets is taken from render/native/front/front.cpp as it stands (the text from `void SortPackets(` to the closing
brace at column 0), compiled with the host g++ next to tests/v1/sort/drive.cpp: adversarial key sets S1-S9 (empty, one
key, all equal, bit 63 only, pass byte, 70,000 keys, descending, upper word only, disjoint bytes) against
std::stable_sort, T0 the same two 200,000-key sorts one after the other (control), and T1 (a note, not a defect of
the claim): those two sorts on two threads at once.
  python3 -I tests/v1/test_sort_radix.py   -> one line per case, exit 1 when a sort differs
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAT = os.path.join(HERE, '..', '..', 'render', 'native')


def main():
    src = open(os.path.join(NAT, 'front', 'front.cpp')).read()
    m = re.search(r'^void SortPackets\(.*?^}\n', src, re.M | re.S)
    if not m:
        print('SortPackets not found in front.cpp')
        return 1
    work = tempfile.mkdtemp(prefix='v1-sort-')   # raises when it cannot: never an empty or relative path
    try:
        unit = os.path.join(work, 'sort.cpp')
        with open(unit, 'w') as f:
            f.write('#include "nx/front.h"\n#include <string.h>\nnamespace nx {\n' + m.group(0) + '}\n')
        exe = os.path.join(work, 'drive')
        r = subprocess.run(['g++', '-std=c++17', '-O2', '-pthread', '-I', os.path.join(NAT, 'include'), '-o', exe,
                            unit, os.path.join(HERE, 'sort', 'drive.cpp')], capture_output=True, text=True)
        if r.returncode:
            print('build failed:\n' + r.stderr[-2000:])
            return 1
        rc = 0
        for mode in ('sort', 'seq', 't1'):
            r = subprocess.run(['stdbuf', '-oL', exe, mode], timeout=300).returncode
            if mode == 't1':   # not part of the claim: the static hist[8][256] (like the former count[]) is shared
                if r:
                    print('T1: note - two threads sorting their own frames at once: %s; SortPackets is not '
                          'reentrant (function-level static hist), which matters for two-thread recording (J-0140)'
                          % ('killed by signal %d' % -r if r < 0 else 'unsorted output'))
            elif r:
                if r < 0:
                    print('%s: driver killed by signal %d' % (mode, -r))
                rc = 1
        return rc
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
