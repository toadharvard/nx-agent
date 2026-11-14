"""pebuild - a small deterministic PE32 (i386) builder for the synthetic tests of the module loader (nothing is derived from a
game file).  Used by loader/synth/synth_modules.py and contract/tests/test_modules.py.

    img = Img(base, dll=True, entry_rva=0)
    img.section('.text', 0x1000, 0x400, 0x60000020)      # name, rva, raw size (multiple of 0x200), characteristics, vsize=None
    img.put(rva, bytes) / img.u32(rva, value)             # write into the section that holds rva
    img.reloc(rva, type=3)                                # an entry of the base relocation table (0 = ABSOLUTE padding, 3 = HIGHLOW)
    slots = img.imports(rva, [('kernel32.dll', ['Sleep', 5])])   # by name, or by ordinal (an int); -> [(slot rva, dll, name|int)]
    img.exports(rva, 'a.dll', [(ordinal, name | None, target rva | 'DLL.Name' forwarder)])
    img.tls(rva, start_rva, end_rva, index_rva, callbacks_rva, zero_fill, chars)   # also adds the relocations of its VAs
    data = img.build(reloc_rva=0x6000)
"""
import struct

HDRS = 0x400


def page_up(v):
    return (v + 0xfff) & ~0xfff


class Img:
    def __init__(self, base, dll=True, entry_rva=0, relocs_stripped=False):
        self.base, self.dll, self.entry_rva, self.stripped = base, dll, entry_rva, relocs_stripped
        self.secs = []          # [name, rva, bytearray, vsize, chars]
        self.dirs = {}
        self.relocs = []
        self.raw_relocs = None  # bytes: replaces the generated relocation table (for malformed input)
        self.stack = (0x100000, 0x1000)

    def section(self, name, rva, size, chars, vsize=None, fill=True):
        buf = bytearray(size)
        if fill:                # never zero: a zero-fill check means something
            for i in range(size):
                buf[i] = ((i * 7 + (i >> 8) * 13 + rva) & 0xfe) | 1
        self.secs.append([name, rva, buf, vsize or size, chars])
        return buf

    def _find(self, rva, n):
        for s in self.secs:
            if s[1] <= rva and rva + n <= s[1] + len(s[2]):
                return s
        raise ValueError('rva 0x%x+%d is in no section raw data' % (rva, n))

    def put(self, rva, data):
        s = self._find(rva, len(data))
        s[2][rva - s[1]:rva - s[1] + len(data)] = data

    def u32(self, rva, v):
        self.put(rva, struct.pack('<I', v & 0xffffffff))

    def reloc(self, rva, typ=3):
        self.relocs.append((rva, typ))

    def va_word(self, rva, target_rva):
        """a word holding the preferred-base VA of target_rva, with its HIGHLOW relocation"""
        self.u32(rva, self.base + target_rva)
        self.reloc(rva)

    def imports(self, rva, items):
        n = len(items)
        desc = rva
        cur = rva + 20 * (n + 1)
        tabs, strs = [], []
        for dll, fns in items:
            tabs.append((cur, cur + 4 * (len(fns) + 1)))
            cur += 8 * (len(fns) + 1)
        slots, descs = [], b''
        for (dll, fns), (ilt, iat) in zip(items, tabs):
            name_rva = cur
            self.put(cur, dll.encode() + b'\0')
            cur += len(dll) + 1 + (len(dll) + 1) % 2
            words = []
            for f in fns:
                if isinstance(f, int):
                    words.append(0x80000000 | f)
                else:
                    b = struct.pack('<H', 1) + f.encode() + b'\0'
                    b += b'\0' * (len(b) % 2)
                    self.put(cur, b)
                    words.append(cur)
                    cur += len(b)
            blob = b''.join(struct.pack('<I', w) for w in words) + b'\0\0\0\0'
            self.put(ilt, blob)
            self.put(iat, blob)
            descs += struct.pack('<IIIII', ilt, 0, 0, name_rva, iat)
            slots += [(iat + 4 * k, dll, f) for k, f in enumerate(fns)]
        self.put(desc, descs + b'\0' * 20)
        self.dirs[1] = (desc, 20 * (n + 1))
        self.imports_end = cur
        return slots

    def exports(self, rva, dllname, ents, ordbase=None):
        """ents: [(ordinal, name | None, target)]; target = rva (int) of a code/data export or a forwarder string"""
        ordbase = ordbase if ordbase is not None else min(o for o, _, _ in ents)
        nfunc = max(o for o, _, _ in ents) - ordbase + 1
        named = sorted((n, o) for o, n, _ in ents if n)
        aof = rva + 40
        aon = aof + 4 * nfunc
        aono = aon + 4 * len(named)
        cur = aono + 2 * len(named)
        cur += cur % 4
        fns = [0] * nfunc
        self.put(cur, dllname.encode() + b'\0')
        name_rva = cur
        cur += len(dllname) + 1
        nrvas = []
        for n, o in named:
            self.put(cur, n.encode() + b'\0')
            nrvas.append(cur)
            cur += len(n) + 1
        for o, n, t in ents:
            if isinstance(t, str):
                self.put(cur, t.encode() + b'\0')
                fns[o - ordbase] = cur
                cur += len(t) + 1
            else:
                fns[o - ordbase] = t
        self.put(aof, b''.join(struct.pack('<I', f) for f in fns))
        self.put(aon, b''.join(struct.pack('<I', r) for r in nrvas))
        self.put(aono, b''.join(struct.pack('<H', o - ordbase) for _, o in named))
        self.put(rva, struct.pack('<IIHHIIIIIII', 0, 0, 0, 0, name_rva, ordbase, nfunc, len(named), aof, aon, aono))
        self.dirs[0] = (rva, cur - rva)

    def tls(self, rva, start, end, index, callbacks, zero_fill, chars):
        self.put(rva, struct.pack('<IIIIII', self.base + start, self.base + end, self.base + index,
                                  self.base + callbacks if callbacks else 0, zero_fill, chars))
        for k in range(4):
            if k != 3 or callbacks:
                self.reloc(rva + 4 * k)
        self.dirs[9] = (rva, 24)

    def reloc_table(self):
        if self.raw_relocs is not None:
            return self.raw_relocs
        pages = {}
        for rva, typ in self.relocs:
            pages.setdefault(rva >> 12, []).append((rva & 0xfff, typ))
        out = b''
        for pg in sorted(pages):
            ents = [(typ << 12) | off for off, typ in sorted(pages[pg])]
            if len(ents) % 2:
                ents.append(0)      # ABSOLUTE padding
            out += struct.pack('<II', pg << 12, 8 + 2 * len(ents)) + b''.join(struct.pack('<H', e) for e in ents)
        return out

    def build(self, reloc_rva=None):
        secs = [list(s) for s in self.secs]
        rt = self.reloc_table()
        dirs = dict(self.dirs)
        if (self.relocs or self.raw_relocs is not None) and reloc_rva is not None:
            size = (len(rt) + 0x1ff) & ~0x1ff
            buf = bytearray(size)
            buf[:len(rt)] = rt
            secs.append(['.reloc', reloc_rva, buf, size, 0x42000040])
            dirs[5] = (reloc_rva, len(rt))
        off, offs = HDRS, []
        for s in secs:
            offs.append(off)
            off += len(s[2])
        soi = page_up(max(s[1] + max(s[3], len(s[2])) for s in secs))
        opt = bytearray(224)
        struct.pack_into('<HBB', opt, 0, 0x10b, 14, 0)
        struct.pack_into('<IIIII', opt, 4, 0x400, 0x400, 0, self.entry_rva, 0x1000)
        struct.pack_into('<II', opt, 24, 0x3000, self.base)
        struct.pack_into('<IIHHHHHHIIII', opt, 32, 0x1000, 0x200, 5, 1, 0, 0, 5, 1, 0, soi, HDRS, 0)
        struct.pack_into('<HH', opt, 68, 2, 0x8140)
        struct.pack_into('<IIII', opt, 72, self.stack[0], self.stack[1], 0x100000, 0x1000)
        struct.pack_into('<II', opt, 88, 0, 16)
        for k, (r, sz) in dirs.items():
            struct.pack_into('<II', opt, 96 + 8 * k, r, sz)
        hdr = bytearray(HDRS)
        hdr[0:2] = b'MZ'
        struct.pack_into('<I', hdr, 0x3c, 0x80)
        hdr[0x80:0x84] = b'PE\0\0'
        chars = 0x0102 | (0x2000 if self.dll else 0) | (1 if self.stripped else 0)
        struct.pack_into('<HHIIIHH', hdr, 0x84, 0x14c, len(secs), 0x5f000000, 0, 0, 224, chars)
        hdr[0x98:0x98 + 224] = opt
        for i, (n, r, buf, vs, c) in enumerate(secs):
            struct.pack_into('<8sIIIIIIHHI', hdr, 0x178 + 40 * i, n.encode(), vs, r, len(buf), offs[i], 0, 0, 0, 0, c)
        return bytes(hdr) + b''.join(bytes(s[2]) for s in secs)
