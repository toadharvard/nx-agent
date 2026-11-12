#!/usr/bin/env python3
"""Gates "order", "addrwrap" and "align" (fnvnx-gate/1) on the compiled objects of the lifted pack: classes B and F of the
owner orders (docs/ORDERING.md "Lifted code") and the alignment fault of the ordered instructions.
    python3 -I gate_order.py RTOBJ OBJ...        env GATE_OUT (result dir), JOBS (default 4)
RTOBJ  the runtime object of rt/enter.c: its 4-byte object symbol fnvnx_lift_cpu_bytes holds sizeof(struct cpu).
OBJ    an ELF32 AArch64 object. Every object linked into the lifted image is given, the runtime ones too.
Every instruction of every executable section of every object is decoded (capstone). The gate decides from the decoded
operands, the encodings and the relocation records only; no symbol name (other than fnvnx_lift_cpu_bytes of RTOBJ) and no
source text is read.

Base classes. A forward dataflow over the control-flow graph of each section gives every general register a class:
  CPU    x27, a copy of it, or x27 plus an offset: a constant, or a register whose unsigned bound is known. The state keeps
         the interval [lo, hi] of the offset; a result outside [0, sizeof(struct cpu)] is GUEST. No instruction may write
         x27 (checked). A CPU value stored to an sp slot and loaded back from the same slot stays CPU (spill rule) while
         nothing may have written the slot: any store that overlaps it, any store through a frame pointer of unknown
         offset, a change of sp, and a call when a frame address at or below the slot has escaped (was in x0-x18 at a
         call, or was stored to memory) forget it.
  FRAME  sp, and a register copied from sp or sp plus a constant. sp may only change by a constant, by a copy from a FRAME
         register, or by its own writeback (checked).
  IMAGE  the result of adrp / adr, a constant added to it, or a load that carries a GOT relocation: an address of the
         linked image. adrp followed by add :lo12: of the same symbol of a non-executable PROGBITS section of the object
         is the address of that datum, and the number of bytes from it to the end of its section is known.
  GUEST  everything else, and every join of different classes. At a function entry (STT_FUNC) only x27 is CPU; a call
         leaves x0-x18 and x30 GUEST and the other registers as they were (AAPCS64; the objects are compiled with
         -fno-ipa-ra). A block no entry reaches starts with x27 = CPU and everything else GUEST.
An access is a guest access iff its base register is GUEST. So nothing is exempt by default: a base is exempt only when
the dataflow proves it is one of the three host classes on every path.
Unsigned bounds: constants (movz/movn/movk), W-form results, and/ubfm (lsl, lsr, ubfx, uxtb, uxth), ldrb/ldrh, add of
bounded values that cannot wrap, and a sign extension of a value below its sign bit. A bound that grows around a loop is
dropped (no bound).

order (B). Universe: the guest accesses, plus every host access the classes do not cover. A guest access is certified iff
it is
  - an ordered instruction (ldar* / stlr* / ldaxr* / stlxr* / ldaxp / stlxp), or
  - a plain store with `dmb ishst` or `dmb ish` earlier in its basic block and no guest access or call between, or
  - a plain load with `dmb ishld` or `dmb ish` later in its basic block and no guest access, call or block end between.
Host accesses that pass (not in the universe): FRAME base plus constant; IMAGE base plus constant, loads and ordered
stores only; an IMAGE load with an index register when the base is the address of a datum and bound(index) << shift plus
the access size fits before the end of its section; CPU base plus constant when [lo + disp, hi + disp + size] lies inside
struct cpu. Every other host access is red: an index on a CPU or FRAME base, an IMAGE store, a CPU access not proven
inside struct cpu, a writeback on a CPU base.
Rule violations (red, counted as rule_violations): a write of x27; a write of sp other than the three forms above; an
X-form write of a guest register x19-x26 whose result is not proven below 2^32; a writeback of a guest register;
`dc`/`ic`/`sys` and LSE instructions; a memory operand without a general base register; a branch out of its section other
than a relocated `b`; an undecodable word outside the $d ranges.

addrwrap (F). Universe: as order. A guest access is certified iff the operand is the base register alone (no displacement,
no index, no writeback) and the base holds a 32-bit value: on every path its last writer is a W-form instruction, a copy
of such a value, a value with an unsigned bound below 2^32, or a guest register (x19-x26, kept below 2^32 by the rule
above in every object of the image).

align. Universe: the ordered guest accesses wider than a byte (they fault on a misaligned address). Certified iff on every
path to the access a branch on `tst base, #(size-1)` (or tbz bit 0 for size 2) took the zero edge and the base value did
not change since, or the base is a constant that is a multiple of the size.

Assumptions (docs/ORDERING.md "Lifted code" states them with their sources): AAPCS64 call clobbers; x27 points at a
struct cpu of fnvnx_lift_cpu_bytes bytes; every object of the image passes this gate.

Exit 0 iff all three results are green."""
import json
import multiprocessing
import os
import struct
import sys

import capstone
from capstone import arm64 as A

GUEST, CPU, FRAME, IMAGE = 0, 1, 2, 3
CLS = ('guest', 'cpu', 'frame', 'image')
SP = 31
NEG = -1 << 60
R_PG_HI21, R_ADD_LO12 = 11, 12        # R_AARCH64_P32_ADR_PREL_PG_HI21, R_AARCH64_P32_ADD_ABS_LO12_NC
R_GOT_LOAD = 27                      # R_AARCH64_P32_LD32_GOT_LO12_NC
LSE = ('cas', 'swp', 'ldadd', 'ldclr', 'ldeor', 'ldset', 'ldsmax', 'ldsmin', 'ldumax', 'ldumin')
ORDERED = {'ldar', 'ldarb', 'ldarh', 'stlr', 'stlrb', 'stlrh', 'ldaxr', 'ldaxrb', 'ldaxrh', 'stlxr', 'stlxrb', 'stlxrh', 'ldaxp', 'stlxp'}
NOACC = {'prfm', 'prfum'}
COND_Z = {'eq': True, 'ne': False}


def elf(path):
    """-> [(section name, bytes, {offset: reloc type}, [function offsets], [data ranges])] for the executable sections."""
    b = open(path, 'rb').read()
    if b[:4] != b'\x7fELF' or b[4] != 1 or struct.unpack_from('<H', b, 18)[0] != 183:
        raise ValueError('%s: not an ELF32 AArch64 object' % path)
    shoff, = struct.unpack_from('<I', b, 0x20); shentsize, shnum, shstr = struct.unpack_from('<HHH', b, 0x2e)
    sh = [struct.unpack_from('<IIIIIIIIII', b, shoff + i * shentsize) for i in range(shnum)]
    cstr = lambda tab, o: b[tab + o:b.index(b'\0', tab + o)].decode()
    names = [cstr(sh[shstr][4], s[0]) for s in sh]
    out = {i: [names[i], b[s[4]:s[4] + s[5]], {}, [], []] for i, s in enumerate(sh) if s[1] == 1 and s[2] & 4}
    syms = {}
    for s in sorted(sh, key=lambda s: s[1] != 2):
        if s[1] == 2:                                                   # SHT_SYMTAB
            marks = {}
            for k in range(s[5] // 16):
                nm, val, _sz, info, _o, shndx = struct.unpack_from('<IIIBBH', b, s[4] + 16 * k)
                if 0 < shndx < shnum and sh[shndx][1] == 1 and not sh[shndx][2] & 4: syms[k] = (shndx, val, sh[shndx][5])   # defined in a PROGBITS data section
                if shndx not in out: continue
                if info & 15 == 2: out[shndx][3].append(val)
                n = cstr(sh[s[6]][4], nm)
                if n[:2] in ('$x', '$d'): marks.setdefault(shndx, []).append((val, n[1]))
            for i, m in marks.items():
                m.sort(); end = len(out[i][1])
                for (v, k), nx in zip(m, m[1:] + [(end, 'x')]):
                    if k == 'd' and nx[0] > v: out[i][4].append((v, nx[0]))
        if s[1] == 4 and s[7] in out:                                   # SHT_RELA
            for k in range(s[5] // 12):
                off, info, add = struct.unpack_from('<IIi', b, s[4] + 12 * k)
                y = syms.get(info >> 8)         # the target: (section, offset, bytes from the target to the section end)
                out[s[7]][2][off] = (info & 0xff, (y[0], y[1] + add, y[2] - y[1] - add) if y and 0 <= y[1] + add < y[2] else None)
    return [tuple(v) for _, v in sorted(out.items())]


def regno(name):
    """a general register name -> (number, is W-form); None for zr, vector and system registers."""
    if name in ('sp', 'wsp'): return SP, name == 'wsp'
    if name in ('fp', 'x29'): return 29, False
    if name in ('lr', 'x30'): return 30, False
    if name[0] in 'wx' and name[1:].isdigit(): return int(name[1:]), name[0] == 'w'
    return None


class Ins:
    __slots__ = ('a', 'txt', 'wr', 'fl', 'kind', 'arg', 'mem', 'term', 'bar', 'call', 'bad', 'wbbase', 'x', 'asz', 'sreg', 'srcs', 'img', 'ix')


def decode(md, code, relocs, data):
    """-> [Ins] for one section, or (None, offset) at the first undecodable word outside the $d ranges."""
    out, pos, n = [], 0, len(code)
    isdata = lambda o: any(lo <= o < hi for lo, hi in data)
    while pos < n:
        if isdata(pos): pos += 4; continue
        got = False
        for ci in md.disasm(code[pos:], pos):
            if isdata(ci.address): break
            got = True; pos = ci.address + 4
            i = Ins(); i.a = ci.address; m = ci.mnemonic; i.txt = m + ' ' + ci.op_str
            i.kind = i.arg = i.mem = i.term = i.bar = i.bad = i.wbbase = i.x = i.asz = i.sreg = i.img = i.ix = None; i.srcs = (); i.call = False
            ops = ci.operands
            _rd, wrs = ci.regs_access()
            i.wr = []; i.fl = False
            for r in wrs:
                nm = ci.reg_name(r)
                if nm == 'nzcv': i.fl = True; continue
                g = regno(nm)
                if g and m not in ('tst', 'cmp', 'cmn', 'ccmp', 'ccmn'): i.wr.append(g)       # flag-setting aliases write no register
            reg = lambda k: regno(ci.reg_name(ops[k].reg)) if len(ops) > k and ops[k].type == A.ARM64_OP_REG else None
            imm = lambda k: ops[k].imm if len(ops) > k and ops[k].type == A.ARM64_OP_IMM else None
            rel, rtgt = relocs.get(ci.address, (None, None))
            if rel in (R_PG_HI21, R_ADD_LO12): i.img = (rel, rtgt)
            if m in ('dc', 'ic', 'sys', 'sysl'): i.bad = 'system instruction'
            if m.startswith(LSE): i.bad = 'LSE instruction'
            if m == 'dmb': i.bar = ci.op_str.strip()
            elif m in ('bl', 'blr'): i.call = True
            elif m == 'b': i.term = ('exit',) if rel is not None else ('b', imm(0))
            elif m.startswith('b.'): i.term = ('bcc', m[2:], imm(0))
            elif m in ('cbz', 'cbnz'): i.term = ('cb', imm(1))
            elif m in ('tbz', 'tbnz'): i.term = ('tb', reg(0), imm(1), m == 'tbnz', imm(2))
            elif m in ('ret', 'br', 'udf', 'brk', 'eret'): i.term = ('exit',)
            elif m in ('adrp', 'adr'): i.kind = 'image'
            elif m == 'mov' and len(ops) == 2 and reg(0) and reg(1): i.kind = 'mov'; i.arg = reg(1)[0]
            elif m in ('add', 'sub') and len(ops) == 3 and reg(0) and reg(1) and imm(2) is not None and not i.fl: i.kind = 'addi'; i.arg = reg(1)[0]
            elif m == 'tst' and len(ops) == 2 and reg(0) and imm(1) is not None: i.kind = 'tst'; i.arg = (reg(0)[0], imm(1))
            word = int.from_bytes(ci.bytes, 'little')
            if (word >> 23) & 0x3f == 0x25 and (word >> 29) & 3 != 1 and reg(0):          # movn / movz / movk: the constant, from the encoding
                sf, opc, sh, v = word >> 31, (word >> 29) & 3, ((word >> 21) & 3) * 16, (word >> 5) & 0xffff
                mask = (1 << 64) - 1 if sf else (1 << 32) - 1
                i.kind = 'const'; i.arg = ('k', v << sh, ~(0xffff << sh) & mask) if opc == 3 else ('v', (~(v << sh) if opc == 0 else v << sh) & mask)
            sf = word >> 31
            if word & 0x7f800000 == 0x53000000: i.x = ('ubfm', sf, (word >> 16) & 63, (word >> 10) & 63, (word >> 5) & 31)
            elif word & 0x7f800000 == 0x12000000 and imm(2) is not None and reg(1): i.x = ('andi', reg(1)[0], imm(2) & ((1 << 64) - 1))
            elif word & 0x3f800000 == 0x11000000 and not word & 0x20000000: i.x = ('addi', (word >> 5) & 31, (-1 if word & 0x40000000 else 1) * (((word >> 10) & 0xfff) << (12 if word & 0x400000 else 0)))
            elif word & 0x7fe00000 == 0x0b000000: i.x = ('addr', (word >> 5) & 31, (word >> 16) & 31, (word >> 10) & 63, 64 if sf else 32, False, False)
            elif word & 0x7fe00000 == 0x0b200000: i.x = ('addr', (word >> 5) & 31, (word >> 16) & 31, (word >> 10) & 7, (8, 16, 32, 64)[(word >> 13) & 3], True, bool(word & 0x8000))
            elif m in ('ldrb', 'ldurb'): i.x = ('ub', 0xff)
            elif m in ('ldrh', 'ldurh'): i.x = ('ub', 0xffff)
            if i.term and i.term[0] != 'exit' and rel is not None: i.bad = 'a conditional branch with a relocation'; i.term = ('exit',)
            if word & 0x3f200c00 == 0x38200800 and word & 0x4000:      # load/store, register offset: (index, extend bits, signed, shift)
                i.ix = ((word >> 16) & 31, 64 if word & 0x2000 else 32, bool(word & 0x8000), (word >> 30) if word & 0x1000 else 0)
            for k, op in enumerate(ops):
                if op.type != A.ARM64_OP_MEM or m in NOACC: continue
                base = regno(ci.reg_name(op.mem.base)) if op.mem.base else None
                if base is None: i.bad = 'memory operand without a general base register'; continue
                if m in ORDERED:
                    sz = {'b': 1, 'h': 2}.get(m[-1]) if m[-1] in 'bh' else None
                    if sz is None:
                        sz = 4 if ci.reg_name(ops[k - 1].reg).startswith('w') else 8
                        if m[-1] == 'p': sz *= 2
                else: sz = 0
                wb = bool(ci.writeback) or (len(ops) > k + 1 and ops[k + 1].type == A.ARM64_OP_IMM)
                i.mem = (base[0], bool(op.mem.index), op.mem.disp, wb, m.startswith('st'), sz)
                if m[:2] in ('ld', 'st') and ops[0].type == A.ARM64_OP_REG:
                    core = m.rstrip('p') if m[-1] == 'p' else m
                    one = 4 if m.endswith('sw') else 1 if core[-1] == 'b' else 2 if core[-1] == 'h' else (4 if reg(0)[1] else 8) if reg(0) else {'wzr': 4, 'xzr': 8}.get(ci.reg_name(ops[0].reg)) or {'b': 1, 'h': 2, 's': 4, 'd': 8, 'q': 16}.get(ci.reg_name(ops[0].reg)[0])
                    if m.startswith(('ldp', 'stp', 'ldnp', 'stnp', 'ldax', 'stlx', 'ldx', 'stx')) and not sz: one = None if m[2] != 'p' and m[3] != 'p' else one
                    i.asz = sz or (one * (2 if m[2:3] == 'p' or m[2:4] == 'np' else 1) if one else None)
                if m in ('str', 'ldr') and i.asz == 8 and reg(0) and not reg(0)[1] and reg(0)[0] != SP and k == 1: i.sreg = reg(0)[0]
                if m.startswith('st'): i.srcs = tuple(g[0] for g in (regno(ci.reg_name(o.reg)) for o in ops[:k] if o.type == A.ARM64_OP_REG) if g and g[0] != SP)
                if wb:
                    if any(o.type == A.ARM64_OP_REG and regno(ci.reg_name(o.reg)) and regno(ci.reg_name(o.reg))[0] == base[0] for o in ops): i.bad = 'writeback base is also an operand'
                    i.wbbase = base[0]
                    if not any(r == base[0] for r, _ in i.wr): i.wr.append((base[0], False))
                if rel == R_GOT_LOAD and not m.startswith('st'): i.kind = 'image'      # a GOT load
            out.append(i)
        if not got and pos < n and not isdata(pos): return None, pos
    return out, None


CPUSIZE = 0


def cpusize(path):
    """sizeof(struct cpu): the value of the 4-byte object symbol fnvnx_lift_cpu_bytes of the runtime object rt/enter.c"""
    b = open(path, 'rb').read()
    shoff, = struct.unpack_from('<I', b, 32); shentsize, shnum = struct.unpack_from('<HH', b, 46)
    sh = [struct.unpack_from('<10I', b, shoff + k * shentsize) for k in range(shnum)]
    for h in sh:
        if h[1] != 2: continue
        stro = sh[h[6]][4]
        for o in range(h[4], h[4] + h[5], 16):
            nm, val, size, info, _other, shndx = struct.unpack_from('<IIIBBH', b, o)
            if b[stro + nm:b.index(b'\0', stro + nm)] == b'fnvnx_lift_cpu_bytes' and size == 4 and 0 < shndx < shnum:
                return struct.unpack_from('<I', b, sh[shndx][4] + val)[0]
    raise ValueError('%s: no fnvnx_lift_cpu_bytes' % path)


def init(n):
    global CPUSIZE
    CPUSIZE = n


def analyse(path):
    """-> {'order': [universe, certified, [bad]], 'addrwrap': .., 'align': .., 'n': {counter: n}}"""
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.detail = True
    res = {'order': [0, 0, []], 'addrwrap': [0, 0, []], 'align': [0, 0, []], 'n': {}}
    cnt = res['n']; name = os.path.basename(path)

    def tick(k, n=1): cnt[k] = cnt.get(k, 0) + n

    def red(g, ins, why):
        if len(res[g][2]) < 50: res[g][2].append('%s+%#x %s: %s' % (name, ins.a if ins else 0, ins.txt if ins else '', why))

    def glob(why, ins=None): tick('rule_violations'); red('order', ins, why)

    for sname, code, relocs, funcs, data in elf(path):
        L, badoff = decode(md, code, relocs, data)
        if L is None:
            res['order'][0] += 1; i = Ins(); i.a = badoff; i.txt = '(undecodable)'; glob('undecodable word in ' + sname, i); continue
        tick('instructions', len(L))
        at = {ins.a: k for k, ins in enumerate(L)}
        lead = set(f for f in funcs if f in at)
        if L: lead.add(L[0].a)
        for k, ins in enumerate(L):
            if ins.term:
                if k + 1 < len(L): lead.add(L[k + 1].a)
                t = ins.term[-1] if ins.term[0] != 'exit' else None
                if t is not None:
                    if t in at: lead.add(t)
                    else: glob('branch target outside the decoded section', ins)
            elif k + 1 < len(L) and L[k + 1].a != ins.a + 4: lead.add(L[k + 1].a)
        starts = sorted(lead); bidx = {a: n for n, a in enumerate(starts)}
        blocks = [(at[a], at[starts[n + 1]] if n + 1 < len(starts) else len(L)) for n, a in enumerate(starts)]

        def top(n):         # the most conservative state: valid at any program point
            return [[GUEST] * 31, [False] * 19 + [True] * 8 + [False] * 4, [1] * 31, [(3 << 40) | (n << 5) | r for r in range(31)], None, [None] * 31, [None] * 31, [None] * 31, [None] * 31, {}, NEG]

        def entry(n):
            s = top(n); s[0][27] = CPU; s[7][27] = (0, 0); s[10] = None; return s

        def join(n, cur, inc):
            ch = False
            for r in range(31):
                if cur[0][r] != inc[0][r] and cur[0][r] != GUEST: cur[0][r] = GUEST; ch = True
                if cur[1][r] and not inc[1][r]: cur[1][r] = False; ch = True
                if inc[2][r] < cur[2][r]: cur[2][r] = inc[2][r]; ch = True
                j = (2 << 40) | (n << 5) | r
                if cur[3][r] != inc[3][r] and cur[3][r] != j: cur[3][r] = j; ch = True
            if cur[4] != inc[4] and cur[4] is not None: cur[4] = None; ch = True
            for r in range(31):
                if cur[5][r] != inc[5][r] and cur[5][r] is not None: cur[5][r] = None; ch = True
                if cur[6][r] is not None and (inc[6][r] is None or inc[6][r] > cur[6][r]): cur[6][r] = None; ch = True     # widening: a growing bound is no bound
                a, b = cur[7][r], inc[7][r]
                if cur[0][r] == CPU and a != b:
                    cur[7][r] = (min(a[0], b[0]), max(a[1], b[1])); ch = True
                elif cur[0][r] == IMAGE:
                    if a is not None and a != b: cur[7][r] = None; ch = True
                elif cur[0][r] != CPU and a is not None: cur[7][r] = None
                if cur[8][r] is not None and cur[8][r] != inc[8][r]: cur[8][r] = None; ch = True
            for k in list(cur[9]):
                a, b = cur[9][k], inc[9].get(k)
                if b is None: del cur[9][k]; ch = True
                elif a != b: cur[9][k] = (min(a[0], b[0]), max(a[1], b[1])); ch = True
            e = inc[10] if cur[10] is None else cur[10] if inc[10] is None else min(cur[10], inc[10])
            if e != cur[10]: cur[10] = e; ch = True
            return ch

        def step(s, ins, check):
            cls, wc, al, vn, kc, ub, off = s[0], s[1], s[2], s[3], s[5], s[6], s[7]
            fo, slots = s[8], s[9]
            if check and ins.mem: checkmem(s, ins)
            if ins.call:
                for r in range(19):          # a frame address in a call-clobbered register is handed to the callee
                    if cls[r] == FRAME: s[10] = NEG if fo[r] is None else fo[r] if s[10] is None else min(s[10], fo[r])
                if s[10] is not None:        # a callee may write at and above every frame address that escaped
                    for k in [k for k in slots if k + 8 > s[10]]: del slots[k]
                for r in list(range(19)) + [30]: cls[r] = GUEST; wc[r] = False; al[r] = 1; vn[r] = (1 << 40) | (ins.a << 5) | r; kc[r] = None; ub[r] = None; off[r] = None
                s[4] = None; return
            # unsigned bound of the result (nub) and, for a struct cpu address, its offset interval (noff), from the state before the instruction
            W32 = 0xffffffff
            val = lambda r, bits: 0 if r == 31 else min(ub[r] if ub[r] is not None else 1 << 64, (1 << bits) - 1) if bits < 64 else ub[r]
            nub = noff = ioff = None; x = ins.x; dw = bool(ins.wr) and ins.wr[0][1]; bits = 32 if dw else 64
            if ins.kind == 'mov' and ins.arg != SP:
                nub = val(ins.arg, bits); noff = off[ins.arg] if cls[ins.arg] == CPU else None
                if cls[ins.arg] == IMAGE and not dw: ioff = off[ins.arg]
            elif x and x[0] == 'ub': nub = x[1]
            if ins.img and ins.img[1]:      # adrp then add :lo12: of the same target: the register holds the target address
                if ins.kind == 'image' and ins.img[0] == R_PG_HI21: ioff = ('pg', ins.img[1])
                elif ins.kind == 'addi' and ins.img[0] == R_ADD_LO12 and ins.arg != SP and cls[ins.arg] == IMAGE and off[ins.arg] == ('pg', ins.img[1]): ioff = ('at', ins.img[1][2])
            elif x and x[0] == 'andi': nub = x[2] if val(x[1], bits) is None else min(x[2], val(x[1], bits))
            elif x and x[0] == 'ubfm':
                _, sf, immr, imms, rn = x; rs = 64 if sf else 32; v = val(rn, rs); v = (1 << rs) - 1 if v is None else v
                nub = min(v >> immr, (1 << (imms - immr + 1)) - 1) if imms >= immr else min(v, (1 << (imms + 1)) - 1) << (rs - immr)
            elif x and x[0] == 'addi' and x[1] != SP:
                if cls[x[1]] == CPU:
                    lo, hi = off[x[1]][0] + x[2], off[x[1]][1] + x[2]
                    if 0 <= lo and hi <= CPUSIZE: noff = (lo, hi)
                elif x[2] >= 0 and val(x[1], bits) is not None and val(x[1], bits) + x[2] < 1 << bits: nub = val(x[1], bits) + x[2]
            elif x and x[0] == 'addr' and x[4]:
                _, rn, rm, sh, eb, ext, sgn = x
                if ext and rn == SP: rn = None
                elif not ext and rn == 31: rn = 31
                bm = val(rm, eb)
                if sgn and bm is not None and bm >= 1 << (eb - 1): bm = None      # a sign extension is the value itself below the sign bit
                if bm is not None: bm <<= sh
                if rn is not None and rn != 31 and cls[rn] == CPU and bm is not None and off[rn][1] + bm <= CPUSIZE: noff = (off[rn][0], off[rn][1] + bm)
                elif rn is not None and rm != 31 and cls[rm] == CPU and sh == 0 and eb >= 32 and val(rn, bits) is not None and off[rm][1] + val(rn, bits) <= CPUSIZE:
                    noff = (off[rm][0], off[rm][1] + val(rn, bits))
                elif rn is not None and bm is not None and val(rn, bits) is not None and val(rn, bits) + bm < 1 << bits: nub = val(rn, bits) + bm
            spill = None
            if ins.mem:
                base, idx, disp, wb, store, _sz = ins.mem
                for r in ins.srcs:            # a frame address stored to memory: any later callee may load it
                    if cls[r] == FRAME: s[10] = NEG if fo[r] is None else fo[r] if s[10] is None else min(s[10], fo[r])
                o = 0 if base == SP else fo[base] if cls[base] == FRAME else None
                if store and (base == SP or cls[base] == FRAME):
                    if o is None or idx or wb or ins.asz is None: slots.clear()
                    else:
                        for k in [k for k in slots if k < o + disp + ins.asz and o + disp < k + 8]: del slots[k]
                if base == SP and not idx and not wb and ins.sreg is not None:
                    if store and cls[ins.sreg] == CPU: slots[disp] = off[ins.sreg]
                    elif not store and disp in slots: spill = slots[disp]
            nfo = None
            if ins.kind == 'mov': nfo = 0 if ins.arg == SP else fo[ins.arg] if cls[ins.arg] == FRAME else None
            elif ins.kind == 'addi' and x and x[0] == 'addi':
                b = 0 if ins.arg == SP else fo[ins.arg] if cls[ins.arg] == FRAME else None
                nfo = None if b is None else b + x[2]
            src = ins.arg if ins.kind in ('mov', 'addi') else None
            new = None
            if src is not None:
                c = FRAME if src == SP else cls[src]
                if ins.kind == 'mov': new = (c, True if src == SP else wc[src], 1 if src == SP else al[src], None if src == SP else vn[src])
                elif c != GUEST: new = (c, False, 1, None)
            for r, w in ins.wr:
                if r == SP:
                    ok = ins.mem and ins.mem[0] == SP and ins.mem[3] or ins.kind == 'addi' and src == SP or ins.kind == 'mov' and src != SP and cls[src] == FRAME
                    if check and not ok: glob('sp written by something else than a constant step or a frame copy', ins)
                    slots.clear(); s[8] = fo = [None] * 31
                    if s[10] is not None: s[10] = NEG
                    continue
                if check and r == 27: glob('x27 written', ins)
                fresh = (1 << 40) | (ins.a << 5) | r
                if ins.wbbase == r:           # writeback of a guest base (a host base with writeback is red in checkmem, sp apart)
                    if check and 19 <= r <= 26: glob('writeback of a guest register', ins)
                    cls[r] = GUEST; off[r] = None; ub[r] = None; wc[r] = False; al[r] = 1; vn[r] = fresh; kc[r] = None; fo[r] = None; continue
                old = kc[r]; kc[r] = None
                if ins.kind == 'const':       # the value is known: its alignment and its width are facts
                    v = ins.arg[1] if ins.arg[0] == 'v' else None if old is None else old & ins.arg[2] | ins.arg[1]
                    if w and v is not None: v &= 0xffffffff
                    kc[r] = v; cls[r] = GUEST; vn[r] = fresh
                    wc[r] = w or (v is not None and v < 1 << 32); al[r] = 1 if v is None else 16 if v & 15 == 0 else v & -v
                elif new is not None and ins.kind == 'mov':
                    cls[r] = new[0]; wc[r] = True if w else new[1]; al[r] = new[2]; vn[r] = new[3] if new[3] is not None else fresh
                    if src != SP and (not w or (kc[src] is not None and kc[src] < 1 << 32)) and r != src: kc[r] = kc[src]
                    if r == src: kc[r] = old if old is not None and old < 1 << 32 else None
                elif new is not None:
                    cls[r] = new[0]; wc[r] = w; al[r] = 1; vn[r] = fresh
                elif ins.kind == 'image': cls[r] = IMAGE; wc[r] = w; al[r] = 1; vn[r] = fresh
                else: cls[r] = GUEST; wc[r] = w; al[r] = 1; vn[r] = fresh
                if (r, w) == ins.wr[0] and len([1 for q, _ in ins.wr if q != SP]) == 1:
                    ub[r] = kc[r] if kc[r] is not None else nub if nub is not None else W32 if w else None
                    if noff is not None: cls[r] = CPU; off[r] = noff
                    else:
                        off[r] = None
                        if cls[r] == CPU: cls[r] = GUEST
                else:
                    ub[r] = W32 if w else None; off[r] = None
                    if cls[r] == CPU: cls[r] = GUEST
                if spill is not None and (r, w) == ins.wr[0] and len(ins.wr) == 1: cls[r] = CPU; off[r] = spill; ub[r] = None
                if ioff is not None and cls[r] == IMAGE and ins.wr == [(r, w)]: off[r] = ioff
                if ub[r] is not None and ub[r] <= W32 and cls[r] == GUEST: wc[r] = True
                if check and 19 <= r <= 26 and not w and not wc[r]: glob('X-form write of a guest register not proven below 2^32', ins)
                fo[r] = None
                if cls[r] == FRAME: fo[r] = nfo
            if ins.kind == 'tst' and ins.arg[0] != SP: s[4] = (vn[ins.arg[0]], ins.arg[1])
            elif ins.fl: s[4] = None

        def checkmem(s, ins):
            base, idx, disp, wb, store, sz = ins.mem
            c = FRAME if base == SP else s[0][base]
            if c != GUEST:
                tick('host_' + CLS[c])
                if c == CPU and not idx and not (wb or ins.asz is None or s[7][base][0] + disp < 0 or s[7][base][1] + disp + ins.asz > CPUSIZE): return
                if c == CPU and not idx:
                    res['order'][0] += 1; res['addrwrap'][0] += 1
                    red('order', ins, 'an access through the cpu pointer not proven inside struct cpu'); red('addrwrap', ins, 'an access through the cpu pointer not proven inside struct cpu'); return
                if c == IMAGE and idx and not store and ins.ix and ins.asz and s[7][base] and s[7][base][0] == 'at':
                    rm, eb, sgn, sh = ins.ix; v = s[6][rm]; v = (1 << eb) - 1 if v is None else min(v, (1 << eb) - 1)
                    if not (sgn and v >= 1 << (eb - 1)) and (v << sh) + ins.asz <= s[7][base][1]: tick('host_image_table'); return
                if idx or (c == IMAGE and store and not sz):
                    res['order'][0] += 1; res['addrwrap'][0] += 1
                    red('order', ins, 'a %s base with %s' % (CLS[c], 'an index register' if idx else 'a plain store'))
                    red('addrwrap', ins, 'a %s base with an index register' % CLS[c] if idx else 'a plain store through an image base')
                return
            tick('guest_store' if store else 'guest_load')
            res['order'][0] += 1; res['addrwrap'][0] += 1
            if sz: tick('guest_ordered'); res['order'][1] += 1
            gacc[ins.a] = not sz
            if idx or disp or wb: red('addrwrap', ins, 'the operand is not the base register alone')
            elif not s[1][base]: red('addrwrap', ins, 'the base is not proven a 32-bit value')
            else: res['addrwrap'][1] += 1
            if sz > 1:
                res['align'][0] += 1
                if s[2][base] >= sz and not (idx or disp or wb): res['align'][1] += 1
                else: red('align', ins, 'no alignment test of %d bytes dominates the access' % sz)

        def edges(n, s):
            """successor states of block n whose exit state is s"""
            b0, b1 = blocks[n]; last = L[b1 - 1] if b1 > b0 else None
            fall = bidx.get(L[b1].a) if b1 < len(L) and (not last or last.a + 4 == L[b1].a) else None
            t = last.term if last else None
            if not t: return [(fall, s, None)] if fall is not None else []
            if t[0] == 'exit': return []
            tgt = bidx.get(t[-1])
            if t[0] == 'b': return [(tgt, s, None)] if tgt is not None else []
            zero = None                     # (edge is the taken one, value number, alignment)
            if t[0] == 'bcc' and t[1] in COND_Z and s[4] and s[4][1] in (1, 3, 7, 15): zero = (COND_Z[t[1]], s[4][0], s[4][1] + 1)
            if t[0] == 'tb' and t[1] and t[1][0] != SP and t[2] == 0: zero = (not t[3], s[3][t[1][0]], 2)
            out = []
            for dst, taken in ((tgt, True), (fall, False)):
                if dst is None: continue
                out.append((dst, s, (zero[1], zero[2]) if zero and zero[0] == taken else None))
            return out

        def refine(s, z):
            if not z: return s
            s = [s[0], s[1], list(s[2])] + s[3:]
            for r in range(31):
                if s[3][r] == z[0] and s[2][r] < z[1]: s[2][r] = z[1]
            return s

        copy = lambda s: [list(s[0]), list(s[1]), list(s[2]), list(s[3]), s[4], list(s[5]), list(s[6]), list(s[7]), list(s[8]), dict(s[9]), s[10]]
        state = [None] * len(blocks); work = []
        for f in sorted(lead & set(funcs)) or starts[:1]:
            n = bidx[f]; e = entry(n)
            if state[n] is None: state[n] = e
            else: join(n, state[n], e)
            work.append(n)
        gacc = {}
        while work:
            n = work.pop(); s = copy(state[n]); b0, b1 = blocks[n]
            for k in range(b0, b1): step(s, L[k], False)
            for dst, es, z in edges(n, s):
                es = refine(es, z)
                if state[dst] is None: state[dst] = copy(es); work.append(dst)
                elif join(dst, state[dst], es): work.append(dst)
        for n, (b0, b1) in enumerate(blocks):
            s = copy(state[n]) if state[n] is not None else None
            if state[n] is None: s = top(n); s[0][27] = CPU; s[7][27] = (0, 0)      # x27 is never written: the one fact valid everywhere
            if state[n] is None: tick('blocks_no_entry_reaches')
            for k in range(b0, b1):
                if L[k].bad: glob(L[k].bad, L[k])
                step(s, L[k], True)
            sb = False; wait = []       # barrier cover of the plain guest accesses of this block
            for k in range(b0, b1):
                ins = L[k]
                if ins.bar:
                    if ins.bar in ('ishst', 'ish', 'sy'): sb = True
                    if ins.bar in ('ishld', 'ish', 'sy'):
                        res['order'][1] += len(wait); tick('guest_plain_load_dmb', len(wait)); wait = []
                    continue
                if not ins.call and ins.a not in gacc: continue
                for w in wait: red('order', w, 'a plain guest load without dmb ishld before the next guest access or call')
                wait = []
                if gacc.get(ins.a):
                    if not ins.mem[4]: wait.append(ins)
                    elif sb: res['order'][1] += 1; tick('guest_plain_store_dmb')
                    else: red('order', ins, 'a plain guest store without dmb ishst after the last guest access or call')
                sb = False
            for w in wait: red('order', w, 'a plain guest load without dmb ishld before the block end')
    return res


def main(argv):
    files = []; init(cpusize(argv[0]))
    files = list(argv[1:])
    tot = {g: [0, 0, []] for g in ('order', 'addrwrap', 'align')}; n = {}
    with multiprocessing.Pool(int(os.environ.get('JOBS', '4')), init, (CPUSIZE,)) as pool:
        for r in pool.imap(analyse, files, chunksize=4):
            for g in tot:
                tot[g][0] += r[g][0]; tot[g][1] += r[g][1]; tot[g][2] += r[g][2]
            for k, v in r['n'].items(): n[k] = n.get(k, 0) + v
    rc = 0
    for g, (u, c, bad) in sorted(tot.items()):
        green = bool(n.get('instructions')) and u == c and not bad and (g != 'order' or not n.get('rule_violations'))
        counts = ', '.join('%s %d' % kv for kv in sorted(n.items()))
        res = {'format': 'fnvnx-gate/1', 'name': g, 'status': 'green' if green else 'red', 'universe': u, 'certified': c, 'uncertified': sorted(bad)[:50],
               'detail': '%d objects; struct cpu %d bytes; %d of %d certified; %s' % (len(files), CPUSIZE, c, u, counts) if files else 'no object given'}
        with open(os.path.join(os.environ['GATE_OUT'], g + '.json'), 'w') as fh: json.dump(res, fh, sort_keys=True)
        print('%s %s %d/%d' % (g, res['status'], c, u), file=sys.stderr)
        rc |= 0 if green else 2
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
