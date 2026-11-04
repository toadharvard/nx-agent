"""PE32 parsing and the mapped-image model of the fnvnx data-image loader, in Python.

Mirrors loader/src/{pe,image,imports}.c closely enough that loader/tools/scan_codeptrs.py gives the same result
as the C runtime scanner (tests/s3 checks that). All address arithmetic is done modulo 2**32 like the C code.
"""
import hashlib
import struct

M32 = 0xffffffff
PAGE = 0x1000

SCN_CNT_CODE = 0x00000020
SCN_DISCARDABLE = 0x02000000
SCN_MEM_EXECUTE = 0x20000000
SCN_MEM_WRITE = 0x80000000

DIR_EXPORT, DIR_IMPORT, DIR_RESOURCE, DIR_EXCEPTION, DIR_SECURITY, DIR_BASERELOC, DIR_DEBUG = 0, 1, 2, 3, 4, 5, 6
DIR_TLS, DIR_LOAD_CONFIG, DIR_BOUND_IMPORT, DIR_IAT, DIR_DELAY_IMPORT, DIR_COM = 9, 10, 11, 12, 13, 14

HEADERS = 0xffff
ORIG_IMAGE_BASE = 0x00400000


class PEError(Exception):
    """code is the C loader's error name (FORMAT, UNSUPPORTED, DRM, ...)."""

    def __init__(self, code, msg=''):
        super().__init__('%s: %s' % (code, msg) if msg else code)
        self.code = code


def u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def page_up(v):
    return (v + PAGE - 1) & ~(PAGE - 1)


class Section:
    def __init__(self, raw):
        self.name = raw[:8].rstrip(b'\0').decode('latin-1')
        self.vsize, self.rva, self.raw_size, self.raw_off = struct.unpack_from('<IIII', raw, 8)
        self.characteristics = u32(raw, 36)

    @property
    def vsize_eff(self):
        return self.vsize or self.raw_size

    @property
    def is_code(self):
        return bool(self.characteristics & (SCN_CNT_CODE | SCN_MEM_EXECUTE))


class PE:
    def __init__(self, data):
        self.data = data = bytes(data)
        size = len(data)
        if size < 64 or data[:2] != b'MZ':
            raise PEError('FORMAT', 'no MZ header')
        lfanew = u32(data, 0x3c)
        if lfanew < 0x40 or lfanew > 0x1000 or lfanew + 24 > size or data[lfanew:lfanew + 4] != b'PE\0\0':
            raise PEError('FORMAT', 'no PE header')
        self.machine, self.nsections, self.timestamp = struct.unpack_from('<HHI', data, lfanew + 4)
        optsize, self.characteristics = struct.unpack_from('<HH', data, lfanew + 20)
        if self.machine != 0x14c or not self.characteristics & 2:
            raise PEError('UNSUPPORTED', 'not an i386 executable')
        if optsize < 96 or optsize > 224 or not 0 < self.nsections <= 96 or lfanew + 24 + optsize > size:
            raise PEError('FORMAT', 'bad optional header')
        opt = data[lfanew + 24:lfanew + 24 + optsize]
        if u16(opt, 0) != 0x10b:
            raise PEError('UNSUPPORTED', 'not PE32')
        self.entry_rva = u32(opt, 16)
        self.image_base, self.section_align, self.file_align = struct.unpack_from('<III', opt, 28)
        self.size_of_image, self.size_of_headers, self.checksum = struct.unpack_from('<III', opt, 56)
        self.dll_characteristics = u16(opt, 70)
        if self.section_align != 0x1000:
            raise PEError('UNSUPPORTED', 'section alignment')
        fa = self.file_align
        if fa < 0x200 or fa > 0x10000 or fa & (fa - 1) or self.image_base & 0xffff:
            raise PEError('FORMAT', 'alignment/base')
        # Windows rounds SizeOfImage up to the section alignment (the GOG FalloutNV.exe declares 0x010089e4)
        if self.size_of_image:
            self.size_of_image = (self.size_of_image + 0xfff) & ~0xfff
        if not self.size_of_image or self.image_base + self.size_of_image > 1 << 32:
            raise PEError('FORMAT', 'size of image')
        ndirs = min(u32(opt, 92), 16, (optsize - 96) // 8)
        self.dirs = [(0, 0)] * 16
        for i in range(ndirs):
            rva, dsize = struct.unpack_from('<II', opt, 96 + 8 * i)
            self.dirs[i] = (rva, dsize)
            if i != DIR_SECURITY and dsize and not (rva <= self.size_of_image and dsize <= self.size_of_image - rva):
                raise PEError('FORMAT', 'data directory %d' % i)
        self.ndirs = ndirs
        shoff = lfanew + 24 + optsize
        if shoff + 40 * self.nsections > size or not shoff + 40 * self.nsections <= self.size_of_headers <= size:
            raise PEError('FORMAT', 'section table')
        self.sections = []
        self.wrapped = False
        lo, hi = None, 0
        for i in range(self.nsections):
            s = Section(data[shoff + 40 * i:shoff + 40 * i + 40])
            if s.rva & 0xfff or s.rva < self.size_of_headers or not s.vsize_eff:
                raise PEError('FORMAT', 'section %s' % s.name)
            end = s.rva + s.vsize_eff
            if end > self.size_of_image:
                raise PEError('FORMAT', 'section %s beyond image' % s.name)
            if self.sections:
                p = self.sections[-1]
                if s.rva < page_up(p.rva + p.vsize_eff):
                    raise PEError('FORMAT', 'sections overlap')
            if s.raw_size and s.raw_off + s.raw_size > size:
                raise PEError('FORMAT', 'section %s raw data' % s.name)
            if s.name == '.bind':          # SteamStub: its own code; .text is encrypted, the data is not
                self.wrapped = True
            elif s.is_code:
                lo = s.rva if lo is None else min(lo, s.rva)
                hi = max(hi, end)
            self.sections.append(s)
        if lo is None:
            raise PEError('UNSUPPORTED', 'no code section')
        self.text_lo_rva, self.text_hi_rva = lo, hi
        for s in self.sections:
            if not s.is_code and s.rva < hi and s.rva + s.vsize_eff > lo:
                raise PEError('UNSUPPORTED', 'code span covers data section %s' % s.name)

    def section_stats(self, i):
        """(bytes, zeros, chi2, plain) of a section's raw data, as fnvnx_pe_section_stats"""
        s = self.sections[i]
        raw = self.data[s.raw_off:s.raw_off + min(s.raw_size, s.vsize_eff)]
        n = len(raw)
        hist = [0] * 256
        for b in raw:
            hist[b] += 1
        chi2 = (sum(c * c for c in hist) * 256 // n - n) if n else 0
        plain = not n or (chi2 > 2000 and hist[0] * 100 >= n)
        return n, hist[0], chi2, plain

    def dir(self, i):
        return self.dirs[i] if i < self.ndirs else (0, 0)

    def rva_to_off(self, rva, size):
        if rva + size <= self.size_of_headers:
            return rva
        for s in self.sections:
            avail = min(s.raw_size, s.vsize_eff)
            if s.rva <= rva and rva + size <= s.rva + avail:
                return s.raw_off + rva - s.rva
        return None

    def read_rva(self, rva, size):
        off = self.rva_to_off(rva, size)
        return None if off is None else self.data[off:off + size]

    def check_probes(self, probes, need=0):
        """probes: [(va, size, sha256 hex)]. Returns (ok, matched)."""
        need = need or min(12, len(probes))
        ok = 0
        for va, size, digest in probes:
            if size > 4096 or va < self.image_base:
                continue
            b = self.read_rva(va - self.image_base, size)
            ok += b is not None and hashlib.sha256(b).hexdigest() == digest.lower()
        return bool(probes) and ok >= need, ok


class Mapping:
    __slots__ = ('va', 'size', 'file_bytes', 'prot', 'sec', 'data', 'words', 'elig')   # words, elig: scanner


class Image:
    """The data sections mapped at load_base + rva. Addresses are current VAs; original = current - delta."""

    def __init__(self, pe, load_base=None, headers=False):
        self.pe = pe
        self.load_base = pe.image_base if load_base is None else load_base
        self.delta = (self.load_base - pe.image_base) & M32
        self.maps = []
        if headers:
            self._add(self.load_base, page_up(pe.size_of_headers), pe.data[:pe.size_of_headers], 1, HEADERS)
        for i, s in enumerate(pe.sections):
            if s.characteristics & (SCN_CNT_CODE | SCN_MEM_EXECUTE | SCN_DISCARDABLE):
                continue
            nfile = min(s.raw_size, s.vsize_eff)
            prot = 1 | (2 if s.characteristics & SCN_MEM_WRITE else 0)
            self._add(self.load_base + s.rva, page_up(s.vsize_eff), pe.data[s.raw_off:s.raw_off + nfile], prot, i)
        self.starts = [m.va for m in self.maps]
        self.relocs = []
        self.relocated = False

    def _add(self, va, size, content, prot, sec):
        m = Mapping()
        m.va, m.size, m.file_bytes, m.prot, m.sec = va, size, len(content), prot, sec
        m.data = bytearray(size)
        m.data[:len(content)] = content
        self.maps.append(m)

    def mapping(self, va):
        import bisect
        i = bisect.bisect_right(self.starts, va) - 1
        if i >= 0 and va - self.maps[i].va < self.maps[i].size:
            return self.maps[i]
        return None

    def ptr(self, va, size):
        """(mapping, offset) if [va, va+size) is wholly in one mapping."""
        va &= M32
        m = self.mapping(va)
        if m is None or size > m.size - (va - m.va):
            return None
        return m, va - m.va

    def read(self, va, size):
        p = self.ptr(va, size)
        return None if p is None else bytes(p[0].data[p[1]:p[1] + size])

    def check_bytes(self, probes, percent=90):
        """probes: [(orig va, bytes)] -> (ok, matched), as fnvnx_image_check_bytes"""
        ok = sum(1 for va, b in probes if b and self.read(va + self.delta, len(b)) == b)
        return bool(probes) and ok * 100 >= len(probes) * percent, ok

    def vtable_slots(self, vftable, cap=4096):
        """original targets of the vftable at original VA vftable (before fixups), as fnvnx_image_vtable_slots"""
        lo = (self.load_base + self.pe.text_lo_rva) & M32
        hi = (self.load_base + self.pe.text_hi_rva) & M32
        out = []
        while len(out) < cap:
            v = self.read32(vftable + self.delta + 4 * len(out))
            if v is None or not lo <= v < hi:
                break
            out.append((v - self.delta) & M32)
        return out

    def read32(self, va):
        p = self.ptr(va, 4)
        return None if p is None else u32(p[0].data, p[1])

    def write32(self, va, v):
        m, o = self.ptr(va, 4)
        struct.pack_into('<I', m.data, o, v & M32)

    def string(self, va, max_len):
        """(bytes, length) of the NUL-terminated string at va, or None (as fnvnx_image_str)."""
        va &= M32
        m = self.mapping(va)
        if m is None:
            return None
        o = va - m.va
        avail = min(m.size - o, max_len)
        end = m.data.find(b'\0', o, o + avail)
        if end < 0:
            return None
        return bytes(m.data[o:end]), end - o

    # -- relocations --
    def load_relocs(self):
        rva, size = self.pe.dir(DIR_BASERELOC)
        self.relocs = []
        if not size:
            return
        buf = self.pe.read_rva(rva, size)
        if buf is None:
            raise PEError('RANGE', 'relocations not in the file')
        out, pos = [], 0
        while pos + 8 <= size:
            page, bsize = struct.unpack_from('<II', buf, pos)
            if bsize < 8 or bsize > size - pos or bsize & 1:
                raise PEError('FORMAT', 'relocation block')
            for k in range(8, bsize - 1, 2):
                e = u16(buf, pos + k)
                if e >> 12 == 0:
                    continue
                if e >> 12 != 3:
                    raise PEError('UNSUPPORTED', 'relocation type %d' % (e >> 12))
                va = (self.load_base + page + (e & 0xfff)) & M32
                if self.ptr(va, 4) is not None:
                    out.append(va)
            pos += bsize
        self.relocs = sorted(out)

    def relocate(self):
        if not self.delta or self.relocated:
            return 0
        if self.pe.characteristics & 1 or not self.pe.dir(DIR_BASERELOC)[1]:
            raise PEError('UNSUPPORTED', 'relocations stripped')
        if not self.relocs:
            self.load_relocs()
        for va in self.relocs:
            self.write32(va, self.read32(va) + self.delta)
        self.relocated = True
        return len(self.relocs)

    # -- imports --
    def walk_imports(self):
        """[(kind, ...)] as loader/src/imports.c walk_imports: ('dll', name_va, len, delay) and
        ('thunk', dll, name, ordinal, delay, terminator, slot, lookup, hn, hnlen). Raises PEError on malformed
        tables."""
        out = []
        base = self.load_base
        rva, size = self.pe.dir(DIR_IMPORT)
        d = 0
        while size and d + 20 <= size:
            desc = self.read(base + rva + d, 20)
            if desc is None:
                raise PEError('UNSUPPORTED', 'import descriptors not mapped')
            oft, _, _, name, ft = struct.unpack('<IIIII', desc)
            if not oft and not name and not ft:
                break
            s = self.string(base + name, 256)
            if s is None or not ft:
                raise PEError('FORMAT', 'import descriptor')
            out.append(('dll', (base + name) & M32, s[1], False))
            lookup = oft or ft
            j = 0
            while True:
                t = self.read32(base + lookup + 4 * j)
                if t is None or self.ptr(base + ft + 4 * j, 4) is None:
                    raise PEError('FORMAT', 'thunk array')
                slot, lk = (base + ft + 4 * j) & M32, (base + lookup + 4 * j) & M32
                if not t:
                    out.append(('thunk', s[0], None, 0, False, True, slot, lk, 0, 0))
                    break
                fn, hn, hnlen, ordv = None, 0, 0, None
                if t & 0x80000000:
                    ordv = t & 0xffff
                else:
                    if self.ptr(base + t, 2) is None:
                        raise PEError('FORMAT', 'hint/name')
                    fs = self.string(base + t + 2, 512)
                    if fs is None:
                        raise PEError('FORMAT', 'hint/name')
                    fn, hn, hnlen = fs[0], (base + t) & M32, 2 + fs[1] + 1
                out.append(('thunk', s[0], fn, ordv, False, False, slot, lk, hn, hnlen))
                j += 1
            d += 20
        rva, size = self.pe.dir(DIR_DELAY_IMPORT)
        d = 0
        while size and d + 32 <= size:
            desc = self.read(base + rva + d, 32)
            if desc is None:
                raise PEError('UNSUPPORTED', 'delay descriptors not mapped')
            attrs, name, _, iat, intt = struct.unpack_from('<IIIII', desc)
            if not name:
                break
            rel = base if attrs & 1 else 0
            s = self.string(rel + name, 256)
            if s is None or not iat or not intt:
                raise PEError('FORMAT', 'delay descriptor')
            out.append(('dll', (rel + name) & M32, s[1], True))
            j = 0
            while True:
                t = self.read32(rel + intt + 4 * j)
                if t is None or self.ptr(rel + iat + 4 * j, 4) is None:
                    raise PEError('FORMAT', 'delay thunk array')
                slot, lk = (rel + iat + 4 * j) & M32, (rel + intt + 4 * j) & M32
                if not t:
                    out.append(('thunk', s[0], None, 0, True, True, slot, lk, 0, 0))
                    break
                fn, hn, hnlen, ordv = None, 0, 0, None
                if t & 0x80000000:
                    ordv = t & 0xffff
                else:
                    if self.ptr(rel + t, 2) is None:
                        raise PEError('FORMAT', 'hint/name')
                    fs = self.string(rel + t + 2, 512)
                    if fs is None:
                        raise PEError('FORMAT', 'hint/name')
                    fn, hn, hnlen = fs[0], (rel + t) & M32, 2 + fs[1] + 1
                out.append(('thunk', s[0], fn, ordv, True, False, slot, lk, hn, hnlen))
                j += 1
            d += 32
        return out

    def exclusions(self):
        """Merged sorted [(lo, hi)] of loader structures the scanner skips (as fnvnx_collect_exclusions)."""
        r = []

        def add(lo, size):
            if size:
                lo &= M32
                r.append((lo, min(lo + size, M32)))

        pe, base = self.pe, self.load_base
        try:
            walk = self.walk_imports()
        except PEError:
            walk = []
        for i in range(pe.ndirs):
            if i != DIR_SECURITY:
                add(base + pe.dirs[i][0], pe.dirs[i][1])
        for e in walk:
            if e[0] == 'dll':
                add(e[1], e[2] + 1)
            else:
                _, _, _, _, _, _, slot, lk, hn, hnlen = e
                add(slot, 4)
                add(lk, 4)
                if hn:
                    add(hn, hnlen)
        drva, dsize = pe.dir(DIR_DEBUG)
        k = 0
        while k + 28 <= dsize:
            d = self.read(base + drva + k, 28)
            if d is not None and u32(d, 20):
                add(base + u32(d, 20), u32(d, 16))
            k += 28
        lrva, lsize = pe.dir(DIR_LOAD_CONFIG)
        lc = self.read(base + lrva, lsize) if lsize else None
        if lc is not None:
            lsize = min(lsize, u32(lc, 0))
            if lsize >= 72 and u32(lc, 68) < 0x10000000:
                add(u32(lc, 64), 4 * u32(lc, 68))
            if lsize >= 92 and u32(lc, 84) < 0x10000000:
                stride = 4 + ((u32(lc, 88) >> 28) & 0xf)
                add(u32(lc, 80), (stride * u32(lc, 84)) & M32)
        r.sort(key=lambda x: x[0])
        merged = []
        for lo, hi in r:
            if merged and lo <= merged[-1][1]:
                if hi > merged[-1][1]:
                    merged[-1] = (merged[-1][0], hi)
            else:
                merged.append((lo, hi))
        return merged
