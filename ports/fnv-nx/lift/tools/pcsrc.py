#!/usr/bin/env python3
"""Closure completeness for code addresses a program computes from its own pc (typed address sources). A program can learn an address of its own image without a
relocation: (i) the return address that a `call` pushed, read as data instead of being consumed by `ret` or an esp adjustment past it (a get-pc thunk
`call X; X: mov r,[esp]; ret`, `call $+5; pop r`, __SEH_prolog-style helpers that move their return address); (ii) the FIP / FDP fields of an FPU environment
image (`fldz; fnstenv [esp-12]; pop r`, fnsave, fxsave). Rule R3 covers relocated words only, so these two are typed sources, like a relocated word.
RULE (per image; reached code = ranges.tsv; entries = universe.tsv; both are facts of universe.py / modlift.py):
  sources   every load of the return-address slot of an entry as data (any `mov/pop/push/ALU r,[slot]`, directly through esp or through a pointer to the slot);
            every load from the FIP/FDP offsets of an FPU environment image that the entry itself stored (stack slot at a known esp offset, `[disp32]`, or `[reg+disp]`
            until reg is redefined).
  value     return address + k is resolved against every direct caller (the return site of the call + k); a derived value is concrete when it is the result of a direct
            call whose callee returns its own return address in a register (summary of the callee: the get-pc thunk), or a FIP (addresses of the preceding x87
            instructions on the paths).
  consumers memory operand = named data (class mem); a control transfer (call/jmp reg, ret through a moved slot, push+ret) goes to a concrete address that must be an
            entry or a return site; compare = no consumer. RED, with the site listed: a store of the value, a pass to a callee (an argument slot live at a call, a
            register at a call whose callee is unknown or writes it, a tail jump), a merge with a non-constant (add/sub of a register, mixed join), an escape of the
            address of the return slot, any unknown use (an instruction that reads the value in a form this rule does not type), a control target that is not an entry,
            a return address read in an entry that has callers the facts do not enumerate (an indirectly reached entry).
  esp       callee-saved behaviour is not assumed. `esp-lost` counts the places where the tracked esp offset was lost (a call whose stack effect is not known, alloca):
            a read of the stack through a lost esp cannot be typed and is RED (`esp-lost-read`) only if the return-address slot of the entry is still live there.
Output: <outdir>/pcsources.tsv (entry, site, source, consumer, detail; red rows marked RED) and gate_pcsrc.json. Deterministic (sorted iteration)."""
import os, sys, json, bisect
from collections import Counter
import capstone
from capstone import x86 as X
M32 = 0xffffffff
R32 = {X.X86_REG_EAX: 'eax', X.X86_REG_EBX: 'ebx', X.X86_REG_ECX: 'ecx', X.X86_REG_EDX: 'edx', X.X86_REG_ESI: 'esi', X.X86_REG_EDI: 'edi', X.X86_REG_EBP: 'ebp', X.X86_REG_ESP: 'esp'}
for _n, _g in (('eax', ('AX', 'AL', 'AH')), ('ebx', ('BX', 'BL', 'BH')), ('ecx', ('CX', 'CL', 'CH')), ('edx', ('DX', 'DL', 'DH')), ('esi', ('SI',)), ('edi', ('DI',)), ('ebp', ('BP',))):
    for _x in _g: R32[getattr(X, 'X86_REG_' + _x)] = _n
CALLER_SAVED = ('eax', 'ecx', 'edx')
CALLER_SAVED_NEVER = ()
COPYLIKE = {'mov', 'push', 'pop', 'call', 'jmp', 'ret', 'cmp', 'test', 'nop', 'leave', 'movzx', 'movsx', 'int3', 'hlt', 'ud2', 'iret', 'iretd', 'retf', 'wait', 'fwait'}
COPYLIKE |= {'j' + c for c in ('o','no','b','ae','e','ne','be','a','s','ns','p','np','l','ge','le','g','ecxz','cxz')} | {'loop', 'loope', 'loopne'}
COPYLIKE |= {'set' + c for c in ('o','no','b','ae','e','ne','be','a','s','ns','p','np','l','ge','le','g')}
BLOCK_KINDS = {'tail', 'inner', 'eh-unwind', 'eh-catch', 'eh-cont', 'eh-throw'}
FP_ENV = {'fnstenv': 28, 'fstenv': 28, 'fnsave': 108, 'fsave': 108}      # 32-bit protected mode image: FIP at +12, FDP at +20
FP_FXSAVE = ('fxsave',)                                                   # FIP at +8, FDP at +16
FPU_CONTROL = {'fnstenv', 'fstenv', 'fnsave', 'fsave', 'fldenv', 'frstor', 'fnstcw', 'fstcw', 'fldcw', 'fnstsw', 'fstsw', 'fnclex', 'fclex', 'fninit', 'finit', 'fwait', 'wait', 'fxsave', 'fxrstor'}
CALL_KINDS = {'call', 'ptr', 'export', 'entry', 'decomp', 'tls', 'seh-handler', 'seh-filter'}   # entered by a call (or by the runtime through one): owns a return-address slot
INDIRECT_KINDS = {'ptr', 'export', 'entry', 'tls', 'seh-handler', 'seh-filter'}                  # callers the facts do not enumerate
RETS = ('ret', 'retf', 'iret', 'iretd')


def tr(v): return v == 'mix' or (isinstance(v, tuple) and v[0] in ('ra', 'fip', 'fdp', 'abs'))


T_CODE = ('code', 0)      # s1: a value that is, or was loaded from, a relocated in-span constant (an entry by rule R3)
T_S4 = ('s4', 0)          # s4: arithmetic on s1 / s4 (no rule makes its result an entry)
TYPES = (T_CODE, T_S4)


def j(a, b):
    """join of two values at a merge: equal stays; a pc-derived value on one path and anything else on the other is 'mix' (possibly pc-derived); two different stack pointers or data are untracked."""
    if a == b: return a
    if a == T_S4 or b == T_S4: return T_S4
    if a == T_CODE or b == T_CODE: return T_CODE if not (tr(a) or tr(b)) else 'mix'
    return 'mix' if (tr(a) or tr(b)) else None


class Img:
    def __init__(self, text, T0, entries, ranges, rd=None, rset=None):
        """text: bytes of the executable span at T0; entries: {addr: set(kinds)}; ranges: sorted list of reached [start, end) byte ranges (any overlap)."""
        self.text, self.T0, self.T1 = text, T0, T0 + len(text); self.entries = entries
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); self.md.detail = True
        mer = []
        for a, b in sorted(ranges):
            if mer and a <= mer[-1][1]: mer[-1][1] = max(mer[-1][1], b)
            else: mer.append([a, b])
        self.mer = mer; self.ms = [a for a, b in mer]; self.cache = {}
        self.rd = rd; self.rset = rset or set(); self.refs = None; self.tabslots = set(); self.visited = set(); self.jtargets = set(); self.pk = {}; self.wmemo = {}; self.calltg = {}; self.retsites = set(); self.callers = {}; self.summ = {}; self.busy = set(); self.rows = []; self.info = Counter()
    def reached(self, a):
        i = bisect.bisect_right(self.ms, a) - 1
        return i >= 0 and a < self.mer[i][1]
    def dec(self, a, keep=True):
        g = self.cache.get(a)
        if g is None:
            o = a - self.T0
            g = next(self.md.disasm(self.text[o:o + 15], a, 1), False) if 0 <= o < len(self.text) else False
            if keep:
                if len(self.cache) > 150000: self.cache.clear()      # bounded memory: a decoded instruction is a pure function of its address
                self.cache[a] = g
        return g


def scan_calls(img):
    """every reached direct call: callers {target: [site]}, return sites; decoded once over the reached ranges."""
    seen = set()
    for a0, b0 in img.mer:
        a = a0
        while a < b0:
            g = img.dec(a, False)
            if not g: a += 1; continue
            if a not in seen:
                seen.add(a)
                if g.mnemonic == 'jmp' and g.operands[0].type == X.X86_OP_MEM and g.operands[0].mem.index and not g.operands[0].mem.base and img.rd is not None:
                    t = g.operands[0].mem.disp & M32; n_ = 0
                    while t in img.rset and n_ < 4096:
                        try: w = img.rd(t)
                        except Exception: break
                        if not (img.T0 <= w < img.T1 and img.reached(w)): break
                        img.tabslots.add(t); t += 4; n_ += 1
                if g.mnemonic == 'call':
                    img.retsites.add(a + g.size)
                    o = g.operands[0]
                    if o.type == X.X86_OP_IMM:
                        t = o.imm & M32; img.callers.setdefault(t, []).append(a); img.calltg[a] = t
            a += g.size
    # ranges are per-entry and may start inside another entry's instruction; sweep every entry too
    for e in sorted(img.entries):
        a = e
        while img.reached(a):
            g = img.dec(a, False)
            if not g: break
            if a in seen: break
            seen.add(a)
            if g.mnemonic == 'call':
                img.retsites.add(a + g.size); o = g.operands[0]
                if o.type == X.X86_OP_IMM:
                    t = o.imm & M32; img.callers.setdefault(t, []).append(a); img.calltg[a] = t
            if g.mnemonic in ('jmp', 'ret', 'retf', 'iret', 'iretd', 'hlt', 'ud2', 'int3'): break
            a += g.size
    for t in img.callers: img.callers[t].sort()


def memkey(img, g, op, esp, regs):
    """-> ('stk', off) | ('abs', a) | ('reg', name, disp, gen?) | ('lost',) | None  for a memory operand."""
    m = op.mem
    if m.index: return None
    if m.base == 0 and m.disp is not None: return ('abs', m.disp & M32)
    b = R32.get(m.base)
    if b == 'esp': return ('stk', esp + m.disp) if esp is not None else ('lost',)
    v = regs.get(b)
    if isinstance(v, tuple) and v[0] == 'sp': return ('stk', v[1] + m.disp)
    return ('reg', b, m.disp)


def owner(img, e):
    """an entry entered by a call (or by the runtime through one) owns a return-address slot; every other kind is a block entered by a jump in its predecessors' context."""
    k = img.entries.get(e) or set()
    if not k or (k & CALL_KINDS) - {'ptr'}: return True
    if 'ptr' not in k: return False
    # a relocated pointer: a call-entered entry, unless every relocated word that holds it is a slot of a jump table (the entry is a case block of its parent)
    if img.refs is None:
        img.refs = {}
        for w in img.rset:
            try: img.refs.setdefault(img.rd(w), []).append(w)
            except Exception: pass
    sl = img.refs.get(e, [])
    return not (sl and all(w in img.tabslots for w in sl))


def analyse_entry(img, e, mode='owner'):
    """forward dataflow over the instructions of entry e. -> (events, summary). events: [(site, source, consumer, detail, red)];
    summary: {'regs': {reg: ('ra', k)}, 'pop': esp offset after return relative to the caller's esp before the call, or None}."""
    anyra = mode == 'possibly'      # a block with a predecessor we cannot see: every stack slot is possibly the return address
    if not anyra:
        if e in img.summ: return img.summ[e]
        if e in img.busy: return ([], {'regs': {}, 'pop': None})
        img.busy.add(e)
    ev = []; rets = []; seen_ev = set(); retscratch = []
    def emit(site, src, cons, detail, red):
        k = (site, src, cons, detail)
        if k not in seen_ev: seen_ev.add(k); ev.append((site, src, cons, detail, red))
    states = {e: ({}, {} if anyra else {0: ('ra', 0)}, 0, frozenset(), {})}      # regs, stack slots, esp offset, last x87 instructions, fpu env images {key: kind}
    work = [e]; steps = 0
    while work:
        a = work.pop(); steps += 1
        if steps > 20000: emit(a, '-', 'incomplete', 'dataflow step cap', True); break
        regs0, st0, esp0, fpl, fpe0 = states[a]; img.visited.add(a)
        if not img.reached(a): continue
        g = img.dec(a)
        if not g: continue
        regs = dict(regs0); st = dict(st0); esp = esp0; fpe = dict(fpe0); mn = g.mnemonic; ops = g.operands; nxt = a + g.size; succ = []; term = False
        def src_of(v):
            return 'ra' if isinstance(v, tuple) and v[0] in ('ra',) else 'fip' if isinstance(v, tuple) and v[0] in ('fip', 'fdp') else 'pc'
        def tracked(v): return isinstance(v, tuple) and v[0] in ('ra', 'fip', 'fdp', 'abs')
        def vsrc(v): return v[0] if isinstance(v, tuple) else 'pc'
        def consume(v, cls, detail='', red=False): emit(a, vsrc(v), cls, detail, red)
        def regval(op):
            if op.type != X.X86_OP_REG: return None
            n = R32.get(op.reg)
            if n is None: return None
            v = ('sp', esp) if n == 'esp' and esp is not None else regs.get(n)
            if v in TYPES and op.size != 4: return None      # a narrow read of an address is not an address
            if v is not None and op.size != 4: return 'mix'
            return v
        def load(key, a=a):
            """value read from memory key."""
            if key is None: return None
            if key[0] == 'lost':
                if any(isinstance(v, tuple) and v[0] == 'ra' for v in st.values()): emit(a, 'ra', 'esp-lost-read', 'stack read through an esp whose offset is unknown while the return slot is live', True)
                return None
            if key[0] == 'stk':
                v = st.get(key[1])
                if v is None and anyra and key[1] not in st: emit(a, 'ra', 'read-possible-ra', 'stack read in a block whose predecessors are not all visible', True); return ('ra', 0)
                return v
            return None
        def fpu_read(key, a=a):
            """a load from the FIP / FDP offset of an FPU environment image we stored."""
            if key is None or key[0] == 'lost': return None
            for ik, kind in fpe.items():
                if ik[0] != key[0]: continue
                if ik[0] == 'stk': d = key[1] - ik[1]
                elif ik[0] == 'abs': d = key[1] - ik[1]
                elif ik[0] == 'reg' and ik[1] == key[1]: d = key[2] - ik[2]
                else: continue
                if kind == 'env' and d in (12, 20): return ('fip' if d == 12 else 'fdp', 0)
                if kind == 'fx' and d in (8, 16): return ('fip' if d == 8 else 'fdp', 0)
            return None
        def read_mem(op):
            """value of the memory operand (tracked or None); records mem-use of a tracked base."""
            m = op.mem
            for r in (m.base, m.index):
                n = R32.get(r) if r else None
                if n and n != 'esp' and tracked(regs.get(n)): consume(regs[n], 'mem-indexed' if (m.index and r == m.index) or m.index else 'mem', 'abs' if regs[n][0] == 'abs' else '')
            key = memkey(img, g, op, esp, regs)
            v = load(key)
            if v is None: v = fpu_read(key)
            if tracked(v) and v[0] == 'ra': emit(a, 'ra', 'read', 'return-address slot read as data', False)
            if tracked(v) and v[0] in ('fip', 'fdp'): emit(a, v[0], 'read', 'FPU environment field read', False)
            return v
        def write_reg(op, v):
            if op.type != X.X86_OP_REG: return
            n = R32.get(op.reg)
            if n is None or n == 'esp': return
            if op.size != 4 and regs.get(n) in TYPES: return      # a partial write keeps the register's type
            if op.size != 4 and regs.get(n) is not None: regs[n] = 'mix'; return
            if v is None or v == 'mix' and False: regs.pop(n, None)
            else: regs[n] = v
            for ik in [k for k in fpe if k[0] == 'reg' and k[1] == n]: fpe.pop(ik)
        def store_mem(op, v):
            key = memkey(img, g, op, esp, regs)
            if key is None: return
            if key[0] == 'stk':
                if v is None: st.pop(key[1], None)
                else: st[key[1]] = v
                for ik in [k for k in fpe if k[0] == 'stk' and k[1] == key[1]]: pass
        def use_reads(ops_, skip=()):
            """tracked register / stack values read by operands that this rule does not type: RED unknown use."""
            for k, o in enumerate(ops_):
                if k in skip: continue
                if o.type == X.X86_OP_REG:
                    v = regval(o)
                    if v is not None and (tracked(v) or v == 'mix'): consume(v, 'unknown-use', mn, True)
        def reloc_imm():
            return g.imm_size == 4 and (a + g.imm_offset) in img.rset and img.T0 <= (ops[-1].imm & M32) < img.T1
        def reloc_load(op):
            m = op.mem
            if m.base or img.rd is None or not op.size == 4 and op.size != 0: return False
            slot = m.disp & M32
            if slot not in img.rset: return False
            try: w = img.rd(slot)
            except Exception: return False
            return img.T0 <= w < img.T1
        # s1/s4: arithmetic on a typed value gives s4 (copies, loads and stores keep the type)
        tin = False
        if mn not in COPYLIKE and mn != 'lea':
            rn_, wn_ = access(g)
            tin = any(regs.get(n) in TYPES for n in rn_) and bool(wn_ or any(o.type == X.X86_OP_MEM and o.access & capstone.CS_AC_WRITE for o in ops))
        if tin:
            for o in ops:
                if o.type == X.X86_OP_MEM and o.access & capstone.CS_AC_READ: read_mem(o)
            for n in sorted(wn_):
                if n != 'esp': regs[n] = T_S4
            for o in ops:
                if o.type == X.X86_OP_MEM and o.access & capstone.CS_AC_WRITE:
                    key = memkey(img, g, o, esp, regs)
                    if key is not None and key[0] == 'stk': st[key[1]] = T_S4
                    else: emit(a, 's4', 'escape-stored', 'arithmetic result stored to memory', False)
            if X.X86_REG_ESP in g.regs_access()[1]:
                img.info['esp-lost'] += 1; esp = None
            succ = [nxt]
        elif mn == 'call' and img.calltg.get(a) == nxt:
            # call $+5: pushes the concrete address of the next instruction (a typed source of its own)
            if esp is not None:
                esp -= 4; st[esp] = ('abs', frozenset({nxt})); emit(a, 'pc', 'read', 'call $+5 pushes its own next address', False)
            else: emit(a, 'pc', 'esp-lost-use', 'call $+5 with an unknown esp', True)
            succ = [nxt]
        elif mn == 'call':
            o = ops[0]; tg = img.calltg.get(a); sm = None
            if o.type == X.X86_OP_REG and regs.get(R32.get(o.reg)) is not None and tracked(regs[R32[o.reg]]):
                v = regs[R32[o.reg]]; consume(v, 'code-target', 'call reg', False)
                if v[0] == 'abs':
                    for t in sorted(v[1]):
                        if t not in img.entries and t not in img.retsites: emit(a, vsrc(v), 'target-not-entry', '%08x' % t, True)
                else: emit(a, vsrc(v), 'unresolved-target', 'call through a relative value', True)
            elif o.type == X.X86_OP_REG and regs.get(R32.get(o.reg)) in TYPES:
                v = regs[R32[o.reg]]
                if v == T_S4: emit(a, 's4', 'target-s4', 'call through a register holding arithmetic on a code address', True)
                else: emit(a, 'code', 'code-target', 'call reg', False)
            elif o.type == X.X86_OP_MEM: read_mem(o)
            if tg is not None and tg in img.entries and tg != e: sm = analyse_entry(img, tg)[1]
            for off, v in sorted(st.items()):
                if esp is not None and off >= esp and tracked(v) and not (off == 0 and v == ('ra', 0)): emit(a, vsrc(v), 'passed', 'argument slot live at call', True)
            keep = {}
            for n in sorted(regs):
                v = regs[n]
                if v is None: continue
                # the callee's code decides: it reads the register first (a pass), overwrites it on every path (the value dies), or leaves it (the value survives)
                fate = path_kill(img, tg, n) if (tg is not None and tg in img.entries) else 'read'
                if v == T_S4 and fate == 'read': emit(a, 's4', 'escape-passed', 'register %s read by the callee' % n, False)
                if (tracked(v) or v == 'mix') and fate == 'read': emit(a, vsrc(v) if v != 'mix' else 'pc', 'passed', 'register %s read by the callee%s' % (n, '' if tg is not None and tg in img.entries else ' (callee unknown)'), True)
                if fate == 'live' and n not in CALLER_SAVED_NEVER: keep[n] = v
            regs.clear(); regs.update(keep)
            if sm is not None and sm['regs']:
                for n, k in sm['regs'].items():
                    regs[n] = ('abs', frozenset({nxt + k[1]}))
            if sm is not None and sm['pop'] is not None and esp is not None: esp = esp + sm['pop']
            else:
                if esp is not None: img.info['esp-lost'] += 1
                esp = None
            for k in [k for k in st if esp is None or k < esp]: st.pop(k)
            succ = [nxt]
        elif mn == 'ret':
            n = 0
            if ops and ops[0].type == X.X86_OP_IMM: n = ops[0].imm & 0xffff
            v = st.get(esp) if esp is not None else None
            if esp is not None and v is not None and tracked(v) and v != ('ra', 0): consume(v, 'ret-transfer', 'ret to %s+%d' % (v[0], v[1] if v[0] != 'abs' else 0), False); emit(a, vsrc(v), 'ret-transfer', 'ret through a moved return address', False)
            elif esp is not None and v == T_S4: emit(a, 's4', 'target-s4', 'ret through a stack slot holding arithmetic on a code address', True)
            elif esp is not None and v == ('ra', 0): pass
            elif esp is None: emit(a, '-', 'esp-lost-ret', 'ret with an unknown esp offset', False)
            rr = {n_: regs[n_] for n_ in sorted(regs) if isinstance(regs[n_], tuple) and regs[n_][0] == 'ra'}
            for n_, v_ in sorted(regs.items()):
                if v_ == T_S4: emit(a, 's4', 'escape-returned', 'returned in %s' % n_, False)
                if v_ is not None and tracked(v_) and v_[0] != 'ra':
                    if n_ in CALLER_SAVED and not anyra: retscratch.append((a, n_, vsrc(v_)))      # dead iff every caller overwrites it first: decided in resolve()
                    else: emit(a, vsrc(v_), 'passed', 'returned in %s' % n_, True)
            rets.append((esp, n, rr)); term = True
        elif mn == 'jmp':
            o = ops[0]
            if o.type == X.X86_OP_REG:
                v = regs.get(R32.get(o.reg))
                if v == T_S4: emit(a, 's4', 'target-s4', 'jmp through a register holding arithmetic on a code address', True)
                elif v == T_CODE: emit(a, 'code', 'code-target', 'jmp reg', False)
                if tracked(v):
                    consume(v, 'code-target', 'jmp reg')
                    if v[0] == 'abs':
                        for t in sorted(v[1]):
                            if t not in img.entries and t not in img.retsites: emit(a, vsrc(v), 'target-not-entry', '%08x' % t, True)
                    else: emit(a, vsrc(v), 'unresolved-target', 'jmp through a relative value', True)
                term = True
            elif o.type == X.X86_OP_MEM:
                read_mem(o); term = True
                m = o.mem
                if m.index and not m.base and img.rd is not None:       # jmp [tbl + r*4]: the relocated words of the table are the successors
                    t = m.disp & M32; n_ = 0
                    while t in img.rset and n_ < 4096:
                        try: w = img.rd(t)
                        except Exception: break
                        if not (img.T0 <= w < img.T1 and img.reached(w)): break
                        succ.append(w); t += 4; n_ += 1
                    term = False
                    for w in succ: img.jtargets.add(w)
            else:
                t = o.imm & M32
                if t != e and t in img.entries and owner(img, t):
                    for n in sorted(regs):
                        if tracked(regs[n]): emit(a, vsrc(regs[n]), 'passed', 'tail jump with %s live' % n, True)
                    term = True
                else: succ = [t]; img.jtargets.add(t)
        elif mn in ('hlt', 'ud2', 'int3', 'iret', 'iretd', 'retf'): term = True
        elif mn[0] == 'j' or mn in ('loop', 'loope', 'loopne', 'jecxz'):
            succ = [nxt, ops[0].imm & M32] if ops and ops[0].type == X.X86_OP_IMM else [nxt]
        elif mn == 'push':
            o = ops[0]
            v = regval(o) if o.type == X.X86_OP_REG else (read_mem(o) if o.type == X.X86_OP_MEM else (T_CODE if (o.type == X.X86_OP_IMM and reloc_imm()) else None))
            if v is None and o.type == X.X86_OP_MEM and reloc_load(o): v = T_CODE
            if esp is not None:
                esp -= 4
                if v is not None: st[esp] = v
                else: st.pop(esp, None)
            elif v is not None and tracked(v): emit(a, vsrc(v), 'esp-lost-use', 'push of a tracked value with an unknown esp', True)
            succ = [nxt]
        elif mn == 'pop':
            o = ops[0]
            if esp is not None:
                v = st.pop(esp, None)
                if v is None and anyra: emit(a, 'ra', 'read-possible-ra', 'pop in a block whose predecessors are not all visible', True); v = ('ra', 0)
                if o.type == X.X86_OP_REG: write_reg(o, v)
                elif o.type == X.X86_OP_MEM and tracked(v): emit(a, vsrc(v), 'store', 'pop to memory', True)
                if tracked(v) and v[0] == 'ra': emit(a, 'ra', 'read', 'return-address slot read as data', False)
                if v is None:
                    fv = fpu_read(('stk', esp))
                    if fv is not None and o.type == X.X86_OP_REG: write_reg(o, fv); emit(a, fv[0], 'read', 'FPU environment field read', False)
                esp += 4
            else:
                if o.type == X.X86_OP_REG: write_reg(o, None)
            succ = [nxt]
        elif mn == 'mov':
            d, s = ops
            if d.type == X.X86_OP_REG:
                if s.type == X.X86_OP_REG: write_reg(d, regval(s))
                elif s.type == X.X86_OP_MEM:
                    v = read_mem(s)
                    if v is None and d.size == 4 and reloc_load(s): v = T_CODE
                    if d.size == 4: write_reg(d, v)
                    else: write_reg(d, 'mix' if tracked(v) else None)
                else: write_reg(d, T_CODE if (d.size == 4 and reloc_imm()) else None)
                if d.type == X.X86_OP_REG and R32.get(d.reg) == 'esp' and s.type == X.X86_OP_REG:
                    v = regs.get(R32.get(s.reg)); esp = v[1] if isinstance(v, tuple) and v[0] == 'sp' else None
                    if esp is None: img.info['esp-lost'] += 1
            elif d.type == X.X86_OP_MEM:
                v = regval(s) if s.type == X.X86_OP_REG else None
                key = memkey(img, g, d, esp, regs)
                if v is not None and tracked(v):
                    if key is not None and key[0] == 'stk': st[key[1]] = v
                    else: emit(a, vsrc(v), 'store', 'mov to memory', True)
                elif v in TYPES and key is not None and key[0] == 'stk': st[key[1]] = v
                elif v == T_S4: emit(a, 's4', 'escape-stored', 'mov to memory', False)
                elif key is not None and key[0] == 'stk': st.pop(key[1], None)
                for r_ in (d.mem.base, d.mem.index):
                    n = R32.get(r_) if r_ else None
                    if n and n != 'esp' and tracked(regs.get(n)): consume(regs[n], 'mem', 'store through')
            succ = [nxt]
        elif mn == 'lea':
            d, s = ops; m = s.mem; v = None
            b = R32.get(m.base) if m.base else None; ix = R32.get(m.index) if m.index else None
            bv = (('sp', esp) if esp is not None else None) if b == 'esp' else regs.get(b)
            if b and not ix and isinstance(bv, tuple) and bv[0] == 'sp': v = ('sp', bv[1] + m.disp)
            elif b and not ix and isinstance(bv, tuple) and bv[0] in ('ra', 'fip', 'fdp'): v = (bv[0], bv[1] + m.disp)
            elif b and not ix and isinstance(bv, tuple) and bv[0] == 'abs': v = ('abs', frozenset(t + m.disp for t in bv[1]))
            elif (b and tracked(bv)) or (ix and tracked(regs.get(ix))): v = 'mix'; emit(a, 'pc', 'merge', 'lea with an index', True)
            if v is None:
                bt = bv if b else None; it = regs.get(ix) if ix else None
                if bt in TYPES or it in TYPES: v = bt if (bt in TYPES and not ix and m.disp == 0) else T_S4
                elif not b and not ix and g.disp_size == 4 and (a + g.disp_offset) in img.rset and img.T0 <= (m.disp & M32) < img.T1: v = T_CODE
            write_reg(d, v)
            succ = [nxt]
        elif mn in ('add', 'sub') and ops[0].type == X.X86_OP_REG and R32.get(ops[0].reg):
            n = R32[ops[0].reg]; s = ops[1]
            if n == 'esp':
                if esp is not None and s.type == X.X86_OP_IMM: esp += s.imm if mn == 'add' else -s.imm
                else:
                    if esp is not None: img.info['esp-lost'] += 1
                    esp = None
                for k in [k for k in st if esp is None or k < esp]: st.pop(k)
            else:
                v = regs.get(n)
                if v is not None and s.type == X.X86_OP_IMM and isinstance(v, tuple) and v[0] != 'abs':
                    regs[n] = (v[0], v[1] + (s.imm if mn == 'add' else -s.imm))
                elif v is not None and s.type == X.X86_OP_IMM and isinstance(v, tuple) and v[0] == 'abs':
                    regs[n] = ('abs', frozenset(t + (s.imm if mn == 'add' else -s.imm) for t in v[1]))
                elif v is not None and tracked(v) or v == 'mix':
                    if mn == 'sub' and s.type == X.X86_OP_REG and R32.get(s.reg) == n: regs.pop(n, None)
                    else:
                        emit(a, vsrc(v), 'merge', '%s with a non-constant' % mn, True); regs[n] = 'mix'
                else:
                    sv = regval(s) if s.type == X.X86_OP_REG else (read_mem(s) if s.type == X.X86_OP_MEM else None)
                    if sv is not None and (tracked(sv) or sv == 'mix'):
                        emit(a, vsrc(sv), 'merge', '%s of a tracked value into a register' % mn, True); regs[n] = 'mix'
                    else: regs.pop(n, None)
                    if isinstance(v, tuple) and v[0] == 'sp': regs.pop(n, None)
            succ = [nxt]
        elif mn in ('add', 'sub') and ops[0].type == X.X86_OP_MEM:
            key = memkey(img, g, ops[0], esp, regs)
            s = ops[1]
            if key is not None and key[0] == 'stk' and isinstance(st.get(key[1]), tuple) and s.type == X.X86_OP_IMM and st[key[1]][0] != 'abs':
                v = st[key[1]]; st[key[1]] = (v[0], v[1] + (s.imm if mn == 'add' else -s.imm))
            elif key is not None and key[0] == 'stk' and st.get(key[1]) is not None:
                emit(a, vsrc(st[key[1]]), 'merge', '%s on a slot holding a tracked value' % mn, True); st[key[1]] = 'mix'
            else:
                if s.type == X.X86_OP_REG and tracked(regval(s)): emit(a, vsrc(regval(s)), 'store', 'ALU to memory', True)
            succ = [nxt]
        elif mn in ('cmp', 'test'):
            for o in ops:
                if o.type == X.X86_OP_REG and (tracked(regval(o)) or regval(o) == 'mix'): consume(regval(o), 'compare')
                elif o.type == X.X86_OP_MEM: read_mem(o)
            succ = [nxt]
        elif mn in ('xor', 'sub') and len(ops) == 2 and ops[0].type == ops[1].type == X.X86_OP_REG and ops[0].reg == ops[1].reg:
            write_reg(ops[0], None); succ = [nxt]
        elif mn == 'leave':
            v = regs.get('ebp'); esp = (v[1] + 4) if isinstance(v, tuple) and v[0] == 'sp' else None
            if esp is None: img.info['esp-lost'] += 1
            else:
                pv = st.pop(esp - 4, None); regs.pop('ebp', None)
            for k in [k for k in st if esp is None or k < esp]: st.pop(k)
            succ = [nxt]
        elif mn in FP_ENV or mn in FP_FXSAVE or (mn.startswith('fxsave')):
            o = ops[0]
            if o.type == X.X86_OP_MEM:
                key = memkey(img, g, o, esp, regs)
                if key is not None and key[0] != 'lost':
                    fpe[key] = 'env' if mn in FP_ENV else 'fx'
                    if key[0] == 'stk':
                        sz = FP_ENV.get(mn, 512)
                        for k in [k for k in st if key[1] <= k < key[1] + sz]: st.pop(k)
                else: img.info['fpu-env-unknown-destination'] += 1
            succ = [nxt]
        elif mn.startswith('f') and mn not in FPU_CONTROL:
            for o in ops:
                if o.type == X.X86_OP_MEM and o.access & capstone.CS_AC_READ: read_mem(o)
            succ = [nxt]
        else:
            for o in ops:
                if o.type == X.X86_OP_MEM and o.access & capstone.CS_AC_READ: read_mem(o)
            rd, wr = g.regs_access(); written = set()
            for r in wr:
                n = R32.get(r)
                if n and n != 'esp': written.add(n)
            for k, o in enumerate(ops):
                if o.type == X.X86_OP_REG and k > 0 or (o.type == X.X86_OP_REG and mn in ('inc', 'dec', 'neg', 'not', 'xchg', 'bswap')):
                    v = regval(o)
                    if v is not None and (tracked(v) or v == 'mix'): consume(v, 'unknown-use', mn, True)
            for n in sorted(written):
                if regs.get(n) is not None and mn in ('inc', 'dec', 'neg', 'not', 'bswap', 'xchg') and False: pass
                regs.pop(n, None)
                for ik in [k for k in fpe if k[0] == 'reg' and k[1] == n]: fpe.pop(ik)
            if X.X86_REG_ESP in wr and mn not in ('push', 'pop'):
                if esp is not None: img.info['esp-lost'] += 1
                esp = None
            succ = [nxt]
        if term: continue
        ns = (regs, st, esp, fpl, fpe)
        for t in succ:
            if not (img.T0 <= t < img.T1) or not img.reached(t): continue
            if t not in states: states[t] = ns; work.append(t)
            else:
                o = states[t]
                r2 = {k: j(o[0].get(k), regs.get(k)) for k in set(o[0]) | set(regs)}
                r2 = {k: v for k, v in r2.items() if v is not None}
                s2 = {k: j(o[1].get(k), st.get(k)) for k in set(o[1]) | set(st)}
                s2 = {k: v for k, v in s2.items() if v is not None}
                e2 = o[2] if o[2] == esp else None
                f2 = o[3]; fe2 = {k: v for k, v in o[4].items() if fpe.get(k) == v}
                # abs values merge as a union of concrete addresses (the same typed source at a join)
                for dct in (r2, s2):
                    for k in list(dct):
                        x, y = o[0 if dct is r2 else 1].get(k), (regs if dct is r2 else st).get(k)
                        if isinstance(x, tuple) and isinstance(y, tuple) and x[0] == y[0] == 'abs': dct[k] = ('abs', x[1] | y[1])
                        elif isinstance(x, tuple) and isinstance(y, tuple) and x[0] == y[0] and x[0] in ('fip', 'fdp') and x[1] == y[1]: dct[k] = x
                if (r2, s2, e2, f2, fe2) != o: states[t] = (r2, s2, e2, f2, fe2); work.append(t)
    if not anyra: img.busy.discard(e)
    # resolve fip / fdp symbolic values: concretise through the x87 instructions that executed before (recorded in fpl at the load)
    pops = [r[0] for r in rets]; ns_ = {r[1] for r in rets}
    pop = None
    if rets and all(p is not None for p in pops) and len(set(pops)) == 1 and len(ns_) == 1: pop = pops[0] + 4 + next(iter(ns_))
    reg_ret = {}
    if rets:
        names = set(k for r in rets for k in r[2])
        for n in sorted(names):
            if all(n in r[2] and r[2][n] == rets[0][2].get(n) for r in rets) and n in rets[0][2]: reg_ret[n] = rets[0][2][n]
    # a symbolic ra value consumed inside this entry: resolve against the callers (concrete consumer checks)
    summ = (ev, {'regs': reg_ret, 'pop': (pop - 4 if pop is not None else None)})
    img.pk[(e, mode)] = retscratch
    if not anyra: img.summ[e] = summ
    return summ


def writes_of_unused(img, e, _memo={}):
    """registers written by the entry e and by everything it calls directly (a pop that restores a register still counts as a write: callee-saved behaviour is not assumed).
    None = a callee is unknown (an indirect call, a call outside the entries)."""
    key = (id(img), e)
    if key in _memo: return _memo[key]
    out = set(); todo = [e]; seen = set(); unknown = False
    while todo:
        f = todo.pop()
        if f in seen: continue
        seen.add(f); a = f
        local = set(); stack = [f]; vis = set()
        while stack:
            a = stack.pop()
            while a not in vis and img.reached(a):
                vis.add(a); g = img.dec(a)
                if not g: break
                rd, wr = g.regs_access()
                for r in wr:
                    n = R32.get(r)
                    if n: out.add(n)
                if g.mnemonic == 'call':
                    t = img.calltg.get(a)
                    if t is None or t not in img.entries: unknown = True
                    elif t not in seen: todo.append(t)
                elif g.mnemonic in ('ret', 'retf', 'hlt', 'ud2'): break
                elif g.mnemonic == 'jmp':
                    o = g.operands[0]
                    if o.type == X.X86_OP_IMM:
                        t = o.imm & M32
                        if t in img.entries and t != f:
                            if t not in seen: todo.append(t)
                            break
                        stack.append(t)
                    else: unknown = True
                    break
                elif g.mnemonic[0] == 'j' and g.operands and g.operands[0].type == X.X86_OP_IMM: stack.append(g.operands[0].imm & M32)
                a += g.size
    _memo[key] = None if unknown else out
    return _memo[key]


FULL32 = {X.X86_REG_EAX: 'eax', X.X86_REG_EBX: 'ebx', X.X86_REG_ECX: 'ecx', X.X86_REG_EDX: 'edx', X.X86_REG_ESI: 'esi', X.X86_REG_EDI: 'edi', X.X86_REG_EBP: 'ebp'}


def access(g):
    """(registers read at all, registers fully overwritten) by one instruction; a partial write or a conditional write does not kill."""
    rd, wr = g.regs_access(); mn = g.mnemonic
    rn = {R32[r] for r in rd if r in R32}; wn = {FULL32[r] for r in wr if r in FULL32}
    o = g.operands
    if mn in ('xor', 'sub') and len(o) == 2 and o[0].type == o[1].type == X.X86_OP_REG and o[0].reg == o[1].reg: rn -= wn
    if mn.startswith('cmov') or mn in ('xchg', 'cmpxchg', 'xadd'): rn |= wn
    return rn, wn


def path_kill(img, e, reg):
    """the fate of a register value at the entry of function e, by e's own code: 'read' (read before overwritten on some path, or a path leaves our knowledge: an indirect
    transfer, an unknown callee), 'live' (survives to a return on some path, never read), 'killed' (overwritten on every path before any return)."""
    k = (e, reg)
    if k in img.wmemo: return img.wmemo[k]
    img.wmemo[k] = 'killed'            # a cycle adds no read and no return of its own
    r = scan_kill(img, e, reg)
    img.wmemo[k] = r
    return r


def scan_kill(img, a0, reg, root=None):
    live = False; stack = [a0]; vis = set(); root = a0 if root is None else root
    while stack:
        a = stack.pop()
        while a not in vis:
            vis.add(a)
            if not img.reached(a): return 'read'
            g = img.dec(a)
            if not g: return 'read'
            mn = g.mnemonic; nxt = a + g.size; rn, wn = access(g)
            if reg in rn: return 'read'
            if reg in wn: break
            if mn == 'call':
                t = img.calltg.get(a)
                if t == nxt: a = nxt; continue
                if t is None or t not in img.entries: return 'read'
                r = path_kill(img, t, reg)
                if r == 'read': return 'read'
                if r == 'killed': break
                a = nxt; continue
            if mn in RETS: live = True; break
            if mn in ('hlt', 'ud2', 'int3'): break
            if mn == 'jmp':
                o = g.operands[0]
                if o.type == X.X86_OP_IMM:
                    t = o.imm & M32
                    if t != root and t in img.entries and owner(img, t):
                        r = path_kill(img, t, reg)
                        if r == 'read': return 'read'
                        if r == 'live': live = True
                        break
                    a = t; continue
                if o.type == X.X86_OP_MEM and o.mem.index and not o.mem.base and img.rd is not None:
                    t = o.mem.disp & M32; n_ = 0; any_ = False
                    while t in img.rset and n_ < 4096:
                        try: w = img.rd(t)
                        except Exception: break
                        if not (img.T0 <= w < img.T1 and img.reached(w)): break
                        stack.append(w); any_ = True; t += 4; n_ += 1
                    if any_: break
                return 'read'
            if mn[0] == 'j' and g.operands and g.operands[0].type == X.X86_OP_IMM: stack.append(g.operands[0].imm & M32)
            a = nxt
    return 'live' if live else 'killed'


def resolve(img, e, ev, kinds, mode, retscratch):
    """rows of one root: its events, the indirect-callers rule for a return address read as data, and the scratch-at-ret rule."""
    rows = [(e, site, src, cons, d, red) for site, src, cons, d, red in ev]
    callers = img.callers.get(e, [])
    indirect = bool(kinds & INDIRECT_KINDS)
    if mode == 'owner' and any(src == 'ra' and cons == 'read' for site, src, cons, d, red in ev):
        if indirect: rows.append((e, e, 'ra', 'indirect-callers', 'return address read as data in an entry reached by callers the facts do not enumerate', True))
        elif not callers: rows.append((e, e, 'ra', 'no-callers', 'return address read as data, no direct caller in the reached code', True))
    for site, n, src in retscratch:
        why = None
        if indirect: why = 'entry has indirect callers'
        elif e in img.jtargets: why = 'entry is also a jump target (tail callers)'
        elif not callers: why = 'no direct caller in the reached code'
        else:
            for c in callers:
                g = img.dec(c)
                if not g or scan_kill(img, c + g.size, n, root=-1) != 'killed': why = 'caller %08x does not overwrite %s first' % (c, n); break
        if why: rows.append((e, site, src, 'passed', 'returned in %s (%s)' % (n, why), True))
        else: rows.append((e, site, src, 'scratch-dead', 'returned in %s, every caller overwrites it first' % n, False))
    return rows


def analyse(img, kinds_of, parent=None):
    scan_calls(img)
    rows = []; parent = parent or {}
    isdec = lambda a: 'decomp' in (img.entries.get(a) or ())
    for e in sorted(img.entries):
        if not img.reached(e) or not owner(img, e): continue
        if isdec(e): img.info['decomp-owner-skipped'] += 1; continue
        ev, sm = analyse_entry(img, e)
        rows.extend(resolve(img, e, ev, kinds_of.get(e, set()), 'owner', img.pk.get((e, 'owner'), [])))
    for e in sorted(img.entries):
        if not img.reached(e) or owner(img, e) or e in img.visited: continue
        p = parent.get(e)
        if p is not None and isdec(p): img.info['decomp-block-skipped'] += 1; continue
        img.info['blocks-without-visible-predecessor'] += 1
        ev, sm = analyse_entry(img, e, 'possibly')
        rows.extend(resolve(img, e, ev, kinds_of.get(e, set()), 'possibly', img.pk.get((e, 'possibly'), [])))
    return rows


def run(text, T0, entries, ranges, outdir, name, rd=None, rset=None, parent=None, cand=None):
    """-> (red count, summary dict); writes outdir/pcsources.tsv and gate_pcsrc.json."""
    img = Img(text, T0, entries, ranges, rd, rset)
    rows = analyse(img, entries, parent)
    red = [r for r in rows if r[5]]
    with open(outdir + '/pcsources.tsv', 'w') as f:
        f.write('# entry, site, source (ra|fip|fdp|abs|pc), consumer, detail; RED rows make the closure red for the image\n')
        for e, site, src, cons, d, rd_ in sorted(set(rows), key=lambda r: (r[1], r[0], r[2], r[3], r[4])): f.write('%08x\t%08x\t%s\t%s\t%s%s\n' % (e, site, src, cons, d, '\tRED' if rd_ else ''))
    rows = sorted(set(rows)); red = [r for r in rows if r[5]]
    cand = cand or set(); redcand = sum(1 for r in red if r[0] in cand)
    byc = Counter((r[2], r[3]) for r in rows); redc = Counter((r[2], r[3]) for r in red)
    sources = len({(r[1], r[2]) for r in rows if r[3] == 'read'})
    res = dict(schema='fnvnx-gate/1', name='l3.pcsrc.' + name, status='red' if red else 'green', sources=sources, rows=len(rows), red=len(red), by_class={'%s/%s' % k: n for k, n in sorted(byc.items())},
               red_by_class={'%s/%s' % k: n for k, n in sorted(redc.items())}, s4_red=sum(1 for r in red if r[2] == 's4'), s4_red_proven=sum(1 for r in red if r[2] == 's4' and r[0] not in cand), s4_escapes=sum(1 for r in rows if r[2] == 's4' and not r[5]), code_sources=sum(1 for r in rows if r[2] == 'code'), red_candidate_entries=redcand, red_proven_entries=len(red) - redcand, esp_lost=img.info['esp-lost'], entries=len(entries), info=dict(img.info))
    json.dump(res, open(outdir + '/gate_pcsrc.json', 'w'), sort_keys=True)
    json.dump({'s4_violations': res['s4_red']}, open(outdir + '/pcsrc_s4.json', 'w'), sort_keys=True)      # read by modlift's residual: no file = unchecked, N > 0 = violation
    return len(red), res


def run_facts(facts, name, text, T0, rd=None, rset=None):
    """facts: directory holding universe.tsv and ranges.tsv of the image; text/T0 the executable span; rd(va) reads a 32-bit word of the image, rset the relocated words. Same outputs as run()."""
    ent = {}; parent = {}; cand = set()
    for l in open(facts + '/universe.tsv'):
        if l[0] == '#' or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); ent[int(f[0], 16)] = set(f[1].split(','))
        for x in (f[3].split(',') if len(f) > 3 else ()):
            if x.startswith('parent='): parent[int(f[0], 16)] = int(x[7:], 16)
            if x == 'candidate': cand.add(int(f[0], 16))
    rg = []
    for l in open(facts + '/ranges.tsv'):
        if l[0] == '#' or not l.strip(): continue
        for r in l.rstrip('\n').split('\t')[1].split(','):
            if '-' in r: a, b = r.split('-'); rg.append((int(a, 16), int(b, 16)))
    return run(text, T0, ent, rg, facts, name, rd, rset, parent, cand)
