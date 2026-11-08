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
import collections
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

# (float class) The exe image the pinned literals are checked against: an object with .read(va, n) (exeimage.PE), set
# by a test, else FNVNX_EXE when that file loads, else None (then nothing is checked: the order of the literals stays)
EXE = None
_EXE_TRIED = False


def exe_image():
    global EXE, _EXE_TRIED
    if EXE is None and not _EXE_TRIED:
        _EXE_TRIED = True
        try:
            import os
            import config
            import exeimage
            if Path(config.EXE).exists():
                EXE = exeimage.load_pe(config.EXE, not os.environ.get('FNVNX_EXE_ANY'))
        except Exception:  # noqa: BLE001
            EXE = None
    return EXE


def exe_literals(lit, exe=None, stats=None):
    """{address: bytes} of pinned literals with the exe's bytes where they disagree. units.json maps several $SG
    strings of one object to one address (26 of 37 overlaps matched neither literal), so a literal may sit where the
    exe keeps other bytes and cover real constants behind it (0x00476b20). The union of a group of overlapping
    literals below the synthetic pool is pinned as the exe's bytes; a lone literal that differs is only counted. Without the exe image nothing changes. `stats` (a dict) counts
    {'differ': literals that differ from the exe, 'overlap': overlapping groups}."""
    exe = exe if exe is not None else exe_image()
    if exe is None:
        return lit
    stats = stats if stats is not None else {}
    stats.setdefault('differ', 0); stats.setdefault('overlap', 0)
    items = sorted((a, b) for a, b in lit.items() if a < LITERAL_POOL)
    out = {a: b for a, b in lit.items() if a >= LITERAL_POOL}
    groups, cur = [], None
    for a, b in items:
        if cur is not None and a < cur[1]:
            cur[1] = max(cur[1], a + len(b)); cur[2].append((a, b))
        else:
            cur = [a, a + len(b), [(a, b)]]; groups.append(cur)
    for lo, hi, members in groups:
        try:
            e = bytes(exe.read(lo, hi - lo))
        except Exception:  # noqa: BLE001
            for a, b in members:
                out[a] = b
            continue
        if len(members) > 1:
            stats['overlap'] += 1
            stats['differ'] += sum(1 for a, b in members if e[a - lo:a - lo + len(b)] != b)
            out[lo] = e
        else:
            a, b = members[0]
            if e != b:
                stats['differ'] += 1         # counted, not replaced: 3334 of 15143 pinned literals (tables whose
            out[a] = b                       # relocated words the object holds as 0, ...), a wider verdict change
    return out


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


def _sg_strings(coff):
    """{n: bytes through the terminator} of the object's numbered $SG literals"""
    out = {}
    for s in coff.symbols.values():
        if s['section'] > 0 and s['name'].startswith('$SG') and s['name'][3:].isdigit():
            b = coff.sections[s['section']]['data'][s['value']:]
            e = b.find(b'\0')
            out[int(s['name'][3:])] = bytes(b[:e + 1 if e >= 0 else len(b)])
    return out


def sg_shift(coff, unit, exe=None):
    """S1-rules (d25-sg): MSVC numbers a unit's string literals $SG<n> by an internal symbol counter, and the oracle
    objects (msvc/pristine, built with S11's force-included header) count 43 higher in every C++ unit than the build
    that wrote units.json (loader/synth/synth_image.py sg_shift). Looking the object's names up unshifted pinned each
    literal at a neighbour's address: 3,334 of 15,143 pinned literals differed from the exe, 37 overlapping groups
    (EQ). The shift is the delta d (object = units.json + d) under which the most of the unit's literals equal the
    exe's bytes at their units.json address; without the exe, the delta that maps the most names, accepted when it
    maps at least 90 %. 0 when unshifted or undecided."""
    mine = {int(k[3:]): int(v, 16) for k, v in unit.get('symbols', {}).items() if k.startswith('$SG') and k[3:].isdigit()}
    if not mine:
        return 0
    have = _sg_strings(coff)
    if not have:
        return 0
    deltas = collections.Counter(b - a for a in mine for b in have if abs(b - a) < 5000)
    if not deltas:
        return 0
    exe = exe if exe is not None else exe_image()
    if exe is not None:
        def score(d):
            n = 0
            for k, a in mine.items():
                b = have.get(k + d)
                if b is None:
                    continue
                try:
                    if bytes(exe.read(a, len(b))) == b:
                        n += 1
                except Exception:  # noqa: BLE001
                    pass
            return n
        best = max([0] + [d for d, _ in deltas.most_common(8)], key=lambda d: (score(d), d == 0))
        return best if score(best) > score(0) else 0
    d, n = deltas.most_common(1)[0]
    return d if d and n * 10 >= len(mine) * 9 else 0


class UnitImage:
    """All functions of one unit placed at their original addresses.

    unit: one entry of units.json (pristine). funcs[addr] = dict(symbol, size, section, start, code (relocated
    bytes), relocs [(offset, kind, target address or None, symbol name)], calls [(offset, target)], data_refs).
    """

    def __init__(self, coff, unit):
        self.coff, self.unit = coff, unit
        self.mappings = {k: int(v, 16) for k, v in unit['symbols'].items()}
        self.sg_shift = sg_shift(coff, unit)
        if self.sg_shift:
            # the object numbers its $SG literals d higher than the build that wrote units.json (S11's force-included
            # header): object $SG<n> is units.json's $SG<n-d>
            sg = {k: v for k, v in self.mappings.items() if k.startswith('$SG') and k[3:].isdigit()}
            for k in sg:
                del self.mappings[k]
            for k, v in sg.items():
                self.mappings['$SG%d' % (int(k[3:]) + self.sg_shift)] = v
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

    def _relocated(self, sec, start, blob, base=None):
        """blob (section bytes from `start`) with its DIR32 relocations applied: a read-only table of code or data
        pointers (a jump table, a callback table, a vftable-like array) holds 0 + addend in the object and the
        target's address in the exe (EQ reports/eq-sg: 91 pinned words were 00000000 where the exe has code
        addresses such as 0x00722b50, slot 0 of ??_7FUN_00722880 -> its ??_E). A target the unit cannot place takes
        the exe's word at that address when the exe is loaded (read-only data: the exe holds the relocated value),
        else keeps the object's bytes."""
        out = bytearray(blob)
        exe = exe_image() if base is not None else None
        for off, idx, kind in sec.get('relocs', ()):
            r = off - start
            if kind != DIR32 or r < 0 or r + 4 > len(out):
                continue
            tsym = self.coff.symbols.get(idx)
            if tsym is None:
                continue
            addend = struct.unpack_from('<I', out, r)[0]
            try:
                v = self.resolve(tsym, addend)
            except Exception:  # noqa: BLE001
                v = None
            if v is None and exe is not None:
                try:
                    v = struct.unpack('<I', bytes(exe.read(base + r, 4)))[0]
                except Exception:  # noqa: BLE001
                    v = None
            if v is not None:
                struct.pack_into('<I', out, r, v & 0xffffffff)
        return bytes(out)

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
                        blob = self._relocated(sec, s['value'], blob[:4096], base)
                    out[base] = bytes(blob)
        return exe_literals(out)

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
