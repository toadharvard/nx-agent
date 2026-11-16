"""cpufeat on the SDM rows (--sdmrows: sdmfeat.py dumprows of the SDM text): the gate is the one SDM row match, no capstone groups.
    test_cpufeat_sdm.py (the input SDMROWS is mounted)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; import tempfile
_u = tempfile.mkdtemp() + '/udsail.tsv'; open(_u, 'w').write('# fnvnx-udsail/2\n0f0e\tUD\t2\tsail\tfixture\n')
_inputs.configure(_inputs.sdmrows_arg(), udsail=_u)
from proof import cpufeat as F
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
assert F.missing(dec('0f0e')) == ['Sail #UD (sail)'] and F.decide(dec('0f0e'))['status'] == 'sail'                           # femms: no SDM row, a Sail #UD verdict
from fnvlift.core import Unsupported
try: F.missing(dec('f6c8aa')); raise SystemExit('f6 /1 must not be #UD without a Sail verdict')
except Unsupported: pass                                                                                                       # test /1 (no SDM row): no Sail #UD verdict -> red, never #UD
assert F.gates(dec('f20f7cc1')) == [('SSE3', 1, 'ECX', 0, 0)], F.gates(dec('f20f7cc1'))
assert F.gates(dec('660f58c1')) == [('SSE2', 1, 'EDX', 26, 1)]
print('cpufeat_sdm: ok')
