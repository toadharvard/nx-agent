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
from proof import privset, shape, symform, x86sys  # noqa: E402
sys.path.insert(0, HERE)
from fnvlift import dsl  # noqa: E402
import sysspec_far  # noqa: E402

K_BP, K_UD, K_BOUNDS, K_INTO, K_PRIV, K_AV, K_STEP = 2, 3, 4, 5, 6, 7, 8

# mnemonic -> (class, SDM citation, sentence): the privileged set read from the SDM rows (sdmpriv.py via privset.py)
PRIV = privset.citations()
CONDROW = {'rdpmc': ('cr4', 'pce'), 'sgdt': ('cr4', 'umip'), 'sidt': ('cr4', 'umip'), 'sldt': ('cr4', 'umip'), 'smsw': ('cr4', 'umip'), 'str': ('cr4', 'umip')}      # the record row that decides the #GP(0) condition of a `cond` SDM row
COND = {m for r in privset.rows() if r['class'] == 'cond' for m in r['mnemonics'] if m in CONDROW}      # a #GP(0) that needs another condition (the SDM sentence names CR4.PCE / CR4.UMIP): its row of the record decides
DECL = ('rdtsc', 'cpuid')      # instructions whose result is the declared machine's function (rt/cpumodel.c, declared.json): the SDM Operation line of the row's basis gives the registers
FAULTROW = {'rdtsc': ('cr4', 'tsd'), 'cpuid': ('cpuid_faulting',)}      # the record row that decides whether the instruction can fault at CPL 3 (SDM RDTSC: CR4.TSD; SDM CPUID: CPUID faulting, MSR_PLATFORM_INFO / MSR_MISC_FEATURES_ENABLES)
LARFAM = ('lar', 'lsl', 'verr', 'verw')      # the result depends on the descriptor of the selector in the register: the record's lar_lsl rows
CERTS = None      # the proved shape keys of a certs.tsv (set by main --certs): the basis of a composed spec must be among them
CLASS_B = {'int3': (K_BP, None), 'int1': (K_STEP, None), 'into': (K_INTO, 'of')}


def mnemonic(key):
    ins = next(iter(shape.decoder().disasm(shape.rep_bytes(key), shape.ADDR)))
    return ins.mnemonic.split()[-1]


SREGS = ('es', 'cs', 'ss', 'ds', 'fs', 'gs')
IO = {m for r in privset.rows() if r['class'] == 'iopl' and 'I/O permission' in r['sentence'] for m in r['mnemonics']}      # the SDM sentence names the TSS I/O permission bitmap: IN/INS/OUT/OUTS


def sreg_load(ins):
    """an instruction that loads a segment register: mov/pop to one, lds/les/lss/lfs/lgs (SDM `MOV`, `POP`, `LDS/LES/LFS/LGS/LSS`)"""
    m = ins.mnemonic.split()[-1]
    return m in ('lds', 'les', 'lss', 'lfs', 'lgs') or (m in ('mov', 'pop') and ins.op_str.split(',')[0].strip() in SREGS)


def cond_fires(m, rec):
    """the #GP(0) condition of a `cond` row holds in every state: RDPMC faults at CPL 3 when CR4.PCE is clear, SGDT/SIDT/SLDT/SMSW/STR when CR4.UMIP is set (SDM sentences of sdm_priv_rows.tsv)"""
    return x86sys.get(rec, *CONDROW[m]) == (0 if m == 'rdpmc' else 1)


def needs(key, rec):
    """the rows of the declared-machine record that the SDM semantics of the shape depends on -> None when all are decided, else the blocker ("record has no row ...", the row's `why`)"""
    ins = next(iter(shape.decoder().disasm(shape.rep_bytes(key), shape.ADDR))); m = ins.mnemonic.split()[-1]
    rows = []
    if m in COND:
        rows.append(CONDROW[m])
    if m in PRIV or m in COND:
        rows.append(('selectors', 'cs'))                                  # CPL = the RPL of cs
        if m in PRIV and PRIV[m][0] == 'iopl':
            rows.append(('eflags', 'iopl'))                              # SDM: #GP(0) if CPL > IOPL
        if m in IO:
            rows.append(('tss_io_bitmap',))                              # SDM IN/OUT/INS/OUTS: then the I/O permission bitmap of the TSS decides
    if m in FAULTROW:
        rows.append(FAULTROW[m])
    if m in LARFAM:
        rows += [('lar_lsl', sr, m) for sr in rec.get('lar_lsl', {}) or [None]]
    if m == 'int':
        rows += [('int_ring3', 'vectors'), ('int_ring3', 'other_vectors')]
    elif sreg_load(ins):
        rows += [('mov_to_sreg', 'undecided')]
    elif m == 'sgdt':
        rows.append(('sgdt',))
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


STR_TEXT = {'effects': 'str: expected one store only (memory form), no register, flag or fault effect', 'nstores': 'str: not exactly one store, the SDM has one', 'width': 'str: the store is not 16 bits wide (SDM: 16-bit quantity regardless of operand size)',
            'value': 'the stored value differs from TR.SegmentSelector of the record'}


def check_str(key, rec):
    """the STR spec is proof/sysspec_str.py (every SDM quote of it is checked by sdm_cite_check.py against sysspec_pages.tsv)"""
    import sysspec_str
    r = sysspec_str.check(key, rec)
    return ('proved', 'SDM STR: DEST := TR(SegmentSelector), the record selectors.tr') if r == 'ok' else ('FAILED(dsl)', STR_TEXT[r])


def far_kind(key):
    """-> None, or (call?, immediate form?) for a far CALL / JMP: 9A / EA (ptr16:32), FF /3 and FF /5 with a memory operand (m16:32); decided by the encoding bytes (SDM Vol 2 CALL, JMP)"""
    b = [x for x in shape.rep_bytes(key) if x not in (0x66, 0x67, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0xf0, 0xf2, 0xf3)]
    if b[0] in (0x9a, 0xea): return b[0] == 0x9a, True
    if b[0] == 0xff and len(b) > 1 and (b[1] >> 3) & 7 in (3, 5) and b[1] >> 6 != 3: return (b[1] >> 3) & 7 == 3, False


def check_far(key, fk, rec):
    """a far transfer: the cases the record decides (the selector of selectors.cs, the NULL selector) against the cited SDM text (sysspec_far.py); every other selector is defined by descriptor rows the record leaves undecided: the shape is
    uncertified, the undecided sites are counted by the caller (the selector of an immediate form is a site field, that of a memory form is data)"""
    call, imm = fk
    try:
        x86sys.get(rec, 'selectors', 'cs'); x86sys.get(rec, 'sgdt')
        return 'uncertified', 'uncertified: far transfer to a selector other than selectors.cs: descriptor rows undecided (sgdt)'      # unreachable until sgdt is decided: kept to name the rule
    except x86sys.Undecided as e:
        why = str(e)
    import sysspec_far
    if not imm:      # composition of certificates: the effective address is the PUSH r/m32 Form's, valid only if that shape has a proved certificate (CERTS = the proved shape keys of certs.tsv)
        bk = sysspec_far.basis_key(key)
        if CERTS is None or bk not in CERTS: return 'uncertified', 'uncertified: ea-basis %s not certified' % bk
        if call and sysspec_far.esp_based(key): return 'uncertified', 'uncertified: esp-based effective address of a far CALL: the SDM states the pre-instruction ESP for PUSH (Vol. 2B 4-523) and the near CALL (Vol. 2A 3-122), not for the far CALL'
    try: cases = sysspec_far.check(key, call, rec, imm)
    except (KeyError, AttributeError, AssertionError) as e: return 'not-covered', 'no Form for the far transfer (%s)' % type(e).__name__
    bad = [c for c, r in cases if r != 'proved']
    if bad: return 'FAILED(dsl)', 'far transfer differs from the SDM in: %s' % ','.join(bad)
    return 'uncertified', 'uncertified: far transfer to a selector other than selectors.cs / NULL (proved: %s): %s' % (','.join(c for c, _ in cases), why)


def decl_spec(m):
    """the SDM semantics of a DECL instruction as {register: z3 term} over the declared function of declared.json (the uninterpreted function named by the row's ufs): RDTSC `EDX:EAX := TimeStampCounter` (high:low in the basis text),
    CPUID `(EAX, EBX, ECX, EDX) := CPUID_INFO(EAX, ECX)` (the i-th function is the i-th register of the tuple)"""
    import json, re, z3
    rows = {r['function']: r for r in json.load(open(os.path.join(HERE, 'declared.json')))['rows']}
    r = rows[m]; bv = lambda n, w: z3.BitVec(n, w)
    if m == 'rdtsc':
        hi, lo = re.search(r'(E[A-D]X):(E[A-D]X) := TimeStampCounter', r['basis']).groups(); t = bv('uf_' + r['ufs'][0], 64)
        return {lo.lower(): z3.Extract(31, 0, t), hi.lower(): z3.Extract(63, 32, t)}
    return {reg: z3.Function('uf_' + u, z3.BitVecSort(32), z3.BitVecSort(32), z3.BitVecSort(32))(bv('eax', 32), bv('ecx', 32)) for reg, u in zip(r['outputs'], r['ufs'])}


def check_decl(key, m, rec):
    import z3
    f = symform.symbuild(key).form
    if f.flags or f.stores or f.flo is not None or f.faults:
        return 'FAILED(dsl)', '%s: expected register results only, no flag, store or fault' % m
    if x86sys.get(rec, *FAULTROW[m]) != 0:
        return 'not-covered', 'no spec for %s (the record says it can fault at CPL %d)' % (m, x86sys.cpl(rec))
    spec = decl_spec(m); got = {r: dsl.to_smt(v) for r, v, _ in f.regs}
    if set(got) != set(spec): return 'FAILED(dsl)', '%s writes %s, the SDM tuple is %s' % (m, sorted(got), sorted(spec))
    for r in sorted(spec):
        s = z3.Solver(); s.add(got[r] != spec[r])
        if s.check() != z3.unsat: return 'FAILED(dsl)', '%s: register %s differs from the SDM Operation' % (m, r)
    return 'proved', 'SDM %s over the declared function of declared.json (%s)' % (m.upper(), rec_name(m))


def rec_name(m): return 'no fault row: ' + '.'.join(FAULTROW[m])


def check(key):
    import z3
    m = mnemonic(key)
    fk = far_kind(key) if m in ('call', 'jmp', 'lcall', 'ljmp') else None
    if fk is not None:
        try: rec = x86sys.load()
        except x86sys.Undecided as e: return 'uncertified', 'uncertified: %s' % e
        return check_far(key, fk, rec)
    if m not in PRIV and m not in COND and m not in CLASS_B and m not in DECL and m not in LARFAM and not needs_any(m):
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
    if m in DECL:
        return check_decl(key, m, rec)
    if x86sys.get(rec, 'cr4', 'umip') != 0:
        return 'FAILED(facts)', 'cr4.umip is set on the declared machine: UMIP semantics are not specified'
    if m not in PRIV and m not in COND and m not in CLASS_B:
        return 'not-covered', 'no spec for %s (its record rows are decided)' % m
    if m == 'str' and not cond_fires(m, rec):
        return check_str(key, rec)
    if m in COND and not cond_fires(m, rec):
        return 'not-covered', 'no spec for %s (its #GP(0) condition does not hold on the declared machine; the instruction has its ordinary effect)' % m
    f = symform.symbuild(key).form
    if f.regs or f.flags or f.stores or f.flo is not None:
        return 'FAILED(dsl)', 'a class (a)/(b) instruction changes state'
    ft = [(k, c) for k, c in f.faults]
    if m in PRIV or m in COND:
        cpl = x86sys.cpl(rec)
        if (m in IO and x86sys.get(rec, 'tss_io_bitmap') != 'no port permitted') or cpl == 0 or (m in PRIV and PRIV[m][0] == 'iopl' and cpl <= x86sys.get(rec, 'eflags', 'iopl')) or (m in COND and not cond_fires(m, rec)):
            return 'not-covered', 'no spec for %s under the record (the instruction does not fault at CPL %d)' % (m, cpl)
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
    global CERTS
    argv = list(argv); far_only = '--far-only' in argv
    if far_only: argv.remove('--far-only')
    if '--certs' in argv:
        i = argv.index('--certs'); CERTS = {ln.split('\t')[0].strip() for ln in open(argv[i + 1]) if ln.strip() and ln[0] != '#'}; del argv[i:i + 2]
    keys = [ln.split()[0] for ln in open(argv[0]) if ln.strip() and ln[0] != '#'] if argv else []
    if far_only: keys = [k for k in keys if mnemonic(k) in ('call', 'jmp', 'lcall', 'ljmp') and far_kind(k) is not None]
    bad = 0
    for k in keys:
        r, n = check(k); bad += r.startswith('FAILED')
        fk = far_kind(k) if mnemonic(k) in ('call', 'jmp', 'lcall', 'ljmp') else None
        print('%s\t%s\t%s\t%s\t%s' % (k, mnemonic(k), r, n, sysspec_far.basis_key(k) if fk is not None and not fk[1] else '-'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
