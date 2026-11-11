#!/usr/bin/env python3
"""W10b: class of every memory operand of FalloutNV.exe (rule R9). Class (a) = plain load/store, by PROOF:
 rule 1  a frame none of whose addresses escapes is private: locals, incoming and outgoing argument slots are plain (total esp/ebp
         tracking in x86flow; funclets are analysed as part of their parent frame);
 rule 2  a frame address that only reaches direct calls whose parameter summary says "does not escape" (greatest fixed point over
         the call graph) contaminates nothing; otherwise it contaminates its object extent (up to the next directly accessed slot).
 Parameter summary lattice (per callee parameter): 0 `n` does not escape  <  1 `r` may be returned (eax) but does not otherwise escape  <  2 `e` escapes.
 Least fixed point from 0 (= greatest set of non-escaping parameters). Transfer: a sink of a parameter value contributes
 e for store / indirect call / unknown use, r for return, and for a call (callee, par) the callee's own value if it is e, else 0;
 a conditional sink (X, cond, s) contributes s only when some (callee, par) in cond is >= r (the callee may hand the address back in eax,
 so the caller's eax is the same frame address). Imports come from FNVNX_IMPORT_ESCAPE (dll, name, args over {n, r, e}); missing = all e.
 ro-const (IAT, .rdata, jump tables) is plain; TLS stays ordered.
 STACK CLASSES: what is PROVED and what is ASSUMED (an access whose coordinate is not proved is class `stack-unproven` = ORDERED, never plain)
  PROVED by x86flow.track (total tracking): esp / ebp = entry_esp + constant at an instruction. esp: every esp write is modelled (push pop pushal popal call leave
   enter ret, add/sub esp imm, mov esp ebp, lea esp [ebp+d]); any other write (and, mov esp reg, xchg, pop esp, ...) opens a new epoch (unresolved). ebp: the value of esp
   at a `mov ebp, esp`, lost by ANY other write of ebp (capstone's explicit and implicit write sets: pop, leave, popal, xchg, xadd, mov, ...) and at joins that differ.
   A function can prove for ITSELF (x86flow.conformance) that it returns with esp balanced and ebp restored; this is only MEASURED here (a closure over the direct-call
   graph that enforced it would order 62 % of all accesses): callees obeying the calling convention is an ASSUMPTION, listed below.
  stack-local   plain iff the coordinate is proved (c < 0, or the return-address slot 0 <= c < 4) and no frame address of the activation escapes into that slot's object
                (rules 1, 2): the slot's address can only be created by this activation (own events; callee-created addresses of it come from a pointer parameter, whose
                summaries are in the flow). Accesses below esp (call pushes the return address at s-4) are fresh stores: no live pointer reaches them.
  stack-args-out plain (a store into a fresh slot below the caller's esp, before the callee runs; contamination as above; a pointer published later by the callee is
                released by an ordered store, which orders this earlier store).
  stack-args-in ORDERED: the slot belongs to the CALLER's frame; the caller may have created its address (an event of the caller contaminates its own `o` slot, but this
                analysis of the callee does not see that). Plain would need a caller-side proof for every possible caller (indirect callers are unknown): not available.
  stack-funclet ORDERED: a funclet's ebp = the parent's frame pointer is the protocol of the EH runtime (the lifted L3 runtime), not provable from the bytes.
  stack-unproven ORDERED: unresolved esp / ebp (ebp as a general register, alloca, `and esp, -16`, esp from a register, indexed esp/ebp addressing, data decoded as code),
                funclet esp, poisoned entries.
  ASSUMED, not proved: EVERY callee (direct, indirect, import) pops esp as RETN says and preserves ebp (conformance counts how many prove it themselves); the guest does not use a frame slot through a dangling
                pointer after its frame is gone; stack walks by the EH runtime run on the same thread. Count-only stdout; per-entry TSV in /home/user/l3facts/memclass/."""
import os, sys, json, bisect, hashlib
from array import array
from collections import Counter, defaultdict
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x86flow as W
from x86flow import X, M32, mn, rn, base, memslot, Fn, FUNK, KINDS, THUNK, IATADDR, track, frame_events, load_universe, pmap

EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
IMPTAB = os.environ.get('FNVNX_IMPORT_ESCAPE', W.FACTS + '/import_escape.tsv')
IMPESC = {}                  # import name -> per-argument string over n / r / e (conservative default: all e)
if os.path.exists(IMPTAB):
    for _ln in open(IMPTAB).read().splitlines():
        _f = _ln.split('\t')
        if len(_f) >= 3 and not _ln.startswith('#'):
            IMPESC[_f[1]] = _f[2]
OUT = os.environ.get('FNVNX_MEMCLASS_OUT', W.FACTS + '/memclass')
NPROC = int(os.environ.get('FNVNX_PROCS', '2'))
STK = ('stack-local', 'stack-args-in', 'stack-args-out', 'stack-funclet', 'stack-unproven', 'stack-escaped', 'stack-unk')
ORDER = STK + ('ro-iat', 'ro-rdata', 'ro-text', 'tls', 'tls-derived', 'global', 'heap/other')
PLAIN = ('stack-local', 'stack-args-out', 'ro-iat', 'ro-rdata', 'ro-text')      # args-in and funclet frames are ordered: see "stack classes" in the docstring
WID = (1, 2, 4, 8, 10, 16)
REGS = ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi')
IMPL = frozenset('mul imul div idiv cdq cwde cwd cbw lodsb lodsw lodsd stosb stosw stosd scasb scasw scasd xchg cmpxchg xadd lahf sahf xlatb'.split())
BEFORE = {'stack-local': 44.6, 'stack-args-in': 5.3, 'stack-args-out': 9.8, 'stack-escaped': 7.6, 'stack-unk': 11.1}   # v1 run (recorded)


def absclass(a, store):
    for n, va, vs, ro, rs, ch in W.IMG.secs:
        if va <= a < va + max(vs, rs):
            if a in IATADDR and not store:
                return 'ro-iat'
            return 'global' if ch & 0x80000000 or store else 'ro-text' if ch & 0x20000000 else 'ro-rdata'
    return 'heap/other'


def tls_taint(F, i, m, o, r0, taint):
    """taint {reg: 'A' (fs:[0x2c] array pointer) | 'S' (element loaded through it)}; returns an offence if an 'A' value leaves in a register."""
    nm = lambda p: i.reg_name(p.reg) if p.type == X.X86_OP_REG else None
    if m == 'push' and o[0].type == X.X86_OP_MEM and o[0].mem.segment and o[0].mem.disp == 0x2c:
        return 'push fs:[2c]'
    if m in ('call', 'ret', 'retf'):
        bad = [r for r in ('eax', 'ecx', 'edx') if taint.get(r) == 'A'] if m == 'call' else ['eax'] if taint.get('eax') == 'A' else []
        for r in ('eax', 'ecx', 'edx'):
            taint.pop(r, None)
        return m + ' with tainted ' + bad[0] if bad else None
    new = None
    if m == 'mov' and r0 and o[1].type == X.X86_OP_MEM:
        mm = o[1].mem
        if mm.segment and i.reg_name(mm.segment) == 'fs' and not mm.base and not mm.index and mm.disp == 0x2c:
            new = 'A'
        elif (mm.base and taint.get(i.reg_name(mm.base)) == 'A') or (mm.index and taint.get(i.reg_name(mm.index)) == 'A'):
            new = 'S'
    elif m == 'mov' and r0 and o[1].type == X.X86_OP_REG and taint.get(nm(o[1])) == 'A':
        new = 'A'
    elif m == 'lea' and r0 and o[1].mem.base and taint.get(i.reg_name(o[1].mem.base)) == 'A':
        new = 'A'
    off = None
    for k, p in enumerate(o):
        if p.type == X.X86_OP_REG and taint.get(nm(p)) == 'A' and p.access & 1 and m not in ('cmp', 'test') and not (m == 'mov' and k == 1 and r0 and o[0].type == X.X86_OP_REG):
            if not (m in ('add', 'sub', 'inc', 'dec') and k == 0):
                off = off or '%s uses tainted %s' % (m, nm(p))
    if r0 and o[0].access & 2 and o[0].type == X.X86_OP_REG:
        taint.pop(r0, None)
        if new:
            taint[r0] = new
    return off


def argreach(F):
    """reach[x]: a call follows x on the fall-through chain (x = outgoing-argument push candidate); saved: entry register saves."""
    reach = {}
    for x in sorted(F.ins, reverse=True):
        i = F.ins[x]; m = mn(i)
        if m == 'call':
            reach[x] = True
        elif m in ('pop', 'ret', 'retf', 'jmp') or m[0] == 'j' or (m == 'add' and rn(i, i.operands[0]) == 'esp') or len(F.succ[x]) != 1:
            reach[x] = False
        else:
            reach[x] = reach.get(F.succ[x][0], False)
    saved, k = [], F.a
    while k in F.ins and len(F.succ[k]) == 1:
        i = F.ins[k]; m = mn(i)
        if m == 'push' and rn(i, i.operands[0]) in ('ebp', 'ebx', 'esi', 'edi'):
            saved.append(k)
        elif not (m == 'mov' and rn(i, i.operands[0]) == 'ebp') and not (m == 'sub' and rn(i, i.operands[0]) == 'esp'):
            break
        k = F.succ[k][0]
    return reach, frozenset(saved)


def target(F, x):
    """sink of a value passed at the call/tail jump x: ('A', callee) | ('I',) | ('M', import)."""
    o = F.ins[x].operands[0]
    if o.type == X.X86_OP_IMM:
        t = o.imm & M32
        return ('M', IATADDR.get(THUNK[t], '?')) if t in THUNK else ('A', t)
    if o.type == X.X86_OP_MEM and not o.mem.base and not o.mem.index and (o.mem.disp & M32) in IATADDR:
        return ('M', IATADDR[o.mem.disp & M32])
    return ('I',)


def pusharg(F, x):
    """(call address, argument index) of the push at x, or None."""
    k = x
    for _ in range(40):
        k = F.succ[k][0] if len(F.succ.get(k, ())) == 1 else None
        if k is None:
            return None
        if mn(F.ins[k]) == 'call':
            a, b = F.sp.get(k), F.sp.get(x)
            if a is None or b is None or F.multi:
                a, b, j = 0, 0, x                        # fall back to the local chain: sum the known stack deltas from the push to the call
                while j != k:
                    q = F.ins[j]; mq = mn(q); cp = W.callpop(F, j) if mq == 'call' else None
                    d = -4 if mq == 'push' else 4 if mq == 'pop' else (q.operands[1].imm * (1 if mq == 'add' else -1) if mq in ('add', 'sub') and rn(q, q.operands[0]) == 'esp' and q.operands[1].type == X.X86_OP_IMM else None) if mq in ('add', 'sub') and rn(q, q.operands[0]) == 'esp' else 0
                    if d is None or (cp and cp[0] != 'c'):
                        return None
                    a += d + (cp[1] if cp else 0); j = F.succ[j][0]
            return (k, (b - a - 4) // 4) if b - a - 4 >= 0 else None
        if mn(F.ins[k]) in ('pop', 'ret', 'jmp') or mn(F.ins[k])[0] == 'j':
            return None
    return None


def imp_val(name, q):
    """level of parameter q of an import from the table: n = 0, r = 1, e = 2 (missing entry, register parameter or no table: e)."""
    st = IMPESC.get(name, '')
    return {'n': 0, 'r': 1, 'e': 2}.get(st[int(q[1:])] if q[0] == 's' and int(q[1:]) < len(st) else 'e', 2)


def sink(F, x, par):
    """sink of the value passed in parameter par at the call / tail jump x: ('A', callee, par) | ('M', import, par) | ('I',)."""
    t = target(F, x)
    return t + (par,) if t[0] in ('A', 'M') else t


def skey(sk):
    """summary key of a call sink: callee address, or 'imp:Name' for an import."""
    return '%08x' % sk[1] if sk[0] == 'A' else 'imp:' + sk[1]


def fcoord(F, a, p):
    """entry-relative coordinate of a direct esp/ebp memory operand at a, or None."""
    i = F.ins[a]
    if p.type != X.X86_OP_MEM or not p.mem.base or p.mem.index or F.multi:
        return None
    bs = i.reg_name(p.mem.base)
    v = F.sp.get(a) if bs == 'esp' else F.bp.get(a) if bs == 'ebp' else None
    return None if v is None else v + p.mem.disp


def flow(F, x0, regs, saved, entry=False, spill=False, slots0=()):
    """Sinks of a frame / parameter address held in regs after x0 (or at entry).
    Sink = ('S',) stored to non-frame memory | ('R',) returned in eax | ('I',) indirect call | ('U', why) not understood |
           ('M', import, par) / ('A', callee, par) passed as parameter par of an import / direct callee |
           ('X', cond, sink): the inner sink applies only if one of the (callee, par) pairs in cond may RETURN its parameter.
    Conditional return flow: after a direct call that received the tracked value in (callee, par), eax holds the same frame
    address iff the callee's summary for par is `returned` (or worse); the flow continues with eax under that condition.
    State: tracked registers / private frame slots, each with its condition; pending = pushed arguments awaiting their call."""
    sinks, seen = set(), set()
    T0 = frozenset((r, ()) for r in regs)
    S0 = frozenset((c, ()) for c in slots0)
    work = [(x0, T0, S0, frozenset())] if entry else [(s, T0, S0, frozenset()) for s in F.succ[x0]]

    def emit(cond, sk):
        sinks.add(('X', cond, sk) if cond else sk)

    def addc(cond, key):
        # only the latest hand-back is kept: activating a sink when only the latest callee returns (not the whole chain) over-approximates
        return (key,)
    while work:
        a, T, S, P = work.pop()
        if (a, T, S, P) in seen:
            continue
        seen.add((a, T, S, P))
        if len(seen) > 1000:
            sinks.add(('U', 'limit'))
            break
        i = F.ins[a]
        m = mn(i)
        o = i.operands
        N, NS, NP = set(T), set(S), set(P)
        reg = lambda p: base(i.reg_name(p.reg)) if p.type == X.X86_OP_REG else None
        mc = [fcoord(F, a, p) for p in o]
        conds = lambda r: [c for q, c in T if q == r]                       # conditions under which register r holds the value
        sconds = lambda co: [c for q, c in S if q == co]                    # same for a private frame slot
        if S and m not in ('mov', 'push', 'cmp', 'test'):
            for co in mc:
                for c in (sconds(co) if co is not None else []):
                    emit(c, ('U', 'slot-use'))
        if m == 'call' or (m == 'jmp' and (a in F.tails or not F.succ[a])):
            tg = target(F, a)
            retc = set()
            for r, cond in sorted(T):
                if r in REGS and (m == 'jmp' or r in ('eax', 'ecx', 'edx') or tg[0] not in ('A',)):
                    sk = sink(F, a, r)
                    emit(cond, sk)
                    if m == 'call' and (tg[0] == 'A' or (tg[0] == 'M' and imp_val(tg[1], r) < 2)):    # an import that already escapes it needs no hand-back
                        retc.add(addc(cond, (skey(sk), r)))
            for k, cond in sorted(P):
                if k == a:
                    retc.add(cond)
            NP = {(k, c) for k, c in NP if k != a}
            N = set() if m == 'jmp' else {(r, c) for r, c in N if r not in ('eax', 'ecx', 'edx')} | {('eax', c) for c in retc}
        elif m in ('ret', 'retf'):
            for c in conds('eax'):
                emit(c, ('R',))
            N = set()
        elif m == 'push' and (conds(reg(o[0])) or (mc[0] is not None and sconds(mc[0]))):
            if a not in saved:
                pa = pusharg(F, a)
                for cond in (conds(reg(o[0])) if o[0].type == X.X86_OP_REG else []) + (sconds(mc[0]) if mc[0] is not None else []):
                    if pa is None:
                        emit(cond, ('U', 'pusharg'))
                    else:
                        sk = sink(F, pa[0], 's%d' % pa[1])
                        emit(cond, sk)
                        if sk[0] == 'A' or (sk[0] == 'M' and imp_val(sk[1], sk[2]) < 2):
                            NP.add((pa[0], addc(cond, (skey(sk), sk[2]))))
        elif m in ('mov', 'lea') and o[0].type == X.X86_OP_REG and base(i.reg_name(o[0].reg)) is not None:
            d = reg(o[0])
            add = []
            if m == 'mov' and o[1].type == X.X86_OP_REG:
                add = conds(reg(o[1]))
            elif m == 'lea':
                add = (conds(base(i.reg_name(o[1].mem.base))) if o[1].mem.base else []) + (conds(base(i.reg_name(o[1].mem.index))) if o[1].mem.index else [])
            elif m == 'mov' and mc[1] is not None:
                add = sconds(mc[1])
            if add or o[0].size == 4:
                N = {(r, c) for r, c in N if r != d} | {(d, c) for c in add}
        elif m == 'mov' and o[0].type == X.X86_OP_MEM and len(o) > 1 and conds(reg(o[1])):
            mm = o[0].mem
            if spill and mc[0] is not None and not (mm.segment and i.reg_name(mm.segment) == 'fs'):
                NS = {(q, c) for q, c in NS if q != mc[0]}          # one strong update of the slot, then EVERY condition of the stored register joins it (a per-condition update kept only the last one in set order)
            for cond in conds(reg(o[1])):
                if mm.segment and i.reg_name(mm.segment) == 'fs':
                    pass
                elif spill and mc[0] is not None:
                    NS = NS | {(mc[0], cond)}
                elif mm.base and i.reg_name(mm.base) in ('esp', 'ebp') and not mm.index:
                    emit(cond, ('U', 'spill'))
                else:
                    emit(cond, ('S',))
        elif m in ('cmp', 'test'):
            pass
        elif m == 'mov' and o[0].type == X.X86_OP_MEM and mc[0] is not None and sconds(mc[0]):
            NS = {(q, c) for q, c in NS if q != mc[0]}
        else:
            for k, p in enumerate(o):
                r = reg(p)
                if conds(r) and p.access & 1 and not (k == 0 and m in ('add', 'sub', 'and', 'or', 'inc', 'dec', 'xor') and (len(o) < 2 or o[1].type == X.X86_OP_IMM)):
                    for c in conds(r):
                        emit(c, ('U', 'use:' + m))
                elif conds(r) and p.access & 2 and k == 0 and not p.access & 1:
                    N = {(q, c) for q, c in N if q != r}
            if m in IMPL:
                for c in conds('eax'):
                    emit(c, ('U', 'implicit-eax'))
        if N or NS or NP:
            work += [(s, frozenset(N), frozenset(NS), frozenset(NP)) for s in F.succ[a]]
    return sinks


def livein(F, saved):
    """registers possibly read (or passed on at a call / tail jump) before being written, from the entry."""
    seen, work, rd = set(), [(F.a, frozenset(REGS))], set()
    while work:
        a, T = work.pop()
        if (a, T) in seen or not T:
            continue
        seen.add((a, T))
        i = F.ins[a]; m = mn(i); o = i.operands; N = set(T)
        used = {base(i.reg_name(p.reg)) for p in o if p.type == X.X86_OP_REG and p.access & 1}
        used |= {base(i.reg_name(p.mem.base)) for p in o if p.type == X.X86_OP_MEM and p.mem.base} | {base(i.reg_name(p.mem.index)) for p in o if p.type == X.X86_OP_MEM and p.mem.index}
        if m in IMPL:
            used.add('eax')
        if m in ('xor', 'sub') and len(o) == 2 and o[0].type == o[1].type == X.X86_OP_REG and o[0].reg == o[1].reg:
            used = set()
        if m == 'call':
            used |= {'eax', 'ecx', 'edx'}
        if m == 'jmp' and a in F.tails:
            used |= set(REGS)
        if a in saved:
            used = set()
        rd |= used & T
        for p in o[:1]:
            if p.type == X.X86_OP_REG and p.access & 2 and not p.access & 1 and p.size == 4 and m != 'push':
                N.discard(base(i.reg_name(p.reg)))
        if m == 'call':
            N -= {'eax', 'ecx', 'edx'}
        work += [(s, frozenset(N)) for s in F.succ[a]]
    return sorted(rd)


def analyze(args):
    a, ext, kind = args
    F = Fn(a, ext, kind)
    if not F.ok:
        return a, None
    # fact: per-instruction esp/ebp offsets (or a named reason); funclets are tracked with ebp = the parent's frame pointer
    track(F)
    fun = kind in FUNK
    # fact: conformance of this entry as a CALLEE (proved from the tracking, W.conformance), and its direct call edges
    ret_ok, ebp_ok = W.conformance(F)
    callees = sorted({target(F, x)[1] for x in F.ins if (mn(F.ins[x]) == 'call' or (mn(F.ins[x]) == 'jmp' and x in F.tails)) and target(F, x)[0] == 'A'})
    # fact: which pushes feed a call (outgoing arguments) and which are entry register saves
    reach, saved = argreach(F)
    nf, fr, misc, ev, srcs, taint, prev, offs, cidx = [], [], [], [], [], {}, None, [], False
    allstack = F.multi and not fun
    aw = {'multi'} if allstack else set()
    for x in sorted(F.ins):
        i = F.ins[x]; m = mn(i); o = i.operands; s = F.sp.get(x); b = F.bp.get(x)
        if F.preds.get(x, []) != [prev]:
            taint = {}
        prev = x
        r0 = rn(i, o[0]) if o else None
        ok = s is not None and not F.multi
        if m in ('push', 'pushfd'):
            if fun or not ok:
                fr.append((x, 1, 4, 'u', 0))
            else:
                fr.append((x, 1, 4, 'o' if reach.get(x) and x not in saved else 'l', s - 4))
        elif m in ('pop', 'popfd'):
            fr.append((x, 0, 4, 'u' if fun or not ok else 'l', 0 if fun or not ok else s))
        elif m == 'call':                  # the return address is stored below esp: slot s-4 of this frame
            fr.append((x, 1, 4, 'u' if fun or not ok else 'l', 0 if fun or not ok else s - 4))
        elif m in ('ret', 'retf'):         # the return address is loaded from slot s (0 when the frame is balanced)
            fr.append((x, 0, 4, 'u' if fun or not ok else 'r', 0 if fun or not ok else s))
        elif m == 'leave':
            fr.append((x, 0, 4, 'u' if fun or b is None or F.multi else 'r', 0 if fun or b is None or F.multi else b))
        if taint or any(p.type == X.X86_OP_MEM and p.mem.segment and p.mem.disp == 0x2c for p in o):
            td = tls_taint(F, i, m, o, r0, taint)
            if td:
                offs.append((x, td))
        if i.prefix[0] == 0xf0:
            misc.append((x, 'lock'))
            misc.append((x, 'lockm:' + m + (':mem' if any(p.type == X.X86_OP_MEM for p in o) else ':reg')))
        if m in ('xchg', 'cmpxchg', 'xadd') and any(p.type == X.X86_OP_MEM for p in o):
            misc.append((x, m))
        if m == 'call':
            t = target(F, x)
            if t[0] == 'M':
                misc.append((x, 'imp:' + t[1]))
        for p in o:
            if p.type != X.X86_OP_MEM or m in ('lea', 'nop') or m.startswith('prefetch'):
                continue
            ld, stt = bool(p.access & 1), bool(p.access & 2)
            if m[0] == 'f' and m not in ('fxsave', 'fxrstor'):
                stt = m.startswith(('fst', 'fist', 'fnst', 'fbst', 'fnsave', 'fsave')); ld = not stt
            if m in ('fxsave', 'stmxcsr'):
                stt, ld = True, False
            if m in ('call', 'jmp', 'push'):
                ld, stt = True, False
            if m == 'pop':
                ld, stt = False, True
            mm = p.mem; bs = i.reg_name(mm.base) if mm.base else None; sg = i.reg_name(mm.segment) if mm.segment else None
            ix = i.reg_name(mm.index) if mm.index else None
            dv = bool(taint) and (taint.get(bs) == 'A' or taint.get(ix) == 'A')
            for ls, on in ((0, ld), (1, stt)):
                if not on:
                    continue
                w = p.size
                if sg == 'fs':
                    nf.append((x, ls, w, 'tls:%x' % (mm.disp & M32) if bs is None and ix is None else 'tls:reg'))
                elif dv:
                    nf.append((x, ls, w, 'tls-derived'))
                elif bs in ('esp', 'ebp'):
                    v = s if bs == 'esp' else b
                    if ix is not None:
                        cidx = True
                        fr.append((x, ls, w, 'u', 0))
                    elif fun:
                        fr.append((x, ls, w, 'p' if bs == 'ebp' and v is not None else 'u', (v + mm.disp) if bs == 'ebp' and v is not None else 0))
                    elif v is None or F.multi:
                        fr.append((x, ls, w, 'u', 0))
                    else:
                        c = v + mm.disp
                        fr.append((x, ls, w, 'a' if c >= 4 else 'r' if c >= 0 else 'l', c))
                        if c >= 4 and ls == 0 and not fun and w == 4 and o[0].type == X.X86_OP_REG and m in ('mov', 'push') and p is o[-1]:
                            srcs.append((x, base(i.reg_name(o[0].reg)), 's%d' % ((c - 4) // 4)))
                        elif c >= 4 and ls == 0 and m == 'push':
                            srcs.append((x, None, 's%d' % ((c - 4) // 4)))
                elif bs is None and ix is None:
                    nf.append((x, ls, w, absclass(mm.disp & M32, bool(ls))))
                elif bs is None:
                    nf.append((x, ls, w, absclass(mm.disp & M32, bool(ls))))
                else:
                    nf.append((x, ls, w, 'heap/other'))
            if bs in ('esp', 'ebp') and not ok and not fun:
                allstack = allstack or ix is not None
                if ix is not None:
                    aw.add('indexed')
        if kind not in FUNK and (any(p.type == X.X86_OP_MEM and p.mem.base and i.reg_name(p.mem.base) in ('esp', 'ebp') and not p.mem.index and p.access & 3 and s is None and F.bp.get(x) is None for p in o)):
            allstack = True; aw.add('unresolved-esp')
    # fact: every place a frame address is created; each gets a flow (sinks) over registers / private frame slots
    fe = frame_events(F)
    allstack = allstack or (not fun and any(c is not None and c >= 4 for x, c, idx, dst, sl in fe))
    if not fun and any(c is not None and c >= 4 for x, c, idx, dst, sl in fe):
        aw.add('argarea-address')
    evflows = lambda sp_: [(c, idx, sorted(flow(F, x, [dst], saved, False, sp_)) if dst else sorted(flow(F, x, [], saved, False, True, [sl])) if sl is not None and sp_ else [('U', 'spill' if sl is not None else 'push/store-esp')]) for x, c, idx, dst, sl in fe]
    events = evflows(False)
    base_ok = not fun and not F.multi and not cidx and not any(f[3] == 'u' for f in fr)
    if base_ok and any(sk for c, idx, sk in events) and all(all(t == ('U', 'spill') for t in sk) for c, idx, sk in events):
        events = evflows(True)               # closed under the assumption that frame slots are private; verified by the re-run
    # fact: per-parameter sinks of the value received at entry (registers read before written; stack slots loaded)
    summ = {}
    spill = base_ok and not any(e[2] for e in events)
    if not fun:
        for r in livein(F, saved):
            summ[r] = sorted(flow(F, F.a, [r], saved, True, spill))
        for x, d, par in srcs:
            if d is None:
                pa = pusharg(F, x)
                sk = [('U', 'pusharg')] if pa is None else [sink(F, pa[0], 's%d' % pa[1])]
            else:
                sk = sorted(flow(F, x, [d], saved, False, spill))
            summ[par] = sorted(set(summ.get(par, [])) | set(sk))
        for t in sorted({t for x in F.tails for t in F.tails[x] if mn(F.ins[x]) == 'jmp'}):
            summ.setdefault('fwd', []).append(t)
    bpv = sorted({v for v in F.bp.values() if v is not None})
    return a, dict(addrs=array('I', sorted(F.ins)).tobytes(), nf=nf, fr=fr, misc=misc, ev=events, summ=summ, allstack=allstack, aw=sorted(aw), cidx=cidx, multi=F.multi,
                   why=F.why, bpv=bpv[0] if len(bpv) == 1 else None, offs=offs, undecoded=not F.ok, ret_ok=ret_ok, ebp_ok=ebp_ok, callees=callees)


def hx(v):
    return int(v, 16) if isinstance(v, str) else v


def parents_map(rows):
    par = {}
    for a, e, k in rows:
        for t in W.FLAGS.get(a, '').split(','):
            if t.startswith('parent='):
                par.setdefault(a, []).append(int(t[7:], 16))
    d = json.load(open(W.EHJ))
    for fi in d['funcinfo']:
        for st, u in fi['unwind']:
            par.setdefault(hx(u), []).extend(hx(o) for o in fi['owners'])
    for sc in d['scopes']:
        for rec in sc['records']:
            for v in rec[1:]:
                par.setdefault(hx(v), []).extend(hx(o) for o in sc['owners'])
    return {a: sorted(set(v)) for a, v in par.items()}


def main():
    raw, rows = load_universe(EXE)
    key = hashlib.sha256(open(EXE, 'rb').read()).hexdigest()[:16] + hashlib.md5(raw).hexdigest()[:16]
    print('memclass: exe sha256 %s  universe.tsv md5 %s  entries %d' % (key[:12], key[16:28], len(rows)))
    kind = {a: k for a, e, k in rows}
    with Pool(NPROC) as p0:
        W.prepare(p0, rows, key)
    with Pool(NPROC) as pool:
        out = pmap(pool, analyze, [(a, e, k) for a, e, k in rows], 100)
    R = {a: r for a, r in out if r}
    # every instruction address is counted once: decomp entries first, then by address
    order = sorted(R, key=lambda a: (kind[a] != 'decomp', a))
    owner = {}
    for a in order:
        for x in array('I', R[a]['addrs']):
            owner.setdefault(x, a)
    # funclets belong to their parent frame
    par = parents_map(rows)
    # ---- conformance closure over the direct-call graph. A frame coordinate (esp / ebp = entry esp + const) is only meaningful after a call if the callee returns with esp
    # balanced and ebp restored. Each entry proves both for ITSELF (W.conformance: ret_ok, ebp_ok); bad = entries that do not, closed upwards: a caller of a bad or unknown
    # direct callee is bad. A caller of a bad callee is POISONED: its coordinates after that call are not proved, so every stack access of it is class `unproven` (ordered).
    # Assumed (not proved): indirect calls and imports obey the same convention (Win32 stdcall: ebp / ebx / esi / edi preserved, esp popped by the callee as RETN says).
    bad = {f for f in R if not (R[f]['ret_ok'] and R[f]['ebp_ok'])}
    unknown = {f for f in R if kind[f] not in FUNK and any(c not in R for c in R[f]['callees'])}
    rev = defaultdict(list)
    for f in sorted(R):
        for c in R[f]['callees']:
            rev[c].append(f)
    poison, work = set(unknown), sorted(bad)
    inbad = set(bad) | set(unknown)
    for f in sorted(unknown):
        work.append(f)
    while work:
        c = work.pop()
        for f in rev.get(c, ()):
            if kind[f] not in FUNK and f not in poison:
                poison.add(f); work.append(f); inbad.add(f)
    npoison, poison = len(poison), set()      # MEASURED only: enforcing it poisons 38,450 entries (62 % of accesses ordered); the ABI of callees stays an ASSUMPTION (docstring)
    nbad = Counter(('ret' if not R[f]['ret_ok'] else '') + ('ebp' if not R[f]['ebp_ok'] else '') for f in bad)
    for f in sorted(poison):                                     # the sinks of a poisoned entry were computed from coordinates that are not proved: all pessimistic
        R[f]['ev'] = [(c, idx, [('U', 'poisoned')]) for c, idx, sk in R[f]['ev']]
        R[f]['summ'] = {q: ([('U', 'poisoned')] if q != 'fwd' else v) for q, v in R[f]['summ'].items()}
    # ---- parameter summaries (lattice n < r < e, least fixed point from n)
    keys = [(f, p) for f in sorted(R) for p in sorted(R[f]['summ']) if p != 'fwd']

    def solve(old):
        """old=True reproduces the previous rule set: a returned parameter counts as escaping and there is no conditional flow."""
        val, cause = {}, {}

        def getv(t, q, seen=()):
            if isinstance(t, str) and t.startswith('imp:'):
                return imp_val(t[4:], q)
            t = int(t, 16) if isinstance(t, str) else t
            if t not in R or (R[t]['allstack'] and q[0] == 's'):
                return 2
            v = val.get((t, q), 0)
            for u in R[t]['summ'].get('fwd', []):                       # tail-jump forwarding: same parameter of the target
                if u not in seen and u != t:
                    v = max(v, getv(u, q, seen + (t,)))
            return v

        def why(s_):
            """(level, cause) of one sink."""
            k = s_[0]
            if k == 'S':
                return 2, 'stored'
            if k == 'I':
                return 2, 'indirect'
            if k == 'U':
                return 2, 'unk:' + s_[1].split(':')[0]
            if k == 'R':
                return (2, 'returned') if old else (1, 'returned')
            if k in ('A', 'M'):
                t = skey(s_)
                v = getv(t, s_[2])
                c = 'import:' + s_[1] if k == 'M' else cause.get((int(t, 16), s_[2]), 'callee')
                return (2, c if c.startswith('import:') else 'callee') if v == 2 else (0, '')
            if old:
                return 0, ''
            return why(s_[2]) if any(getv(t, q) >= 1 for t, q in s_[1]) else (0, '')
        rounds = 0
        while True:
            changed = False
            for f, p in keys:
                v = val.get((f, p), 0)
                if v == 2:
                    continue
                nv, nc = v, ''
                if R[f]['allstack'] and p[0] == 's':
                    nv, nc = 2, 'allstack'
                for s_ in R[f]['summ'][p]:
                    lv, c = why(s_)
                    if lv > nv:
                        nv, nc = lv, c
                if nv > v:
                    val[(f, p)] = nv
                    cause[(f, p)] = nc
                    changed = True
            if not changed:
                break
            rounds += 1
        return val, cause, getv, why, rounds
    SOLVE = {True: solve(True), False: solve(False)}
    val, cause, getv, why, rounds = SOLVE[False]
    npar = Counter(val.get(k, 0) for k in keys)
    pcause = Counter(cause[k] for k in keys if val.get(k, 0) == 2)

    def verdict(sk, old):
        """reasons (sorted strings) for which the frame-address sinks sk of one event escape; a returned frame address always escapes."""
        w = SOLVE[old][3]
        r = []
        for s_ in sk:
            while s_[0] == 'X' and not old and any(SOLVE[old][2](t, q) >= 1 for t, q in s_[1]):
                s_ = s_[2]
            if s_[0] == 'X':
                continue
            lv, c = w(s_)
            if s_[0] == 'R':
                lv, c = 2, 'returned'
            if lv == 2:
                r.append('unknown:' + s_[1] if s_[0] == 'U' else c)
        return sorted(set(r))
    fro = defaultdict(list)
    for f in sorted(R):
        for p in (par.get(f, []) if kind[f] in FUNK else [f]):
            if p in R and kind[p] not in FUNK:
                fro[p].append(f)
    frame = {}
    for g in sorted(R):
        evs, bnd, whole, esc = [], set(), False, Counter()
        for f in fro.get(g, []):
            r = R[f]; fun = f != g
            bp = R[g]['bpv']
            whole = whole or (r['multi'] and not fun) or r['cidx']
            for c, idx, sk in r['ev']:
                if f in poison:
                    c = None                                    # coordinates are not proved after a call to a non-conforming callee
                elif fun:
                    c = None if c is None or bp is None else bp + c
                evs.append((c, idx, verdict(sk, False), bool(sk), verdict(sk, True)))
            bnd.update(c if not fun else (bp + c if bp is not None else 0) for x, ls, w, role, c in r['fr'] if role in 'alorp' and f not in poison)
        frame[g] = (sorted(bnd), evs, whole)
    res_t, per, lost, whatif, retrule = Counter(), {}, Counter(), Counter(), Counter()
    nescf, reasons, rule2, rulef, ureasons = 0, Counter(), Counter(), Counter(), Counter()
    for g in sorted(R):
        bnd, evs, whole = frame[g]
        anyv = [e for e in evs if e[2]]
        if anyv:
            nescf += 1
            reasons[(sorted(r for e in anyv for r in e[2])[0]).split(':')[0]] += 1
            for r in sorted({r for e in anyv for r in e[2] if r.startswith('unknown')}):
                ureasons[r[8:].split(':')[0]] += 1

    def pick(e, mode):
        return e[2] if mode == 'new' else e[4] if mode == 'old' else (e[2] or e[4] or ['callee']) if e[3] else []

    def contam(g, c, mode):
        """reasons for which slot c of frame g is contaminated under rule set mode (new | old | v1 = any sink counts)."""
        bnd, evs, whole = frame[g]
        hit = []
        for e in evs:
            vd = pick(e, mode)
            if not vd:
                continue
            if e[0] is None or e[1] or whole:
                return vd
            j = bisect.bisect_right(bnd, e[0])
            if e[0] <= c and (j >= len(bnd) or bnd[j] > c):
                hit += vd
        return sorted(set(hit))

    def anyesc(g, mode):
        return any(pick(e, mode) for e in frame[g][1])
    tot, ent, imp, fl = Counter(), defaultdict(Counter), Counter(), Counter()
    pending = []
    # R9 export: per instruction address x, over EVERY entry whose extent contains x (not only its owner): the class of each access seen;
    # x is all-plain only if it has >= 1 recorded access and all of them, in all entries, are in PLAIN
    seen, nonplain = set(), set()
    atom = []
    for a in order:
        r = R[a]; mine = lambda x: owner[x] == a
        for x, ls, w, cls in r['nf']:
            seen.add(x)
            if cls not in PLAIN:
                nonplain.add(x)
            if mine(x):
                k = 'tls' if cls.startswith('tls:') else cls
                if cls.startswith('tls:'):
                    fl[cls] += 1
                tot[(k, 'S' if ls else 'L', w if w in WID else 'o')] += 1; ent[a][(k, ls)] += 1
        for x, kd in r['misc']:
            if mine(x):
                fl[kd] += 1
                if kd in ('lock', 'xchg', 'cmpxchg', 'xadd'):
                    atom.append((x, kd))
        for x, ls, w, role, c in r['fr']:
            cnt = mine(x)            # counters below only for the owner; the class is computed for every entry containing x
            if a in poison:
                role = 'u'
            ps = [p for p in (par.get(a, []) if kind[a] in FUNK else [a]) if p in R and kind[p] not in FUNK]
            if not ps:
                cls = 'stack-unk'; res_t['funclet-parent-unknown'] += cnt
            else:
                cl = {}; unp = role not in 'alorp'
                for g in ps:
                    cc = c
                    if kind[a] in FUNK and role == 'p':
                        cc = None if R[g]['bpv'] is None else R[g]['bpv'] + c
                    unp = unp or cc is None or g in poison
                    if role in 'alorp' and cc is not None:
                        v = contam(g, cc, 'new'); v1 = contam(g, cc, 'old'); v0 = contam(g, cc, 'v1')
                    else:
                        v = ['frame'] if anyesc(g, 'new') else []; v1 = ['frame'] if anyesc(g, 'old') else []; v0 = ['frame'] if anyesc(g, 'v1') else []
                    cl[g] = (v, v1, v0)
                if unp:
                    cls = 'stack-unproven'                       # the coordinate of the access is not proved (rule R9: ordered)
                    if cnt:
                        res_t['unproven:' + ('poisoned' if a in poison else 'role-' + role)] += 1
                elif any(c3[0] for c3 in cl.values()):
                    cls = 'stack-escaped'
                    rs_ = sorted({t for c3 in cl.values() for t in c3[0]})
                    for t in rs_ if cnt else ():
                        if t.startswith('import:'):
                            lost[t[7:]] += 1
                    if cnt and len(rs_) == 1 and rs_[0].startswith('import:'):
                        whatif[rs_[0][7:]] += 1
                else:
                    cls = 'stack-funclet' if kind[a] in FUNK else 'stack-args-in' if role == 'a' else 'stack-args-out' if role == 'o' else 'stack-local'
                    if not cnt:
                        pass
                    elif any(c3[1] for c3 in cl.values()):
                        retrule[role] += 1                       # contaminated under the previous rules, plain with conditional return flow
                    elif any(c3[2] for c3 in cl.values()):
                        rule2[role] += 1
            seen.add(x)
            if cls not in PLAIN:
                nonplain.add(x)
            if cnt:
                tot[(cls, 'S' if ls else 'L', w if w in WID else 'o')] += 1; ent[a][(cls, ls)] += 1
    nacc = sum(tot.values())
    pc = {c: sum(v for k, v in tot.items() if k[0] == c) for c in ORDER}
    print('entries decoded %d (undecodable %d); instructions %d (each counted once); memory accesses %d (loads %d, stores %d)' % (
        len(R), len(rows) - len(R), len(owner), nacc, sum(v for k, v in tot.items() if k[1] == 'L'), sum(v for k, v in tot.items() if k[1] == 'S')))
    print('%-15s %9s %9s %6s %6s   widths 1/2/4/8/10/16/o (loads | stores)' % ('class', 'loads', 'stores', 'share', 'v1'))
    for c in ORDER:
        L, S = [sum(v for k, v in tot.items() if k[0] == c and k[1] == t) for t in 'LS']
        print('%-15s %9d %9d %5.1f%% %6s   %s | %s' % (c, L, S, 100.0 * (L + S) / max(nacc, 1), BEFORE.get(c, '-'), '/'.join(str(tot[(c, 'L', w)]) for w in WID + ('o',)), '/'.join(str(tot[(c, 'S', w)]) for w in WID + ('o',))))
    offs = [(x, w) for a in order for x, w in R[a]['offs'] if owner.get(x) == a]
    plain = sum(pc[c] for c in PLAIN)
    # L-TLS (rule R9): a TLS slot may become plain only if NO function stores or passes an address derived from fs:[2Ch]. Today every tls / tls-derived access is ORDERED
    # (not in PLAIN), whatever the taint finds; the taint (tls_taint) only labels class tls-derived. Sites where the array address leaves in a register are listed in tls_escapes.tsv
    # as the facts a future TLS promotion needs; they are not a soundness condition of the plain set. The invariant of the export is asserted below (hard failure).
    tlsc = Counter(w if w == 'push fs:[2c]' else 'call' if w.startswith('call') else 'ret' if w.startswith('ret') else 'use:' + w.split(' ')[0] for x, w in offs)
    print('plain-eligible accesses %d of %d (%.1f %%)  [v1: 46.1 %%]; tls (%d) and tls-derived (%d) are ordered; fs:[2c] array address leaves a register at %d sites (L-TLS facts: %s)' % (
        plain, nacc, 100.0 * plain / max(nacc, 1), pc['tls'], pc['tls-derived'], len(offs), ' '.join('%s=%d' % kv for kv in sorted(tlsc.items()))))
    print('contribution (plain): rule1 locals %d, args-out %d; ORDERED by rule (not plain): args-in %d (caller side not analysed), funclet frames %d (EH runtime protocol not analysed), unproven coordinates %d; rule2 (summary-proved, formerly stack-escaped) %d (by role %s)' % (
        pc['stack-local'], pc['stack-args-out'], pc['stack-args-in'], pc['stack-funclet'], pc['stack-unproven'], sum(rule2.values()), dict(sorted(rule2.items()))))
    print('unproven coordinates by cause: %s' % ' '.join('%s=%d' % kv for kv in sorted(res_t.items()) if kv[0].startswith('unproven:')))
    print('conformance (callee proofs): entries %d, not proved conforming (own code) %d (%s), unknown direct callee %d, would be poisoned (call a non-conforming / unknown callee, closed over direct calls; measured, NOT enforced) %d' % (
        len(R), len(bad), ' '.join('%s=%d' % kv for kv in sorted(nbad.items())), len(unknown), npoison))
    rs = Counter(w for a in order if kind[a] not in FUNK for w in R[a]['why'][:1])
    print('esp-tracking residue: entries with an unresolved instruction %d (non-funclet %d) by first reason: %s; funclets without a known parent frame: %d accesses' % (
        sum(1 for a in order if R[a]['why']), sum(1 for a in order if R[a]['why'] and kind[a] not in FUNK), dict(sorted(rs.items())), res_t['funclet-parent-unknown']))
    print('frames with an escaping address: %d of %d; first reason: %s; frames flagged whole-frame (computed offsets / epochs): %d; entries taking their arg-area address: %d' % (
        nescf, len(frame), dict(sorted(reasons.items())), sum(1 for g in frame if frame[g][2]), sum(1 for a in R if R[a]['allstack'])))
    print('  arg-area flag reasons (entries): ' + ' '.join('%s=%d' % kv for kv in sorted(Counter(w for a in R for w in R[a]['aw']).items())))
    print('  unknown-sink reasons (frames): ' + ' '.join('%s=%d' % kv for kv in sorted(ureasons.items(), key=lambda kv: (-kv[1], kv[0]))[:10]))
    print('parameter summaries: %d n (non-escaping), %d r (returned only), %d e (escaping; root causes: %s); fixed-point rounds %d (old rules: %d)' % (npar[0], npar[1], npar[2], ' '.join('%s=%d' % kv for kv in sorted(pcause.items())), rounds, SOLVE[True][4]))
    print('returned rule: %d accesses plain that the previous rules (returned = escape) contaminated (by role %s); import table: %d imports read from %s' % (sum(retrule.values()), dict(sorted(retrule.items())), len(IMPESC), IMPTAB))
    print('top imports by accesses lost: ' + ' '.join('%s=%d' % kv for kv in sorted(lost.items(), key=lambda kv: (-kv[1], kv[0]))[:20]))
    print('HYPOTHETICAL what-if (not measured with a table): accesses recovered if the pointer parameters of this import alone were n: ' + ' '.join('%s=%d' % kv for kv in sorted(whatif.items(), key=lambda kv: (-kv[1], kv[0]))[:20]))
    print('lock-prefixed by mnemonic: ' + ' '.join('%s=%d' % (k[6:], v) for k, v in sorted(fl.items()) if k.startswith('lockm:')))
    print('atomics: lock %d xchg-mem %d cmpxchg %d xadd %d; tls displacements: %s' % (fl['lock'], fl['xchg'], fl['cmpxchg'], fl['xadd'], ' '.join('%s=%d' % kv for kv in sorted(fl.items()) if kv[0].startswith('tls:'))[:150]))
    ck = lambda n: n.startswith('Interlocked') or n.endswith('CriticalSection') or n in ('TlsGetValue', 'TlsSetValue', 'TlsAlloc', 'CreateThread')
    print('import call sites: ' + ' '.join('%s=%d' % (k[4:], v) for k, v in sorted(fl.items()) if k.startswith('imp:') and ck(k[4:])))
    os.makedirs(OUT, exist_ok=True)
    # R9 export (consumed by fnvlift/memclass.py): instructions ALL of whose recorded memory accesses (explicit operands and implicit ones:
    # push/pop/call/ret/leave stack accesses, string esi/edi accesses) are PLAIN in every entry that contains them. An instruction memclass does
    # not see, or sees with any non-plain access, is not in the set (unknown = ORDERED). Format: sorted little-endian u32 addresses.
    pl = sorted(seen - nonplain)
    with open(OUT + '/plain_insns.bin', 'wb') as f:
        f.write(array('I', pl).tobytes())
    with open(OUT + '/class_totals.tsv', 'w') as f:
        f.write('class\tloads\tstores\tplain\n')
        for c in ORDER:
            f.write('%s\t%d\t%d\t%d\n' % (c, sum(v for k, v in tot.items() if k[0] == c and k[1] == 'L'), sum(v for k, v in tot.items() if k[0] == c and k[1] == 'S'), int(c in PLAIN)))
    plset = set(pl)
    tlssites = {x for a in order for x, ls, w, cls in R[a]['nf'] if cls.startswith('tls') or cls in ('tls-derived', 'tls:reg')}
    if tlssites & plset:                      # export invariant: an instruction with a TLS access (any entry that contains it) is never plain
        print('memclass: INVARIANT VIOLATED: %d instructions with a TLS access are in the plain set (first %08x)' % (len(tlssites & plset), min(tlssites & plset)), file=sys.stderr)
        sys.exit(1)
    with open(OUT + '/tls_escapes.tsv', 'w') as f:      # L-TLS facts: sites where the address of the TLS array (fs:[2Ch]) leaves a register
        f.write('insn\tcause\n')
        for x, w in sorted(offs):
            f.write('%08x\t%s\n' % (x, w))
    with open(OUT + '/atomic_sites.tsv', 'w') as f:      # xchg-mem / lock sites with their class (plain = proven thread-local: no atomic needed)
        f.write('insn\tkind\tclass\n')
        for x, kd in sorted(atom):
            f.write('%08x\t%s\t%s\n' % (x, kd, 'plain' if x in plset else 'ordered'))
    print('R9 instructions: all-plain %d of %d with a recorded memory access (%.1f %%); export %s/plain_insns.bin' % (len(pl), len(seen), 100.0 * len(pl) / max(len(seen), 1), OUT))
    with open(OUT + '/entries.tsv', 'w') as f:
        f.write('entry\tkind\tevents_escaping\twhole\tawhy\t' + '\t'.join(c.replace('/', '_') + '.L\t' + c.replace('/', '_') + '.S' for c in ORDER) + '\n')
        for a in sorted(R):
            f.write('%08x\t%s\t%d\t%d\t%s\t' % (a, kind[a], sum(1 for e in frame[a][1] if e[2]) if a in frame else 0, int(frame[a][2]) if a in frame else 0, ','.join(R[a]['why']) or '-')
                    + '\t'.join('%d\t%d' % (ent[a][(c, 0)], ent[a][(c, 1)]) for c in ORDER) + '\n')


if __name__ == '__main__':
    main()
