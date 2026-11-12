"""The integer / control / string forms in the DSL (fnvlift/dsl.py), built from a capstone-decoded instruction.
build(ins, addr) -> Form.  A builder is written over operand placeholders (Op: register of a class/width, immediate,
memory effective address); a Form is that builder instantiated on one concrete encoding.  SDM = Intel SDM vol. 2 / vol. 1 3.4.3.
Families: forms_alu.py (arithmetic, shifts, bit ops, mul/div), forms_move.py (moves, stack, flags, strings), forms_flow.py (control flow, faults).
Form-level conventions: fault(code, cond) = the instruction raises DE/BP/UD when cond holds (state unchanged, eip = its address);
`when` on a register/flag write = the SDM defines the value only when `when` holds; df / flo (other EFLAGS bits) are state the
model driver does not expose, so a form that writes them can only be certified on its observable fields."""
import os, sys
sys.path.insert(0, os.environ.get('FNVLIFT_ROOT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from fnvlift.dsl import C, Form, V, bit, concat, eq, extract, load, msb, trunc, zext
from capstone import x86 as X

R8 = ['al', 'cl', 'dl', 'bl', 'ah', 'ch', 'dh', 'bh']; R16 = ['ax', 'cx', 'dx', 'bx', 'sp', 'bp', 'si', 'di']
SEGPFX = {0x64: 'fs', 0x65: 'gs', 0x2e: 'cs', 0x26: None, 0x36: None, 0x3e: None}
PFX = {0x66, 0x67, 0xf0, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65}
SEGS = {'cs', 'ds', 'es', 'ss', 'fs', 'gs'}
def form_key(ins):
    """same as form_key in test/instest.py (prove.py checks the two agree when instest imports)"""
    raw = bytes(ins.bytes); i = 0
    while i < len(raw) and raw[i] in PFX: i += 1
    opc = raw[i:i + (3 if raw[i] == 0x0f and raw[i + 1] in (0x38, 0x3a) else 2 if raw[i] == 0x0f else 1)]
    ks = []
    for o in ins.operands:
        if o.type == X.X86_OP_REG:
            n = ins.reg_name(o.reg)
            ks.append('xmm' if n.startswith('xmm') else 'st' if n.startswith('st') else 'mm' if n.startswith('mm') else 'seg' if n in SEGS
                      else 'r8' if n in R8 else 'r16' if n in R16 else 'r32' if n in ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'] else n)
        elif o.type == X.X86_OP_IMM: ks.append('imm')
        else: ks.append('m%d' % (o.size * 8) + {X.X86_REG_FS: ':fs', X.X86_REG_GS: ':gs'}.get(o.mem.segment, ''))
    return '%s|%s|%s' % (ins.mnemonic, ','.join(ks), opc.hex())

class Op:
    a16 = False                      # address-size attribute 16 (set by build() from the 0x67 prefix)
    def __init__(s, ins, o):
        s.w = o.size * 8
        if o.type == X.X86_OP_REG: s.k, s.name = 'r', ins.reg_name(o.reg)
        elif o.type == X.X86_OP_IMM: s.k, s.val = 'i', o.imm
        else:
            m = o.mem; s.k = 'm'; s.base = ins.reg_name(m.base) if m.base else None
            s.index = ins.reg_name(m.index) if m.index else None; s.scale, s.disp = m.scale, m.disp
            s.cseg = m.segment; s.seg = {X.X86_REG_FS: 'fs', X.X86_REG_GS: 'gs'}.get(m.segment)      # build() replaces seg by the one the decoded prefixes give
    @staticmethod
    def reg(name, w): o = Op.__new__(Op); o.k, o.name, o.w = 'r', name, w; return o
    def full(s):                     # (32-bit register, bit position) of a register operand
        n = s.name
        if s.w == 32: return n, 0
        if s.w == 16: return 'e' + n, 0
        return 'e' + n[0] + 'x', (8 if n[1] == 'h' else 0)
def ea(op, esp=None, seg=True):
    """effective address of a memory operand.  Address-size attribute 16 (prefix 0x67 in a 32-bit code segment, SDM Vol. 2 2.1.5 / Table 2-1, Vol. 1 3.7.5):
    the offset is base16 + index16 + disp, computed modulo 2^16 and zero-extended to 32 bits; the segment base is added after.  Capstone names the 16-bit
    registers (bx, bp, si, di): their values are the low words of ebx, ebp, esi, edi, so the sum of the 32-bit registers has the same low 16 bits."""
    if op.a16:
        sub16 = lambda n: V('e' + n, 32)
        a = C(op.disp, 32)
        if op.base: a = a + sub16(op.base)
        if op.index: a = a + sub16(op.index) * op.scale
        a = zext(trunc(a, 16), 32)
        if seg and op.seg in ('fs', 'gs'): a = a + V(op.seg + '_base', 32)
        return a
    sub = lambda n: esp if (n == 'esp' and esp is not None) else V(n, 32)
    a = C(op.disp, 32)
    if op.base: a = a + sub(op.base)
    if op.index: a = a + sub(op.index) * op.scale
    if seg and op.seg in ('fs', 'gs'): a = a + V(op.seg + '_base', 32)
    return a
def rd(op, w=None):
    w = w or op.w
    if op.k == 'i': return C(op.val, w)
    if op.k == 'm': return load(ea(op), w)
    r, lo = op.full(); return extract(V(r, 32), lo + op.w - 1, lo)
def store_op(F, op, addr, v):
    """the one store of a memory operand.  SDM Vol. 3, 5.6 (data-segment write rule) and 3.4.5.1: a code segment is not writable, so a store whose effective segment is CS
    (the 0x2e override, last prefix wins) raises #GP(0) -- Windows reports STATUS_ACCESS_VIOLATION -- before anything is written: fault code 5 (f_dsl FAULT), always."""
    if op.seg == 'cs': F.fault(7, C(1, 1))
    F.store(addr, v)
def wr(F, op, v, when=None):
    if op.k == 'm': return store_op(F, op, ea(op), v)
    r, lo = op.full(); old = F.cur(r)
    if op.w == 32: F.reg(r, v, when)
    elif lo == 0: F.reg(r, concat(extract(old, 31, op.w), v), when)
    else: F.reg(r, concat(concat(extract(old, 31, 16), v), extract(old, 7, 0)), when)

def pzs(F, r, g=lambda f, v: v):
    x = trunc(r, 8); x = x ^ (x >> 4); x = x ^ (x >> 2); x = x ^ (x >> 1)
    F.flag('pf', g('pf', bit(x, 0) ^ 1)); F.flag('zf', g('zf', eq(r, 0))); F.flag('sf', g('sf', msb(r)))
def af(F, a, b, r): F.flag('af', bit(a ^ b ^ r, 4))
def cond(cc):                        # condition code 0..15 as a 1-bit node over the pre-state flags
    f = {n: V(n, 1) for n in ('cf', 'pf', 'zf', 'sf', 'of')}
    c = [f['of'], f['cf'], f['zf'], f['cf'] | f['zf'], f['sf'], f['pf'], f['sf'] ^ f['of'], f['zf'] | (f['sf'] ^ f['of'])][cc >> 1]
    return c ^ 1 if cc & 1 else c
def undef(F, names, sdm):
    for f in names: F.undefined(f, sdm)

class AReg:
    """an address-size register of the string / loop / xlat instructions (ESI, EDI, ECX / SI, DI, CX under the 0x67 prefix, SDM Vol. 2 MOVS, LODS, STOS, CMPS,
    SCAS, LOOP, JCXZ).  .val = the register as an unsigned 32-bit value (zero-extended CX/SI/DI under a16); .put(F, v, when) writes v to it: the low
    word only under a16 (the upper half of the 32-bit register is kept: SDM 'the 16-bit register is written, bits 31:16 are not modified')."""
    def __init__(s, c, name):
        s.name, s.a16 = name, c.a16; s.cur = V(name, 32)
        s.val = zext(trunc(s.cur, 16), 32) if s.a16 else s.cur
    def put(s, F, v, when=None):
        F.reg(s.name, concat(extract(s.cur, 31, 16), trunc(v, 16)) if s.a16 else v, when)

class Ctx:
    pass
H = {}
def handler(*names):
    def d(fn):
        for n in names: H[n] = fn
        return fn
    return d

def build(ins, addr):
    """Form for a decoded instruction at addr; key = form_key(ins).  Raises KeyError for mnemonics outside the covered set."""
    from proof import forms_alu, forms_move, forms_flow      # noqa: F401  (register their handlers)
    c = Ctx(); c.ins, c.addr = ins, addr; c.F = Form(form_key(ins), addr, ins.size)
    words = ins.mnemonic.split(); c.rep = words[0] if len(words) > 1 else ''; c.m = words[-1]
    c.ops = [Op(ins, o) for o in ins.operands]
    raw = bytes(ins.bytes); i = 0; c.o16 = False; c.a16 = False
    sp = None                        # effective segment override: the LAST segment-override prefix byte wins (model: prefixes[seg] is overwritten by each one, SDM Vol. 2 2.1.1); 26 / 36 / 3e are flat like the default
    while raw[i] in PFX:
        c.o16 |= raw[i] == 0x66; c.a16 |= raw[i] == 0x67
        if raw[i] in SEGPFX: sp = SEGPFX[raw[i]]
        i += 1
    for o in c.ops:
        o.a16 = c.a16
        if o.k == 'm': o.seg = None if o.cseg == X.X86_REG_ES else sp      # the string destination is ES:EDI whatever the override (SDM Vol. 2, MOVS / STOS / SCAS / CMPS)
    c.opc = raw[i + 1] if raw[i] == 0x0f else raw[i]; c.raw = raw
    fn = H.get(c.m)
    if fn is None and c.m[0] == 'j': fn = H['jcc']
    if fn is None and c.m.startswith('set'): fn = H['setcc']
    if fn is None and c.m.startswith('cmov'): fn = H['cmovcc']
    if fn is None: raise KeyError(c.m)
    fn(c)
    return c.F
