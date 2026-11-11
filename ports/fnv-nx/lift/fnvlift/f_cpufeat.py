"""CPUID-gated instruction forms (task C4a / B4): ONE table, ONE rule.

THE RULE (CPUID-UD). The reference CPU model advertises exactly the feature bits that rt/cpumodel.c returns (leaf 1 EDX 0x078bfbff, ECX 0; leaf 0x80000001 EDX 0x00100000, ECX 0; no leaf 7).
An instruction whose required feature set is not a subset of those bits raises #UD, exactly like `ud2` (SDM Vol 2, "Exceptions" section of every instruction: "#UD  If CPUID feature flag
<X> is 0" / "If the LOCK prefix ..."; for the x87 FISTTP: SDM Vol 2 FISTTP, "IA-32 Architecture Compatibility: ... #UD if CPUID.01H:ECX.SSE3[bit 0] = 0"). The check is made in core.dispatch
BEFORE any handler of any family runs, so no handler (x87, SSE, MMX, integer) can implement a form that the model's own CPUID denies.

THE TABLE. Required feature of a form = the union of
  1. the feature of every capstone instruction group of the decoded form (GROUP_FEATURE: capstone's group is the assembler's "instruction set" tag of the form),
  2. MNEMONIC_FEATURE: forms that capstone leaves without an SSE3.. group although the SDM gates them (x87 FISTTP is SSE3: capstone tags it `fpu`; POPCNT).
Completeness argument: a feature is checked iff it is a key of FEATURES; every FEATURES key outside the model (MODEL) is in the "absent" set, and `missing(ins)` is non-empty exactly when a
group or mnemonic of the form maps to an absent feature. test/cpufeat_check.py proves (a) FEATURES' bits agree with the numbers that rt/cpumodel.c really returns (the C file is compiled and
called), (b) every form of the instest corpus that is implemented by an x87/SSE/MMX handler has required ⊆ MODEL or lifts to #UD, (c) tools/fpstate.py reports, per module, every instruction that `missing` flags and which handler would otherwise have run.
Assumption A-GROUPS: capstone's group tags are a correct classification of the instruction-set extension of a form (they come from LLVM's X86 instruction tables); where they are known to be
missing (item 2) the table adds the SDM's feature. A tag that is too wide on a feature the model HAS is harmless (the model has it); too wide on an absent feature would raise a wrong #UD:
test/cpufeat_check.py lists, over every instruction of every module, the forms whose group maps to an absent feature (the A3 table of tools/fpstate.py)."""

# feature -> (CPUID leaf, register, bit), SDM Vol 2 CPUID tables 3-8 / 3-10 and Vol 1 Ch.12
FEATURES = {
    'FPU': (1, 'EDX', 0), 'TSC': (1, 'EDX', 4), 'CX8': (1, 'EDX', 8), 'CMOV': (1, 'EDX', 15), 'MMX': (1, 'EDX', 23), 'FXSR': (1, 'EDX', 24), 'SSE': (1, 'EDX', 25), 'SSE2': (1, 'EDX', 26),
    'SSE3': (1, 'ECX', 0), 'PCLMULQDQ': (1, 'ECX', 1), 'SSSE3': (1, 'ECX', 9), 'FMA': (1, 'ECX', 12), 'SSE4.1': (1, 'ECX', 19), 'SSE4.2': (1, 'ECX', 20), 'MOVBE': (1, 'ECX', 22),
    'POPCNT': (1, 'ECX', 23), 'AES': (1, 'ECX', 25), 'AVX': (1, 'ECX', 28), 'F16C': (1, 'ECX', 29), 'RDRAND': (1, 'ECX', 30),
    'BMI1': (7, 'EBX', 3), 'AVX2': (7, 'EBX', 5), 'BMI2': (7, 'EBX', 8), 'ADX': (7, 'EBX', 19), 'SHA': (7, 'EBX', 29),
    '3DNOW': (0x80000001, 'EDX', 31), '3DNOWEXT': (0x80000001, 'EDX', 30), 'SSE4A': (0x80000001, 'ECX', 6), 'XOP': (0x80000001, 'ECX', 11), 'FMA4': (0x80000001, 'ECX', 16),
}
# the model's answer: the constants of rt/cpumodel.c (leaf 1 EDX/ECX, leaf 0x80000001 EDX/ECX); leaf 7 is above the model's max basic leaf, so every leaf-7 feature is absent
MODEL_BITS = {(1, 'EDX'): 0x078bfbff, (1, 'ECX'): 0x00000000, (0x80000001, 'EDX'): 0x00100000, (0x80000001, 'ECX'): 0x00000000}
MODEL = frozenset(f for f, (leaf, reg, bit) in FEATURES.items() if MODEL_BITS.get((leaf, reg), 0) >> bit & 1)
ABSENT = frozenset(FEATURES) - MODEL

# capstone group name -> feature
GROUP_FEATURE = {'mmx': 'MMX', 'sse1': 'SSE', 'sse2': 'SSE2', 'sse3': 'SSE3', 'ssse3': 'SSSE3', 'sse41': 'SSE4.1', 'sse42': 'SSE4.2', 'sse4a': 'SSE4A', 'avx': 'AVX', 'avx2': 'AVX2',
                 'fma': 'FMA', 'fma4': 'FMA4', 'bmi': 'BMI1', 'bmi2': 'BMI2', 'aes': 'AES', 'pclmul': 'PCLMULQDQ', '3dnow': '3DNOW', 'adx': 'ADX', 'sha': 'SHA', 'xop': 'XOP', 'f16c': 'F16C',
                 'rdrand': 'RDRAND', 'cmov': 'CMOV'}
# forms capstone does not tag with the extension that the SDM gates them with
MNEMONIC_FEATURE = {'fisttp': 'SSE3', 'popcnt': 'POPCNT', 'movbe': 'MOVBE'}


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
