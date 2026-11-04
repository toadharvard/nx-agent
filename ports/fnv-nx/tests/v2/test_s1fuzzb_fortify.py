#!/usr/bin/env python3
"""V2 check of S1-fuzzB 6318d13 (-U_FORTIFY_SOURCE in the game build).

    python3 tests/v2/test_s1fuzzb_fortify.py     # aarch64-linux-gnu-g++

  F1  premise: Ubuntu's aarch64-linux-gnu-g++ predefines _FORTIFY_SOURCE at -O2 (memcpy/strcpy/sprintf become
      __*_chk calls)
  F2  a unit compiled with compile.sh --print-flags has no __*_chk reference (memcpy, strcpy, sprintf)
  F3  every other flag source of game units carries -U_FORTIFY_SOURCE: census.sh, static/flags.sh
      (bodies.py and vtables.py take compile.sh --print-flags)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SRC = '#include <string.h>\n#include <stdio.h>\nchar b[8];\nvoid f(const char *s, int n) { memcpy(b, s, n); ' \
      'strcpy(b, s); sprintf(b, "%d", n); }\n'


def main():
    work = tempfile.mkdtemp(prefix='v2-fortify-')   # raises when it cannot create the directory
    fails = 0

    def check(c, what):
        nonlocal fails
        print('%s %s' % ('ok' if c else 'FAIL', what))
        fails += not c
    try:
        d = subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-O2', '-dM', '-E', '-x', 'c++', '-'], input='',
                           capture_output=True, text=True).stdout
        check('_FORTIFY_SOURCE' in d, 'F1 the cross compiler predefines _FORTIFY_SOURCE at -O2')
        os.makedirs(os.path.join(work, 'tree', 'src'))
        r = subprocess.run(['bash', os.path.join(PORT, 'tools', 'link', 'compile.sh'), '--print-flags',
                            os.path.join(work, 'tree'), os.path.join(work, 'w')], capture_output=True, text=True)
        flags = [f for f in r.stdout.split('\n') if f]
        p = os.path.join(work, 'f.cpp')
        open(p, 'w').write(SRC)
        subprocess.run(['aarch64-linux-gnu-g++'] + [f for f in flags if f != '-c'] + ['-c', p, '-o', p + '.o'],
                       capture_output=True, check=True)
        und = subprocess.run(['aarch64-linux-gnu-nm', '-u', p + '.o'], capture_output=True, text=True).stdout
        chk = re.findall(r'__\w+_chk', und)
        check('-U_FORTIFY_SOURCE' in flags and not chk, 'F2 compile.sh flags: no __*_chk references %s' % chk)
        for f in ('tools/census.sh', 'tools/static/flags.sh'):
            check('-U_FORTIFY_SOURCE' in open(os.path.join(PORT, f)).read(), 'F3 %s carries -U_FORTIFY_SOURCE' % f)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
