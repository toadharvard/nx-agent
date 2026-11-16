"""f_cpufeat on the SDM rows (--sdmrows: sdmfeat.py dumprows of the SDM text): the gate is the one SDM row match, no capstone groups.
    test_cpufeat_sdm.py --sdmrows PATH"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure(_inputs.sdmrows_arg())
from fnvlift import f_cpufeat as F
from proof import shape, sdmfeat
md = shape.decoder()
dec = lambda h: sdmfeat.decode(bytes.fromhex(h) + b'\0' * 8, md)
miss = lambda h: F.missing(dec(h))
assert not hasattr(F, 'GROUP_FEATURE') and not hasattr(F, 'MNEMONIC_FEATURE')           # one source: the capstone maps are gone
assert miss('0f58c1') == [] and miss('660f58c1') == [] and miss('90') == [] and miss('d9c0') == [] and miss('0f6fc1') == []        # addps, addpd (SSE/SSE2), nop, fld, movq mm (MMX): present
assert miss('f20f7cc1') == ['SSE3'], miss('f20f7cc1')                                                                          # haddps: SSE3 absent on the declared CPU
assert miss('df08') == ['SSE3'], miss('df08')                                                                                  # fisttp: the SDM's FISTTP row (no capstone group needed)
assert miss('660f3800c1') == ['SSSE3'] and miss('0f3800c1') == ['SSSE3']                                                      # pshufb xmm / mm
assert miss('c5f858c1') and miss('f30fbcc1') == [] and miss('f30fbdc1') == [], miss('f30fbcc1')                               # vaddps: absent; tzcnt/lzcnt: SDM `executed as` BSF/BSR, not #UD
assert miss('0f1808') == []                                                                                                   # prefetcht0: an SDM row without a flag
assert F.missing(dec('0f0e')) == ['undefined opcode'] and F.decide(dec('0f0e'))['status'] == 'undefined'               # femms (3DNow!, AMD): no SDM row, undefined opcode
assert F.gates(dec('f20f7cc1')) == [('SSE3', 1, 'ECX', 0, 0)], F.gates(dec('f20f7cc1'))
assert F.gates(dec('660f58c1')) == [('SSE2', 1, 'EDX', 26, 1)]
print('cpufeat_sdm: ok')
