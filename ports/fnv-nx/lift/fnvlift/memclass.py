"""R9: memory-ordering class of the accesses of every lifted instruction.

Every guest memory access is ORDERED (rt/fnvl_mem.h: ldar/stlr, dmb on the unaligned and 16-byte paths) unless tools/memclass.py PROVED it
thread-local or read-only (PLAIN). The class is bound by C++ scope, not by rewriting text: the macros M8..MX of the handlers mean
fnvl_m<..., FNVL_R9>, FNVL_R9 is 1 (ordered) at namespace scope, and the text of an instruction that is all-plain is wrapped in
`{ enum { FNVL_R9 = 0 }; ... }`. A class is an INPUT of the lift: the plain set comes from a file the caller names (PlainSet(path); from_env is the
one loader core.MEMCLASS uses). No class known (variable unset, or an instruction not in the set) = ORDERED = sound; that is the measurement default,
it does not change the semantics of anything.

MEMCLASS object (core.MEMCLASS): `.plain(insn_addr) -> bool`: ALL memory accesses of that instruction, explicit and implicit, are class (a).
Sources: env FNVL_MEMCLASS=<plain_insns.bin> (sorted little-endian u32 addresses, written by tools/memclass.py); `core.MEMCLASS = obj` by hand.

Atomic read-modify-write on a non-plain location (rule decided with L1): `xchg` with a memory operand (implicitly locked) and every LOCK-prefixed RMW
(add adc sub sbb and or xor inc dec neg not xadd cmpxchg cmpxchg8b bts btr btc) become ONE `__atomic_*` operation with seq_cst (GCC: ldaxr/stlxr loop,
build with -mno-outline-atomics) + `dmb ish`. The operation needs a naturally aligned location; alignment is PROVED statically (const_address): the
address is a constant by in-block constant propagation, constant = 0 mod width (a 1-byte operand is aligned by construction). Not proven ->
Unsupported (the entry is a lift error). The flags and register results of a LOCK form come from the handler of the plain form, which runs in class 2
(`{ enum { FNVL_R9 = 2 }; ... }`): its read of the memory operand yields the old value the atomic operation returned, its store is already done."""
import bisect
import os
from array import array

from capstone import x86 as X


M32 = 0xffffffff


class PlainSet:
    def __init__(self, path):
        self.a = array('I')
        with open(path, 'rb') as f:
            self.a.frombytes(f.read())
        self.path = path

    def plain(self, addr):
        i = bisect.bisect_left(self.a, addr)
        return i < len(self.a) and self.a[i] == addr

    def __len__(self):
        return len(self.a)


class AllPlain:
    def plain(self, addr):
        return True


def from_env():
    p = os.environ.get('FNVL_MEMCLASS')
    return PlainSet(p) if p else None


# ---- static alignment proof ----
_FLOW = (X.X86_GRP_JUMP, X.X86_GRP_CALL, X.X86_GRP_RET, X.X86_GRP_INT, X.X86_GRP_IRET, X.X86_GRP_BRANCH_RELATIVE)


def _targets(cx):
    """addresses control can reach other than by falling through from the previous instruction (cached per Ctx): the function entry, jump / branch /
    loop targets, jump-table entries, every relocated code pointer of the range (indirect jumps)"""
    t = getattr(cx, '_r9_targets', None)
    if t is None:
        t = {cx.start}
        for a, i in cx.insns.items():
            if i.operands and i.operands[0].type == X.X86_OP_IMM and (i.mnemonic.split(' ')[-1][0] == 'j' or i.mnemonic.split(' ')[-1].startswith('loop')):
                t.add(i.operands[0].imm & M32)
        for ent in cx.tables.values():
            t.update(ent)
        t.update(cx.taken)
        cx._r9_targets = t
    return t


def _label(cx, a):
    """a is a label of the lifted function (control can arrive other than by falling through from its predecessor): static targets, return addresses
    of calls, resume points of raising instructions (ContinueExecution), universe entries"""
    return a in _targets(cx) or a in cx.callrets or a in cx.raises or a in cx.uent


def _pred(cx, a):
    """the unique decoded instruction ending exactly at a, or None"""
    p = getattr(cx, '_r9_pred', None)
    if p is None:
        p = {}
        for x, i in cx.insns.items():
            p.setdefault(x + i.size, []).append(i)
        cx._r9_pred = p
    c = p.get(a, [])
    return c[0] if len(c) == 1 else None


def const_address(cx, ins, op):
    from . import core
    """the constant address of memory operand op of the instruction at cx.a, or None when not proven: absolute disp32 (no base, no index), or
    base = r32 defined in the same basic block by `mov r32, imm32` with no other write of that register and no label (nor flow instruction, nor
    raising instruction: ContinueExecution may rewrite the context) between the definition and the use. Assumption: only the instructions of
    the lifter's own decode (cx.insns, cx.tables, cx.taken, cx.callrets, cx.raises, cx.uent) transfer control."""
    m = op.mem
    if ins.addr_size != 4 or m.index or (m.segment and m.segment != X.X86_REG_DS):
        return None
    if not m.base:
        return m.disp & M32
    breg = core.reg(ins.reg_name(m.base))[0]
    cur = cx.a
    while True:
        if _label(cx, cur):
            return None
        p = _pred(cx, cur)
        if p is None or p.address in cx.raises or any(g in _FLOW for g in p.groups):
            return None
        if breg in {core.reg(p.reg_name(r))[0] for r in p.regs_access()[1] if p.reg_name(r) in core.SUB}:
            o = p.operands
            if p.mnemonic == 'mov' and o[0].type == X.X86_OP_REG and core.reg(p.reg_name(o[0].reg)) == (breg, 0, 32) and o[1].type == X.X86_OP_IMM:
                return ((o[1].imm & M32) + m.disp) & M32
            return None
        cur = p.address


def aligned(cx, ins, op):
    """the memory operand op is naturally aligned by static proof"""
    w = op.size
    return w == 1 or (w in (2, 4, 8) and (lambda c: c is not None and c % w == 0)(const_address(cx, ins, op)))


# ---- atomic forms ----
# Rule (lead decision, closes the class "LOCK / xchg at any address"): x86 makes a LOCKed RMW (xchg with memory locks implicitly) atomic against every other access of
# every thread, aligned or split. Path (i) address = 0 mod width: one __atomic_* operation + `dmb ish`; path (ii) otherwise: fnvl_world_stop(); plain RMW; `dmb ish`;
# fnvl_world_resume() (rt/fnvl_mem.h has the contract). The path is chosen at run time by the address unless const_address PROVES it aligned (then (ii) is not emitted).
# bt/bts/btr/btc with a register bit offset address the dword (word) at ea + (idx >> log2(bits))*bytes: that effective address is computed first, then the same two paths.
_FETCH = {'add': 'FNVL_ADD', 'sub': 'FNVL_SUB', 'and': 'FNVL_AND', 'or': 'FNVL_OR', 'xor': 'FNVL_XOR', 'xadd': 'FNVL_ADD'}
_CAS = {'adc': '(u)(o+(u)(SRC)+cf)', 'sbb': '(u)(o-(u)(SRC)-cf)', 'neg': '(u)(0-o)'}
_BT = {'bts': ('FNVL_OR', '%s'), 'btr': ('FNVL_AND', '~%s'), 'btc': ('FNVL_XOR', '%s')}
LOCK_FORMS = frozenset(_FETCH) | frozenset(_CAS) | frozenset(_BT) | {'inc', 'dec', 'not', 'cmpxchg', 'cmpxchg8b'}


def _xchg(cx, ins):
    """xchg r, m -> C text, or None when the instruction has no memory operand"""
    ops = cx.ops
    mi = [i for i, o in enumerate(ops) if o.type == X.X86_OP_MEM]
    if not mi:
        return None
    m, r = ops[mi[0]], ops[1 - mi[0]]
    s = m.size * 8
    return '{ u32 t1=%s; u32 t0=fnvl_rmw<u%d,FNVL_XCHG,%s>(%s,(u%d)t1); %s }' % (cx.rd(ins, r), s, 'true' if aligned(cx, ins, m) else 'false', cx.ea(ins, m), s, cx.wr(ins, r, 't0'))


def _lock(cx, ins, L):
    """lock-prefixed RMW (memory destination ops[0]) -> the lines: the operation on both paths (old value = FNVL_LK), then the handler's own text in class 2"""
    ops, mn = cx.ops, cx.m
    m = ops[0]
    n = m.size * 8
    ty = 'u%d' % n
    pre = ''                                                  # declarations of the effective address when it is not the operand's own
    ea = cx.ea(ins, m)
    pv = aligned(cx, ins, m)
    if mn in _BT and ops[1].type != X.X86_OP_IMM:             # bit-string addressing (f_int.bitstr): the register index selects the unit
        sh = {16: 4, 32: 5}[n]
        pre = 's32 FNVL_I=(s32)(s%d)%s; ' % (n, cx.rd(ins, ops[1]))
        ea = '(%s+(u32)((FNVL_I>>%d)*%d))' % (ea, sh, n // 8)
        bit = '((%s)1<<(FNVL_I&%d))' % (ty, n - 1)
        pv = False
    elif mn in _BT:
        bit = '((%s)1<<((%s)(%s)&%d))' % (ty, ty, cx.rd(ins, ops[1], m.size), n - 1)
    src = '(%s)(%s)' % (ty, cx.rd(ins, ops[1], m.size)) if len(ops) > 1 else None
    pr = 'true' if pv else 'false'
    if mn in _FETCH:
        e = 'fnvl_rmw<%s,%s,%s>(FNVL_AD,%s)' % (ty, _FETCH[mn], pr, src)
    elif mn in ('inc', 'dec'):
        e = 'fnvl_rmw<%s,%s,%s>(FNVL_AD,1)' % (ty, 'FNVL_ADD' if mn == 'inc' else 'FNVL_SUB', pr)
    elif mn == 'not':
        e = 'fnvl_rmw<%s,FNVL_XOR,%s>(FNVL_AD,(%s)~0)' % (ty, pr, ty)
    elif mn in _BT:
        op, f = _BT[mn]
        e = 'fnvl_rmw<%s,%s,%s>(FNVL_AD,(%s)(%s))' % (ty, op, pr, ty, f % bit)
    elif mn == 'cmpxchg':
        e = 'fnvl_cas<%s,%s>(FNVL_AD,(%s)eax,%s)' % (ty, pr, ty, src)
    elif mn == 'cmpxchg8b':
        e = 'fnvl_cas<u64,%s>(FNVL_AD,((u64)edx<<32)|eax,((u64)ecx<<32)|ebx)' % pr
        ty = 'u64'
    else:
        e = 'fnvl_rmwf<%s,%s>(FNVL_AD,[&](%s o){ return %s; })' % (ty, pr, ty, _CAS[mn].replace('(u)', '(%s)' % ty).replace('SRC', cx.rd(ins, ops[1], m.size) if len(ops) > 1 else '0'))
    return ['{ enum { FNVL_R9 = 2 }; %su32 FNVL_AD = %s; %s FNVL_LK = %s;' % (pre, ea, ty, e)] + L + ['}']


def apply(cx, ins, n0, mc=None):
    """the lines of the instruction at cx.a (cx.L[n0:]) with its class bound: -> the new list of lines"""
    from . import f_sys
    L = cx.L[n0:]
    if not L:
        return L
    if mc is not None and mc.plain(cx.a):
        return ['{ enum { FNVL_R9 = 0 };'] + L + ['}']
    if cx.m == 'xchg':
        t = _xchg(cx, ins)
        if t is not None:
            return [t]
    elif X.X86_PREFIX_LOCK in ins.prefix and f_sys.lock_ok(ins, cx.m):
        assert cx.m in LOCK_FORMS, cx.m                  # f_sys.LOCKABLE minus xchg: the table is total
        return _lock(cx, ins, L)
    return L
