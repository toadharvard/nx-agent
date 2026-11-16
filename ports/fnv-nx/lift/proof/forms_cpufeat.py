"""Forms of the instructions whose CPUID feature the declared CPU (CPUREF, lift/rt/cpumodel.c) clears (CLAUDE.md "The declared CPU").  The one reader is proof/cpufeat.py (the SDM opcode rows and the declared CPU's words: `sdmrows` and `cpumodel` inputs);
this module only turns its answer into a Form, and forms.build asks it before any handler runs.
  UD   cpufeat.missing(ins) is non-empty: the Form is fault(UD) with no other effect, the behaviour of a CPU that lacks the feature (SDM Vol. 3 Event 6 and the instruction's `#UD If CPUID ... = 0` line).  It is valid only for a site under L3a's call-graph certificate (dominated by the exe's own cpuid test); the Form does not decide reachability.
  NOP  the SDM says that the encodings of a cleared feature execute as NOP: MPX (SDM Vol. 1 E.4.1 'All Intel MPX instructions are NOP on processors that report CPUID.07H.00H:EBX.MPX[14] = 0'), the opcodes 0F 1A and
       0F 1B (every cell of Table A-3 at those opcodes is an MPX instruction: tests/opmap_cells.tsv).  The Form has no effect and reads no memory: the operand is decoded (the decoder consumes ModRM, SIB and
       displacement) and nothing else happens.  Whether MPX is cleared is the declared CPU's answer (the record's `exec_as_nop`, read by proof/cpufeat.nop), never a constant here.
The cpumodel input (fnvlift/inputs.py: the declared CPU record) is the table; a process that does not configure it (a test of an unrelated family) gets no decision from this module: the instruction takes the ordinary path (KeyError for a mnemonic with no handler)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
from fnvnx.lift.fnvlift import inputs
from fnvnx.lift.fnvlift.dsl import C



def decide(ins, raw, i):
    """-> 'ud' | 'nop' | None for the decoded instruction (raw = its bytes, i = the index of its opcode byte after the legacy prefixes)"""
    try: inputs.get('cpumodel')
    except RuntimeError: return None
    from fnvnx.lift.proof import cpufeat, shape
    real = next(iter(shape.decoder().disasm(bytes(ins.bytes), 0)))      # the decoder's own instruction: a symbolic shape instruction (symform) carries no capstone groups; the feature depends on the opcode bytes only
    if cpufeat.missing(real): return 'ud'
    if cpufeat.nop(raw, i): return 'nop'
    return None
