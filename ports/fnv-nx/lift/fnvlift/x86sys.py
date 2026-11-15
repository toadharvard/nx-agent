"""The declared x86 system record (platform/declared/x86sys.json, owner: L5 platform) as the ONE source of the process's selectors, the user-callable int vectors and the facts a Form needs about the OS.
Nothing is typed here: a field with status "undecided" is absent from SEL / INT_VECTORS and is listed in UNDECIDED; a Form or lifter row that needs it is not produced (the instruction is Unsupported)."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get('FNVNX_X86SYS') or os.path.join(HERE, '..', '..', 'platform', 'declared', 'x86sys.json')
REC = json.load(open(PATH))
assert REC['format'] == 'fnvnx-x86sys/2', REC['format']
SEL = {n: v['value'] for n, v in REC['selectors'].items() if 'value' in v}          # name -> selector (cs ds es ss fs tr; gs is undecided)
INT_VECTORS = tuple(REC['int_ring3']['vectors'])                                        # the ring-3 gates; every other vector is undecided (int_ring3.other_vectors)
MOV_TO_SREG = {r: (tuple(REC['mov_to_sreg'][r]['selectors']), REC['mov_to_sreg'][r]['null']) for r in ('es', 'ds', 'fs', 'gs', 'ss')}      # register -> (loadable non-null selectors, null loadable)
UNDECIDED = sorted(k for k, v in REC.items() if isinstance(v, dict) and v.get('status') == 'undecided') + ['selectors.' + n for n, v in REC['selectors'].items() if 'value' not in v] \
    + ['mov_to_sreg.' + str(s) for s in REC['mov_to_sreg']['undecided']['selectors']] + ['cr4.' + n for n, v in REC['cr4'].items() if 'value' not in v] \
    + ['int_ring3.other_vectors']
from fnvlift import faults as _f
_f.check_int_vectors(INT_VECTORS)
