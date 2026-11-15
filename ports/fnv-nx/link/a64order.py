"""a64order - the machine-code rule of class B (memory ordering, R9 TSO-MAP) for native AArch64 ILP32 objects.

    scan(path) -> Scan(rows, stats)     decide every memory-access instruction of every executable byte of one object

rows  [(item, kind)] sorted, kind '' = certified.  Items: `` (the object: `.fnvnx.frame` present and strictly parsed), `FUNC` (a function
      symbol that is a usable code range: every symbol of the function has the one same record), `FUNC+0xOFF` (a memory-access
      instruction, or a word that cannot be decoded), `FUNC+0xOFF/escape` (an instruction that hands a frame address out), and
      `SYM` for a function symbol of size 0 or one that overlaps another.  FUNC is the first (sorted) symbol of the function; code in
      no function is named by its section, offset from the section start.
stats {rule: count}  how many items each certifying rule took

RULES.  An instruction with a memory operand (decoded by capstone; prefetch hints are no access; a PC-relative literal load is one)
is certified by the first rule that applies:
 O  ordered: LDAR*/LDAXR*/LDAXP (acquire), STLR*/STLXR*/STLXP (release), and the LSE read-modify-write forms with both acquire and
    release (CASAL*, SWPAL*, LDxxxAL*): the forms of the R9 map (ldar/stlr and the Interlocked loops).
 F  frame (record R1): the access goes through the frame (sp while its offset from the CFA is statically known; x29 while it is
    the frame pointer; a register that holds a computed frame address) with an immediate offset, so that the gate itself computes the
    CFA-relative range [c, c+size) it touches (size from the mnemonic and the data register: LDR/STR/LDUR/STUR (+B/H/SB/SH/SW),
    LDP/STP/LDNP/STNP, LDPSW; any other form is not F); the function has exactly one `.fnvnx.frame` record, it is not `dynamic`,
    the range lies below the CFA (inside the function's own frame), and it overlaps no recorded range (the frame ranges of the
    variables whose address the compiler took).  A plain access that overlaps a recorded range, or whose range the gate cannot
    compute, is not F and must be O or R like any non-frame access.
 L  linker or read-only load (a load, never a store): (a) a relocation that makes the linker fill the loaded word (GOT, TLS
    descriptor, TLS initial-exec slot); (b) a relocation to a symbol placed in an allocated section without SHF_WRITE
    (LD_PREL_LO19, LDST*_ABS_LO12_NC), or a literal load without relocation (the pool is in the function's own section); (c) a base
    register that holds an address in such a section: `adrp`+`add` whose relocations name it, copied by mov/uxtw, plus a run-time
    index (a table, a jump table, a string).
 R  fenced run (the form the plugin emits where ldar/stlr cannot express the access: bit-fields, packed or misaligned fields,
    aggregates, vectors): a plain load (or LDXR*) is certified when on every path from it the next event that is not part of the
    same run is a `dmb ishld|ish|sy|ld` (for LDXR*: `dmb ish|sy`), before an ordered access, a call, a return, a jump out of the
    function or an undecodable word; a plain store (STXR* likewise) when on every path to it the previous such event is a `dmb ish|sy`.
    The run is the statement: plain accesses, frame, read-only and linker accesses between the fence and the access belong to it.
 Escapes.  A register that holds a frame address (`add xN, sp, #imm`, `mov xN, sp`, x29-based, copied and offset by immediates:
    tag D with its CFA offset; derived any other way: tag X with the CFA offsets it comes from) is an escape when an instruction
    stores it as data, uses it as an index, or leaves it in x0-x8 at a call (the callee may read it as an argument). The escape is certified iff every origin lies in a recorded range (end included: a one-past-the-end pointer); an
    origin unknown to the gate (the stack pointer was not static) or outside every range is uncertified. This is the consistency
    check of record R1: a frame address the compiler computes and lets out must be one the compiler recorded. The frame pointer
    itself (x29) is not an escape (the frame-record chain names no variable).
 Everything else is uncertified: a plain load or store through a register that is not proven frame or read-only, an exclusive pair
 without release or fence, a weak atomic, an undecodable word.

FRAME FACTS (dataflow over the function's instructions).  State per instruction: sp as an offset from the CFA (0 at entry; changed by
immediates, pre and post index write back, restores from x29 or a derived frame address) or unknown (alloca, a register amount);
register tags as above plus RO (an address in a read-only section); registers x0-x18 and x30 lose their tags at a call.  Edges:
fall-through, direct branches inside the function, an indirect jump goes to every instruction of the function, a call goes to every
instruction no other edge reaches (a landing pad). A branch with a relocation or a target outside the function leaves it. Joins keep
what all paths agree on; two different frame addresses join to the tag X with both origins. Unreached instructions have an unknown
stack pointer.

INPUT FACTS AND RULES.
 R1      input fact: the `.fnvnx.frame` record is produced by the pinned compiler's plugin (ASM-001) from the final frame layout and
         names every variable whose address the compiler takes where the machine code does not show the address leaving the function.
         The gate reads the plugin's record for WHICH variables are address-taken (TREE_ADDRESSABLE after the optimizers) and for their
         extents; it checks the machine code for everything else (offsets, access sizes, escapes, ordering).
 BOUNDS  Assumption ASM-006 (the exe makes no wild accesses; array indices in bounds): pointer arithmetic on an address stays inside
         the object the address points into; this is what lets a derived frame address (tag X) be judged by its origins.
 STACK   input fact: an sp/x29-relative access with an immediate offset lies in the thread's own stack frame; the frame size is
         the CFA-relative sp that this gate computes from the instructions (FRAME FACTS above).
 ROBND   input fact: a load through a read-only base register plus an index stays inside the object; the read-only sections and
         their objects are those of objects.json (the object's section table, flags without SHF_WRITE and PT_GNU_RELRO).
 RUN     part of rule R9 (docs/ORDERING.md, "Everything else is ordered", aggregates on the dmb fence path): the loads of one run
         (one source statement: an aggregate copy) are ordered as a whole against everything outside the run, and R9 gives no order
         among the loads of the run itself.
 GOT     input fact: GOT and TLS-descriptor slots are written only by the linker/runtime before the code that reads them runs;
         provided by layout.json (fnvnx-layout/1, prot) and PT_GNU_RELRO of the link.
 RWX     input fact: no code writes a section that was placed without SHF_WRITE; provided by objects.json (section flags) and
         layout.json (prot).
Pure: a function of the object bytes."""
import bisect
import collections

import capstone
from capstone import arm64_const as K

import a64obj
import framesec as a64frame

_MD = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
_MD.detail = True

SP = 31


def _insnames():
    out = {}
    for n in range(1, 2000):
        try:
            nm = _MD.insn_name(n)
        except capstone.CsError:
            nm = None
        if nm:
            out[n] = nm.upper()
    return out


_INSNAME = _insnames()
_BYNAME = {v: k for k, v in _INSNAME.items()}


def _ids(*names):
    """capstone instruction ids of the mnemonics (the disassembler's own table); a mnemonic it does not know is absent."""
    return frozenset(_BYNAME[n] for n in names if n in _BYNAME)


def _regmap():
    """capstone register id -> 0..30 (x or w register) or SP.  The disassembler's own identities of the registers."""
    m = {}
    for rid in range(1, 600):
        try:
            n = _MD.reg_name(rid)
        except capstone.CsError:
            continue
        if n in ('sp', 'wsp'):
            m[rid] = SP
        elif n == 'fp':
            m[rid] = 29
        elif n == 'lr':
            m[rid] = 30
        elif n and n[0] in 'xw' and n[1:].isdigit() and int(n[1:]) <= 30:
            m[rid] = int(n[1:])
    return m


REG = _regmap()

ACQ_REL = _ids('LDAR', 'LDARB', 'LDARH', 'LDAXR', 'LDAXRB', 'LDAXRH', 'LDAXP', 'STLR', 'STLRB', 'STLRH', 'STLXR', 'STLXRB',
               'STLXRH', 'STLXP')
LSE_AL = _ids(*[b + 'AL' + s for b in ('CAS', 'SWP', 'LDADD', 'LDCLR', 'LDEOR', 'LDSET', 'LDSMAX', 'LDSMIN', 'LDUMAX', 'LDUMIN')
                for s in ('', 'B', 'H')], 'CASPAL')
EXCL = _ids('LDXR', 'LDXRB', 'LDXRH', 'LDXP', 'STXR', 'STXRB', 'STXRH', 'STXP') | _ids(
    'LDAXR', 'LDAXRB', 'LDAXRH', 'LDAXP', 'STLXR', 'STLXRB', 'STLXRH', 'STLXP')
EXCL_PLAIN_LOAD = _ids('LDXR', 'LDXRB', 'LDXRH', 'LDXP')
EXCL_PLAIN_STORE = _ids('STXR', 'STXRB', 'STXRH', 'STXP')
RMW_PREFIX = ('CAS', 'SWP', 'LDADD', 'LDCLR', 'LDEOR', 'LDSET', 'LDSMAX', 'LDSMIN', 'LDUMAX', 'LDUMIN', 'STADD', 'STCLR', 'STEOR',
              'STSET', 'STSMAX', 'STSMIN', 'STUMAX', 'STUMIN')
HINT = _ids('PRFM', 'PRFUM')
LITERAL = _ids('LDR', 'LDRSW')
END = _ids('ERET', 'ERETAA', 'ERETAB', 'BRK', 'UDF', 'HLT', 'DCPS1', 'DCPS2', 'DCPS3')
COPY_MOV = _ids('MOV')
UBFX = _ids('UBFX')
ADD = _ids('ADD')
SUB = _ids('SUB')
ADRP = _ids('ADRP')
B_, CBZ_ = K.ARM64_INS_B, _ids('CBZ', 'CBNZ', 'TBZ', 'TBNZ')
CC_UNCOND = (0, K.ARM64_CC_AL)

# R_AARCH64_P32_* (ILP32), numbers as the assembler writes them (checked with readelf on an object of every form)
R_LD_PREL_LO19 = 9
R_ADR_PREL_PG_HI21 = 11
R_LDST_LO12 = frozenset((13, 14, 15, 16, 17))                 # LDST8/16/32/64/128_ABS_LO12_NC
R_LINKER_LOAD = frozenset((25, 27, 104, 105, 125))            # GOT_LD_PREL19, LD32_GOT_LO12_NC, TLSIE_LD_GOTTPREL_PREL19/LD32_..LO12_NC, TLSDESC_LD32_LO12_NC
R_DIRECT_LOAD = frozenset((R_LD_PREL_LO19,)) | R_LDST_LO12

BARRIER_FULL = frozenset((K.ARM64_BARRIER_ISH, K.ARM64_BARRIER_SY))
BARRIER_LOAD = BARRIER_FULL | frozenset((K.ARM64_BARRIER_ISHLD, K.ARM64_BARRIER_LD))

Scan = collections.namedtuple('Scan', 'rows stats')


class Ins:
    __slots__ = ('off', 'id', 'ops', 'mem', 'cc', 'rd', 'wr', 'groups', 'wb', 'size', 'ok')


def decode_range(data, start, end):
    """[Ins or (off, None)] for the words of data[start:end]; offsets are section offsets."""
    out = []
    for off in range(start, end, 4):
        got = list(_MD.disasm(data[off:off + 4], off, 1))
        if not got or got[0].size != 4:
            out.append((off, None))
            continue
        c = got[0]
        i = Ins()
        i.off, i.id, i.ops, i.cc, i.groups, i.wb, i.size, i.ok = off, c.id, c.operands, c.cc, set(c.groups), c.writeback, 4, True
        i.mem = next((o.mem for o in c.operands if o.type == K.ARM64_OP_MEM), None)
        rd, wr = c.regs_access()
        i.rd = [REG[r] for r in rd if r in REG]
        i.wr = [REG[r] for r in wr if r in REG]
        out.append(i)
    return out


def _base(i):
    return REG.get(i.mem.base) if i.mem is not None else None


def _name(i):
    return _INSNAME.get(i.id, '')


def mem_kind(i):
    """None (no memory access) | 'load' | 'store' | 'rmw' for a decoded instruction."""
    if i.id in HINT:
        return None
    if i.mem is None:
        if i.id in LITERAL and len(i.ops) == 2 and i.ops[1].type == K.ARM64_OP_IMM:
            return 'load'
        return None
    n = _name(i)
    if n.startswith(RMW_PREFIX) and i.id not in EXCL:
        return 'rmw'
    return 'store' if n.startswith('ST') else 'load'


UNKNOWN_X = ('X', None)


def origins(t):
    """the CFA offsets a derived tag comes from, or None"""
    return (t[1],) if t[0] in ('D', 'FP') else t[1]


def mkx(*tags):
    """the tag of a value derived from several tagged values (None and RO entries do not count)"""
    og = set()
    for t in tags:
        if t in (None, 'RO'):
            continue
        o = origins(t)
        if o is None:
            return UNKNOWN_X
        og.update(o)
    return ('X', tuple(sorted(og)))


def _regsize(rid):
    """bytes of a data register operand: x 8, w 4, q 16, d 8, s 4, h 2, b 1; None for a vector register list"""
    n = _MD.reg_name(rid) or ''
    if n in ('fp', 'lr', 'sp'):
        return 8
    if n == 'wsp':
        return 4
    return {'x': 8, 'w': 4, 'q': 16, 'd': 8, 's': 4, 'h': 2, 'b': 1}.get(n[:1]) if n[1:].isdigit() or n in ('xzr', 'wzr') else None


def acc_size(i):
    """bytes a plain memory instruction accesses, from its mnemonic and data registers; None when the form is not one of
    LDR/STR/LDUR/STUR (+B/H/SB/SH/SW) and LDP/STP/LDNP/STNP/LDPSW, which are the only plain forms the compiler uses on the frame"""
    n = _name(i)
    regs = [o.reg for o in i.ops if o.type == K.ARM64_OP_REG]
    if n in ('LDPSW',):
        return 8
    if n in ('LDP', 'STP', 'LDNP', 'STNP'):
        z = _regsize(regs[0]) if regs else None
        return None if z is None else 2 * z
    if n in ('LDR', 'STR', 'LDUR', 'STUR'):
        return _regsize(regs[0]) if regs else None
    if n in ('LDRB', 'STRB', 'LDURB', 'STURB', 'LDRSB', 'LDURSB'):
        return 1
    if n in ('LDRH', 'STRH', 'LDURH', 'STURH', 'LDRSH', 'LDURSH'):
        return 2
    if n in ('LDRSW', 'LDURSW'):
        return 4
    return None


def ctl(i):
    """'seq' | 'jmp' (unconditional direct) | 'cond' (direct, falls through) | 'call' | 'ijmp' | 'end'."""
    if i.id == B_:
        return 'jmp' if i.cc in CC_UNCOND else 'cond'
    if i.id in CBZ_:
        return 'cond'
    if K.ARM64_GRP_CALL in i.groups or K.ARM64_GRP_INT in i.groups:
        return 'call'
    if K.ARM64_GRP_RET in i.groups or i.id in END:
        return 'end'
    if K.ARM64_GRP_JUMP in i.groups:
        return 'ijmp'
    return 'seq'


def target_ro(o, rel):
    """Is the symbol of relocation REL placed in an allocated section that nothing writes?"""
    y = o.symbols[rel.sym]
    if y.shndx is None or y.type == 'STT_TLS':
        return False
    s = o.sections[y.shndx]
    return bool(s.flags & a64obj.SHF_ALLOC) and not s.flags & a64obj.SHF_WRITE and s.type != 'SHT_NOBITS'


def section_ro(o, shndx):
    s = o.sections[shndx]
    return bool(s.flags & a64obj.SHF_ALLOC) and not s.flags & a64obj.SHF_WRITE


class Func:
    """One body: instructions, edges, dataflow, verdicts."""

    def __init__(self, o, shndx, a, b, name, rel, rec):
        self.o, self.shndx, self.a, self.b, self.name, self.rel, self.rec = o, shndx, a, b, name, rel, rec
        data = o.sections[shndx].data
        mp = o.mapping.get(shndx, [])
        keys = [m[0] for m in mp]
        kind = lambda x: mp[bisect.bisect_right(keys, x) - 1][1] if bisect.bisect_right(keys, x) else 'x'   # noqa: E731
        self.ins, self.undec = [], []
        cut = [a] + [k for k in keys if a < k < b] + [b]
        for s, e in zip(cut, cut[1:]):
            if kind(s) == 'd':
                continue
            for x in decode_range(data, s, e - (e - s) % 4):
                (self.undec.append(x[0]) if isinstance(x, tuple) else self.ins.append(x))
        self.undec_set = set(self.undec)
        self.pos = {i.off: k for k, i in enumerate(self.ins)}
        self.words = sorted([i.off for i in self.ins] + self.undec)

    # -- control flow
    def build(self):
        n = len(self.ins)
        succ = [set() for _ in range(n)]
        self.ctl = [ctl(i) for i in self.ins]
        self.exits = [False] * n
        nxt = lambda k: self.pos.get(self.ins[k].off + 4)   # noqa: E731
        for k, i in enumerate(self.ins):
            c = self.ctl[k]
            if c == 'seq' or c == 'call' or c == 'cond':
                if nxt(k) is not None:
                    succ[k].add(nxt(k))
            if c in ('jmp', 'cond'):
                tgt = [o.imm for o in i.ops if o.type == K.ARM64_OP_IMM][-1]
                if i.off in self.rel or tgt not in self.pos:
                    self.exits[k] = True
                else:
                    succ[k].add(self.pos[tgt])
            if c == 'ijmp':
                succ[k].update(range(n))
                self.exits[k] = True
        pred = [set() for _ in range(n)]
        for k in range(n):
            for s in succ[k]:
                pred[s].add(k)
        orphans = [k for k in range(1, n) if not pred[k]]
        for k in range(n):
            if self.ctl[k] == 'call':
                succ[k].update(orphans)
        self.succ = [sorted(s) for s in succ]
        self.pred = [set() for _ in range(n)]
        for k in range(n):
            for s in self.succ[k]:
                self.pred[s].add(k)
        self.orphans = set(orphans)

    # -- facts. State = (sp, regs). sp: the stack pointer as a CFA-relative offset (an int, 0 at entry) or None (dynamic).
    # regs: sorted ((reg, tag)): tag 'RO' (an address in a read-only section), ('D', c) (the frame address CFA+c computed
    # from sp or the frame pointer), ('FP', c) (the frame pointer: x29 = CFA+c), ('X', origins) (a value derived from frame
    # addresses in a way the rules do not follow: pointer arithmetic, a join; ORIGINS are the CFA offsets of the frame
    # addresses it comes from, or None when one is not known; the derivation stays inside the object the origin points
    # into, Assumption ASM-006: the exe makes no wild accesses; array indices in bounds).  A register without a tag holds no frame address (a frame address stored anywhere is
    # an escape, judged where it is stored, and a value loaded back is untracked).
    @staticmethod
    def _imm(i, k):
        o = i.ops[k]
        sh = o.shift.value if o.shift.type != 0 else 0
        return o.imm << sh

    def _val(self, reg, sp, rg):
        """the tag a register holds as a source: SP reads as the frame address CFA+sp"""
        if reg == SP:
            return ('D', sp) if sp is not None else UNKNOWN_X
        return rg.get(reg)

    def transfer(self, k, st):
        i = self.ins[k]
        sp, rg = st[0], dict(st[1])
        st_rg = dict(st[1])
        c = self.ctl[k]
        wr = set(i.wr)
        mk = mem_kind(i)
        reloc = self.rel.get(i.off)
        reg_ops = [o for o in i.ops if o.type == K.ARM64_OP_REG and o.reg in REG]
        src = [REG[o.reg] for o in i.ops[1:] if o.type == K.ARM64_OP_REG and o.reg in REG]
        dst = REG[i.ops[0].reg] if i.ops and i.ops[0].type == K.ARM64_OP_REG and i.ops[0].reg in REG else None
        tag = None
        newsp = sp
        derived_read = any(self._val(r, sp, rg) not in (None, 'RO') for r in sorted(set(i.rd)) if r in rg or r == SP)
        if mk is not None:
            if SP in wr:   # pre or post index write back of the stack pointer
                if i.mem.index == 0 and sp is not None:
                    post = bool(i.ops) and i.ops[-1].type == K.ARM64_OP_IMM
                    newsp = sp + (i.ops[-1].imm if post else i.mem.disp)
                else:
                    newsp = None
        elif i.id in ADRP:
            if reloc is not None and reloc.type == R_ADR_PREL_PG_HI21 and target_ro(self.o, reloc):
                tag = 'RO'
        elif i.id in (ADD | SUB) and len(i.ops) >= 3 and dst is not None and src:
            s0 = self._val(src[0], sp, rg)
            imm = self._imm(i, 2) if i.ops[2].type == K.ARM64_OP_IMM else None
            sign = 1 if i.id in ADD else -1
            ro = [s for s in src if rg.get(s) == 'RO']
            if s0 is not None and s0 != 'RO':
                if imm is not None and s0[0] != 'X':
                    kind, cv = s0
                    tag = (('FP' if (kind == 'FP' and dst == 29) else 'D'), cv + sign * imm)
                    if dst == 29 and kind == 'D' and src[0] == SP:
                        tag = ('FP', cv + sign * imm)
                    elif kind == 'FP' and dst != 29:
                        tag = ('D', cv + sign * imm)
                    elif dst == 29 and kind == 'D':
                        tag = ('D', cv + sign * imm)
                else:
                    tag = mkx(s0, *[self._val(s, sp, rg) for s in src[1:]])
            elif i.id in ADD and len(ro) == 1 and not (len(src) == 2 and src[0] == src[1]) and not any(
                    self._val(s, sp, rg) not in (None, 'RO') for s in src[1:]):
                tag = 'RO'
            elif imm is None and any(self._val(s, sp, rg) not in (None, 'RO') for s in src[1:]):
                tag = mkx(*[self._val(s, sp, rg) for s in src[1:]])
        elif i.id in COPY_MOV and len(i.ops) == 2 and i.ops[1].type == K.ARM64_OP_REG and dst is not None:
            s0 = self._val(REG.get(i.ops[1].reg), sp, rg)
            if s0 is not None:
                tag = s0
                if dst == 29 and isinstance(s0, tuple) and s0[0] == 'D' and REG.get(i.ops[1].reg) == SP:
                    tag = ('FP', s0[1])
        elif i.id in UBFX and len(i.ops) == 4 and i.ops[2].type == K.ARM64_OP_IMM and i.ops[2].imm == 0 and i.ops[3].imm == 32:
            if rg.get(REG.get(i.ops[1].reg)) == 'RO':
                tag = 'RO'
        elif derived_read and dst is not None:
            tag = mkx(*[self._val(r, sp, rg) for r in sorted(set(i.rd)) if r in rg or r == SP])
        if SP in wr and mk is None:   # the stack pointer written by an ALU instruction
            if tag is not None and dst == SP:
                newsp = tag[1] if isinstance(tag, tuple) and tag[0] in ('D', 'FP') else None
                tag = None
            else:
                newsp = None
        for r in wr:
            rg.pop(r, None)
        if c == 'call':
            for r in list(rg):
                if r <= 18 or r == 30:
                    rg.pop(r)
        if tag is not None and dst not in (None, SP):
            rg[dst] = tag
        if derived_read and tag is None and mk is None:
            for r in wr:
                if r != SP:
                    rg[r] = mkx(*[self._val(q, sp, st_rg) for q in sorted(set(i.rd)) if q in st_rg or q == SP])
        return (newsp, tuple(sorted(rg.items(), key=lambda t: t[0])))

    @staticmethod
    def join(a, b):
        if a is None:
            return b
        ra, rb = dict(a[1]), dict(b[1])
        out = {}
        for r in sorted(set(ra) | set(rb)):
            ta, tb = ra.get(r), rb.get(r)
            if ta == tb:
                out[r] = ta
                continue
            ds = [t for t in (ta, tb) if t not in (None, 'RO')]
            if ds:   # a frame address on one path: the merged register may hold it
                out[r] = mkx(*ds)
        return (a[0] if a[0] == b[0] else None, tuple(sorted(out.items(), key=lambda t: t[0])))

    def dataflow(self):
        n = len(self.ins)
        self.state = [None] * n
        if not n:
            return
        self.state[0] = (0, ())
        work = collections.deque([0])
        queued = {0}
        while work:
            k = work.popleft()
            queued.discard(k)
            st = self.state[k]
            for s in self.succ[k]:
                if self.ctl[k] == 'call' and s in self.orphans:
                    out = (st[0], tuple((r, t) for r, t in st[1] if r > 18 and r != 30))
                else:
                    out = self.transfer(k, st)
                nw = self.join(self.state[s], out)
                if nw != self.state[s]:
                    self.state[s] = nw
                    if s not in queued:
                        queued.add(s)
                        work.append(s)

    # -- verdicts
    def fact(self, k):
        return self.state[k] if self.state[k] is not None else (None, ())

    def frame_cfa(self, i, sp, rg):
        """CFA-relative offset of the first byte a plain memory instruction touches through the frame, or None"""
        b = _base(i)
        if i.mem is None or i.mem.index != 0 or b is None:
            return None
        if b == SP:
            return None if sp is None else sp + i.mem.disp
        t = rg.get(b)
        return t[1] + i.mem.disp if isinstance(t, tuple) else None

    def escape_sites(self, k, sp, rg):
        """[origins tuple, or None when unknown]: the frame addresses that instruction k hands out: stored, passed in x0-x8 to a
        call or tail jump, returned, or used as an index"""
        i = self.ins[k]
        out = []
        mk = mem_kind(i)

        def take(r):
            t = rg.get(r)
            if isinstance(t, tuple) and t[0] in ('D', 'X'):
                out.append(origins(t))
        if mk in ('store', 'rmw') or (mk == 'load' and i.id in EXCL):
            data = [REG[o.reg] for o in i.ops if o.type == K.ARM64_OP_REG and o.reg in REG]
            if i.id in EXCL_PLAIN_STORE or i.id in _ids('STLXR', 'STLXRB', 'STLXRH', 'STLXP'):
                data = data[1:]
            for r in data:
                take(r)
        if mk is not None and i.mem is not None and i.mem.index != 0:
            take(REG.get(i.mem.index))
        if self.ctl[k] == 'call':   # the callee may read any argument register (a return or a tail jump pops the frame: a
            for r in range(0, 9):    # frame address it carries dangles, it names no live slot)
                take(r)
        return out

    def judge(self):
        """-> {offset: (what, rule)}: rule is a certifying rule letter or None.  Items: every memory-access instruction, and
        every instruction that hands out a frame address (what 'frame-escape')."""
        res, cand = {}, {}
        self.esc = {}
        for k, i in enumerate(self.ins):
            sp, rgt = self.fact(k)
            rg = dict(rgt)
            for og in self.escape_sites(k, sp, rg):
                ok = og is not None and self.rec not in (None, a64frame.DYNAMIC) and all(
                    any(o <= c <= o + s for o, s in self.rec) for c in og)
                prev = self.esc.get(i.off)
                self.esc[i.off] = ('frame-escape', 'E') if ok and (prev is None or prev[1] == 'E') else (
                    'frame-escape-unrecorded' if og is not None else 'frame-escape-unknown', None)
            mk = mem_kind(i)
            if mk is None:
                continue
            b = _base(i)
            reloc = self.rel.get(i.off)
            if i.id in ACQ_REL or i.id in LSE_AL:
                res[i.off] = ('ordered', 'O')
            elif self.frame_ok(i, sp, rg):
                res[i.off] = ('frame', 'F')
            elif mk == 'load' and reloc is not None and reloc.type in R_LINKER_LOAD:
                res[i.off] = ('linker', 'L')
            elif mk == 'load' and reloc is not None and reloc.type in R_DIRECT_LOAD and target_ro(self.o, reloc):
                res[i.off] = ('readonly', 'L')
            elif mk == 'load' and i.mem is None and reloc is None:
                tgt = [o.imm for o in i.ops if o.type == K.ARM64_OP_IMM][0]
                res[i.off] = ('literal', 'L') if section_ro(self.o, self.shndx) and self.a <= tgt < self.o.sections[
                    self.shndx].size else ('plain-load', None)
                if res[i.off][1] is None:
                    cand[k] = 'load'
            elif mk == 'load' and b is not None and rg.get(b) == 'RO':
                res[i.off] = ('readonly', 'L')
            elif mk == 'rmw':
                res[i.off] = ('weak-atomic', None)
            else:
                cand[k] = 'load' if mk == 'load' else 'store'
                res[i.off] = ('plain-' + cand[k], None)
        if cand:
            self.fences(res, cand)
        return res

    def frame_ok(self, i, sp, rg):
        """rule F: the access is inside the function's own frame and overlaps no address-taken variable of the record"""
        if self.rec is None or self.rec == a64frame.DYNAMIC:
            return False
        c = self.frame_cfa(i, sp, rg)
        sz = acc_size(i)
        if c is None or sz is None or c + sz > 0:
            return False
        return not any(c < o + s and o < c + sz for o, s in self.rec)

    def fences(self, res, cand):
        n = len(self.ins)
        excl_load = {k for k in cand if self.ins[k].id in EXCL_PLAIN_LOAD}
        excl_store = {k for k in cand if self.ins[k].id in EXCL_PLAIN_STORE}

        def barrier(k):
            i = self.ins[k]
            if i.id == K.ARM64_INS_DMB and i.ops and i.ops[0].type == K.ARM64_OP_BARRIER:
                return i.ops[0].barrier
            return None

        def ordered(k):
            i = self.ins[k]
            return mem_kind(i) is not None and ((i.id in ACQ_REL or i.id in LSE_AL) or res.get(i.off, ('', ''))[0] == 'weak-atomic')

        def fail(k, excl_neutral):
            c = self.ctl[k]
            if c in ('call', 'end', 'ijmp') or self.exits[k]:
                return True
            i = self.ins[k]
            if i.id in EXCL:
                return not excl_neutral
            return ordered(k)

        def solve(forward, quals, excl_neutral):
            """greatest fixpoint of g(k): the paths from (forward) / to (backward) k meet a qualifying barrier before a failing event."""
            nb = [barrier(k) for k in range(n)]
            g = [True] * n
            ch = True
            while ch:
                ch = False
                for k in range(n):
                    if not g[k]:
                        continue
                    if nb[k] in quals:
                        continue
                    if fail(k, excl_neutral):
                        v = False
                    else:
                        nbrs = self.succ[k] if forward else sorted(self.pred[k])
                        v = all(g[s] for s in nbrs) if (nbrs or forward) else False
                        if not forward and (k == 0 or not self.pred[k]):
                            v = False
                    if not v:
                        g[k] = False
                        ch = True
            return g
        fl = solve(True, BARRIER_LOAD, False)
        ffull = solve(True, BARRIER_FULL, True)
        bk = solve(False, BARRIER_FULL, False)
        bkx = solve(False, BARRIER_FULL, True)
        for k, d in sorted(cand.items()):
            i = self.ins[k]
            if d == 'load':
                gg = ffull if k in excl_load else fl
                ok = all(gg[s] for s in self.succ[k])
            else:
                gg = bkx if k in excl_store else bk
                ok = k != 0 and bool(self.pred[k]) and all(gg[p] for p in self.pred[k])
            if ok:
                res[i.off] = ('fenced-' + d, 'R')


def functions_of(o):
    """([(shndx, start, end, names, kind)], [(shndx, start, name, kind)]): the bodies to judge and the function symbols that are
    no usable range. kind of a body: 'func' (names are the function's symbols) | 'gap' (code no function symbol covers; the
    name is the section's). The others: 'size0' | 'overlap'."""
    out, extra = [], []
    for si, s in enumerate(o.sections):
        if not (s.flags & a64obj.SHF_EXECINSTR) or s.type != 'SHT_PROGBITS':
            continue
        fs = [(a, sz, names) for (sh, a, sz, names) in o.functions if sh == si]
        iv, last = [], 0
        for a, sz, names in fs:
            if sz == 0:
                extra.append((si, a, names[0], 'size0'))
                continue
            if a < last:
                extra.append((si, a, names[0], 'overlap'))
            iv.append((a, min(a + sz, s.size), names))
            last = max(last, a + sz)
        pos = 0
        for a, b, names in sorted(iv):
            if a > pos:
                out.append((si, pos, a, [s.name], 'gap'))
            out.append((si, a, b, names, 'func'))
            pos = max(pos, b)
        if pos < s.size:
            out.append((si, pos, s.size, [s.name], 'gap'))
    return out, extra


def frame_record(frows, names):
    """(ranges | 'dynamic' | None, kind): the one record all names of a function agree on; kind '' or why there is none"""
    if frows is None:
        return None, 'no-frame-record'
    recs = []
    for n in names:
        r = [x[1] for x in frows if x[0] == n]
        if len(r) != 1:
            return None, 'no-frame-record'
        recs.append(r[0])
    if any(r != recs[0] for r in recs):
        return None, 'frame-record-mismatch'
    return recs[0], ''


def scan(path):
    """-> Scan(rows, stats): rows [(item, kind)] sorted, kind '' = certified. Items: '' (the object: its `.fnvnx.frame` section
    is present and parses), 'FUNC' (a function: all its symbols have the same one record), 'FUNC+0xOFF' (a memory-access
    instruction, or an undecodable word), 'FUNC+0xOFF/escape' (an instruction that hands out a frame address)."""
    o = a64obj.read(path)
    rows, stats = [], collections.Counter()
    try:
        frows, ferr = a64frame.from_sections(o.sections, path), None
        if frows is None:
            ferr = 'no-frame-section'
    except ValueError as e:
        frows, ferr = None, 'bad-frame-section'
    rows.append(('', ferr or ''))
    bodies, extra = functions_of(o)
    for si, a, name, kind in extra:
        rows.append((name, kind))
    for si, a, b, names, kind in bodies:
        name = names[0]
        if (b - a) % 4 or a % 4:
            rows.append(('%s+0x0' % name, 'odd-code-range'))
            continue
        rec = None
        if kind == 'func':
            rec, why = frame_record(frows, names)
            rows.append((name, why))
        f = Func(o, si, a, b, name, o.relocs.get(si, {}), rec)
        f.build()
        f.dataflow()
        res = f.judge()
        base = a if kind == 'func' else 0
        for off in f.undec:
            rows.append(('%s+0x%x' % (name, off - base), 'undecodable'))
        for off, (what, rule) in sorted(res.items()):
            rows.append(('%s+0x%x' % (name, off - base), '' if rule else what))
            if rule:
                stats[what] += 1
        for off, (what, rule) in sorted(f.esc.items()):
            rows.append(('%s+0x%x/escape' % (name, off - base), '' if rule else what))
            if rule:
                stats[what] += 1
    return Scan(sorted(rows), dict(stats))
