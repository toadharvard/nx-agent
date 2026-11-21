"""The declared x86 system record (platform/declared/x86sys.json, owner: L5 platform) as the ONE source of the process's selectors, the user-callable int vectors and the facts a Form needs about the OS.
Nothing is typed here: a field with status "undecided" is absent from SEL / INT_VECTORS and is listed in UNDECIDED; a Form or lifter row that needs it is not produced (the instruction is Unsupported)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import json
from . import inputs
PATH = inputs.get('x86sys')       # explicit: the platform X86SYS output, passed by the program that runs the lifter (fnvlift/inputs.py)
REC = json.load(open(PATH))
assert REC['format'] == 'fnvnx-x86sys/2', REC['format']
SEL = {n: v['value'] for n, v in REC['selectors'].items() if 'value' in v}          # name -> selector (cs ds es ss fs tr; gs is undecided)
INT_EXCLUDED = {int(k): v for k, v in REC['int_ring3']['excluded'].items()}                # vectors a documented gate excludes at the declared OS: each carries its own status (undecided: no body)
INT_OTHER = REC['int_ring3']['other_vectors']                                          # what a user-mode `int n` at every vector that is neither a gate nor excluded does (fault, exception code, parameters, address, eip_advance)
NULL_SEG = REC['null_seg_access']                                                       # a memory operand through a null ds/es/fs/gs: fault row + privileged-opcode check (Wine 9.0 is_privileged_instr)
INT_VECTORS = tuple(REC['int_ring3']['vectors'])                                        # the ring-3 gates; every other vector is undecided (int_ring3.other_vectors)
MOV_TO_SREG = {r: (tuple(REC['mov_to_sreg'][r]['selectors']), REC['mov_to_sreg'][r]['null']) for r in ('es', 'ds', 'fs', 'gs', 'ss')}      # register -> (loadable non-null selectors, null loadable)
UNDECIDED = sorted(k for k, v in REC.items() if isinstance(v, dict) and v.get('status') == 'undecided') + ['selectors.' + n for n, v in REC['selectors'].items() if 'value' not in v] \
    + ['mov_to_sreg.' + str(s) for s in REC['mov_to_sreg']['undecided']['selectors']] + ['cr4.' + n for n, v in REC['cr4'].items() if 'value' not in v] \
    + ['int_ring3.other_vectors']
from fnvnx.lift.fnvlift import faults as _f
_f.check_int_vectors(INT_VECTORS)


# The code descriptors of the declared GDT and the data segment registers that can hold one (SDM Vol. 3A 3.4.5.1 / 5.5: a code segment is not writable, a store through it is #GP).  Derived from the record, nothing typed:
# the GDT indexes whose descriptor is S=1 with the code type bit, intersected, per register, with the selectors the record lets that register hold (its initial selector, mov_to_sreg, and for CS the far-jump targets).
CODE_INDEX = frozenset(int(k, 16) >> 3 for k, d in REC['gdt']['descriptors'].items() if d['s'] == 1 and d['type'] & 8)


def code_indices(reg):
    """the GDT indexes of a code descriptor that segment register `reg` may hold, from the record (empty: no store through `reg` can fault on its type)"""
    held = set()
    if reg in SEL: held.add(SEL[reg])
    if reg in MOV_TO_SREG: held.update(MOV_TO_SREG[reg][0])
    if reg == 'cs': held.update(REC['jmp_far']['jumps']['selectors'])
    return tuple(sorted({v >> 3 for v in held} & CODE_INDEX))
