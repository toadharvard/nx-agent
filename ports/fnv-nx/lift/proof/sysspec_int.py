"""The spec of INT n (shape with a symbolic vector byte, `cd<imm>/1.1.0.0`, or a concrete one) under the declared-machine record: only SDM quotes (checked by sdm_cite_check.py against sysspec_int_pages.tsv), the record and the CPU fault table.
SDM 2A:P1174  (INT n, protected mode)
  'IF gate DPL < CPL (* PE = 1, DPL < CPL, software interrupt *)'
  'THEN #GP(error_code(vector_number,1,0)); FI;'
  an INT n at a vector whose gate is not DPL 3 is a #GP with the error code vector_number * 8 + 2 (idt = 1, ext = 0); a gate that user code may use (DPL 3) enters the handler of its vector.
The record (x86sys.py through fnvlift.x86sys) decides, per vector, what the declared OS does with it, by the rows the vector falls under (CLAUDE.md, decision on the sources of the declared machine):
  int_ring3.vectors      the ring-3 gates: the fault table row whose `int_vector` is the vector (fnvlift/faults.py INT_ROWS, equal to the record's set by faults.check_int_vectors)
  int_ring3.excluded     a vector a documented gate excludes at the declared OS: its own status (undecided: no body, the vector is `uncertified: <why>`)
  int_ring3.other_vectors every other vector: the access violation at the instruction, the error code vector*8+2 of the SDM line above (the record row is read, not restated)
The shape is the 256 concrete vectors the symbolic byte stands for (a complete case split of a finite field); result `proved` iff every vector is decided and its Form is the row, `uncertified` (naming the first
undecided row, and the vectors proved) when a vector is undecided, `FAILED(dsl)` on any difference."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import z3  # noqa: E402
from fnvlift import dsl, faults, x86sys as lx  # noqa: E402
from proof import shape, symform  # noqa: E402
from sysspec_text import INT as TEXT  # noqa: E402


def claims(key):
    """the key decodes to `int imm8` (encoding 0xCD ib; INT3 0xCC and INT1 0xF1 are other mnemonics, claimed by sysspec.py)"""
    rep = symform.parse_key(key)[0]
    ins = next(iter(shape.decoder().disasm(rep, shape.ADDR)))
    return ins.mnemonic.split()[-1] == 'int' and rep[0] == 0xcd


def vectors(key):
    rep, fields = symform.parse_key(key)
    return range(256) if fields['imm'] else [rep[1]]


def row(v):
    """-> ('gate', fault code) | ('excluded', why) | ('other', fault code) for vector v, from the record and the fault table"""
    if v in faults.INT_ROWS: return 'gate', faults.BYNAME[faults.INT_ROWS[v][0]]['code']
    if v in lx.INT_EXCLUDED: return 'excluded', lx.INT_EXCLUDED[v].get('why', 'undecided')
    o = lx.INT_OTHER; av = faults.BYNAME['AV']
    ok = (o['fault'] == '#GP' and o['error_code'] == 'vector*8+2' and o['exception_code'] == int(av['status'], 16) and o['flags'] == 0 and o['eip_advance'] == 0 and o['address'] == TEXT['addr'])
    return ('other', av['code']) if ok else ('excluded', TEXT['inv'])


def form_ok(v, code):
    f = symform.symbuild('cd%02x/0.0.0.0' % v).form        # the vector as a concrete byte: a symbolic one has no Form (int_ raises SymbolicBranch)
    if f.regs or f.flags or f.stores or f.flo is not None or len(f.faults) != 1 or f.faults[0][0] != code: return False
    s = z3.Solver(); s.add(dsl.to_smt(f.faults[0][1]) != 1)
    return s.check() == z3.unsat


def check(key):
    proved, bad, undecided = 0, [], []
    for v in vectors(key):
        kind, x = row(v)
        if kind == 'excluded': undecided.append((v, x)); continue
        if form_ok(v, x): proved += 1
        else: bad.append(v)
    if bad: return 'FAILED(dsl)', TEXT['bad'] % ','.join('0x%02x' % v for v in bad[:8])
    if undecided: return 'uncertified', TEXT['unc'] % (undecided[0][0], undecided[0][1], proved, proved + len(undecided))
    return 'proved', TEXT['ok'] % proved
