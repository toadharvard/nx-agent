#!/usr/bin/env python3
"""tests/s2d - the game is built with -fno-sized-deallocation (W41): MSVC 2008 never emits a sized delete, so a
delete-expression must call the unsized `operator delete(void*)` (_ZdlPv, the exe's 0x00401030), not GCC's gnu++17
default `_ZdlPvm`/`_ZdlPvj` (0x00aa1460).

    python3 -I tests/s2d/test_nosized.py

  S1  the flag is in layout.SEMANTICS, tools/link/compile.sh, tools/census.sh, tools/static/flags.sh, toolchain/game/build.sh
  S2  `delete p` of a class without a member operator delete -> a call of _ZdlPv, none of _ZdlPvm/_ZdlPvj
      (without the flag the same source calls the sized one: the test would be vacuous otherwise)
  S3  a class-member sized `operator delete(void*, unsigned)` is still the one called for that class
Needs aarch64-linux-gnu-g++; S2/S3 skip without it.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
import layout  # noqa: E402

FLAG = '-fno-sized-deallocation'
SRC = '''typedef __SIZE_TYPE__ size_t_;
struct P { int x; ~P(); };
void del_plain(P *p) { delete p; }
struct S { int x; static void operator delete(void *, size_t_); ~S(); };
void del_sized(S *p) { delete p; }
struct V { virtual ~V(); int x; };
void del_virt(V *p) { delete p; }
'''


def compile_asm(cxx, work, flags):
    src = os.path.join(work, 't.cpp')
    open(src, 'w').write(SRC)
    r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O1', '-S', '-fcheck-new'] + flags +
                       [src, '-o', '-'], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None, r.stderr.strip()[-300:]


def func_calls(asm, fn):
    m = re.search(r'^%s:.*?\.size\s+%s,' % (re.escape(fn), re.escape(fn)), asm, re.S | re.M)
    body = m.group(0) if m else ''
    return set(re.findall(r'\b(?:bl|b)\s+(\S+)', body))


def main():
    fails = 0
    files = ['tools/link/compile.sh', 'tools/census.sh', 'tools/static/flags.sh', 'toolchain/game/build.sh']
    miss = [f for f in files if FLAG not in open(os.path.join(PORT, f)).read()]
    if FLAG not in layout.SEMANTICS:
        miss.append('layout.SEMANTICS')
    print('S1 %s' % ('ok' if not miss else 'FAIL: missing in ' + ', '.join(miss)))
    fails += bool(miss)
    cxx = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
    if not shutil.which(cxx):
        print('skip S2/S3: no %s' % cxx)
        return 1 if fails else 0
    work = tempfile.mkdtemp(prefix='s2d-nosized-')
    try:
        base = [f for f in layout.SEMANTICS]
        asm, err = compile_asm(cxx, work, base)
        if asm is None:
            print('S2 FAIL: compile: ' + err)
            return 1
        plain, virt, sized = (func_calls(asm, n) for n in ('_Z9del_plainP1P', '_Z9del_virtP1V', '_Z9del_sizedP1S'))
        bad = [c for c in plain | virt if re.match(r'_ZdlPv[mj]$', c)]
        ok2 = '_ZdlPv' in plain and not bad
        print('S2 %s: del_plain calls %s' % ('ok' if ok2 else 'FAIL', sorted(plain)))
        fails += not ok2
        ok3 = any(re.match(r'_ZN1SdlEPv[mj]$', c) for c in sized) and not any(c.startswith('_ZdlPv') for c in sized)
        print('S3 %s: del_sized calls %s' % ('ok' if ok3 else 'FAIL', sorted(sized)))
        fails += not ok3
        ctl, _ = compile_asm(cxx, work, [f for f in base if f != FLAG])
        c = func_calls(ctl or '', '_Z9del_plainP1P')
        okc = any(re.match(r'_ZdlPv[mj]$', x) for x in c)
        print('S2 control (no flag) %s: %s' % ('ok' if okc else 'FAIL', sorted(c)))
        fails += not okc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
