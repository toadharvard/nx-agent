"""classify.py - root-cause hints for one minimised divergence (probes + the oracle's instruction mix).

Causes (first that applies):
  x86-custom-cc   the oracle reads a register its declared convention does not pass (MSVC whole-program custom
                  convention, e.g. a pointer in eax): an oracle-model gap, not a port bug
  split-global    the native function reaches its unit's own copy of an image variable (S2 rebinding bypassed by a
                  section-relative relocation) while the oracle uses the image copy
  nan-bits        only NaN payload/sign differ (x87 real indefinite vs AArch64 default NaN)
  vptr-store      only x86 stores an image vftable address (dead vptr store GCC drops in a destructor; D7 objects
                  hold Itanium vptrs anyway)
  division        x86 raised #DE (div by zero / INT_MIN/-1) where AArch64 sdiv/udiv returns 0 or wraps
  ilp32-pointer   AArch64 touched an address >= 4 GiB (a 32-bit pointer used with garbage upper bits)
  uninit-x86      the oracle's own result depends on garbage (stack/registers): undefined behaviour MSVC got away with
  uninit-arm      the native result depends on garbage: uninitialised read or an ABI slot the caller never wrote
  float->int      x86 truncation gives 0x80000000 (integer indefinite) where AArch64 fcvtz* saturates
  x87-precision   float/double result within tolerance (x87 80-bit intermediates vs AArch64 IEEE single/double)
  float           other float result difference (NaN/inf, larger than tolerance)
  bool-repr       an input byte that is not 0/1 is read as bool (invalid representation; MSVC callers never pass it)
  signedness      the divergence disappears when an input byte >= 0x80 loses its top bit (char/short widening)
  shift-count     a shift count >= the operand width reaches the divergence
  call-flow       the outgoing call sequence differs (control flow; check inlining, rules, DECL1 rebinds)
  callee-saved    an ABI break: callee-saved registers clobbered
  unclassified
"""
import struct

import capstone

import check_rebind
import config
import world

CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)


def mnemonics(fn):
    return {i.mnemonic for i in CS.disasm(fn['code'], fn['address'])}


CSD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CSD.detail = True
GPR = {'eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ax', 'al', 'ah', 'cx', 'cl', 'ch', 'dx', 'dl', 'dh', 'bx', 'bl', 'bh',
       'si', 'di'}
BASE = {'ax': 'eax', 'al': 'eax', 'ah': 'eax', 'cx': 'ecx', 'cl': 'ecx', 'ch': 'ecx', 'dx': 'edx', 'dl': 'edx',
        'dh': 'edx', 'bx': 'ebx', 'bl': 'ebx', 'bh': 'ebx', 'si': 'esi', 'di': 'edi'}


def _register_idiom(ins):
    """An instruction capstone lists as reading a register whose value it does not use (difffuzz-20, F1b): 'nop'
    for MSVC's alignment padding (`lea ecx, [ecx]`, `lea ebx, [ebx+0]`, `mov edi, edi`, `xchg r, r`), the register
    for a write that ignores the old value (`or r, -1`, `and r, 0`); None otherwise. Taking them for reads made a
    custom register argument of a register the function only overwrites."""
    if ins.mnemonic.startswith('nop'):
        return 'nop'
    ops = ins.operands
    if len(ops) != 2 or ops[0].type != capstone.x86.X86_OP_REG:
        return None
    r0 = ins.reg_name(ops[0].reg)
    if ins.mnemonic == 'lea' and ops[1].type == capstone.x86.X86_OP_MEM:
        m = ops[1].mem
        if m.base == ops[0].reg and m.index == 0 and m.disp == 0 and m.segment == 0:
            return 'nop'
        return None
    if ins.mnemonic in ('mov', 'xchg') and ops[1].type == capstone.x86.X86_OP_REG and ops[1].reg == ops[0].reg:
        return 'nop'
    if ops[1].type == capstone.x86.X86_OP_IMM and ops[0].size == 4:
        v = ops[1].imm & 0xffffffff
        if (ins.mnemonic == 'or' and v == 0xffffffff) or (ins.mnemonic == 'and' and v == 0):
            return BASE.get(r0, r0)
    return None


def undeclared_stack_reads(fn, conv):
    """Stack parameter offsets (bytes from the first argument word) the oracle function reads past the stack
    parameters its declaration has: `[esp+N]` before the first call (esp followed through push/pop/sub/add), or
    `[ebp+N]` in a frame-pointer function. The decomp declares fewer parameters than the exe function takes: every
    caller passes too little, and the native reads registers / slots the exe's callers fill (0x00d32b40: declared
    (int, int, int), the exe reads the words at +0x10 and +0x14 too). [] when the declaration covers every read."""
    extent = max([off + size for _, sp, off, size in conv.x86 if sp == 'stk'] + [0])
    delta, ebp, out = 0, None, set()
    for ins in CSD.disasm(fn['code'], fn['address']):
        m = ins.mnemonic
        if m in ('call', 'ret', 'retn', 'jmp') or m.startswith('loop'):
            break
        for op in ins.operands:
            if op.type == capstone.x86.X86_OP_MEM and op.mem.index == 0:
                base = ins.reg_name(op.mem.base) if op.mem.base else None
                if base == 'esp':
                    out.add(delta + op.mem.disp - 4)
                elif base == 'ebp' and ebp is not None and op.mem.disp > 0:
                    out.add(ebp + op.mem.disp - 4)
        if m == 'push':
            delta -= 4
        elif m == 'pop':
            delta += 4
        elif m in ('sub', 'add') and ins.op_str.startswith('esp, ') and ins.operands[1].type == \
                capstone.x86.X86_OP_IMM:
            delta += ins.operands[1].imm if m == 'add' else -ins.operands[1].imm
        elif m == 'mov' and ins.op_str == 'ebp, esp':
            ebp = delta
        elif m in ('and',) and ins.op_str.startswith('esp, '):
            return []                     # an aligned frame: esp is no longer known
    return sorted(o for o in out if o >= extent and o % 4 == 0 and o < extent + 64)


def entry_register_reads(fn, conv):
    """Registers the oracle function reads before writing them on its entry path (straight line up to the first
    branch), excluding those its declared convention passes (ecx/edx) and the callee-saved pushes: a register
    argument MSVC's whole-program optimisation added (a custom convention units.json does not record)."""
    passed = {sp for _, sp, _, _ in conv.x86 if sp in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
    written, reads = set(), []
    insns = list(CSD.disasm(fn['code'], fn['address']))
    for ix, ins in enumerate(insns):
        if ins.mnemonic == 'push' and ins.op_str in ('ebx', 'esi', 'edi', 'ebp', 'ecx') and \
                not (ins.op_str == 'ecx' and _push_is_call_arg(insns, ix)):
            continue                  # saving a callee-saved register / reserving a slot is not a use
        if ins.mnemonic in ('xor', 'sub', 'sbb') and len(ins.operands) == 2 and ins.op_str.split(', ')[0] == \
                ins.op_str.split(', ')[-1]:
            written.add(BASE.get(ins.op_str.split(', ')[0], ins.op_str.split(', ')[0]))
            continue
        idiom = _register_idiom(ins)
        if idiom == 'nop':
            continue
        if idiom:
            written.add(idiom)
            continue
        try:
            rr, ww = ins.regs_access()
        except capstone.CsError:
            break
        for r in rr:
            n = BASE.get(ins.reg_name(r), ins.reg_name(r))
            if n in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi') and n not in written and n not in passed and \
                    n not in reads:
                reads.append(n)
        for r in ww:
            written.add(BASE.get(ins.reg_name(r), ins.reg_name(r)))
        if capstone.CS_GRP_JUMP in ins.groups or capstone.CS_GRP_CALL in ins.groups or \
                capstone.CS_GRP_RET in ins.groups:
            break
    for n in _later_entry_reads(fn, passed):
        if n not in reads:
            reads.append(n)
    return reads          # in first-read order: the straight-line prefix first, then the later branches


def _push_is_call_arg(insns, ix, window=24):
    """(R4 flow) `push ecx` at the entry is MSVC's one-word stack reservation, unless the word is an argument of the
    next call: the pushes from it to a `call` followed by `add esp, N` number no more than N / 4, so the call's
    cleanup consumes it (0x00d409b0: `push eax; push ecx; push esi; call; add esp, 0xc` passes ecx on). A reservation
    is followed by fewer cleanup words than pushes (or by none) and stays padding."""
    pushes = 0
    for j in range(ix, min(ix + window, len(insns))):
        m = insns[j].mnemonic
        if m == 'push':
            pushes += 1
        elif m == 'call':
            # the cleanup may follow a few plain instructions (0x00d409b0: `mov dl, [esi+2]; mov eax, [esp+0x10]`)
            for nxt in insns[j + 1:j + 7]:
                if nxt.mnemonic == 'add' and nxt.op_str.startswith('esp, '):
                    try:
                        return pushes <= int(nxt.op_str.split(', ')[1], 0) // 4
                    except ValueError:
                        return False
                if nxt.mnemonic in ('call', 'push', 'pop', 'ret', 'leave') or nxt.mnemonic.startswith('j'):
                    return False
            return False
        elif m in ('pop', 'ret', 'jmp') or m.startswith('j') or m in ('leave',) or \
                (m in ('sub', 'add') and insns[j].op_str.startswith('esp')):
            return False
    return False


def _later_entry_reads(fn, passed, limit=20000):
    """Registers some path from the entry reads before writing it (difffuzz-20, stores class): the straight-line
    scan stops at the first branch, so a register argument first used on a later path (0x00db0e80 takes its second
    pointer in edi and stores through it after a `jp`) was left out of the custom convention and the oracle stored
    through garbage. Path-sensitive: (address, registers written so far) states over the function's own direct
    jumps; a call clobbers eax/ecx/edx; an indirect jump or ret ends a path."""
    insns = {i.address: i for i in CSD.disasm(fn['code'], fn['address'])}
    lo, hi = fn['address'], fn['address'] + len(fn['code'])
    found, seen = [], set()
    stack = [(fn['address'], frozenset())]
    steps = 0
    while stack and steps < limit:
        addr, written = stack.pop()
        while addr in insns and (addr, written) not in seen and steps < limit:
            seen.add((addr, written))
            steps += 1
            ins = insns[addr]
            nxt = addr + ins.size
            if ins.mnemonic == 'push' and ins.op_str in ('ebx', 'esi', 'edi', 'ebp', 'ecx') and \
                    not (ins.op_str == 'ecx' and _push_is_call_arg(list(insns.values()), list(insns).index(addr))):
                addr = nxt
                continue
            if ins.mnemonic in ('xor', 'sub', 'sbb') and len(ins.operands) == 2 and \
                    ins.op_str.split(', ')[0] == ins.op_str.split(', ')[-1]:
                written = written | {BASE.get(ins.op_str.split(', ')[0], ins.op_str.split(', ')[0])}
                addr = nxt
                continue
            idiom = _register_idiom(ins)
            if idiom == 'nop':
                addr = nxt
                continue
            if idiom:
                written = written | {idiom}
                addr = nxt
                continue
            try:
                rr, ww = ins.regs_access()
            except capstone.CsError:
                break
            for r in rr:
                n = BASE.get(ins.reg_name(r), ins.reg_name(r))
                if n in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi') and n not in written and n not in passed \
                        and n not in found:
                    found.append(n)
            written = written | {BASE.get(ins.reg_name(r), ins.reg_name(r)) for r in ww}
            if capstone.CS_GRP_RET in ins.groups:
                break
            if capstone.CS_GRP_CALL in ins.groups:
                written = written | {'eax', 'ecx', 'edx'}
                addr = nxt
                continue
            if capstone.CS_GRP_JUMP in ins.groups:
                op = ins.operands[0] if ins.operands else None
                tgt = op.imm if op is not None and op.type == capstone.x86.X86_OP_IMM else None
                if tgt is not None and lo <= tgt < hi:
                    if ins.mnemonic == 'jmp':
                        addr = tgt
                        continue
                    stack.append((tgt, written))
                    addr = nxt
                    continue
                if ins.mnemonic == 'jmp':
                    break
                addr = nxt
                continue
            addr = nxt
    return found


def is_dtor_name(sym):
    """An MSVC destructor (??1, ??_G, ??_E) or an Itanium one (D0/D1/D2 at the end of a nested name)."""
    import re
    return sym.startswith(('??1', '??_G', '??_E')) or bool(re.search(r'D[012]Ev$', sym))


def partial_return(pair, case, div, max_steps):
    """(F-uncls, difffuzz-20) The oracle's last write to eax was an al / ax write and the two results agree in those
    bytes: the exe function returns a byte or short (a bool, a char) in al / ax and the rest of eax is whatever it
    held (0x008b3c70: `mov al, [eax+0x72]`, the upper bytes are the pointer it loaded through). The declaration
    says int; no caller of the exe reads more than the low bytes."""
    try:
        bx, ba = bytes.fromhex(div.get('x86') or ''), bytes.fromhex(div.get('arm') or '')
    except ValueError:
        return False
    if len(bx) != 4 or len(ba) != 4 or bx == ba:
        return False
    world.EAX_TRACK = {}
    try:
        pair.run(case, max_steps)
        last = world.EAX_TRACK.get('last')
    finally:
        world.EAX_TRACK = None
    return last in (1, 2) and bx[:last] == ba[:last]


def int_float_close(x86_hex, arm_hex):
    """Two 32-bit integer results of one float expression: both at least 2^24 in magnitude (a float's ulp is then at
    least 2) and no more than 4 float ulps (of the larger) apart."""
    try:
        bx, ba = bytes.fromhex(x86_hex or ''), bytes.fromhex(arm_hex or '')
    except ValueError:
        return False
    if len(bx) != 4 or len(ba) != 4:
        return False
    vx, va = struct.unpack('<i', bx)[0], struct.unpack('<i', ba)[0]
    big = max(abs(vx), abs(va))
    if min(abs(vx), abs(va)) < (1 << 24) or (vx < 0) != (va < 0):
        return False
    return abs(vx - va) <= 4 * (1 << (big.bit_length() - 24))


def indefinite_word(x86_hex, arm_hex):
    """An aligned 32-bit word of the x86 value is 0x80000000 (x87/SSE integer indefinite) where the native word
    differs (difffuzz-19: was a substring test for '80000000' / '0000008' anywhere in the hex)."""
    try:
        bx, ba = bytes.fromhex(x86_hex or ''), bytes.fromhex(arm_hex or '')
    except ValueError:
        return False
    for o in range(0, len(bx) - 3, 4):
        if bx[o:o + 4] == b'\0\0\0\x80' and ba[o:o + 4] != bx[o:o + 4]:
            return True
    return False


def _static_only_store(div, target):
    """The record's own evidence for local-static: a mem-write whose differing bytes are all oracle-only stores to
    the unit's function-local statics, or one extra native call followed by the oracle's call there (the native's
    guarded initializer; V2 C1); the runner's `local-static` kind covers the rest."""
    if div.get('kind') == 'call-target' and div.get('x86') and div.get('arm_next') == div.get('x86') and \
            div.get('x86_next') != div.get('arm'):     # (x86_next == arm too: two calls swapped, V2 L1)
        return True     # the native makes one extra call (its own static's guarded initializer), then the oracle's
    if div.get('kind') != 'mem-write' or div.get('only_x86') != div.get('ndiff'):
        return False
    st = getattr(target, 'local_statics', None)
    addrs = [int(a, 16) for a in div.get('addrs') or ()]
    return bool(addrs) and st is not None and all(a in st for a in addrs)


def _outcome(r):
    return (r.error, r.ret, tuple(c.target for c in r.calls), tuple(sorted(r.writes.items())))


def _observable(pair, r, div, side):
    """What a machine produced for the value a return / mem-write / call-args divergence is about: the raw argument,
    the result bytes, the bytes stored at the differing addresses (the garbage probe of a random-background case)."""
    k = div.get('kind')
    if k == 'call-args':
        return _arg_raw(pair, r, div, side)
    if k == 'return':
        return r.ret
    if k == 'mem-write':
        return tuple(r.writes.get(int(a, 16)) for a in div.get('addrs') or ())
    return None


def _arg_raw(pair, r, div, side):
    """The raw bytes a machine passed for the argument a `call-args` divergence names (before the compare's bool
    normalisation), or None for any other divergence. _outcome holds the return value, the calls' targets and the
    stores, not what the calls were given: an argument that is a byte of an uninitialised local (the out-parameter
    of an intercepted callee, which the harness does not model) reached the compare as a divergence whatever the
    garbage was (0x005b6cd0, 0x00805920, 0x006ab8e0, 0x006fe830, 0x00b6d170)."""
    import runner
    if div.get('kind') != 'call-args' or r is None:
        return None
    try:
        calls = r.calls
        call = calls[div['index']]
        cv = pair.target.callee_info.conv(call.target)
        dec = runner.decode_x86 if side == 'x86' else runner.decode_a64
        return dec(cv, call).get(div['arg'])
    except Exception:  # noqa: BLE001 - no convention for the callee, or the call is not there: no evidence
        return None


_VFT = None


def _benign(d):
    """No divergence, or only a by-design one (soft, or NaN payload / sign bits): what a probe trial may leave
    (F1b: 0x00c207a0's bool byte set to 1 leaves an x87 0xffc00000 vs AArch64 0x7fc00000 store)."""
    if d is None or d.get('soft'):
        return True
    if d.get('kind') in ('mem-write', 'return'):
        try:
            bx, ba = bytes.fromhex(d.get('x86') or ''), bytes.fromhex(d.get('arm') or '')
        except ValueError:
            return False
        return len(bx) == len(ba) and len(bx) >= 4 and _nan_only(bx, ba)
    return False


def _nan_only(bx, ba):
    """Every differing aligned word is a NaN on both sides (payload/sign differ: x87 'real indefinite' 0xffc00000
    vs AArch64 default NaN 0x7fc00000)."""
    diff = False
    for i in range(0, len(bx) - 3, 4):
        x, a = struct.unpack_from('<I', bx, i)[0], struct.unpack_from('<I', ba, i)[0]
        if x == a:
            continue
        diff = True
        if not ((x & 0x7f800000) == 0x7f800000 and x & 0x7fffff and (a & 0x7f800000) == 0x7f800000 and a & 0x7fffff):
            return False
    return diff


def image_vftables():
    """Original addresses of image vftables (address map kind V)."""
    global _VFT
    if _VFT is None:
        _VFT = set()
        p = config.LINK / 'out/addrmap.tsv'
        if p.exists():
            for line in open(p):
                f = line.split('\t')
                if len(f) > 1 and f[1] == 'V':
                    _VFT.add(int(f[0], 16))
    return _VFT


def oracle_vcall(pair, case, max_steps):
    """The oracle faulted on an indirect call through a fuzzed object (loading its vptr or slot, or calling the
    loaded address) that the native code never makes (GCC devirtualised it, or the call is on a path it does not
    take with these canned results). Indirect calls are not modelled: an oracle gap."""
    import capstone
    from unicorn.x86_const import UC_X86_REG_EIP
    rx, _, _ = pair.run(case, max_steps)
    if not str(rx.error or '').startswith('fault:'):
        return False
    uc = pair.xe.uc
    pc = uc.reg_read(UC_X86_REG_EIP)
    try:
        code = bytes(uc.mem_read(pc, 24))
    except Exception:  # noqa: BLE001
        return str(rx.error).startswith('fault:x:')     # the call itself jumped to an unmapped address
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for i, ins in enumerate(cs.disasm(code, pc)):
        if i >= 4:
            break
        if ins.mnemonic == 'call' and not ins.op_str.startswith('0x'):
            return True                                 # call [reg+N] / call reg within a few instructions
    return False


def msvc_inlined(target, div):
    """The native code calls X out of line where the oracle runs X's body inline (MSVC inlined it; the oracle's
    next call is one X itself makes): the native side would have to run X too. An oracle-model gap."""
    callees = getattr(target, 'oracle_callees', None)
    try:
        a = int(div['arm'], 16)
    except (TypeError, ValueError, KeyError):
        return False
    x = div.get('x86') or ''
    if x.startswith('imp:'):
        import libcalls
        xv = libcalls.import_id(x[4:])
    else:
        try:
            xv = int(x, 16)
        except ValueError:
            return False
    return bool(callees) and xv in callees(a)


def garbage_probe(pair, case, max_steps, h):
    """(float class) The determinism probe below, for a case with a random background (it skips those: the seed that
    changes the garbage also redraws the background pages): only the garbage changes (world.GARBAGE_XOR). A float
    result computed from an uninitialised stack slot (0x00c70550: the native zero-initialises the struct, MSVC does
    not) changes with the garbage on one machine only."""
    base = case.copy()
    base.rets = [case.ret_bytes(i) for i in range(max(8, len(case.rets)))]
    rx0, ra0, _ = pair.run(base, max_steps)
    world.GARBAGE_XOR = 0x13572468
    try:
        rx1, ra1, _ = pair.run(base, max_steps)
    finally:
        world.GARBAGE_XOR = 0
    h['_runs'] += 2
    ux, ua = _outcome(rx0) != _outcome(rx1), _outcome(ra0) != _outcome(ra1)
    if not (ux or ua):
        return False
    h['cause'] = 'uninit-x86' if ux and not ua else 'uninit-arm' if ua and not ux else 'uninit-both'
    if ux and ua:
        world.UNIFORM = True
        try:
            _, _, du = pair.run(base, max_steps)
        finally:
            world.UNIFORM = False
        h['_runs'] += 1
        h['uniform'] = 'agree' if du is None else du.get('kind')
    return True


def x87_probes(pair, case, div, max_steps, h):
    """(float class) The compilers' evaluation methods, shown by rerunning the oracle until it agrees with the native:
      x87-precision       the x87 control word at 24-bit precision (every x87 operation rounds to a float, as on
                          AArch64): the whole difference is the 80/53-bit intermediate (an excess-precision
                          cancellation shows up as a large ulp distance);
      callee-float-width  the other guess of the width an untyped callee returns its float in (float or double: the
                          consuming `fstp dword` / `fstp qword` cannot tell) - an artifact (0x00b4df80: a
                          function-pointer call the native converts from d0);
      x87-range           the 24-bit control word with every x87 register value beyond the float exponent range
                          flushed to inf / zero (or the 53-bit one with the double range): the x87 stack keeps a
                          15-bit exponent at every precision, so a float / double machine overflows or underflows
                          where it does not (the record keeps which range and how many values it flushed).
    None fires when the native uses arithmetic the oracle rerun does not model, or the difference has another cause."""
    def rerun():
        _, _, d = pair.run(case, max_steps)
        h['_runs'] += 1
        return _benign(d)
    world.X87_CW = 0x007f
    try:
        if rerun():
            h['cause'] = 'x87-precision'; h['x87_pc24'] = True
            return True
    finally:
        world.X87_CW = 0x027f
    world.X87_FLIP_WIDTH = True
    try:
        if rerun():
            h['cause'] = 'callee-float-width'
            return True
    finally:
        world.X87_FLIP_WIDTH = False
    world.TRIG_INF = True
    try:
        if rerun():
            h['cause'] = 'x87-trig-inf'
            return True
    finally:
        world.TRIG_INF = False
    for kind, cw in (('f32', 0x007f), ('f64', 0x027f)):
        rec = {'kind': kind}
        world.X87_CLAMP, world.X87_CW = rec, cw
        try:
            ok = rerun()
        finally:
            world.X87_CLAMP, world.X87_CW = None, 0x027f
        if ok and rec['n']:
            h['cause'] = 'x87-range'
            h['x87_range'] = {'range': kind, 'clamped': rec['n'], 'over': rec.get('over', 0), 'under': rec.get('under', 0),
                              'after': '%08x' % rec['addr']}
            return True
def arg_narrow_top(target, fn, lab, i):
    """(difffuzz-20, sign-args) May byte i of argument `lab` be widened by the oracle code? Only the top byte of a
    1- or 2-byte value is (a char / short parameter; a declared-int parameter the exe reads through a 1- or 2-byte
    [ebp+d] access over that byte). The sign bit of a float argument, or any bit of a 4-byte integer the exe reads
    whole, is not a sign extension: clearing it changes a compare or a branch (0x00e30fb0, 0x00afd390, 0x00abb700)."""
    conv = getattr(target, 'conv', None)
    val = next((v for v in getattr(conv, 'vals', ()) if v.label == lab), None)
    if val is None:
        return True                     # unknown parameter: keep the earlier behaviour
    if val.is_float or val.is_ptr:
        return False
    if val.size in (1, 2):
        return i == val.size - 1
    slot = next((off for l, sp, off, _ in conv.x86 if l == lab and sp == 'stk'), None)
    if slot is None or val.size != 4 or val.kind != 'gpr':
        return False
    try:
        for ins in CSD.disasm(fn['code'], fn['address']):
            for op in ins.operands:
                if op.type == capstone.x86.X86_OP_MEM and op.size in (1, 2) and \
                        ins.reg_name(op.mem.base) == 'ebp' and not op.mem.index:
                    if op.mem.disp - 8 + op.size - 1 == slot + i:
                        return True
    except capstone.CsError:
        return False
    return False


_IN_CHAIN = False


def _bool_chain(pair, target, case, div, fn, max_steps, h, rounds=8):
    """(r4 uncls) bool-repr when it takes SEVERAL bytes: a function reads a bool flag, then more of them (the early
    exit of 0x006141f0 depends on eight random-background bytes, one per branch), so no single byte removes the
    divergence the probes above look for. Every round plants 1 in the 1-byte reads the oracle's code makes of a byte
    above 1 (random background or planted memory) and runs again; once the machines agree, the planted set is
    minimised (a byte whose removal leaves the agreement is left out) and returned. When a divergence remains
    after the last round that is itself a by-design or artifact cause (the divergence the early exit hid, named by a
    nested `hint`; h['then'] keeps its name), the earlier one was bool-repr too; anything else leaves the record
    where it was (a real second divergence is not hidden behind the bool bytes)."""
    global _IN_CHAIN
    if case.bg == 'zero' and not any(x > 1 for b in case.mem.values() for x in b):
        return []
    cur, planted = case.copy(), []
    d = div
    _IN_CHAIN = True
    try:
        for _ in range(rounds):
            if h['_runs'] >= 45:
                return []
            reads, bg = {}, {}
            world.BYTE_READS, world.BG_READS = reads, bg
            try:
                pair.run(cur, max_steps)
            finally:
                world.BYTE_READS = world.BG_READS = None
            h['_runs'] += 1
            new = []
            for a, b in cur.mem.items():
                for i, x in enumerate(b):
                    if x > 1 and reads.get(a + i) == {1} and \
                            not any(s > k for k in range(1, 8) for s in reads.get(a + i - k, ())):
                        new.append(a + i)
            held = [(a, a + len(b)) for a, b in cur.mem.items()]
            for a in sorted(bg):
                sizes, v = bg[a]
                if sizes == {1} and v is not None and v > 1 and a not in planted and \
                        not any(lo <= a < hi for lo, hi in held) and \
                        not any(s > k for k in range(1, 8) for s in bg.get(a - k, (set(),))[0]):
                    new.append(a)
            new = [a for a in new if a not in planted][:4]
            if not new:
                break
            for a in new:
                _plant(cur, a)
                planted.append(a)
            _, _, d = pair.run(cur, max_steps)
            h['_runs'] += 1
            if _benign(d):
                break
        if not planted:
            return []
        if d is not None and not _benign(d):
            # the early exit hid another divergence: only a call-flow divergence the planted bytes cleared (the calls
            # now agree and a later difference shows) can; it must be named by the other probes, and a soft one
            if div.get('kind') not in ('call-target', 'call-count') or d.get('kind') in ('call-target', 'call-count'):
                return []
            import causes
            sub = hint(pair, target, cur, d, fn, max_steps)
            h['_runs'] += sub.get('_runs', 0)
            c = sub.get('cause')
            h['then'] = c
            if c in (None, 'unclassified', 'call-flow', 'bool-repr') or \
                    (c == 'uninit-both' and sub.get('uniform') != 'agree'):
                return []
            if causes.policy(c)[2]:
                h['chain_cause'] = c        # a real second divergence behind the bool bytes: named, not hidden
                return []
        keep = list(planted)
        for a in list(planted):
            if d is not None and not _benign(d):
                break                       # the remaining divergence is the nested cause's: no minimisation
            if len(keep) <= 1 or h['_runs'] >= 55:
                break
            t = case.copy()
            for b in keep:
                if b != a:
                    _plant(t, b)
            _, _, dd = pair.run(t, max_steps)
            h['_runs'] += 1
            if _benign(dd):
                keep.remove(a)
        return ['%08x' % a for a in keep]
    finally:
        _IN_CHAIN = False


def _plant(c, a):
    """1 at address a of the case's memory (inside a planted block, or as a new one-byte block)."""
    for base, b in list(c.mem.items()):
        if base <= a < base + len(b):
            nb = bytearray(b); nb[a - base] = 1; c.mem[base] = bytes(nb)
            return
    c.mem[a] = b'\x01'


def hint(pair, target, case, div, fn, max_steps):
    h = dict(_runs=0)
    mn = mnemonics(fn)
    k = div['kind']
    if k == 'local-static':
        h['cause'] = 'local-static'; return h
    if k == 'error' and str(div.get('arm') or '').startswith('trap:'):
        # the native called a vtable slot the link filled with its trap stub (a virtual defined nowhere,
        # arm64emu.TRAPS): the native calls a name the link binds to nothing (difffuzz-14)
        h['cause'] = 'native-undefined'; h['trap'] = div['arm'][5:]; return h
    if div.get('bool_repr'):
        h['cause'] = 'bool-repr'; return h
    if div.get('nrvo_copy'):
        h['cause'] = 'nrvo-copy'; return h
    if div.get('nrvo_elided'):
        h['cause'] = 'nrvo-elision'; return h
    if div.get('nan_bits'):
        h['cause'] = 'nan-bits'; return h
    if div.get('split_delete'):
        h['cause'] = 'split-delete'; return h
    if div.get('after_perm'):
        h['cause'] = 'eval-order-args'; return h
    if div.get('eval_order'):
        h['cause'] = 'eval-order'; return h
    if div.get('null_delete'):
        h['cause'] = 'null-delete'; return h
    if div.get('vptr_store'):
        # only a destructor's vptr resets differ (runner.dtor_vptr_bytes, F2 d21-dtorvptr): by design
        h['cause'] = 'vptr-store'; return h
    if k in ('call-target', 'call-count') and any(str(x).startswith('name:__s13_vtpad_')
                                                  for x in [div.get('arm')] + list(div.get('arm_trace') or [])):
        # the native call went through a slot past the native vtable the link pairs with the planted vftable
        # (vtmodel.py padding): a pairing gap of S2c, not this function's behaviour; tested
        # before `unplanted`, which would file it as oracle-vcall (S13b)
        h['cause'] = 'vptr-unpaired'; return h
    if (k == 'call-count' or (k == 'call-target' and div.get('arm_vcall') == 'planted')) and \
            any(s.get('shift') == -1 and (k == 'call-count' or s.get('ran')) for s in div.get('vslot') or []):
        # the native's extra call is planted slot k, the oracle ran slot k+1 (unit code) for real (Pair.vslots_run)
        h['cause'] = 'vslot-shift'; return h
    if k == 'call-target' and div.get('vslot_native'):
        # the native's vtable slot calls another exe function than the exe's vftable slot (runner.Pair.vslot_native)
        h['cause'] = 'native-vslot-differs'; return h
    if k == 'call-target' and div.get('vcall') == 'planted' and div.get('arm_vcall') == 'planted':
        # both machines called a slot of the planted vftable, different slots (runner.Pair.vslots): the native
        # numbers this class's slots otherwise; one lower is D26 (b)'s call-only placeholder declaration (F2)
        sh = any(s.get('shift') == -1 for s in div.get('vslot') or [])
        h['cause'] = 'vslot-shift' if sh else 'vslot-differs'; return h
    if k == 'call-target' and div.get('vcall') == 'unplanted':
        # an indirect call through memory the case did not plant (an object's vptr the fuzzer filled with bytes):
        # each machine reads its own slot layout (Itanium has two destructor slots), not a port divergence
        h['cause'] = 'oracle-vcall'; return h
    if k == 'call-target' and msvc_inlined(target, div):
        h['cause'] = 'msvc-inlined'; return h
    regs = entry_register_reads(fn, target.conv)
    if regs:
        # the modelled custom convention (Conv.with_registers) passed fuzz.py's garbage gate for the function; this
        # case is an oracle gap only when its own oracle outcome still depends on the planted garbage (difffuzz-19:
        # every divergence of such a function was x86-custom-cc, 206 records, real causes among them)
        h['regs'] = regs
        dep = True
        if case is not None and pair is not None and case.bg == 'zero':
            b0 = case.copy(); b0.rets = [case.ret_bytes(i) for i in range(max(8, len(case.rets)))]
            b1 = b0.copy(); b1.seed = case.seed ^ 0x2468ace1
            r0, _, _ = pair.run(b0, max_steps)
            r1, _, _ = pair.run(b1, max_steps)
            h['_runs'] += 2
            dep = _outcome(r0) != _outcome(r1)
        if dep:
            h['cause'] = 'x86-custom-cc'; return h
    src = getattr(target, 'source', None)
    if src and k in ('return', 'mem-write', 'call-args', 'error'):
        obj = config.LINK / 'fixed' / config.obj_name(src)
        if obj.exists():
            hits = check_rebind.bypassing_functions(str(obj)).get(target.orig_addr)
            if hits:
                h['cause'] = 'split-global'; h['vars'] = hits; return h
    if div.get('arm_local_static') and _static_only_store(div, target):
        # evidence in the record: every differing byte is an oracle-only store into a function-local static the
        # native keeps in its .bss (S13b audit, V2 L1: the flag alone filed 344 call/error records, among them a
        # swapped call pair, as local-static)
        h['cause'] = 'local-static'; return h
    if div.get('arm_private_reads') and k in ('return', 'mem-write', 'call-args', 'error', 'call-target'):
        h['cause'] = 'split-global'; h['note'] = 'native code read its unit-local data'; return h
    if k in ('mem-write', 'return'):
        bx, ba = bytes.fromhex(div.get('x86') or ''), bytes.fromhex(div.get('arm') or '')
        if len(bx) == len(ba) and len(bx) >= 4 and _nan_only(bx, ba):
            h['cause'] = 'nan-bits'; return h
    if k == 'mem-write' and div.get('only_x86') == div.get('ndiff') and div.get('ndiff', 0) <= 4:
        w = int.from_bytes(bytes.fromhex(div['x86'])[:4], 'little')
        if w in image_vftables():
            # the dead vptr store GCC drops is a destructor's; elsewhere (InitFromSource, Clone...) it is a
            # difference to look at (S13b audit)
            sym = getattr(getattr(target, 'conv', None), 'name', '') or ''
            h['cause'] = 'vptr-store' if is_dtor_name(sym) else 'vptr-differs'; return h
    if k == 'error':
        if div.get('x86') == 'div0':
            h['cause'] = 'division'; return h
        if 'ilp32-high' in str(div.get('arm')):
            h['cause'] = 'ilp32-pointer'; return h
        if str(div.get('x86', '')).startswith('fault:') and div.get('arm') is None and oracle_vcall(pair, case,
                                                                                                    max_steps):
            h['cause'] = 'oracle-vcall'; return h
    if k == 'callee-saved':
        h['cause'] = 'callee-saved'; return h
    isfloat = False
    # determinism probes: only garbage changes (stack fill, non-argument registers, argument padding)
    # (F-uncls, difffuzz-20) a random-background case is probed too, with the background pinned (world.BG_SEED), but
    # only for the value the divergence is about (an argument, the result, the stored bytes): its
    # whole outcome also moves with things that say nothing about this divergence
    if case.bg == 'zero' or k in ('call-args', 'return', 'mem-write'):
        alt = case.copy(); alt.seed = case.seed ^ 0x13572468
        alt.rets = [case.ret_bytes(i) for i in range(max(8, len(case.rets)))]
        base = case.copy(); base.rets = list(alt.rets)
        world.BG_SEED = case.seed if case.bg != 'zero' else None
        try:
            rx0, ra0, _ = pair.run(base, max_steps)
            rx1, ra1, _ = pair.run(alt, max_steps)
        finally:
            world.BG_SEED = None
        h['_runs'] += 2
        if case.bg == 'zero':
            ux = _outcome(rx0) != _outcome(rx1) or _arg_raw(pair, rx0, div, 'x86') != _arg_raw(pair, rx1, div, 'x86')
            ua = _outcome(ra0) != _outcome(ra1) or _arg_raw(pair, ra0, div, 'a64') != _arg_raw(pair, ra1, div, 'a64')
        else:
            ux = _observable(pair, rx0, div, 'x86') != _observable(pair, rx1, div, 'x86')
            ua = _observable(pair, ra0, div, 'a64') != _observable(pair, ra1, div, 'a64')
        if ua and not ux:
            # (difffuzz-20, sign-outparam) the garbage the native reads may be a buffer it passed to a callee the
            # harness stubs (the real callee fills it; MSVC passed its own destination, GCC a temporary it copies
            # back): zero the stack words of such buffers that the native never stored to and run both seeds again
            world.STUB_OUT = True
            try:
                d0, d1 = pair.run(base, max_steps), pair.run(alt, max_steps)
            finally:
                world.STUB_OUT = False
            h['_runs'] += 2
            if _outcome(d0[1]) == _outcome(d1[1]) and _benign(d0[2]) and _benign(d1[2]):
                h['cause'] = 'stub-outparam'; return h
        if ux or ua:
            h['cause'] = 'uninit-x86' if ux and not ua else 'uninit-arm' if ua and not ux else 'uninit-both'
            if ux and ua:
                # both machines read garbage: the same garbage (all zero) on both tells whether anything else
                # differs (difffuzz-18). 'agree': nothing does, fuzz.py keeps no record of it
                world.UNIFORM = True
                try:
                    _, _, du = pair.run(base, max_steps)
                finally:
                    world.UNIFORM = False
                h['_runs'] += 1
                h['uniform'] = 'agree' if du is None else du.get('kind')
            return h
    if k == 'call-args' and pair is not None and h['_runs'] < 40:
        # (R4 flow, stub-outparam both ways) an argument that is an uninitialised local passed on after an
        # intercepted call that should have filled it: the words the function never stored to are 0 on both
        # machines (world.STUB_OUT zeroes them in the oracle too). A rerun that agrees names the cause
        world.STUB_OUT = True
        try:
            _, _, d_so = pair.run(case, max_steps)
        finally:
            world.STUB_OUT = False
        h['_runs'] += 1
        if _benign(d_so):
            h['cause'] = 'stub-outparam'; return h
    if k in ('return', 'mem-write'):
        fl = div.get('float') or div.get('float_word')
        conv_ops = {'fistp', 'fist', 'cvttss2si', 'cvttsd2si', 'cvtss2si', 'cvtsd2si', 'fisttp'}
        if (mn & conv_ops) and indefinite_word(div.get('x86'), div.get('arm')):
            h['cause'] = 'float->int'; return h
        if k == 'return' and pair is not None and any(m.startswith('f') for m in mn):
            # (F-uncls, difffuzz-20) an integer converted from a float expression: the same mechanism probe as a
            # float result (x87 control word at 24 bits, world.X87_CW): the divergence is x87-precision when the
            # rerun matches the native. The distance in float ulps is recorded, not decided on (0x00603fc0)
            world.X87_CW = 0x007f
            try:
                _, _, d24 = pair.run(case, max_steps)
            finally:
                world.X87_CW = 0x027f
            h['_runs'] += 1
            if _benign(d24):
                h['cause'] = 'x87-precision'; h['x87_pc24'] = True
                h['int_float_close'] = int_float_close(div.get('x86'), div.get('arm'))
                return h
        if div.get('zero_sign') and pair is not None and any(m.startswith('f') for m in mn) and \
                x87_probes(pair, case, div, max_steps, h):
            # (difffuzz-20, zero-sign) -0.0 against +0.0: an x87 intermediate beyond the float range keeps its sign
            # until the store rounds it (0x00c206b0: -4.5e-44 * 4.5e-44 = -2e-87, then + 0.0 stays negative); the
            # rerun that clamps such values (x87-range) is the evidence, not the equal numeric value
            return h
        if fl:
            if fl.get('tolerant'):
                h['cause'] = 'x87-precision' if any(m.startswith('f') for m in mn) else 'float-tolerance'
                return h
            # (float class, difffuzz-20) a float difference beyond the tolerance is not yet a float bug: the x87
            # probes tell the two compilers' evaluation methods apart, and the probes below (bool byte, signedness)
            # still run before `float` is the answer
            isfloat = True
            if pair is not None and case.bg != 'zero' and garbage_probe(pair, case, max_steps, h):
                return h
            if pair is not None and any(m.startswith('f') for m in mn) and x87_probes(pair, case, div, max_steps, h):
                return h
    # bool representation probe: a byte that is neither 0 nor 1 read as bool (MSVC tests the whole byte, GCC may
    # test bit 0 only): set each such input byte to 1
    # S1-rules: only a byte the MSVC code reads by itself (a 1-byte access at that address, no wider access over it)
    # is a bool; a byte of a pointer or integer word that removes the divergence when set to 1 is not (before:
    # ~80 % of bool-repr records were such bytes, mostly the 0x03 top byte of a planted pointer)
    reads = {}
    if pair is not None and any(x > 1 for b in case.mem.values() for x in b):
        world.BYTE_READS = reads
        try:
            pair.run(case, max_steps)
        finally:
            world.BYTE_READS = None
        h['_runs'] += 1

    def byte_read(addr):
        if reads.get(addr) != {1}:
            return False
        return not any(s > k for k in (1, 2, 3, 4, 5, 6, 7) for s in reads.get(addr - k, ()))
    bhits = []
    for a, b in list(case.mem.items()):
        for i, x in enumerate(b):
            if x > 1 and len(bhits) < 4 and h['_runs'] < 30 and byte_read(a + i):
                trial = case.copy()
                nb = bytearray(b); nb[i] = 1; trial.mem[a] = bytes(nb)
                _, _, d = pair.run(trial, max_steps); h['_runs'] += 1
                if _benign(d):
                    bhits.append('%08x' % (a + i))
    if not bhits and pair is not None and h['_runs'] < 30:
        # (R4 flow) the same for a one-byte argument (a by-value aggregate of a single bool, `BoolArg { bool v; }`
        # passed in dl: 0x00572a50, MSVC tests the byte, GCC bit 0): replace a byte above 1 by 1; only a rerun that
        # agrees names the cause
        for lab, b in list(case.args.items()):
            if len(b) == 1 and 1 < b[0] < 0x80 and len(bhits) < 2 and h['_runs'] < 30:
                trial = case.copy()
                trial.args[lab] = b'\x01'
                _, _, d = pair.run(trial, max_steps); h['_runs'] += 1
                if _benign(d):
                    bhits.append('%s+0' % lab)
    if not bhits and pair is not None and case.bg != 'zero' and h['_runs'] < 30:
        # (F1b, difffuzz-20) the same for a byte of the random background the MSVC code reads by itself (a bool
        # global the case does not plant, a field behind a background pointer): plant 1 there (0x00adb9f0,
        # 0x0087b7c0)
        bg = {}
        world.BG_READS = bg
        try:
            pair.run(case, max_steps)
        finally:
            world.BG_READS = None
        h['_runs'] += 1
        planted = [(a, a + len(b)) for a, b in case.mem.items()]
        for a in sorted(bg):
            sizes, v = bg[a]
            if len(bhits) >= 4 or h['_runs'] >= 30:
                break
            if sizes != {1} or v is None or v <= 1 or any(lo <= a < hi for lo, hi in planted) or \
                    any(s > k for k in range(1, 8) for s in bg.get(a - k, (set(),))[0]):
                continue
            trial = case.copy()
            trial.mem[a] = b'\x01'
            _, _, d = pair.run(trial, max_steps); h['_runs'] += 1
            if _benign(d):
                bhits.append('%08x' % a)
    if not bhits and pair is not None and not _IN_CHAIN:
        bhits = _bool_chain(pair, target, case, div, fn, max_steps, h)
        if h.get('chain_cause'):
            h['cause'] = h['chain_cause']; return h
    if bhits:
        h['cause'] = 'bool-repr'; h['bytes'] = bhits; return h
    if isfloat:
        # (float class) a smaller input byte that happens to remove a float difference proves no signedness (0x00a26b40:
        # clearing the top bit of a stride byte changes which memory is read); only the bool byte above is evidence
        h['cause'] = 'float'; return h
    # (float class) an integer result computed through x87 float arithmetic and a fist / fistp: the 80/53-bit
    # intermediates are amplified by the conversion (0x00e30fb0 NormalizeToUShort: 0xaac3 against 0xaac2); the oracle
    # rerun at 24-bit precision agrees with the native
    if pair is not None and not isfloat and k in ('return', 'mem-write') and (mn & {'fistp', 'fist', 'fisttp'}) and \
            (mn & {'fadd', 'fsub', 'fmul', 'fdiv', 'faddp', 'fsubp', 'fmulp', 'fdivp', 'fsubr', 'fdivr'}):
        world.X87_CW = 0x007f
        try:
            _, _, d = pair.run(case, max_steps)
        finally:
            world.X87_CW = 0x027f
        h['_runs'] += 1
        if _benign(d):
            h['cause'] = 'x87-precision'; h['x87_pc24'] = True; return h
    # signedness probe: clear the top bit of each input byte >= 0x80
    # S1-rules (d19-sign): in memory only the top byte of a narrow read counts (a char read by itself, or the high
    # byte of a 2-byte read), as for bool-repr; a byte of a pointer or integer word is not widened (before: 434 of
    # 461 memory hits on 8b233f99 were bytes inside a word read whole)
    def narrow_top(addr):
        cover = [(k, s) for k in range(8) for s in reads.get(addr - k, ()) if s > k]
        return bool(cover) and all(s <= 2 for _, s in cover) and \
            any((k, s) in ((0, 1), (1, 2)) for k, s in cover)
    hits = []
    for a, b in list(case.mem.items()):
        for i, x in enumerate(b):
            if x >= 0x80 and len(hits) < 4 and h['_runs'] < 40 and narrow_top(a + i):
                trial = case.copy()
                nb = bytearray(b); nb[i] &= 0x7f; trial.mem[a] = bytes(nb)
                _, _, d = pair.run(trial, max_steps); h['_runs'] += 1
                if d is None:
                    hits.append('%08x' % (a + i))
    for lab, b in list(case.args.items()):
        for i, x in enumerate(b):
            if x >= 0x80 and len(hits) < 4 and h['_runs'] < 60 and arg_narrow_top(target, fn, lab, i):
                trial = case.copy()
                nb = bytearray(b); nb[i] &= 0x7f; trial.args[lab] = bytes(nb)
                _, _, d = pair.run(trial, max_steps); h['_runs'] += 1
                if d is None:
                    hits.append('%s+%d' % (lab, i))
    if hits and (mn & {'movsx', 'movsx', 'cbw', 'cwde', 'cdq', 'movzx'}):
        h['cause'] = 'signedness'; h['bytes'] = hits; return h
    if mn & {'shl', 'shr', 'sar', 'shld', 'shrd', 'rol', 'ror'}:
        big = [lab for lab, b in case.args.items() if int.from_bytes(b[:4], 'little') & 0xffffffff >= 32]
        if big and hits:
            h['cause'] = 'shift-count'; h['args'] = big; return h
    if k == 'call-count' and pair is not None:
        # (uncls2) a call that only runs on garbage: the same uniform-fill probe (0x00a82d90)
        c = _uncls2_probes(pair, case, div, fn, mn, max_steps, h, only_uniform=True)
        if c:
            h['cause'] = c; return h
    if k in ('call-target', 'call-count'):
        h['cause'] = 'call-flow'; return h
    if hits:
        h['cause'] = 'signedness?'; h['bytes'] = hits; return h
    if k == 'return' and pair is not None and not getattr(getattr(target.conv, 'ret', None), 'is_float', False) and \
            partial_return(pair, case, div, max_steps):
        h['_runs'] += 1
        h['cause'] = 'partial-return'; return h
    if k in ('return', 'mem-write', 'call-args'):
        extra = undeclared_stack_reads(fn, target.conv)
        if extra:
            # (F-uncls, difffuzz-20) the exe function takes stack parameters its declaration lacks (0x00d32b40)
            h['cause'] = 'param-count'; h['offsets'] = extra; return h
    if k in ('return', 'mem-write', 'call-args') and pair is not None:
        # (F-uncls, difffuzz-20) the machines read the same fields at offsets that differ by one constant: a member
        # or a base subobject the native lays out differently (0x00e3cb10: every member read at +0x10)
        import readset
        d = readset.layout_shift(pair, case, max_steps)
        h['_runs'] += 2
        if d:
            h['cause'] = 'layout-offset'; h['shift'] = d; return h
    if k in ('return', 'mem-write', 'call-args') and pair is not None:
        c = _uncls2_probes(pair, case, div, fn, mn, max_steps, h)
        if c:
            h['cause'] = c; return h
    h['cause'] = 'unclassified'
    return h


def pool_vftable_stores(rx, ra, pinned):
    """(uncls2) Every byte the two machines store differently lies in a 4-byte word where the oracle stored the
    synthetic pool address of a read-only section of its object (coffobj: a `$SG<n>` section symbol, here the
    unit's own unnamed vftable) and the native stored an address of the exe whose first word is the first word of
    that pool literal, a code address (0x006ad660: the oracle's constructors store 0x02f00000, the pool copy of
    the table {0x006b3970, ...}; the native stores 0x0106ba58, where the exe's vftable begins with 0x006b3970)."""
    import coffobj
    exe = coffobj.exe_image()
    if exe is None:
        return False
    diff = {a for a in set(rx.writes) | set(ra.writes) if rx.writes.get(a) != ra.writes.get(a)}
    if not diff:
        return False
    for a in diff:
        base = a & ~3
        if any(rx.writes.get(base + i) is None or ra.writes.get(base + i) is None for i in range(4)):
            return False
        vx = struct.unpack('<I', bytes(rx.writes[base + i] for i in range(4)))[0]
        va = struct.unpack('<I', bytes(ra.writes[base + i] for i in range(4)))[0]
        lit = None
        for pa, pb in pinned.items():
            if pa <= vx < pa + max(len(pb), 1):
                lit = pb[vx - pa:vx - pa + 4]
        if lit is None or len(lit) < 4 or not coffobj.LITERAL_POOL <= vx < coffobj._POOL_NEXT or va & 3 or \
                not world.TEXT[0] <= struct.unpack('<I', lit)[0] < world.TEXT[1]:
            return False        # the pool literal must begin with a code address (a vftable's first slot)
        try:
            if bytes(exe.read(va, 4)) != lit:
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


def canned_unaliased(case):
    """(difffuzz-20, canned-alias) The case with every canned call result whose low word points into the argument
    buffers (world.BUF .. +0x10000) moved to a private heap address, one per call; None when no result does. The
    fuzzer draws 30 % of the canned words as pointers into the buffers, so an allocator or getter callee "returns"
    memory inside the object the function builds and then writes: the two compilers order the stores to the object
    and through that pointer differently (GCC assumes they do not alias), which no real allocation does."""
    out, n = [], 0
    for i in range(max(8, len(case.rets))):
        b = case.ret_bytes(i)
        w = int.from_bytes(b[:4], 'little')
        if world.BUF <= w < world.BUF + 0x10000:
            w = world.HEAP + 0x10000 + 0x1000 * i
            b = w.to_bytes(4, 'little') + b[4:]
            n += 1
        out.append(b)
    if not n:
        return None
    t = case.copy()
    t.rets = out
    return t


def _uncls2_probes(pair, case, div, fn, mn, max_steps, h, only_uniform=False):
    """(uncls2, difffuzz-20) What is left after every other probe: each one reruns the case with one mechanism changed
    and names the cause only when the two machines then agree (`_benign`).
      uninit-*       every garbage word 0 on both machines (world.UNIFORM): the outcome of a machine moves with the
                     fill but not between two random fills (a bool test of an unwritten byte is 1 for 255 of 256
                     fills, so the seed probe above saw nothing)
      canned-alias   every canned result that points into the argument buffers moved to the heap (world.HEAP)
      call-order     two adjacent calls of one callee that return the same value: the machines used the two results
                     in swapped roles (C++ leaves the evaluation order of operands open)
      shift-count    the 64-bit shift helpers take the count modulo 64 (world.SHIFT_MASK)
      x87-trig-inf   fsin / fcos / fsincos / fptan of an infinity give the indefinite NaN (world.TRIG_INF)
      vptr-bytes     the planted vptr word holds the native address point on the oracle too (world.VPTR_NATIVE): a
                     byte or part of the vptr word that the code reads as data
      ptrdiff-unaligned  a result that differs by the low two bits only, with every argument word 4-aligned
                     (GCC folds (end - begin) / 4 * 4 to end - begin: UB for pointers that are not 4 apart)"""
    def rerun(c=case):
        _, _, d = pair.run(c, max_steps)
        h['_runs'] += 1
        return _benign(d)

    def pinned_rets():
        t = case.copy(); t.rets = [case.ret_bytes(i) for i in range(max(8, len(case.rets)))]
        return t
    world.BG_SEED = case.seed if case.bg != 'zero' else None
    try:
        base = pinned_rets()
        rx, ra, _ = pair.run(base, max_steps)
        h['_runs'] += 1
        world.UNIFORM = True
        try:
            rxu, rau, du = pair.run(base, max_steps)
        finally:
            world.UNIFORM = False
        h['_runs'] += 1
        if _benign(du):
            mx, ma = _outcome(rxu) != _outcome(rx), _outcome(rau) != _outcome(ra)
            if mx or ma:
                return 'uninit-x86' if mx and not ma else 'uninit-arm' if ma and not mx else 'uninit-both'
        if only_uniform:
            return None
        tx = [c.target for c in rx.calls]
        for i in range(min(len(tx), len(base.rets)) - 1):
            if tx[i] == tx[i + 1] and tx[i] >= 0 and base.rets[i] != base.rets[i + 1]:
                # equal results in either value: only the roles the two machines gave them differ (one value alone
                # could remove the divergence by moving a pointer, 0x00480000)
                ta, tb = base.copy(), base.copy()
                ta.rets[i + 1] = ta.rets[i]
                tb.rets[i] = tb.rets[i + 1]
                if rerun(ta) and rerun(tb):
                    return 'call-order'
        moved = canned_unaliased(base)
        if moved is not None and rerun(moved):
            return 'canned-alias'
        for flag, cause in (('SHIFT_MASK', 'shift-count'), ('TRIG_INF', 'x87-trig-inf')):
            setattr(world, flag, True)
            try:
                if rerun(base):
                    return cause
            finally:
                setattr(world, flag, False)
        if div.get('kind') == 'mem-write' and pool_vftable_stores(rx, ra, pair.pinned):
            return 'vptr-pool'
        if case.vptrs:
            world.VPTR_NATIVE = True
            try:
                if rerun(base):
                    return 'vptr-bytes'
            finally:
                world.VPTR_NATIVE = False
        if div.get('kind') == 'return' and mn & {'sar', 'shr', 'idiv', 'div'}:
            bx, ba = bytes.fromhex(div.get('x86') or ''), bytes.fromhex(div.get('arm') or '')
            if len(bx) == 4 == len(ba) and 0 < abs(int.from_bytes(bx, 'little') - int.from_bytes(ba, 'little')) < 4:
                t = base.copy()
                t.args = {l: b''.join((int.from_bytes(b[o:o + 4], 'little') & ~3).to_bytes(4, 'little')
                                      for o in range(0, len(b) - 3, 4)) + b[len(b) // 4 * 4:]
                          for l, b in base.args.items()}
                if t.args != base.args and rerun(t):
                    return 'ptrdiff-unaligned'
    finally:
        world.BG_SEED = None
    return None
