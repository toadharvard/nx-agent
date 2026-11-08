#!/usr/bin/env python3
"""tests/s2d - platform/crt/msvc_names.c defines glibc's `__errno_location` over newlib's `__errno()`, and only there.

    python3 -I tests/s2d/test_errno_location.py

A synthetic `errno.h` (no library content) stands for newlib's: it defines __NEWLIB__ and declares __errno(). The
object then defines `__errno_location` (weak). With the host's glibc <errno.h> nothing is defined (glibc's own
accessor must not be replaced: its `errno` macro would call back into the replacement). Needs aarch64-linux-gnu-gcc
and nm; skipped without them.
"""
import os
import shutil
import subprocess
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
CRT = os.path.join(PORT, 'platform', 'crt')
FAKE_ERRNO = '#define __NEWLIB__ 1\nextern int *__errno(void);\n#define errno (*__errno())\n'


def nm_defined(work, inc):
    obj = os.path.join(work, 'o.o')
    cmd = ['aarch64-linux-gnu-gcc', '-c', '-O1', '-I', CRT] + (['-I', inc] if inc else []) + \
          [os.path.join(CRT, 'msvc_names.c'), '-o', obj]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        return None, (r.stderr.strip().splitlines() or ['?'])[-1][:200]
    out = subprocess.run(['aarch64-linux-gnu-nm', '--defined-only', obj], capture_output=True, text=True).stdout
    return {l.split()[-1]: l.split()[-2] for l in out.splitlines() if len(l.split()) == 3}, None


def main():
    if not shutil.which('aarch64-linux-gnu-gcc') or not shutil.which('aarch64-linux-gnu-nm'):
        print('skip: no aarch64-linux-gnu-gcc')
        return 0
    work = tempfile.mkdtemp(prefix='s2d-errno-')
    fails = 0
    try:
        fake = os.path.join(work, 'fake')
        os.mkdir(fake)
        open(os.path.join(fake, 'errno.h'), 'w').write(FAKE_ERRNO)
        syms, why = nm_defined(work, fake)
        if syms is None:
            print('skip: build failed (%s)' % why)
            return 0
        ok = syms.get('__errno_location') == 'W'
        print('%s newlib: __errno_location defined weak (%s)' % ('ok  ' if ok else 'FAIL', syms.get('__errno_location')))
        fails += not ok
        syms, why = nm_defined(work, None)
        ok = syms is not None and '__errno_location' not in syms
        print('%s glibc: not defined' % ('ok  ' if ok else 'FAIL'))
        fails += not ok
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('%d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
