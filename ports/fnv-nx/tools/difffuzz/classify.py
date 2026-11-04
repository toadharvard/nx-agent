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


def entry_register_reads(fn, conv):
    """Registers the oracle function reads before writing them on its entry path (straight line up to the first
    branch), excluding those its declared convention passes (ecx/edx) and the callee-saved pushes: a register
    argument MSVC's whole-program optimisation added (a custom convention units.json does not record)."""
    passed = {sp for _, sp, _, _ in conv.x86 if sp in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')}
    written, reads = set(), []
    for ins in CSD.disasm(fn['code'], fn['address']):
        if ins.mnemonic == 'push' and ins.op_str in ('ebx', 'esi', 'edi', 'ebp', 'ecx'):
            continue                  # saving a callee-saved register / reserving a slot is not a use
        if ins.mnemonic in ('xor', 'sub') and len(ins.operands) == 2 and ins.op_str.split(', ')[0] == \
                ins.op_str.split(', ')[-1]:
            written.add(BASE.get(ins.op_str.split(', ')[0], ins.op_str.split(', ')[0]))
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
    return reads          # in first-read order


def _outcome(r):
    return (r.error, r.ret, tuple(c.target for c in r.calls), tuple(sorted(r.writes.items())))


_VFT = None


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
    if div.get('nan_bits'):
        h['cause'] = 'nan-bits'; return h
    if k in ('call-target', 'call-count') and any(str(x).startswith('name:__s13_vtpad_')
                                                  for x in [div.get('arm')] + list(div.get('arm_trace') or [])):
        # the native call went through a slot past the native vtable the link pairs with the planted vftable
        # (vtmodel.py padding): a pairing gap of S2c, not this function's behaviour; tested
        # before `unplanted`, which would file it as oracle-vcall (S13b)
        h['cause'] = 'vptr-unpaired'; return h
    if k == 'call-target' and div.get('vcall') == 'unplanted':
        # an indirect call through memory the case did not plant (an object's vptr the fuzzer filled with bytes):
        # each machine reads its own slot layout (Itanium has two destructor slots), not a port divergence
        h['cause'] = 'oracle-vcall'; return h
    if k == 'call-target' and msvc_inlined(target, div):
        h['cause'] = 'msvc-inlined'; return h
    regs = entry_register_reads(fn, target.conv)
    if regs:
        h['cause'] = 'x86-custom-cc'; h['regs'] = regs; return h
    src = getattr(target, 'source', None)
    if src and k in ('return', 'mem-write', 'call-args', 'error'):
        obj = config.LINK / 'fixed' / config.obj_name(src)
        if obj.exists():
            hits = check_rebind.bypassing_functions(str(obj)).get(target.orig_addr)
            if hits:
                h['cause'] = 'split-global'; h['vars'] = hits; return h
    if div.get('arm_local_static'):
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
            h['cause'] = 'vptr-store'; return h
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
    # determinism probes: only garbage changes (stack fill, non-argument registers, argument padding)
    if case.bg == 'zero':
        alt = case.copy(); alt.seed = case.seed ^ 0x13572468
        alt.rets = [case.ret_bytes(i) for i in range(max(8, len(case.rets)))]
        base = case.copy(); base.rets = list(alt.rets)
        rx0, ra0, _ = pair.run(base, max_steps)
        rx1, ra1, _ = pair.run(alt, max_steps)
        h['_runs'] += 2
        ux, ua = _outcome(rx0) != _outcome(rx1), _outcome(ra0) != _outcome(ra1)
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
    if k in ('return', 'mem-write'):
        fl = div.get('float') or div.get('float_word')
        conv_ops = {'fistp', 'fist', 'cvttss2si', 'cvttsd2si', 'cvtss2si', 'cvtsd2si', 'fisttp'}
        if (mn & conv_ops) and ('80000000' in str(div.get('x86')) or '0000008' in str(div.get('x86'))):
            h['cause'] = 'float->int'; return h
        if fl:
            if fl.get('tolerant'):
                h['cause'] = 'x87-precision' if any(m.startswith('f') for m in mn) else 'float-tolerance'
            else:
                h['cause'] = 'float'
            return h
    # bool representation probe: a byte that is neither 0 nor 1 read as bool (MSVC tests the whole byte, GCC may
    # test bit 0 only): set each such input byte to 1
    bhits = []
    for a, b in list(case.mem.items()):
        for i, x in enumerate(b):
            if x > 1 and len(bhits) < 4 and h['_runs'] < 30:
                trial = case.copy()
                nb = bytearray(b); nb[i] = 1; trial.mem[a] = bytes(nb)
                _, _, d = pair.run(trial, max_steps); h['_runs'] += 1
                if d is None:
                    bhits.append('%08x' % (a + i))
    if bhits:
        h['cause'] = 'bool-repr'; h['bytes'] = bhits; return h
    # signedness probe: clear the top bit of each input byte >= 0x80
    hits = []
    for a, b in list(case.mem.items()):
        for i, x in enumerate(b):
            if x >= 0x80 and len(hits) < 4 and h['_runs'] < 40:
                trial = case.copy()
                nb = bytearray(b); nb[i] &= 0x7f; trial.mem[a] = bytes(nb)
                _, _, d = pair.run(trial, max_steps); h['_runs'] += 1
                if d is None:
                    hits.append('%08x' % (a + i))
    for lab, b in list(case.args.items()):
        for i, x in enumerate(b):
            if x >= 0x80 and len(hits) < 4 and h['_runs'] < 60:
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
    if k in ('call-target', 'call-count'):
        h['cause'] = 'call-flow'; return h
    if hits:
        h['cause'] = 'signedness?'; h['bytes'] = hits; return h
    h['cause'] = 'unclassified'
    return h
