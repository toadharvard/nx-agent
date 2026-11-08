"""retsrc.py - what an intercepted callee returns in eax, and which inputs it reads, read from the player's exe
(difffuzz-20, canned-this; difffuzz-20 args class: unread inputs).

The harness intercepts every call that leaves the unit and gives it a random canned eax. A callee that returns one of
its own inputs (`T *Assign(T *p)` ends in `mov eax, [ebp+8]`; a constructor ends in `mov eax, ecx`) hands the caller
that input back, and the caller (MSVC: `mov ecx, eax`, GCC: the variable it already holds) uses it as the next call's
`this`. With a random eax the oracle then passes garbage where the native passes the object, and the record reads as a
difference of the two machines. `ret_source(addr)` walks the callee's control-flow graph in the exe and reports what
every `ret` leaves in eax: ('arg', n) (the n-th stack word after the return address), 'this' (ecx at entry), or None
(anything else, a call result, a computed value, paths that disagree, code the walk cannot follow). It follows copies
through the six general registers (a constructor keeps `this` in esi over its calls; 0x00dba6a0 keeps its stack
argument in edx over a call to a leaf that does not write edx). The caller (x86emu / arm64emu) uses it only for a callee
whose definition says it returns a pointer.

`input_reads(addr)` is the other direction: which of the callee's declared inputs its code can read. The tree declares
a free function as a member (`this` in ecx the code never reads: 0x00d315d0, 0x00c32f60) or with stack parameters the
exe never pushes or reads (0x0065e750, a bare `ret 4` at 0x00a89af0); the native passes some value for each, the
oracle whatever its registers and stack held, and the call-args record reads as a difference. An input the callee
cannot read is not an argument.

Reads the exe only (never copied). Without the exe nothing is reported.
"""
import config

_CACHE = {}
_READS = {}
_PE = []
MAX_STEPS = 600

_GPR = {'eax': 'eax', 'ax': 'eax', 'al': 'eax', 'ah': 'eax', 'ecx': 'ecx', 'cx': 'ecx', 'cl': 'ecx', 'ch': 'ecx',
        'edx': 'edx', 'dx': 'edx', 'dl': 'edx', 'dh': 'edx', 'ebx': 'ebx', 'bx': 'ebx', 'bl': 'ebx', 'bh': 'ebx',
        'esi': 'esi', 'si': 'esi', 'edi': 'edi', 'di': 'edi'}
_REGS = ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')


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


def _leaf_writes(sec, md, addr, limit=40):
    """The general registers a straight-line leaf at addr writes up to its `ret`, or None when it is not one (a
    branch, a call, an indirect jump, no `ret` within `limit` instructions)."""
    base, size, data = sec[0], sec[1], sec[3]
    off = addr - base
    if not 0 <= off < size:
        return None
    wr = set()
    for n, i in enumerate(md.disasm(bytes(data[off:off + 16 * limit]), addr)):
        if n >= limit:
            return None
        if i.mnemonic in ('ret', 'retn'):
            return wr
        if i.mnemonic in ('call', 'jmp', 'leave') or i.mnemonic.startswith(('j', 'rep')):
            return None
        _, write = i.regs_access()
        for r in write:
            nm = _GPR.get(i.reg_name(r))
            if nm:
                wr.add(nm)
    return None


def _callee_pop(sec, md, addr, limit=400):
    """N of the first `ret N` in the straight-line code from addr (0 for a plain `ret`), None when a jump or an
    indirect transfer comes first (a thunk: the target's pop) or no `ret` shows within `limit` instructions."""
    base, size, data = sec[0], sec[1], sec[3]
    off = addr - base
    if not 0 <= off < size:
        return None
    for n, i in enumerate(md.disasm(bytes(data[off:off + 16 * 64]), addr)):
        if n >= limit:
            return None
        if i.mnemonic in ('ret', 'retn'):
            return int(i.op_str, 0) if i.op_str else 0
        if i.mnemonic == 'jmp' and not i.op_str.startswith('0x'):
            return None
    return None


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
    # state: (regs, esp_delta, ebp_frame); regs holds what eax ecx edx ebx esi edi carry from the entry: 'this' (ecx at
    # entry), ('arg', n) (the n-th stack word after the return address) or None (anything else)
    seen, found = set(), []
    work = [(addr, (tuple('this' if r == 'ecx' else None for r in _REGS), 0, None))]
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
            regs, delta, ebp = st
            m, ops = i.mnemonic, i.operands
            nxt = a + i.size
            if m in ('ret', 'retn'):
                found.append(regs[0])
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
                # the callee's result is in eax; ecx and edx survive only a direct callee that is a straight-line
                # leaf not writing them (MSVC knows what its callees leave); ebx esi edi are the callee's to restore
                lost = {'eax', 'ecx', 'edx'}
                if ops[0].type == capstone.x86.X86_OP_IMM:
                    wr = _leaf_writes(s, md, ops[0].imm)
                    if wr is not None:
                        lost = {'eax'} | (wr & {'ecx', 'edx'})
                st = (tuple(None if r in lost else v for r, v in zip(_REGS, regs)), delta, ebp)
                a = nxt
                continue
            _, write = i.regs_access()
            regs = list(regs)
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
            new = {}
            for r in write:
                nm = _GPR.get(i.reg_name(r))
                if nm:
                    new[nm] = None
            if m == 'mov' and len(ops) == 2 and ops[0].type == capstone.x86.X86_OP_REG:
                dst = i.reg_name(ops[0].reg)
                o = ops[1]
                if dst in _REGS and o.type == capstone.x86.X86_OP_REG and i.reg_name(o.reg) in _REGS:
                    new[dst] = regs[_REGS.index(i.reg_name(o.reg))]
                elif dst in _REGS and o.type == capstone.x86.X86_OP_MEM and o.size == 4 and o.mem.index == 0 and \
                        o.mem.segment == 0:
                    d = o.mem.disp
                    if o.mem.base == X.X86_REG_EBP and ebp is not None and d >= ebp + 4 and (d - ebp) % 4 == 0:
                        new[dst] = ('arg', (d - ebp - 4) // 4)
                    elif o.mem.base == X.X86_REG_ESP and delta is not None and d - delta >= 4 and \
                            (d - delta) % 4 == 0:
                        new[dst] = ('arg', (d - delta - 4) // 4)
            for nm, v in new.items():
                regs[_REGS.index(nm)] = v
            if m == 'leave':
                delta = None
            st = (tuple(regs), delta, ebp)
            a = nxt
    if not found:
        return None
    first = found[0]
    if first is None or any(f != first for f in found):
        return None
    return first


def returned_input(cv, addr):
    """What the callee at `addr` hands back, for the canned result of an intercepted call: 'this', the label of a
    pointer parameter (its x86 stack word and its native register carry the same value), or None. `cv` is the callee's
    definition conv; only a callee that returns a pointer or reference is looked at. A free function whose first
    parameter lives in ecx (the tree declares a constructor as `void *F(void *p)`, fastcall) hands back 'this' too: ecx
    at entry is the native's x0."""
    if cv is None or cv.ret is None or not cv.ret.is_ptr or getattr(cv, 'hidden', False):
        return None
    src = ret_source(addr)
    if src == 'this':
        if any(v.label == 'this' for v in cv.vals):
            return 'this'
        return 'this' if any(space == 'ecx' and size == 4 for _l, space, _o, size in cv.x86) else None
    if isinstance(src, tuple):
        for lab, space, off, size in getattr(cv, 'x86_declared', cv.x86):
            if space == 'stk' and off == 4 * src[1] and size == 4:
                v = next((x for x in cv.vals if x.label == lab), None)
                if v is not None and v.kind == 'gpr' and v.is_ptr:
                    return lab
    return None


def input_reads(addr, depth=2):
    """(ecx, args, every): whether the exe function at addr can read ecx at entry, the set of its stack-argument
    words (0 = the first after the return address) it can read, and whether it may read any of them (the address of
    one is taken, a tail jump, an indirect jump or call). A write to an argument slot before a read makes the slot
    a local. None when the code does not say (a branch it cannot follow, esp-relative access after a call, no exe)."""
    key = (addr, depth)
    if key not in _READS:
        try:
            _READS[key] = _reads(addr, depth)
        except Exception:  # noqa: BLE001
            _READS[key] = None
    return _READS[key]


def _reads(addr, depth):
    pe = _pe()
    if pe is None:
        return None
    import capstone
    from capstone import x86_const as X
    s = pe.section_of(addr)
    if s is None or not s[2] & 0x20000000:
        return None
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    base, data = s[0], s[3]

    def at(a):
        off = a - base
        for i in md.disasm(bytes(data[off:off + 16]), a, 1):
            return i
        return None
    ecx_read, args, every = False, set(), False
    # state: (esp_delta, ebp_frame, ecx still holds its incoming value, argument words written so far)
    seen, work, steps = set(), [(addr, (0, None, True, frozenset()))], 0
    ESPEBP = (X.X86_REG_ESP, X.X86_REG_EBP)
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
            delta, ebp, live, wrote = st
            m, ops = i.mnemonic, i.operands
            nxt = a + i.size
            if m in ('ret', 'retn'):
                break
            read, write = i.regs_access()
            rnames = {_GPR.get(i.reg_name(r)) for r in read}
            wnames = {_GPR.get(i.reg_name(r)) for r in write}
            if m in ('xor', 'sub', 'sbb') and len(ops) == 2 and ops[0].type == ops[1].type == capstone.x86.X86_OP_REG \
                    and ops[0].reg == ops[1].reg:
                rnames = set()
            if live and 'ecx' in rnames and m != 'call':
                ecx_read = True
            # operands: memory through esp / ebp, and a stack register copied out of its frame role
            for o in ops:
                if o.type == capstone.x86.X86_OP_REG and o.reg in ESPEBP and o.access & capstone.CS_AC_READ and \
                        m not in ('push', 'pop', 'call', 'ret', 'leave') and \
                        not (m == 'mov' and ops[0].type == capstone.x86.X86_OP_REG and
                             {ops[0].reg, ops[1].reg} <= set(ESPEBP)) and \
                        not (m in ('add', 'sub') and o is ops[0]):
                    every = True                       # a stack register copied out: pointers into the frame
                if o.type != capstone.x86.X86_OP_MEM or o.mem.base not in ESPEBP:
                    continue
                if o.mem.index != 0:
                    return None                        # an indexed frame access: which slot is not known
                d = o.mem.disp
                if o.mem.base == X.X86_REG_EBP:
                    if ebp is None:
                        return None
                    n = (d - ebp - 4) // 4 if d >= ebp + 4 and (d - ebp) % 4 == 0 else None
                else:
                    if delta is None:
                        return None
                    n = (d - delta - 4) // 4 if d - delta >= 4 and (d - delta) % 4 == 0 else None
                if n is None:
                    continue                           # a local, the return address or a saved register
                if m == 'lea':
                    every = True                       # the address of an argument slot: anything above it
                elif o.access & capstone.CS_AC_READ and n not in wrote:
                    args.add(n)
                if o.access & capstone.CS_AC_WRITE:
                    wrote = wrote | {n}
            if m == 'call':
                if live:
                    if ops[0].type == capstone.x86.X86_OP_IMM and depth > 0:
                        sub = input_reads(ops[0].imm, depth - 1)
                        if sub is None or sub[0]:
                            ecx_read = True
                    else:
                        ecx_read = True
                pop = _callee_pop(s, md, ops[0].imm) if ops[0].type == capstone.x86.X86_OP_IMM else None
                live = False
                delta = delta - pop if delta is not None and pop is not None else None   # esp after the call
                st = (delta, ebp, live, wrote)
                a = nxt
                continue
            if m == 'jmp':
                if ops[0].type == capstone.x86.X86_OP_IMM and s[0] <= ops[0].imm < s[0] + s[1] and \
                        addr <= ops[0].imm < addr + 0x1000:
                    a = ops[0].imm
                    st = (delta, ebp, live, wrote)
                    continue
                every = True                           # a tail jump or an indirect jump: the target reads them
                if live:
                    ecx_read = True
                break
            if m.startswith('j'):
                if ops[0].type != capstone.x86.X86_OP_IMM:
                    return None
                work.append((ops[0].imm, (delta, ebp, live, wrote)))
                a = nxt
                continue
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
                ebp = delta
            if m == 'leave':
                delta = None
            if 'ecx' in wnames:
                live = False
            st = (delta, ebp, live, wrote)
            a = nxt
    return (ecx_read, frozenset(args), every)


def unread_labels(cv, addr):
    """The labels of cv's parameters that the exe code at addr cannot read: `this` / a first parameter in ecx when the
    code never reads ecx at entry, a stack parameter whose word it never reads. Empty without the exe or when the
    code does not say."""
    r = input_reads(addr)
    if r is None:
        return set()
    ecx, args, every = r
    out = set()
    for lab, space, off, size in getattr(cv, 'x86_declared', cv.x86):
        if lab == 'sret':
            continue
        if space == 'ecx' and not ecx:
            out.add(lab)
        elif space == 'stk' and not every and all(w not in args for w in range(off // 4, (off + max(size, 1) + 3) // 4)):
            out.add(lab)
    return out


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
