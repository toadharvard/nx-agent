#!/usr/bin/env python3
"""nxd9io.py - read and write NXD9, the D3D9-level capture format (docs/RENDER_NATIVE.md 8.3,
render/native/capture/d3d9cap.h), plus S16's extension records (tools/compare/README.md, "NXD9 extensions").

    for tag, payload in nxd9io.records(path): ...          tag as a 4-char str ('TEX ', 'DRWI', ...)
    w = nxd9io.Writer(path); w.rec('DEV ', struct.pack(...)); w.close()
    nxd9io.py stat <file.nxd9>                              counts per tag, frames, draws, bytes per tag

A journal from the capture layer (d9cap.c, *.nxd9j) has the same record syntax and magic "NXD9J\\0\\0\\1".
"""
import mmap, struct, sys

MAGIC = b'NXD9\0\0\0\1'
JMAGIC = b'NXD9J\0\0\1'


def tag_str(t):
    return struct.pack('<I', t).decode('latin-1')


def tag_u32(s):
    return struct.unpack('<I', s.ljust(4).encode('latin-1'))[0]


def records(path, with_offsets=False):
    """yields (tag, payload) or (tag, payload, offset of the payload in the file); payload is a memoryview into an
    mmap of the file (copy it with bytes() to keep it). Raises ValueError on a bad magic or a truncated record."""
    f = open(path, 'rb')
    try:
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    except ValueError:  # empty file
        f.close()
        raise ValueError(f'{path}: empty')
    mv = memoryview(m)
    if bytes(mv[:8]) not in (MAGIC, JMAGIC):
        raise ValueError(f'{path}: not NXD9')
    at, n = 8, len(mv)
    while at + 8 <= n:
        t, ln = struct.unpack_from('<II', mv, at)
        if at + 8 + ln > n:
            raise ValueError(f'{path}: truncated record {tag_str(t)!r} at {at}')
        p = mv[at + 8: at + 8 + ln]
        yield (tag_str(t), p, at + 8) if with_offsets else (tag_str(t), p)
        at += 8 + ((ln + 3) & ~3)


class Writer:
    def __init__(self, path, magic=MAGIC):
        self.f = open(path, 'wb')
        self.f.write(magic)

    def rec(self, tag, payload=b''):
        payload = bytes(payload)
        self.f.write(struct.pack('<II', tag_u32(tag), len(payload)))
        self.f.write(payload)
        self.f.write(b'\0' * ((-len(payload)) % 4))

    def u32(self, tag, *v):
        self.rec(tag, struct.pack('<%dI' % len(v), *v))

    def close(self):
        self.f.close()


def fnv1a64(data):
    h = 0xcbf29ce484222325
    for b in bytes(data):
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def stat(path):
    c, b = {}, {}
    for t, p in records(path):
        c[t] = c.get(t, 0) + 1
        b[t] = b.get(t, 0) + len(p)
    return {'records': c, 'bytes': b, 'frames': c.get('PRES', 0), 'draws': c.get('DRAW', 0) + c.get('DRWI', 0)}


def main():
    if len(sys.argv) != 3 or sys.argv[1] != 'stat':
        print(__doc__)
        return 2
    s = stat(sys.argv[2])
    print(f"frames {s['frames']} draws {s['draws']}")
    for t in sorted(s['records']):
        print(f"{t!r:8} {s['records'][t]:9d} {s['bytes'][t]:12d}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
