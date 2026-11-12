"""System and fault forms of the integer class (see forms.py), the DSL expression of fnvlift/f_sys.py's classes: the semantics are the SDM's, the Windows reporting is the hand handlers' (their comments cite
KiTrap0D / Wine 9.0), the machine constants are rt/fnvl.h's.  Fault kinds (dsl.FAULTS): a Form that raises says which exception the process observes; the C text of each kind is f_dsl.FAULT_ROWS.
  ARPL          protected-mode RPL adjust: ZF = RPL(dest) < RPL(src); dest.RPL = src.RPL only then (a memory destination is written only then: Form.store `when`)
  BOUND         #BR as FNVL_BOUNDS when the signed index is outside [lo, hi]
  privileged    CPL-0 instructions and I/O at IOPL 0 (f_sys.PRIV_MN, MOV CRn/DRn): #GP(0), reported by KiTrap0D's opcode table: PRIV (STATUS_PRIVILEGED_INSTRUCTION) or AV {0, 0xFFFFFFFF}
  INT n / INTO / INT1   the vector table of f_sys.INT_VECTORS
  SLDT STR SMSW SGDT SIDT   the model's constants
  CPUID RDTSC   the reference CPU's fixed answer / the platform timer: uf nodes (dsl.uf)"""
from fnvlift.dsl import C, V, extract, ite, slt, uf, ult, zext
from proof.forms import Op, ea, handler, rd, store_op, wr

# the reference machine (rt/fnvl.h; fnvlift/tests/dsl_int_classes_test.py checks these against the header)
GDTR_BASE, GDTR_LIMIT, IDTR_BASE, IDTR_LIMIT, LDTR_SEL, TR_SEL, CR0 = 0x80b95000, 0x03ff, 0x80b95400, 0x07ff, 0, 0x28, 0x80010033
K_BP, K_UD, K_BOUNDS, K_INTO, K_PRIV, K_AV, K_STEP, K_ASSERT, K_DBGSRV, K_INTO_AT1 = 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
INT_KIND = {3: K_BP, 4: K_INTO_AT1, 5: K_BOUNDS, 0x2c: K_ASSERT, 0x2d: K_DBGSRV}      # f_sys.INT_VECTORS; every other vector is #GP -> AV
PRIV_MN = ('cli', 'sti', 'hlt', 'in', 'out', 'insb', 'insw', 'insd', 'outsb', 'outsw', 'outsd', 'rdmsr', 'wrmsr', 'invd', 'wbinvd', 'wbnoinvd', 'clts', 'lgdt', 'lidt', 'lldt', 'ltr',
           'lmsw', 'invlpg', 'rdpmc', 'sysexit')      # = f_sys.PRIV_MN (fnvlift/tests/dsl_int_classes_test.py checks the equality)

@handler('arpl')
def arpl(c):
    F, o = c.F, c.ops; d, s = rd(o[0], 16), rd(o[1], 16)
    lt = ult(d & C(3, 16), s & C(3, 16)); new = (d & C(0xfffc, 16)) | (s & C(3, 16))
    F.flag('zf', lt)
    if o[0].k == 'm': store_op(F, o[0], ea(o[0]), new, lt)
    else: wr(F, o[0], ite(lt, new, d))

def privileged(c):
    from fnvlift.f_sys import trap0d_privileged
    c.F.fault(K_PRIV if trap0d_privileged(c.raw) else K_AV, C(1, 1))
handler(*PRIV_MN)(privileged)

@handler('int')
def int_(c): c.F.fault(INT_KIND.get(c.ops[0].val & 0xff, K_AV), C(1, 1))
@handler('into')
def into(c): c.F.fault(K_INTO, V('of', 1))
@handler('int1')
def int1(c): c.F.fault(K_STEP, C(1, 1))

def store16(c, v):
    """SLDT / STR / SMSW into r/m16 or a register: memory gets 16 bits, a 32-bit register gets the value zero-extended (SDM)"""
    o = c.ops[0]
    if o.k == 'm': store_op(c.F, o, ea(o), v)
    else: wr(c.F, o, zext(v, o.w) if o.w > 16 else v)
@handler('sldt')
def sldt(c): store16(c, C(LDTR_SEL, 16))
@handler('str')
def str_(c): store16(c, C(TR_SEL, 16))
@handler('smsw')
def smsw(c):
    o = c.ops[0]
    if o.k == 'r' and o.w == 32: wr(c.F, o, C(CR0, 32))
    else: store16(c, C(CR0 & 0xffff, 16))
@handler('sgdt', 'sidt')
def sgdt(c):
    o = c.ops[0]; lim, base = (GDTR_LIMIT, GDTR_BASE) if c.m == 'sgdt' else (IDTR_LIMIT, IDTR_BASE)
    store_op(c.F, o, ea(o), C(lim, 16)); store_op(c.F, o, ea(o, off=2), C(base, 32))

@handler('cpuid')
def cpuid(c):
    for r, i in (('eax', 0), ('ebx', 1), ('ecx', 2), ('edx', 3)): c.F.reg(r, uf('cpuid%d' % i, 32, V('eax', 32), V('ecx', 32)))
@handler('rdtsc')
def rdtsc(c):
    t = uf('rdtsc', 64); c.F.reg('eax', extract(t, 31, 0)); c.F.reg('edx', extract(t, 63, 32))
