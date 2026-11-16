#!/usr/bin/env python3
"""udcert: the #UD certificate of the declared CPU (CLAUDE.md "The declared CPU", third bullet).

    udcert.py --model cpuid_model.json --cleared CLEARED.tsv --lib DIR --out udcert.tsv  IMAGE:PE:FACTS_DIR ...

THE RULE. An instruction whose CPUID feature is cleared in the declared CPU (fnvlift/f_cpufeat.py over verify/cpuid_model.json) is #UD. It is certified unreachable on the
declared machine iff every path of the interprocedural CFG from every entry the facts know (a ROOT) to it crosses an EXCLUDED EDGE: a conditional branch whose condition is decided
by the declared cpuid answer (a value that came out of a `cpuid` instruction of the image itself), the excluded edge being the direction that is not taken under that answer.
  Graph  : node = instruction address (decoded from the facts' ranges.tsv, one decoder). Edges: fall-through, direct jump / branch targets, direct call targets, and the
           call-site -> next instruction edge (the callee is assumed to return: more paths, never fewer). An indirect call or jump has no edge; its possible targets are roots.
  Roots  : every universe entry whose kinds (primary kind and flags of universe.tsv) are not all of call / tail / inner: entry, tls, export, ptr (every relocated pointer), eh-*,
           seh-*, decomp without a direct call. A node whose only incoming flow is the graph's edges is not a root.
  Decided: a forward known-bits dataflow over the nodes that every root path reaches only through a `cpuid` (the region D: reachable from the roots only through a cpuid node).
           Registers hold (known mask, value, taint = the cpuid sites the value came from); the leaf of a cpuid is the known EAX; the answer is DATA (cpuid_model.json through
           fnvlift/cpumodel_bits.model_words), a leaf or a field the model does not give stays unknown. A call, a load, and any instruction that is not modelled makes what it writes unknown
           (capstone regs_access). A jcc whose flags are known and tainted excludes one edge.
  Certified: pruned reachability. A site that no root reaches once the excluded edges are removed is certified; the certificate names the frontier edges (the excluded edges that
           separate the roots from the site), the cpuid site, leaf, register, decisive bits (a bit is decisive iff making it unknown makes the branch undecided) and the declared word.
  Not certified: a row with its reason (U-NOGATE: no cleared feature gates the form, cpuid cannot decide it; U-ROOT: a root path avoids every excluded edge, with the witness root and
           its kinds; U-DEAD: no root reaches the site; U-NODEC: the site is not decoded by the facts). The gate is red on any such row.
No clock, no entropy: every iteration is sorted. The format is `fnvnx-udcert/1`."""
import argparse
import hashlib
import json
import os
import sys
import types
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import x86flow as W                       # noqa: E402  (the one capstone handle: 32-bit, detail on)
from capstone import x86 as X             # noqa: E402

FORMAT = 'fnvnx-udcert/1'
M32 = 0xffffffff
NONROOT = frozenset(('call', 'tail', 'inner'))
KINDS = frozenset(('decomp', 'entry', 'tls', 'export', 'call', 'tail', 'eh-unwind', 'eh-catch', 'eh-cont', 'seh-filter', 'seh-handler', 'seh-frame', 'eh-throw', 'inner', 'ptr'))
ENDS = frozenset(('ret', 'retf', 'iret', 'iretd', 'int3', 'hlt', 'ud2'))
REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')
FLAGS = ('ZF', 'CF', 'SF', 'OF')
CC = {'je': ('ZF', 1), 'jz': ('ZF', 1), 'jne': ('ZF', 0), 'jnz': ('ZF', 0), 'jb': ('CF', 1), 'jc': ('CF', 1), 'jnae': ('CF', 1), 'jae': ('CF', 0), 'jnb': ('CF', 0), 'jnc': ('CF', 0),
      'js': ('SF', 1), 'jns': ('SF', 0), 'jo': ('OF', 1), 'jno': ('OF', 0)}
SETCC = {'sete': 'je', 'setz': 'je', 'setne': 'jne', 'setnz': 'jne', 'setb': 'jb', 'setc': 'jb', 'setae': 'jae', 'setnb': 'jae', 'setnc': 'jae', 'setbe': 'jbe', 'seta': 'ja',
         'sets': 'js', 'setns': 'jns', 'setl': 'jl', 'setge': 'jge', 'setle': 'jle', 'setg': 'jg', 'seto': 'jo', 'setno': 'jno'}
LOW8 = {'al': 'eax', 'cl': 'ecx', 'dl': 'edx', 'bl': 'ebx'}


# ---------------------------------------------------------------- the declared answer (data)
def load_model(path, lib):
    """-> {leaf: {reg: (known, value)}}: the declared cpuid answer, from the one derivation of the model (cpumodel_bits.model_words) as the lifter's f_cpufeat reads it"""
    pkg = types.ModuleType('fnvlift'); pkg.__path__ = [lib]; sys.modules['fnvlift'] = pkg
    from fnvlift.cpumodel_bits import model_words
    m = json.load(open(path))
    w = model_words(m)
    I = lambda x: int(x, 0) if isinstance(x, str) else x
    full = M32
    return {0: {'eax': (full, I(m['max_basic']))},
            1: {'ecx': (full, w['leaf1_ecx']), 'edx': (full, w['leaf1_edx'])},
            0x80000000: {'eax': (full, I(m['max_ext']))},
            0x80000001: {'ecx': (full, w['ext1_ecx']), 'edx': (full, w['ext1_edx'])}}


# ---------------------------------------------------------------- the image and the graph
class Pe:
    """sections of a PE file at a load base"""
    def __init__(s, path, base):
        import struct
        d = s.d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        n = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        s.secs = []
        for i in range(n):
            vs, va, rs, rp = struct.unpack_from('<IIII', d, pe + 24 + opt + 40 * i + 8)
            s.secs.append((base + va, base + va + rs, rp))
        s.secs.sort()

    def read(s, lo, n):
        for a, b, rp in s.secs:
            if a <= lo < b:
                return s.d[rp + lo - a: rp + min(lo + n, b) - a]
        return b''


def load_ranges(path):
    base, rows = None, {}
    for l in open(path):
        l = l.rstrip('\n')
        if l.startswith('#'):
            if 'base=' in l:
                base = int(l.split('base=')[1].split(':')[0].split()[0], 16)
            continue
        if l:
            a, r = l.split('\t')
            rows[int(a, 16)] = sorted((int(x.split('-')[0], 16), int(x.split('-')[1], 16)) for x in r.split(',') if x)
    return base, rows


def load_universe(path):
    out = {}
    for l in open(path):
        if l[0] == '#' or not l.strip():
            continue
        f = l.rstrip('\n').split('\t')
        out[int(f[0], 16)] = (f[1], f[3] if len(f) > 3 else '')
    return out


class Lite:
    """what the graph keeps of an instruction: its size and its mnemonic without prefix words; the capstone object is decoded again on demand (Graph.dec)"""
    __slots__ = ('size', 'm')

    def __init__(s, size, m):
        s.size, s.m = size, m


class Graph:
    def __init__(s, pe, ranges, uni, gate):
        """gate(capstone insn) -> True for an instruction whose CPUID feature the declared CPU clears (the lifter's cpufeat-UD set)"""
        s.pe = pe
        s.ins, s.succ, raw, s.gated = {}, {}, {}, set()
        for e in sorted(ranges):
            rgs = ranges[e]
            work = [e]
            while work:
                a = work.pop()
                if a in s.ins or not any(lo <= a < hi for lo, hi in rgs):
                    continue
                code = pe.read(a, 15)
                i = next(W.md.disasm(code, a, 1), None) if code else None
                if i is None:
                    continue
                s.ins[a] = Lite(i.size, i.mnemonic.split(' ')[-1])
                if gate(i):
                    s.gated.add(a)
                raw[a] = s.next(i, a)
                work.extend(raw[a])
        for a in s.ins:
            s.succ[a] = [t for t in raw[a] if t in s.ins]
        del raw
        s.roots = sorted(e for e in ranges if s.is_root(uni.get(e)) and e in s.ins)
        s.pred = defaultdict(list)
        for a in sorted(s.succ):
            for t in s.succ[a]:
                s.pred[t].append(a)

    def dec(s, a):
        """the capstone instruction at a (a pure function of the image bytes)"""
        return next(W.md.disasm(s.pe.read(a, 15), a, 1))

    @staticmethod
    def is_root(u):
        if u is None:
            return True
        ks = {u[0]} | {x for x in u[1].split(',') if x in KINDS}
        return not (ks & NONROOT) or bool(ks - NONROOT - {'decomp'})

    @staticmethod
    def next(i, a):
        m = i.mnemonic.split(' ')[-1]
        n = a + i.size
        o = i.operands
        if m in ENDS:
            return []
        if m == 'jmp':
            return [o[0].imm & M32] if o and o[0].type == X.X86_OP_IMM else []
        if (m[0] == 'j' or m.startswith('loop')) and o and o[0].type == X.X86_OP_IMM:
            return [n, o[0].imm & M32]
        if m == 'call' and o and o[0].type == X.X86_OP_IMM:
            return [n, o[0].imm & M32]
        return [n]

    def reach(s, starts, block=frozenset(), cut=frozenset()):
        """nodes reachable from starts; a node in `block` is reached but not expanded; an edge (u, v) in `cut` is not followed"""
        seen = set()
        q = deque(x for x in starts if x in s.ins)
        seen.update(q)
        while q:
            a = q.popleft()
            if a in block:
                continue
            for t in s.succ[a]:
                if t not in seen and (a, t) not in cut:
                    seen.add(t); q.append(t)
        return seen


# ---------------------------------------------------------------- known-bits dataflow
UNK = (0, 0, frozenset())
FULL = M32


def rd(i, op, st):
    """(known, value, taint) of a register or immediate operand of width <= 32, else None"""
    if op.type == X.X86_OP_IMM:
        return (FULL, op.imm & M32, frozenset())
    if op.type != X.X86_OP_REG:
        return None
    n = i.reg_name(op.reg)
    if n in W.SUB:
        b, sh, w = W.SUB[n]
        if b not in st[0]:
            return None
        k, v, t = st[0][b]
        mk = (1 << w) - 1
        return ((k >> sh) & mk, (v >> sh) & mk, t)
    return None


def flags_of(k, v, t, width=32):
    """ZF, SF of a result (known mask k, value v), CF = OF = 0 (and / or / xor / test)"""
    mk = (1 << width) - 1
    zf = 0 if v & k & mk else (1 if k & mk == mk else None)
    sf = (v >> (width - 1)) & 1 if k >> (width - 1) & 1 else None
    f = {}
    for name, val in (('ZF', zf), ('SF', sf), ('CF', 0), ('OF', 0)):
        f[name] = None if val is None else (val, t)
    return f


def join_val(a, b):
    k = a[0] & b[0] & ~(a[1] ^ b[1]) & M32
    return (k, a[1] & k, a[2] | b[2])


def join(s1, s2):
    regs = {}
    for r in REGS:
        x, y = s1[0].get(r), s2[0].get(r)
        if x is not None and y is not None:
            j = join_val(x, y)
            if j[0]:
                regs[r] = j
    fl = {}
    for f in FLAGS:
        x, y = s1[1].get(f), s2[1].get(f)
        if x is not None and y is not None and x[0] == y[0]:
            fl[f] = (x[0], x[1] | y[1])
    st = None
    if s1[2] is not None and s2[2] is not None and len(s1[2]) == len(s2[2]):
        st = tuple(None if (x is None or y is None) else (join_val(x, y) if join_val(x, y)[0] else None) for x, y in zip(s1[2], s2[2]))
    return (regs, fl, st)


def cond_value(mn, fl):
    """-> (0|1, taint) when the flags decide the condition `mn`, else None"""
    def f(n):
        return fl.get(n)
    if mn in CC:
        n, want = CC[mn]
        x = f(n)
        return None if x is None else (1 if x[0] == want else 0, x[1])
    if mn in ('jbe', 'ja'):
        c, z = f('CF'), f('ZF')
        t = (c[1] if c else frozenset()) | (z[1] if z else frozenset())
        if (c and c[0]) or (z and z[0]):
            r = 1
            t = (c[1] if c and c[0] else z[1])
        elif c and z:
            r = 0
        else:
            return None
        return (r if mn == 'jbe' else 1 - r, t)
    if mn in ('jl', 'jge', 'jle', 'jg'):
        s, o, z = f('SF'), f('OF'), f('ZF')
        if not (s and o):
            if mn in ('jle', 'jg') and z and z[0]:
                return (1 if mn == 'jle' else 0, z[1])
            return None
        lt = int(s[0] != o[0]); t = s[1] | o[1]
        if mn == 'jl':
            return (lt, t)
        if mn == 'jge':
            return (1 - lt, t)
        if lt:
            return (1 if mn == 'jle' else 0, t)
        if z:
            r = lt | z[0]
            return (r if mn == 'jle' else 1 - r, t | z[1])
        return None
    return None


def havoc_written(i, st):
    regs, fl, stk = dict(st[0]), dict(st[1]), st[2]
    try:
        _, wr = i.regs_access()
    except Exception:
        wr = ()
        regs, fl, stk = {}, {}, None
    for r in wr:
        n = i.reg_name(r)
        if n == 'eflags':
            fl = {}
        elif n in W.SUB:
            b = W.SUB[n][0]
            regs.pop(b, None)
            if b == 'esp':
                stk = None
    return (regs, fl, stk)


def put(regs, r, val):
    if val[0]:
        regs[r] = val
    else:
        regs.pop(r, None)


class Dataflow:
    def __init__(s, g, model, unknown_bits=None):
        s.g, s.model = g, model
        s.unk = unknown_bits or {}                # (cpuid site, register) -> mask made unknown (the decisive-bit probe)

    def cpuid(s, a, st):
        """the registers a cpuid writes: the declared answer for a known leaf (EAX known in full), else unknown; taint = (site, leaf, register)"""
        regs = {r: v for r, v in st[0].items() if r not in ('eax', 'ebx', 'ecx', 'edx')}
        eax = st[0].get('eax')
        if eax is not None and eax[0] == FULL:
            for r, (k, v) in sorted(s.model.get(eax[1], {}).items()):
                k &= ~s.unk.get((a, r), 0)
                if k:
                    regs[r] = (k, v & k, frozenset(((a, eax[1], r),)))
        return (regs, st[1], st[2])

    def step(s, a, st):
        """state after the instruction at a (the fall-through out-state)"""
        i = s.g.dec(a)
        m = i.mnemonic.split(' ')[-1]
        o = i.operands
        regs, fl, stk = dict(st[0]), dict(st[1]), st[2]
        if m == 'cpuid':
            return s.cpuid(a, st)
        if m == 'call':
            return ({}, {}, None)
        two = len(o) == 2
        d = rd(i, o[0], st) if o else None
        reg0 = i.reg_name(o[0].reg) if o and o[0].type == X.X86_OP_REG else None
        full0 = reg0 in REGS
        if m == 'mov' and two and full0:
            v = rd(i, o[1], st)
            put(regs, reg0, v if (v is not None and o[1].type != X.X86_OP_MEM) else UNK)
            return (regs, fl, stk)
        if m in ('xor', 'sub') and two and full0 and o[1].type == X.X86_OP_REG and i.reg_name(o[1].reg) == reg0:
            regs[reg0] = (FULL, 0, frozenset())
            return (regs, {'ZF': (1, frozenset()), 'SF': (0, frozenset()), 'CF': (0, frozenset()), 'OF': (0, frozenset())}, stk)
        if m in ('and', 'or', 'xor', 'test') and two and o[0].type == X.X86_OP_REG and rd(i, o[1], st) is not None and d is not None:
            b = rd(i, o[1], st)
            width = 32 if full0 else None
            if width is None:
                if m == 'test':
                    pass
                else:
                    return havoc_written(i, st)
            ka, va, ta = d; kb, vb, tb = b
            if m in ('and', 'test'):
                k = (ka & kb) | (ka & ~va) | (kb & ~vb)
                v = va & vb
            elif m == 'or':
                k = (ka & kb) | (ka & va) | (kb & vb)
                v = va | vb
            else:
                k = ka & kb; v = va ^ vb
            k &= M32; v &= k
            t = ta | tb
            sz = o[0].size * 8
            nf = flags_of(k, v, t, sz if m == 'test' else 32)
            if m != 'test':
                put(regs, reg0, (k, v, t))
            return (regs, nf, stk)
        if m in ('shr', 'shl', 'sar') and two and full0 and o[1].type == X.X86_OP_IMM and d is not None:
            n = o[1].imm & 31
            ka, va, ta = d
            if n == 0:
                return (regs, fl, stk)
            if m == 'shr':
                k = (ka >> n) | (M32 ^ (M32 >> n)); v = va >> n
                cfk = ka >> (n - 1) & 1; cf = va >> (n - 1) & 1
            elif m == 'shl':
                k = ((ka << n) & M32) | ((1 << n) - 1); v = (va << n) & M32
                cfk = ka >> (32 - n) & 1; cf = va >> (32 - n) & 1
            else:
                if ka >> 31 & 1:
                    sv = va - (1 << 32) if va >> 31 else va
                    k = ((ka >> n) | (M32 ^ (M32 >> n))) & M32 if True else 0
                    v = (sv >> n) & M32
                else:
                    k = ka >> n
                    v = (va >> n) & ((1 << (32 - n)) - 1)
                cfk = ka >> (n - 1) & 1; cf = va >> (n - 1) & 1
            k &= M32; v &= k
            nf = flags_of(k, v, ta)
            nf['CF'] = (cf, ta) if cfk else None
            nf['OF'] = None
            put(regs, reg0, (k, v, ta))
            return (regs, nf, stk)
        if m == 'neg' and full0 and d is not None:
            ka, va, ta = d
            if ka == FULL:
                r = (-va) & M32
                nf = {'ZF': (int(r == 0), ta), 'CF': (int(va != 0), ta), 'SF': (r >> 31, ta), 'OF': None}
                regs[reg0] = (FULL, r, ta)
            else:
                nf = {'CF': (1, ta)} if va & ka else {}
                regs.pop(reg0, None)
            return (regs, nf, stk)
        if m == 'sbb' and two and full0 and o[1].type == X.X86_OP_REG and i.reg_name(o[1].reg) == reg0:
            c = fl.get('CF')
            if c is not None:
                regs[reg0] = (FULL, M32 if c[0] else 0, c[1])
                return (regs, {'ZF': (int(not c[0]), c[1]), 'SF': (c[0], c[1]), 'CF': (c[0], c[1]), 'OF': (0, c[1])}, stk)
            regs.pop(reg0, None)
            return (regs, {}, stk)
        if m == 'not' and full0 and d is not None:
            ka, va, ta = d
            regs[reg0] = (ka, ~va & ka & M32, ta)
            return (regs, fl, stk)
        if m == 'bt' and two and full0 and o[1].type == X.X86_OP_IMM and d is not None:
            ka, va, ta = d
            n = o[1].imm & 31
            nfl = dict(fl)
            nfl['CF'] = (va >> n & 1, ta) if ka >> n & 1 else None
            return (regs, nfl, stk)
        if m in ('cmp', 'add', 'sub', 'adc', 'inc', 'dec') and full0:
            src = rd(i, o[1], st) if two else (FULL, 1, frozenset())
            if d is not None and src is not None and d[0] == FULL and src[0] == FULL and m not in ('adc',):
                a_, b_ = d[1], src[1]
                r = (a_ + b_ if m in ('add', 'inc') else a_ - b_) & M32
                t = d[2] | src[2]
                nf = {'ZF': (int(r == 0), t), 'SF': (r >> 31, t)}
                if m == 'add':
                    nf['CF'] = (int(a_ + b_ > M32), t)
                    nf['OF'] = (int(((a_ ^ r) & (b_ ^ r)) >> 31), t)
                elif m in ('sub', 'cmp'):
                    nf['CF'] = (int(a_ < b_), t)
                    nf['OF'] = (int(((a_ ^ b_) & (a_ ^ r)) >> 31), t)
                else:
                    nf['CF'] = fl.get('CF'); nf['OF'] = None
                    if nf['CF'] is None:
                        del nf['CF']
                if m != 'cmp':
                    regs[reg0] = (FULL, r, t)
                return (regs, {k: v for k, v in nf.items() if v is not None}, stk)
            return havoc_written(i, st)
        if m in SETCC and o and o[0].type == X.X86_OP_REG and i.reg_name(o[0].reg) in LOW8:
            b = LOW8[i.reg_name(o[0].reg)]
            c = cond_value(SETCC[m], fl)
            old = regs.get(b, UNK)
            if c is not None:
                k = (old[0] & ~0xff) | 0xff; v = (old[1] & ~0xff) | c[0]; t = old[2] | c[1]
            else:
                k = (old[0] & ~0xff) | 0xfe; v = old[1] & ~0xff; t = old[2]
            put(regs, b, (k & M32, v & k, t))
            return (regs, fl, stk)
        if m == 'push' and stk is not None:
            v = rd(i, o[0], st) if o[0].type in (X.X86_OP_REG, X.X86_OP_IMM) else None
            return (regs, fl, stk + (v if v and v[0] else None,))
        if m == 'pop' and stk and o[0].type == X.X86_OP_REG and full0:
            v = stk[-1]
            put(regs, reg0, v if v else UNK)
            return (regs, fl, stk[:-1])
        if m == 'pushal' and stk is not None:
            return (regs, fl, stk + tuple(regs.get(r) for r in REGS))
        if m == 'popal' and stk is not None and len(stk) >= 8:
            vals = stk[-8:]
            for r, v in zip(REGS, vals):
                if r != 'esp':
                    put(regs, r, v if v else UNK)
            return (regs, fl, stk[:-8])
        return havoc_written(i, st)

    def run(s, region, starts_states):
        """forward dataflow over `region` (a set of nodes); starts_states: {node: in-state} seeds. -> in-states of every node"""
        g = s.g
        inn = dict(starts_states)
        q = deque(sorted(inn))
        queued = set(q)
        out = {}
        while q:
            a = q.popleft(); queued.discard(a)
            st = inn[a]
            o_ = s.step(a, st)
            for t in g.succ[a]:
                if t not in region:
                    continue
                ns = o_
                if t in inn:
                    j = join(inn[t], ns)
                    if j == inn[t]:
                        continue
                    inn[t] = j
                else:
                    inn[t] = ns
                if t not in queued:
                    queued.add(t); q.append(t)
        return inn

    def decided(s, inn):
        """-> {jcc node: (taken?, taint)} for every conditional branch of the region whose flags decide it with a tainted value"""
        res = {}
        for a in sorted(inn):
            m = s.g.ins[a].m
            i = s.g.dec(a) if (m in CC or m in ('jbe', 'ja', 'jl', 'jge', 'jle', 'jg')) else None
            if i is not None and (m in CC or m in ('jbe', 'ja', 'jl', 'jge', 'jle', 'jg')) and i.operands and i.operands[0].type == X.X86_OP_IMM:
                c = cond_value(m, inn[a][1])
                if c is not None and c[1]:
                    res[a] = c
        return res


def cone(g, cp):
    """the intra-procedural component of the cpuid nodes: nodes connected to a cpuid by edges that are not call edges (a call writes everything unknown; flow across it carries no fact)"""
    iscall = lambda a: g.ins[a].m == 'call'
    S = set(cp)
    work = list(cp)
    while work:
        a = work.pop()
        nb = [] if iscall(a) else g.succ[a]
        nb = list(nb) + [p for p in g.pred.get(a, ()) if not iscall(p)]
        for t in nb:
            if t not in S:
                S.add(t); work.append(t)
    return S


def analyse(g, model, unknown_bits=None, S=None):
    """-> (S, in-states, decided): `decided` = {jcc: (taken, taint)} of the conditional branches the declared answer decides. A node of S with a root, a call or an outside predecessor starts from
    the all-unknown state (every path into it that the dataflow does not see is unknown), so a decision holds for every execution that reaches the branch."""
    cp = sorted(a for a, i in g.ins.items() if i.m == 'cpuid')
    if S is None:
        S = cone(g, cp)
    rootset = set(g.roots)
    seeds = {}
    for n in S:
        ps = g.pred.get(n, ())
        if n in rootset or any(p not in S or g.ins[p].m == 'call' for p in ps):
            seeds[n] = ({}, {}, ())
    df = Dataflow(g, model, unknown_bits)
    inn = df.run(S, seeds)
    return S, inn, df.decided(inn)


def probe_bits(g, model, S, jcc, taint):
    """the decisive bits of a decision: {(site, leaf, reg): mask} where a bit is decisive iff making it unknown makes the branch undecided"""
    out = {}
    for (site, leaf, reg) in sorted(taint):
        k = model[leaf][reg][0]
        mask = 0
        for b in range(32):
            if not k >> b & 1:
                continue
            _, _, dec = analyse(g, model, {(site, reg): 1 << b}, S)
            if jcc not in dec or not any(x[:1] == (site,) for x in [t for t in dec[jcc][1]]):
                mask |= 1 << b
        out[(site, leaf, reg)] = mask
    return out


def origin_reach(g, cut):
    """multi-source BFS from the roots without the cut edges: {node: root it was reached from}"""
    org = {}
    q = deque()
    for r in g.roots:
        if r not in org:
            org[r] = r; q.append(r)
    while q:
        a = q.popleft()
        for t in g.succ[a]:
            if t not in org and (a, t) not in cut:
                org[t] = org[a]; q.append(t)
    return org


def back_reach(g, site):
    seen = {site}
    q = deque([site])
    while q:
        a = q.popleft()
        for p in g.pred.get(a, ()):
            if p not in seen:
                seen.add(p); q.append(p)
    return seen


def load_cleared(path):
    out = defaultdict(dict)
    for l in open(path):
        if l[0] == '#' or not l.strip():
            continue
        f = l.rstrip('\n').split('\t')
        mod, ad = f[0].split(':')
        out[mod][int(ad, 16)] = f[3] if len(f) > 3 else ''
    return out


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def hx(x):
    return '%08x' % x


def image_rows(name, pe_path, facts, model, cleared, f_cpufeat):
    base, ranges = load_ranges(os.path.join(facts, 'ranges.tsv'))
    uni = load_universe(os.path.join(facts, 'universe.tsv'))
    def gate(i):
        try:
            return bool(f_cpufeat.missing(i))
        except Exception:
            return False
    g = Graph(Pe(pe_path, base), ranges, uni, gate)
    S, inn, dec = analyse(g, model)
    cut = {}
    for a in sorted(dec):
        taken, taint = dec[a]
        i = g.dec(a)
        tgt = i.operands[0].imm & M32
        nxt = a + i.size
        cut[(a, nxt if taken else tgt)] = (taken, taint, a)
        if tgt == nxt:
            cut.pop((a, nxt))      # both directions reach the same node: nothing is excluded
    cutset = frozenset(cut)
    full = g.reach(g.roots)
    org = origin_reach(g, cutset)
    sites = {}
    for a in sorted(g.gated):
        sites[a] = 'both' if a in cleared.get(name, {}) else 'lifter'
    for a in cleared.get(name, {}):
        sites.setdefault(a, 'l3b')
    rows = []
    probes = {}
    for a in sorted(sites):
        i = g.dec(a) if a in g.ins else None
        if i is None:
            rows.append((name, hx(a), '-', '-', sites[a], '-', 'RED', 'U-NODEC', '-', '-', '-', '-', '-', '-', 'the facts decode no instruction at the site'))
            continue
        form = i.bytes.hex()
        mn = i.mnemonic
        try:
            gs = [x for x in f_cpufeat.gates(i) if x[4] == 0] if f_cpufeat.missing(i) else []
        except Exception:
            gs = []
        gate = ';'.join('%s:%x.%s.%d=%d' % (f, l, r, b, v) for f, l, r, b, v in gs) or '-'
        if not gs:
            rows.append((name, hx(a), mn, form, sites[a], gate, 'RED', 'U-NOGATE', '-', '-', '-', '-', '-', '-', 'no cleared CPUID feature gates this form: a cpuid test cannot exclude it'))
            continue
        if a not in full:
            rows.append((name, hx(a), mn, form, sites[a], gate, 'RED', 'U-DEAD', '-', '-', '-', '-', '-', '-', 'no root reaches the site'))
            continue
        if a in org:
            r = org[a]
            u = uni.get(r)
            rows.append((name, hx(a), mn, form, sites[a], gate, 'RED', 'U-ROOT', '-', '-', '-', '-', '-', '-',
                         'root %s kind %s flags %s reaches the site without crossing an excluded edge' % (hx(r), u[0] if u else '-', u[1] if u else '-')))
            continue
        B = back_reach(g, a)
        F = sorted(e for e in cut if e[0] in org and e[1] in B)
        e = F[0]
        taken, taint, jcc = cut[e]
        pb = probes.get(jcc)
        if pb is None:
            pb = probes[jcc] = probe_bits(g, model, S, jcc, taint)
        (site, leaf, reg) = sorted(taint)[0]
        word = model[leaf][reg]
        rows.append((name, hx(a), mn, form, sites[a], gate, 'CERT', '-', hx(site), '%x' % leaf, reg, '0x%08x' % pb[(site, leaf, reg)], '0x%08x' % word[1],
                     '%s->%s %s' % (hx(e[0]), hx(e[1]), 'not-taken' if not taken else 'taken'),
                     'every root path crosses the excluded edge(s) %s (%d); the branch is decided by the declared %x.%s' % (','.join('%s->%s' % (hx(x), hx(y)) for x, y in F[:4]), len(F), leaf, reg)))
    return rows, dict(nodes=len(g.ins), roots=len(g.roots), cpuid=sum(1 for a in g.ins if g.ins[a].m == 'cpuid'), decided=len(dec), excluded=len(cut))


COLS = ('module', 'site', 'mnemonic', 'bytes', 'source', 'gate', 'status', 'reason', 'cpuid_site', 'leaf', 'register', 'decisive_bits', 'declared', 'excluded_edge', 'path')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--cleared', required=True)
    ap.add_argument('--lib', required=True, help='the directory holding f_cpufeat.py and cpumodel_bits.py (the lifter package files)')
    ap.add_argument('--out', required=True)
    ap.add_argument('images', nargs='+', help='NAME:PE:FACTS_DIR')
    a = ap.parse_args()
    model = load_model(a.model, a.lib)
    from fnvlift import f_cpufeat
    cleared = load_cleared(a.cleared)
    allrows, stats = [], {}
    for spec in a.images:
        name, pe, facts = spec.split(':')
        rows, st = image_rows(name, pe, facts, model, cleared, f_cpufeat)
        allrows += rows
        stats[name] = st
    tot = defaultdict(int)
    per = defaultdict(lambda: defaultdict(int))
    for r in allrows:
        k = 'CERT' if r[6] == 'CERT' else r[7]
        per[r[0]][k] += 1; tot[k] += 1
    h = ['# %s' % FORMAT, '# model_sha256=%s cleared_sha256=%s' % (sha(a.model), sha(a.cleared))]
    for n in sorted(per):
        h.append('# image %s: %s; %s' % (n, ' '.join('%s=%d' % kv for kv in sorted(per[n].items())), ' '.join('%s=%d' % kv for kv in sorted(stats[n].items()))))
    h.append('# total: sites=%d %s' % (len(allrows), ' '.join('%s=%d' % kv for kv in sorted(tot.items()))))
    h.append('# ' + '\t'.join(COLS))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, 'w').write('\n'.join(h) + '\n' + ''.join('\t'.join(r) + '\n' for r in allrows))
    print('\n'.join(h[2:-1]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
