"""CPUID-gated instruction forms (task C4a / B4): ONE table, ONE rule.

THE RULE (CPUID-UD). The reference CPU model advertises exactly the feature bits that rt/cpumodel.c returns (leaf 1 EDX 0x178bfbff (HTT kept for the 3-processor EBX count), ECX 0; leaf 0x80000001 EDX 0x00100000, ECX 0; no leaf 7).
An instruction whose required feature set is not a subset of those bits raises #UD, exactly like `ud2` (SDM Vol 2, "Exceptions" section of every instruction: "#UD  If CPUID feature flag
<X> is 0" / "If the LOCK prefix ..."; for the x87 FISTTP: SDM Vol 2 FISTTP, "IA-32 Architecture Compatibility: ... #UD if CPUID.01H:ECX.SSE3[bit 0] = 0"). The check is made in core.dispatch
BEFORE any handler of any family runs, so no handler (x87, SSE, MMX, integer) can implement a form that the model's own CPUID denies.

THE TABLE. Required feature of a form = the union of
  1. the feature of every capstone instruction group of the decoded form (GROUP_FEATURE: capstone's group is the assembler's "instruction set" tag of the form),
  2. MNEMONIC_FEATURE: forms that capstone leaves without an SSE3.. group although the SDM gates them (x87 FISTTP is SSE3: capstone tags it `fpu`; POPCNT).
Completeness argument: a feature is checked iff it is a key of FEATURES; every FEATURES key outside the model (MODEL) is in the "absent" set, and `missing(ins)` is non-empty exactly when a
group or mnemonic of the form maps to an absent feature. test/cpufeat_check.py proves (a) FEATURES' bits agree with the numbers that rt/cpumodel.c really returns (the C file is compiled and
called), (b) every form of the the retired one-instruction harness corpus that is implemented by an x87/SSE/MMX handler has required ⊆ MODEL or lifts to #UD, (c) tools/fpstate.py reports, per module, every instruction that `missing` flags and which handler would otherwise have run.
Assumption ASM-203: capstone's group tags are a correct classification of the instruction-set extension of a form (they come from LLVM's X86 instruction tables); where they are known to be
missing (item 2) the table adds the SDM's feature. A tag that is too wide on a feature the model HAS is harmless (the model has it); too wide on an absent feature would raise a wrong #UD:
test/cpufeat_check.py lists, over every instruction of every module, the forms whose group maps to an absent feature (the A3 table of tools/fpstate.py)."""

# feature -> (CPUID leaf, register, bit): DERIVED from the generated table (inputs 'cpufeat', L3b cpuid_feature_bits.tsv), never written here.
# Rule: a feature's gate is its row in a CPUID feature-flag leaf (1, 7, 0x80000001); where the table cites both Intel SDM and AMD APM for one feature, the Intel row decides (the declared
# machine's CPU is Intel-documented, source order of CLAUDE.md); several remaining rows that differ in register or bit, or no row, is an error at load; the same register and bit in two flag leaves counts as one row (lowest leaf) (never a silent default).
FLAG_LEAVES = (1, 7, 0x80000001)
def _load_table(want):
    from . import inputs
    rows = {}
    for l in open(inputs.get('cpufeat')):
        if not l.strip() or l[0] == '#':
            continue
        f, leaf, sub, reg, bit, cit = l.rstrip('\n').split('\t')
        if f in want and int(leaf, 16) in FLAG_LEAVES:
            rows.setdefault(f, []).append(((int(leaf, 16), reg, int(bit)), cit.startswith('Intel SDM')))
    out = {}
    for f, rs in rows.items():
        pick = [r for r, intel in rs if intel] or [r for r, intel in rs]
        if len({(reg, bit) for leaf, reg, bit in pick}) != 1:
            raise ValueError('cpufeat: feature %s has several flag rows %s' % (f, sorted(set(pick))))
        pick = [min(pick)]      # the same register and bit in leaf 1 and in 0x80000001 (AMD alias): the basic leaf is the gate, as the declared CPU answers both identically
        out[f] = pick[0]
    return out


# the model's answer, DERIVED from verify/cpuid_model.json by cpumodel_bits.model_words (the one derivation, shared with tools/gen_cpuref.py); leaf 7 is above the
# model's max basic leaf (verify/cpuid_model.json max_basic), so every leaf-7 feature is absent. No hand-copied constant: a change of the reference CPU changes the gate.
def _model_bits():
    import json, os
    from .cpumodel_bits import model_words
    here = os.path.dirname(os.path.abspath(__file__)); rel = 'ports/fnv-nx/verify/cpuid_model.json'
    path = next(p for p in (os.path.join('/in', rel), os.path.join(here, '..', '..', 'verify', 'cpuid_model.json')) if os.path.exists(p))
    w = model_words(json.load(open(path)))
    return {(1, 'EDX'): w['leaf1_edx'], (1, 'ECX'): w['leaf1_ecx'], (0x80000001, 'EDX'): w['ext1_edx'], (0x80000001, 'ECX'): w['ext1_ecx']}
MODEL_BITS = _model_bits()

# capstone group name -> feature (the table's own feature names: no translation exists)
GROUP_FEATURE = {'mmx': 'MMX', 'sse1': 'SSE', 'sse2': 'SSE2', 'sse3': 'SSE3', 'ssse3': 'SSSE3', 'sse41': 'SSE4_1', 'sse42': 'SSE4_2', 'sse4a': 'SSE4A', 'avx': 'AVX', 'avx2': 'AVX2',
                 'fma': 'FMA', 'fma4': 'FMA4', 'bmi': 'BMI1', 'bmi2': 'BMI2', 'aes': 'AESNI', 'pclmul': 'PCLMULQDQ', '3dnow': '3DNow', 'adx': 'ADX', 'sha': 'SHA', 'xop': 'XOP', 'f16c': 'F16C',
                 'rdrand': 'RDRAND', 'cmov': 'CMOV'}
# forms capstone does not tag with the extension that the SDM gates them with
MNEMONIC_FEATURE = {'fisttp': 'SSE3', 'popcnt': 'POPCNT', 'movbe': 'MOVBE'}

# FEATURES: every feature a form can need (the values of the two tables above) -> (leaf, register, bit), from the generated table; a feature without a row is an error
_T = _load_table(set(GROUP_FEATURE.values()) | set(MNEMONIC_FEATURE.values()))
_no_row = sorted((set(GROUP_FEATURE.values()) | set(MNEMONIC_FEATURE.values())) - set(_T))
if _no_row:
    raise ValueError('cpufeat: the table has no flag row for %s' % ', '.join(_no_row))
FEATURES = dict(_T)
MODEL = frozenset(f for f, (leaf, reg, bit) in FEATURES.items() if MODEL_BITS.get((leaf, reg), 0) >> bit & 1)
ABSENT = frozenset(FEATURES) - MODEL


# forms that the SDM defines as ANOTHER legal instruction on a CPU without the feature (so the absent feature is not #UD): TZCNT F3 0F BC executes as BSF and LZCNT F3 0F BD as BSR (SDM Vol 2
# TZCNT / LZCNT: "on processors that do not support ... the instruction is executed as BSF / BSR"), PREFETCH / PREFETCHW 0F 0D are reserved-NOP hints (Vol 2 Table A-6, executed as NOP by a CPU
# that lacks them). They are implemented by fnvlift/f_sys.py (classes 4 and 5), not gated here.
LEGACY_ALIAS = frozenset(('tzcnt', 'lzcnt', 'prefetch', 'prefetchw'))


def required(ins):
    """the set of features the decoded form needs"""
    mn = ins.mnemonic.split()[-1]
    if mn in LEGACY_ALIAS:
        return set()
    r = {GROUP_FEATURE[n] for n in (ins.group_name(g) for g in getattr(ins, 'groups', ())) if n in GROUP_FEATURE}
    m = MNEMONIC_FEATURE.get(mn)
    if m:
        r.add(m)
    return r


def missing(ins):
    """features the form needs that the model's CPUID denies (non-empty = #UD)"""
    return sorted(required(ins) & ABSENT)


def gates(ins):
    """-> [(feature, leaf, register, bit, value in the reference CPU)] of every feature the form needs: the evidence of `missing` (value 0 = the form is #UD)"""
    out = []
    for f in sorted(required(ins)):
        leaf, reg, bit = FEATURES[f]
        out.append((f, leaf, reg, bit, MODEL_BITS.get((leaf, reg), 0) >> bit & 1))
    return out
