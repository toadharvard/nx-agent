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
    """cpuid_model.json (a dict) -> {(leaf, reg): word, (0, 'EAX'): max basic leaf, (0x80000000, 'EAX'): max extended leaf}: the declared CPU's words of every field a feature flag can sit in (leaf 1 ECX/EDX, 0x80000001 ECX/EDX); fields the platform owns are not features.
    The one derivation: proof/cpufeat.py and the sail model's dispatch splice (sail_patches/bump/bsf_bsr_dispatch.py) both call it"""
    w = model_words(mod)
    return {(0, 'EAX'): I(mod['max_basic']), (0x80000000, 'EAX'): I(mod['max_ext']), (1, 'EDX'): w['leaf1_edx'], (1, 'ECX'): w['leaf1_ecx'], (0x80000001, 'EDX'): w['ext1_edx'], (0x80000001, 'ECX'): w['ext1_ecx']}
