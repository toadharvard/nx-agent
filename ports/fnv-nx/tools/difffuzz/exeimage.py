"""exeimage.py - the x86 side's code taken from the owner's FalloutNV.exe image (FNVNX_ORACLE=exe, D23 b/c).

The default oracle (coffobj.py) is the pristine decomp compiled with MSVC 2008: it has no code for a function the
decomp lacks (the recon fleet's reconstructions, D23). The exe itself is the original of every function, so this
oracle runs the function's bytes straight from the image, at its address, under the same x86 emulator, with the same
seeds, probes and comparison: ExeImage is a drop-in for coffobj.UnitImage (engine.UnitCache.x86_unit).

  * Extents: the functions of the unit in the PREPARED tree's build/units.json (FNVNX_TREE), so REC1's appended
    recon functions are included (address, size, symbol).
  * Code: the image bytes [address, address + size). They are final: every address in them is the original one,
    so nothing is relocated. Relocation records are recovered for the harness's own uses (fuzz seeds from the
    data the function references, callee discovery, preload of callees GCC inlined): DIR32 from the image's base
    relocation table (IMAGE_REL_BASED_HIGHLOW inside the extent; the target is the word stored there), REL32 for
    every `call` / `jmp` rel32 whose target is outside the instruction's own function (capstone).
  * Data: the read-only bytes a DIR32 target points into (up to 4 KiB, within its section) are pinned on both
    machines, as coffobj pins the literals an object holds; writable targets seed the fuzzer with the image's
    initial values (zero in .bss), never pinned.
  * __tls_index (0x0126fd98) is pinned to 0, as for the COFF oracle.

The exe is the player's file (FNVNX_EXE, sha256 checked): agent-only, never committed; nothing derived from it is
written except the usual addresses, counts and divergence facts.
"""
import hashlib
import struct
from pathlib import Path

from coffobj import REL32, DIR32, TLS_INDEX

EXE_SHA256 = '89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe'   # GOG 1.4.0.525(a)
SCN_CODE, SCN_UNINIT, SCN_WRITE = 0x20, 0x80, 0x80000000
PIN_MAX = 4096


class PE:
    """A minimal PE32 reader: sections mapped at their virtual addresses, base relocations."""

    def __init__(self, path, check=True):
        data = Path(path).read_bytes()
        if check and hashlib.sha256(data).hexdigest() != EXE_SHA256:
            raise ValueError('%s is not FalloutNV.exe 1.4.0.525 (sha256 differs; FNVNX_EXE_ANY=1 skips the check)'
                             % path)
        if data[:2] != b'MZ':
            raise ValueError('not a PE image: %s' % path)
        pe, = struct.unpack_from('<I', data, 0x3c)
        if data[pe:pe + 4] != b'PE\0\0':
            raise ValueError('no PE header: %s' % path)
        nsec, = struct.unpack_from('<H', data, pe + 6)
        optsz, = struct.unpack_from('<H', data, pe + 20)
        opt = pe + 24
        if struct.unpack_from('<H', data, opt)[0] != 0x10b:
            raise ValueError('not PE32')
        self.base, = struct.unpack_from('<I', data, opt + 28)
        ndir, = struct.unpack_from('<I', data, opt + 92)
        dirs = [struct.unpack_from('<II', data, opt + 96 + 8 * k) for k in range(min(ndir, 16))]
        self.sections = []           # (va, vsize, flags, bytes padded to vsize)
        sh = opt + optsz
        for k in range(nsec):
            name, vsize, rva, rawsz, rawptr = struct.unpack_from('<8sIIII', data, sh + 40 * k)
            flags, = struct.unpack_from('<I', data, sh + 40 * k + 36)
            raw = data[rawptr:rawptr + min(rawsz, vsize or rawsz)]
            size = max(vsize, len(raw))
            self.sections.append((self.base + rva, size, flags, raw + bytes(size - len(raw)),
                                  name.rstrip(b'\0').decode('latin-1')))
        self.relocs = set()          # absolute addresses of HIGHLOW fixups
        if len(dirs) > 5 and dirs[5][0]:
            rva, size = dirs[5]
            blob = self.read(self.base + rva, size)
            p = 0
            while p + 8 <= len(blob):
                page, bsz = struct.unpack_from('<II', blob, p)
                if bsz < 8:
                    break
                for q in range(p + 8, p + bsz, 2):
                    e, = struct.unpack_from('<H', blob, q)
                    if e >> 12 == 3:
                        self.relocs.add(self.base + page + (e & 0xfff))
                p += bsz

    def section_of(self, va):
        for s in self.sections:
            if s[0] <= va < s[0] + s[1]:
                return s
        return None

    def read(self, va, n):
        s = self.section_of(va)
        if s is None:
            raise ValueError('address %08x outside the image' % va)
        off = va - s[0]
        return s[3][off:off + n]

    def word(self, va):
        return struct.unpack('<I', self.read(va, 4))[0]


_PE = {}


def load_pe(path, check=True):
    key = str(Path(path).resolve())
    if key not in _PE:
        _PE[key] = PE(path, check)
    return _PE[key]


class ExeImage:
    """All functions of one (prepared) unit, from the exe image; the interface of coffobj.UnitImage."""

    def __init__(self, pe, unit):
        """unit: an entry of the prepared build/units.json."""
        import capstone
        self.pe, self.unit = pe, unit
        self.mappings = {k: int(v, 16) for k, v in unit.get('symbols', {}).items()}
        self.errors, self.synthetic, self.uses_tls = [], {}, True
        self.funcs = {}
        cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        cs.skipdata = True          # a jump table inside the extent does not end the scan
        for f in unit['functions']:
            a = int(f['address'], 16)
            self.mappings[f['symbol']] = a
            try:
                self.funcs[a] = self._load(f, a, cs)
            except ValueError as e:
                self.errors.append((a, str(e)))

    def _load(self, f, a, cs):
        size = f['size']
        s = self.pe.section_of(a)
        if s is None or not s[2] & SCN_CODE:
            raise ValueError('not in an executable section: %08x' % a)
        code = self.pe.read(a, size)
        if len(code) != size:
            raise ValueError('function runs past its section')
        relocs = []
        for va in sorted(r for r in self.pe.relocs if a <= r and r + 4 <= a + size):
            relocs.append((va - a, DIR32, self.pe.word(va), ''))
        for ins in cs.disasm(code, a):
            if ins.mnemonic in ('call', 'jmp') and ins.size == 5 and ins.bytes[0] in (0xe8, 0xe9):
                t = int(ins.op_str, 0)
                if not a <= t < a + size:
                    relocs.append((ins.address + 1 - a, REL32, t, ''))
        relocs.sort()
        return dict(symbol=f['symbol'], size=size, section=0, start=a, code=bytes(code), relocs=relocs,
                    unresolved=[], address=a)

    def resolve(self, sym, addend):
        n = sym.get('name') if isinstance(sym, dict) else sym
        return (self.mappings[n] + addend) & 0xffffffff if n in self.mappings else None

    def literal_bytes(self):
        """{address: bytes} of read-only image data the unit's code points at (pinned on both machines)."""
        out = {TLS_INDEX: b'\0\0\0\0'}
        for f in self.funcs.values():
            for _, kind, t, _ in f['relocs']:
                if kind != DIR32:
                    continue
                s = self.pe.section_of(t)
                if s is None or s[2] & (SCN_CODE | SCN_WRITE | SCN_UNINIT):
                    continue
                out[t] = s[3][t - s[0]:min(t - s[0] + PIN_MAX, s[1])]
        return out

    def writable_initial(self):
        """{address: bytes}: initial values of writable image data the unit's code points at (a fuzz seed)."""
        out = {}
        for f in self.funcs.values():
            for _, kind, t, _ in f['relocs']:
                if kind != DIR32:
                    continue
                s = self.pe.section_of(t)
                if s is None or s[2] & SCN_CODE or not s[2] & SCN_WRITE:
                    continue
                out.setdefault(t, s[3][t - s[0]:min(t - s[0] + PIN_MAX, s[1])])
        return out
