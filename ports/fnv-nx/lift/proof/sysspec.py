#!/usr/bin/env python3
"""sysspec: the SDM semantics of the system classes (a) and (b), under the declared-machine record platform/declared/x86sys.json (read by x86sys.py; no value of it is held here), compared with the DSL Forms (z3).
    python3 -I sysspec.py [SHAPES.tsv]        (default: every shape of the pack's system family; one `shape` per line, first column)
Class (a): privileged instructions executed at CPL 3 (SDM Vol. 2 per instruction, `Protected Mode Exceptions`: #GP(0) if the CPL is not 0, resp. for CLI/STI/IN/OUT/INS/OUTS CPL > IOPL and no I/O-bitmap grant):
  with the record's cpl (the RPL of its cs selector) and iopl the condition holds in every pre-state, the instruction changes no register, flag or memory (the fault leaves the pre-state; the Form fault contract: eip = the instruction), and
  the exception is #GP(0), which the process sees as FNVL_PRIV or FNVL_AV (both kinds of #GP(0); the choice per opcode is f_sys.trap0d_privileged, the code per kind is checked by lift/tools/tests/sehcodes.py ROWS: not repeated here).
Class (b): INT3 (SDM `INT3`: #BP, vector 3), INT1 (`INT1`: #DB, vector 1) and INTO (`INTO`: #OF, vector 4, only when EFLAGS.OF = 1; no exception otherwise, the instruction is a no-op), no other effect.
Shapes that depend on a row the record does not decide (`int n`: other_vectors; mov to a segment register; sgdt/sidt/sldt; IN/OUT: the TSS I/O bitmap; RDPMC: cr4.pce) are `uncertified: <the row's why>`, with no default.
Output: one line per shape `shape<TAB>mnemonic<TAB>result<TAB>note`; exit 1 if any shape fails."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import shape, symform, x86sys  # noqa: E402
from fnvlift import dsl  # noqa: E402

K_BP, K_UD, K_BOUNDS, K_INTO, K_PRIV, K_AV, K_STEP = 2, 3, 4, 5, 6, 7, 8

# mnemonic -> (SDM page, #GP(0) at CPL 3 under the declared facts?)
PRIV = {m: 'SDM Vol. 2 %s: Protected Mode Exceptions: #GP(0) if the CPL is greater than %s' % (m.upper(), 'IOPL (CLI/STI) / and no I/O bitmap grant (IN/OUT/INS/OUTS)' if m in ('cli', 'sti', 'in', 'out', 'insb', 'insw', 'insd', 'outsb', 'outsw', 'outsd') else '0')
        for m in ('cli', 'sti', 'hlt', 'in', 'out', 'insb', 'insw', 'insd', 'outsb', 'outsw', 'outsd', 'lldt', 'ltr', 'lgdt', 'lidt', 'rdmsr', 'wrmsr', 'invd', 'wbinvd', 'clts', 'lmsw', 'invlpg', 'rdpmc')}
CLASS_B = {'int3': (K_BP, None), 'int1': (K_STEP, None), 'into': (K_INTO, 'of')}


def mnemonic(key):
    ins = next(iter(shape.decoder().disasm(shape.rep_bytes(key), shape.ADDR)))
    return ins.mnemonic.split()[-1]


SREGS = ('es', 'cs', 'ss', 'ds', 'fs', 'gs')
IO = ('in', 'out', 'insb', 'insw', 'insd', 'outsb', 'outsw', 'outsd')


def sreg_load(ins):
    """an instruction that loads a segment register: mov/pop to one, lds/les/lss/lfs/lgs (SDM `MOV`, `POP`, `LDS/LES/LFS/LGS/LSS`)"""
    m = ins.mnemonic.split()[-1]
    return m in ('lds', 'les', 'lss', 'lfs', 'lgs') or (m in ('mov', 'pop') and ins.op_str.split(',')[0].strip() in SREGS)


def needs(key, rec):
    """the rows of the declared-machine record that the SDM semantics of the shape depends on -> None when all are decided, else the blocker ("record has no row ...", the row's `why`)"""
    ins = next(iter(shape.decoder().disasm(shape.rep_bytes(key), shape.ADDR))); m = ins.mnemonic.split()[-1]
    rows = []
    if m in PRIV:
        rows.append(('selectors', 'cs'))                                  # CPL = the RPL of cs
        if m in ('cli', 'sti') or m in IO:
            rows.append(('eflags', 'iopl'))                              # SDM: #GP(0) if CPL > IOPL
        if m in IO:
            rows.append(('tss_io_bitmap',))                              # SDM IN/OUT/INS/OUTS: then the I/O permission bitmap of the TSS decides
        if m == 'rdpmc':
            rows.append(('cr4', 'pce'))                                  # SDM RDPMC: #GP(0) if CR4.PCE = 0 and CPL > 0
    elif m == 'int':
        rows += [('int_ring3', 'vectors'), ('int_ring3', 'other_vectors')]
    elif sreg_load(ins):
        rows += [('mov_to_sreg', 'undecided')]
    elif m == 'sgdt':
        rows.append(('sgdt_base',))
    elif m == 'sidt':
        rows += [('sidt_base',), ('idtr_limit',)]
    elif m == 'sldt':
        rows.append(('sldt',))
    elif m == 'str':
        rows.append(('selectors', 'tr'))
    for r in rows:
        try:
            if r == ('mov_to_sreg', 'undecided'):
                x = rec.get('mov_to_sreg', {}).get('undecided')
                if x is not None:
                    return 'mov_to_sreg.undecided: %s' % x.get('why', 'undecided')
                x86sys.get(rec, 'mov_to_sreg')
            else:
                x86sys.get(rec, *r)
        except x86sys.Undecided as e:
            return str(e)
    return None


def check(key):
    import z3
    m = mnemonic(key)
    if m not in PRIV and m not in CLASS_B and not needs_any(m):
        return 'not-covered', 'no spec for %s' % m
    if m in CLASS_B:
        return class_b(symform.symbuild(key).form, m)                                              # no row of the record is involved: the SDM condition alone
    try:
        rec = x86sys.load()
    except x86sys.Undecided as e:
        return 'uncertified', 'uncertified: %s' % e
    why = needs(key, rec)
    if why:
        return 'uncertified', 'uncertified: %s' % why
    if x86sys.get(rec, 'cr4', 'umip') != 0:
        return 'FAILED(facts)', 'cr4.umip is set on the declared machine: UMIP semantics are not specified'
    if m not in PRIV and m not in CLASS_B:
        return 'not-covered', 'no spec for %s (its record rows are decided)' % m
    f = symform.symbuild(key).form
    if f.regs or f.flags or f.stores or f.flo is not None:
        return 'FAILED(dsl)', 'a class (a)/(b) instruction changes state'
    ft = [(k, c) for k, c in f.faults]
    if m in PRIV:
        if not (x86sys.cpl(rec) == 3 and x86sys.get(rec, 'eflags', 'iopl') == 0) or m in IO or m == 'rdpmc':
            return 'not-covered', 'no spec for %s under the record' % m
        if len(ft) != 1 or ft[0][0] not in (K_PRIV, K_AV):
            return 'FAILED(dsl)', 'expected one #GP(0) fault (PRIV or AV), got %s' % [k for k, _ in ft]
        s = z3.Solver(); s.add(dsl.to_smt(ft[0][1]) != 1)
        return ('proved', 'privileged at CPL 3: #GP(0) in every state') if s.check() == z3.unsat else ('FAILED(dsl)', 'the fault is conditional')
    return 'not-covered', 'no spec for %s' % m


def class_b(f, m):
    import z3
    ft = [(k, c) for k, c in f.faults]
    if f.regs or f.flags or f.stores or f.flo is not None:
        return 'FAILED(dsl)', 'a class (b) instruction changes state'
    kind, flag = CLASS_B[m]
    if len(ft) != 1 or ft[0][0] != kind:
        return 'FAILED(dsl)', 'expected fault kind %d, got %s' % (kind, [k for k, _ in ft])
    s = z3.Solver(); c = dsl.to_smt(ft[0][1])
    s.add(c != (z3.BitVec(flag, 1) if flag else z3.BitVecVal(1, 1)))
    return ('proved', 'SDM %s' % m.upper()) if s.check() == z3.unsat else ('FAILED(dsl)', 'fault condition differs from the SDM condition')


def needs_any(m):
    return m in ('int', 'sgdt', 'sidt', 'sldt', 'str', 'lds', 'les', 'lss', 'lfs', 'lgs', 'mov', 'pop')


def main(argv):
    keys = [ln.split()[0] for ln in open(argv[0]) if ln.strip() and ln[0] != '#'] if argv else []
    bad = 0
    for k in keys:
        r, n = check(k); bad += r.startswith('FAILED')
        print('%s\t%s\t%s\t%s' % (k, mnemonic(k), r, n))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
