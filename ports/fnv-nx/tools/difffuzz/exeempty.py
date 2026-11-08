"""exeempty.py - exe functions whose body has no effect at all (difffuzz-20, oracle-empty-call).

MSVC /Od emits `push ebp; mov ebp, esp; pop ebp; ret` for an empty member or a destructor with nothing to destroy,
and the call to it stays in the oracle's trace; GCC drops the call. `effect_free(addr)` is True only when the exe body
is proven to do nothing a caller can see: it walks the straight-line code from `addr` (following an incremental-link
`jmp rel32` thunk, at most 3 deep) and accepts only the frame instructions (`push ebp`, `mov ebp, esp`, `pop ebp`,
`leave` after the frame is set up), padding nops and `ret` / `ret N`. That is no memory write beyond the return-address
slots below esp, no call, no branch, and no write to any register but ebp (restored) and esp (rebalanced): eax, ecx and
edx leave the function as they entered, so the caller computes the same thing with or without the call.

Reads the exe only (never copied). Without the exe nothing is proven (False).
"""
import config

_CACHE = {}
_PE = []
MAX_INSNS = 12


def _pe():
    if not _PE:
        try:
            import exeimage
            _PE.append(exeimage.load_pe(config.EXE, False) if config.EXE.is_file() else None)
        except Exception:  # noqa: BLE001
            _PE.append(None)
    return _PE[0]


def effect_free(addr, _depth=0):
    if addr not in _CACHE:
        try:
            _CACHE[addr] = _scan(addr, _depth)
        except Exception:  # noqa: BLE001
            _CACHE[addr] = False
    return _CACHE[addr]


def _scan(addr, depth):
    pe = _pe()
    if pe is None or depth > 3:
        return False
    import capstone
    from capstone import x86_const as X
    s = pe.section_of(addr)
    if s is None or not s[2] & 0x20000000:           # not code
        return False
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    base, data = s[0], s[3]
    a = addr
    pushed = framed = False                           # push ebp done / mov ebp, esp done (ebp not yet restored)
    for _ in range(MAX_INSNS):
        off = a - base
        ins = next(iter(md.disasm(bytes(data[off:off + 16]), a, 1)), None)
        if ins is None:
            return False
        m, ops = ins.mnemonic, ins.operands
        if m == 'push' and not pushed and not framed and ops[0].type == capstone.x86.X86_OP_REG \
                and ops[0].reg == X.X86_REG_EBP:
            pushed = True
        elif m == 'mov' and pushed and not framed and len(ops) == 2 and all(
                o.type == capstone.x86.X86_OP_REG for o in ops) and ops[0].reg == X.X86_REG_EBP \
                and ops[1].reg == X.X86_REG_ESP:
            framed = True
        elif m == 'pop' and pushed and ops[0].type == capstone.x86.X86_OP_REG and ops[0].reg == X.X86_REG_EBP:
            pushed = framed = False
        elif m == 'leave' and framed:
            pushed = framed = False
        elif m == 'nop' or (m == 'mov' and len(ops) == 2 and all(o.type == capstone.x86.X86_OP_REG for o in ops)
                            and ops[0].reg == ops[1].reg == X.X86_REG_EDI):
            pass
        elif m in ('ret', 'retn'):
            return not pushed and not framed
        elif m == 'jmp' and not pushed and not framed and ops[0].type == capstone.x86.X86_OP_IMM:
            return effect_free(ops[0].imm, depth + 1)
        else:
            return False
        a += ins.size
    return False


# ---------------------------------------------------------------- r4 uncls: destructor shapes read from the exe
#
# Two shapes of MSVC code that GCC compiles differently, recognised from the exe's own bytes (never copied):
#   deleting_wrapper(addr)  a scalar deleting destructor wrapper `F(this, flags)`: it tests bit 0 of its first stack
#                           argument, and only then calls something (operator delete / the Havok free) before `ret 4`.
#                           Natively the same source is the destructor `D(this)` followed by a separate `delete`.
#   dtor_chain(addr)        a destructor body: the function stores two class vftables into one object, the second
#                           the vftable of a base class of the first's class (RTTI: complete object locator ->
#                           class hierarchy -> base class array), derived first. A constructor stores base first.
_WRAP = {}
_CHAIN = {}


def _read_u32(pe, a):
    import struct
    return struct.unpack('<I', pe.read(a, 4))[0]


def vftable_class(v):
    """(class name, [names of the class and its bases]) of the vftable at exe address v by its RTTI locator, or None."""
    pe = _pe()
    if pe is None:
        return None
    try:
        col = _read_u32(pe, v - 4)
        if pe.section_of(col) is None or _read_u32(pe, col) != 0:
            return None
        td, chd = _read_u32(pe, col + 12), _read_u32(pe, col + 16)

        def name(t):
            out, a = b'', t + 8
            while len(out) < 160:
                c = pe.read(a, 1)
                if c in (b'\0', b''):
                    break
                out += c
                a += 1
            return out.decode('latin1')
        n, arr = _read_u32(pe, chd + 8), _read_u32(pe, chd + 12)
        if n > 64:
            return None
        return name(td), [name(_read_u32(pe, _read_u32(pe, arr + 4 * i))) for i in range(n)]
    except Exception:  # noqa: BLE001
        return None


def _insns(addr, limit):
    pe = _pe()
    if pe is None:
        return []
    import capstone
    s = pe.section_of(addr)
    if s is None or not s[2] & 0x20000000:
        return []
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    base, data = s[0], s[3]
    out, a = [], addr
    for _ in range(limit):
        ins = next(iter(md.disasm(bytes(data[a - base:a - base + 16]), a, 1)), None)
        if ins is None:
            break
        out.append(ins)
        a += ins.size
    return out


def deleting_wrapper(addr):
    if addr not in _WRAP:
        try:
            _WRAP[addr] = _deleting_wrapper(addr)
        except Exception:  # noqa: BLE001
            _WRAP[addr] = False
    return _WRAP[addr]


def _deleting_wrapper(addr):
    import capstone
    from capstone import x86_const as X
    ins = _insns(addr, 40)
    flag_at = None
    for i, n in enumerate(ins):
        if n.mnemonic in ('ret', 'retn'):
            break
        t = (n.mnemonic, n.op_str.replace(' ', ''))
        # test byte ptr [esp+8], 1 / test byte ptr [ebp+8], 1 / (mov r, [ebp+8]; and r, 1)
        if t[0] == 'test' and t[1] in ('byteptr[esp+8],1', 'byteptr[esp+0xc],1', 'byteptr[ebp+8],1'):
            flag_at = i
            break
        if t[0] == 'and' and t[1].endswith(',1') and i and ins[i - 1].mnemonic == 'mov' and \
                ins[i - 1].op_str.replace(' ', '').endswith(('[ebp+8]', '[esp+8]')) and \
                ins[i - 1].op_str.split(',')[0].strip() == n.op_str.split(',')[0].strip():
            flag_at = i
            break
    if flag_at is None or flag_at + 1 >= len(ins) or ins[flag_at + 1].mnemonic not in ('je', 'jz'):
        return False
    tgt = ins[flag_at + 1].operands[0].imm
    # something is called inside the guarded block, and the function returns `ret 4`
    inside = [n for n in ins[flag_at + 2:] if n.address < tgt]
    if not any(n.mnemonic == 'call' for n in inside):
        return False
    rets = [n for n in ins if n.mnemonic in ('ret', 'retn')]
    return bool(rets) and rets[0].op_str.strip() in ('4', '0x4')


def dtor_chain(addr):
    if addr not in _CHAIN:
        try:
            _CHAIN[addr] = _dtor_chain(addr)
        except Exception:  # noqa: BLE001
            _CHAIN[addr] = False
    return _CHAIN[addr]


def _dtor_chain(addr):
    import capstone
    stores = []
    rets = 0
    for n in _insns(addr, 80):
        if n.mnemonic in ('ret', 'retn'):
            rets += 1
            if rets >= 2:
                break
        if n.mnemonic == 'int3':
            break
        ops = n.operands
        if n.mnemonic == 'mov' and len(ops) == 2 and ops[0].type == capstone.x86.X86_OP_MEM and \
                ops[0].mem.disp == 0 and ops[0].mem.index == 0 and ops[0].mem.base != 0 and \
                ops[1].type == capstone.x86.X86_OP_IMM and ops[0].size == 4:
            c = vftable_class(ops[1].imm & 0xffffffff)
            if c is not None:
                stores.append((ops[0].mem.base, ops[1].imm & 0xffffffff, c))
    for (b1, v1, c1), (b2, v2, c2) in zip(stores, stores[1:]):
        if b1 == b2 and v1 != v2 and c2[0] != c1[0] and c2[0] in c1[1]:
            return True                       # derived vftable first, then a base class's
    return False
