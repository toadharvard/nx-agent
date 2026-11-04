#!/usr/bin/env python3
"""V2 check of S2c-abi f3dc3fd (tools/link/vacall.parse_format): the argument widths MSVC 2008's printf reads for a
format string, MSVC x86 meanings (long 4 bytes, I = ptrdiff/size_t 4 bytes, I64 / ll 8, long double = double).

    python3 tests/v2/test_s2c_format.py

  P1  MSVC size prefixes: %Iu %Id %I32x -> 4-byte words, %I64d %lld -> 8, %ld %hd %hhd %lc -> 4
  P2  strings and pointers with prefixes: %hs %ls %ws %S %Z %p -> pointers; %%, flags, widths, precisions, `*`
  P3  unmodelled -> None: %n, positional %1$d, %Lf (8 bytes on MSVC, 16 here), an unknown conversion %y
"""
import os
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
import vacall  # noqa: E402

CASES = [
    ('P1', b'%Iu %Id %I32x', ['I32', 'I32', 'I32']),
    ('P1', b'%I64d %lld %I64X', ['I64', 'I64', 'I64']),
    ('P1', b'%ld %hd %hhd %lc %c', ['I32'] * 5),
    ('P2', b'%hs %ls %ws %S %Z %p %s', ['P'] * 7),
    ('P2', b'100%% %-08.3lf %+5d %#x', ['F64', 'I32', 'I32']),
    ('P2', b'%*.*f %.*s %*d', ['I32', 'I32', 'F64', 'I32', 'P', 'I32', 'I32']),
    ('P2', b'%e %g %E %G', ['F64'] * 4),
    ('P3', b'%d %n', None),
    ('P3', b'%1$d', None),
    ('P3', b'%Lf', None),
    ('P3', b'%y', None),
]


def main():
    fails = 0
    for case, fmt, want in CASES:
        got = vacall.parse_format(fmt)
        got = list(got) if got is not None else None
        ok = got == want
        print('%s %s %r -> %s%s' % ('ok' if ok else 'FAIL', case, fmt, got, '' if ok else ' (want %s)' % want))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
