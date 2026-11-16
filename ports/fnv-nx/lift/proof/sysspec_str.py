"""The SDM spec of STR (memory form) under the declared-machine record: only SDM quotes (checked by sdm_cite_check.py), code and short result codes, no message text.
SDM 2B:STR  Operation (the pinned edition):
  'DEST := TR(SegmentSelector);'
  'When the destination operand is a memory location, the segment selector is written to memory as a 16-bit quantity, regardless of operand size.'
its #GP(0) on 'If CR4.UMIP = 1 and CPL > 0.' is the COND row of sdm_priv_rows.tsv; with the record's cr4.umip = 0 the instruction has its ordinary effect, this store.
Result: 'ok', or the code of the first difference (effects, nstores, width, value)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import z3  # noqa: E402
from fnvlift import dsl  # noqa: E402
from proof import symform, x86sys  # noqa: E402


def check(key, rec):
    f = symform.symbuild(key).form
    if f.regs or f.flags or f.flo is not None or f.faults: return 'effects'
    if len(f.stores) != 1: return 'nstores'
    v = dsl.to_smt(f.stores[0][1])
    if v.size() != 16: return 'width'
    s = z3.Solver(); s.add(v != z3.BitVecVal(x86sys.get(rec, 'selectors', 'tr'), 16))
    return 'ok' if s.check() == z3.unsat else 'value'
