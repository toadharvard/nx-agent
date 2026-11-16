#!/usr/bin/env python3
"""ctrans: z3 terms with exact C semantics for the C of lift/rt (the x87 value boundary), derived from the source text.

Why (CLAUDE.md "Derive, never enumerate", rule 2): the value semantics of the x87 carrier ops are the C bodies of rt/fnvl_x87.h and rt/x87.c.  A second hand-written
term next to them would be a second copy of the fact.  This module reads the C, parses it (pycparser) and symbolically executes it into z3 bit-vector terms.

Source.  `Source(root)` reads rt/fnvl.h (typedefs, struct fv, struct cpu: the lines up to the `register struct cpu *c` declaration), rt/x87.h, rt/fnvl_x87.h and rt/x87.c.
Comments are removed, `__attribute__((..))` groups are removed (aligned / may_alias / noinline / fnvnx_f2i do not change the value of any expression; FNVL_F2I is recorded per
function before it is removed), `unsigned __int128` is the typedef-name `u128` (GCC manual, "__int128"), and the preprocessor (cpp from the pinned cpp-13) expands the macros.
Top-level items are split by brace matching.  A function whose text does not parse (C++ templates, inline asm, references ...) is REFUSED with the parser's message: the
subset is whatever pycparser accepts of the source, there is no list.

Semantics (ISO C11 as GCC implements it, ILP32/LP64-independent: the source uses only the fixed-width typedefs and int):
  - integer types are bit-vectors with signedness; usual arithmetic conversions and integer promotions (C11 6.3.1.1, 6.3.1.8); `/ % >> <<` follow 6.5.5-6.5.7.
  - every operation whose C behaviour is undefined (shift count >= width, division by zero, signed overflow, float->int out of range, clz of 0) adds a proof obligation
    `Result.ub` (a z3 Bool that must hold); the term itself is then a defined bit-vector value.  Nothing is approximated.
  - control flow: if / ?: / && / || / switch (with fall-through and break) / return / forward goto / `for` whose induction variable has a constant bound (the unrolled
    trip count must be a z3 literal).  Anything else raises Unsupported.
  - pointers are references into the symbolic store (locals, struct members, array elements); `c->field` reads the field of the global `struct cpu *c` as a free input.
  - double and float are bit-vectors (BV64, BV32).  An operation is the FPA operation on non-NaN operands under the rounding mode `fpcr_rmode`; NaN results follow the
    Arm Architecture Reference Manual (DDI 0487, FPProcessNaNs / FPDefaultNaN / FPConvertNaN, FPCR.DN = 0, AH = 0, FZ = 0 as the console runs): a signalling NaN operand
    (first, then second) is returned quieted, else the first quiet NaN operand, else the default NaN 0x7FF8000000000000 / 0x7FC00000.
  - builtins, one documented mapping (BUILTINS): GCC manual "Other Builtins".
"""
import json, os, re, sys
import z3
from pycparser import c_parser, c_ast
try: import fnvnx.lift.proof.ctasm as ctasm
except ImportError: import ctasm

# The FPCR the terms are a function of.  RMode: Arm ARM (DDI 0487) FPCR.RMode encoding, an architecture constant (00 RN, 01 RP, 10 RM, 11 RZ).  DN and FZ are free inputs
# `fpcr_dn`, `fpcr_fz`: every FP operation adds the obligation DN = 0 and FZ = 0 to `Result.dom` (the NaN rules and the denormal handling modelled are those of DN = FZ = 0), and
# whoever uses a term discharges it from the declared guest thread FPCR (`fpcr_record`).  AH is RES0 on the console CPU (`ah_reserved`).
ARM_RMODE = z3.BitVec('fpcr_rmode', 2)
FPCR_DN = z3.BitVec('fpcr_dn', 1)
FPCR_FZ = z3.BitVec('fpcr_fz', 1)
FPCR_TRAP = z3.BitVec('fpcr_trap', 6)      # FPCR.IDE, IXE, UFE, OFE, DZE, IOE (bits 15, 12..8): FPProcessException traps when the enable bit of the exception is set; the terms are those of all six clear
FPSR_IN = z3.BitVec('fpsr_in', 64)         # the FPSR a function starts with: its cumulative bits (IOC 0, DZC 1, OFC 2, UFC 3, IXC 4, IDC 7) are state, updated by every FP operation (`fpflags`)
# the FPCR a function starts with: DN, FZ, RMode and the six enables as named above, every other bit 0.  FPCR.AH, FIZ, NEP (FEAT_AFP, Armv8.7), FZ16 (FEAT_FP16), EBF (FEAT_EBF16) are absent on the
# console CPU (Cortex-A57, armv8-a: `ah_reserved`), Len and Stride are not in AArch64 (Arm ARM FPCR): they read as 0
FPCR_INIT = z3.Concat(z3.BitVecVal(0, 38), FPCR_DN, FPCR_FZ, ARM_RMODE, z3.BitVecVal(0, 6), z3.Extract(5, 5, FPCR_TRAP), z3.BitVecVal(0, 2), z3.Extract(4, 0, FPCR_TRAP), z3.BitVecVal(0, 8))
TARGETS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'verify', 'targets.json')


class Undecided(Exception):
    """a declared-target row that the terms need and no record states"""


def fpcr_record(path=TARGETS):
    """the initial FPCR of a guest thread from the console CPU record: {"DN": 0|1, "FZ": 0|1, "RMode": 0..3}; Undecided when the record does not state it"""
    with open(path) as fh: cpu = json.load(fh)['console_cpu']
    rec = cpu.get('fpcr')
    if not isinstance(rec, dict) or not {'DN', 'FZ', 'RMode'} <= set(rec):
        raise Undecided('verify/targets.json console_cpu.fpcr (initial FPCR DN, FZ, RMode of a guest thread) is not declared; owner L5 / L1')
    return rec


def ah_reserved(path=TARGETS):
    """FPCR.AH is RES0 below Armv8.7 (FEAT_AFP, Arm ARM): from console_cpu.arch"""
    with open(path) as fh: arch = json.load(fh)['console_cpu']['arch']
    m = re.match(r'armv8(?:\.(\d))?-a$', arch)
    if not m: raise Undecided('console_cpu.arch %r is not armv8[.N]-a' % arch)
    return int(m.group(1) or 0) < 7


class Unsupported(Exception):
    pass


# ---------------------------------------------------------------- source
def strip_comments(t):
    t = re.sub(r'/\*.*?\*/', ' ', t, flags=re.S)
    return re.sub(r'//[^\n]*', '', t)


def strip_attrs(t):
    out, i = [], 0
    while True:
        j = t.find('__attribute__', i)
        if j < 0:
            out.append(t[i:]); break
        out.append(t[i:j]); k = t.index('(', j); d = 0
        while True:
            d += {'(': 1, ')': -1}.get(t[k], 0); k += 1
            if d == 0: break
        i = k
    return ''.join(out)


def split_items(t):
    """top-level items of preprocessed text.  A definition ends at its balanced '}'; a declaration (typedef/struct/union/enum) at the next ';' at depth 0"""
    items, i, n = [], 0, len(t)
    while i < n:
        while i < n and t[i] in ' \t\r\n': i += 1
        if i >= n: break
        decl = t.startswith(('typedef', 'struct', 'union', 'enum'), i)
        j, d = i, 0
        while j < n:
            ch = t[j]
            if ch == '{': d += 1
            elif ch == '}':
                d -= 1
                if d == 0 and not decl: break
            elif ch == ';' and d == 0: break
            j += 1
        items.append(t[i:j + 1]); i = j + 1
    return items


SYSREGS = ('fpsr', 'fpcr')      # the system registers that are state of the model (Machine.fpsr, Machine.fpcr)
ASM_RE = re.compile(r'__asm__\s+volatile\s*\(\s*"(mrs %0, (\w+)|msr (\w+), %0)"\s*:\s*(?:"=r"\s*\(([^()]*)\))?\s*(?::\s*(?:"r"\s*\(([^()]*)\))?\s*)?\)')


def asm_sysreg(text):
    """One mapping, asm body text -> Arm ARM instruction semantics (grammar mapping like `__int128`): `__asm__ volatile("mrs %0, R" : "=r"(X))` is `X = MRS(R)` (Arm ARM, A64 System instructions:
    MRS, Move System Register to general-purpose register) and `__asm__ volatile("msr R, %0" :: "r"(X))` is `MSR(R, X)`, for R a system register the model holds as state (SYSREGS).  The
    statement becomes the call `__arm_mrs_R()` / `__arm_msr_R(X)` that the executor evaluates (BUILTINS).  An asm statement of any other text stays and its function is refused."""
    def sub(m):
        reg = m.group(2) or m.group(3)
        if reg not in SYSREGS: return m.group(0)
        if m.group(2): return '%s = __arm_mrs_%s()' % (m.group(4), reg)
        return '__arm_msr_%s(%s)' % (reg, m.group(5))
    return ASM_RE.sub(sub, text)


FUNC_RE = re.compile(r'^((?:static\s+|inline\s+|extern\s+)*[A-Za-z_][\w\s\*]*?)\b([A-Za-z_]\w*)\s*\(([^{;]*)\)\s*\{', re.S)


def user_text(path):
    """the text of a preprocessed unit (.i) without its linemarkers and without the regions the preprocessor marks as system headers (linemarker flag 3, GNU cpp manual
    'Preprocessor Output'): the source of the root itself"""
    out, sysh = [], False
    with open(path) as fh:
        for line in fh:
            m = re.match(r'# (\d+) "([^"]*)"((?: \d+)*)\s*$', line)
            if m:
                sysh = '3' in m.group(3).split(); continue
            if line.startswith('#'): continue
            if not sysh: out.append(line)
    return ''.join(out)


class Source:
    """the C of lift/rt as items, read from the PREPROCESSED units the recipe's PP stage makes with the guest compiler (`ipaths`, .i files): functions (name -> text), type
    declarations.  `macro_files` (the headers' text) give the macro names and bodies the closure follows; the functions come from the .i, so their macros are expanded."""

    def __init__(self, ipaths, macro_files):
        self.macros = {}
        for n in macro_files:
            with open(n) as fh: t = strip_comments(fh.read()).replace('\\\n', ' ')
            for m in re.finditer(r'^[ \t]*#define[ \t]+(\w+)(\([^)]*\))?[ \t]*(.*)$', t, re.M): self.macros.setdefault(m.group(1), (m.group(2), m.group(3)))
        self.types, self.funcs, self.refused = [], {}, {}
        seen = set()
        for p in ipaths:
            text = user_text(p)
            text = re.sub(r'unsigned\s+__int128', 'u128', text)       # grammar mapping: pycparser has no __int128 (GCC manual "__int128"); u128 is a typedef-name of ctrans
            text = strip_attrs(text)
            text = asm_sysreg(text)
            text = ctasm.convert_asm(text)
            text = re.sub(r'\bregister\s+struct\s+cpu\s*\*\s*c\s+asm\s*\([^)]*\)\s*;', '', text)
            for it in split_items(text):
                head = it.split('{')[0]
                m = FUNC_RE.match(it) if it.rstrip().endswith('}') else None
                if m and not it.lstrip().startswith(('typedef', 'struct', 'enum', 'union')) and 'template' not in head and 'extern "C"' not in head:
                    self.funcs.setdefault(m.group(2), it)
                elif it.lstrip().startswith(('typedef', 'struct', 'enum', 'union')) and ('(' not in head or it.lstrip().startswith('typedef')):
                    if it not in seen: seen.add(it); self.types.append(it)
                elif it.strip().startswith('template'):
                    mm = re.search(r'\b(\w+)\s*\(', head); self.refused[mm.group(1) if mm else it[:30]] = 'C++ template'

    def prelude(self):
        """the type declarations that parse, in order (a C++ declaration of the unit is dropped); u128 and `c` are ctrans's own"""
        pre = 'typedef int u128;\nextern struct cpu *c;\n'
        for t in self.types:
            if re.match(r'typedef\s+u128\s+u128', t) or t.lstrip().startswith('struct fnvl_nframe;'): continue
            cand = pre + (t if t.rstrip().endswith(';') else t + ';') + '\n'
            try: c_parser.CParser().parse(cand, 'prelude')
            except Exception: continue
            pre = cand
            m = re.match(r'struct\s+(\w+)\s*\{', t)
            if m: pre += 'typedef struct %s %s;\n' % (m.group(1), m.group(1))      # grammar mapping: in C++ a struct tag is a type name (ISO C++ [class.name])
        return pre


# ---------------------------------------------------------------- parse
def parse_unit(src, names=None):
    """parse the type declarations and every function of `names` (all when None); -> (ast of types, {name: FuncDef}, {name: reason refused})"""
    pre = src.prelude()
    tast = c_parser.CParser().parse(pre, 'types')
    protos = []
    funcs, refused = {}, {}
    for fname, ftxt in src.funcs.items():
        if names is not None and fname not in names: continue
        try:
            u = c_parser.CParser().parse(pre + '\n'.join(protos) + '\n' + ftxt, fname)
            fd = [e for e in u.ext if isinstance(e, c_ast.FuncDef) and e.decl.name == fname]
            funcs[fname] = fd[0]
        except Exception as e:
            refused[fname] = 'parse: ' + str(e).split('\n')[0][:160]
    return tast, funcs, refused


# ---------------------------------------------------------------- types and values
class T(tuple):
    pass


def I(bits, signed): return ('i', bits, signed)


INTS = {'u8': I(8, 0), 'u16': I(16, 0), 'u32': I(32, 0), 'u64': I(64, 0), 'u128': I(128, 0), 's8': I(8, 1), 's16': I(16, 1), 's32': I(32, 1), 's64': I(64, 1)}
F64, F32 = ('f', 64), ('f', 32)
INT = I(32, 1)


class V:
    __slots__ = ('e', 't')

    def __init__(self, e, t): self.e, self.t = e, t

    def __repr__(self): return 'V(%s,%s)' % (self.e, self.t)


class Ptr:
    __slots__ = ('cont', 'key', 'et')

    def __init__(self, cont, key, et): self.cont, self.key, self.et = cont, key, et


def width(t): return t[1]


def kindof(spec_names):
    s = ' '.join(spec_names)
    return s


def rm_term(r=None):
    r = ARM_RMODE if r is None else r
    return z3.If(r == 0, z3.RNE(), z3.If(r == 1, z3.RTP(), z3.If(r == 2, z3.RTN(), z3.RTZ())))


def fpsort(t): return z3.Float64() if t[1] == 64 else z3.Float32()


def tofp(e, t): return z3.fpBVToFP(e, fpsort(t))


def frombits(fp): return z3.fpToIEEEBV(fp)


def nan_(e, t):
    eb, mb = (11, 52) if t[1] == 64 else (8, 23)
    return z3.And(z3.Extract(eb + mb - 1, mb, e) == (1 << eb) - 1, z3.Extract(mb - 1, 0, e) != 0)


def snan_(e, t):
    mb = 52 if t[1] == 64 else 23
    return z3.And(nan_(e, t), z3.Extract(mb - 1, mb - 1, e) == 0)


def quiet(e, t):
    mb = 52 if t[1] == 64 else 23
    return e | z3.BitVecVal(1 << (mb - 1), t[1])


def defnan(t): return z3.BitVecVal(0x7FF8000000000000 if t[1] == 64 else 0x7FC00000, t[1])


def fop(kind, args, t, rmode=None):
    """one FP operation `kind` (a z3 FPA constructor name) on operand bit patterns of type t: FPA when no operand is NaN, else Arm FPProcessNaNs; a NaN FPA result is the
    default NaN (invalid operation).  Arm ARM DDI 0487 'FPProcessNaNs', 'FPDefaultNaN'; FPCR.DN = 0."""
    fps = [tofp(a, t) for a in args]
    rm = rm_term(rmode)
    if kind == 'add': r = z3.fpAdd(rm, *fps)
    elif kind == 'sub': r = z3.fpSub(rm, *fps)
    elif kind == 'mul': r = z3.fpMul(rm, *fps)
    elif kind == 'div': r = z3.fpDiv(rm, *fps)
    elif kind == 'sqrt': r = z3.fpSqrt(rm, *fps)
    elif kind in ('roundeven', 'floor', 'ceil', 'trunc'):
        r = z3.fpRoundToIntegral({'roundeven': z3.RNE(), 'floor': z3.RTN(), 'ceil': z3.RTP(), 'trunc': z3.RTZ()}[kind], *fps)
    else: raise Unsupported('fop ' + kind)
    res = z3.If(z3.fpIsNaN(r), defnan(t), frombits(r))
    for a in reversed(args):
        pass
    out = res
    # NaN operands: first signalling (quieted), else first quiet
    for a in reversed(args): out = z3.If(z3.And(nan_(a, t), z3.Not(snan_(a, t))), a, out)
    for a in reversed(args): out = z3.If(snan_(a, t), quiet(a, t), out)
    return out


# ---------------------------------------------------------------- the cumulative exception bits of the FPSR
# Source (pinned): Arm A-profile A64 ISA XML 2023-09, ISA_A64_xml_A_profile-2023-09.tar.gz sha256 7f2092a1416fa1f8b784e96fbc7cdd67b2ce81f84426968279694f96f1b11596, file shared_pseudocode.xml
# (the pseudocode of DDI 0487 as Arm publishes it); the function of every row is named.  AH = 0 (FEAT_AFP absent, `ah_reserved`), so every `altfp` branch is the false one.
#   FPProcessException: FPSR<cumul> = 1 with cumul IOC 0, DZC 1, OFC 2, UFC 3, IXC 4, IDC 7 (no trap: the six enables are 0, obligation FPCR_TRAP)
#   FPUnpackBase: the IDC bit (FPExc_InputDenorm) is raised only when `fz` (FPCR.FZ = 1) flushes a denormal input; FPCR.FZ = 0 (obligation), so IDC is never raised, and a denormal input is
#                 FPType_Denormal with its exact value
#   FPProcessNaN / FPProcessNaNs: IOC for a signalling NaN operand (type1 SNaN, or type2 SNaN; a QNaN alone raises nothing)
#   FPAdd/FPSub/FPMul/FPDiv/FPSqrt: IOC for the invalid operation (inf + -inf, 0 * inf, 0 / 0, inf / inf, sqrt of a negative nonzero or -inf), DZC for x / 0 with x finite and nonzero
#   FPRoundBase (FPRound, FPRoundCV), non-altfp: with the exact value op != 0, E exponent bits, F fraction bits, minimum_exp:
#       exponent = floor(log2 |op|); biased_exp = max(exponent - minimum_exp + 1, 0); error = the part of the denormal-grid / normal-grid quotient that rounding drops
#       UFC  iff biased_exp == 0 and error != 0          (tininess before rounding: |op| < 2^minimum_exp)
#       round up as the mode says; OFC iff biased_exp >= 2^E - 1 after rounding, with unbounded exponent range (and then error := 1)
#       IXC  iff error != 0 (after the overflow rule)
#   FPCompare: IOC for a signalling NaN, and for a QNaN when signal_nans (FCMPE, the ordered relations; GCC aarch64: aarch64_select_cc_mode gives CCFPEmode to < <= > >=, CCFPmode to == !=)
#   FPRoundInt (FRINTN/M/P/Z, exact = FALSE): IOC for a signalling NaN, nothing else.  FPToFixed (FCVTZS/FCVTZU): IXC iff error != 0 (the C range obligation excludes NaN and overflow)
#   FPConvert (FCVT): IOC for a signalling NaN, FPRoundCV for a finite nonzero operand.  FixedToFP (SCVTF/UCVTF): FPRound of the integer, IXC iff inexact.
# Class rule for the rounding bits: the unbounded-exponent rounding of the exact result to F+1 bits is the FPA operation in the format with the same precision and exponent width 15 (`wide`);
# it differs from the pseudocode's exact value only by the rounding the pseudocode applies too, so (overflow: its magnitude >= 2^(emax+1); tininess: the round-toward-zero value is below the
# smallest normal, which a value >= 2^minimum_exp never is) and (inexact: the round-toward-positive and round-toward-negative results differ iff the exact value is not representable).
FPFMT = {64: (53, 1024, 1022), 32: (24, 128, 126)}      # significand bits, emax + 1, -minimum_exp


def fpbits(ioc=None, dzc=None, ofc=None, ufc=None, ixc=None):
    """the FPSR cumulative bits as a 64-bit vector (IOC bit 0, DZC 1, OFC 2, UFC 3, IXC 4; IDC bit 7 is never set, FPUnpackBase with FPCR.FZ = 0)"""
    b = lambda x: z3.BitVecVal(0, 1) if x is None else z3.If(x, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1))
    return z3.ZeroExt(59, z3.Concat(b(ixc), b(ufc), b(ofc), b(dzc), b(ioc)))


def _wide(sb): return z3.FPSort(15, sb)


def _const(sb, e2, sign=0):
    """the wide-format value 2^e2 (e2 may be negative): exponent field 16383 + e2, fraction 0"""
    return z3.fpBVToFP(z3.BitVecVal(((sign << 15) | (16383 + e2)) << (sb - 1), 16 + sb - 1), _wide(sb))


def _round_bits(rm, narrow_of, wide_of, sb, emax1, emin, fin):
    """FPRoundBase's OFC, UFC, IXC.  narrow_of(mode) is the operation in the operand format rounded by `mode`, wide_of(mode) the same operation in the wide format; FIN says the operation reaches FPRound"""
    rw, rz = wide_of(rm), wide_of(z3.RTZ())
    ofc = z3.And(fin, z3.fpGEQ(z3.fpAbs(rw), _const(sb, emax1)))
    inex = z3.Not(z3.fpEQ(narrow_of(z3.RTP()), narrow_of(z3.RTN())))
    ufc = z3.And(fin, z3.fpLT(z3.fpAbs(rz), _const(sb, -emin)), inex)
    return ofc, ufc, z3.And(fin, inex)


def fpflags(kind, args, t, rmode=None):
    """the FPSR cumulative bits (64-bit vector) the operation `kind` raises on the operand patterns ARGS of type t (FPCR.DN = FZ = 0, traps off), per the rows above.
    kinds: add sub mul div sqrt, roundeven floor ceil trunc, cmp (fcmp), cmpe (fcmpe)"""
    sb, emax1, emin = FPFMT[t[1]]
    nan = [nan_(a, t) for a in args]; snan = [snan_(a, t) for a in args]
    anynan, anysnan = z3.Or(*nan) if len(nan) > 1 else nan[0], z3.Or(*snan) if len(snan) > 1 else snan[0]
    if kind in ('cmp', 'cmpe'): return fpbits(ioc=anynan if kind == 'cmpe' else anysnan)
    if kind in ('roundeven', 'floor', 'ceil', 'trunc'): return fpbits(ioc=anysnan)
    rm = rm_term(rmode)
    fps = [tofp(a, t) for a in args]
    wfps = [z3.fpToFP(z3.RNE(), x, _wide(sb)) for x in fps]
    opf = {'add': z3.fpAdd, 'sub': z3.fpSub, 'mul': z3.fpMul, 'div': z3.fpDiv}.get(kind)
    narrow_of = (lambda m: opf(m, *fps)) if opf else (lambda m: z3.fpSqrt(m, *fps))
    wide_of = (lambda m: (opf or z3.fpSqrt)(m, *wfps))
    r = narrow_of(rm)
    ioc = z3.Or(anysnan, z3.And(z3.Not(anynan), z3.fpIsNaN(r)))
    dzc = z3.And(z3.Not(anynan), z3.fpIsZero(fps[1]), z3.Not(z3.fpIsZero(fps[0])), z3.Not(z3.fpIsInf(fps[0]))) if kind == 'div' else None
    rw = wide_of(rm)
    fin = z3.And(z3.Not(anynan), z3.Not(z3.Or(*[z3.fpIsInf(x) for x in fps])), z3.Not(z3.fpIsNaN(rw)), z3.Not(z3.fpIsInf(rw)), z3.Not(z3.fpIsZero(rw)))
    ofc, ufc, ixc = _round_bits(rm, narrow_of, wide_of, sb, emax1, emin, fin)
    return fpbits(ioc, dzc, ofc, ufc, ixc)


def fpflags_cvt(src, dst, e, rmode=None):
    """FPConvert / FixedToFP / FPToFixed: src and dst are ('f', bits) or ('i', bits, signed); E is the operand pattern"""
    rm = rm_term(rmode)
    if src[0] == 'f' and dst[0] == 'f':
        sn = snan_(e, src)
        if dst[1] >= src[1]: return fpbits(ioc=sn)
        sb, emax1, emin = FPFMT[dst[1]]
        x = tofp(e, src)
        fin = z3.Not(z3.Or(nan_(e, src), z3.fpIsInf(x), z3.fpIsZero(x)))
        ofc, ufc, ixc = _round_bits(rm, lambda m: z3.fpToFP(m, x, fpsort(dst)), lambda m: z3.fpToFP(m, x, _wide(sb)), sb, emax1, emin, fin)
        return fpbits(sn, None, ofc, ufc, ixc)
    if src[0] == 'i' and dst[0] == 'f':
        sb, emax1, emin = FPFMT[dst[1]]
        cv = z3.fpSignedToFP if src[2] else z3.fpUnsignedToFP
        ofc, ufc, ixc = _round_bits(rm, lambda m: cv(m, e, fpsort(dst)), lambda m: cv(m, e, _wide(sb)), sb, emax1, emin, e != 0)
        return fpbits(None, None, ofc, ufc, ixc)
    x = tofp(e, src)
    return fpbits(ixc=z3.And(z3.Not(nan_(e, src)), z3.Not(z3.fpEQ(x, z3.fpRoundToIntegral(z3.RTZ(), x)))))


# ---------------------------------------------------------------- the executor
class UnionVal(dict):
    """a union of scalars (ctype 'U'): the stored bit pattern; a member read is its low bits, a member write replaces its low bits and keeps the rest (the bytes of the union object, little-endian target)"""
    def __init__(self, t, bits): super().__init__(); self.t, self.bits = t, bits; self.mt = dict(t[2])
    def __getitem__(self, k): mt = self.mt[k]; return V(z3.Extract(mt[1] - 1, 0, self.bits), mt)
    def __setitem__(self, k, v):
        if k not in self.mt: return dict.__setitem__(self, k, v)
        w = self.t[1]; e = v.e
        self.bits = e if e.size() == w else z3.Concat(z3.Extract(w - 1, e.size(), self.bits), e)
    def __contains__(self, k): return k in self.mt


class Frame:
    def __init__(self, name, L):
        self.name, self.vars, self.L, self.pend, self.ret, self.rett = name, {}, L, {}, None, None


class Result:
    def __init__(self, ret, outs, ub, dom=(), fpsr=None, fpcr=None): self.ret, self.outs, self.ub, self.dom, self.fpsr, self.fpcr = ret, outs, ub, list(dom), fpsr, fpcr


class Lazy(dict):
    """a struct input whose members are free symbolic inputs created on first use (struct cpu: the register file the lifted code runs on)"""
    def __init__(self, m, sname, name):
        super().__init__(); self.m, self.sname, self.name = m, sname, name
        self.ft = dict(m.structs[sname])

    def __missing__(self, k):
        if k not in self.ft: raise KeyError(k)
        v = self.m.fresh_val(self.ft[k], self.name + '.' + k) if not self.m.has_union(self.ft[k]) else None
        if v is None: raise Unsupported('union member ' + k)
        self[k] = v
        return v


MEM_IN = z3.Array('mem0', z3.BitVecSort(64), z3.BitVecSort(8))      # guest memory at entry: byte at a 64-bit address (the asm templates address with the 64-bit register)
GLOBAL_REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')      # `register u32 R asm(..)` of rt/fnvl.h: the guest registers, free inputs


class Machine:
    def __init__(self, tast, funcs, f2i=()):
        self.funcs, self.structs, self.typedefs, self.enums = funcs, {}, {}, {}
        self.G = {}               # globals: 'c' -> struct cpu (fields free inputs)
        self.ub = []
        self.dom = []
        self.fpsr, self.fpcr, self.rmode = FPSR_IN, FPCR_INIT, ARM_RMODE
        self.fresh = 0
        self.mem = MEM_IN
        for e in tast.ext: self.declare(e)

    # -- declarations
    def declare(self, e):
        if isinstance(e, c_ast.Typedef):
            self.typedefs[e.name] = self.ctype(e.type)
        elif isinstance(e, c_ast.Decl) and isinstance(e.type, (c_ast.Struct, c_ast.Union, c_ast.Enum)):
            self.ctype(e.type)

    def ctype(self, n):
        if isinstance(n, c_ast.TypeDecl): return self.ctype(n.type)
        if isinstance(n, c_ast.IdentifierType):
            nm = ' '.join(n.names)
            if nm in INTS: return INTS[nm]
            if nm in self.typedefs: return self.typedefs[nm]
            return {'int': INT, 'unsigned int': I(32, 0), 'unsigned': I(32, 0), 'long long': I(64, 1), 'unsigned long long': I(64, 0), 'double': F64, 'float': F32,
                    'unsigned char': I(8, 0), 'signed char': I(8, 1), 'char': I(8, 1), 'short': I(16, 1), 'unsigned short': I(16, 0), 'long long int': I(64, 1),
                    'long long unsigned int': I(64, 0), 'void': ('v',)}.get(nm) or (_ for _ in ()).throw(Unsupported('type ' + nm))
        if isinstance(n, c_ast.Struct):
            if n.decls is not None:
                self.structs[n.name or id(n)] = [(d.name, self.ctype(d.type)) for d in n.decls]
            return ('s', n.name or id(n))
        if isinstance(n, c_ast.Union):
            if n.decls is not None:
                ms = [(d.name, self.ctype(d.type)) for d in n.decls]
                if ms and all(t[0] in 'if' for _, t in ms): return ('U', max(t[1] for _, t in ms), tuple(ms))      # a union of scalars: the bit pattern of its largest member (GCC manual, "Structures, unions, enumerations, and bit-fields": type punning through a union reads the bytes of the stored member)
            return ('u', n.name or id(n))   # a union is declared but no value of it is ever built: using one is refused where it is read
        if isinstance(n, c_ast.Enum):
            v = 0
            for en in n.values.enumerators:
                if en.value is not None: v = self.const_int(en.value)
                self.enums[en.name] = v; v += 1
            return INT
        if isinstance(n, c_ast.PtrDecl): return ('p', self.ctype(n.type))
        if isinstance(n, c_ast.ArrayDecl): return ('a', self.ctype(n.type), self.const_int(n.dim) if n.dim is not None else None)
        if isinstance(n, c_ast.FuncDecl): return ('v',)
        raise Unsupported('type node ' + type(n).__name__)

    def const_int(self, n):
        v = self.eval(n, Frame('const', z3.BoolVal(True)))
        s = z3.simplify(v.e)
        if not z3.is_bv_value(s): raise Unsupported('non-constant integer expression')
        return s.as_long()

    # -- values
    def zero(self, t):
        if t[0] == 'i': return V(z3.BitVecVal(0, t[1]), t)
        if t[0] == 'f': return V(z3.BitVecVal(0, t[1]), t)
        if t[0] == 's': return {n: self.zero(ft) for n, ft in self.structs[t[1]]}
        if t[0] == 'a': return [self.zero(t[1]) for _ in range(t[2])]
        if t[0] == 'p': return None
        if t[0] == 'U': return UnionVal(t, z3.BitVecVal(0, t[1]))
        if t[0] == 'u': return None       # an uninitialised union local (the lifter's xmm_t x0..x7): reading it is an error of the executor, never a value
        raise Unsupported('zero of ' + str(t))

    def fresh_val(self, t, name):
        if t[0] in ('i', 'f'): return V(z3.BitVec(name, t[1]), t)
        if t[0] == 's':
            if any(self.has_union(ft) for _, ft in self.structs[t[1]]): return Lazy(self, t[1], name)       # struct cpu: members are created when read
            return {n: self.fresh_val(ft, name + '.' + n) for n, ft in self.structs[t[1]]}
        if t[0] == 'a': return [self.fresh_val(t[1], '%s[%d]' % (name, i)) for i in range(t[2])]
        raise Unsupported('input of type ' + str(t))

    def has_union(self, t):
        if t[0] == 'u': return True
        if t[0] == 'a': return self.has_union(t[1])
        if t[0] == 's': return any(self.has_union(ft) for _, ft in self.structs[t[1]])
        return False

    def tyof(self, v):
        return v.t

    def guard(self, fr, old, new):
        if old is None: return new
        if isinstance(new, V): return V(z3.If(fr.L, new.e, old.e), new.t)
        if isinstance(new, dict): return {k: self.guard(fr, old[k], new[k]) for k in new}
        if isinstance(new, list): return [self.guard(fr, o, n) for o, n in zip(old, new)]
        return new

    def ite(self, c, a, b):
        if isinstance(a, V): return V(z3.If(c, a.e, b.e), a.t)
        if isinstance(a, dict): return {k: self.ite(c, a[k], b[k]) for k in a}
        if isinstance(a, list): return [self.ite(c, x, y) for x, y in zip(a, b)]
        raise Unsupported('?: on pointers')

    def copy(self, v):
        if isinstance(v, dict): return {k: self.copy(x) for k, x in v.items()}
        if isinstance(v, list): return [self.copy(x) for x in v]
        return v

    # -- conversions
    def cast(self, v, t, fr):
        if not isinstance(v, V): raise Unsupported('cast of non-scalar')
        s = v.t
        if s == t: return v
        if t[0] == 'i' and s[0] == 'i':
            if t[1] == s[1]: return V(v.e, t)
            if t[1] < s[1]: return V(z3.Extract(t[1] - 1, 0, v.e), t)
            return V((z3.SignExt if s[2] else z3.ZeroExt)(t[1] - s[1], v.e), t)
        if 'f' in (t[0], s[0]): self.fpdom(fr)
        if t[0] == 'f' and s[0] == 'i':
            x = (z3.fpSignedToFP if s[2] else z3.fpUnsignedToFP)(rm_term(self.rmode), v.e, fpsort(t))
            self.raise_(fr, fpflags_cvt(s, t, v.e, self.rmode))
            return V(frombits(x), t)
        if t[0] == 'i' and s[0] == 'f':
            x = tofp(v.e, s); b = t[1]; sig = s[1] == 64
            lo = -(1 << (b - 1)) - 1 if t[2] else -1
            hi = (1 << (b - 1)) if t[2] else (1 << b)
            ok = z3.And(z3.Not(z3.fpIsNaN(x)), z3.fpGT(x, z3.FPVal(float(lo), fpsort(s))), z3.fpLT(x, z3.FPVal(float(hi), fpsort(s))))
            self.ub.append(z3.Implies(fr.L, ok))
            self.raise_(fr, fpflags_cvt(s, t, v.e, self.rmode))
            return V((z3.fpToSBV if t[2] else z3.fpToUBV)(z3.RTZ(), x, z3.BitVecSort(b)), t)
        if t[0] == 'f' and s[0] == 'f':
            self.raise_(fr, fpflags_cvt(s, t, v.e, self.rmode))
            if s[1] == 32 and t[1] == 64:          # Arm FCVT: NaN keeps its payload, quieted
                nanr = z3.Concat(z3.Extract(31, 31, v.e), z3.BitVecVal(0x7FF, 11), z3.BitVecVal(1, 1), z3.Extract(21, 0, v.e), z3.BitVecVal(0, 29))
                return V(z3.If(nan_(v.e, s), nanr, frombits(z3.fpToFP(z3.RNE(), tofp(v.e, s), z3.Float64()))), t)
            nanr = z3.Concat(z3.Extract(63, 63, v.e), z3.BitVecVal(0xFF, 8), z3.BitVecVal(1, 1), z3.Extract(50, 29, v.e))
            return V(z3.If(nan_(v.e, s), nanr, frombits(z3.fpToFP(rm_term(self.rmode), tofp(v.e, s), z3.Float32()))), t)
        raise Unsupported('cast %s -> %s' % (s, t))

    def fpdom(self, fr):
        if fr is not None:
            f = self.fpcr
            self.dom.append(z3.Implies(fr.L, z3.And(z3.Extract(25, 25, f) == 0, z3.Extract(24, 24, f) == 0, z3.Extract(15, 15, f) == 0, z3.Extract(12, 8, f) == 0)))

    def raise_(self, fr, bits):
        """FPProcessException: the cumulative bits BITS (fpflags) are set in the FPSR when the path condition holds"""
        if fr is not None: self.fpsr = z3.If(fr.L, self.fpsr | bits, self.fpsr)

    def msr(self, fr, reg, v):
        """MSR <reg>, Xt (Arm ARM, A64 System instructions: Move general-purpose register to System register): FPSR and FPCR are the whole 64-bit register value"""
        x = self.cast(v, I(64, 0), fr).e
        if reg == 'fpsr': self.fpsr = z3.If(fr.L, x, self.fpsr)
        else:
            self.fpcr = z3.If(fr.L, x, self.fpcr); self.rmode = z3.If(fr.L, z3.Extract(23, 22, x), self.rmode)

    def promote(self, v):
        if v.t[0] == 'i' and v.t[1] < 32: return self.cast(v, INT, None)
        return v

    def usual(self, a, b, fr):
        a, b = self.promote(a), self.promote(b)
        if a.t[0] == 'f' or b.t[0] == 'f':
            t = F64 if F64 in (a.t, b.t) else F32
            return self.cast(a, t, fr), self.cast(b, t, fr)
        ta, tb = a.t, b.t
        if ta == tb: return a, b
        if ta[1] != tb[1]: t = ta if ta[1] > tb[1] else tb
        else: t = I(ta[1], 0)
        return self.cast(a, t, fr), self.cast(b, t, fr)

    def truth(self, v, fr=None):
        if v.t[0] == 'i': return v.e != 0
        self.raise_(fr, fpflags('cmp', [v.e], v.t))      # x != 0.0 is FCMP against zero
        return z3.Not(z3.fpIsZero(tofp(v.e, v.t)))

    def bool_(self, c): return V(z3.If(c, z3.BitVecVal(1, 32), z3.BitVecVal(0, 32)), INT)

    # -- expressions
    def lval(self, n, fr):
        """-> (container, key, type) of an lvalue"""
        if isinstance(n, c_ast.ID):
            if n.name in fr.vars: return fr.vars, n.name, None
            if n.name in GLOBAL_REGS: self.eval(n, fr); return self.G, n.name, None
            raise Unsupported('lvalue ' + n.name)
        if isinstance(n, c_ast.StructRef):
            if n.type == '->':
                p = self.eval(n.name, fr)
                if not isinstance(p, Ptr): raise Unsupported('-> of non-pointer')
                if p.cont is self.G and p.key == 'c': self.cpu()
                base = self.load(p)
            else:
                cont, key, _ = self.lval(n.name, fr); base = cont[key]
            if isinstance(base, str) and base == 'cpu':
                pass
            if not isinstance(base, dict): raise Unsupported('member of non-struct')
            return base, n.field.name, None
        if isinstance(n, c_ast.ArrayRef):
            p = self.eval(n.name, fr); i = self.eval(n.subscript, fr)
            s = z3.simplify(i.e)
            if not z3.is_bv_value(s): raise Unsupported('non-constant array index')
            return p.cont, p.key + (s.as_signed_long() if i.t[2] else s.as_long()), None
        if isinstance(n, c_ast.UnaryOp) and n.op == '*':
            p = self.eval(n.expr, fr)
            if not isinstance(p, Ptr): raise Unsupported('deref of non-pointer')
            return p.cont, p.key, None
        raise Unsupported('lvalue ' + type(n).__name__)

    def load(self, p): return p.cont[p.key]

    def eval(self, n, fr):
        m = getattr(self, 'e_' + type(n).__name__, None)
        if m is None: raise Unsupported('expression ' + type(n).__name__)
        return m(n, fr)

    def e_Constant(self, n, fr):
        s = n.value
        if n.type in ('int', 'long int', 'long long int', 'unsigned int', 'unsigned long int', 'unsigned long long int'):
            m = re.match(r'^(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9]\d*)([uUlL]*)$', s)
            digs, suf = m.group(1), m.group(2).lower()
            v = int(digs, 0) if not (digs.startswith('0') and len(digs) > 1 and not digs.lower().startswith('0x')) else int(digs, 8)
            u, ll = 'u' in suf, 'll' in suf or suf.count('l') > 0
            if ll:
                t = I(64, 0) if u or v >= 1 << 63 else I(64, 1)
            elif u: t = I(32, 0) if v < 1 << 32 else I(64, 0)
            else:
                dec = not digs.lower().startswith('0')
                if v < 1 << 31: t = INT
                elif not dec and v < 1 << 32: t = I(32, 0)
                elif v < 1 << 63: t = I(64, 1)
                else: t = I(64, 0)
            return V(z3.BitVecVal(v, t[1]), t)
        if n.type == 'char': return V(z3.BitVecVal(ord(s[1:-1]), 32), INT)
        if n.type == 'string': return V(z3.BitVecVal(0, 32), INT)      # only __builtin_nan("") takes one; its argument is not read
        if n.type in ('double', 'float'):
            import struct
            f32 = s[-1] in 'fF'
            fv = float.fromhex(s.rstrip('fFlL')) if 'x' in s.lower() else float(s.rstrip('fFlL'))
            if f32: return V(z3.BitVecVal(struct.unpack('<I', struct.pack('<f', fv))[0], 32), F32)
            return V(z3.BitVecVal(struct.unpack('<Q', struct.pack('<d', fv))[0], 64), F64)
        raise Unsupported('constant ' + n.type)

    def e_ID(self, n, fr):
        if n.name in fr.vars:
            v = fr.vars[n.name]
            if isinstance(v, list): return Ptr(v, 0, None)
            return v
        if n.name in self.enums: return V(z3.BitVecVal(self.enums[n.name], 32), INT)
        if n.name == 'c': return Ptr(self.G, 'c', ('s', 'cpu'))
        if n.name in GLOBAL_REGS:
            if n.name not in self.G: self.G[n.name] = self.fresh_val(I(32, 0), 'g.' + n.name)
            return self.G[n.name]
        raise Unsupported('identifier ' + n.name)

    def cpu(self):
        if 'c' not in self.G: self.G['c'] = self.fresh_val(('s', 'cpu'), 'c')
        return self.G['c']

    def e_StructRef(self, n, fr):
        if n.type == '->':
            p = self.eval(n.name, fr)
            if not isinstance(p, Ptr): raise Unsupported('-> of non-pointer')
            if p.cont is self.G and p.key == 'c': self.cpu()
            base = self.load(p)
        else:
            base = self.eval(n.name, fr)
        if not isinstance(base, dict): raise Unsupported('member of non-struct')
        v = base[n.field.name]
        if isinstance(v, list): return Ptr(v, 0, None)
        return v

    def e_ArrayRef(self, n, fr):
        p = self.eval(n.name, fr)
        if isinstance(p, Ptr) and isinstance(p.cont, list):
            i = self.eval(n.subscript, fr)
            if not z3.is_bv_value(z3.simplify(i.e)) and p.key == 0 and p.cont and all(isinstance(x, V) and x.t == p.cont[0].t for x in p.cont):
                # a read at a symbolic index of an array of scalars (C11 6.5.2.1): the selected element; an index outside the array is undefined (J.2), an obligation
                idx = self.cast(i, I(64, 0), fr).e if i.t[1] != 64 else i.e
                self.ub.append(z3.Implies(fr.L, z3.ULT(idx, z3.BitVecVal(len(p.cont), 64))))
                r = p.cont[-1].e
                for j in reversed(range(len(p.cont) - 1)): r = z3.If(idx == j, p.cont[j].e, r)
                return V(r, p.cont[0].t)
        cont, key, _ = self.lval(n, fr)
        return cont[key]

    def e_Cast(self, n, fr):
        t = self.ctype(n.to_type.type)
        v = self.eval(n.expr, fr)
        if t == ('v',): return None                          # (void)e: evaluated for its effects, no value
        if isinstance(v, Ptr) or t[0] == 'p': raise Unsupported('pointer cast')
        return self.cast(v, t, fr)

    def e_ExprList(self, n, fr):
        r = None
        for e in n.exprs: r = self.eval(e, fr)
        return r

    def e_UnaryOp(self, n, fr):
        op = n.op
        if op == '&':
            if isinstance(n.expr, c_ast.ID) and n.expr.name in fr.vars and isinstance(fr.vars[n.expr.name], list): return Ptr(fr.vars[n.expr.name], 0, None)
            cont, key, _ = self.lval(n.expr, fr); return Ptr(cont, key, None)
        if op == '*':
            cont, key, _ = self.lval(n, fr); return cont[key]
        if op in ('p++', 'p--', '++', '--'):
            cont, key, _ = self.lval(n.expr, fr); old = cont[key]
            one = V(z3.BitVecVal(1, old.t[1]), old.t) if old.t[0] == 'i' else None
            new = V(old.e + one.e if '+' in op else old.e - one.e, old.t)
            cont[key] = self.guard(fr, old, new)
            return old if op.startswith('p') else new
        if op == 'sizeof':      # C11 6.5.3.4: the size of a scalar type in bytes (an integer or floating type: its width / 8), of type size_t = unsigned int (the data model, ILP32)
            t = self.ctype(n.expr.type) if isinstance(n.expr, c_ast.Typename) else self.tyof(self.eval(n.expr, fr))
            if t[0] not in 'if': raise Unsupported('sizeof of a non-scalar')
            return V(z3.BitVecVal(t[1] // 8, 32), I(32, 0))
        v = self.eval(n.expr, fr)
        if op == '!': return self.bool_(z3.Not(self.truth(v, fr)))
        v = self.promote(v)
        if op == '-':
            if v.t[0] == 'f': return V(v.e ^ z3.BitVecVal(1 << (v.t[1] - 1), v.t[1]), v.t)
            if v.t[2]: self.ub.append(z3.Implies(fr.L, v.e != z3.BitVecVal(1 << (v.t[1] - 1), v.t[1])))
            return V(-v.e, v.t)
        if op == '+': return v
        if op == '~': return V(~v.e, v.t)
        if op == 'sizeof': raise Unsupported('sizeof')
        raise Unsupported('unary ' + op)

    def arith(self, op, a, b, fr):
        if op in ('<<', '>>'):
            a = self.promote(a); b = self.promote(b)
            bw = a.t[1]
            cnt = self.cast(b, I(bw, 0), fr).e if b.t[1] != bw else b.e
            if b.t[2] and b.t[1] <= bw: self.ub.append(z3.Implies(fr.L, b.e >= 0)) if b.t[1] == bw else None
            self.ub.append(z3.Implies(fr.L, z3.ULT(cnt, z3.BitVecVal(bw, bw))))
            if op == '<<':
                if a.t[2]: self.ub.append(z3.Implies(fr.L, a.e >= 0))
                return V(a.e << cnt, a.t)
            return V(a.e >> cnt if a.t[2] else z3.LShR(a.e, cnt), a.t)
        a, b = self.usual(a, b, fr)
        t = a.t
        if t[0] == 'f':
            self.fpdom(fr)
            if op in ('+', '-', '*', '/'):
                k = {'+': 'add', '-': 'sub', '*': 'mul', '/': 'div'}[op]
                self.raise_(fr, fpflags(k, [a.e, b.e], t, self.rmode))
                return V(fop(k, [a.e, b.e], t, self.rmode), t)
            self.raise_(fr, fpflags('cmp' if op in ('==', '!=') else 'cmpe', [a.e, b.e], t))
            x, y = tofp(a.e, t), tofp(b.e, t)
            c = {'<': z3.fpLT, '>': z3.fpGT, '<=': z3.fpLEQ, '>=': z3.fpGEQ, '==': z3.fpEQ}.get(op)
            if op == '!=': return self.bool_(z3.Not(z3.fpEQ(x, y)))
            if c: return self.bool_(c(x, y))
            raise Unsupported('float op ' + op)
        s = t[2]; bw = t[1]
        if op in ('<', '>', '<=', '>=', '==', '!='):
            c = {'<': (lambda x, y: x < y) if s else z3.ULT, '>': (lambda x, y: x > y) if s else z3.UGT, '<=': (lambda x, y: x <= y) if s else z3.ULE,
                 '>=': (lambda x, y: x >= y) if s else z3.UGE, '==': lambda x, y: x == y, '!=': lambda x, y: x != y}[op]
            return self.bool_(c(a.e, b.e))
        if op in ('+', '-', '*'):
            if s:
                ex = {'+': z3.BVAddNoOverflow, '-': z3.BVSubNoUnderflow, '*': z3.BVMulNoOverflow}[op]
                ok = z3.And(ex(a.e, b.e, True), {'+': z3.BVAddNoUnderflow, '-': z3.BVSubNoOverflow, '*': z3.BVMulNoUnderflow}[op](a.e, b.e))
                self.ub.append(z3.Implies(fr.L, ok))
            return V({'+': a.e + b.e, '-': a.e - b.e, '*': a.e * b.e}[op], t)
        if op in ('/', '%'):
            self.ub.append(z3.Implies(fr.L, b.e != 0))
            if s: return V(z3.SRem(a.e, b.e) if op == '%' else a.e / b.e, t)
            return V(z3.URem(a.e, b.e) if op == '%' else z3.UDiv(a.e, b.e), t)
        if op == '&': return V(a.e & b.e, t)
        if op == '|': return V(a.e | b.e, t)
        if op == '^': return V(a.e ^ b.e, t)
        raise Unsupported('binary ' + op)

    def e_BinaryOp(self, n, fr):
        op = n.op
        if op in ('&&', '||'):
            a = self.truth(self.eval(n.left, fr), fr); save = fr.L
            fr.L = z3.And(save, a if op == '&&' else z3.Not(a))
            b = self.truth(self.eval(n.right, fr), fr); fr.L = save
            return self.bool_(z3.And(a, b) if op == '&&' else z3.Or(a, b))
        a, b = self.eval(n.left, fr), self.eval(n.right, fr)
        if isinstance(a, Ptr) and isinstance(b, V) and op in ('+', '-'):
            s = z3.simplify(b.e)
            if not z3.is_bv_value(s): raise Unsupported('non-constant pointer offset')
            d = s.as_signed_long()
            return Ptr(a.cont, a.key + (d if op == '+' else -d), a.et)
        return self.arith(op, a, b, fr)

    def e_TernaryOp(self, n, fr):
        c = self.truth(self.eval(n.cond, fr), fr); save = fr.L
        fr.L = z3.And(save, c); a = self.eval(n.iftrue, fr)
        fr.L = z3.And(save, z3.Not(c)); b = self.eval(n.iffalse, fr); fr.L = save
        if isinstance(a, V) and isinstance(b, V):
            a, b = self.usual(a, b, fr) if a.t != b.t else (a, b)
            return V(z3.If(c, a.e, b.e), a.t)
        return self.ite(c, a, b)

    def asm_call(self, k, args, fr):
        vals = [self.eval(a, fr) for a in args]
        outs, olist = ctasm.run_asm(self, fr, k, vals, sys.modules[__name__])
        for (name, a), raw in zip(olist, [a for (kd, nm, cn), a in zip(ctasm.ASMS[k]['ops'], args) if kd == 'out']):
            cont, key, _ = self.lval(raw, fr); old = cont[key]
            r = V(z3.Extract(old.e.size() - 1, 0, outs[name]), old.t)
            cont[key] = self.guard(fr, old, r)
        return V(z3.BitVecVal(0, 32), INT)

    def e_Assignment(self, n, fr):
        if isinstance(n.lvalue, c_ast.FuncCall) and isinstance(n.lvalue.name, c_ast.ID) and n.lvalue.name.name + '_st' in self.funcs and n.op == '=':      # an accessor temporary written (ctasm)
            a = [self.eval(x, fr) for x in (n.lvalue.args.exprs if n.lvalue.args else [])]
            r = self.eval(n.rvalue, fr); self.call(self.funcs[n.lvalue.name.name + '_st'], a + [r], fr); return r
        cont, key, _ = self.lval(n.lvalue, fr)
        old = cont[key]
        r = self.eval(n.rvalue, fr)
        if n.op != '=':
            op = n.op[:-1]
            r = self.arith(op, old, r, fr)
        if isinstance(old, V):
            if not isinstance(r, V): raise Unsupported('assign non-scalar to scalar')
            r = self.cast(r, old.t, fr)
        elif isinstance(old, dict): r = self.copy(r)
        cont[key] = self.guard(fr, old, r)
        return r

    def e_FuncCall(self, n, fr):
        name = n.name.name if isinstance(n.name, c_ast.ID) else None
        args = n.args.exprs if n.args else []
        if name in BUILTINS: return BUILTINS[name](self, [self.eval(a, fr) for a in args], fr, args)
        if name and name.startswith('__asm_'): return self.asm_call(int(name[6:]), args, fr)
        if name and name + '_ld' in self.funcs: return self.call(self.funcs[name + '_ld'], [self.eval(a, fr) for a in args], fr)      # an accessor temporary read (ctasm)
        if name in self.funcs:
            return self.call(self.funcs[name], [self.eval(a, fr) for a in args], fr)
        raise Unsupported('call of ' + str(name))

    # -- calls and statements
    def call(self, fd, args, caller):
        fr = Frame(fd.decl.name, caller.L)
        ft = fd.decl.type
        params = ft.args.params if ft.args else []
        rett = self.ctype(ft.type)
        if len(params) == 1 and isinstance(params[0].type, c_ast.TypeDecl) and params[0].name is None: params = []
        for p, a in zip(params, args):
            t = self.ctype(p.type)
            fr.vars[p.name] = self.cast(a, t, caller) if isinstance(a, V) and t[0] in 'if' else (a if isinstance(a, Ptr) else self.copy(a))
        fr.rett = rett
        fr.ret = self.zero(rett) if rett != ('v',) else None
        self.run(fd.body, fr)
        self.last_vars = fr.vars          # the callee's final locals (the fragment executor reads the lifter's own flag locals c0 c2 c3 zf pf cf ...)
        return fr.ret

    def run(self, n, fr):
        m = getattr(self, 's_' + type(n).__name__, None)
        if m is None: raise Unsupported('statement ' + type(n).__name__)
        m(n, fr)

    def s_Compound(self, n, fr):
        for s in (n.block_items or []): self.run(s, fr)

    def s_EmptyStatement(self, n, fr): pass

    def s_Decl(self, n, fr):
        t = self.ctype(n.type)
        if isinstance(n.type, c_ast.FuncDecl): return
        v = self.zero(t)
        if n.init is not None:
            if isinstance(n.init, c_ast.InitList):
                if t[0] != 'a' or t[1][0] not in 'if': raise Unsupported('initializer list')      # C11 6.7.9: an array of scalars, the members in order, the rest zero
                v = self.zero(t)
                for i, ie in enumerate(n.init.exprs):
                    if isinstance(ie, c_ast.InitList) or i >= len(v): raise Unsupported('initializer list')
                    v[i] = self.cast(self.eval(ie, fr), t[1], fr)
                fr.vars[n.name] = v; return
            r = self.eval(n.init, fr)
            v = self.cast(r, t, fr) if isinstance(r, V) and t[0] in 'if' else r
            if isinstance(v, dict): v = self.copy(v)
        fr.vars[n.name] = v

    def s_Assignment(self, n, fr): self.eval(n, fr)
    def s_FuncCall(self, n, fr): self.eval(n, fr)
    def s_Cast(self, n, fr): self.eval(n, fr)           # `(void)x;`: the value is dropped
    def s_UnaryOp(self, n, fr): self.eval(n, fr)
    def s_ExprList(self, n, fr): self.eval(n, fr)

    def s_If(self, n, fr):
        c = self.truth(self.eval(n.cond, fr), fr); L0 = fr.L
        fr.L = z3.And(L0, c); self.run(n.iftrue, fr); Lt = fr.L
        fr.L = z3.And(L0, z3.Not(c))
        if n.iffalse is not None: self.run(n.iffalse, fr)
        fr.L = z3.Or(Lt, fr.L)

    def s_Return(self, n, fr):
        if n.expr is not None:
            r = self.eval(n.expr, fr)
            if isinstance(r, V): r = self.cast(r, fr.rett, fr)
            else: r = self.copy(r)
            fr.ret = self.guard(fr, fr.ret, r)
        fr.L = z3.BoolVal(False)

    def s_Goto(self, n, fr):
        fr.pend[n.name] = z3.Or(fr.pend.get(n.name, z3.BoolVal(False)), fr.L); fr.L = z3.BoolVal(False)

    def s_Label(self, n, fr):
        if n.name in fr.pend: fr.L = z3.Or(fr.L, fr.pend.pop(n.name))
        self.run(n.stmt, fr)

    def s_Break(self, n, fr):
        fr.pend['__break'] = z3.Or(fr.pend.get('__break', z3.BoolVal(False)), fr.L); fr.L = z3.BoolVal(False)

    def s_Switch(self, n, fr):
        tag = self.promote(self.eval(n.cond, fr)); L0 = fr.L
        saved_break = fr.pend.pop('__break', z3.BoolVal(False))
        items = n.stmt.block_items
        labels = []
        for it in items:
            if isinstance(it, c_ast.Case): labels.append(self.eval(it.expr, fr))
        anyc = z3.Or(*[tag.e == self.cast(l, tag.t, fr).e for l in labels]) if labels else z3.BoolVal(False)
        fall = z3.BoolVal(False)
        for it in items:
            if isinstance(it, c_ast.Case):
                hit = tag.e == self.cast(self.eval(it.expr, fr), tag.t, fr).e
                body = it.stmts
            elif isinstance(it, c_ast.Default):
                hit = z3.Not(anyc); body = it.stmts
            else: raise Unsupported('switch item ' + type(it).__name__)
            fr.L = z3.Or(fall, z3.And(L0, hit))
            for s in body: self.run(s, fr)
            fall = fr.L
        fr.L = z3.Or(fall, fr.pend.pop('__break', z3.BoolVal(False)), z3.And(L0, z3.Not(z3.Or(anyc, z3.BoolVal(any(isinstance(i, c_ast.Default) for i in items))))))
        if saved_break is not None: fr.pend['__break'] = saved_break if not z3.is_false(z3.simplify(saved_break)) else fr.pend.get('__break', saved_break)

    def s_DoWhile(self, n, fr):
        # the body runs, then the condition decides; the bound must be constant (the macro idiom do{...}while(0) runs once)
        nloop = 0
        while True:
            self.run(n.stmt, fr); nloop += 1
            Lc = fr.L; fr.L = z3.BoolVal(True)
            c = z3.simplify(self.truth(self.eval(n.cond, fr), fr)); fr.L = Lc
            if not (z3.is_true(c) or z3.is_false(c)): raise Unsupported('do-while bound not constant')
            if z3.is_false(c): break
            if nloop > 4096: raise Unsupported('loop too long')

    def s_For(self, n, fr):
        # induction variable: assigned a constant in init; the bound is evaluated on constants after every step
        if n.init is None or n.cond is None: raise Unsupported('for without init/cond')
        Lsave = fr.L
        inits = n.init.decls if isinstance(n.init, c_ast.DeclList) else (n.init.exprs if isinstance(n.init, c_ast.ExprList) else [n.init])
        iv = []
        for it in inits:
            if isinstance(it, c_ast.Decl):
                self.s_Decl(it, fr); iv.append(it.name)
            elif isinstance(it, c_ast.Assignment) and isinstance(it.lvalue, c_ast.ID) and it.op == '=':
                r = self.eval(it.rvalue, fr); old = fr.vars[it.lvalue.name]
                fr.vars[it.lvalue.name] = self.cast(r, old.t, fr); iv.append(it.lvalue.name)      # unguarded: the induction variable is loop-local
            else: raise Unsupported('for init')
        nloop = 0
        step = n.next.exprs if isinstance(n.next, c_ast.ExprList) else [n.next]
        while True:
            Lc = fr.L; fr.L = z3.BoolVal(True)
            c = z3.simplify(self.truth(self.eval(n.cond, fr), fr)); fr.L = Lc
            if not (z3.is_true(c) or z3.is_false(c)): raise Unsupported('loop bound not constant')
            if z3.is_false(c): break
            nloop += 1
            if nloop > 4096: raise Unsupported('loop too long')
            Lb = fr.pend.pop('__break', None)
            self.run(n.stmt, fr)
            fr.pend['__break'] = z3.Or(Lb, fr.pend.get('__break', z3.BoolVal(False))) if Lb is not None else fr.pend.get('__break', z3.BoolVal(False))
            for s in step:
                if isinstance(s, c_ast.UnaryOp) and isinstance(s.expr, c_ast.ID) and s.expr.name in iv:
                    old = fr.vars[s.expr.name]; fr.vars[s.expr.name] = V(old.e + 1 if '+' in s.op else old.e - 1, old.t)
                else: self.eval(s, fr)
        fr.L = z3.Or(fr.L, fr.pend.pop('__break', z3.BoolVal(False))) if '__break' in fr.pend and not z3.is_false(z3.simplify(fr.pend['__break'])) else fr.L
        fr.pend.pop('__break', None)

    # -- public
    def apply(self, name, args):
        """execute function `name` on `args` (V / dict / Ptr values); -> Result(ret, outs, ub), outs = the final contents of every pointer argument"""
        self.ub = []; self.dom = []; self.fpsr, self.fpcr, self.rmode = FPSR_IN, FPCR_INIT, ARM_RMODE; self.mem = MEM_IN
        fd = self.funcs[name]
        fr = Frame('top', z3.BoolVal(True))
        ret = self.call(fd, args, fr)
        return Result(ret, None, list(self.ub), self.dom, self.fpsr, self.fpcr)

    def inputs(self, name):
        """fresh symbolic arguments for `name`: scalars and structs by value, pointers to a fresh cell; -> (args, cells)"""
        fd = self.funcs[name]; ft = fd.decl.type; args, cells = [], []
        for p in (ft.args.params if ft.args else []):
            if p.name is None: continue
            t = self.ctype(p.type)
            if t[0] == 'p':
                et = t[1]
                if et[0] == 'i' and et[1] == 8: cell = [V(z3.BitVec('%s[%d]' % (p.name, i), 8), et) for i in range(16)]
                else: cell = [self.fresh_val(et, p.name + '*')]
                cells.append((p.name, cell)); args.append(Ptr(cell, 0, et))
            else: args.append(self.fresh_val(t, p.name))
        return args, cells


# ---------------------------------------------------------------- builtins (GCC manual "Other Builtins" / "Integer Overflow Builtins")
def b_expect(m, a, fr, raw): return a[0]


def b_clzll(m, a, fr, raw):
    """__builtin_clzll(x): the number of leading 0-bits in x, starting at the most significant bit; undefined if x is 0 (GCC manual)"""
    x = a[0]; x = m.cast(x, I(64, 0), fr)
    m.ub.append(z3.Implies(fr.L, x.e != 0))
    r = z3.BitVecVal(64, 32)
    for i in range(64): r = z3.If(z3.Extract(i, i, x.e) == 1, z3.BitVecVal(63 - i, 32), r)
    return V(r, INT)


def b_unary_fp(kind):
    def f(m, a, fr, raw):
        x = m.cast(a[0], F64, fr); m.fpdom(fr)
        m.raise_(fr, fpflags(kind, [x.e], F64, m.rmode))
        return V(fop(kind, [x.e], F64, m.rmode), F64)
    return f


def b_fabs(m, a, fr, raw):
    """__builtin_fabs(x): the sign bit cleared (GCC manual "Other Builtins"); FABS (Arm ARM) raises nothing, an SNaN included"""
    x = m.cast(a[0], F64, fr)
    return V(x.e & z3.BitVecVal((1 << 63) - 1, 64), F64)


def b_nan(m, a, fr, raw):
    """__builtin_nan(""): a quiet NaN with a zero payload: 0x7FF8000000000000 (GCC manual: 'Return a quiet NaN')"""
    return V(z3.BitVecVal(0x7FF8000000000000, 64), F64)


def b_memcpy(m, a, fr, raw):
    """__builtin_memcpy(dst, src, n): little-endian byte copy between scalar cells and byte arrays (GCC manual; AArch64 and x86 are little endian)"""
    dst, src, n = a[0], a[1], z3.simplify(a[2].e).as_long()
    def bits(p):
        v = p.cont[p.key]
        if isinstance(v, V) and v.t == I(8, 0) and (p.et is None or True) and (isinstance(p.cont, list)):
            return z3.Concat(*[p.cont[p.key + j].e for j in reversed(range(n))]) if n > 1 else p.cont[p.key].e
        return v.e
    s = bits(src); d = dst.cont[dst.key]
    if isinstance(dst.cont, list) and isinstance(d, V) and d.t == I(8, 0):
        for j in range(n): dst.cont[dst.key + j] = m.guard(fr, dst.cont[dst.key + j], V(z3.Extract(8 * j + 7, 8 * j, s), I(8, 0)))
    else:
        if d.t[1] != 8 * n: raise Unsupported('memcpy size mismatch')
        dst.cont[dst.key] = m.guard(fr, d, V(s, d.t))
    return V(z3.BitVecVal(0, 32), INT)


def b_mrs(reg):
    def f(m, a, fr, raw): return V(m.fpsr if reg == 'fpsr' else m.fpcr, I(64, 0))
    return f


def b_msr(reg):
    def f(m, a, fr, raw): m.msr(fr, reg, a[0]); return V(z3.BitVecVal(0, 32), INT)
    return f


BUILTINS = {'__builtin_expect': b_expect, '__builtin_clzll': b_clzll, '__builtin_roundeven': b_unary_fp('roundeven'), '__builtin_floor': b_unary_fp('floor'),
            '__builtin_ceil': b_unary_fp('ceil'), '__builtin_trunc': b_unary_fp('trunc'), '__builtin_sqrt': b_unary_fp('sqrt'), '__builtin_nan': b_nan, '__builtin_fabs': b_fabs,
            '__builtin_memcpy': b_memcpy,
            **{'__arm_mrs_' + r: b_mrs(r) for r in SYSREGS}, **{'__arm_msr_' + r: b_msr(r) for r in SYSREGS}}


def load(ipaths, macro_files, mutate=None):
    """-> (Machine, Source, refused {name: reason}) over every function of the preprocessed units `ipaths`.  mutate {function: (old, new)} edits a function's text first (the tests'
    mutation check: an altered C body must change the term)"""
    src = Source(ipaths, macro_files)
    for fn, (old, new) in (mutate or {}).items():
        assert old in src.funcs[fn], (fn, old)
        src.funcs[fn] = src.funcs[fn].replace(old, new, 1)
    tast, funcs, refused = parse_unit(src)
    mc = Machine(tast, funcs)
    refused = dict(refused); refused.update(src.refused)
    return mc, src, refused


if __name__ == '__main__':
    mc, src, refused = load(sys.argv[1].split(':'), sys.argv[2:])
    ok, bad = [], dict(refused)
    for name in sorted(mc.funcs):
        try:
            args, _ = mc.inputs(name); mc.apply(name, args); ok.append(name)
        except (Unsupported, KeyError, RecursionError, AttributeError, TypeError, IndexError) as e:
            bad[name] = '%s: %s' % (type(e).__name__, e)
    print('translated', len(ok), 'refused', len(bad))
    for k, v in sorted(bad.items()): print(' refused', k, v)


# ---------------------------------------------------------------- the helper closure of the emission
IDENT = re.compile(r'[A-Za-z_]\w*')


def emitted_identifiers(pyfiles):
    """every identifier inside a string literal of the emitter sources (ast: Constant str and the literal parts of f-strings / % formats)"""
    import ast
    ids = set()
    for pf in pyfiles:
        for n in ast.walk(ast.parse(open(pf).read())):
            if isinstance(n, ast.Constant) and isinstance(n.value, str): ids.update(IDENT.findall(n.value))
    return ids


def closure(src, roots):
    """-> (functions, macros): the functions of the C sources reachable from the emitted identifiers: macro bodies are expanded, then the call graph over function bodies"""
    seen_m, seen_f, work = set(), set(), list(roots)
    while work:
        x = work.pop()
        if x in src.macros and x not in seen_m:
            seen_m.add(x); work.extend(IDENT.findall(src.macros[x][1]))
        if x in src.funcs and x not in seen_f:
            seen_f.add(x); work.extend(IDENT.findall(src.funcs[x]))
        if x in src.refused and x not in seen_f:
            seen_f.add(x)
    return seen_f, seen_m


def translate_all(mc, names):
    """-> (translated names, {refused name: reason}): each function executed on fresh symbolic inputs"""
    ok, bad = [], {}
    for name in sorted(names):
        try:
            args, _ = mc.inputs(name); mc.G.pop('c', None); mc.apply(name, args); ok.append(name)
        except (Unsupported, KeyError, RecursionError, AttributeError, TypeError, IndexError) as e:
            bad[name] = '%s: %s' % (type(e).__name__, e)
    return ok, bad
