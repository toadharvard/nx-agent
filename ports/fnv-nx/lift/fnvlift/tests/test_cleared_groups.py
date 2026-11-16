"""The CPUID-UD rule (proof/cpufeat.py, CLAUDE.md "The declared CPU"): an instruction whose capstone feature group has a clear CPUID bit on the declared CPU (verify/cpuid_model.json) is lowered to the synchronous
#UD raise at its address; an MMX/SSE/SSE2 instruction still lowers to its Form.  The group table is total over the pinned capstone's X86_GRP_* constants.
    test_cleared_groups.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
import fnvlift as L
from fnvlift.image import Image
from proof import cpufeat as F, shape
from capstone import x86_const as X

md = shape.decoder()
dec = lambda h: next(md.disasm(bytes.fromhex(h) + b'\0' * 8, 0))
miss = lambda h: F.missing(dec(h))

m = json.load(open(_inputs.CPUMODEL))                                      # totality: every capstone group is a feature row or a non-feature one, and no name is in both or unknown
consts = {k[8:] for k in vars(X) if k.startswith('X86_GRP_')}
both = set(m['capstone_groups']) | set(m['capstone_nonfeature'])
assert consts == both and not set(m['capstone_groups']) & set(m['capstone_nonfeature']), sorted(consts ^ both)

assert miss('0f58c1') == [] and miss('660f58c1') == [] and miss('90') == [] and miss('d9c0') == [] and miss('0f6fc1') == []      # addps, addpd (SSE/SSE2), nop, fld, movq mm (MMX): present
assert miss('f20f7cc1') == ['SSE3']                                                                                          # haddps: SSE3 is clear
assert miss('660f3800c1') == ['SSSE3'] and miss('0f0e') == ['3DNOW']                                                          # pshufb xmm, femms
assert miss('c5f858c1') == ['AVX'], miss('c5f858c1')                                                                          # vaddps
assert F.gates(dec('f20f7cc1')) == [('SSE3', 1, 'ECX', 0, 0)] and F.gates(dec('660f58c1')) == [('SSE2', 1, 'EDX', 26, 1)]


def lifted(h):
    img = Image.from_bytes(bytes.fromhex(h) + b'\x90\xc3', 0x401000)
    return L.lift(img, 0x401000, len(h) // 2 + 2)[0]


t = lifted('f20f7cc1')                                                      # haddps xmm0, xmm1: #UD at 0x401000, the CONTEXT of that instruction
assert 'FNVL_UD' in t, t[:600]
t2 = lifted('660f58c1')                                                     # addpd xmm0, xmm1: its Form, no #UD
assert 'FNVL_UD' not in t2, t2[:600]
print('cleared_groups: ok')
