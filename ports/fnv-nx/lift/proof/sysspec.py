#!/usr/bin/env python3
"""sysspec: the SDM semantics of the system classes (a) and (b), under the declared-machine facts of declared.json, compared with the DSL Forms (z3).
    python3 -I sysspec.py [SHAPES.tsv]        (default: every shape of the pack's system family; one `shape` per line, first column)
Class (a): privileged instructions executed at CPL 3 (SDM Vol. 2 per instruction, `Protected Mode Exceptions`: #GP(0) if the CPL is not 0, resp. for CLI/STI/IN/OUT/INS/OUTS CPL > IOPL and no I/O-bitmap grant):
  with the declared facts cpl = 3, iopl = 0, io_bitmap_grants = none the condition holds in every pre-state, the instruction changes no register, flag or memory (the fault leaves the pre-state; the Form fault contract: eip = the instruction), and
  the exception is #GP(0), which the process sees as FNVL_PRIV or FNVL_AV (both kinds of #GP(0); the choice per opcode is f_sys.trap0d_privileged, the code per kind is checked by lift/tools/tests/sehcodes.py ROWS: not repeated here).
Class (b): INT3 (SDM `INT3`: #BP, vector 3), INT1 (`INT1`: #DB, vector 1) and INTO (`INTO`: #OF, vector 4, only when EFLAGS.OF = 1; no exception otherwise, the instruction is a no-op), no other effect.
Not covered here (reported, not guessed): `int n` for the other vectors (needs the declared IDT gate DPLs with their Wine 9.0 source), class (c) (mov to a segment register: the declared selector table).
Output: one line per shape `shape<TAB>mnemonic<TAB>result<TAB>note`; exit 1 if any shape fails."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import privset, shape, symform  # noqa: E402
from fnvlift import dsl  # noqa: E402

K_BP, K_UD, K_BOUNDS, K_INTO, K_PRIV, K_AV, K_STEP = 2, 3, 4, 5, 6, 7, 8

def facts():
    with open(os.path.join(HERE, 'declared.json')) as fh:
        return {f['name']: f['value'] for f in json.load(fh)['facts']}


def umip_clear():
    from proof import sdmfeat
    c = sdmfeat.declared_cpu()
    return not (c[(0, 'EAX')] >= 7 and (c[(7, 'ECX')] >> 2) & 1)


# mnemonic -> (class, SDM citation, sentence): the privileged set read from the SDM rows (sdmpriv.py via privset.py)
PRIV = privset.citations()
CLASS_B = {'int3': (K_BP, None), 'int1': (K_STEP, None), 'into': (K_INTO, 'of')}


def mnemonic(key):
    ins = next(iter(shape.decoder().disasm(shape.rep_bytes(key), shape.ADDR)))
    return ins.mnemonic.split()[-1]


def check(key):
    import z3
    f = symform.symbuild(key).form; m = mnemonic(key)
    if m not in PRIV and m not in CLASS_B:
        return 'not-covered', 'no spec for %s' % m
    if f.regs or f.flags or f.stores or f.flo is not None:
        return 'FAILED(dsl)', 'a class (a)/(b) instruction changes state'
    ft = [(k, c) for k, c in f.faults]
    fa = facts()
    if m in PRIV:
        if not (fa['cpl'] == 3 and fa['iopl'] == 0 and fa['io_bitmap_grants'] == 'none'):
            return 'FAILED(facts)', 'declared facts changed'
        if len(ft) != 1 or ft[0][0] not in (K_PRIV, K_AV):
            return 'FAILED(dsl)', 'expected one #GP(0) fault (PRIV or AV), got %s' % [k for k, _ in ft]
        s = z3.Solver(); s.add(dsl.to_smt(ft[0][1]) != 1)
        return ('proved', 'privileged at CPL 3: #GP(0) in every state') if s.check() == z3.unsat else ('FAILED(dsl)', 'the fault is conditional')
    if m in CLASS_B:
        kind, flag = CLASS_B[m]
        if len(ft) != 1 or ft[0][0] != kind:
            return 'FAILED(dsl)', 'expected fault kind %d, got %s' % (kind, [k for k, _ in ft])
        s = z3.Solver(); c = dsl.to_smt(ft[0][1])
        s.add(c != (z3.BitVec(flag, 1) if flag else z3.BitVecVal(1, 1)))
        return ('proved', 'SDM %s' % m.upper()) if s.check() == z3.unsat else ('FAILED(dsl)', 'fault condition differs from the SDM condition')
    return 'not-covered', 'no spec for %s' % m


def main(argv):
    if not umip_clear():
        print('umip is set on the declared CPU: UMIP semantics are not specified'); return 2
    keys = [ln.split()[0] for ln in open(argv[0]) if ln.strip() and ln[0] != '#'] if argv else []
    bad = 0
    for k in keys:
        r, n = check(k); bad += r.startswith('FAILED')
        print('%s\t%s\t%s\t%s' % (k, mnemonic(k), r, n))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
