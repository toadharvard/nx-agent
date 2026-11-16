"""The x87 transcendentals lower to CORE-MATH (CLAUDE.md orchestrator decision 2026-11-16 05:50Z, ASM OWNER 8 / 10).
  lifted buffers of each of the 8 instructions: the emitted text calls exactly the table's cr_* function (and no fnvl_m_ engine function);
  the C2 path (|x| >= 2^63) of fsin fcos fptan fsincos: the macros test fnvl_m_trig_big and set C2 with the operand and the stack unchanged; trig_big itself on the boundary values;
  0 libm symbols in the emitted text and in lift/rt (code, comments and strings removed); no cr_* is defined in lift/rt and the one declaration is in rt/fmath.h.
    test_crmath.py"""
import os, re, sys, glob, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
from fnvlift.image import Image
import fnvlift as L
from fnvlift import f_x87
RT = os.path.join(_inputs.LIFT, 'rt')
FAILS = []
B = 0x401000


def check(n, ok):
    print('%-100s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


def lift(h):
    code = bytes.fromhex(h)
    return L.lift(Image.from_bytes(code, B), B, len(code))[0]


LD0 = 'dd0424'                  # fld qword [esp]
LD1 = 'dd442404'                # fld qword [esp+4]
# instruction, bytes, the macro the emitted text must hold (the stack slots are those of the buffer), the CORE-MATH function(s) of the table
CASES = {
    'f2xm1': (LD0 + 'd9f0c3', r'FM_UN\(cr_exp2m1,', ['cr_exp2m1']),
    'fyl2x': (LD0 + LD1 + 'd9f1c3', r'FM_LOG\(cr_log2,', ['cr_log2']),
    'fyl2xp1': (LD0 + LD1 + 'd9f9c3', r'FM_LOG\(cr_log2p1,', ['cr_log2p1']),
    'fptan': (LD0 + 'd9f2c3', r'FM_TAN\(cr_tan,', ['cr_tan']),
    'fpatan': (LD0 + LD1 + 'd9f3c3', r'FM_ATAN\(cr_atan2,', ['cr_atan2']),
    'fsin': (LD0 + 'd9fec3', r'FM_TRIG\(cr_sin,', ['cr_sin']),
    'fcos': (LD0 + 'd9ffc3', r'FM_TRIG\(cr_cos,', ['cr_cos']),
    'fsincos': (LD0 + 'd9fbc3', r'FM_SINCOS\(cr_sin,cr_cos,', ['cr_sin', 'cr_cos']),
}
LIBM = ('sin cos tan asin acos atan atan2 sinh cosh tanh asinh acosh atanh exp exp2 expm1 log log2 log10 log1p pow sqrt cbrt hypot fmod remainder fma ldexp frexp scalbn modf '
        'floor ceil round trunc rint nearbyint lrint fabs copysign nextafter fegetround fesetround feclearexcept fetestexcept feholdexcept feupdateenv fegetenv fesetenv').split()
ROUNDING = 'floor ceil round trunc rint nearbyint sqrt'.split()      # their __builtin_ forms are the frint* / fsqrt instructions of the target (the guest flags hold -fno-math-errno), not calls; a plain call is libm
LIBM_RE = re.compile(r'(?<![\w.>])(?:(?:__builtin_)?(?:%s)|(?:%s))[fl]?\s*\(' % ('|'.join(n for n in LIBM if n not in ROUNDING + ['isnan', 'isinf']), '|'.join(ROUNDING)))


def strip(src):
    src = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S); src = re.sub(r'//[^\n]*', ' ', src)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', src)


texts = {}
for nm, (h, pat, crs) in CASES.items():
    t = lift(h); texts[nm] = t
    ms = [l for l in t.split('\n') if 'FM_' in l]
    check('%s: the emitted text holds exactly one FM_ line, calling %s' % (nm, '+'.join(crs)), len(ms) == 1 and re.search(pat, ms[0]) is not None)
    check('%s: the emitted text names no engine function (fnvl_m_) and no libm symbol' % nm, 'fnvl_m_' not in t and LIBM_RE.search(strip(t)) is None)
    check('%s: the emitted text names exactly the table\'s cr_ functions' % nm, sorted(set(re.findall(r'\bcr_\w+', t))) == sorted(crs))
want = {'f2xm1': 'cr_exp2m1', 'fyl2x': 'cr_log2', 'fyl2xp1': 'cr_log2p1', 'fptan': 'cr_tan', 'fpatan': 'cr_atan2', 'fsin': 'cr_sin', 'fcos': 'cr_cos'}    # the table of the rule
check('the lifter\'s table CR is the rule\'s table (fsincos = cr_sin + cr_cos)', f_x87.CR == want)
check('fptan and fsincos use the dynamic stack (data dependent depth)', 'dyn_rd' in texts['fptan'] and 'dyn_rd' in texts['fsincos'])

# macros of rt/fnvl_x87.h
hdr = open(os.path.join(RT, 'fnvl_x87.h')).read()
mac = {m.group(1): m.group(0) for m in re.finditer(r'#define (FM_\w+)\([^\n]*', hdr)}
for m_ in ('FM_TRIG', 'FM_TAN', 'FM_SINCOS'):
    check('%s: |x| >= 2^63 is exactly "c2=1" (no result, no push, the operand and the stack unchanged); the result, the push and c2=0 are in the else branch' % m_,
          re.search(r'if\(fnvl_m_trig_big\(x_\)\) c2=1; else\{', mac[m_]) is not None and 'c2=0' in mac[m_].split('else{')[1] and 'D=' in mac[m_].split('else{')[1])
check('FM_TAN pushes 1.0 after the cr_tan result, FM_SINCOS pushes the cosine', '0x3ff0000000000000ull' in mac['FM_TAN'] and 'dyn_push(fv_f64(k_))' in mac['FM_SINCOS'])
check('the macros call fnvl_m_cr1 / cr2 / crl with the CORE-MATH function they are given (kind = FMK_ + name)',
      all(('fnvl_m_cr%s(' % k) in mac[m_] for m_, k in (('FM_UN', '1'), ('FM_ATAN', '2'), ('FM_LOG', 'l'), ('FM_TRIG', '1'))) and 'FMK_##CR' in mac['FM_UN'])

# trig_big on the boundary values: the definition of fmath.c compiled alone
src = open(os.path.join(RT, 'fmath.c')).read()
m = re.search(r'u32 fnvl_m_trig_big\(double x\)[^\n]*', src)
prog = ('typedef unsigned int u32; typedef unsigned long long u64; static u64 d2u(double d){ u64 u; __builtin_memcpy(&u,&d,8); return u; }\n' + m.group(0) +
        '\n#include <stdio.h>\nint main(void){ double v[]={0.0,1.0,9.2233720368547748e18,9.2233720368547758e18,-9.2233720368547758e18,1e300,1.7976931348623157e308,__builtin_inf(),-__builtin_inf(),__builtin_nan("")};'
        ' for(int i=0;i<10;i++) printf("%u",fnvl_m_trig_big(v[i])); return 0; }\n')
with tempfile.TemporaryDirectory() as d:
    open(d + '/t.c', 'w').write(prog)
    r = subprocess.run(['cc', '-O1', '-o', d + '/t', d + '/t.c'], capture_output=True, text=True)
    out = subprocess.run([d + '/t'], capture_output=True, text=True).stdout if r.returncode == 0 else r.stderr
check('fnvl_m_trig_big: 0, 1, just below 2^63 -> 0; 2^63, -2^63, 1e300, max -> 1; +-inf, NaN -> 0 (got %s)' % out, out == '0001111000')

# 0 libm symbols in lift/rt, no cr_* definition there, one declaration (rt/fmath.h)
bad = []; defs = []; decls = []
for f in sorted(glob.glob(RT + '/*')):
    if not f.endswith(('.c', '.cc', '.h')): continue
    s = strip(open(f).read())
    for mm in LIBM_RE.finditer(s): bad.append('%s: %s' % (os.path.basename(f), mm.group(0)))
    for mm in re.finditer(r'(?<![\w])(?:double|float)\s+(cr_\w+)\s*\(([^)]*)\)\s*([;{])', s):
        (defs if mm.group(3) == '{' else decls).append((os.path.basename(f), mm.group(1)))
check('lift/rt holds 0 libm symbols (code only): %s' % bad[:5], not bad)
check('no cr_* is defined in lift/rt (L2\'s runtime root builds CORE-MATH): %s' % defs, not defs)
check('every cr_* is declared in rt/fmath.h only, 7 functions', sorted(decls) == sorted(('fmath.h', n) for n in 'cr_sin cr_cos cr_tan cr_exp2m1 cr_log2 cr_log2p1 cr_atan2'.split()))
check('the integer engine kept for the msvcrt functions has no x87-instruction evaluator left (ev_trig ev_exp ev_log ev_atan ev_piq)', not re.search(r'\bev_(trig|exp|log|atan|piq)\b|\bW_(SIN|COS|TAN|2XM1|YL2X|YL2XP1|ATAN2|PIQ)\b', src))

print('FAILED: %d' % len(FAILS) if FAILS else 'all ok')
sys.exit(1 if FAILS else 0)
