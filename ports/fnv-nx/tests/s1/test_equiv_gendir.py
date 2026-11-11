#!/usr/bin/env python3
"""tests/s1/test_equiv_gendir.py - equiv.gen_dir / cmath_dir called from many threads of one process (build_tree.py's
pool): every generated file is written under a name unique per writer and replaced atomically, so the content is
intact and no temp file stays. No MSVC, no Wine: a fake SDK tree.

    python3 -s -B tests/s1/test_equiv_gendir.py
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, '..', '..', 'tools', 'equiv')))
import equiv  # noqa: E402

FAILS = []


def check(name, cond, detail=''):
    if not cond:
        FAILS.append(name); print('FAIL', name, detail)


def main():
    tmp = Path(tempfile.mkdtemp())
    inc = tmp / 'msvc/SDK/Include'; inc.mkdir(parents=True)
    (inc / 'winerror.h').write_text(''.join('#define ERROR_E%d 1\n' % i for i in range(3000)))
    (inc / 'windows.h').write_text('#define CreateFile CreateFileA\n' + ''.join('void WINAPI Fn%d(int);\n' % i for i in range(3000)))
    cm = tmp / 'cmath'; cm.mkdir()
    for i in range(20): (cm / ('f%d.h' % i)).write_text('/* %d */\n' % i * 500)
    os.environ['XDG_CACHE_HOME'] = str(tmp / 'cache'); os.environ['FNV_MSVC9_ROOT'] = str(tmp / 'msvc')
    equiv.CMATH = cm
    errs, bad, N = [], [], 24
    start = threading.Barrier(N)

    def work():
        try:
            start.wait()
            d = equiv.gen_dir()
            for n in ('fnvnx_equiv_unaw.h', 'fnvnx_equiv_winerror.h', 'fnvnx_equiv_sdkhide.h', 'fnvnx_equiv_sdkunhide.h',
                      'fnvnx_equiv_sdk_windows.h'):
                t = (d / n).read_text()
                if n == 'fnvnx_equiv_winerror.h' and t.count('#undef ERROR_E') != 3000: bad.append((n, len(t)))
                if n == 'fnvnx_equiv_sdk_windows.h' and not t.rstrip().endswith('"'): bad.append((n, len(t)))
            c = equiv.cmath_dir()
            for f in cm.glob('*.h'):
                if (c / 'fnvnx_cmath' / f.name).read_bytes() != f.read_bytes(): bad.append(f.name)
        except Exception as e:
            errs.append(repr(e))
    ts = [threading.Thread(target=work) for _ in range(N)]
    for t in ts: t.start()
    for t in ts: t.join()
    check('no thread failed', not errs, errs[:3])
    check('every generated file complete for every reader', not bad, bad[:3])
    left = [str(p) for p in (tmp / 'cache').rglob('*') if p.is_file() and not p.name.endswith('.h')]
    check('no temp file stays', not left, left[:3])
    print('test_equiv_gendir: %s' % ('FAILED %d' % len(FAILS) if FAILS else 'ok'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
