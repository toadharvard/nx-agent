"""The declared CPU's cpuid feature words, derived ONCE from verify/cpuid_model.json (the data of the reference host and the masks that make the declared machine).
Both the runtime generator (tools/gen_cpuref.py: FNVL_CPUID1_ECX/EDX, the 0x80000001 table of rt/cpumodel.c) and the lifter's feature gate (cpufeat.py: the
features whose instructions are #UD) call model_words(); there is no second copy of the masks."""
I = lambda x: int(x, 0) if isinstance(x, str) else x


def model_words(mod):
    """cpuid_model.json (a dict) -> {'leaf1_ecx', 'leaf1_edx', 'ext1_ecx', 'ext1_edx'}: the answer of the declared machine."""
    return {'leaf1_ecx': I(mod['leaf1_ecx_raw']) & I(mod['leaf1_ecx_keep']),
            'leaf1_edx': I(mod['leaf1_edx_raw']) & ~I(mod['leaf1_edx_clear']) & 0xffffffff,
            'ext1_ecx': I(mod['ext1_ecx_raw']) & I(mod['ext1_ecx_keep']),
            'ext1_edx': I(mod['ext1_edx_raw']) & I(mod['ext1_edx_keep'])}


def declared_cpu(mod):
    """cpuid_model.json (a dict) -> {(leaf, reg): word, (0, 'EAX'): max basic leaf, (0x80000000, 'EAX'): max extended leaf}: the declared CPU's words of every field a feature flag can sit in (leaf 1 ECX/EDX, 0x80000001 ECX/EDX).
    Fields the platform owns (leaf 1 EBX, leaf 4 EAX) are not features.  The one reader of the record's words: cpufeat.py, the sail model's dispatch script (sail_patches/bump/bsf_bsr_dispatch.py) and gen_cpuref.py go through model_words"""
    w = model_words(mod)
    return {(0, 'EAX'): I(mod['max_basic']), (0x80000000, 'EAX'): I(mod['max_ext']), (1, 'EDX'): w['leaf1_edx'], (1, 'ECX'): w['leaf1_ecx'], (0x80000001, 'EDX'): w['ext1_edx'], (0x80000001, 'ECX'): w['ext1_ecx']}


def bit(cpu, leaf, reg, b):
    """the declared value (0/1) of CPUID bit LEAF.REG[B] in declared_cpu() CPU; a leaf above the declared max of its range (basic / extended) is all zero"""
    top = cpu[(0, 'EAX')] if leaf < 0x80000000 else cpu[(0x80000000, 'EAX')]
    return (cpu.get((leaf, reg), 0) >> b) & 1 if leaf <= top else 0


def exec_as(mod):
    """{feature: row} of the record's `exec_as`: the encodings that a CPU WITHOUT the feature executes as another instruction (SDM, cited per row).  A record row is [leaf, reg, bit, as, prefix, [op...], cite]: the feature's CPUID bit,
    `as` ('nop' or the mnemonic it executes as), the mandatory prefix (hex, '' = any), the opcode bytes after the legacy prefixes (hex, one string per encoding), the SDM text.  Returned as a dict with those names, leaf normalised to int"""
    return {n: dict(zip(('leaf', 'reg', 'bit', 'as', 'prefix', 'op', 'cite'), (I(r[0]),) + tuple(r[1:]))) for n, r in mod['exec_as'].items()}
