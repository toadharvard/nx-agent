"""r5design.py - divergences earlier workers called "by design" without proof (EQ, r5 design, difffuzz-20).

Each probe names its cause only when two things hold: the exe's own code shows the mechanism (an instruction pattern
at named addresses, read with capstone and never copied), and a rerun of the case with that mechanism removed makes
the two machines agree. A function whose name looks right but whose code does not show it keeps its divergence.

  dead-result        `result_dead`: an int / pointer result of the target that no caller in the exe reads (every
                     direct call is followed by a write of eax before any read, or is a bare pass-through wrapper whose
                     own callers do the same; no address of the function is taken). Rerun with the result not compared.
  dead-init-call     `dead_init_site`: a call of a constructor that only stores through `this` into a frame object
                     (0x00b12af0, nine floats), after which the caller stores every byte of that object before any
                     read or escape. MSVC /Od keeps the call, GCC drops the dead initialisation. Rerun without the call.
  local-index-range  `frame_index_probe`: a frame-relative address with a variable index (`[esp + r*4 + d]`, `lea
                     [esp + r + d]`) that the oracle forms outside its own frame (at or above the entry esp, or below
                     esp): it writes the caller's frame / arguments, so the result moves with the frame layout of each
                     compiler. Rerun with the integer parameters at small values: every rerun that keeps the index in
                     the frame and reaches the same call must agree (at least three do).
  ptrdiff-unaligned  `ptrdiff_probe`: `sub r, [m]; cdq; mov rK, K; idiv rK` (a pointer difference of K-byte elements,
                     K not a power of two) whose dividend is not a multiple of K: GCC divides exactly. Rerun with the
                     subtrahend word rounded so that every such dividend is a multiple of K.
  canned-null-deref  `deref_after_call`: an oracle-only read fault at a small address right after an indirect call
                     whose canned result was 0 and whose next instructions load through eax. The native's declaration
                     returns a reference (a valid object); the oracle's wild call has none. Rerun with that call's
                     result the reference object on both machines.
  x87-range (call-args)  the existing flush rerun, for a float argument, when what is left is the garbage of a stubbed
                     callee's out-parameter (see `x87_args`).
"""
import contextlib
import random
import re
import struct

import capstone
from capstone import x86_const as X
from unicorn import UC_HOOK_CODE
import unicorn.x86_const as UX

import world
import x86emu

_MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_MD.detail = True

REG = {'eax': UX.UC_X86_REG_EAX, 'ecx': UX.UC_X86_REG_ECX, 'edx': UX.UC_X86_REG_EDX, 'ebx': UX.UC_X86_REG_EBX,
       'esi': UX.UC_X86_REG_ESI, 'edi': UX.UC_X86_REG_EDI, 'ebp': UX.UC_X86_REG_EBP, 'esp': UX.UC_X86_REG_ESP}


def _benign(d):
    import classify
    return classify._benign(d)


def _pe():
    import exeempty
    return exeempty._pe()


# ------------------------------------------------------------------------------------------------------ run hooks

@contextlib.contextmanager
def x86_hook(pair, lo, hi, cb):
    """Every x86 engine the pair builds while the context is open gets a code hook over [lo, hi) calling
    cb(uc, address, size). The pair's engines are dropped on the way in and out (the hook belongs to the engine)."""
    orig = x86emu.X86Engine.__init__
    pair.xe = pair.ae = None

    def init(self, *a, **k):
        orig(self, *a, **k)
        self.uc.hook_add(UC_HOOK_CODE, lambda uc, address, size, _u: cb(uc, address, size), begin=lo, end=hi)
    x86emu.X86Engine.__init__ = init
    try:
        yield
    finally:
        x86emu.X86Engine.__init__ = orig
        pair.xe = pair.ae = None


def _ea(uc, ins, op):
    m = op.mem
    ea = m.disp
    if m.base:
        ea += uc.reg_read(REG[ins.reg_name(m.base)])
    if m.index:
        ea += uc.reg_read(REG[ins.reg_name(m.index)]) * m.scale
    return ea & 0xffffffff


def _mem_ops(ins):
    return [op for op in ins.operands if op.type == capstone.x86.X86_OP_MEM]


def _code_range(pair, addr):
    """(start, bytes) of the oracle code that holds `addr`, or None."""
    for a, b in pair.target.x86_code.items():
        if a <= addr < a + len(b):
            return a, b
    return None


# ------------------------------------------------------------------------------ local-index-range (frame escape)

def frame_sites(code, base):
    """[(address, instruction)] of the instructions of `code` that form a frame-relative address with a variable
    index: a memory operand whose base is esp or ebp and that has an index register."""
    out = []
    for ins in _MD.disasm(bytes(code), base):
        for op in _mem_ops(ins):
            if op.mem.base and ins.reg_name(op.mem.base) in ('esp', 'ebp') and op.mem.index and \
                    ins.reg_name(op.mem.index) not in ('esp',):
                out.append((ins.address, ins))
                break
    return out


def _run_sites(pair, case, max_steps, entry, lo, hi, sites):
    """One pair.run with a hook on the frame sites: (rx, ra, d, record). record = {'entry': esp at entry, 'esc':
    [(site, ea)] of the sites that formed an address outside [esp, entry esp), 'n': sites executed}."""
    rec = {'entry': None, 'esc': [], 'n': 0}
    by = {a: i for a, i in sites}

    def cb(uc, address, size):
        if address == entry:
            rec.update(entry=uc.reg_read(UX.UC_X86_REG_ESP), esc=[], n=0)
        ins = by.get(address)
        if ins is None or rec['entry'] is None:
            return
        for op in _mem_ops(ins):
            if op.mem.index:
                ea = _ea(uc, ins, op)
                rec['n'] += 1
                if ea >= rec['entry'] or ea < uc.reg_read(UX.UC_X86_REG_ESP):
                    rec['esc'].append((address, ea))
                break
    with x86_hook(pair, lo, hi, cb):
        rx, ra, d = pair.run(case, max_steps)
    return rx, ra, d, rec


def _reaches(rx, ra, div):
    if rx.error or ra.error:
        return False
    if div.get('kind') == 'call-args':
        i = div.get('index', 0)
        return len(rx.calls) > i and len(ra.calls) > i and ('%08x' % (rx.calls[i].target & 0xffffffff)) == div.get('target')
    return True


def frame_index_probe(pair, target, case, div, fn, max_steps, h):
    """local-index-range, or None. See the module doc."""
    if div.get('kind') not in ('call-args', 'return', 'mem-write'):
        return None
    code, base = bytes(fn['code']), fn['address']
    sites = frame_sites(code, base)
    if not sites:
        return None
    lo, hi = base, base + len(code)
    rx, ra, d, rec = _run_sites(pair, case, max_steps, base, lo, hi, sites)
    h['_runs'] += 1
    if not rec['esc']:
        return None                    # every variable-index frame address of this case is inside the frame
    params = [v.label for v in getattr(target.conv, 'vals', ())
              if v.cls == 'I32' and not v.is_ptr and not v.is_float and v.label in case.args][:5]
    if not params:
        return None
    rng = random.Random(base)
    grid = [tuple(rng.randrange(4) for _ in params) for _ in range(48)]
    reached = 0
    for assign in dict.fromkeys(grid):
        c = case.copy()
        for lab, v in zip(params, assign):
            c.args[lab] = struct.pack('<I', v).ljust(len(case.args[lab]), b'\0')[:len(case.args[lab])]
        rx, ra, d, rec = _run_sites(pair, c, max_steps, base, lo, hi, sites)
        h['_runs'] += 1
        if rec['esc'] or not _reaches(rx, ra, div):
            continue                    # the index is out of the frame again, or the run no longer gets there
        if not _benign(d):
            return None                 # a divergence inside the frame: not an out-of-range index
        reached += 1
    if reached < 3:
        return None
    h['frame_index'] = {'sites': ['%08x' % a for a, _ in sites][:4], 'reruns': reached}
    return 'local-index-range'


# ------------------------------------------------------------------------------------------- ptrdiff-unaligned

def idiv_sites(code, base):
    """[(idiv address, K, sub address, sub instruction)]: `sub r, [m]` within five instructions before a `cdq`, then
    `mov rK, K` and `idiv rK` (K an immediate that is not a power of two, > 2)."""
    ins = list(_MD.disasm(bytes(code), base))
    out = []
    for i, n in enumerate(ins):
        if n.mnemonic != 'idiv' or n.operands[0].type != capstone.x86.X86_OP_REG:
            continue
        reg = n.operands[0].reg
        k = None
        for m in ins[max(0, i - 2):i]:
            if m.mnemonic == 'mov' and len(m.operands) == 2 and m.operands[0].type == capstone.x86.X86_OP_REG and \
                    m.operands[0].reg == reg and m.operands[1].type == capstone.x86.X86_OP_IMM:
                k = m.operands[1].imm & 0xffffffff
        j = next((j for j in range(i - 1, max(-1, i - 4), -1) if ins[j].mnemonic == 'cdq'), None)
        if k is None or k < 3 or k & (k - 1) == 0 or j is None:
            continue
        for m in reversed(ins[max(0, j - 5):j]):
            if m.mnemonic == 'sub' and len(m.operands) == 2 and m.operands[0].type == capstone.x86.X86_OP_REG and \
                    m.operands[1].type == capstone.x86.X86_OP_MEM and m.operands[1].size == 4:
                out.append((n.address, k, m.address, m))
                break
    return out


def patch_word(case, ea, val):
    """The case with the dword at `ea` set to val (a new overlay entry when no planted buffer holds it)."""
    c = case.copy()
    b = struct.pack('<I', val & 0xffffffff)
    for a, buf in c.mem.items():
        if a <= ea and ea + 4 <= a + len(buf):
            nb = bytearray(buf)
            nb[ea - a:ea - a + 4] = b
            c.mem[a] = bytes(nb)
            return c
    c.mem[ea] = b
    return c


def _run_idiv(pair, case, max_steps, entry, lo, hi, sites):
    """(rx, ra, d, found): found = [(sub EA, sub operand value, remainder, K)] of every idiv executed whose dividend
    is not a multiple of K."""
    found, last_sub = [], {}
    by_sub = {s: m for _, _, s, m in sites}
    by_idiv = {a: (k, s) for a, k, s, _ in sites}

    def cb(uc, address, size):
        if address == entry:
            found.clear(); last_sub.clear()
        m = by_sub.get(address)
        if m is not None:
            op = m.operands[1]
            ea = _ea(uc, m, op)
            last_sub[address] = (ea, struct.unpack('<I', bytes(uc.mem_read(ea, 4)))[0])
        if address in by_idiv:
            k, s = by_idiv[address]
            dv = uc.reg_read(UX.UC_X86_REG_EAX)
            dv = dv - (1 << 32) if dv & 0x80000000 else dv
            rem = dv - k * int(dv / k)
            if rem and s in last_sub:
                found.append((last_sub[s][0], last_sub[s][1], rem, k))
    with x86_hook(pair, lo, hi, cb):
        rx, ra, d = pair.run(case, max_steps)
    return rx, ra, d, list(found)


def ptrdiff_probe(pair, case, div, fn, max_steps, h):
    code, base = bytes(fn['code']), fn['address']
    sites = idiv_sites(code, base)
    if not sites:
        return None
    lo, hi = base, base + len(code)
    c = case
    first = None
    for _ in range(4):
        rx, ra, d, found = _run_idiv(pair, c, max_steps, base, lo, hi, sites)
        h['_runs'] += 1
        if first is None:
            first = found
            if not found:
                return None            # every pointer difference is a multiple of the element size
        if not found:
            break
        ea, val, rem, _k = found[0]
        c = patch_word(c, ea, val + rem)
    else:
        return None
    if not _benign(d) or c is case:
        return None
    h['ptrdiff'] = {'size': first[0][3], 'sites': ['%08x' % s[0] for s in sites][:4], 'remainder': first[0][2]}
    return 'ptrdiff-unaligned'


# --------------------------------------------------------------------------------------------------- dead-result

_DEAD = {}
_EAX = {'eax', 'ax', 'al', 'ah'}


def _sections(pe):
    return [s for s in getattr(pe, 'sections', ())]


def _callers(pe, target):
    """([(site, 'call' | 'jmp')], address_taken) of the exe: rel32 call / jmp sites that reach `target`, and whether
    any other word of the image looks like its address (a data word, or an immediate / absolute operand)."""
    sites, taken = [], False
    pat = struct.pack('<I', target)
    for sec in _sections(pe):
        va, flags, data = sec[0], sec[2], bytes(sec[3])
        if flags & 0x20000000:
            for m in re.finditer(b'[\xe8\xe9]', data):
                i = m.start()
                if i + 5 <= len(data) and (va + i + 5 + struct.unpack_from('<i', data, i + 1)[0]) & 0xffffffff == target:
                    sites.append((va + i, 'call' if data[i] == 0xe8 else 'jmp'))
            j = data.find(pat)
            while j >= 0:
                before = data[max(0, j - 2):j]
                if before[-1:] == b'\x68' or (before[-1:] and 0xb8 <= before[-1] <= 0xbf) or \
                        (len(before) == 2 and before[0] in (0x8d, 0xc7, 0x8b, 0x89, 0xa1) and before[1] & 0xc7 == 5):
                    taken = True
                j = data.find(pat, j + 1)
        else:
            j = data.find(pat)
            while j >= 0:
                if j % 4 == 0:
                    taken = True
                j = data.find(pat, j + 1)
    return sites, taken


def _function_start(pe, site):
    """Start of the branch-free wrapper that holds `site`: the byte after the padding (int3) before it, with only
    straight-line instructions up to the site; None otherwise."""
    sec = pe.section_of(site)
    if sec is None:
        return None
    va, data = sec[0], sec[3]
    j = site
    while j > site - 0x40 and data[j - 1 - va] != 0xcc:
        j -= 1
    if data[j - 1 - va] != 0xcc:
        return None
    a = j
    while a < site:
        ins = next(iter(_MD.disasm(bytes(data[a - va:a - va + 16]), a, 1)), None)
        if ins is None or ins.mnemonic in ('ret', 'retn', 'jmp', 'call') or ins.mnemonic.startswith('j'):
            if not (ins is not None and ins.mnemonic == 'call'):
                return None
        a += ins.size
    return j if a == site else None


def _eax_after(pe, a, depth):
    """True when eax is dead after the call that ends at `a`: written before it is read; or a bare `ret` follows
    (pass-through) and the wrapper's own callers do not read it either (checked by the caller via the 2nd item)."""
    sec = pe.section_of(a)
    va, data = sec[0], sec[3]
    for _ in range(14):
        ins = next(iter(_MD.disasm(bytes(data[a - va:a - va + 16]), a, 1)), None)
        if ins is None:
            return None
        if ins.mnemonic in ('ret', 'retn'):
            return 'pass'
        if ins.mnemonic.startswith('j') or ins.mnemonic in ('loop', 'jmp'):
            return None
        rd, wr = ins.regs_access()
        names_r = {ins.reg_name(r) for r in rd}
        names_w = {ins.reg_name(r) for r in wr}
        zero_idiom = ins.mnemonic in ('xor', 'sub') and len(ins.operands) == 2 and \
            ins.operands[0].type == ins.operands[1].type == capstone.x86.X86_OP_REG and \
            ins.operands[0].reg == ins.operands[1].reg
        if zero_idiom:
            if ins.reg_name(ins.operands[0].reg) == 'eax':
                return 'dead'
            a += ins.size
            continue
        if names_r & _EAX:
            return None
        if 'eax' in names_w or ins.mnemonic == 'call':
            return 'dead'
        a += ins.size
    return None


def result_dead(addr, depth=0):
    """True when the exe shows that no caller reads the eax `addr` returns (see the module doc). False without the
    exe, with no caller found, or when the address is taken."""
    key = (addr, depth)
    if key not in _DEAD:
        _DEAD[key] = False
        try:
            _DEAD[key] = _result_dead(addr, depth)
        except Exception:  # noqa: BLE001
            _DEAD[key] = False
    return _DEAD[key]


def _result_dead(addr, depth):
    pe = _pe()
    if pe is None or depth > 3:
        return False
    sites, taken = _callers(pe, addr)
    if taken or not sites:
        return False
    for site, kind in sites:
        if kind == 'jmp':
            start = _function_start(pe, site)
            if start is None or start != site or not result_dead(start, depth + 1):
                return False
            continue
        r = _eax_after(pe, site + 5, depth)
        if r == 'dead':
            continue
        if r != 'pass':
            return False
        start = _function_start(pe, site)
        if start is None or not result_dead(start, depth + 1):
            return False
    return True


def result_dead_probe(pair, target, case, div, max_steps, h):
    if div.get('kind') != 'return' or getattr(target.conv, 'ret', None) is None:
        return None
    ret = target.conv.ret
    if ret.is_float or getattr(ret, 'size', 4) > 4 or str(ret.cls).startswith('AGG'):
        return None
    if not result_dead(target.orig_addr):
        return None
    world.RET_DEAD = True
    try:
        _, _, d = pair.run(case, max_steps)
    finally:
        world.RET_DEAD = False
    h['_runs'] += 1
    return 'dead-result' if _benign(d) else None


# ------------------------------------------------------------------------------------------------ dead-init-call

def _callee_stores(code_bytes, base):
    """{offset: size} stored through `this` by a straight-line constructor body (frame code, `this` spilled to
    [ebp - 4] or kept in ecx, stores `[reg + off]` of a reg loaded from there), or None when the body does anything
    else (a branch, a call, a store elsewhere)."""
    stores, this_regs, spilled = {}, {'ecx'}, False
    for ins in _MD.disasm(bytes(code_bytes), base):
        m = ins.mnemonic
        if m in ('ret', 'retn'):
            return stores or None
        if m in ('call', 'jmp') or m.startswith('j') or m in ('loop', 'rep', 'movsd', 'movsb', 'stosd', 'stosb'):
            return None
        ops = ins.operands
        if m == 'push' and ops[0].type == capstone.x86.X86_OP_REG:
            continue
        if m == 'mov' and len(ops) == 2 and ops[0].type == capstone.x86.X86_OP_REG and \
                ops[1].type == capstone.x86.X86_OP_MEM and ops[1].mem.base and \
                ins.reg_name(ops[1].mem.base) == 'ebp' and ops[1].mem.disp == -4:
            this_regs.add(ins.reg_name(ops[0].reg))               # mov reg, [ebp - 4]
            continue
        if m == 'mov' and len(ops) == 2 and ops[0].type == capstone.x86.X86_OP_MEM and \
                ops[0].mem.base and ins.reg_name(ops[0].mem.base) == 'ebp' and ops[0].mem.disp == -4 and \
                ops[1].type == capstone.x86.X86_OP_REG and ins.reg_name(ops[1].reg) == 'ecx':
            spilled = True                                        # mov [ebp - 4], ecx
            continue
        if m in ('fstp', 'fst', 'mov') and ops and ops[0].type == capstone.x86.X86_OP_MEM and not ops[0].mem.index:
            reg = ins.reg_name(ops[0].mem.base) if ops[0].mem.base else None
            if reg in this_regs and ops[0].mem.disp >= 0 and ops[0].size in (4, 8):
                stores[ops[0].mem.disp] = ops[0].size
                continue
            return None
        if m in ('fld1', 'fldz', 'fld', 'pop', 'nop', 'mov', 'lea', 'leave', 'sub', 'add', 'xor', 'fchs'):
            if any(op.type == capstone.x86.X86_OP_MEM for op in ops):
                if m in ('fld', 'mov'):
                    continue
                return None
            if m == 'mov' and ops[0].type == capstone.x86.X86_OP_REG and ins.reg_name(ops[0].reg) in this_regs:
                return None
            continue
        return None
    return None


def dead_init_site(code, base, ret_to, callee_code):
    """(this slot offset from ebp, covered bytes) when the call that returns to `ret_to` in `code` is
    `lea ecx, [ebp - D]; call C` and the caller then stores every byte C stores (callee_code) before any read of the
    object, any escape of its address, or a branch; else None."""
    ins = list(_MD.disasm(bytes(code), base))
    i = next((j for j, n in enumerate(ins) if n.address + n.size == ret_to and n.mnemonic == 'call'), None)
    if i is None or i == 0:
        return None
    lea = ins[i - 1]
    if lea.mnemonic != 'lea' or lea.operands[0].type != capstone.x86.X86_OP_REG or \
            lea.reg_name(lea.operands[0].reg) != 'ecx' or lea.operands[1].mem.base == 0 or \
            lea.reg_name(lea.operands[1].mem.base) != 'ebp' or lea.operands[1].mem.index:
        return None
    d0 = lea.operands[1].mem.disp                     # the object is [ebp + d0, ebp + d0 + size)
    stores = _callee_stores(callee_code[0], callee_code[1])
    if not stores:
        return None
    need = set()
    for off, sz in stores.items():
        need.update(range(d0 + off, d0 + off + sz))
    lo, hi = min(need), max(need) + 1
    if need != set(range(lo, hi)):
        return None
    done, dead = set(), set()
    for n in ins[i + 1:]:
        m = n.mnemonic
        if m.startswith('j') or m in ('ret', 'retn', 'loop'):
            break
        rd, wr = n.regs_access()
        names_r = {n.reg_name(r) for r in rd}
        names_w = {n.reg_name(r) for r in wr}
        # the callee's register results (ecx may be kept, eax = this) must not be relied on after the call
        if names_r & {'ecx', 'cx', 'cl'} and 'ecx' not in dead:
            return None
        if names_r & {'eax', 'ax', 'al'} and 'eax' not in dead:
            return None
        if m == 'call' or 'ecx' in names_w:
            dead.add('ecx')
        if m == 'call' or 'eax' in names_w:
            dead.add('eax')
        for op in n.operands:
            if op.type != capstone.x86.X86_OP_MEM:
                continue
            base_r = n.reg_name(op.mem.base) if op.mem.base else None
            if base_r != 'ebp':
                continue
            if op.mem.index:
                return None                           # an indexed frame access could reach the object
            if m == 'lea':
                if lo - 0x100 <= op.mem.disp < hi:
                    return None                       # the object's address escapes
                continue
            rng = set(range(op.mem.disp, op.mem.disp + max(op.size, 1)))
            if rng & need:
                if m in ('fstp', 'fst', 'fistp', 'fist', 'mov') and op is n.operands[0]:
                    done |= rng
                else:
                    return None                       # a read of the object's bytes before they are all stored
        if need <= done:
            return d0, sorted(need)
    return None


def dead_init_probe(pair, case, div, max_steps, h):
    """dead-init-call for a call-count divergence where the oracle makes calls the native does not."""
    if div.get('kind') != 'call-count':
        return None
    rx, ra, d = pair.run(case, max_steps)
    h['_runs'] += 1
    if d is None or d.get('kind') != 'call-count' or len(rx.calls) <= len(ra.calls):
        return None
    pe = _pe()
    if pe is None:
        return None
    tried = set()
    for c in rx.calls[:12]:
        if c.ret_to in tried or c.target < 0:
            continue
        tried.add(c.ret_to)
        rngc = _code_range(pair, c.ret_to)
        sec = pe.section_of(c.target)
        if rngc is None or sec is None:
            continue
        callee = (bytes(sec[3][c.target - sec[0]:c.target - sec[0] + 0x80]), c.target)
        got = dead_init_site(rngc[1], rngc[0], c.ret_to, callee)
        if not got:
            continue
        world.SKIP_RETS = frozenset([c.ret_to])
        try:
            rx2, ra2, d2 = pair.run(case, max_steps)
        finally:
            world.SKIP_RETS = frozenset()
        h['_runs'] += 1
        if _benign(d2):
            h['dead_init'] = {'call': '%08x' % c.target, 'site': '%08x' % c.ret_to, 'bytes': len(got[1])}
            return 'dead-init-call'
    return None


# ---------------------------------------------------------------------------------------------- canned-null-deref

def deref_after_call(code, base, ret_to, disp=None):
    """True when the call that returns to `ret_to` in `code` is an indirect call (through a register or memory) and
    the next instructions, before eax is written or any branch, load through `[eax + disp]` (the first read of eax;
    any displacement when `disp` is None)."""
    ins = list(_MD.disasm(bytes(code), base))
    i = next((j for j, n in enumerate(ins) if n.address + n.size == ret_to and n.mnemonic == 'call'), None)
    if i is None or ins[i].operands[0].type == capstone.x86.X86_OP_IMM:
        return False
    for n in ins[i + 1:i + 7]:
        if n.mnemonic.startswith('j') or n.mnemonic in ('call', 'ret', 'retn'):
            return False
        for op in n.operands:
            if op.type == capstone.x86.X86_OP_MEM and op.mem.base and n.reg_name(op.mem.base) == 'eax' and \
                    not op.mem.index:
                return disp is None or op.mem.disp == disp
        rd, wr = n.regs_access()
        if {n.reg_name(r) for r in rd} & _EAX:
            return False
        if 'eax' in {n.reg_name(r) for r in wr}:
            return False
    return False


def null_deref_probe(pair, case, div, max_steps, h):
    """canned-null-deref: the fault call is an indirect call whose canned result is 0 and whose next instructions
    load through [eax + the faulting address]. Every indirect call of the run whose result the exe code dereferences
    at once (a wild call: the unit declares it by value or by reference, the oracle has no declaration) then answers
    with an object holding the canned bytes (world.REF_CALLS) on both machines; the rerun must agree."""
    if div.get('kind') != 'error' or div.get('arm') is not None:
        return None
    m = re.match(r'fault:r:([0-9a-f]{8})$', str(div.get('x86') or ''))
    if not m or int(m.group(1), 16) >= 0x10000:
        return None
    rx, ra, _ = pair.run(case, max_steps)
    h['_runs'] += 1
    if not rx.calls or ra.error:
        return None
    k = len(rx.calls) - 1
    c = rx.calls[k]
    if case.ret_bytes(k)[:4] != bytes(4):
        return None
    rngc = _code_range(pair, c.ret_to)
    if rngc is None or not deref_after_call(rngc[1], rngc[0], c.ret_to, int(m.group(1), 16)):
        return None
    refs = {k}
    try:
        for _ in range(4):
            for kk, cc in enumerate(rx.calls):
                rngc = _code_range(pair, cc.ret_to)
                if kk not in refs and rngc is not None and deref_after_call(rngc[1], rngc[0], cc.ret_to):
                    refs.add(kk)
            world.REF_CALLS = frozenset(refs)
            rx, ra, d = pair.run(case, max_steps)
            h['_runs'] += 1
            if _benign(d):
                h['null_deref'] = {'calls': sorted(refs), 'site': '%08x' % c.ret_to}
                return 'canned-null-deref'
    finally:
        world.REF_CALLS = frozenset()
    return None


# --------------------------------------------------------------------------------------- x87-range for an argument

def x87_args(pair, case, div, max_steps, h):
    """x87-range for a float argument of an intercepted call (call-args). The flush rerun of classify.x87_probes
    asks for an equal run; here the callee's out-parameter (a stubbed constructor of a matrix the code then reads)
    leaves garbage that moves the later stores of both machines, so a flush that removes the argument divergence
    names the cause only when it also agrees once the garbage is all zero on both machines (world.UNIFORM), which on
    its own leaves the divergence in place."""
    if div.get('kind') != 'call-args' or div.get('cls') not in ('F32', 'F64'):
        return None
    base = case.copy()
    base.rets = [case.ret_bytes(i) for i in range(max(8, len(case.rets)))]
    world.BG_SEED = case.seed if case.bg != 'zero' else None
    try:
        world.UNIFORM = True
        try:
            _, _, du = pair.run(base, max_steps)
        finally:
            world.UNIFORM = False
        h['_runs'] += 1
        if _benign(du):
            return None                 # uninitialised garbage alone explains it: not this probe
        for kind, cw in (('f32', 0x007f), ('f64', 0x027f)):
            rec = {'kind': kind}
            world.X87_CLAMP, world.X87_CW = rec, cw
            try:
                _, _, dc = pair.run(base, max_steps)
                alone = _benign(dc)
                moved = alone or (dc.get('kind'), dc.get('index'), dc.get('arg')) != \
                    (div.get('kind'), div.get('index'), div.get('arg'))
                world.UNIFORM = True
                try:
                    _, _, dcu = pair.run(base, max_steps)
                finally:
                    world.UNIFORM = False
            finally:
                world.X87_CLAMP, world.X87_CW = None, 0x027f
            h['_runs'] += 2
            if rec.get('n') and moved and (alone or _benign(dcu)):
                h['x87_range'] = {'range': kind, 'clamped': rec['n'], 'over': rec.get('over', 0),
                                  'under': rec.get('under', 0), 'after': '%08x' % rec['addr'], 'uniform': not alone}
                return 'x87-range'
    finally:
        world.BG_SEED = None
    return None


def value_probes(pair, target, case, div, fn, max_steps, h):
    """The probes of a return / mem-write / call-args divergence, in the order of their evidence."""
    return (result_dead_probe(pair, target, case, div, max_steps, h)
            or frame_index_probe(pair, target, case, div, fn, max_steps, h)
            or ptrdiff_probe(pair, case, div, fn, max_steps, h)
            or x87_args(pair, case, div, max_steps, h))
