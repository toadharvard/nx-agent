"""The declared CPU's cpuid feature words, derived ONCE from verify/cpuid_model.json (the data of the reference host and the masks that make the declared machine).
Both the runtime generator (tools/gen_cpuref.py: FNVL_CPUID1_ECX/EDX, the 0x80000001 table of rt/cpumodel.c) and the lifter's feature gate (f_cpufeat.py: the
features whose instructions are #UD) call model_words(); there is no second copy of the masks."""
I = lambda x: int(x, 0) if isinstance(x, str) else x


def model_words(mod):
    """cpuid_model.json (a dict) -> {'leaf1_ecx', 'leaf1_edx', 'ext1_ecx', 'ext1_edx'}: the answer of the declared machine."""
    return {'leaf1_ecx': I(mod['leaf1_ecx_raw']) & I(mod['leaf1_ecx_keep']),
            'leaf1_edx': I(mod['leaf1_edx_raw']) & ~I(mod['leaf1_edx_clear']) & 0xffffffff,
            'ext1_ecx': I(mod['ext1_ecx_raw']) & I(mod['ext1_ecx_keep']),
            'ext1_edx': I(mod['ext1_edx_raw']) & I(mod['ext1_edx_keep'])}
