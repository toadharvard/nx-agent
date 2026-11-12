"""PE loader: sections, base relocations (HIGHLOW) and the address-taken value list."""
import os, struct


class Image:
    def __init__(self, path, base=None):
        with open(path, 'rb') as f:
            self._load(f.read(), base)

    @classmethod
    def from_bytes(cls, data, base=None):
        """the image of the PE file bytes `data` (as Image(path) for a file with this content)"""
        img = cls.__new__(cls)
        img._load(data, base)
        return img

    def _load(self, data, base):
        """base: None = the image as stored (preferred base). Otherwise the image as the Windows loader maps it at `base`: sections at the new
        addresses and delta = base - preferred added to the dword of every HIGHLOW relocation site (u32/bytes/taken/rset/relocs speak `base` addresses).
        base None and env FNVNX_BASE (hex) set: base = FNVNX_BASE (tools that construct Image(path) pick the module's assigned base from the environment)."""
        if base is None and os.environ.get('FNVNX_BASE'):        # the one resolver: the image as mapped at the load base the loader assigned (default: the preferred base)
            base = int(os.environ['FNVNX_BASE'], 16)
        d = self.d = data
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        n = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        self.base = struct.unpack_from('<I', d, pe + 52)[0]
        self.secs = []
        self.secw = []       # per section: IMAGE_SCN_MEM_WRITE (a writable section's bytes are not a fact of the image)
        o = pe + 24 + opt
        for i in range(n):
            name = d[o:o + 8].rstrip(b'\0').decode()
            vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8)
            self.secs.append((name, self.base + va, vs, ro, rs))
            self.secw.append(bool(struct.unpack_from('<I', d, o + 36)[0] & 0x80000000))
            o += 40
        rr, rsz = struct.unpack_from('<II', d, pe + 24 + 96 + 40)
        self.relocs = []
        p = self.off(self.base + rr)
        end = p + rsz
        while p < end:
            page, bs = struct.unpack_from('<II', d, p)
            if not bs:
                break
            for i in range((bs - 8) // 2):
                e = struct.unpack_from('<H', d, p + 8 + 2 * i)[0]
                if e >> 12 == 3:
                    self.relocs.append(self.base + page + (e & 0xfff))
            p += bs
        pref = self.base
        self.nobytes = sum(1 for r in self.relocs if self.off(r) is None)      # HIGHLOW sites in the zero-filled tail of a section (no file bytes: the rebase cannot patch them)
        if base is not None and base != pref:
            delta = (base - pref) & 0xffffffff
            self.d = d = bytearray(d)
            for r in self.relocs:
                o = self.off(r)
                if o is not None: struct.pack_into('<I', d, o, (struct.unpack_from('<I', d, o)[0] + delta) & 0xffffffff)
            self.relocs = [(r + delta) & 0xffffffff for r in self.relocs]
            self.secs = [(n, (va + delta) & 0xffffffff, vs, ro, rs) for n, va, vs, ro, rs in self.secs]
            self.base = base
        self.relocs.sort()
        self.rset = set(self.relocs)
        # every reloc'd dword value -> sorted list (address-taken code candidates)
        self.taken = sorted(self.u32(r) for r in self.relocs)

    def off(self, a):
        for n, va, vs, ro, rs in self.secs:
            if va <= a < va + vs and a - va < rs:
                return ro + a - va

    def readonly(self, a, n):
        """True iff [a, a+n) lies in one section that is mapped without write permission and has file bytes there (the bytes are a fact of the image at run time)"""
        for (nm, va, vs, ro, rs), w in zip(self.secs, self.secw):
            if va <= a and a + n <= va + vs and a + n - va <= rs:
                return not w
        return False

    def u32(self, a):
        o = self.off(a)
        return struct.unpack_from('<I', self.d, o)[0] if o is not None else 0

    def bytes(self, a, n):
        o = self.off(a)
        return bytes(self.d[o:o + n])
