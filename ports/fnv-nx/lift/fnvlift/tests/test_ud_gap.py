"""f_sys.decode_gap / ud_gap, consumed by core.decode and core.dispatch: a byte sequence capstone refuses is lifted as a #UD site exactly when a row of the `udsail` input (proof/udsail.py output) is a prefix of it;
the lifter never decides it.  The rows here are an input fixture (the verdicts of the pinned Sail model come from the farm run of proof/udsail.py).
    test_ud_gap.py --sdmrows PATH"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; SDMROWS = _inputs.sdmrows_arg(); _inputs.configure(SDMROWS)
import fnvlift as L
from fnvlift import f_sys, inputs
from fnvlift.core import Unsupported
from fnvlift.image import Image

d = tempfile.mkdtemp(prefix='udgap-')
open(d + '/udsail.tsv', 'w').write('# fnvnx-udsail/2\nc7d8\tUD\t2\tsail\tfixture\n8fe0\tUD\t2\tfeature\tfixture\n')
open(d + '/universe.tsv', 'w').close(); open(d + '/depth.tsv', 'w').close()
inputs.reset()
inputs.configure(universe=d + '/universe.tsv', fpstate=d, fpdepth=d + '/depth.tsv', x86sys=_inputs.X86SYS, sdmrows=SDMROWS, cpumodel=_inputs.CPUMODEL, udsail=d + '/udsail.tsv', segwrites=d + '/none.json', image='buf')

assert f_sys.ud_gap(bytes.fromhex('c7d8112233445566')).size == 2           # the row is the decode extent: bytes after it are not part of the instruction
assert f_sys.ud_gap(bytes.fromhex('c7c8')) is None                        # c7 /1: no row: undecided
assert f_sys.ud_gap(bytes.fromhex('66c7d8')) is None                      # a row is a prefix of the sequence (legacy prefixes are part of the decided bytes): this one is not
assert f_sys.decode_gap(bytes.fromhex('0f1f00')) is not None and f_sys.decode_gap(bytes.fromhex('0f1f00')).mnemonic == 'nop'      # reserved NOP is still nop_gap's
ins = f_sys.decode_gap(bytes.fromhex('8fe0aabb'))
assert ins.mnemonic == '(ud)' and ins.size == 2 and ins.operands == () and ins.ud_class == 'feature'
img = Image.from_bytes(bytes.fromhex('c7d8') + b'\x90\xc3', 0x401000)
t = L.lift(img, 0x401000, 4)[0]
assert 'FNVL_UD' in t, t[:400]
print('ok')
