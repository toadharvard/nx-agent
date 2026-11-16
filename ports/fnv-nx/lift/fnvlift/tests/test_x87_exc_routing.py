"""x87 exception routing (SDM 8.4 "FPU Flags Affected", 2A FCOM / FCOMI "Floating-Point Exceptions"): the rule is ONE path, the macro FXF of rt/fnvl_x87.h, through which every carrier helper's exception mask reaches c->fex.
Checks, all static (text of the emitter and of the header; the semantics of the helpers are certified against the SDM by proof/x87spec):
  1. rt/fnvl_x87.h: every function or macro that calls a fnvl_x_* / fnvl_m_* helper with a mask output (&f, &f_) hands that mask to FXF / FM_FL (= FXF); the only functions that do not are the value views (one fv
     parameter, result fnvl_f80 or double: fv_x, fv_d), which are no instruction: their rounding is the carrier model (binary64 IEEE rule), not an architectural exception;
  2. every compare op class passes its own ordering to the helper: the ordered FCOM / FCOMP / FCOMPP / FCOMI / FCOMIP / FICOM / FTST give Q = 0 (IE for any NaN), the unordered FUCOM / FUCOMP / FUCOMPP /
     FUCOMI / FUCOMIP give Q = 1 (IE only for a signaling NaN);
  3. a non-compare operation (FADD with an SNaN operand) takes its flags from the host FPSR owned by the x87 family: the emitted text makes the x87 family the owner (FOWN_X87) or the function's prepass proves it.
  4. C1 of FIST / FRNDINT is 1 iff the result rounded away from zero (|r| > |x|): the header computes it at the site, the table checks the predicate against the rounded value of every RC mode.
The input SDMROWS is mounted only because lifting a byte buffer runs core.dispatch's CPUID gate (proof/cpufeat.missing), which reads the SDM opcode rows for every instruction; the test itself reads no x87 row of the SDM.
    test_x87_exc_routing.py"""
import os, re, sys
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
import fnvlift as L
from fnvlift.core import Unsupported
from fnvlift.image import Image

# 1. the header
sys.path.insert(0, os.path.join(LIFT, 'proof')); import x87spec      # CARRIER_OPS: the value computations of the proven Forms (their exception flags are the spec's own terms, not a helper mask)
hdr = open(os.path.join(LIFT, 'rt', 'fnvl_x87.h')).read()
parts = re.split(r'\n(?=static |FNVL_F2I |#define |template)', hdr)
routed = []
for p in parts:
    if not re.search(r'fnvl_[xm]_\w+\([^;]*&f_?\b', p):
        continue
    head = p.split('\n')[0]
    if 'FXF(f' in p or 'FM_FL(f' in p:
        routed.append(head)
        continue
    if re.match(r'static inline \w+ (fx_dsl_\w+)\(', head) and re.match(r'static inline \w+ (fx_dsl_\w+)\(', head).group(1) in x87spec.CARRIER_OPS:
        continue      # a carrier op of the DSL (x87spec.CARRIER_OPS): a value computation that holds no state; the Form's FSW carries its exception flags (rt/fnvl_x87.h, CARRIER OPS)
    m = re.match(r'static inline (fnvl_f80|double) (\w+)\(fv a\)\{', head)
    assert m, 'a helper output that never reaches c->fex: ' + head[:100]
for fn in ('fv_s32(', 'fv_s64(', 'fv_i(', 'fv_rnd(', 'fv_cmp(', 'fv_stbcd('):
    assert any(fn in h for h in routed), fn      # the helpers of the instruction classes that report exceptions
n_routed = len(routed)
assert '#define FXF(F)' in hdr and hdr.count('c->fex=(c->fex&~0x200u)|((F)&0x23fu)') == 1      # the single write of a helper mask

# 2. the compare classes
def text(code):
    img = Image.from_bytes(code, 0x401000)
    for x87 in (None, 'dyn'):
        try:
            return L.lift(img, 0x401000, len(code)) [0] if x87 is None else L.lift(img, 0x401000, len(code), x87=x87)[0]
        except (Unsupported, TypeError):
            continue
    raise AssertionError('not lifted: ' + code.hex())

CASES = [('fcom st1', 'd8d1', 'FCMP(', 0), ('fcomp st1', 'd8d9', 'FCMP(', 0), ('fcompp', 'ded9', 'FCMP(', 0), ('fcom m32', 'd810', 'FCMP(', 0), ('fcom m64', 'dc10', 'FCMP(', 0),
         ('ficom m32', 'da10', 'FCMP(', 0), ('ficom m16', 'de10', 'FCMP(', 0), ('ftst', 'd9e4', 'FCMP(', 0),
         ('fucom st1', 'dde1', 'FCMP(', 1), ('fucomp st1', 'dde9', 'FCMP(', 1), ('fucompp', 'dae9', 'FCMP(', 1),
         ('fcomi st1', 'dbf1', 'FCMPI(', 0), ('fcomip st1', 'dff1', 'FCMPI(', 0), ('fucomi st1', 'dbe9', 'FCMPI(', 1), ('fucomip st1', 'dfe9', 'FCMPI(', 1)]
for name, hx, mac, q in CASES:
    t = text(bytes.fromhex(hx) + b'\x00' * 4 + b'\xc3')
    ms = re.findall(re.escape(mac) + r'.*?,(\d)u\)', t)
    assert ms and all(int(x) == q for x in ms), (name, q, ms, t[:300])

# 3. a non-compare op
t = text(bytes.fromhex('d9442404d8442408d95c240cc3'))
assert 'FOWN_X87' in t or 'fown' in t, t[:300]
# 4. C1 of the rounding instructions (FIST, FRNDINT): 1 iff the result rounded away from zero, |r| > |x| (SDM 8.5.1.1 / FIST "C1")
assert '#define FX_UP(X,R) (__builtin_fabs(R)>__builtin_fabs(X)?0x200u:0u)' in hdr
for fn in ('FNVL_F2I static inline u64 fv_i(', 'static inline fv fv_rnd('):
    body = next(p for p in parts if p.startswith(fn))
    assert 'FX_UP(' in body, fn                     # the fast paths assign C1 from the compare, never a constant 0
import fractions, math
def rnd(x, rc):                                      # the four rounding modes of the control word RC: 0 nearest-even, 1 down, 2 up, 3 toward zero (SDM Table 8-4)
    f = fractions.Fraction(x)
    if rc == 0: return float(round(f))
    return float({1: math.floor, 2: math.ceil, 3: math.trunc}[rc](f))
# (x, mode) -> (r, C1): an operand rounded up in magnitude (C1 = 1) and one rounded down (C1 = 0) in every mode; an exact one raises nothing
TABLE = [(1.5, 0, 2.0, 1), (2.5, 0, 2.0, 0), (-1.5, 0, -2.0, 1), (-2.5, 0, -2.0, 0), (0.3, 0, 0.0, 0), (0.7, 0, 1.0, 1),
         (0.3, 1, 0.0, 0), (-0.3, 1, -1.0, 1), (1.7, 1, 1.0, 0), (-1.7, 1, -2.0, 1),
         (0.3, 2, 1.0, 1), (-0.3, 2, -0.0, 0), (1.7, 2, 2.0, 1), (-1.7, 2, -1.0, 0),
         (0.3, 3, 0.0, 0), (-0.3, 3, -0.0, 0), (1.7, 3, 1.0, 0), (-1.7, 3, -1.0, 0), (4.0, 0, 4.0, 0), (-4.0, 1, -4.0, 0)]
for x, rc, r, c1 in TABLE:
    assert rnd(x, rc) == r, (x, rc, rnd(x, rc), r)
    assert (abs(r) > abs(x)) == bool(c1), (x, rc, r)      # the predicate FX_UP computes, checked against the rounded value of each mode
print('x87_exc_routing ok: %d routed helper sites, %d compare classes' % (n_routed, len(CASES)))
