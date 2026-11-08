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


def arg_blocks(rets, limit=160):
    """(r4 uncls, call-order) Split the oracle's traced calls of one region into argument blocks, from the exe's code.
    `rets` are the return addresses of the traced calls in trace order. A block starts at a traced call, and the code
    after it is walked linearly: it is a run of traced calls closed by the next call instruction that is not one of
    them (the consuming call, run as code), and every traced call of the block must have its result (eax) pushed or
    moved to a register before the next call instruction: `push eax`, or `mov <register other than eax>, eax` (a
    store to memory is a statement, not an argument). The next block starts where the trace goes on (a block may sit
    in another function). Returns a list of (first, last+1) index pairs that tile the region, or None when the code
    does not show that shape (branches are not followed)."""
    if not rets:
        return None
    blocks, start = [], 0
    while start < len(rets):
        end = _one_block(rets, start, limit)
        if end is None:
            return None
        blocks.append((start, end))
        start = end
    return blocks


def _one_block(rets, start, limit):
    import capstone
    from capstone import x86_const as X
    want = {r: i for i, r in enumerate(rets)}
    nxt, pending = start + 1, start            # the scan starts after the first call: its result is pending
    for n in _insns(rets[start], limit):
        if n.mnemonic == 'call':
            if pending is not None:
                return None                    # a call before the previous result was taken
            ret = n.address + n.size
            if ret in want:
                if want[ret] != nxt:
                    return None
                pending, nxt = nxt, nxt + 1
            else:
                return nxt                     # the consumer closes the block
            continue
        if n.mnemonic in ('ret', 'retn') or n.mnemonic.startswith('j'):
            return None                        # branches are not followed
        if pending is not None:
            ops = n.operands
            if (n.mnemonic == 'push' and ops[0].type == capstone.x86.X86_OP_REG and ops[0].reg == X.X86_REG_EAX) or \
                    (n.mnemonic == 'mov' and len(ops) == 2 and ops[1].type == capstone.x86.X86_OP_REG and
                     ops[1].reg == X.X86_REG_EAX and ops[0].type == capstone.x86.X86_OP_REG and
                     ops[0].reg != X.X86_REG_EAX):
                pending = None
    return None


# ------------------------------------------------------------- r5 design: a destructor that leaves the vptr to its base
_DEFER = {}


def dtor_defers_vptr(addr):
    """(r5 design, dtor-base-vptr) True when the exe shows that the destructor at `addr` (or the destructor a scalar
    deleting wrapper at `addr` calls first) never stores a vftable into its object itself and ends by calling a
    function that does: the last direct call of the body is a base-class destructor whose first instructions store
    an image vftable (RTTI-checked) at offset 0 of `this`. The final vptr of the object is that base's vftable on
    the exe whatever the harness does to the call, so a machine that left the word alone (the call is intercepted
    with a canned result) and one that stored an address point of its own agree only if that base destructor is
    what sets it. False without the exe."""
    if addr not in _DEFER:
        try:
            _DEFER[addr] = _defers(addr, 0)
        except Exception:  # noqa: BLE001
            _DEFER[addr] = False
    return _DEFER[addr]


def _vftable_store_at0(ins):
    import capstone
    ops = ins.operands
    if ins.mnemonic == 'mov' and len(ops) == 2 and ops[0].type == capstone.x86.X86_OP_MEM and ops[0].size == 4 and \
            ops[0].mem.disp == 0 and ops[0].mem.index == 0 and ops[0].mem.base != 0 and \
            ops[1].type == capstone.x86.X86_OP_IMM:
        return vftable_class(ops[1].imm & 0xffffffff) is not None
    return False


def _defers(addr, depth):
    import capstone
    body = []
    for n in _insns(addr, 90):
        if n.mnemonic in ('ret', 'retn') or n.mnemonic == 'int3':
            break
        body.append(n)
    if not body:
        return False
    calls = [n for n in body if n.mnemonic == 'call' and n.operands[0].type == capstone.x86.X86_OP_IMM]
    if depth == 0 and deleting_wrapper(addr) and calls:
        return _defers(calls[0].operands[0].imm, 1)           # the wrapper's first call is the destructor
    if any(_vftable_store_at0(n) for n in body):
        return False                                           # it stores a vftable itself
    if not calls:
        return False
    base = calls[-1].operands[0].imm
    # nothing is called after the base destructor but the epilogue, and its body begins by storing a vftable
    after = body[body.index(calls[-1]) + 1:]
    if any(n.mnemonic == 'call' for n in after):
        return False
    return any(_vftable_store_at0(n) for n in _insns(base, 24) if n.mnemonic not in ('ret', 'retn'))

# ---------------------------------------------------------------- r5 order: one full expression, read from the exe
#
# arg_blocks above proves the narrow shape (results pushed or moved, the calls in a run). MSVC /Od also evaluates the
# operands of one expression in an order of its own that is not a plain reversal: `A()->f() == B()->g()` calls both
# getters first (each result spilled to a compiler temporary, loaded back as `this`), then the two virtual calls and
# compares them (`mov esi, eax` ... `cmp eax, esi`); `F(a(), b()->c()->d(), x->e())` pushes each argument's result
# (`push eax`, or `push ecx; fstp [esp]` for a float) and evaluates the argument chains one after another.
# GCC's order differs, and C++ leaves both open. expr_deps() proves the region is ONE such expression, from the
# exe's bytes (never from a name): a straight-line walk from the first traced call's return address that follows
# where every call's result goes (registers, the x87 top, the pushed stack, and `[ebp-N]` frame slots) and accepts
# it only when
#   - there is no branch, jump or return before every traced result has been consumed (no `&&`, `||`, `?:`, no end
#     of a statement in the middle);
#   - every traced result is consumed by a later call (ecx / the call target register), pushed as an argument, or
#     combined by an instruction (`cmp eax, esi`), i.e. reaches the end of the expression; a result that is
#     dropped (a statement `f();`) is no operand;
#   - no result is stored to memory except into an `[ebp-N]` slot that the rest of the function never reads again
#     (a compiler temporary: a statement `x = f();` stores to a variable that is read later; a store through any
#     other address is a side effect).
# The result is the data-dependence relation between the traced calls: i depends on j when j's result reaches i's
# `this` / target register (through temporaries and the vtable load). Calls with no path between them may run in any
# order. What it cannot see: a result pushed for a callee that takes it (g(f()), two calls adjacent in the stack
# area) is not a dependence here; the argument comparison after pairing the calls catches a native that passed
# something else.
_EXPR = {}
_CANON = {'eax': 'a', 'ax': 'a', 'al': 'a', 'ah': 'a', 'ecx': 'c', 'cx': 'c', 'cl': 'c', 'ch': 'c',
          'edx': 'd', 'dx': 'd', 'dl': 'd', 'dh': 'd', 'ebx': 'b', 'bx': 'b', 'bl': 'b', 'bh': 'b',
          'esi': 'si', 'si': 'si', 'edi': 'di', 'di': 'di', 'ebp': 'bp', 'bp': 'bp', 'esp': 'sp', 'sp': 'sp'}
_NO = frozenset()


def expr_deps(rets, limit=260):
    """{i: frozenset of the traced calls j whose result reaches call i} for the traced calls whose return addresses
    are `rets` (trace order), when the exe shows them to be the calls of one expression (see above); else None."""
    if not rets or None in rets:
        return None
    key = tuple(rets)
    if key not in _EXPR:
        try:
            _EXPR[key] = _expr_deps(list(rets), limit)
        except Exception:  # noqa: BLE001
            _EXPR[key] = None
    return _EXPR[key]


def _expr_deps(rets, limit):
    import capstone
    from capstone import x86_const as X
    n = len(rets)
    want = {r: i for i, r in enumerate(rets)}
    if len(want) != n:
        return None
    code = _insns(rets[0], limit)
    if not code:
        return None
    OP_REG, OP_MEM, OP_IMM = capstone.x86.X86_OP_REG, capstone.x86.X86_OP_MEM, capstone.x86.X86_OP_IMM
    reg = {k: _NO for k in ('a', 'b', 'c', 'd', 'si', 'di', 'bp', 'sp')}
    slots, temps, stack = {}, set(), []
    deps, sinks = {}, set()
    st = {'st0': frozenset([0]), 'nxt': 1, 'node': n}        # the scan starts after the first call: its result is in eax / st0
    deps[0] = set()
    reg['a'] = frozenset([0])

    def closure(roots):
        seen, todo = set(), list(roots)
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            todo.extend(deps.get(x, ()))
        return seen

    def done():
        return st['nxt'] == n and set(range(n)) <= closure(sinks)

    def cn(ins, r):
        return _CANON.get(ins.reg_name(r))

    def slot_of(op):
        m = op.mem
        return m.disp if m.base == X.X86_REG_EBP and m.index == 0 else None

    def mem_taint(ins, op):
        d = slot_of(op)
        if d is not None:
            return slots.get(d, _NO)
        t = _NO
        for r in (op.mem.base, op.mem.index):
            if r:
                t = t | reg.get(cn(ins, r), _NO)
        return t

    def src_taint(ins, op):
        if op.type == OP_REG:
            return reg.get(cn(ins, op.reg), _NO)
        if op.type == OP_MEM:
            return mem_taint(ins, op)
        return _NO

    last = None
    for ins in code:
        last = ins
        m, ops = ins.mnemonic, ins.operands
        if m == 'call':
            ret = ins.address + ins.size
            inp = set(reg['c'])
            if ops and ops[0].type != OP_IMM:
                inp |= src_taint(ins, ops[0])
            if ret in want:
                if want[ret] != st['nxt']:
                    return None
                node, st['nxt'] = st['nxt'], st['nxt'] + 1
                deps[node] = inp
            else:
                node, st['node'] = st['node'], st['node'] + 1
                for t in stack:
                    inp |= t                # the closing call takes what was pushed
                deps[node] = inp
                sinks.add(node)
            reg['a'], reg['c'], reg['d'] = frozenset([node]), _NO, _NO
            st['st0'] = frozenset([node])
            if done():
                break
            continue
        if m in ('ret', 'retn'):
            sinks |= set(reg['a']) | set(st['st0'])
            if done():
                break
            return None
        if m.startswith('j') or m in ('leave', 'int3') or m.startswith('loop'):
            return None
        if m == 'push':
            stack.append(src_taint(ins, ops[0]) if ops else _NO)
            continue
        if m == 'pop':
            t = stack.pop() if stack else _NO
            if ops and ops[0].type == OP_REG and cn(ins, ops[0].reg) in reg:
                reg[cn(ins, ops[0].reg)] = t
            continue
        if m in ('sub', 'add') and len(ops) == 2 and ops[0].type == OP_REG and ops[0].reg == X.X86_REG_ESP and \
                ops[1].type == OP_IMM:
            k = (ops[1].imm & 0xffffffff) // 4
            if m == 'sub':
                stack.extend([_NO] * k)
            else:
                for t in stack[max(0, len(stack) - k):]:
                    sinks |= set(t)
                del stack[max(0, len(stack) - k):]
            continue
        if m.startswith('f'):
            mem = next((o for o in ops if o.type == OP_MEM), None)
            if m in ('fstp', 'fst', 'fistp', 'fist', 'fisttp') and mem is not None:
                t = st['st0']
                d = slot_of(mem)
                if mem.mem.base == X.X86_REG_ESP and mem.mem.index == 0 and mem.mem.disp == 0 and stack:
                    stack[-1] = t
                elif d is not None:
                    slots[d] = t
                    if t:
                        temps.add(d)
                elif t:
                    return None                 # a result stored through another address: a side effect
                if m != 'fst' and m != 'fist':
                    st['st0'] = _NO
            elif m.startswith(('fld', 'fild')):
                st['st0'] = mem_taint(ins, mem) if mem is not None else _NO
            elif m.startswith(('fcom', 'fucom', 'ftst', 'fnstsw', 'fstsw')):
                sinks |= set(st['st0'])
            elif mem is not None:
                st['st0'] = st['st0'] | mem_taint(ins, mem)
            continue
        # everything else: the registers and slots read -> the registers and slots written
        try:
            rd, wr = ins.regs_access()
        except Exception:  # noqa: BLE001
            return None
        t = _NO
        for r in rd:
            c = cn(ins, r)
            if c in reg and c not in ('sp', 'bp'):
                t = t | reg[c]
        for o in ops:
            if o.type == OP_MEM and (o.access & capstone.CS_AC_READ):
                d = slot_of(o)
                if d is not None:
                    t = t | slots.get(d, _NO)
        if m in ('xor', 'sub') and len(ops) == 2 and ops[0].type == ops[1].type == OP_REG and \
                ops[0].reg == ops[1].reg:
            t = _NO
        wrote = False
        for o in ops:
            if o.type == OP_MEM and (o.access & capstone.CS_AC_WRITE):
                wrote = True
                d = slot_of(o)
                if d is not None:
                    slots[d] = t
                    if t:
                        temps.add(d)
                elif t:
                    return None                 # a result stored through another address: a side effect
        for r in wr:
            c = cn(ins, r)
            if c in reg and c not in ('sp', 'bp'):
                reg[c] = t
                wrote = True
        if m == 'lea' and len(ops) == 2 and ops[1].type == OP_MEM and ops[0].type == OP_REG:
            t2 = _NO
            for r in (ops[1].mem.base, ops[1].mem.index):
                if r:
                    t2 = t2 | reg.get(cn(ins, r), _NO)
            reg[cn(ins, ops[0].reg)] = t2
        elif not wrote:
            sinks |= set(t)                     # cmp / test: the expression consumes the value
        if done():
            break
    if not done():
        return None
    # the temporaries must be dead: nothing after the expression reads them (a variable would be read again)
    live = set(temps)
    for ins in _insns(last.address + last.size, 100):
        if not live or ins.mnemonic in ('ret', 'retn'):
            break
        for o in ins.operands:
            if o.type == OP_MEM and o.mem.base == X.X86_REG_EBP and o.mem.index == 0 and o.mem.disp in live:
                if o.access & capstone.CS_AC_READ:
                    return None
                live.discard(o.mem.disp)
    return {i: frozenset(j for j in closure(deps.get(i, ())) if j < n and j != i) for i in range(n)}
