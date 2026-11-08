#!/usr/bin/env python3
"""test_readfs.py - MSVC's __readfsdword intrinsic is a native builtin (EQ, farm baseline 588863: 0x00a5b5a0,
0x00b86de0, 0x00b86f20, 0x00b60790). The exe reads fs:[0x2c] inline (no call); the native calls
platform/win32/thread.cpp's __readfsdword, which a unit image does not link, so it hit a named stub.
  R1  libcalls.A64 maps __readfsdword to the readfsdword builtin
  R2  readfsdword(0x2c) is the slot array in the AArch64 private TLS block, whose slot 0 the engine writes
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13readfs'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import libcalls  # noqa: E402

FAIL = 0


def check(name, cond, got):
    global FAIL
    FAIL += not cond
    print('%s: %s (%s)' % (name, 'ok' if cond else 'FAIL', got))


def main():
    b = libcalls.A64.get('__readfsdword')
    check('R1', b == 'readfsdword', b)
    try:
        v = libcalls.run('readfsdword', None, [0x2c])
    except Exception as e:  # noqa: BLE001
        v = repr(e)
    check('R2', v == getattr(libcalls, 'A64_TLS_ARRAY', None) and 0x0b900000 <= (v if isinstance(v, int) else 0)
          < 0x0b910000, v)
    print('test_readfs: %d failed' % FAIL)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
