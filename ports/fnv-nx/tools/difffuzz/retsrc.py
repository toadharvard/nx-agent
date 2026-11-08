"""retsrc.py - what an intercepted callee returns in eax, read from the player's exe (difffuzz-20, canned-this).

The harness intercepts every call that leaves the unit and gives it a random canned eax. A callee that returns one of
its own inputs (`T *Assign(T *p)` ends in `mov eax, [ebp+8]`; a constructor ends in `mov eax, ecx`) hands the caller
that input back, and the caller (MSVC: `mov ecx, eax`, GCC: the variable it already holds) uses it as the next call's
`this`. With a random eax the oracle then passes garbage where the native passes the object, and the record reads as a
difference of the two machines. `ret_source(addr)` walks the callee's control-flow graph in the exe and reports what
every `ret` leaves in eax: ('arg', n) (the n-th stack word after the return address), 'this' (ecx at entry), or None
(anything else, a call result, a computed value, paths that disagree, code the walk cannot follow). The caller
(x86emu / arm64emu) uses it only for a callee whose definition says it returns a pointer.

Reads the exe only (never copied). Without the exe nothing is reported.
"""
import config

_CACHE = {}
_PE = []
MAX_STEPS = 600


def _pe():
    if not _PE:
        try:
            import exeimage
            _PE.append(exeimage.load_pe(config.EXE, False) if config.EXE.is_file() else None)
        except Exception:  # noqa: BLE001
            _PE.append(None)
    return _PE[0]


def ret_source(addr):
    if addr not in _CACHE:
        try:
            _CACHE[addr] = _walk(addr)
        except Exception:  # noqa: BLE001
            _CACHE[addr] = None
    return _CACHE[addr]


def _walk(addr):
    pe = _pe()
    if pe is None:
        return None
    import capstone
    from capstone import x86_const as X
    s = pe.section_of(addr)
    if s is None or not s[2] & 0x20000000:       # not code
        return None
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    base, data = s[0], s[3]

    def at(a):
        off = a - base
        for i in md.disasm(bytes(data[off:off + 16]), a, 1):
            return i
        return None
    # state: (eax, ecx_is_this, esp_delta, ebp_frame): eax in {'this', ('arg', n), 'junk'}
    seen, found = set(), []
    work = [(addr, ('junk', True, 0, None))]
    steps = 0
    while work:
        a, st = work.pop()
        while True:
            if (a, st) in seen:
                break
            seen.add((a, st))
            steps += 1
            if steps > MAX_STEPS:
                return None
            i = at(a)
            if i is None:
                return None
            eax, ecx, delta, ebp = st
            m, ops = i.mnemonic, i.operands
            nxt = a + i.size
            if m in ('ret', 'retn'):
                found.append(eax)
                break
            if m == 'jmp':
                if ops[0].type != capstone.x86.X86_OP_IMM:
                    return None
                a = ops[0].imm
                if not s[0] <= a < s[0] + s[1]:
                    return None
                continue
            if m.startswith('j'):
                if ops[0].type != capstone.x86.X86_OP_IMM:
                    return None
                work.append((ops[0].imm, st))
                a = nxt
                continue
            if m == 'call':
                eax, ecx = 'junk', False       # the callee's result; ecx is clobbered
                # (stack arguments of a cdecl call are popped by the caller: the delta is rebalanced below)
                if ops[0].type != capstone.x86.X86_OP_IMM:
                    pass
                st = (eax, ecx, delta, ebp)
                a = nxt
                continue
            read, write = i.regs_access()
            new_eax = eax
            if m == 'push' and delta is not None:
                delta += 4
            elif m == 'pop' and delta is not None:
                delta -= 4
            elif m in ('sub', 'add') and ops[0].type == capstone.x86.X86_OP_REG and ops[0].reg == X.X86_REG_ESP:
                if ops[1].type == capstone.x86.X86_OP_IMM and delta is not None:
                    delta += ops[1].imm if m == 'sub' else -ops[1].imm
                else:
                    delta = None
            elif ops and ops[0].type == capstone.x86.X86_OP_REG and ops[0].reg == X.X86_REG_ESP and m != 'cmp':
                delta = None
            if m == 'mov' and len(ops) == 2 and ops[0].type == capstone.x86.X86_OP_REG and ops[0].reg == X.X86_REG_EBP \
                    and ops[1].type == capstone.x86.X86_OP_REG and ops[1].reg == X.X86_REG_ESP:
                ebp = delta                    # ebp = esp: arguments at [ebp + 4 + 4 * (n + 1)] after the saved ebp
            if X.X86_REG_EAX in write or X.X86_REG_AX in write or X.X86_REG_AL in write:
                new_eax = 'junk'
                if m == 'mov' and len(ops) == 2 and ops[0].type == capstone.x86.X86_OP_REG and \
                        ops[0].reg == X.X86_REG_EAX:
                    o = ops[1]
                    if o.type == capstone.x86.X86_OP_REG and o.reg == X.X86_REG_ECX and ecx:
                        new_eax = 'this'
                    elif o.type == capstone.x86.X86_OP_MEM and o.size == 4 and o.mem.index == 0 and \
                            o.mem.segment == 0:
                        d = o.mem.disp
                        if o.mem.base == X.X86_REG_EBP and ebp is not None and d >= ebp + 4 and (d - ebp) % 4 == 0:
                            new_eax = ('arg', (d - ebp - 4) // 4)
                        elif o.mem.base == X.X86_REG_ESP and delta is not None and d - delta >= 4 and \
                                (d - delta) % 4 == 0:
                            new_eax = ('arg', (d - delta - 4) // 4)
            if X.X86_REG_ECX in write or X.X86_REG_CX in write or X.X86_REG_CL in write:
                ecx = False
            if m == 'leave':
                delta = None
            st = (new_eax, ecx, delta, ebp)
            a = nxt
    if not found:
        return None
    first = found[0]
    if first == 'junk' or any(f != first for f in found):
        return None
    return first


def returned_input(cv, addr):
    """What the callee at `addr` hands back, for the canned result of an intercepted call: 'this', the label of a
    pointer parameter (its x86 stack word and its native register carry the same value), or None. `cv` is the callee's
    definition conv; only a callee that returns a pointer or reference is looked at."""
    if cv is None or cv.ret is None or not cv.ret.is_ptr or getattr(cv, 'hidden', False):
        return None
    src = ret_source(addr)
    if src == 'this':
        return 'this' if any(v.label == 'this' for v in cv.vals) else None
    if isinstance(src, tuple):
        for lab, space, off, size in getattr(cv, 'x86_declared', cv.x86):
            if space == 'stk' and off == 4 * src[1] and size == 4:
                v = next((x for x in cv.vals if x.label == lab), None)
                if v is not None and v.kind == 'gpr' and v.is_ptr:
                    return lab
    return None


def trivial_leaf(addr, limit=12):
    """True when the exe function at addr is a straight-line leaf up to its `ret`: at most `limit` instructions, no
    call, no jump, and no memory write other than through esp / ebp (its own frame). Such a function does no work
    beyond its result (S2 W6: 0x0065e750 returns an uninitialised al, 0x0065fd70 its argument), so a native
    placeholder for it is not the native-zero-stub class. None without the exe."""
    pe = _pe()
    if pe is None:
        return None
    import capstone
    from capstone import x86_const as X
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    try:
        code = pe.read(addr, 16 * limit)
    except Exception:  # noqa: BLE001
        return None
    for n, ins in enumerate(md.disasm(code, addr)):
        if n >= limit:
            return False
        g = set(ins.groups)
        if X.X86_GRP_RET in g:
            return True
        if X.X86_GRP_CALL in g or X.X86_GRP_JUMP in g or X.X86_GRP_INT in g:
            return False
        if ins.mnemonic.startswith(('push', 'pop', 'leave')):
            continue
        for op in ins.operands:
            if op.type == X.X86_OP_MEM and (op.access & capstone.CS_AC_WRITE):
                base = ins.reg_name(op.mem.base) if op.mem.base else None
                if base not in ('esp', 'ebp') or op.mem.segment:
                    return False
        if ins.mnemonic.startswith(('stos', 'movs', 'rep')):
            return False
    return False
