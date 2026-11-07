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


def plain_vftables(path, addrs, starts):
    """{address: Vft(None, None, [], slots)} for vftables without RTTI (no COL before them: some of the shader
    classes) at the given image addresses (the V rows the units name), their slots read like scan() but bounded by
    the next known vftable start (starts: every scanned or named vftable): with no COL pointer between them, one
    vftable's words run straight into the next one's."""
    import bisect
    import fnvnx_pe
    pe = fnvnx_pe.PE(open(path, 'rb').read())
    img = fnvnx_pe.Image(pe)
    st = sorted(set(starts) | set(addrs))
    out = {}
    for v in sorted(addrs):
        slots = img.vtable_slots(v, cap=1024)
        i = bisect.bisect_right(st, v)
        if i < len(st):
            slots = slots[:max(0, (st[i] - v) // 4)]
        if slots:
            out[v] = Vft(None, None, [], slots)
    return out


# ---- trivial leaf functions of the exe (S2c-vt): an exact native stub where no unit compiles the address

_PE = {}


def _text(path):
    if path not in _PE:
        import fnvnx_pe
        data = open(path, 'rb').read()
        _PE[path] = (fnvnx_pe.PE(data), data)
    return _PE[path]


def leaf_constant(path, addr):
    """What the x86 function at addr does, when it is a constant leaf: 'void' (`ret`, `ret N`), or the 32-bit value
    it leaves in eax/al (`xor eax, eax` / `xor al, al` / `mov al, imm8` / `mov eax, imm32`, then `ret` or `ret N`),
    bare or inside an /Od frame (`push ebp; mov ebp, esp` [+ the spilled ecx], then `mov esp, ebp; pop ebp`); else
    None. MSVC folds identical functions, so one such body (0x00e7cd40 `return 0;`) stands in many vtable
    slots. al-only forms leave eax's upper bytes undefined on x86; the native stub returns the zero-extended byte,
    which every bool / char reader of the result sees the same."""
    pe, data = _text(path)
    rva = addr - pe.image_base
    try:
        off = pe.rva_to_off(rva, 16)
    except Exception:
        return None
    if off is None:
        return None

    b = data[off:off + 32]

    def ret_at(i):
        return b[i:i + 1] == b'\xc3' or (b[i:i + 1] == b'\xc2' and b[i + 2:i + 3] == b'\x00')

    def value_then(i, tail):
        """(value, length) of an eax/al constant at i followed by tail(i + length), else None."""
        if tail(i):
            return 'void', 0
        if b[i:i + 2] in (b'\x33\xc0', b'\x31\xc0', b'\x32\xc0', b'\x30\xc0') and tail(i + 2):
            return 0, 2
        if b[i:i + 1] == b'\xb0' and tail(i + 2):
            return b[i + 1], 2
        if b[i:i + 1] == b'\xb8' and tail(i + 5):
            return int.from_bytes(b[i + 1:i + 5], 'little'), 5
        return None
    got = value_then(0, ret_at)
    if got is not None:
        return got[0]
    # /Od frames (the decomp's debug-built units): push ebp; mov ebp, esp; [push ecx; mov [ebp-4], ecx | sub esp, 4;
    # mov [ebp-4], ecx]; <constant>; mov esp, ebp; pop ebp; ret / ret N  (or pop ebp right after the prologue)
    for pro in (b'\x55\x8b\xec\x51\x89\x4d\xfc', b'\x55\x8b\xec\x83\xec\x04\x89\x4d\xfc', b'\x55\x8b\xec'):
        if b.startswith(pro):
            n = len(pro)
            # a prologue that moved esp needs `mov esp, ebp` before `pop ebp`
            for epi in ((b'\x8b\xe5\x5d', b'\x5d') if pro == b'\x55\x8b\xec' else (b'\x8b\xe5\x5d',)):
                got = value_then(n, lambda i, e=epi: b[i:i + len(e)] == e and ret_at(i + len(e)))
                if got is not None:
                    return got[0]
    return None


_FLAGS_TEST = re.compile(
    rb'\xf6\x44\x24[\x04-\x20]\x01|\xf6\x45\x08\x01|\xf6[\xc0-\xc7]\x01|\xa8\x01|\x8b\x45\x08\x83\xe0\x01|'
    rb'\x8b\x44\x24[\x04-\x20]\x83\xe0\x01')


def deleting_dtor(path, addr):
    """True when the x86 function at addr has the shape of MSVC's deleting destructor `??_G(flags)`: it returns
    with `ret 4` (thiscall, one stack argument) and tests bit 0 of that argument before (`test byte [esp+n], 1`,
    `test r8, 1`, `test al, 1`, or the /Od `mov eax, [ebp+8]; and eax, 1`). On link 9f865b0a it recognises 121 of
    the address map's 132 kind-G addresses; used for exe slot 0 only (D26 c), where the prior is high."""
    pe, data = _text(path)
    try:
        off = pe.rva_to_off(addr - pe.image_base, 16)
    except Exception:
        return False
    if off is None:
        return False
    b = data[off:off + 0x400]
    i = b.find(b'\xc2\x04\x00')
    return i > 0 and _FLAGS_TEST.search(b[:i]) is not None


def thunk(path, addr):
    """(adjustment, target) of an MSVC thunk at addr: `sub ecx, imm8|imm32; jmp rel32` (an adjustor thunk of a
    secondary vftable: this -= imm), `add ecx, imm8; jmp rel32` (this += imm: a negative adjustment) or a plain
    `jmp rel32` (0); else None."""
    pe, data = _text(path)
    try:
        off = pe.rva_to_off(addr - pe.image_base, 12)
    except Exception:
        return None
    if off is None:
        return None
    b = data[off:off + 12]

    def rel(i):
        return (addr + i + 5 + int.from_bytes(b[i + 1:i + 5], 'little', signed=True)) & 0xffffffff
    if b[:2] == b'\x83\xe9' and b[3:4] == b'\xe9':
        return b[2], rel(3)
    if b[:2] == b'\x83\xc1' and b[3:4] == b'\xe9':
        return -b[2], rel(3)
    if b[:2] == b'\x81\xe9' and b[6:7] == b'\xe9':
        return int.from_bytes(b[2:6], 'little'), rel(6)
    if b[:1] == b'\xe9':
        return 0, rel(0)
    return None


# ---- the vftable an exe constructor stores (S2c, S5 class 3: constructor-named vtables)

CTOR_SPAN = 0x400   # bytes decoded from a constructor's entry (S5's stand-in reads the same span)
_CS = {}


def ctor_vftable(path, addr, starts):
    """The vftable the x86 constructor at addr names: the immediate of its last `mov dword ptr [reg], imm32` (a base
    register only: no index, no displacement, no segment - the vptr of the object the register holds) whose value is
    in starts (vftable starts, e.g. scan()'s), decoded with capstone from addr up to its first `ret` / `ret N` (at
    most CTOR_SPAN bytes). An inlined base constructor stores its own vftable first, so the last store is the most
    derived class's. None without capstone, outside the image's sections, or when there is no such store."""
    try:
        import capstone
        from capstone import x86 as cx
    except ImportError:
        return None
    pe, data = _text(path)
    off, n = None, CTOR_SPAN
    while off is None and n >= 16:   # a constructor near its section's end
        try:
            off = pe.rva_to_off(addr - pe.image_base, n)
        except Exception:
            off = None
        if off is None:
            n //= 2
    if off is None:
        return None
    if 'md' not in _CS:
        _CS['md'] = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        _CS['md'].detail = True
    last = None
    for ins in _CS['md'].disasm(data[off:off + n], addr):
        if ins.id == cx.X86_INS_RET:
            break
        if ins.id != cx.X86_INS_MOV or len(ins.operands) != 2:
            continue
        dst, src = ins.operands
        if dst.type == cx.X86_OP_MEM and dst.size == 4 and src.type == cx.X86_OP_IMM and dst.mem.base and \
                not dst.mem.index and not dst.mem.disp and not dst.mem.segment and (src.imm & 0xffffffff) in starts:
            last = src.imm & 0xffffffff
    return last
