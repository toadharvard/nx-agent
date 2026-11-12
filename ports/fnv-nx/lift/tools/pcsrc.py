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
FP_ENV = {'fnstenv': 28, 'fstenv': 28, 'fnsave': 108, 'fsave': 108}      # 32-bit protected mode image: FIP at +12, FDP at +20
FP_FXSAVE = ('fxsave',)                                                   # FIP at +8, FDP at +16
FPU_CONTROL = {'fnstenv', 'fstenv', 'fnsave', 'fsave', 'fldenv', 'frstor', 'fnstcw', 'fstcw', 'fldcw', 'fnstsw', 'fstsw', 'fnclex', 'fclex', 'fninit', 'finit', 'fwait', 'wait', 'fxsave', 'fxrstor'}
DIRECT_KINDS = {'call', 'tail', 'decomp', 'inner'}
RETS = ('ret', 'retf', 'iret', 'iretd')


def j(a, b):
    if a == b: return a
    return 'mix' if (a is not None or b is not None) else None


class Img:
    def __init__(self, text, T0, entries, ranges):
        """text: bytes of the executable span at T0; entries: {addr: set(kinds)}; ranges: sorted list of reached [start, end) byte ranges (any overlap)."""
        self.text, self.T0, self.T1 = text, T0, T0 + len(text); self.entries = entries
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); self.md.detail = True
        mer = []
        for a, b in sorted(ranges):
            if mer and a <= mer[-1][1]: mer[-1][1] = max(mer[-1][1], b)
            else: mer.append([a, b])
        self.mer = mer; self.ms = [a for a, b in mer]; self.cache = {}
        self.calltg = {}; self.retsites = set(); self.callers = {}; self.summ = {}; self.busy = set(); self.rows = []; self.info = Counter()
    def reached(self, a):
        i = bisect.bisect_right(self.ms, a) - 1
        return i >= 0 and a < self.mer[i][1]
    def dec(self, a):
        g = self.cache.get(a)
        if g is None:
            o = a - self.T0
            g = next(self.md.disasm(self.text[o:o + 15], a, 1), False) if 0 <= o < len(self.text) else False
            self.cache[a] = g
        return g


def scan_calls(img):
    """every reached direct call: callers {target: [site]}, return sites; decoded once over the reached ranges."""
    seen = set()
    for a0, b0 in img.mer:
        a = a0
        while a < b0:
            g = img.dec(a)
            if not g: a += 1; continue
            if a not in seen:
                seen.add(a)
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
            g = img.dec(a)
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


def analyse_entry(img, e):
    """forward dataflow over the instructions of entry e. -> (events, summary). events: [(site, source, consumer, detail, red)];
    summary: {'regs': {reg: ('ra', k)}, 'pop': esp offset after return relative to the caller's esp before the call, or None}."""
    if e in img.summ: return img.summ[e]
    if e in img.busy: return ([], {'regs': {}, 'pop': None})
    img.busy.add(e)
    ev = []; rets = []; seen_ev = set()
    def emit(site, src, cons, detail, red):
        k = (site, src, cons, detail)
        if k not in seen_ev: seen_ev.add(k); ev.append((site, src, cons, detail, red))
    states = {e: ({}, {0: ('ra', 0)}, 0, frozenset(), {})}      # regs, stack slots, esp offset, last x87 instructions, fpu env images {key: kind}
    work = [e]; steps = 0
    while work:
        a = work.pop(); steps += 1
        if steps > 20000: emit(a, '-', 'incomplete', 'dataflow step cap', True); break
        regs0, st0, esp0, fpl, fpe0 = states[a]
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
            v = regs.get(n)
            if v is not None and op.size != 4: return 'mix'
            return v
        def load(key, a=a):
            """value read from memory key."""
            if key is None: return None
            if key[0] == 'lost':
                if any(isinstance(v, tuple) and v[0] == 'ra' for v in st.values()): emit(a, 'ra', 'esp-lost-read', 'stack read through an esp whose offset is unknown while the return slot is live', True)
                return None
            if key[0] == 'stk': return st.get(key[1])
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
        if mn == 'call' and img.calltg.get(a) == nxt:
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
            elif o.type == X.X86_OP_MEM: read_mem(o)
            if tg is not None and tg in img.entries and tg != e: sm = analyse_entry(img, tg)[1]
            for off, v in sorted(st.items()):
                if esp is not None and off >= esp and tracked(v) and not (off == 0 and v == ('ra', 0)): emit(a, vsrc(v), 'passed', 'argument slot live at call', True)
            callee_w = writes_of(img, tg) if sm is not None else None
            for n in sorted(regs):
                v = regs[n]
                if v is None: continue
                if tracked(v) or v == 'mix':
                    if callee_w is not None and n not in callee_w: continue
                    if n in CALLER_SAVED and sm is not None: continue
                    emit(a, vsrc(v) if v != 'mix' else 'pc', 'passed', 'register %s live across a call (callee %s)' % (n, 'writes it' if sm is not None else 'unknown'), True)
            keep = {n: v for n, v in regs.items() if n not in CALLER_SAVED and callee_w is not None and n not in callee_w}
            for n in list(regs):
                if n in keep: continue
                regs.pop(n)
            for n in list(keep): regs[n] = keep[n]
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
            elif esp is not None and v == ('ra', 0): pass
            elif esp is None: emit(a, '-', 'esp-lost-ret', 'ret with an unknown esp offset', False)
            rr = {n_: regs[n_] for n_ in sorted(regs) if isinstance(regs[n_], tuple) and regs[n_][0] == 'ra'}
            for n_, v_ in sorted(regs.items()):
                if v_ is not None and tracked(v_) and v_[0] != 'ra': emit(a, vsrc(v_), 'passed', 'returned in %s' % n_, True)
                elif v_ == 'mix': pass
            rets.append((esp, n, rr)); term = True
        elif mn == 'jmp':
            o = ops[0]
            if o.type == X.X86_OP_REG:
                v = regs.get(R32.get(o.reg))
                if tracked(v):
                    consume(v, 'code-target', 'jmp reg')
                    if v[0] == 'abs':
                        for t in sorted(v[1]):
                            if t not in img.entries and t not in img.retsites: emit(a, vsrc(v), 'target-not-entry', '%08x' % t, True)
                    else: emit(a, vsrc(v), 'unresolved-target', 'jmp through a relative value', True)
                term = True
            elif o.type == X.X86_OP_MEM: read_mem(o); term = True
            else:
                t = o.imm & M32
                if t != e and t in img.entries:
                    for n in sorted(regs):
                        if tracked(regs[n]): emit(a, vsrc(regs[n]), 'passed', 'tail jump with %s live' % n, True)
                    term = True
                else: succ = [t]
        elif mn in ('hlt', 'ud2', 'int3', 'iret', 'iretd', 'retf'): term = True
        elif mn[0] == 'j' or mn in ('loop', 'loope', 'loopne', 'jecxz'):
            succ = [nxt, ops[0].imm & M32] if ops and ops[0].type == X.X86_OP_IMM else [nxt]
        elif mn == 'push':
            o = ops[0]
            v = regval(o) if o.type == X.X86_OP_REG else (read_mem(o) if o.type == X.X86_OP_MEM else None)
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
                    if d.size == 4: write_reg(d, v)
                    else: write_reg(d, 'mix' if tracked(v) else None)
                else: write_reg(d, None)
                if d.type == X.X86_OP_REG and R32.get(d.reg) == 'esp' and s.type == X.X86_OP_REG:
                    v = regs.get(R32.get(s.reg)); esp = v[1] if isinstance(v, tuple) and v[0] == 'sp' else None
                    if esp is None: img.info['esp-lost'] += 1
            elif d.type == X.X86_OP_MEM:
                v = regval(s) if s.type == X.X86_OP_REG else None
                key = memkey(img, g, d, esp, regs)
                if v is not None and tracked(v):
                    if key is not None and key[0] == 'stk': st[key[1]] = v
                    else: emit(a, vsrc(v), 'store', 'mov to memory', True)
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
            if v is not None and isinstance(v, tuple) and v[0] == 'sp' and v[1] <= 0 <= v[1] + 0 and False: pass
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
            fpl = frozenset({a});
            for o in ops:
                if o.type == X.X86_OP_MEM: read_mem(o)
            succ = [nxt]
        else:
            for o in ops:
                if o.type == X.X86_OP_MEM: read_mem(o)
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
                f2 = o[3] | fpl; fe2 = {k: v for k, v in o[4].items() if fpe.get(k) == v}
                # abs values merge as a union of concrete addresses (the same typed source at a join)
                for dct in (r2, s2):
                    for k in list(dct):
                        x, y = o[0 if dct is r2 else 1].get(k), (regs if dct is r2 else st).get(k)
                        if isinstance(x, tuple) and isinstance(y, tuple) and x[0] == y[0] == 'abs': dct[k] = ('abs', x[1] | y[1])
                        elif isinstance(x, tuple) and isinstance(y, tuple) and x[0] == y[0] and x[0] in ('fip', 'fdp') and x[1] == y[1]: dct[k] = x
                if (r2, s2, e2, f2, fe2) != o: states[t] = (r2, s2, e2, f2, fe2); work.append(t)
    img.busy.discard(e)
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
    img.summ[e] = summ
    return summ


def writes_of(img, e, _memo={}):
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


def resolve(img, e, ev, kinds):
    """expand the return-address events of entry e against its callers: concrete addresses of control targets, indirect-callers red."""
    rows = []
    callers = img.callers.get(e, [])
    indirect = bool(kinds - DIRECT_KINDS)      # an entry with any kind other than a direct call/jump target is reached by a call site the facts do not enumerate
    uses_ra = any(src == 'ra' and cons == 'read' for site, src, cons, d, red in ev)
    for site, src, cons, d, red in ev: rows.append((e, site, src, cons, d, red))
    if uses_ra:
        if indirect: rows.append((e, e, 'ra', 'indirect-callers', 'return address read as data in an entry that is also reached indirectly (call sites unknown)', True))
        if not callers and not indirect: rows.append((e, e, 'ra', 'no-callers', 'return address read as data, no direct caller in the reached code', False))
    return rows


def analyse(img, kinds_of):
    scan_calls(img)
    rows = []; summaries = 0
    for e in sorted(img.entries):
        if not img.reached(e): continue
        ev, sm = analyse_entry(img, e)
        rows.extend(resolve(img, e, ev, kinds_of.get(e, set())))
    return rows


def run(text, T0, entries, ranges, outdir, name):
    """-> (red count, summary dict); writes outdir/pcsources.tsv and gate_pcsrc.json."""
    img = Img(text, T0, entries, ranges)
    rows = analyse(img, entries)
    red = [r for r in rows if r[5]]
    with open(outdir + '/pcsources.tsv', 'w') as f:
        f.write('# entry, site, source (ra|fip|fdp|abs|pc), consumer, detail; RED rows make the closure red for the image\n')
        for e, site, src, cons, d, rd in sorted(rows, key=lambda r: (r[1], r[0], r[2], r[3], r[4])): f.write('%08x\t%08x\t%s\t%s\t%s%s\n' % (e, site, src, cons, d, '\tRED' if rd else ''))
    byc = Counter((r[2], r[3]) for r in rows)
    sources = len({(r[1], r[2]) for r in rows if r[3] == 'read'})
    res = dict(schema='fnvnx-gate/1', name='l3.pcsrc.' + name, status='red' if red else 'green', sources=sources, rows=len(rows), red=len(red), by_class={'%s/%s' % k: n for k, n in sorted(byc.items())}, esp_lost=img.info['esp-lost'], entries=len(entries))
    json.dump(res, open(outdir + '/gate_pcsrc.json', 'w'), sort_keys=True)
    return len(red), res


def run_facts(facts, name, text, T0):
    """facts: directory holding universe.tsv and ranges.tsv of the image; text/T0 the executable span. Same outputs as run()."""
    ent = {}
    for l in open(facts + '/universe.tsv'):
        if l[0] == '#' or not l.strip(): continue
        f = l.rstrip('\n').split('\t'); ent[int(f[0], 16)] = set(f[1].split(','))
    rg = []
    for l in open(facts + '/ranges.tsv'):
        if l[0] == '#' or not l.strip(): continue
        for r in l.rstrip('\n').split('\t')[1].split(','):
            if '-' in r: a, b = r.split('-'); rg.append((int(a, 16), int(b, 16)))
    return run(text, T0, ent, rg, facts, name)
