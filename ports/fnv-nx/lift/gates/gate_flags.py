#!/usr/bin/env python3
"""Gate "flags" (fnvnx-gate/1), class B of the verdict (item 21, L3b finding 5): no read of an arithmetic flag is reached on any path by a
value that the lift does not define.
    python3 -I gate_flags.py IMAGE FACTS_DIR        env GATE_OUT (result dir), JOBS (default 4); writes GATE_OUT/flags.json
IMAGE      the PE file (exe or module). FACTS_DIR holds ranges.tsv and universe.tsv of that image (tools/universe.py, tools/modlift.py): `# base=0xB ...`;
           ranges: per entry `ADDR8 <TAB> lo-hi[,lo-hi...]`, universe: `ADDR8 <TAB> kind <TAB> extent <TAB> flags`; absolute VAs at the load base B.

The rule. The lifter keeps the six arithmetic flags (cf pf af zf sf of) as locals of the lifted body of ONE entry; a transfer to another entry passes them
only through the lifter's own entry state (below). A flag that an instruction leaves architecturally undefined carries NO value: neither the DSL emitter nor a
hand handler materialises it. So the lift is right for a read of a flag exactly when every path to the read crosses a write that defines that flag. Red: a read
reached by a path on which the flag is (E) undefined as at an entry that has an unknown predecessor, (C) as after a call, or (U) undefined by an instruction.

Single source of the flag effects of an instruction: its certified Form (proof/forms.build: the DSL Form that L3b certifies against the Sail model; the
instruction's operands come from the facts' decoder, capstone, which decides nothing about flags). From the Form, per flag f:
  reads    every flag variable that occurs in a root of the Form, except the keep leaf `V(f)` of the flag's own write `ite(k, new, V(f))`;
  write    the written value's keep condition k and its `when` w (the SDM condition under which f is defined), evaluated on the instruction's operand
           when the operand is constant: k false (no write, in-state kept: a shift or rotate by an immediate whose low five bits are 0); w true (defined);
           w false (undefined: OF of a rotate or shld/shrd with an immediate count other than 1); a count in cl leaves k and w unknown: in-state kept, defined
           or undefined (in-state U-union);
  undef    the Form's `undefined(f)`.
The transfer of a flag is out = (in & KEEP) | UNDEF, per instruction; a call (and int, syscall) sets every flag to origin C (the calling convention: the
status flags are undefined at a call and after it); the direction flag is not tracked (DF = 0 by the ABI).
A shape with no Form is UNCOVERED: its instructions are red with reason no-form (item 23 closes them) and pass the in-state through.

Dataflow. Per entry forward, may-be-undefined (a map instruction address -> 18 bits, three origin bits per flag, 0 = defined on every path), union at joins.
Instructions are decoded linearly from each range start to its end; the successors of an instruction are its fall-through (unless it ends flow: ret, jmp, hlt,
ud2, int3) and the target of a direct jmp / jcc / loop / jecxz when that address is an instruction of the same entry. Interprocedural: a direct call, a
direct jmp or jcc whose target is another entry, and the fall-through past the end of a body into another entry, contribute the state before the transfer (for
a call: before the call instruction) to the in-state of the target entry. An entry has an UNKNOWN predecessor when the facts record that something other than a
direct transfer reaches it (flag `ptr`, a kind other than decomp, call or tail: export, entry, tls, eh-*, inner...) or when no direct transfer reaches it; it
starts with every flag at origin E (undefined at the entry, as the x86 call ABI states) in addition to the join of its direct predecessors. An entry without
an unknown predecessor and without a reached direct predecessor is unreached and has no reads. The in-states iterate to a fixpoint (monotone: three bits per
flag), then the reads are judged once. The class of a reading instruction (only for the report): jcc, setcc, cmov, carry (adc sbb rcl rcr), pushf (pushf lahf),
other.
Assumptions: ASM-001 (the compiler), ASM-009 (the Form is certified against the Sail model, whose pinned patch is OWNER 9; so the Form's reads, writes and undefined
sets are those of the instruction). The facts' decoder (capstone) gives operands and boundaries only; flagagree.py cross-checks its flag table against the Forms and
a disagreement is a report row, never an input."""
import json
import multiprocessing
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import capstone
from capstone import x86 as X

from proof import forms, shape

FLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of')
NF = len(FLAGS)
E_, C_, U_ = 1, 2, 4
ALL_E = sum(E_ << (3 * k) for k in range(NF))
ALL_C = sum(C_ << (3 * k) for k in range(NF))
SHIFT_IDS = (X.X86_INS_SHL, X.X86_INS_SHR, X.X86_INS_SAR, X.X86_INS_SAL, X.X86_INS_ROL, X.X86_INS_ROR, X.X86_INS_RCL, X.X86_INS_RCR,
             X.X86_INS_SHLD, X.X86_INS_SHRD)
CALL_IDS = (X.X86_INS_CALL, X.X86_INS_LCALL, X.X86_INS_INT, X.X86_INS_SYSCALL, X.X86_INS_SYSENTER)
END_IDS = (X.X86_INS_RET, X.X86_INS_RETF, X.X86_INS_IRET, X.X86_INS_IRETD, X.X86_INS_HLT, X.X86_INS_UD2, X.X86_INS_INT3)
JMP_IDS = (X.X86_INS_JMP, X.X86_INS_LJMP)
CARRY_IDS = (X.X86_INS_ADC, X.X86_INS_SBB, X.X86_INS_RCL, X.X86_INS_RCR)
PUSHF_IDS = (X.X86_INS_PUSHF, X.X86_INS_PUSHFD, X.X86_INS_LAHF)
KSEQ, KJMP, KJCC, KEND, KCALL = 0, 1, 2, 3, 4
CLASSES = ('jcc', 'setcc', 'cmov', 'carry', 'pushf', 'other', 'no-form')
FULL = (1 << (3 * NF)) - 1


def classof(ins):
    m = ins.mnemonic.split()[-1]
    if ins.id in PUSHF_IDS: return 'pushf'
    if ins.id in CARRY_IDS: return 'carry'
    if m.startswith('set'): return 'setcc'
    if m.startswith('cmov'): return 'cmov'
    if m[0] == 'j' and m not in ('jmp', 'jecxz', 'jcxz'): return 'jcc'
    return 'other'


# ---------------------------------------------------------------- the Form's flag effect
def cv(n):
    """constant value of a DSL node whose leaves are constants, else None (an operand in a register or memory)"""
    o = n.op
    if o == 'const': return n.x
    if o in ('var', 'fld', 'load'): return None
    a = [cv(c) for c in n.a]
    if any(v is None for v in a): return None
    m = (1 << n.w) - 1
    if o == 'zext': return a[0]
    if o == 'extract': return (a[0] >> n.x[1]) & m
    if o == 'and': return a[0] & a[1]
    if o == 'or': return a[0] | a[1]
    if o == 'xor': return a[0] ^ a[1]
    if o == 'not': return ~a[0] & m
    if o == 'eq': return int(a[0] == a[1])
    if o == 'ult': return int(a[0] < a[1])
    if o == 'add': return (a[0] + a[1]) & m
    if o == 'sub': return (a[0] - a[1]) & m
    if o == 'ite': return a[1] if a[0] else a[2]
    return None


def keepof(v, f):
    """the keep condition of a flag write `ite(k, new, V(f))` / `ite(k, V(f), new)`: (k, in-state kept when k is 1, the other branch) or None"""
    if v.op != 'ite': return None
    if v.a[2].op == 'var' and v.a[2].x == f: return v.a[0], False, v.a[1]
    if v.a[1].op == 'var' and v.a[1].x == f: return v.a[0], True, v.a[2]
    return None


def form_effect(F):
    """-> (reads mask over FLAGS, KEEP mask, UNDEF mask): the last two are masks over the 18-bit state (KEEP: the in-state of a flag survives;
    UNDEF: origin U is added)"""
    seen, reads = {}, [0]
    last = {}
    for f, v, w in F.flags: last[f] = (v, w)

    def walk(n):
        if n.u in seen: return
        seen[n.u] = True
        if n.op == 'var' and n.x in FLAGS: reads[0] |= 1 << FLAGS.index(n.x)
        for c in n.a: walk(c)
    for r in [v for _, v, _ in F.regs] + [w for _, _, w in F.regs if w is not None] + [x for st in F.stores for x in st] + \
             [c for _, c in F.faults] + ([F.flo] if F.flo is not None else []) + [F.eip]:
        walk(r)
    for f, (v, w) in sorted(last.items()):
        k = keepof(v, f)
        if k is None: walk(v)
        else: walk(k[0]); walk(k[2])
        if w is not None: walk(w)
    keep = undef = 0
    for i, f in enumerate(FLAGS):
        sh = 7 << (3 * i)
        u = U_ << (3 * i)
        if f in F.undef: undef |= u
        elif f not in last: keep |= sh
        else:
            v, w = last[f]
            k = keepof(v, f)
            kv = False                                  # no keep pattern: the write is unconditional
            if k is not None:
                c = cv(k[0])
                kv = None if c is None else bool(c) == k[1]          # True: the in-state is kept; None: it depends on the operand
            wv = None if w is None else cv(w)
            if kv is True:
                keep |= sh
                continue
            if kv is None: keep |= sh
            if w is not None and (wv is None or wv == 0): undef |= u
    return reads[0], keep, undef


FCACHE = {}        # (shape key, count class) -> (reads, keep, undef) or None (no Form): a function of the shape, valid for any image


def effect(ins):
    """-> (reads, keep, undef) of the instruction from its Form, or None when its shape has no Form"""
    cc = None
    if ins.id in SHIFT_IDS and ins.operands and ins.operands[-1].type == X.X86_OP_IMM: cc = min(ins.operands[-1].imm & 31, 2)
    try: key = (shape.shape_key(ins), cc)
    except shape.ShapeError: return None
    if key in FCACHE: return FCACHE[key]
    try: r = form_effect(forms.build(ins, 0x1000))
    except Exception: r = None          # KeyError: no builder for the mnemonic; others: a shape the builders do not cover
    FCACHE[key] = r
    return r


# ---------------------------------------------------------------- the pushf / lahf class: bit dataflow of the flag word
# A pushf or lahf copies the flags into a value. A flag that is may-undefined at that point makes the matching bit of the copy may-undefined. That bit is
# harmless unless it is OBSERVED: the value of the bit decides a branch, a setcc, a compare, a store to memory or an address, or leaves the entry in a register
# or in an argument slot. A pushf, lahf or popf only moves the mask between the flags and a word. So the copy is followed forward, per entry, on one abstract state: a may-undefined mask per general register, a stack of masks for the
# words pushed since the copy, and one mask "the flags computed from a may-undefined value" (the flag bits of a flag word). Transfer by operation (and/or/xor/test/mov/movzx/shl/shr/rol/ror/
# push/pop/popf/xchg; the register operands come from the decoder, which decides nothing about the masks); any other instruction that reads a masked register is
# an observation. At a join the masks are united; stacks of different depth are an observation. The state without any mask ends the walk.
GPR = {'eax': (0, 0, 0xffffffff), 'ecx': (1, 0, 0xffffffff), 'edx': (2, 0, 0xffffffff), 'ebx': (3, 0, 0xffffffff), 'esp': (4, 0, 0xffffffff),
       'ebp': (5, 0, 0xffffffff), 'esi': (6, 0, 0xffffffff), 'edi': (7, 0, 0xffffffff), 'ax': (0, 0, 0xffff), 'cx': (1, 0, 0xffff), 'dx': (2, 0, 0xffff),
       'bx': (3, 0, 0xffff), 'sp': (4, 0, 0xffff), 'bp': (5, 0, 0xffff), 'si': (6, 0, 0xffff), 'di': (7, 0, 0xffff), 'al': (0, 0, 0xff),
       'cl': (1, 0, 0xff), 'dl': (2, 0, 0xff), 'bl': (3, 0, 0xff), 'ah': (0, 8, 0xff), 'ch': (1, 8, 0xff), 'dh': (2, 8, 0xff), 'bh': (3, 8, 0xff)}
WORDBIT = {'cf': 0, 'pf': 2, 'af': 4, 'zf': 6, 'sf': 7, 'of': 11}
FLAGWORD = sum(1 << b for b in WORDBIT.values())
ESP = 4


def _w(regs, loc, v):
    i, sh, m = loc
    r = list(regs)
    r[i] = (r[i] & ~(m << sh) & 0xffffffff) | ((v & m) << sh)
    return tuple(r)


def _tv(i, regs, op):
    """taint mask of a register or immediate operand value, None for memory"""
    if op.type == X.X86_OP_REG:
        loc = GPR.get(i.reg_name(op.reg))
        return 0 if loc is None else (regs[loc[0]] >> loc[1]) & loc[2]
    if op.type == X.X86_OP_IMM: return 0
    return None


def _addr_taint(i, regs, stack, op):
    """a memory operand is an observation when its address is masked, or when it may read a masked slot of the stack"""
    for rid in (op.mem.base, op.mem.index):
        if rid:
            loc = GPR.get(i.reg_name(rid))
            if loc is not None and regs[loc[0]] & (loc[2] << loc[1]): return True
            if rid == X.X86_REG_ESP and any(stack): return True
    return False


def tstep(i, row, regs, stack, ft):
    """one instruction on the abstract state -> (regs, stack, ft, observed)"""
    rd, keep = row[4], row[5]
    ops, ID = i.operands, i.id
    if ID in (X.X86_INS_PUSHF, X.X86_INS_PUSHFD): return regs, stack + (ft,), ft, False                   # the flag word carries the masked flags
    if ID == X.X86_INS_LAHF: return _w(regs, GPR['ah'], ft & 0xff), stack, ft, False
    if ft & sum(1 << WORDBIT[FLAGS[k]] for k in range(NF) if rd >> k & 1): return regs, stack, ft, True        # a masked flag decides this instruction
    for op in ops:
        if op.type == X.X86_OP_MEM and _addr_taint(i, regs, stack, op): return regs, stack, ft, True
    if ID == X.X86_INS_PUSH:
        v = 0
        if ID == X.X86_INS_PUSH:
            v = _tv(i, regs, ops[0])
            v = 0 if v is None else v
        return regs, stack + (v,), ft, False
    if ID in (X.X86_INS_POP, X.X86_INS_POPF, X.X86_INS_POPFD):
        v, stack = (stack[-1], stack[:-1]) if stack else (0, stack)
        if ID != X.X86_INS_POP: return regs, stack, v & FLAGWORD, False                      # popf: the masked bits of the word are the masked flags
        if ops[0].type == X.X86_OP_REG:
            loc = GPR.get(i.reg_name(ops[0].reg))
            if loc is not None: return _w(regs, loc, v), stack, ft, False
            return regs, stack, ft, False
        return regs, stack, ft, bool(v)
    if ID in (X.X86_INS_MOV, X.X86_INS_MOVZX) and len(ops) == 2:
        v = _tv(i, regs, ops[1])
        v = 0 if v is None else v
        if ops[0].type == X.X86_OP_MEM: return regs, stack, ft, bool(v)
        loc = GPR.get(i.reg_name(ops[0].reg))
        return (regs if loc is None else _w(regs, loc, v)), stack, ft, False
    if ID in (X.X86_INS_AND, X.X86_INS_OR, X.X86_INS_XOR, X.X86_INS_TEST) and len(ops) == 2:
        d, s = ops
        dl = GPR.get(i.reg_name(d.reg)) if d.type == X.X86_OP_REG else None
        td, ts = _tv(i, regs, d), _tv(i, regs, s)
        if d.type == X.X86_OP_MEM: return regs, stack, ft, bool(ts)
        if td is None or dl is None: return regs, stack, ft, True
        m = dl[2]
        if s.type == X.X86_OP_IMM:
            im = s.imm & m
            nv = td & im if ID in (X.X86_INS_AND, X.X86_INS_TEST) else (td & ~im if ID == X.X86_INS_OR else td)
        elif s.type == X.X86_OP_REG and s.reg == d.reg: nv = 0 if ID == X.X86_INS_XOR else td
        else: nv = td | (ts or 0)
        regs = regs if ID == X.X86_INS_TEST else _w(regs, dl, nv)
        return regs, stack, FLAGWORD if nv else 0, False
    if ID in (X.X86_INS_SHL, X.X86_INS_SHR, X.X86_INS_ROL, X.X86_INS_ROR) and len(ops) == 2 and ops[0].type == X.X86_OP_REG and ops[1].type == X.X86_OP_IMM:
        dl = GPR.get(i.reg_name(ops[0].reg))
        td = _tv(i, regs, ops[0])
        if dl is None or td is None: return regs, stack, ft, True
        c, m = ops[1].imm & 31, dl[2]
        if c == 0: return regs, stack, ft, False
        nb = m.bit_length()
        if ID == X.X86_INS_SHL: nv = (td << c) & m
        elif ID == X.X86_INS_SHR: nv = td >> c
        elif ID == X.X86_INS_ROL: c %= nb; nv = ((td << c) | (td >> (nb - c))) & m
        else: c %= nb; nv = ((td >> c) | (td << (nb - c))) & m
        return _w(regs, dl, nv), stack, (FLAGWORD if nv else 0) if ID in (X.X86_INS_SHL, X.X86_INS_SHR) else ft, False
    if ID == X.X86_INS_NOT and ops and ops[0].type == X.X86_OP_REG: return regs, stack, ft, False
    # any other instruction: a masked register that it reads is an observation; the registers it writes are unmasked
    rr, ww = i.regs_access()
    for rid in rr:
        loc = GPR.get(i.reg_name(rid))
        if loc is not None and rid != X.X86_REG_ESP and regs[loc[0]] & (loc[2] << loc[1]): return regs, stack, ft, True
    if ID in (X.X86_INS_CALL, X.X86_INS_LCALL, X.X86_INS_INT, X.X86_INS_SYSCALL, X.X86_INS_SYSENTER) and any(stack): return regs, stack, ft, True
    for rid in ww:
        loc = GPR.get(i.reg_name(rid))
        if loc is not None and rid != X.X86_REG_ESP: regs = _w(regs, loc, 0)
    return regs, stack, (0 if keep == 0 else ft), False


def taint_observed(ins, objs, site, mask, lahf):
    """True when the may-undefined bits of the flag word copied at `site` reach an observing consumer"""
    r0 = ins[site]
    if lahf: regs, stack = (mask << 8, 0, 0, 0, 0, 0, 0, 0), ()
    else: regs, stack = (0,) * 8, (mask,)
    state = {site + r0[1]: (regs, stack, 0)}
    work = [site + r0[1]]
    while work:
        a = work.pop()
        regs, stack, ft = state[a]
        if a not in ins: continue
        row = ins[a]
        regs, stack, ft, obs = tstep(objs[a], row, regs, stack, ft)
        if obs: return True
        kind, tgt = row[2], row[3]
        if kind == KEND: succ = ()
        elif kind == KCALL: succ = (a + row[1],)
        else:
            succ = [a + row[1]] if kind in (KSEQ, KJCC) else []
            if kind in (KJMP, KJCC) and tgt is not None: succ.append(tgt)
        if kind in (KEND, KJMP) and (kind == KEND or tgt not in ins):          # the value leaves the entry: ret, indirect jmp, tail jmp
            if any(regs) or any(stack): return True
        if not (any(regs) or any(stack) or ft): continue
        for s in succ:
            if s not in ins:
                if any(regs) or any(stack): return True          # falls into, or jumps to, another entry
                continue
            n = state.get(s)
            if n is None: state[s] = (regs, stack, ft); work.append(s)
            else:
                if len(n[1]) != len(stack): return True
                j = (tuple(x | y for x, y in zip(n[0], regs)), tuple(x | y for x, y in zip(n[1], stack)), n[2] or ft)
                if j != n: state[s] = j; work.append(s)
    return False


def pushf_filter(img, ranges, ins, red):
    """drop the red rows of the pushf class whose may-undefined bits are never observed"""
    sites = {}
    for r in red:
        if r[3] == 'pushf': sites.setdefault(r[0], []).append(r)
    if not sites: return red
    objs = {}
    for lo, hi in ranges:
        code = img.read(lo, hi)
        if code is None: continue
        for i in _md.disasm(code, lo): objs[i.address] = i
    keep = [r for r in red if r[3] != 'pushf']
    for a, rows in sorted(sites.items()):
        lahf = objs[a].id == X.X86_INS_LAHF
        mask = 0
        for r in rows:
            if lahf and r[1] == 'of': continue
            mask |= 1 << (WORDBIT[r[1]] + (0 if not lahf else 0))
        if mask and taint_observed(ins, objs, a, mask, lahf): keep += rows
    return sorted(keep)


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


def load_universe(path):
    """-> {entry: (kind, [flags])}"""
    u = {}
    for l in open(path):
        if l.startswith('#') or not l.strip(): continue
        p = l.rstrip('\n').split('\t')
        u[int(p[0], 16)] = (p[1], p[3].split(',') if len(p) > 3 else [])
    return u


_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_md.detail = True
MEMO = {}          # address -> decoded instruction: valid for ONE image (cleared by the entry points that load an image)


def decode(img, lo, hi):
    """-> ([(addr, size, kind, target, reads, keep, undef, cls, mnemonic, covered)], undecodable address or None)"""
    code = img.read(lo, hi)
    if code is None: return [], lo
    out, a = [], lo
    for ins in _md.disasm(code, lo):
        if ins.address + ins.size > hi: break
        r = MEMO.get(ins.address)
        if r is None:
            e = effect(ins)
            tgt = None
            op = ins.operands[0] if ins.operands else None
            if ins.id in CALL_IDS:
                kind = KCALL
                tgt = op.imm if op is not None and op.type == X.X86_OP_IMM and ins.id == X.X86_INS_CALL else None
            elif ins.id in END_IDS: kind = KEND
            elif ins.id in JMP_IDS:
                kind = KJMP if op is not None and op.type == X.X86_OP_IMM else KEND
                tgt = op.imm if kind == KJMP else None
            elif X.X86_GRP_JUMP in ins.groups or X.X86_GRP_BRANCH_RELATIVE in ins.groups:
                kind = KJCC; tgt = op.imm if op is not None and op.type == X.X86_OP_IMM else None
            else: kind = KSEQ
            rd, keep, undef = e if e is not None else (0, FULL, 0)
            r = MEMO[ins.address] = (ins.address, ins.size, kind, tgt, rd, keep, undef, classof(ins), ins.mnemonic, e is not None)
        out.append(r)
        a = ins.address + ins.size
    return out, (None if a == hi else a)


def analyse(img, entry, ranges, st0, entries=(), final=False):
    """Forward flow over the body of one entry from in-state st0. -> (n instructions, contributions [(target entry, state)], red rows
    [(site, flag, origins, class, mnemonic)] (only when final), reads, undecodable ranges)"""
    ins, bad = {}, 0
    for lo, hi in ranges:
        rows, b = decode(img, lo, hi)
        if b is not None: bad += 1
        for r in rows: ins[r[0]] = r
    state = {entry: st0} if entry in ins and st0 else {}
    work = [entry] if state else []
    contrib = []
    while work:
        a = work.pop()
        r = ins[a]
        st = state[a]
        _, size, kind, tgt, rd, keep, undef, cls, mn, cov = r
        o = ALL_C if kind == KCALL else (st & keep) | undef
        if kind == KCALL and tgt is not None and tgt in entries and tgt not in ins: contrib.append((tgt, st))
        succ = []
        if kind in (KSEQ, KCALL, KJCC): succ.append(a + size)
        if kind in (KJMP, KJCC) and tgt is not None: succ.append(tgt)
        for s in succ:
            if s not in ins:
                if s in entries and kind != KCALL: contrib.append((s, o if s == a + size else st))
                continue
            n = state.get(s)
            if n is None or n | o != n:
                state[s] = o if n is None else n | o
                work.append(s)
    red, reads = [], 0
    for a in sorted(state):
        r = ins[a]
        if not r[9]:
            if final: red.append((a, '-', 0, 'no-form', r[8]))
            continue
        rd = r[4]
        if not rd: continue
        st = state[a]
        for k in range(NF):
            if rd >> k & 1:
                reads += 1
                org = st >> (3 * k) & 7
                if org and final: red.append((a, FLAGS[k], org, r[7], r[8]))
    if final and red: red = pushf_filter(img, ranges, ins, red)
    return len(ins), sorted(set(contrib)), red, reads, bad


G = {}


def _init(path, base, rows, entries):
    MEMO.clear()
    G['img'] = Image(path, base) if isinstance(path, str) else path; G['rows'] = rows; G['ents'] = entries


def _work(task):
    img, rows, ents = G['img'], G['rows'], G['ents']
    final, items = task
    out = []
    for e, st in items:
        n, contrib, red, reads, bad = analyse(img, e, rows[e], st, ents, final)
        out.append((e, n, contrib, red, reads, bad))
    return out


def solve(path, base, rows, uni, jobs=4):
    """-> (in-state per entry (None: unreached), results of the final pass [(entry, n, contributions, red rows, reads, undecodable)])"""
    ents = frozenset(rows)
    chunk = lambda lst: [lst[i:i + 64] for i in range(0, len(lst), 64)]
    pool = multiprocessing.Pool(jobs, _init, (path, base, rows, ents)) if jobs > 1 else None
    if pool is None:
        _init(path, base, rows, ents)
        if not isinstance(path, str): G['img'] = path          # jobs = 1: an image object (the self-test)
    run = (lambda t: pool.imap(_work, t)) if pool is not None else (lambda t: map(_work, t))
    try:
        # pass 0: the direct edges (they do not depend on the state)
        preds = {}
        for part in run([(False, [(e, ALL_E) for e in c]) for c in chunk(sorted(rows))]):
            for e, n, contrib, red, reads, bad in part:
                for t, st in contrib: preds[t] = True
        inst = {}
        for e in sorted(rows):
            kind, fl = uni.get(e, ('decomp', ['ptr']))
            unknown = 'ptr' in fl or kind not in ('decomp', 'call', 'tail') or e not in preds
            inst[e] = ALL_E if unknown else None
        dirty = sorted(e for e in rows if inst[e])
        while dirty:
            nxt = {}
            for part in run([(False, [(e, inst[e]) for e in c]) for c in chunk(dirty)]):
                for e, n, contrib, red, reads, bad in part:
                    for t, st in contrib:
                        cur = inst[t]
                        if cur is None or cur | st != cur:
                            inst[t] = st if cur is None else cur | st
                            nxt[t] = True
            dirty = sorted(nxt)
        res = []
        for part in run([(True, [(e, inst[e]) for e in c if inst[e]]) for c in chunk(sorted(rows))]):
            res += part
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    return inst, res


def summary(ents, inst, res):
    cls = {c: 0 for c in CLASSES}
    org = {}
    ntot = reads = bad = 0
    red = []
    for e, n, contrib, rd, rds, b in res:
        ntot += n; reads += rds; bad += b
        red += [(e,) + r for r in rd]
    sites, uncovered = {}, {}
    for e, a, f, o, c, m in red:
        cls[c] += 1
        sites[a] = True
        if c == 'no-form':
            uncovered[m] = uncovered.get(m, 0) + 1
            continue
        k = ''.join(n for n, b in (('E', E_), ('C', C_), ('U', U_)) if o & b)
        org[k] = org.get(k, 0) + 1
    return {'entries': len(ents), 'unreached_entries': sum(1 for e in ents if inst[e] is None), 'instructions': ntot, 'flag_reads': reads,
            'red_flag_reads': len(red), 'red_sites': len(sites), 'red_by_class': cls, 'red_by_origin': dict(sorted(org.items())),
            'uncovered_by_mnemonic': dict(sorted(uncovered.items())), 'undecodable_ranges': bad}, red


def main(argv):
    path, facts = argv[0], argv[1]
    base, rows = load_ranges(os.path.join(facts, 'ranges.tsv'))
    uni = load_universe(os.path.join(facts, 'universe.tsv'))
    ents = sorted(rows)
    inst, res = solve(path, base, rows, uni, int(os.environ.get('JOBS', '4')))
    s, red = summary(ents, inst, res)
    out = dict(s, format='fnvnx-gate/1', name='flags', status='green' if not red and not s['undecodable_ranges'] else 'red',
               certified=s['flag_reads'] - s['red_flag_reads'], universe=s['flag_reads'],
               uncertified=['%08x:%s' % (r[1], r[2]) for r in red[:50]],
               pushf_sites=sorted({'%08x' % r[1] for r in red if r[4] == 'pushf'}),
               first_red=[{'entry': '%08x' % e, 'site': '%08x' % a, 'flag': f, 'origin': o, 'class': c, 'mnemonic': m} for e, a, f, o, c, m in red[:50]])
    d = os.environ.get('GATE_OUT')
    if d:
        os.makedirs(d, exist_ok=True)
        json.dump(out, open(os.path.join(d, 'flags.json'), 'w'), indent=1, sort_keys=True)
    print(json.dumps(s, sort_keys=True))
    return 0 if out['status'] == 'green' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
