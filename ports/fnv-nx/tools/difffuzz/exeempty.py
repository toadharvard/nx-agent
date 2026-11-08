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
