"""coffobj.py - the x86 side's code: functions of an MSVC i386 COFF object relocated to their ORIGINAL addresses.

The decomp is byte-matching (every function of a unit compiles to the bytes of FalloutNV.exe 1.4.0.525 at the address
units.json gives it), so a function of the pristine MSVC 2008 object, loaded at its original address with every
relocation resolved through units.json, is the original function: the oracle. Resolution follows the decomp's own
compare rule: a symbol named in the unit's `symbols`/`functions` (and fold aliases) -> that address; a section or
local label inside a known function extent of the same section -> that address plus the offset; absolute symbols as
they are. Anything else is unresolved (reported; the function is not run).

Literal data the code reads from the image (string literals, `__real@`/`__xmm@` constants, read-only tables the unit
itself defines at a mapped address) is recovered from the object, so the shared world can hold those bytes at their
original addresses without the player's executable.
"""
import struct
from pathlib import Path

REL32, DIR32, DIR32NB, SECREL = 0x14, 0x06, 0x07, 0x0B
IMAGE_SCN_CNT_CODE, IMAGE_SCN_CNT_UNINIT = 0x20, 0x80
IMAGE_SCN_MEM_WRITE = 0x80000000
LITERAL_POOL = 0x02f00000      # synthetic addresses of literals units.json does not name (world.py: background range)
# Thread-local variables (difffuzz-16): units.json gives them image addresses in the exe's .tls template, which
# starts at 0x01272000 (the unit symbol ?__tls_index@@3PAEA there); a SECREL relocation is the variable's offset
# from that start. The code reaches the block through fs:[__tls_array = 0x2c][__tls_index] (x86emu.TLS_*), and
# __tls_index (0x0126fd98, units.json) is pinned to 0.
TLS_IMAGE_START = 0x01272000
TLS_INDEX = 0x0126fd98
_POOL_NEXT = LITERAL_POOL


class Coff:
    def __init__(self, path):
        self.path = str(path)
        d = self.data = Path(path).read_bytes()
        machine, count, _, symptr, symcount, opts, _ = struct.unpack_from('<HHIIIHH', d)
        if machine != 0x14c or opts:
            raise ValueError('%s: not an i386 COFF object' % path)
        self.sections = [None]
        for i in range(count):
            name, _, _, length, raw, relptr, _, relcount, _, flags = struct.unpack_from('<8sIIIIIIHHI', d, 20 + i * 40)
            if flags & 0x1000000:
                raise ValueError('extended relocation counts unsupported')
            self.sections.append(dict(
                name=name.rstrip(b'\0').decode('latin-1'), flags=flags,
                data=bytes(length) if raw == 0 or flags & IMAGE_SCN_CNT_UNINIT else d[raw:raw + length],
                relocs=[struct.unpack_from('<IIH', d, relptr + n * 10) for n in range(relcount)]))
        strptr = symptr + symcount * 18
        strings = d[strptr:]
        self.symbols, self.by_name = {}, {}
        i = 0
        while i < symcount:
            raw, value, sec, typ, storage, aux = struct.unpack_from('<8sIhHBB', d, symptr + i * 18)
            if raw[:4] == b'\0\0\0\0':
                off, = struct.unpack_from('<I', raw, 4)
                name = strings[off:strings.index(b'\0', off)].decode('latin-1')
            else:
                name = raw.rstrip(b'\0').decode('latin-1')
            s = dict(name=name, value=value, section=sec, type=typ, storage=storage, index=i)
            self.symbols[i] = s
            self.by_name.setdefault(name, []).append(s)
            i += 1 + aux

    def defined(self, name):
        c = [s for s in self.by_name.get(name, []) if s['section'] > 0]
        return c[0] if len(c) == 1 else None


class UnitImage:
    """All functions of one unit placed at their original addresses.

    unit: one entry of units.json (pristine). funcs[addr] = dict(symbol, size, section, start, code (relocated
    bytes), relocs [(offset, kind, target address or None, symbol name)], calls [(offset, target)], data_refs).
    """

    def __init__(self, coff, unit):
        self.coff, self.unit = coff, unit
        self.mappings = {k: int(v, 16) for k, v in unit['symbols'].items()}
        self.placements = {}
        self.errors = []
        self.synthetic = {}                       # (section, value) -> (pool address, bytes)
        for f in unit['functions']:
            a = int(f['address'], 16)
            for name in [f['symbol']] + f.get('fold_aliases', []):
                self.mappings[name] = a
                s = coff.defined(name)
                if s is not None:
                    self.placements[(s['section'], s['value'])] = (a, f['size'])
        self.funcs = {}
        for f in unit['functions']:
            a = int(f['address'], 16)
            try:
                self.funcs[a] = self._load(f, a)
            except (ValueError, KeyError, struct.error) as e:
                self.errors.append((a, str(e)))

    def resolve(self, sym, addend):
        if sym['name'] in self.mappings:
            return (self.mappings[sym['name']] + addend) & 0xffffffff
        if sym['section'] > 0:
            point = sym['value'] + addend
            for (sec, value), (address, size) in self.placements.items():
                if sec == sym['section'] and value <= point < value + size:
                    return address + point - value
        if sym['section'] == -1:
            return (sym['value'] + addend) & 0xffffffff
        lit = self._synthetic(sym)
        if lit is not None:
            return (lit + addend) & 0xffffffff
        return None

    def _synthetic(self, sym):
        """A read-only literal of this object that units.json does not name ($SG strings, ??_C@ copies): only its
        bytes matter, so it gets an address in the synthetic literal pool (pinned on both machines)."""
        if sym['section'] <= 0 or not sym['name'].startswith(('$SG', '??_C@', '__real@', '__xmm@')):
            return None
        sec = self.coff.sections[sym['section']]
        if sec['flags'] & (IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_WRITE | IMAGE_SCN_CNT_UNINIT):
            return None
        key = (sym['section'], sym['value'])
        if key not in self.synthetic:
            global _POOL_NEXT
            blob = sec['data'][sym['value']:]
            end = blob.find(b'\0\0' if sym['name'].startswith('??_C@_1') else b'\0')
            blob = blob[:end + 2 if sym['name'].startswith('??_C@_1') else end + 1] if end >= 0 else blob[:256]
            if sym['name'].startswith(('__real@', '__xmm@')):
                blob = bytes.fromhex(sym['name'].split('@', 1)[1])[::-1]
            self.synthetic[key] = (_POOL_NEXT, bytes(blob))
            _POOL_NEXT += (len(blob) + 15) & ~15
        return self.synthetic[key][0]

    def _load(self, f, a):
        s = self.coff.defined(f['symbol'])
        if s is None:
            raise ValueError('symbol not defined in object: ' + f['symbol'])
        sec = self.coff.sections[s['section']]
        start, size = s['value'], f['size']
        code = bytearray(sec['data'][start:start + size])
        if len(code) != size:
            raise ValueError('section shorter than the function')
        relocs, unresolved = [], []
        for off, idx, kind in sec['relocs']:
            if not start <= off < start + size:
                continue
            rel = off - start
            if rel + 4 > size:
                raise ValueError('relocation crosses the function end')
            tsym = self.coff.symbols[idx]
            if kind == REL32:
                addend, = struct.unpack_from('<i', code, rel)
                t = self.resolve(tsym, addend)
                if t is not None:
                    struct.pack_into('<i', code, rel, ((t - (a + rel + 4) + 2 ** 31) % 2 ** 32) - 2 ** 31)
            elif kind in (DIR32, DIR32NB):
                addend, = struct.unpack_from('<I', code, rel)
                t = self.resolve(tsym, addend)
                if t is not None:
                    struct.pack_into('<I', code, rel, t if kind == DIR32 else (t - 0x400000) & 0xffffffff)
            elif kind == SECREL:
                addend, = struct.unpack_from('<I', code, rel)
                v = self.resolve(tsym, addend)
                t = None if v is None else (v - TLS_IMAGE_START) & 0xffffffff
                if t is not None:
                    struct.pack_into('<I', code, rel, t)
                    self.uses_tls = True
            else:
                t = None
            if t is None:
                unresolved.append((rel, kind, tsym['name']))
            relocs.append((rel, kind, t, tsym['name']))
        return dict(symbol=f['symbol'], size=size, section=s['section'], start=start, code=bytes(code),
                    relocs=relocs, unresolved=unresolved, address=a)

    def literal_bytes(self):
        """{original address: bytes} for read-only data the unit's code refers to and the object holds:
        string literals ($SG, ??_C@), __real@/__xmm@ constants, other symbols defined in non-writable data sections
        that have a mapped address. These are pinned in the shared world."""
        out = {a: b for a, b in self.synthetic.values()}
        if getattr(self, 'uses_tls', False):
            out[self.mappings.get('__tls_index', TLS_INDEX)] = b'\0\0\0\0'    # the image's only TLS slot: 0
        for f in self.funcs.values():
            for rel, kind, t, name in f['relocs']:
                if t is None or kind != DIR32:
                    continue
                for s in self.coff.by_name.get(name, []):
                    if s['section'] <= 0 or name not in self.mappings:
                        continue
                    sec = self.coff.sections[s['section']]
                    if sec['flags'] & (IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_WRITE | IMAGE_SCN_CNT_UNINIT):
                        continue
                    base = self.mappings[name]
                    blob = sec['data'][s['value']:]
                    if name.startswith('__real@') or name.startswith('__xmm@'):
                        blob = bytes.fromhex(name.split('@', 1)[1])[::-1]
                    elif name.startswith(('??_C@', '$SG')):
                        end = blob.find(b'\0')
                        if name.startswith('??_C@_1'):   # wide string: up to the double NUL
                            end = next((i for i in range(0, len(blob) - 1, 2) if blob[i] == 0 and blob[i + 1] == 0),
                                       len(blob))
                            end += 1
                        blob = blob[:end + 1 if end >= 0 else len(blob)]
                    else:
                        nxt = [x['value'] for x in self.coff.symbols.values()
                               if x['section'] == s['section'] and x['value'] > s['value']]
                        blob = blob[:min(nxt) - s['value']] if nxt else blob
                        blob = blob[:4096]
                    out[base] = bytes(blob)
        return out

    def writable_initial(self):
        """{original address: bytes}: initial values of writable data the unit defines at mapped addresses (a seed
        for the fuzzer, never pinned)."""
        out = {}
        for name, a in self.mappings.items():
            s = self.coff.defined(name)
            if s is None:
                continue
            sec = self.coff.sections[s['section']]
            if not sec['flags'] & IMAGE_SCN_MEM_WRITE or sec['flags'] & IMAGE_SCN_CNT_CODE:
                continue
            nxt = [x['value'] for x in self.coff.symbols.values()
                   if x['section'] == s['section'] and x['value'] > s['value']]
            end = min(nxt) if nxt else len(sec['data'])
            out[a] = bytes(sec['data'][s['value']:min(end, s['value'] + 4096)])
        return out
