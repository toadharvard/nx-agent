"""synth - byte builders for the synthetic fixtures of the link gate tests (own invented names, no game data).

    (ELF objects are never hand-built: link/tests/realobj.py builds them with the one build and mutate.py edits them.)
    coff(sections, symbols)             -> bytes of an i386 COFF object
          sections: [dict(name=bytes, chars=int, data=bytes, relocs=[(offset, symbol name, type)])]  (numbers 1..n)
          symbols: [dict(name=bytes, value=int, sec=int, type=int, cls=int, naux=int)]  (aux records are zero bytes)
    CV()                                -> builder of a `.debug$T` blob: add(kind, payload) -> type index, blob()
"""
import struct


def coff(sections, symbols):
    st = b''
    sym = b''
    index = {}
    i = 0
    for s in symbols:
        nm = s['name']
        if len(nm) > 8:
            raw = b'\0\0\0\0' + struct.pack('<I', 4 + len(st))
            st += nm + b'\0'
        else:
            raw = nm.ljust(8, b'\0')
        index.setdefault(nm, i)
        sym += raw + struct.pack('<IhHBB', s['value'], s['sec'], s['type'], s['cls'], s.get('naux', 0)) + b'\0' * 18 * s.get('naux', 0)
        i += 1 + s.get('naux', 0)
    nsym = i
    pos = 20 + 40 * len(sections)
    hdr, raw = b'', b''
    for s in sections:
        data, rels = s.get('data', b''), s.get('relocs', [])
        ptr = pos + len(raw) if data else 0
        raw += data
        relptr = pos + len(raw) if rels else 0
        for off, name, ty in rels:
            raw += struct.pack('<IIH', off, index[name], ty)
        nm = s['name']
        hdr += struct.pack('<8sIIIIIIHHI', nm.ljust(8, b'\0'), 0, 0, len(data), ptr, relptr, 0, len(rels), 0, s['chars'])
    symoff = pos + len(raw)
    return struct.pack('<HHIIIHH', 0x14c, len(sections), 0, symoff, nsym, 0, 0) + hdr + raw + sym + struct.pack('<I', 4 + len(st)) + st


class CV:
    def __init__(self):
        self.recs = []

    def add(self, kind, payload):
        self.recs.append((kind, payload))
        return 0x1000 + len(self.recs) - 1

    def blob(self):
        out = struct.pack('<I', 4)
        for k, p in self.recs:
            out += struct.pack('<HH', 2 + len(p), k) + p
        return out


def num(n):
    return struct.pack('<H', n) if n < 0x8000 else struct.pack('<HI', 0x8004, n)


def cstr(s):
    return s.encode() + b'\0'


def pad(b):
    k = (-len(b)) % 4
    return b + bytes(0xf0 + k - i for i in range(k))


def member(ty, off, name):
    return pad(struct.pack('<HHI', 0x150d, 3, ty) + num(off) + cstr(name))


def bclass(ty, off):
    return pad(struct.pack('<HHI', 0x1400, 3, ty) + num(off))


def vbclass(ty, vbtype=0x1000):
    return pad(struct.pack('<HHII', 0x1401, 3, ty, vbtype) + num(4) + num(1))


def vfunctab():
    return pad(struct.pack('<HHI', 0x1409, 0, 0x1000))


def nest(name):
    return pad(struct.pack('<HHI', 0x1510, 0, 0x74) + cstr(name))


def fieldlist(*items):
    return b''.join(items)


def bitfield(base, length, pos):
    return struct.pack('<IBB', base, length, pos) + b'\xf1\xf2'[:2]


def record(cv, kind_name, unique, fl, size, fwd=False, union=False):
    prop = 0x200 | (0x80 if fwd else 0)
    if union:
        p = struct.pack('<HHI', 0, prop, fl) + num(size) + cstr(unique[1:].split('@')[0]) + cstr(unique)
        return cv.add(0x1506, pad(p))
    p = struct.pack('<HHIII', 0, prop, fl, 0, 0) + num(size) + cstr(unique[1:].split('@')[0]) + cstr(unique)
    return cv.add(0x1505, pad(p))


def pointer(cv, size=4):
    return cv.add(0x1002, struct.pack('<II', 0x74, 0x0a | (size << 13)))


def debug_object(cv):
    """a COFF object holding only the .debug$T section of `cv`"""
    return coff([dict(name=b'.debug$T', chars=0x40000040, data=cv.blob())], [])


def write(d, name, data):
    import os
    p = os.path.join(d, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as fh:
        fh.write(data)
    return p
