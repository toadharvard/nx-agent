"""thisadj.py - the virtual functions MSVC enters with `this` at a secondary base (stream S2c-abi).

An override of a virtual function that a non-primary base introduces is compiled by MSVC 2008 with `this` pointing
at that base subobject (the callee adjusts by -d itself). The exe's vftables show it: the function's address is a
slot only of vftables whose complete object locator has offset d > 0, never of a primary (offset 0) one; callers
that hold the full object call it through an adjustor thunk. GCC (Itanium ABI) compiles the same function for the
full object and emits a `_ZThn<d>_` thunk for the secondary vtable. So every image code pointer to such an address
(image vftable words, function tables, the harness's x86-convention calls) must reach the thunk, while the native
units' own references (full object) keep the primary entry.

    secondary_entries(exe path) -> {original address: d}   (exe vftables with RTTI only)

Same COL scan as exevft.py (S2c-vt), reduced to the offset and the slots; the exe is the player's own (agent-only),
read and never copied.
"""
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'loader', 'tools'))

TEXT = (0x00401000, 0x00fdf000)
DATA = (0x00fdf000, 0x02100000)


def exe_path(explicit=None):
    """The player's exe: the argument, else FNVNX_EXE, else PLAYER_EXE; None when no such regular file exists."""
    for p in (explicit, os.environ.get('FNVNX_EXE'), os.environ.get('PLAYER_EXE')):
        if p and p != 'none' and os.path.isfile(p):
            return os.path.abspath(p)
    return None


def vftables(path):
    """[(COL offset, [slot addresses])] of every vftable with a valid complete object locator."""
    import fnvnx_pe
    img = fnvnx_pe.Image(fnvnx_pe.PE(open(path, 'rb').read()))
    cols, out = {}, []

    def col_off(a):
        if a not in cols:
            got = None
            w = img.read(a, 20)
            if w is not None:
                sig, off, _, td, _ = struct.unpack('<IIIII', w)
                name = img.string(td + 8, 512) if sig == 0 and img.read(td, 8) is not None else None
                if name and name[0].startswith(b'.?A'):
                    got = off
            cols[a] = got
        return cols[a]

    for m in img.maps:
        for i in range(m.file_bytes // 4 - 1):
            w, nxt = struct.unpack_from('<II', m.data, 4 * i)
            if not (DATA[0] <= w < DATA[1] and TEXT[0] <= nxt < TEXT[1]):
                continue
            off = col_off(w)
            if off is None:
                continue
            slots = img.vtable_slots(m.va + 4 * i + 4, cap=1024)
            if slots:
                out.append((off, slots))
    return out


def secondary_entries(path):
    offs = collections.defaultdict(set)
    for off, slots in vftables(path):
        for s in slots:
            offs[s].add(off)
    return {a: min(o) for a, o in offs.items() if 0 not in o}


def thunk_name(primary, d):
    """Itanium name of the non-virtual this-adjusting thunk by d of the member function `primary` (_ZN.. / _ZNK..)."""
    if not primary.startswith('_ZN'):
        return None
    return '_ZThn%d_%s' % (d, primary[2:])


def from_env(explicit=None):
    """{address: d} for resolve: FNVNX_SECONDARY_ENTRIES (a tsv `address<TAB>d`, for tests) or the player's exe
    (explicit: resolve's own --exe, else FNVNX_EXE / PLAYER_EXE); {} without either."""
    t = os.environ.get('FNVNX_SECONDARY_ENTRIES')
    if t:
        out = {}
        for line in open(t):
            f = line.split()
            if len(f) >= 2 and not line.startswith('#'):
                out[int(f[0], 16)] = int(f[1])
        return out
    p = exe_path(explicit)
    return secondary_entries(p) if p else {}
