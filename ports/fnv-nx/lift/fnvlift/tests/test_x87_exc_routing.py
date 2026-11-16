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
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, re, sys
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import fnvnx.lift.fnvlift.tests._inputs as _inputs; _inputs.configure()
import fnvnx.lift.fnvlift as L
from fnvnx.lift.fnvlift.core import Unsupported
from fnvnx.lift.fnvlift.image import Image

# 1. the header
sys.path.insert(0, os.path.join(LIFT, '..', '..')); from fnvnx.lift.proof import x87spec      # CARRIER_OPS: the value computations of the proven Forms (their exception flags are the spec's own terms, not a helper mask)
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
# 3b. DE of the arithmetic family (SDM 1:8.5.2, rt/fnvl_x87.h fx_de through FXD): every emitted FXN site carries the source operands as carriers into FXD; the fmath macros fold fx_de into their one FM_FL
for name, hx in (('fadd m32', 'd8442404'), ('fmul m64', 'dc4c2404'), ('fiadd m32', 'da442404'), ('fsub st1', 'd8e1'), ('fdivrp', 'def1'), ('fsqrt', 'd9fa')):
    t = text(bytes.fromhex(hx) + b'\x00' * 4 + b'\xc3')
    assert t.count('FXN(') >= 1 and t.count('FXD(') == t.count('FXN('), (name, t[:400])
for mac in ('FM_UN', 'FM_BIN', 'FM_TRIG', 'FM_TAN', 'FM_SINCOS', 'FM_PREM'):
    body = next(p for p in parts if p.startswith('#define %s(' % mac))
    assert 'fx_de(' in body and body.count('FM_FL(') == 1, mac
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

# 5. DE (SDM 1:8.5.2, 1:4.9.2): ONE rule, fx_de, at the operand's own type; the host IDC is never a source of DE; DE enters c->fex only through FXF
assert hdr.count('static inline u32 fx_de(') == 1
assert '>>7&1)<<1)' not in hdr.split('static inline void fnvl_fp_x87_clear')[0], 'DE read from the host IDC'      # fnvl_fp_x87_flags
cmp_body = next(p for p in parts if p.startswith('static inline u32 fv_cmp('))
assert 'fx_de(' in cmp_body and 'FXF(f)' in cmp_body and cmp_body.index('fx_de(') < cmp_body.rindex('FXF(f)')
de_lines = [l for l in hdr.split('\n') if re.search(r'\bfx_de\(', l)]
defs = [l for l in de_lines if l.startswith('static inline u32 fx_de(')]
assert len(defs) == 1
assert all(('FXF(' in l or 'FM_FL(' in l) for l in de_lines if l not in defs), 'a fx_de use outside the FXF/FM_FL routing'   # the rule: every use feeds c->fex through the one routing, never a count
import subprocess, tempfile
blk = hdr[hdr.index('static inline u32 fx_den('):hdr.index('static inline u32 fv_cmp(')]      # the real text of the rule, compiled as is
C = ('typedef unsigned u32; typedef unsigned long long u64; struct fv{u64 v;u32 e;u32 k;}; typedef struct fv fv; enum { FK_E, FK_F64, FK_F32, FK_I16, FK_I32, FK_I64, FK_M80 };\n' + blk +
     '\n#include <stdio.h>\n#include <stdlib.h>\nint main(int n,char**a){ fv x={strtoull(a[1],0,0),(u32)strtoul(a[2],0,0),(u32)strtoul(a[3],0,0)}, y={strtoull(a[4],0,0),(u32)strtoul(a[5],0,0),(u32)strtoul(a[6],0,0)}; printf("%u",fx_de(x,y,(u32)strtoul(a[7],0,0))); return 0; }\n')
with tempfile.TemporaryDirectory() as td:
    open(td + '/de.cc', 'w').write(C)
    subprocess.run(['g++', '-O1', '-o', td + '/de', td + '/de.cc'], check=True)
    def de(a, b, ie=0): return int(subprocess.run([td + '/de'] + [str(x) for x in (*a, *b, ie)], capture_output=True, text=True, check=True).stdout)
    F64, F32, I32, M80 = 1, 2, 4, 6
    assert de((0x32abda83, 0, F32), (0x6ee90d, 0, F32)) == 2                                   # (F32,F32) counterexample: the second is a binary32 denormal
    assert de((0xb009fffffffffff5, 0, F64), (0x80088374e8fae9e6, 0, F64)) == 2                 # (F64,F64) counterexample
    assert de((0x3f800000, 0, F32), (0x3f800000, 0, F32)) == 0 and de((0, 0, F64), (0, 0, F64)) == 0      # normal operands, zeros: no DE
    assert de((0x6ee90d, 0, F32), (5, 0, I32)) == 2 and de((5, 0, I32), (7, 0, I32)) == 0      # integers have none, the denormal operand has
    assert de((0x7fc00000, 0, F32), (0x6ee90d, 0, F32)) == 0                                    # a QNaN operand: precedence over DE
    assert de((0x6ee90d, 0, F32), (0x3f800000, 0, F32), 1) == 0                                 # IE raised: no DE
    assert de((0x8000000000000001, 0, M80), (0x8000000000000000, 0x3fff, M80)) == 2 and de((0x8000000000000000, 0x3fff, M80), (0x8000000000000000, 0x3fff, M80)) == 0 and de((0x8000000000000001, 0, M80), (0, 0, F64)) == 2      # an 80-bit operand: its image (e = 0, nonzero significand: a pseudo-denormal counts)
    assert de((0xc000000000000001, 0x7fff, M80), (0x6ee90d, 0, F32)) == 0                       # an 80-bit QNaN
print('x87_exc_routing ok: %d routed helper sites, %d compare classes' % (n_routed, len(CASES)))
