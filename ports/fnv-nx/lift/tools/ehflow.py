#!/usr/bin/env python3
"""Abstract interpretation of decoded x86-32 code for tools/ehtables.py (no names, strings or byte patterns: decoded instructions, relocations and the call graph).

Values (a register, a 4-byte stack slot):
  int                 a constant;  ('set', frozenset)  one of at most 8 constants (the join of paths)
  ('sp', o)           the address esp had o bytes above the esp of the entry of the root (the root's esp at entry points at the return address: arg k is at 4 + 4k)
  ('arg', R, k)       the value of incoming stack argument k of the root R (R = the entry the flow started at)
  ('in', r)           the value register r had at the entry of the analysed function (callee-saved proofs)
  ('mem', a)          a value loaded from the fixed address a;  ('fs0',) the value of fs:[0];  ('ld',) a value loaded from a stack slot the flow does not know
  ('xor', x, y)       x ^ y of two non-constant values
  None                unknown
State: regs (full register -> value), stack (aligned 4-byte slot offset -> value; a slot absent from the dict holds the root's incoming argument if root is set and the slot lies above the
return address), sp (the offset of esp or None), root, exp (an address inside the incoming-argument area has been formed: arguments are no longer tracked across calls).
A call: eax/ecx/edx die; ebx/esi/edi/ebp survive only if the direct callee is PROVEN to restore them (summary below); the stack facts below the arguments die; esp advances by the callee's
`ret n` if the callee is proven balanced, else it is unknown. A store through an esp/ebp-based address that the flow cannot place kills every stack fact.
Callee summary (R2) = (preserved registers, balance n or None, registers that leave the callee holding an esp-derived address (frame helpers: ebp = frame pointer)), by forward flow over the
callee's decoded code from its entry: a register is preserved iff at EVERY `ret` it holds its value at the entry (push/pop pairing at equal stack depth, or never written); balance iff every
`ret` executes at the entry's esp; a path that leaves decoded code (an unresolved indirect jump, a target not decoded) makes everything unproven; a call cycle makes the member unproven."""
import capstone
from capstone import x86 as cx
from collections import deque

M32 = 0xffffffff
FULL = {f: f for f in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp', 'esp')}
for _f, _s in {'eax': 'ax al ah', 'ebx': 'bx bl bh', 'ecx': 'cx cl ch', 'edx': 'dx dl dh', 'esi': 'si', 'edi': 'di', 'ebp': 'bp', 'esp': 'sp'}.items():
    for _x in _s.split(): FULL[_x] = _f
CALLEE = ('ebx', 'esi', 'edi', 'ebp')
SETMAX = 8


def load_keys():
    """{'dll!name': (convention, argument types)} of L1's keys.tsv (env FNVNX_KEYS, else keys.tsv beside the directory of FNVNX_MODULES)"""
    import os
    p = os.environ.get('FNVNX_KEYS') or os.path.join(os.path.dirname(os.path.dirname(os.environ.get('FNVNX_MODULES', ''))), 'keys.tsv')
    out = {}
    if os.path.isfile(p):
        for ln in open(p):
            f = ln.rstrip('\n').split('\t')
            if len(f) >= 7 and not ln.startswith('#') and not f[1].startswith('!'): out['%s!%s' % (f[1].lower(), f[2].lower())] = (f[4], f[6])
    return out


def sext(b): return b - (1 << 32) if b >= 1 << 31 else b


def cset(v):
    """the constants a value may be (frozenset) or None if it is not a constant / set of constants"""
    if isinstance(v, int): return frozenset((v,))
    if isinstance(v, tuple) and v and v[0] == 'set': return v[1]
    return None


def mkset(u): return next(iter(u)) if len(u) == 1 else ('set', frozenset(u))


LOADED = ('ld', 'fs0', 'mem')      # values read from memory: indistinguishable for the rules that use them (none treats a loaded value as a fresh stack address or a constant)


def vjoin(a, b):
    if a == b: return a
    if isinstance(a, tuple) and isinstance(b, tuple) and a[0] in LOADED and b[0] in LOADED: return ('ld',)
    sa = cset(a); sb = cset(b)
    if sa is not None and sb is not None:
        u = sa | sb
        return mkset(u) if len(u) <= SETMAX else None
    return None


class St:
    __slots__ = ('regs', 'stack', 'sp', 'root', 'exp')

    def __init__(s, regs, stack, sp, root, exp): s.regs = regs; s.stack = stack; s.sp = sp; s.root = root; s.exp = exp
    def cp(s): return St(dict(s.regs), dict(s.stack), s.sp, s.root, s.exp)
    def __eq__(s, o): return s.sp == o.sp and s.root == o.root and s.exp == o.exp and s.regs == o.regs and s.stack == o.stack


def sload(st, o):
    if o in st.stack: return st.stack[o]
    if st.root is not None and o >= 4 and o % 4 == 0: return ('arg', st.root, (o - 4) // 4)
    return None


def swrite(st, o, size, v):
    if size == 4 and o % 4 == 0: st.stack[o] = v; return
    for w in range(o & ~3, (o + size + 3) & ~3, 4): st.stack[w] = None


def killstack(st): st.stack = {}; st.root = None


def joinst(p, q):
    regs = {}
    for k in set(p.regs) | set(q.regs):
        v = vjoin(p.regs.get(k), q.regs.get(k))
        if v is not None: regs[k] = v
    stack = {}
    for k in set(p.stack) | set(q.stack): stack[k] = vjoin(sload(p, k), sload(q, k))
    return St(regs, stack, p.sp if p.sp == q.sp else None, p.root if p.root == q.root else None, p.exp or q.exp)


UNKNOWN = lambda: St({}, {}, None, None, True)


def alu(mn, a, b):
    if a is None or b is None: return None
    if mn in ('add', 'sub'):
        g = 1 if mn == 'add' else -1
        if isinstance(a, tuple) and a[0] == 'sp' and isinstance(b, int): return ('sp', a[1] + g * sext(b))
        if isinstance(a, int) and isinstance(b, int): return (a + g * b) & M32
        sa = cset(a)
        if sa is not None and isinstance(b, int): return mkset({(x + g * b) & M32 for x in sa})
        return None
    if mn == 'xor':
        if isinstance(a, int) and isinstance(b, int): return a ^ b
        if a == b: return 0
        if (isinstance(a, tuple) and a[0] == 'set') or (isinstance(b, tuple) and b[0] == 'set'): return None
        x, y = sorted((a, b), key=repr)
        return ('xor', x, y)
    if mn in ('or', 'and'):
        if isinstance(a, int) and isinstance(b, int): return (a | b) if mn == 'or' else (a & b)
        if mn == 'or' and M32 in (a, b): return M32
        if mn == 'and' and 0 in (a, b): return 0
        return None
    return None


class Flow:
    """mixin of ehtables.EH: needs s.X, s.dis, s.extent, s.preds, s.seeded, s.relocvals, s.pe_entries"""

    def flow_init(s):
        s.sm = {}; s.ip = set(); s.ev = None; s.keys = None

    # ---- operands
    def addr(s, ins, op, st):
        m = op.mem
        if m.segment or m.index: return None
        if not m.base: return ('abs', m.disp & M32)
        nm = ins.reg_name(m.base)
        v = (('sp', st.sp) if st.sp is not None else None) if nm == 'esp' else st.regs.get(FULL.get(nm))
        if isinstance(v, tuple) and v[0] == 'sp': return ('sp', v[1] + m.disp)
        if isinstance(v, int): return ('abs', (v + m.disp) & M32)
        return None

    def rd(s, ins, op, st):
        t = op.type
        if t == cx.X86_OP_IMM: return op.imm & M32
        if t == cx.X86_OP_REG:
            if op.size != 4: return None
            f = FULL.get(ins.reg_name(op.reg))
            if f == 'esp': return ('sp', st.sp) if st.sp is not None else None
            return st.regs.get(f)
        if t == cx.X86_OP_MEM:
            if op.size != 4: return None
            m = op.mem
            if m.segment == cx.X86_REG_FS and not m.base and not m.index: return ('fs0',) if m.disp == 0 else None
            a = s.addr(ins, op, st)
            if a is None: return ('ld',)      # a load through a pointer the flow cannot place: some value read from memory (never a fresh stack address)
            if a[0] == 'sp':
                if a[1] % 4: return None
                v = sload(st, a[1])
                return ('ld',) if v is None else v
            return ('mem', a[1])
        return None

    def wr(s, ins, op, st, v):
        if isinstance(v, tuple) and v and v[0] == 'sp' and v[1] >= 4 and not (op.type == cx.X86_OP_REG and FULL.get(ins.reg_name(op.reg)) == 'esp'):
            st.exp = True      # an address inside the incoming-argument area escapes: a call may modify the arguments
        if op.type == cx.X86_OP_REG:
            f = FULL.get(ins.reg_name(op.reg))
            if f is None: return
            if f == 'esp':
                st.sp = v[1] if op.size == 4 and isinstance(v, tuple) and v[0] == 'sp' else None; return
            if v is None or op.size != 4: st.regs.pop(f, None)
            else: st.regs[f] = v
        elif op.type == cx.X86_OP_MEM:
            m = op.mem
            if m.segment == cx.X86_REG_FS and not m.base and not m.index:
                if m.disp == 0 and s.ev is not None: s.ev.append((ins.address, v))
                return
            if m.segment: return
            a = s.addr(ins, op, st)
            if a is None:
                if m.base and ins.reg_name(m.base) in ('esp', 'ebp'): killstack(st)
                return
            if a[0] == 'sp': swrite(st, a[1], max(op.size, 1), v if op.size == 4 else None)

    # ---- one instruction
    def step(s, ins, st):
        mn = ins.mnemonic; ops = ins.operands; n = len(ops)
        if mn == 'push' and n == 1:
            v = s.rd(ins, ops[0], st) if ops[0].size == 4 else None
            if st.sp is None: return
            if isinstance(v, tuple) and v[0] == 'sp' and v[1] >= 4: st.exp = True
            st.sp -= 4; swrite(st, st.sp, 4, v); return
        if mn == 'pop' and n == 1:
            if st.sp is None: s.wr(ins, ops[0], st, ('ld',) if ops[0].size == 4 else None); return
            v = sload(st, st.sp); v = ('ld',) if v is None else v
            st.sp += 4; s.wr(ins, ops[0], st, v if ops[0].size == 4 else None); return
        if mn == 'call': s.docall(ins, st); return
        if mn == 'leave':
            e = st.regs.get('ebp')
            if isinstance(e, tuple) and e[0] == 'sp': v = sload(st, e[1]); st.sp = e[1] + 4; st.regs['ebp'] = ('ld',) if v is None else v
            else: st.sp = None; st.regs.pop('ebp', None)
            return
        if mn in ('pushfd', 'pushf'):
            if st.sp is not None: st.sp -= 4; st.stack[st.sp] = None
            return
        if mn in ('popfd', 'popf'):
            if st.sp is not None: st.sp += 4
            return
        if mn in ('pushal', 'pushad'):
            if st.sp is not None:
                for k in range(8): st.sp -= 4; st.stack[st.sp] = None
            return
        if mn in ('popal', 'popad'):
            if st.sp is not None: st.sp += 32
            for r in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp'): st.regs.pop(r, None)
            return
        if mn == 'mov' and n == 2: s.wr(ins, ops[0], st, s.rd(ins, ops[1], st)); return
        if mn == 'lea' and n == 2 and ops[1].type == cx.X86_OP_MEM and ops[0].size == 4:
            a = s.addr(ins, ops[1], st) if not ops[1].mem.segment else None
            s.wr(ins, ops[0], st, None if a is None else (('sp', a[1]) if a[0] == 'sp' else a[1])); return
        if mn in ('xor', 'sub') and n == 2 and ops[0].type == ops[1].type == cx.X86_OP_REG and ops[0].reg == ops[1].reg and ops[0].size == 4: s.wr(ins, ops[0], st, 0); return
        if mn in ('add', 'sub', 'xor', 'or', 'and') and n == 2 and ops[0].size == 4:
            s.wr(ins, ops[0], st, alu(mn, s.rd(ins, ops[0], st), s.rd(ins, ops[1], st))); return
        if mn in ('inc', 'dec') and n == 1 and ops[0].size == 4:
            s.wr(ins, ops[0], st, alu('add' if mn == 'inc' else 'sub', s.rd(ins, ops[0], st), 1)); return
        if mn == 'neg' and n == 1 and ops[0].size == 4:
            v = s.rd(ins, ops[0], st); s.wr(ins, ops[0], st, (-v) & M32 if isinstance(v, int) else None); return
        if mn == 'not' and n == 1 and ops[0].size == 4:
            v = s.rd(ins, ops[0], st); s.wr(ins, ops[0], st, (~v) & M32 if isinstance(v, int) else None); return
        if mn == 'xchg' and n == 2 and ops[0].type == ops[1].type == cx.X86_OP_REG and ops[0].size == 4:
            a = s.rd(ins, ops[0], st); b = s.rd(ins, ops[1], st); s.wr(ins, ops[0], st, b); s.wr(ins, ops[1], st, a); return
        try: wr = [ins.reg_name(r) for r in ins.regs_access()[1]]
        except capstone.CsError: st.regs.clear(); st.sp = None; killstack(st); return
        for nm in wr:
            f = FULL.get(nm)
            if f == 'esp': st.sp = None
            elif f: st.regs.pop(f, None)
        for op in ops:
            if op.type == cx.X86_OP_MEM and op.access & capstone.CS_AC_WRITE and not op.mem.segment:
                a = s.addr(ins, op, st)
                if a is None:
                    if op.mem.base and ins.reg_name(op.mem.base) in ('esp', 'ebp'): killstack(st)
                elif a[0] == 'sp': swrite(st, a[1], max(op.size, 1), None)

    def docall(s, ins, st):
        op = ins.operands[0]; eff = s.summary(op.imm & M32) if op.type == cx.X86_OP_IMM else s.import_effect(op)
        sp0 = st.sp
        for r in ('eax', 'ecx', 'edx'): st.regs.pop(r, None)
        for r in CALLEE:
            if eff and r in eff[2] and sp0 is not None: st.regs[r] = ('sp', sp0 - 4 + eff[2][r])
            elif not (eff and r in eff[0]): st.regs.pop(r, None)
        if st.exp: killstack(st)
        else: st.stack = {k: v for k, v in st.stack.items() if k >= 4}
        st.sp = sp0 + eff[1] if sp0 is not None and eff and eff[1] is not None else None

    # ---- forward solver over a set of decoded instructions
    def solve(s, nodes, init, cut=frozenset()):
        X = s.X; IN = dict(init); work = deque(sorted(init))
        while work:
            x = work.popleft(); st = IN[x].cp(); ins = s.dis(x)
            if ins is None: st = UNKNOWN()
            else: s.step(ins, st)
            sz, mn, ft, fall, tg = X.insns[x]
            for t in list(tg) + ([x + sz] if fall else []):
                if t not in nodes or t in cut: continue
                if t not in IN: IN[t] = st.cp(); work.append(t)
                else:
                    m = joinst(IN[t], st)
                    if m != IN[t]: IN[t] = m; work.append(t)
        return IN

    # ---- imports: the declared machine's contract (L1 keys.tsv: calling convention and argument types of every imported function), Win32 as documented (ASSUMPTIONS.json 3):
    # a function of the platform preserves ebx/esi/edi/ebp; a stdcall callee pops its arguments, a cdecl or vararg callee does not
    def import_effect(s, op):
        m = op.mem if op.type == cx.X86_OP_MEM else None
        if m is None or m.base or m.index or m.segment: return None
        nm = s.X.imports.get(m.disp & M32)
        if nm is None: return None
        if s.keys is None: s.keys = load_keys()
        r = s.keys.get(nm.lower())
        if r is None: return None
        conv, types = r
        if conv in ('cdecl', 'vararg'): bal = 0
        elif conv == 'stdcall': bal = sum(8 if t in 'dq' else 4 for t in types)
        else: return None
        return (frozenset(CALLEE), bal, {})

    # ---- R2: callee summaries
    def calltargets(s, n):
        out = set()
        for x in s.extent(n):
            if s.X.insns[x][1] == 'call':
                ins = s.dis(x)
                if ins is not None and ins.operands and ins.operands[0].type == cx.X86_OP_IMM: out.add(ins.operands[0].imm & M32)
        return sorted(out)

    def summary(s, C):
        """-> (frozenset preserved, balance or None, {reg: esp-derived offset at ret}) or None (not decoded / in a call cycle)"""
        if C in s.sm: return s.sm[C]
        if C in s.ip or C not in s.X.insns: return None
        s.ip.add(C); stack = [[C, None, 0]]
        while stack:
            fr = stack[-1]
            if fr[1] is None: fr[1] = s.calltargets(fr[0])
            adv = False
            while fr[2] < len(fr[1]):
                t = fr[1][fr[2]]; fr[2] += 1
                if t in s.sm or t in s.ip or t not in s.X.insns: continue
                s.ip.add(t); stack.append([t, None, 0]); adv = True; break
            if adv: continue
            n = fr[0]; s.sm[n] = s.compute(n); s.ip.discard(n); stack.pop()
        return s.sm[C]

    def compute(s, C):
        X = s.X; nodes = s.extent(C)
        IN = s.solve(nodes, {C: St({r: ('in', r) for r in CALLEE}, {}, 0, None, False)})
        rets = []
        for x in sorted(nodes):
            sz, mn, ft, fall, tg = X.insns[x]
            if mn in ('retf', 'iret', 'iretd') or any(t not in X.insns for t in tg) or (fall and (x + sz) not in X.insns): return (frozenset(), None, {})
            if mn == 'jmp' and not tg:
                ins = s.dis(x)
                if ins is None: return (frozenset(), None, {})
                if ins.operands[0].type == cx.X86_OP_MEM and s.import_effect(ins.operands[0]) is not None: rets.append(x); continue      # tail call of an import: the import returns for this function
                if ins.operands[0].type != cx.X86_OP_IMM: return (frozenset(), None, {})      # a jump the decoded flow does not resolve leaves decoded code
            if mn == 'ret': rets.append(x)
        sts = []; ns = set()
        for x in rets:
            if x not in IN: continue
            ins = s.dis(x); sts.append(IN[x])
            if ins.mnemonic == 'jmp': ns.add(s.import_effect(ins.operands[0])[1])
            else: ns.add(ins.operands[0].imm & 0xffff if ins.operands else 0)
        if not sts: return (frozenset(CALLEE), None, {})
        pres = frozenset(r for r in CALLEE if all(st.regs.get(r) == ('in', r) for st in sts))
        bal = next(iter(ns)) if len(ns) == 1 and all(st.sp == 0 for st in sts) else None
        outs = {}
        for r in CALLEE:
            if r in pres: continue
            vs = {st.regs.get(r) for st in sts}
            if len(vs) == 1:
                v = next(iter(vs))
                if isinstance(v, tuple) and v[0] == 'sp': outs[r] = v[1]
        return (pres, bal, outs)

    # ---- state before an instruction: forward dataflow over its backward cone, cut at seeds
    def state_before(s, a):
        """the abstract state before the instruction at a. The cone = the instructions from which a is reachable over decoded edges, cut at seeded instructions (relocated code
        address, call target, PE entry or export), which start a root: sp = 0, the incoming stack arguments named ('arg', seed, k), nothing else known. Every non-seeded instruction of
        the cone has all its predecessors in the cone, so its state is the join over every path; an instruction no seed reaches is unknown."""
        if a in s.sb: return s.sb[a]
        X = s.X; s.relocvals(); cone = {a}; seed = set(); st = [a]
        while st:
            x = st.pop()
            if s.seeded(x): seed.add(x); continue
            for q in s.preds(x):
                if q not in cone and q in X.insns: cone.add(q); st.append(q)
        IN = s.solve(cone, {x: St({}, {}, 0, x, False) for x in seed}, frozenset(seed))
        r = s.sb[a] = IN.get(a) or UNKNOWN(); return r
