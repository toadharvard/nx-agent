"""System and fault forms of the integer class (see forms.py), the DSL expression of fnvlift/f_sys.py's classes: the semantics are the SDM's, the Windows reporting is the hand handlers' (their comments cite
KiTrap0D / Wine 9.0), the machine constants are rt/fnvl.h's.  Fault kinds (dsl.FAULTS): a Form that raises says which exception the process observes; the C text of each kind is f_dsl.FAULT_ROWS.
  ARPL          protected-mode RPL adjust: ZF = RPL(dest) < RPL(src); dest.RPL = src.RPL only then (a memory destination is written only then: Form.store `when`)
  BOUND         #BR as FNVL_BOUNDS when the signed index is outside [lo, hi]
  privileged    CPL-0 instructions and I/O at IOPL 0 (proof/privset.py: the SDM rows; MOV CRn/DRn): #GP(0), reported by Wine 9.0's is_privileged_instr: PRIV (STATUS_PRIVILEGED_INSTRUCTION) or AV {0, 0xFFFFFFFF}
  INT n / INTO / INT1   the int_vector column of faults.json
  STR   the record's TR selector (SLDT SMSW SGDT SIDT SLDT LAR LSL VERR VERW: undecided fields, no Form)
  CPUID RDTSC   the reference CPU's fixed answer / the platform timer: uf nodes (dsl.uf)"""
from fnvlift.dsl import C, V, eq, extract, ite, slt, uf, ult, zext
from proof.forms import Op, ea, handler, rd, store_op, wr

from fnvlift import x86sys
from fnvlift.f_sys import wine_is_privileged
from fnvlift.faults import BYNAME as _FT, INT_ROWS
from proof import privset
K_UD, K_BOUNDS, K_INTO, K_PRIV, K_AV, K_STEP = (_FT[n]['code'] for n in ('UD', 'BOUNDS', 'INTO', 'PRIV', 'AV', 'STEP'))      # the one fault table, fnvlift/faults.json
INT_KIND = {v: _FT[n]['code'] for v, (n, _at) in INT_ROWS.items()}      # the int_vector column of faults.json; a vector outside the record's int_ring3.vectors is undecided (no Form)
_PRIV = privset.mnemonics()      # the SDM rows of proof/sdm_priv_rows.tsv (class cpl0 / iopl)

@handler('arpl')
def arpl(c):
    F, o = c.F, c.ops; d, s = rd(o[0], 16), rd(o[1], 16)
    lt = ult(d & C(3, 16), s & C(3, 16)); new = (d & C(0xfffc, 16)) | (s & C(3, 16))
    F.flag('zf', lt)
    if o[0].k == 'm': store_op(F, o[0], ea(o[0]), new, lt)
    else: wr(F, o[0], ite(lt, new, d))

def privileged(c):
    c.F.fault(K_PRIV if wine_is_privileged(c.raw) else K_AV, C(1, 1))
handler(*_PRIV)(privileged)

def _int_other():
    """the record's other_vectors row (the access violation {0, 0xffffffff} at the int, error code vector*8+2) checked against the runtime's fault: every vector that is neither a gate nor excluded takes it"""
    o = x86sys.INT_OTHER
    assert (o['fault'] == '#GP' and o['exception_code'] == int(_FT['AV']['status'], 16) and o['flags'] == 0 and o['eip_advance'] == 0 and o['address'] == 'the int instruction'
            and o['error_code'] == 'vector*8+2' and o['parameters'] == [0, 0xffffffff]), 'int: the other_vectors row of x86sys int_ring3 is not the access violation the runtime raises'
    return K_AV

@handler('int')
def int_(c):
    """INT imm8 over the record's rows (x86sys int_ring3): a gate vector (INT_KIND) is its trap row, an excluded vector (INT_EXCLUDED: undecided on the declared OS) has no Form, every other vector is the other_vectors row.
    A concrete vector is that one row.  A symbolic vector (a shape's imm field) is the case split over all 256 vectors derived from the same rows: one arm per fault kind (the vectors with the same row collapse into one
    condition), the other_vectors arm is the complement of the gates and the excluded vectors, and each excluded vector is a domain exclusion of the shape (F.require: the values outside the domain have no Form,
    symform.OutsideDomain); no fault code is invented for it and the other arms are unaffected."""
    v = c.ops[0].val & 0xff
    if isinstance(v, int):                                # a concrete vector
        if v in INT_KIND: c.F.fault(INT_KIND[v], C(1, 1)); return
        assert v not in x86sys.INT_EXCLUDED, 'int 0x%x: undecided on the declared OS (x86sys int_ring3)' % v
        c.F.fault(_int_other(), C(1, 1)); return
    F = c.F; hit = lambda vs: _any(eq(v, C(x, v.w)) for x in sorted(vs))
    arms = {}
    for x, k in INT_KIND.items(): arms.setdefault(k, set()).add(x)
    for k, vs in sorted(arms.items()): F.fault(k, hit(vs))
    F.fault(_int_other(), ~hit(set(INT_KIND) | set(x86sys.INT_EXCLUDED)))
    for x in sorted(x86sys.INT_EXCLUDED): F.require(~eq(v, C(x, v.w)))

def _any(it):
    it = list(it); r = it[0]
    for e in it[1:]: r = r | e
    return r
@handler('into')
def into(c): c.F.fault(K_INTO, V('of', 1))
@handler('int1')
def int1(c): c.F.fault(K_STEP, C(1, 1))

def store16(c, v):
    """SLDT / STR / SMSW into r/m16 or a register: memory gets 16 bits, a 32-bit register gets the value zero-extended (SDM)"""
    o = c.ops[0]
    if o.k == 'm': store_op(c.F, o, ea(o), v)
    else: wr(c.F, o, zext(v, o.w) if o.w > 16 else v)
@handler('str')
def str_(c): store16(c, C(x86sys.SEL['tr'], 16))
@handler('cpuid')
def cpuid(c):
    for r, i in (('eax', 0), ('ebx', 1), ('ecx', 2), ('edx', 3)): c.F.reg(r, uf('cpuid%d' % i, 32, V('eax', 32), V('ecx', 32)))
@handler('rdtsc')
def rdtsc(c):
    t = uf('rdtsc', 64); c.F.reg('eax', extract(t, 31, 0)); c.F.reg('edx', extract(t, 63, 32))
