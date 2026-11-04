"""exevft.py - the vftables of the player's FalloutNV.exe, found through MSVC's RTTI (docs/LINK.md, "Vftables from
the player's exe").

Every MSVC vftable with RTTI is preceded by a pointer to its complete object locator (COL: signature 0, the vfptr's
offset in the complete object, the TypeDescriptor `.?AV<class>@@` of the complete class, its hierarchy descriptor).
Scanning the image's data for (COL pointer, .text word) pairs gives every such vftable with its class, its offset
(0: the primary vftable, else the base subobject it serves) and its slots (the .text words from the vftable on, as
loader/tools/fnvnx_pe.Image.vtable_slots reads them). The link uses it to pair each image vftable with the native
vtable of the class the exe says it belongs to, and to fill the slots a native vtable lacks.

    scan(exe path) -> {vftable address: Vft(td, off, bases, slots)}
        td: the TypeDescriptor name ('.?AVFoo@@'), off: the COL offset, bases: [(TypeDescriptor name, mdisp)] of the
        hierarchy (the class itself first), slots: [original address of slot k]
    msvc_name(td, base_td=None) -> the MSVC vftable name ('??_7Foo@@6B@'), which llvm-undname spells as
        "const Foo::`vftable'" (templates included)
    load(exe path, cache path) -> scan() through a JSON cache keyed on the exe's size, mtime and this format

The exe is the player's own (agent-only, D3): it is read, never copied, and the cache is derived data (link out/,
local only). Same scan as tools/difffuzz/vtmodel.py (S13), which reads RTTI class names only.
"""
import collections
import re
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'loader', 'tools'))

FORMAT = 'fnvnx-exevft/1'
TEXT = (0x00401000, 0x00fdf000)
DATA = (0x00fdf000, 0x02100000)

Vft = collections.namedtuple('Vft', 'td off bases slots')


def exe_path(explicit=None):
    """The player's exe: the argument, else FNVNX_EXE, else PLAYER_EXE; None when no such regular file exists."""
    for p in (explicit, os.environ.get('FNVNX_EXE'), os.environ.get('PLAYER_EXE')):
        if p and p != 'none' and os.path.isfile(p):
            return os.path.abspath(p)
    return None


def scan(path):
    import fnvnx_pe
    pe = fnvnx_pe.PE(open(path, 'rb').read())
    img = fnvnx_pe.Image(pe)

    def cstr(a):
        s = img.string(a, 512)
        return s[0].decode('latin-1') if s else None

    cols = {}

    def col(a):
        if a in cols:
            return cols[a]
        got = None
        w = img.read(a, 20)
        if w is not None:
            sig, off, _, td, chd = struct.unpack('<IIIII', w)
            name = cstr(td + 8) if sig == 0 and img.read(td, 8) is not None else None
            if name and name.startswith('.?A'):
                bases = []
                h = img.read(chd, 16)
                if h is not None:
                    hsig, _, nb, bca = struct.unpack('<IIII', h)
                    if hsig == 0 and 0 < nb < 128:
                        for i in range(nb):
                            bcd = img.read32(bca + 4 * i)
                            b = img.read(bcd, 12) if bcd is not None else None
                            if b is None:
                                break
                            btd, _, mdisp = struct.unpack('<IIi', b)
                            bn = cstr(btd + 8)
                            if bn and bn.startswith('.?A'):
                                bases.append((bn, mdisp))
                got = (name, off, bases)
        cols[a] = got
        return got

    out = {}
    for m in img.maps:
        n = m.file_bytes // 4
        for i in range(n - 1):
            w = struct.unpack_from('<I', m.data, 4 * i)[0]
            if not DATA[0] <= w < DATA[1]:
                continue
            nxt = struct.unpack_from('<I', m.data, 4 * i + 4)[0]
            if not TEXT[0] <= nxt < TEXT[1]:
                continue
            c = col(w)
            if c is None:
                continue
            v = m.va + 4 * i + 4
            slots = img.vtable_slots(v, cap=1024)
            if slots:
                out[v] = Vft(c[0], c[1], c[2], slots)
    return out


def msvc_name(td):
    """'.?AVFoo@@' -> '??_7Foo@@6B@' (the primary vftable's name; the class text is what matters here)."""
    return '??_7' + td[4:] + '6B@'


def load(path, cache):
    st = os.stat(path)
    key = '%s %d %d' % (FORMAT, st.st_size, int(st.st_mtime))
    try:
        d = json.load(open(cache))
        if d.get('key') == key:
            return {int(v): Vft(e[0], e[1], [tuple(b) for b in e[2]], e[3]) for v, e in d['vft'].items()}
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    got = scan(path)
    os.makedirs(os.path.dirname(cache) or '.', exist_ok=True)
    with open(cache + '.tmp', 'w') as f:
        json.dump(dict(key=key, vft={str(v): list(e) for v, e in got.items()}), f)
    os.replace(cache + '.tmp', cache)
    return got


# ---- GCC vtable slot k -> exe vftable slot j (the slot fill, docs/LINK.md "Vftables from the player's exe")

D1_SLOT = re.compile(r'(D[12]Ev$|_D[12]$|_d1$)')
D0_SLOT = re.compile(r'(D0Ev$|_d0$)')


def dtor_pos(names, words=None):
    """Index of D1 in GCC's destructor pair (D1 then D0) among one vtable's slot symbols, or None. names: the slot
    symbols (Itanium names, or their address-bound forms `__fnv_<a>_D1`, `__fnvnx_vslot_*_d1|_d0`; None for a
    slot without a relocation). An abstract class's pair is two adjacent zero words (words: the slot words)."""
    for i in range(len(names) - 1):
        a, b = names[i], names[i + 1]
        if a and b and D1_SLOT.search(a) and D0_SLOT.search(b):
            return i
    if words is not None:
        for i in range(len(names) - 1):
            if names[i] is None and names[i + 1] is None and words[i] == 0 and words[i + 1] == 0:
                return i
    return None


def exe_slot_index(k, d):
    """The minus-one rule: MSVC has one deleting destructor where GCC has D1 and D0 (at d, d + 1), so GCC slot k
    is exe slot k up to D1, D0 shares D1's slot, and every later slot is one lower."""
    return k if d is None or k <= d else k - 1
