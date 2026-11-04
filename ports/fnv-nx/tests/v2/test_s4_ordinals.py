#!/usr/bin/env python3
"""V2 check of S4 c47ba39 (platform/win32/exports.cpp g_ordinals): every (dll, ordinal) -> name row against the export
table of an independent build of that DLL: Wine's i386 wsock32.dll, xinput1_3.dll, dsound.dll (wine32 package).
Optionally (FNVNX_EXE) every ordinal the player's exe imports must be in the table.

    python3 tests/v2/test_s4_ordinals.py     (skip without Wine's i386 DLLs)

  O1  each table row names the export Wine's DLL has at that ordinal
  O2  every ordinal import of the exe (WSOCK32 21, XINPUT1_3 2, DSOUND 1) has a row
"""
import os
import re
import struct
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
WINE = os.environ.get('WINE_I386_DLLS', '/usr/lib/i386-linux-gnu/wine/i386-windows')


class PE:
    def __init__(self, path):
        self.b = open(path, 'rb').read()
        pe = struct.unpack_from('<I', self.b, 0x3c)[0]
        nsec, opt = struct.unpack_from('<H', self.b, pe + 6)[0], struct.unpack_from('<H', self.b, pe + 20)[0]
        self.dd = pe + 24 + 96          # PE32 data directories
        self.secs = [struct.unpack_from('<IIII', self.b, pe + 24 + opt + 40 * i + 8) for i in range(nsec)]

    def off(self, rva):
        for vsz, va, rsz, raw in self.secs:
            if va <= rva < va + max(vsz, rsz):
                return raw + rva - va
        raise KeyError(hex(rva))

    def cstr(self, rva):
        o = self.off(rva)
        return self.b[o:self.b.index(b'\0', o)].decode()

    def exports(self):
        rva = struct.unpack_from('<I', self.b, self.dd)[0]
        o = self.off(rva)
        base, nfun, nname, afun, aname, aord = struct.unpack_from('<IIIIII', self.b, o + 16)
        out = {}
        for i in range(nname):
            nm = self.cstr(struct.unpack_from('<I', self.b, self.off(aname) + 4 * i)[0])
            out[base + struct.unpack_from('<H', self.b, self.off(aord) + 2 * i)[0]] = nm
        return out

    def ordinal_imports(self):
        o, out = self.off(struct.unpack_from('<I', self.b, self.dd + 8)[0]), {}
        while True:
            oft, _, _, name, ft = struct.unpack_from('<IIIII', self.b, o)
            o += 20
            if not name:
                return out
            t = self.off(oft or ft)
            while True:
                v = struct.unpack_from('<I', self.b, t)[0]
                t += 4
                if not v:
                    break
                if v & 0x80000000:
                    out.setdefault(self.cstr(name).lower().replace('.dll', ''), set()).add(v & 0xffff)


def table():
    src = open(os.path.join(PORT, 'platform', 'win32', 'exports.cpp')).read()
    body = src[src.index('g_ordinals[]'):]
    body = body[:body.index('};')]
    return [(d, int(n), nm) for d, n, nm in re.findall(r'\{"(\w+)",\s*(\d+),\s*"(\w+)"\}', body)]


def main():
    rows = table()
    dlls = sorted({d for d, _, _ in rows})
    if not all(os.path.isfile(os.path.join(WINE, d + '.dll')) for d in dlls):
        print('skip: no Wine i386 DLLs in %s' % WINE)
        return 0
    fails = 0
    exp = {d: PE(os.path.join(WINE, d + '.dll')).exports() for d in dlls}
    bad = [(d, n, nm, exp[d].get(n)) for d, n, nm in rows if exp[d].get(n) != nm]
    print('%s O1 %d rows, %d differ from Wine %s' % ('ok' if not bad else 'FAIL', len(rows), len(bad), bad[:6]))
    fails += bool(bad)
    exe = os.environ.get('FNVNX_EXE')
    if exe and os.path.isfile(exe):
        have = {(d, n) for d, n, _ in rows}
        miss = sorted((d, n) for d, ns in PE(exe).ordinal_imports().items() for n in ns if (d, n) not in have)
        print('%s O2 ordinal imports of the exe without a row: %s' % ('ok' if not miss else 'FAIL', miss))
        fails += bool(miss)
    else:
        print('skip O2: no FNVNX_EXE')
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
