#!/usr/bin/env python3
"""Gate "flags" (fnvnx-gate/1), class B of the verdict (item 21, L3b finding 5): no read of an arithmetic flag is reached on any path by a
value that the lift does not define.
    python3 -I gate_flags.py IMAGE FACTS_DIR        env GATE_OUT (result dir), JOBS (default 4); writes GATE_OUT/flags.json
IMAGE      the PE file (exe or module). FACTS_DIR holds ranges.tsv of that image (tools/universe.py, tools/modlift.py): `# base=0xB ...` and per
           entry `ADDR8 <TAB> lo-hi[,lo-hi...]`, absolute VAs at the load base B (the image's sections are mapped at B + RVA).

The rule. The lifter keeps the six arithmetic flags (cf pf af zf sf of) as locals of the lifted body of ONE entry. They are not carried across a
call, a return or a transfer to another entry. A flag that an instruction leaves architecturally undefined carries NO value: neither the DSL emitter
nor a hand handler materialises it (they write the marker FNVL_UNDEF(flag), which has no run-time value). So the lift is right for a read of a flag
exactly when every path from the entry of the body to the read crosses a write that defines that flag. Red: a read reached by a path on which the
flag is (E) as at the entry of the body, (C) as after a call, or (U) undefined by an instruction.
Universe: every (instruction, flag) read in the instruction bytes of every entry (`ranges.tsv`: a body is exactly its range). Certified: no path
reaches it undefined. Red rows carry the site, the flag, the origin set (E, C, U) and the class of the reading instruction.

Dataflow (per entry, forward, may-be-undefined, union at joins; a map instruction address -> 18 bits, three origin bits per flag; 0 = defined on
every path). Instructions are decoded (capstone, x86-32) linearly from each range start to its end; the successors of an instruction are its fall-through
(unless it ends flow: ret, direct or indirect jmp, hlt, ud2, int3) and the target of a direct jmp / jcc / loop / jecxz when that address is an instruction
of the same entry; any other target is an exit (a tail call or a switch dispatch: the lifter enters the target entry with fresh locals). A call (and int,
syscall, sysenter) sets every flag to origin C. Entry sets every flag to origin E. Writes of an instruction come from the decoder's EFLAGS fields
(MODIFY, RESET, SET = defined; UNDEFINED = origin U), reads from TEST; the decoder's table has holes that are covered by the rules below, taken from
the SDM and stated as ids of the decoder (not names):
  reads the decoder does not list: cmc (CF); daa, das, aaa, aas (AF, CF); salc (CF); lahf (CF PF AF ZF SF); pushf/pushfd (all six); into (OF).
  writes the decoder does not list for an encoding: test with a memory operand (84 /r; CF PF ZF SF OF defined, AF undefined, as every other encoding of
  test); fcomi, fucomi, fcomip, fucomip (CF PF ZF defined; OF SF AF reset); sahf (CF PF AF ZF SF).
  Conditional writes: a shift or rotate by cl, and a rep cmps/scas, writes its flags only when the count is not 0: a defined flag keeps its in-state
  (the prior value may be undefined), an undefined one adds U. A shift or rotate by an immediate whose low five bits are 0 writes nothing.
The direction flag is not tracked (the lifter keeps it in `df`, defined at entry by the ABI). The class of a reading instruction (only for the report):
jcc, setcc, cmov, carry (adc sbb rcl rcr), pushf (pushf lahf), other.
Assumptions: ASM-043 (the decoder's EFLAGS fields decode correctly), ASM-045 (its write sets are complete, up to the holes above; the agreement of
the DSL's undefined sets with the decoder's is checked per (mnemonic, flag) by `agreement()`), ASM-001 (the compiler)."""
import json
import multiprocessing
import os
import struct
import sys

import capstone
from capstone import x86 as X

FLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of')
NF = len(FLAGS)
E_, C_, U_ = 1, 2, 4
ALL_E = sum(E_ << (3 * k) for k in range(NF))
ALL_C = sum(C_ << (3 * k) for k in range(NF))
FULL = (1 << (3 * NF)) - 1


FM = [
    {'MODIFY': X.X86_EFLAGS_MODIFY_CF, 'RESET': X.X86_EFLAGS_RESET_CF, 'SET': X.X86_EFLAGS_SET_CF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_CF, 'TEST': X.X86_EFLAGS_TEST_CF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_PF, 'RESET': X.X86_EFLAGS_RESET_PF, 'SET': X.X86_EFLAGS_SET_PF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_PF, 'TEST': X.X86_EFLAGS_TEST_PF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_AF, 'RESET': X.X86_EFLAGS_RESET_AF, 'SET': X.X86_EFLAGS_SET_AF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_AF, 'TEST': X.X86_EFLAGS_TEST_AF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_ZF, 'RESET': X.X86_EFLAGS_RESET_ZF, 'SET': X.X86_EFLAGS_SET_ZF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_ZF, 'TEST': X.X86_EFLAGS_TEST_ZF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_SF, 'RESET': X.X86_EFLAGS_RESET_SF, 'SET': X.X86_EFLAGS_SET_SF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_SF, 'TEST': X.X86_EFLAGS_TEST_SF},
    {'MODIFY': X.X86_EFLAGS_MODIFY_OF, 'RESET': X.X86_EFLAGS_RESET_OF, 'SET': X.X86_EFLAGS_SET_OF, 'UNDEFINED': X.X86_EFLAGS_UNDEFINED_OF, 'TEST': X.X86_EFLAGS_TEST_OF},
]
INS = {
    'AAA': X.X86_INS_AAA,
    'AAS': X.X86_INS_AAS,
    'CALL': X.X86_INS_CALL,
    'CMC': X.X86_INS_CMC,
    'CMPSB': X.X86_INS_CMPSB,
    'CMPSD': X.X86_INS_CMPSD,
    'CMPSQ': X.X86_INS_CMPSQ,
    'CMPSW': X.X86_INS_CMPSW,
    'DAA': X.X86_INS_DAA,
    'DAS': X.X86_INS_DAS,
    'FCOMI': X.X86_INS_FCOMI,
    'FCOMPI': X.X86_INS_FCOMPI,
    'FUCOMI': X.X86_INS_FUCOMI,
    'FUCOMPI': X.X86_INS_FUCOMPI,
    'HLT': X.X86_INS_HLT,
    'INT': X.X86_INS_INT,
    'INT1': X.X86_INS_INT1,
    'INT3': X.X86_INS_INT3,
    'INTO': X.X86_INS_INTO,
    'IRET': X.X86_INS_IRET,
    'IRETD': X.X86_INS_IRETD,
    'IRETQ': X.X86_INS_IRETQ,
    'JMP': X.X86_INS_JMP,
    'LAHF': X.X86_INS_LAHF,
    'LCALL': X.X86_INS_LCALL,
    'LJMP': X.X86_INS_LJMP,
    'PUSHF': X.X86_INS_PUSHF,
    'PUSHFD': X.X86_INS_PUSHFD,
    'RCL': X.X86_INS_RCL,
    'RCR': X.X86_INS_RCR,
    'RET': X.X86_INS_RET,
    'RETF': X.X86_INS_RETF,
    'RETFQ': X.X86_INS_RETFQ,
    'ROL': X.X86_INS_ROL,
    'ROR': X.X86_INS_ROR,
    'SAHF': X.X86_INS_SAHF,
    'SAL': X.X86_INS_SAL,
    'SALC': X.X86_INS_SALC,
    'SAR': X.X86_INS_SAR,
    'SCASB': X.X86_INS_SCASB,
    'SCASD': X.X86_INS_SCASD,
    'SCASQ': X.X86_INS_SCASQ,
    'SCASW': X.X86_INS_SCASW,
    'SHL': X.X86_INS_SHL,
    'SHLD': X.X86_INS_SHLD,
    'SHR': X.X86_INS_SHR,
    'SHRD': X.X86_INS_SHRD,
    'SYSCALL': X.X86_INS_SYSCALL,
    'SYSENTER': X.X86_INS_SYSENTER,
    'SYSEXIT': X.X86_INS_SYSEXIT,
    'SYSRET': X.X86_INS_SYSRET,
    'TEST': X.X86_INS_TEST,
    'UD2': X.X86_INS_UD2,
}
ID = lambda n: INS.get(n, -1)
SHIFTS = frozenset(ID(n) for n in ('SHL', 'SHR', 'SAR', 'SAL', 'ROL', 'ROR', 'RCL', 'RCR', 'SHLD', 'SHRD')) - {-1}
STRCMP = frozenset(ID(n) for n in ('CMPSB', 'CMPSW', 'CMPSD', 'CMPSQ', 'SCASB', 'SCASW', 'SCASD', 'SCASQ')) - {-1}
RD_EXTRA = {ID('CMC'): 0b000001, ID('DAA'): 0b000101, ID('DAS'): 0b000101, ID('AAA'): 0b000101, ID('AAS'): 0b000101, ID('SALC'): 0b000001,
            ID('LAHF'): 0b011111, ID('PUSHFD'): 0b111111, ID('PUSHF'): 0b111111, ID('INTO'): 0b100000}      # bit k = FLAGS[k]
WR_EXTRA = {ID('FCOMI'): 0b111111, ID('FUCOMI'): 0b111111, ID('FCOMPI'): 0b111111, ID('FUCOMPI'): 0b111111, ID('SAHF'): 0b011111}
HOLE = {ID('TEST'): (0b111011, 0b000100)}      # cf pf zf sf of defined, af undefined: capstone gives `test m8, r8` (84 /r) no flags at all
CALLS = frozenset(ID(n) for n in ('CALL', 'LCALL', 'INT', 'INT1', 'SYSCALL', 'SYSENTER', 'SYSEXIT', 'SYSRET')) - {-1}
ENDS = frozenset(ID(n) for n in ('RET', 'RETF', 'RETFQ', 'IRET', 'IRETD', 'IRETQ', 'HLT', 'UD2', 'INT3', 'JMP', 'LJMP')) - {-1}
JMP, LJMP = ID('JMP'), ID('LJMP')


def classof(ins):
    m = ins.mnemonic.split()[-1]
    if ins.id in (ID('PUSHF'), ID('PUSHFD'), ID('LAHF')): return 'pushf'
    if m in ('adc', 'sbb', 'rcl', 'rcr'): return 'carry'
    if m.startswith('set'): return 'setcc'
    if m.startswith('cmov'): return 'cmov'
    if m[0] == 'j' and m not in ('jmp', 'jecxz', 'jcxz'): return 'jcc'
    return 'other'


CLASSES = ('jcc', 'setcc', 'cmov', 'carry', 'pushf', 'other')


def effect(ins):
    """-> (reads, defs, undefs, cond): flag-number bit masks; cond = the writes happen only when a count is not 0 (a defined flag keeps its in-state)"""
    ef = ins.eflags
    rd = df = un = 0
    for k in range(NF):
        m = FM[k]
        if ef & m['TEST']: rd |= 1 << k
        if ef & (m['MODIFY'] | m['RESET'] | m['SET']): df |= 1 << k
        if ef & m['UNDEFINED']: un |= 1 << k
    if ef == 0 and ins.id in HOLE:
        df, un = HOLE[ins.id]
    rd |= RD_EXTRA.get(ins.id, 0)
    df |= WR_EXTRA.get(ins.id, 0)
    cond = False
    if ins.id in SHIFTS and ins.operands:
        c = ins.operands[-1]
        if c.type == X.X86_OP_IMM:
            if c.imm & 31 == 0: return rd, 0, 0, False
        else: cond = True
    elif ins.id in STRCMP and (df or un) and any(p in (0xf2, 0xf3) for p in ins.prefix[:1]): cond = True
    return rd, df, un, cond


class Image:
    def __init__(self, path, base):
        d = self.d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        n = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        self.base = base
        self.secs = []
        for i in range(n):
            o = pe + 24 + opt + 40 * i
            vs, va, rs, rp = struct.unpack_from('<IIII', d, o + 8)
            self.secs.append((base + va, base + va + max(vs, rs), rp, rs))
        self.secs.sort()

    def read(self, lo, hi):
        for a, b, rp, rs in self.secs:
            if a <= lo and hi <= a + rs: return self.d[rp + lo - a: rp + hi - a]
        return None


def load_ranges(path):
    base, rows = None, {}
    for l in open(path):
        l = l.rstrip('\n')
        if l.startswith('#'):
            if 'base=' in l: base = int(l.split('base=')[1].split(':')[0].split()[0], 16)
            continue
        if not l: continue
        a, r = l.split('\t')
        rows[int(a, 16)] = sorted((int(x.split('-')[0], 16), int(x.split('-')[1], 16)) for x in r.split(',') if x)
    return base, rows


_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_md.detail = True
KSEQ, KJMP, KJCC, KEND, KCALL = 0, 1, 2, 3, 4
MEMO = {}          # address -> decoded instruction: valid for ONE image (cleared by the entry points that load an image)


def decode(img, lo, hi):
    """-> ([(addr, size, kind, target, reads, defs, undefs, cond, cls, mnemonic)], undecodable address or None)"""
    code = img.read(lo, hi)
    if code is None: return [], lo
    out, a = [], lo
    for ins in _md.disasm(code, lo):
        if ins.address + ins.size > hi: break
        r = MEMO.get(ins.address)
        if r is None:
            rd, df, un, cond = effect(ins)
            tgt = None
            op = ins.operands[0] if ins.operands else None
            if ins.id in CALLS: kind = KCALL
            elif ins.id in ENDS and ins.id not in (JMP, LJMP): kind = KEND
            elif ins.id in (JMP, LJMP):
                kind = KJMP if op is not None and op.type == X.X86_OP_IMM else KEND
                tgt = op.imm if kind == KJMP else None
            elif X.X86_GRP_JUMP in ins.groups or X.X86_GRP_BRANCH_RELATIVE in ins.groups:
                kind = KJCC; tgt = op.imm if op is not None and op.type == X.X86_OP_IMM else None
            else: kind = KSEQ
            r = MEMO[ins.address] = (ins.address, ins.size, kind, tgt, rd, df, un, cond, classof(ins), ins.mnemonic)
        out.append(r)
        a = ins.address + ins.size
    return out, (None if a == hi else a)


def analyse_entry(img, entry, ranges):
    """-> (instructions, [red rows (site, flag, origins, cls, mnemonic)], reads, undecodable)"""
    ins, bad = {}, 0
    for lo, hi in ranges:
        rows, b = decode(img, lo, hi)
        if b is not None: bad += 1
        for r in rows: ins[r[0]] = r
    state = {entry: ALL_E} if entry in ins else {}
    work, seen = [entry] if entry in ins else [], set()
    while work:
        a = work.pop()
        r = ins[a]
        st = state[a]
        _, size, kind, tgt, rd, df, un, cond, cls, mn = r
        o = st
        if kind == KCALL: o = ALL_C
        else:
            for k in range(NF):
                bit = 1 << k
                sh = 3 * k
                if un & bit: o = (o & ~(7 << sh)) | (U_ << sh) if not cond else o | (U_ << sh)
                elif df & bit and not cond: o = o & ~(7 << sh)
        succ = []
        if kind in (KSEQ, KCALL, KJCC): succ.append(a + size)
        if kind in (KJMP, KJCC) and tgt is not None: succ.append(tgt)
        for s in succ:
            if s not in ins: continue
            n = state.get(s)
            if n is None or n | o != n:
                state[s] = o if n is None else n | o
                work.append(s)
    red, reads = [], 0
    for a in sorted(state):
        r = ins[a]
        rd = r[4]
        if not rd: continue
        st = state[a]
        for k in range(NF):
            if rd >> k & 1:
                reads += 1
                org = st >> (3 * k) & 7
                if org: red.append((a, FLAGS[k], org, r[8], r[9]))
    return len(ins), red, reads, bad


G = {}


def _init(path, base, rows):
    MEMO.clear(); G['img'] = Image(path, base); G['rows'] = rows


def _work(entries):
    img, rows = G['img'], G['rows']
    out = []
    for e in entries:
        n, red, reads, bad = analyse_entry(img, e, rows[e])
        out.append((e, n, red, reads, bad))
    return out


def run(path, facts):
    base, rows = load_ranges(os.path.join(facts, 'ranges.tsv'))
    ents = sorted(rows)
    chunks = [ents[i:i + 64] for i in range(0, len(ents), 64)]
    ntot = reads = bad = 0
    red = []
    jobs = int(os.environ.get('JOBS', '4'))
    with multiprocessing.Pool(jobs, _init, (path, base, rows)) as pool:
        for part in pool.imap(_work, chunks):
            for e, n, rd, rds, b in part:
                ntot += n; reads += rds; bad += b
                red += [(e,) + r for r in rd]
    return ents, ntot, reads, bad, red


def summary(ents, ntot, reads, bad, red):
    cls = {c: 0 for c in CLASSES}
    org = {}
    for e, a, f, o, c, m in red:
        cls[c] += 1
        k = ''.join(n for n, b in (('E', E_), ('C', C_), ('U', U_)) if o & b)
        org[k] = org.get(k, 0) + 1
    sites = len({r[1] for r in red})
    return {'entries': len(ents), 'instructions': ntot, 'flag_reads': reads, 'red_flag_reads': len(red), 'red_sites': sites,
            'red_by_class': cls, 'red_by_origin': dict(sorted(org.items())), 'undecodable_ranges': bad}


def main(argv):
    path, facts = argv[0], argv[1]
    res = run(path, facts)
    s = summary(*res)
    red = res[4]
    out = dict(s, format='fnvnx-gate/1', name='flags', status='green' if not red and not res[3] else 'red',
               certified=s['flag_reads'] - s['red_flag_reads'], universe=s['flag_reads'],
               uncertified=['%08x:%s' % (r[1], r[2]) for r in red[:50]],
               pushf_sites=['%08x' % r[1] for r in red if r[4] == 'pushf'][:200],
               first_red=[{'entry': '%08x' % e, 'site': '%08x' % a, 'flag': f, 'origin': o, 'class': c, 'mnemonic': m} for e, a, f, o, c, m in red[:50]])
    d = os.environ.get('GATE_OUT')
    if d:
        os.makedirs(d, exist_ok=True)
        json.dump(out, open(os.path.join(d, 'flags.json'), 'w'), indent=1, sort_keys=True)
    print(json.dumps(s, sort_keys=True))
    return 0 if out['status'] == 'green' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
