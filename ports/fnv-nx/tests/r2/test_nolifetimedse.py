#!/usr/bin/env python3
"""tests/r2 - the game is built with -fno-lifetime-dse (EQ farm baseline 588863, 00d979f0): decomp code calls a
destructor explicitly and then reuses the object (`this->Base::~Base(); m_a = 0;`). MSVC 2008 keeps the destructor's
vptr store; GCC's default -flifetime-dse drops it as a store to a dead object, so the native object kept the derived
class's vptr where the exe has the base's.

    python3 -I tests/r2/test_nolifetimedse.py

  L1  the flag is in layout.SEMANTICS, tools/link/compile.sh, tools/census.sh, tools/static/flags.sh, toolchain/game/build.sh
  L2  with the flag the explicit-destructor-then-reuse function stores the base vptr; without it GCC drops the store
      (so the test is not vacuous). Needs aarch64-linux-gnu-g++; L2 skips without it.
Our own snippet, nothing of the decomp.
"""
import os
import shutil
import subprocess
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
import layout  # noqa: E402

FLAG = '-fno-lifetime-dse'
SRC = '''struct Base { virtual ~Base(); int a, b; };
struct Obj : Base { virtual ~Obj(); void Reset(); };
inline Base::~Base() {}
void Obj::Reset() { this->Base::~Base(); a = 0; b = 0; }
'''
fails = []


def ok(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond: fails.append(name)


def main():
    files = {'tools/link/compile.sh': None, 'tools/census.sh': None, 'tools/static/flags.sh': None,
             'toolchain/game/build.sh': None}
    for f in files:
        ok('L1 %s has %s' % (f, FLAG), FLAG in open(os.path.join(PORT, f)).read().replace('# -fno-lifetime-dse', ''))
    ok('L1 layout.SEMANTICS has %s' % FLAG, FLAG in layout.SEMANTICS)
    cxx = shutil.which('aarch64-linux-gnu-g++')
    if not cxx:
        print('skip L2 (no aarch64-linux-gnu-g++)')
    else:
        work = tempfile.mkdtemp()
        src = os.path.join(work, 't.cpp'); open(src, 'w').write(SRC)
        def asm(extra):
            r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-S', '-o', '-', src] + extra,
                               capture_output=True, text=True)
            body = r.stdout.split('_ZN3Obj5ResetEv:', 1)[-1].split('.cfi_endproc', 1)[0]
            return 'ZTV4Base' in body
        ok('L2 with %s Reset stores the Base vptr' % FLAG, asm([FLAG]))
        ok('L2 without it GCC drops the store (the test is not vacuous)', not asm([]))
        shutil.rmtree(work)
    print('%d failed' % len(fails))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
