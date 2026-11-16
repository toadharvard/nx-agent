"""Forms of the instructions whose CPUID feature the declared CPU (CPUREF, lift/rt/cpumodel.c) clears (CLAUDE.md "The declared CPU").  The one reader is proof/cpufeat.py (the SDM opcode rows and the declared CPU's words: `sdmrows` and `cpumodel` inputs);
this module only turns its answer into a Form, and forms.build asks it before any handler runs.
  UD   cpufeat.missing(ins) is non-empty: the Form is fault(UD) with no other effect, the behaviour of a CPU that lacks the feature (SDM Vol. 3 Event 6 and the instruction's `#UD If CPUID ... = 0` line;
       sysspec_cpufeat.py).  It is valid only for a site under L3a's call-graph certificate (dominated by the exe's own cpuid test); the Form does not decide reachability.
  NOP  the SDM says that the encodings of a cleared feature execute as NOP: MPX (SDM Vol. 1 E.4.1 'All Intel MPX instructions are NOP on processors that report CPUID.07H.00H:EBX.MPX[14] = 0'), the opcodes 0F 1A and
       0F 1B (every cell of Table A-3 at those opcodes is an MPX instruction: tests/opmap_cells.tsv, checked by test_sysspec_cpufeat).  The Form has no effect and reads no memory: the operand is decoded (the decoder consumes ModRM, SIB and
       displacement) and nothing else happens.  Whether MPX is cleared is the declared CPU's answer (proof/cpufeat.decide: exec_as NOP), never a constant here.
The sdmrows and cpumodel inputs (fnvlift/inputs.py) are the table; a process that does not configure it (a test of an unrelated family) gets no decision from this module: the instruction takes the ordinary path (KeyError for a mnemonic with no handler)."""
from fnvlift import inputs
from fnvlift.dsl import C

MPX_OPCODES = (0x1a, 0x1b)      # second byte after 0F: evidence tests/opmap_cells.tsv rows `two 1A *` and `two 1B *`, all bnd*


def decide(ins, raw, i):
    """-> 'ud' | 'nop' | None for the decoded instruction (raw = its bytes, i = the index of its opcode byte after the legacy prefixes)"""
    try: inputs.get('sdmrows'); inputs.get('cpumodel')
    except RuntimeError: return None
    from proof import cpufeat, shape
    real = next(iter(shape.decoder().disasm(bytes(ins.bytes), 0)))      # the decoder's own instruction: a symbolic shape instruction (symform) carries no capstone groups; the feature depends on the opcode bytes only
    if cpufeat.missing(real): return 'ud'
    if raw[i] == 0x0f and i + 1 < len(raw) and raw[i + 1] in MPX_OPCODES and 'NOP' in cpufeat.decide(real)['exec_as'].split(','): return 'nop'
    return None
