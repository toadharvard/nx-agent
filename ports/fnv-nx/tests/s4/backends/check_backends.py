#!/usr/bin/env python3
"""check_backends.py - S4's device metric: imported Win32/DirectX functions without a device-correct backend.

    python3 -I tests/s4/backends/check_backends.py [--imports <pe_imports.py output>] [--list]

tests/s4/backends/backends.tsv has one row per import of FalloutNV.exe (dll, name; ordinal imports as `#<n> <name>`)
plus rows for the COM interfaces the game drives (`IDirectSoundBuffer8 ...`), each with a class:
  device  reaches a host service the device implements (toolchain/host/*.inc: fs, threads, ticks, audren, HID)
  logic   in-process, nothing device-specific
  policy  the service does not exist on Switch; the note says what the game sees and why that is the decision
  open    no device-correct backend: host-only, stub or no-op where the game needs the service (the metric)
With --imports (make -C tests/s4 iat writes build-m32/imports.txt), every import must have exactly one row.
Prints the class counts and `device-open: <n>`; exit 1 on a missing, duplicate or malformed row.
"""
import argparse
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLASSES = ('device', 'logic', 'policy', 'open')

ORD = {'wsock32': {1: 'accept', 2: 'bind', 3: 'closesocket', 4: 'connect', 9: 'htons', 10: 'inet_addr',
                   11: 'inet_ntoa', 12: 'ioctlsocket', 13: 'listen', 15: 'ntohs', 16: 'recv', 18: 'select', 19: 'send',
                   21: 'setsockopt', 23: 'socket', 52: 'gethostbyname', 57: 'gethostname', 101: 'WSAAsyncSelect',
                   111: 'WSAGetLastError', 115: 'WSAStartup', 151: '__WSAFDIsSet'}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--imports')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    rows, bad = {}, 0
    for n, line in enumerate(open(os.path.join(HERE, 'backends.tsv')), 1):
        if line.startswith('#') or not line.strip():
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) != 6 or f[2] not in CLASSES or (f[2] in ('policy', 'open') and not f[5]):
            print('backends.tsv:%d: malformed (6 columns, a class of %s, a note for policy/open)' % (n, CLASSES))
            bad += 1
            continue
        key = (f[0], f[1].split(' ')[0] if f[1].startswith('#') else f[1])
        if key in rows:
            print('backends.tsv:%d: duplicate %s!%s' % (n, *key))
            bad += 1
        rows[key] = f
    if a.imports:
        seen = set()
        for line in open(a.imports):
            p = line.rstrip('\n').split('\t')
            if len(p) < 3:
                continue
            dll = p[1].lower()[:-4] if p[1].lower().endswith('.dll') else p[1].lower()
            dll = {'galaxywrp': 'galaxywrp'}.get(dll, dll)
            key = (dll, p[2])
            seen.add(key)
            if key not in rows:
                print('missing row: %s!%s' % key)
                bad += 1
        extra = [k for k in rows if not k[1].startswith('I') and k not in seen]
        for k in extra:
            print('row without an import: %s!%s' % k)
            bad += 1
    c = collections.Counter(f[2] for f in rows.values())
    if a.list:
        for f in rows.values():
            if f[2] == 'open':
                print('open\t%s!%s\t%s' % (f[0], f[1], f[5]))
    print('classes: ' + ', '.join('%s %d' % (k, c[k]) for k in CLASSES))
    print('device-open: %d' % c['open'])
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
