"""x87 exception routing (SDM 8.4 "FPU Flags Affected", 2A FCOM / FCOMI "Floating-Point Exceptions"): the rule is ONE path, the macro FXF of rt/fnvl_x87.h, through which every carrier helper's exception mask reaches c->fex.
Checks, all static (text of the emitter and of the header; the semantics of the helpers are certified against the SDM by proof/x87spec):
  1. rt/fnvl_x87.h: every function or macro that calls a fnvl_x_* / fnvl_m_* helper with a mask output (&f, &f_) hands that mask to FXF / FM_FL (= FXF); the only functions that do not are the value views (one fv
     parameter, result fnvl_f80 or double: fv_x, fv_d), which are no instruction: their rounding is the carrier model (binary64 IEEE rule), not an architectural exception;
  2. every compare op class passes its own ordering to the helper: the ordered FCOM / FCOMP / FCOMPP / FCOMI / FCOMIP / FICOM / FTST give Q = 0 (IE for any NaN), the unordered FUCOM / FUCOMP / FUCOMPP /
     FUCOMI / FUCOMIP give Q = 1 (IE only for a signaling NaN);
  3. a non-compare operation (FADD with an SNaN operand) takes its flags from the host FPSR owned by the x87 family: the emitted text makes the x87 family the owner (FOWN_X87) or the function's prepass proves it.
    test_x87_exc_routing.py (the input SDMROWS is mounted)"""
import os, re, sys
LIFT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
import fnvlift as L
from fnvlift.core import Unsupported
from fnvlift.image import Image

# 1. the header
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
print('x87_exc_routing ok: %d routed helper sites, %d compare classes' % (n_routed, len(CASES)))
