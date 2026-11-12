#!/usr/bin/env python3
"""Gates "order", "addrwrap" and "align" (fnvnx-gate/1) on the compiled objects of the lifted pack: classes B and F of the
owner orders (docs/ORDERING.md "Lifted code") and the alignment fault of the ordered instructions.
    python3 -I gate_order.py RTOBJ OBJ... [--asm OBJ]...        env GATE_OUT (result dir), JOBS
RTOBJ  the runtime object of rt/enter.c: read only for its 4-byte object symbol fnvnx_lift_cpu_bytes (sizeof(struct cpu)); it is not judged.
OBJ    an ELF32 AArch64 object or a directory of *.o: the objects of the roots whose admit cxx_origin is lifted (ASM-202: L1's build
       manifest lists them). The universe is these objects only; native objects (rt's C/C++ units) are closed by fnvnx_order.
--asm  an object whose source is assembler (the build manifest says which); it is gated like the others and also by rule (c) below.
rows(rtobj, lifted, asm) / run(...) take the same split: the lifted objects (judged) and the runtime object (read only for the size).
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
         linked image. adrp followed by add :lo12: of the same symbol is the address of that datum, and the section of the
         relocation target (its flags: SHF_WRITE, its alignment, the number of bytes from the datum to its end) is known.
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
Host accesses that pass (not in the universe): FRAME base plus constant; an IMAGE load whose target section is read-only (no
SHF_WRITE in the section header of the relocation target) and which lies inside it: a constant displacement within the bytes
from the datum to the end of the section, an index register when bound(index) << shift plus the access size fits there, or an
`ldr :lo12:` of the page's symbol; the load of a GOT entry; CPU base plus constant when [lo + disp, hi + disp + size] lies
inside struct cpu. A W-form copy of x27, and a W-form add of a CPU value and a bounded value, are CPU with the same interval
(an ILP32 object: x27 is a 32-bit pointer, ASM-007, and struct cpu lies below 2^32, ASM-201, so the sum cannot wrap; an
interval that exceeds sizeof(struct cpu) is GUEST). An IMAGE access into a writable section, an unknown target, a store, a
writeback, or a read-only access not proven inside its section is a guest access: it must be ordered like any other (its address
is a 32-bit link-time address of an ILP32 object, ASM-007; it is aligned iff the datum's offset and the section's alignment are
multiples of the access size). Every other host access is red: an index on a CPU or FRAME base, a CPU access not proven inside
struct cpu, a writeback on a CPU base. The two checks that make the CPU class a host class are counted globally and always
reported: x27_writes (must be 0) and cpu_outside_struct (must be 0), with cpu_accesses_inside_struct.
Rule violations (red, counted as rule_violations): a write of x27; a write of sp other than the three forms above; an
X-form write of a guest register x19-x26 whose result is not proven below 2^32; a writeback of a guest register;
`dc`/`ic`/`sys` and LSE instructions; a memory operand without a general base register; a branch out of its section other
than a relocated `b`; an undecodable word outside the $d ranges.

Writes of x19-x27 (rule (a), docs/ORDERING.md): the destination registers of every instruction are taken from the capstone operand view
and from the destination fields of the encoding (gprwrites: Rd, Rt, Rt2, the status register of exclusives, the base register of a
writeback, mrs/umov/smov/fcvt-to-integer destinations); the union decides. A write capstone does not report is a rule violation of its own.
x27 is never written; x19-x26 are written only by results proven below 2^32.
In an --asm object a write of x19-x26 that rule (c) proves takes back the register's entry value (a reload of the frame slot its entry value was
stored to) is not a guest-register write: an X-form ldr/ldp restore is legal there. Compiler objects keep (a) as stated.

Assembler objects (rule (c)): an object given with --asm is also held to this: every STT_FUNC preserves x19-x30 and sp on every path to every
exit (ret, br, a relocated or out-of-range b, falling into the next symbol). The proof is a forward dataflow per function (asmcheck): a
register holds its value at entry again only by a reload of the 8-byte slot (4-byte for x19-x26) that an `str`/`stp` through sp or a frame
address filled with it, at the same constant offset from the entry sp, and sp comes back to its entry value. Code that no STT_FUNC range
covers is red. The rule discharges the assumption of AAPCS64 for hand-written assembler (ASM-200); for compiler output it stays ASM-001.

Indirect branches (rule (b)): lifted objects are built with -fno-jump-tables, so every `br`/`blr` (not `ret`) is in the universe (one row,
tag br) and is certified only in these forms, on every path: (i) the target is the return value x0 of a `bl` whose relocation targets
the dispatcher, unchanged since (only `mov` copies it); (ii) a word loaded from an IMAGE address within a bounded range of a
read-only section (no SHF_WRITE), where every word of the range carries a relocation to an executable section; (iv) a word loaded
from the GOT entry (R_AARCH64_P32_LD32_GOT_LO12_NC) of an STT_FUNC symbol. The forward dataflow gives every register the set of
origins of its value on some path: a load gives its class, a `mov` copies, a call or any other instruction gives `unknown`, and a
join is the union. Red: any other origin (an argument, a call result of another function, arithmetic, a CPU, FRAME or GUEST load,
a writable or unbounded table, a word without a relocation to code, a GOT entry of a symbol that is no STT_FUNC). Form (iii), `ret`
through x30, is rule (c)'s. The only name the gate reads is the dispatcher's, `fnvl_dispatch`, which rt/rt.c exports: it must be a
defined global STT_FUNC of RTOBJ (otherwise the gate stops), and a call to it is a `bl` whose relocation's target symbol has that
name. An STT_FUNC symbol is one that some given object or RTOBJ defines with that type. Counted globally: br_indirect, br_dispatch_result,
br_table_target, br_got_function and br_illegal_<class>.

addrwrap (F). Universe: as order. A guest access is certified iff the operand is the base register alone (no displacement,
no index, no writeback) and the base holds a 32-bit value: on every path its last writer is a W-form instruction, a copy
of such a value, a value with an unsigned bound below 2^32, or a guest register (x19-x26, kept below 2^32 by the rule
above in every object of the image).

align. Universe: the ordered guest accesses wider than a byte (they fault on a misaligned address). Certified iff on every
path to the access a branch on `tst base, #(size-1)` (or tbz bit 0 for size 2) took the zero edge and the base value did
not change since, or the base is a constant that is a multiple of the size.

The assumptions it rests on are registered (verify/ASSUMPTIONS.json): ASM-001 (the compiler: AAPCS64 call clobbers of compiler
output, -fno-ipa-ra), ASM-200 (AAPCS64 of hand-written assembler, discharged by rule (c)), ASM-201 (layout: x27 points at a
struct cpu of fnvnx_lift_cpu_bytes bytes below 2^32, host mappings disjoint from guest ones), ASM-202 (every object of the
lifted roots is given), ASM-007 (ILP32), ASM-043 (the decoder, capstone) and ASM-045 (its write sets; rule (a) adds the
encoding's destination fields).

Exit 0 iff all three results are green."""
import json
import multiprocessing
import os
import struct
import sys

import capstone
from capstone import arm64 as A

GUEST, CPU, FRAME, IMAGE = 0, 1, 2, 3
GATES = ('order', 'addrwrap', 'align')
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
    """-> [(section name, bytes, {offset: reloc type}, [function offsets], [data ranges], [(offset, name, size) of the STT_FUNC symbols])] for the executable sections."""
    b = open(path, 'rb').read()
    if b[:4] != b'\x7fELF' or b[4] != 1 or struct.unpack_from('<H', b, 18)[0] != 183:
        raise ValueError('%s: not an ELF32 AArch64 object' % path)
    shoff, = struct.unpack_from('<I', b, 0x20); shentsize, shnum, shstr = struct.unpack_from('<HHH', b, 0x2e)
    sh = [struct.unpack_from('<IIIIIIIIII', b, shoff + i * shentsize) for i in range(shnum)]
    cstr = lambda tab, o: b[tab + o:b.index(b'\0', tab + o)].decode()
    names = [cstr(sh[shstr][4], s[0]) for s in sh]
    out = {i: [names[i], b[s[4]:s[4] + s[5]], {}, [], [], []] for i, s in enumerate(sh) if s[1] == 1 and s[2] & 4}
    syms = {}; symname = {}; deffunc = set(); drel = {}        # drel: {section: {offset: (reloc type, the target is in an executable section)}} for every section
    for s in sorted(sh, key=lambda s: s[1] != 2):
        if s[1] == 2:                                                   # SHT_SYMTAB
            marks = {}
            for k in range(s[5] // 16):
                nm, val, _sz, info, _o, shndx = struct.unpack_from('<IIIBBH', b, s[4] + 16 * k)
                symname[k] = cstr(sh[s[6]][4], nm)
                if info & 15 == 2 and 0 < shndx < shnum: deffunc.add(symname[k])
                if 0 < shndx < shnum: syms[k] = (shndx, val, sh[shndx][5], bool(sh[shndx][2] & 1), sh[shndx][8], bool(sh[shndx][2] & 4))   # defined in a section: (section, offset, size, SHF_WRITE, alignment, SHF_EXECINSTR)
                if shndx not in out: continue
                n = cstr(sh[s[6]][4], nm)
                if info & 15 == 2: out[shndx][3].append(val); out[shndx][5].append((val, n, _sz))
                if n[:2] in ('$x', '$d'): marks.setdefault(shndx, []).append((val, n[1]))
            for i, m in marks.items():
                m.sort(); end = len(out[i][1])
                for (v, k), nx in zip(m, m[1:] + [(end, 'x')]):
                    if k == 'd' and nx[0] > v: out[i][4].append((v, nx[0]))
        if s[1] == 4:                                                   # SHT_RELA
            for k in range(s[5] // 12):
                off, info, add = struct.unpack_from('<IIi', b, s[4] + 12 * k)
                y = syms.get(info >> 8)
                drel.setdefault(s[7], {})[off] = (info & 0xff, bool(y and y[5]))
                if s[7] not in out: continue         # the target: (section, offset, bytes from the target to the section end, the section is SHF_WRITE, section alignment)
                out[s[7]][2][off] = (info & 0xff, (y[0], y[1] + add, y[2] - y[1] - add, y[3], y[4]) if y and 0 <= y[1] + add < y[2] else None, symname.get(info >> 8))
    return [tuple(v) + (drel, deffunc) for _, v in sorted(out.items())]


def regno(name):
    """a general register name -> (number, is W-form); None for zr, vector and system registers."""
    if name in ('sp', 'wsp'): return SP, name == 'wsp'
    if name in ('fp', 'x29'): return 29, False
    if name in ('lr', 'x30'): return 30, False
    if name[0] in 'wx' and name[1:].isdigit(): return int(name[1:]), name[0] == 'w'
    return None


def gprwrites(w):
    """the general registers (0-31; 31 is zr or sp) an instruction word writes, from the destination fields of its encoding class: Rd, Rt, Rt2,
    the status register Rs of exclusives and the compare register of CAS, the base register of a writeback, x30 of bl/blr. Vector destinations
    are not general registers; a class that is not decoded here (reserved, SME) writes nothing here and is undecodable for capstone."""
    r = set(); rd, rn, rm, rt2 = w & 31, (w >> 5) & 31, (w >> 16) & 31, (w >> 10) & 31
    g = (w >> 25) & 15; top = (w >> 24) & 0x3f
    if g in (8, 9): r.add(rd)                                                   # data processing, immediate
    elif g in (10, 11):                                                         # branches, exceptions, system
        if w & 0xffc00000 == 0xd5000000 and w & (1 << 21): r.add(rd)           # mrs, sysl
        if w & 0xfc000000 == 0x94000000 or w & 0xfffffc1f == 0xd63f0000: r.add(30)     # bl, blr
    elif g in (5, 13):                                                          # data processing, register
        if not ((w >> 21) & 0xff == 0xd2 and (w >> 29) & 1): r.add(rd)          # all but ccmp/ccmn (rd is nzcv there)
    elif g == 2: r.add(rd)                                                      # SVE: a general destination is possible, conservative
    elif g in (4, 6, 12, 14):                                                   # loads and stores
        V = (w >> 26) & 1; size = w >> 30; opc = (w >> 22) & 3
        if w & 0xbf000000 in (0x0c000000, 0x0d000000):                          # AdvSIMD structure: post-index writes the base
            if w & (1 << 23): r.add(rn)
        elif w >> 24 == 0xd9 and (w >> 21) & 1: r.update((rd, rn))                                # MTE tag stores and loads
        elif top == 0x08:                                                       # exclusives, ordered, CAS
            o2, L, o1 = (w >> 23) & 1, (w >> 22) & 1, (w >> 21) & 1
            if L: r.update((rd, rt2))
            if (not L and not o2) or (o2 and o1): r.add(rm)
        elif top == 0x19:                                                       # RCpc unscaled: ldapur* load (opc != 0)
            if opc: r.add(rd)
        elif top & 0x3b == 0x18:                                                # load literal
            if not V and size != 3: r.add(rd)
        elif (w >> 27) & 7 == 5:                                                # pairs
            if not V and (w >> 22) & 1: r.update((rd, rt2))
            if (w >> 23) & 3 in (1, 3): r.add(rn)
        elif (w >> 27) & 7 == 7:                                                # single register
            load = not V and opc != 0 and not (size == 3 and opc == 2)         # prfm is not a load
            if (w >> 24) & 1: wb = False; ld = load
            elif not (w >> 21) & 1: wb = (w >> 10) & 3 in (1, 3); ld = load
            elif (w >> 10) & 3 == 0: wb = False; ld = True                      # atomics (LSE, ldapr): Rt
            elif (w >> 10) & 3 == 2: wb = False; ld = load
            else: wb = bool(w & 0x800); ld = True                               # ldraa / ldrab
            if ld: r.add(rd)
            if wb: r.add(rn)
    elif g in (7, 15):                                                          # scalar and vector FP, AdvSIMD
        if w & 0x7f000000 == 0x1e000000 and (not (w >> 21) & 1 or (w >> 10) & 0x3f == 0):     # FP <-> integer and FP <-> fixed conversions
            if (w >> 16) & 7 not in (2, 3, 7): r.add(rd)                        # scvtf, ucvtf, fmov to FP write a vector register
        elif w & 0x9fe08400 == 0x0e000400 and (w >> 11) & 15 in (5, 7): r.add(rd)         # smov, umov
    return r


class Ins:
    __slots__ = ('a', 'txt', 'wr', 'fl', 'kind', 'arg', 'mem', 'term', 'bar', 'call', 'bad', 'wbbase', 'x', 'asz', 'sreg', 'srcs', 'img', 'ix', 'post', 'rd', 'own')


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
            i.kind = i.arg = i.mem = i.term = i.bar = i.bad = i.wbbase = i.x = i.asz = i.sreg = i.img = i.ix = i.post = i.own = None; i.srcs = (); i.call = False
            ops = ci.operands
            _rd, wrs = ci.regs_access()
            i.rd = frozenset(g[0] for g in (regno(ci.reg_name(r)) for r in _rd) if g)
            i.wr = []; i.fl = False
            for r in wrs:
                nm = ci.reg_name(r)
                if nm == 'nzcv': i.fl = True; continue
                g = regno(nm)
                if g and m not in ('tst', 'cmp', 'cmn', 'ccmp', 'ccmn'): i.wr.append(g)       # flag-setting aliases write no register
            reg = lambda k: regno(ci.reg_name(ops[k].reg)) if len(ops) > k and ops[k].type == A.ARM64_OP_REG else None
            imm = lambda k: ops[k].imm if len(ops) > k and ops[k].type == A.ARM64_OP_IMM else None
            rel, rtgt, rname = relocs.get(ci.address, (None, None, None))
            if rel in (R_PG_HI21, R_ADD_LO12): i.img = (rel, rtgt)
            elif rel is not None: i.own = (rel, rtgt, rname)
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
                if len(ops) > k + 1 and ops[k + 1].type == A.ARM64_OP_IMM: i.post = ops[k + 1].imm
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
            for q in sorted(gprwrites(word)):
                if 19 <= q <= 27 and q not in [x[0] for x in i.wr]:
                    i.wr.append((q, False)); i.bad = i.bad or 'a write of x%d that the decoder did not report' % q
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


ASM = frozenset()
FUNCS = frozenset()
DISPATCH = 'fnvl_dispatch'     # the one name the gate reads: the dispatcher that rt/rt.c exports (defined global STT_FUNC of the runtime object)


def init(n, asm=frozenset(), funcs=frozenset()):
    global CPUSIZE, ASM, FUNCS
    CPUSIZE = n; ASM = asm; FUNCS = funcs


def deffuncs(path):
    """-> (names of the STT_FUNC symbols the object defines, names of its global ones)"""
    b = open(path, 'rb').read(); allf, glob = set(), set()
    shoff, = struct.unpack_from('<I', b, 32); shentsize, shnum = struct.unpack_from('<HH', b, 46)
    sh = [struct.unpack_from('<10I', b, shoff + k * shentsize) for k in range(shnum)]
    for h in sh:
        if h[1] != 2: continue
        stro = sh[h[6]][4]
        for o in range(h[4], h[4] + h[5], 16):
            nm, val, size, info, _other, shndx = struct.unpack_from('<IIIBBH', b, o)
            if info & 15 == 2 and 0 < shndx < shnum:
                n = b[stro + nm:b.index(b'\0', stro + nm)].decode(); allf.add(n)
                if info >> 4 == 1: glob.add(n)
    return allf, glob


def asmcheck(L, fsyms, code):
    """Rule (c), for an object whose source is assembler: every STT_FUNC preserves x19-x30 and sp on every path to every exit (ret, br, a
    relocated or out-of-range b, falling into the next symbol). -> ([(Ins, reason or '')], restores): one row per exit instruction, and rows for code that no
    STT_FUNC covers; restores are the addresses of the instructions all of whose writes to x19-x26 take back the register's entry value. Forward dataflow over each function: a register holds INIT(r) (its value at entry), a frame address (sp, offset) or nothing
    known; the frame slots hold the INIT value stored by an 8-byte `str`/`stp` through sp or a frame address, at a constant offset; a store that
    may overlap a slot, an index register on a frame base, a store through a register that is not a frame address once a frame address was
    handed out (copied to a register outside x19-x30, stored, or used in other arithmetic), and a call after that, forget the slots. sp changes only by a
    constant, by a writeback of a frame base, or by a copy from a frame address. At an exit: sp offset 0 and every register INIT(r)."""
    rows = []; cand = {}; starts = sorted(set(v for v, _, _ in fsyms)); rng = []
    for fi, f in enumerate(starts):
        nxt = starts[fi + 1] if fi + 1 < len(starts) else 1 << 62
        sz = max(z for v, _, z in fsyms if v == f)
        rng.append((f, min(nxt, f + sz) if sz else nxt))
    for ins in L:
        if not any(lo <= ins.a < hi for lo, hi in rng): rows.append((ins, 'code outside every STT_FUNC'))
    for f, end in rng:
        F = [i for i in L if f <= i.a < end]
        if not F: continue
        at = {i.a: k for k, i in enumerate(F)}
        INIT = lambda r: ('i', r)
        s0 = (0, tuple(INIT(r) for r in range(19, 31)), (), False)

        def join(a, b):
            sp = a[0] if a[0] == b[0] else None
            regs = tuple(x if x == y else None for x, y in zip(a[1], b[1]))
            da, db = dict(a[2]), dict(b[2])
            return (sp, regs, tuple(sorted((k, v) for k, v in da.items() if db.get(k) == v)), a[3] or b[3])

        def step(st, ins):
            sp, regs, slots, esc = st; regs = list(regs); slots = dict(slots)
            w = int.from_bytes(code[ins.a:ins.a + 4], 'little')
            tok = lambda r: None if r < 19 or r == 31 else regs[r - 19] if r <= 30 else None
            frame = lambda r: sp if r == 31 else tok(r)[1] if tok(r) and tok(r)[0] == 's' else None
            wr = {q for q in gprwrites(w) if 19 <= q <= 30} | {r for r, _ in ins.wr if 19 <= r <= 30}
            if ins.call: wr.add(30)
            handled = False; newsp = sp; assign = {}
            if ins.mem:
                base, idx, disp, wb, store, _sz = ins.mem
                fb = frame(base)
                V = (w >> 26) & 1; pair = ins.txt.split()[0].startswith(('ldp', 'stp', 'ldnp', 'stnp'))
                size = ins.asz
                if fb is not None:
                    handled = True
                    if idx or size is None:
                        if store: slots.clear()
                    else:
                        addr = fb + (0 if ins.post is not None else disp)
                        u = size // 2 if pair else size                     # the width of each register
                        rs = [w & 31, (w >> 10) & 31][:2 if pair else 1]
                        if store:
                            for k in [k for k, v in slots.items() if k < addr + size and addr < k + v[1]]: del slots[k]
                            if any(tok(r) and tok(r)[0] == 's' for r in rs): esc = True       # a frame address stored to memory
                            if not V and u in (4, 8):
                                for n, r in enumerate(rs):
                                    t = tok(r)
                                    # a 4-byte slot keeps only a guest register (x19-x26: below 2^32 by the guest-register rule, so the upper half is zero)
                                    if t is not None and (u == 8 or (t[0] == 'i' and 19 <= t[1] <= 26)): slots[addr + u * n] = (t, u)
                        elif not V and u in (4, 8):
                            for n, r in enumerate(rs):
                                v = slots.get(addr + u * n)
                                if 19 <= r <= 30 and v and v[1] == u and (u == 8 or v[0] == INIT(r)): assign[r] = v[0]
                    if wb:
                        nb = fb + (ins.post if ins.post is not None else disp)
                        if base == 31: newsp = nb
                        else: assign[base] = ('s', nb)
                elif store and esc: slots.clear()
                if wb and fb is None and 19 <= base <= 30: wr.add(base)
                if store and fb is None: handled = False
            elif ins.kind == 'addi' and ins.x and ins.x[0] == 'addi' and ins.arg == SP and ins.wr and ins.wr[0][0] == SP:       # add/sub sp, sp, #imm
                if sp is not None: newsp = sp + ins.x[2]
                else: newsp = None
                handled = True
            elif ins.kind in ('mov', 'addi') and ins.arg == SP and ins.wr and 19 <= ins.wr[0][0] <= 30 and not ins.wr[0][1]:     # a frame address into x19-x30
                if sp is not None:
                    assign[ins.wr[0][0]] = ('s', sp + (ins.x[2] if ins.kind == 'addi' and ins.x else 0)); handled = True
            elif ins.kind == 'mov' and ins.wr and ins.wr[0][0] == SP and ins.arg != SP and tok(ins.arg) and tok(ins.arg)[0] == 's':   # mov sp, frame address
                newsp = tok(ins.arg)[1]; handled = True
            elif ins.wr and any(r == SP for r, _ in ins.wr): newsp = None
            if not handled and (SP in ins.rd or any(tok(r) and tok(r)[0] == 's' for r in ins.rd)):
                if not (ins.term and ins.txt.startswith('ret')): esc = True
            if ins.call:
                if esc: slots.clear()
            gw = [r for r in wr if 19 <= r <= 26]          # a write of x19-x26 is a restore iff every such write takes back the entry value of that register
            if gw: cand[ins.a] = cand.get(ins.a, True) and all(assign.get(r) == INIT(r) for r in gw)
            for r in wr: regs[r - 19] = None
            for r, t in assign.items():
                if 19 <= r <= 30: regs[r - 19] = t
            if ins.call: regs[11] = None
            sp = newsp
            return (sp, tuple(regs), tuple(sorted(slots.items())), esc)

        def exitcheck(st):
            bad = []
            if st[0] != 0: bad.append('sp')
            bad += ['x%d' % r for r in range(19, 31) if st[1][r - 19] != INIT(r)]
            return ('not preserved at the exit: ' + ', '.join(bad)) if bad else ''

        state = {f: s0}; work = [f]; exits = {}
        while work:
            a = work.pop(); k = at[a]; ins = F[k]; st = step(state[a], ins)
            nxt = []
            m = ins.txt.split()[0]
            if ins.term and ins.term[0] == 'exit':
                if m in ('ret', 'br') or (m == 'b'): exits.setdefault(ins.a, []).append(st)
            else:
                tg = ins.term[-1] if ins.term else None
                if ins.term and ins.term[0] == 'b' and tg is not None: nxt.append(tg)
                else:
                    if tg is not None: nxt.append(tg)
                    nxt.append(ins.a + 4)
            for t in nxt:
                if t not in at: exits.setdefault(ins.a, []).append(st); continue
                if t not in state: state[t] = st; work.append(t)
                else:
                    j = join(state[t], st)
                    if j != state[t]: state[t] = j; work.append(t)
        # an exit's state is the join of the states that reached it, as computed at the last visit
        for a, sts in sorted(exits.items()):
            ins = F[at[a]]
            cur = sts[0]
            for x in sts[1:]: cur = join(cur, x)
            rows.append((ins, exitcheck(cur)))
    return rows, frozenset(a for a, ok in cand.items() if ok)


def analyse(path):
    """-> {'rows': {gate: [(object stem, 'FUNC+0xOFF', reason)]}, 'n': {counter: n}}: one row per universe item of each gate."""
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.detail = True
    tab = {g: {} for g in GATES}; res = {'n': {}}
    cnt = res['n']; stem = os.path.splitext(os.path.basename(path))[0]; cur = ['', []]

    def tick(k, n=1): cnt[k] = cnt.get(k, 0) + n

    def label(a):           # the containing STT_FUNC symbol of the section: a name for the report, no input of any decision
        best = None
        for v, nm, _ in cur[1]:
            if v <= a and (best is None or v > best[0]): best = (v, nm)
        return '%s+%#x' % (best[1], a - best[0]) if best else '%s+%#x' % (cur[0], a)

    def item(g, ins, tag='acc'): return tab[g].setdefault((cur[0], ins.a, tag), [label(ins.a), ''])

    def red(g, ins, why, tag='acc'):
        r = item(g, ins, tag)
        if why not in r[1]: r[1] = r[1] + '; ' + why if r[1] else why

    def glob(why, ins): tick('rule_violations'); red('order', ins, why, 'rule')

    for sname, code, relocs, funcs, data, fsyms, drel, _deff in elf(path):
        cur[0] = sname; cur[1] = fsyms
        L, badoff = decode(md, code, relocs, data)
        if L is None:
            i = Ins(); i.a = badoff; i.txt = '(undecodable)'; glob('undecodable word in ' + sname, i); continue
        tick('instructions', len(L))
        restores = frozenset()
        if os.path.abspath(path) in ASM:
            arows, restores = asmcheck(L, fsyms, code)
            for ins, why in arows:
                tick('asm_exits'); item('order', ins, 'asm')
                if why: tick('rule_violations'); red('order', ins, why, 'asm')
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
            return [[GUEST] * 31, [False] * 19 + [True] * 8 + [False] * 4, [1] * 31, [(3 << 40) | (n << 5) | r for r in range(31)], None, [None] * 31, [None] * 31, [None] * 31, [None] * 31, {}, NEG, [64] * 31]

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
            for r in range(31):
                if cur[11][r] | inc[11][r] != cur[11][r]: cur[11][r] |= inc[11][r]; ch = True
            e = inc[10] if cur[10] is None else cur[10] if inc[10] is None else min(cur[10], inc[10])
            if e != cur[10]: cur[10] = e; ch = True
            return ch

        def imgro(s, ins):
            """an IMAGE load -> ('ro', (section, offset, stride, count)): the words it can read, in a read-only section and inside it;
            ('bound', None): read-only section, not proven inside it; ('other', None): writable or unknown target, a store, a writeback"""
            base, idx, disp, wb, store, _sz = ins.mem; st = s[7][base]
            if store or wb or not ins.asz or not st: return 'other', None
            if st[0] == 'at':
                if st[2]: return 'other', None
                if not idx:
                    return ('ro', (st[5], st[3] + disp, 1, 1)) if 0 <= disp and disp + ins.asz <= st[1] else ('bound', None)
                if ins.ix:
                    rm, eb, sgn, sh = ins.ix; v = s[6][rm]; v = (1 << eb) - 1 if v is None else min(v, (1 << eb) - 1)
                    if not (sgn and v >= 1 << (eb - 1)) and (v << sh) + ins.asz <= st[1]: return 'ro', (st[5], st[3], 1 << sh, v + 1)
                    return 'bound', None
                return 'other', None
            if st[0] == 'pg' and ins.own and not idx and ins.own[1] and ins.own[1][:2] == st[1][:2]:          # ldr :lo12: of the page's symbol
                t = ins.own[1]
                if t[3]: return 'other', None
                return ('ro', (t[0], t[1], 1, 1)) if ins.asz <= t[2] else ('bound', None)
            return 'other', None

        BR = {'br', 'blr', 'braa', 'braaz', 'brab', 'brabz', 'blraa', 'blraaz', 'blrab', 'blrabz'}
        BRCLS = ((2, 'cpu'), (4, 'frame'), (8, 'guest'), (16, 'image_writable_or_unknown_or_not_a_function'), (32, 'image_unbounded_or_unrelocated'), (64, 'unknown_origin'))
        TABLE, UNK, DISP, GOTF = 1, 64, 128, 256; OKB = TABLE | DISP | GOTF; ILLB = 2 | 4 | 8 | 16 | 32

        def loadmask(s, ins):
            """provenance bit of a loaded value: 1 a word of a bounded read-only image table, all relocated to executable sections; 2 cpu; 4 frame; 8 guest;
            16 image, writable or unknown target; 32 image, read-only but not proven inside the table or a word without a relocation to code"""
            base = ins.mem[0]; c = FRAME if base == SP else s[0][base]
            if c == CPU: return 2
            if c == FRAME: return 4
            if c == GUEST: return 8
            if ins.kind == 'image': return GOTF if ins.own and ins.own[0] == R_GOT_LOAD and ins.own[2] in FUNCS else 16
            k, pos = imgro(s, ins)
            if k == 'other': return 16
            if k == 'bound' or ins.asz not in (4, 8): return 32
            sec, off, stride, count = pos
            return 1 if all(drel.get(sec, {}).get(off + i * stride, (0, False))[1] for i in range(count)) else 32

        def brcheck(ins, m):
            """every br/blr is in the universe. Legal: the target is the return value of a call to the dispatcher (copies of it), a word of a
            bounded read-only image table whose words are relocated to code, or a GOT entry of an STT_FUNC symbol, on every path"""
            tick('br_indirect'); item('order', ins, 'br')
            if m and not m & ~OKB:
                for bit, nm in ((TABLE, 'table_target'), (DISP, 'dispatch_result'), (GOTF, 'got_function')):
                    if m & bit: tick('br_' + nm)
                return
            names = [nm for bit, nm in BRCLS if m & bit] or ['unknown_origin']
            for nm in names: tick('br_illegal_' + nm)
            tick('rule_violations'); red('order', ins, 'an indirect branch whose target is not a dispatcher result, a relocated read-only table word or a function GOT entry on every path (' + ', '.join(names) + ')', 'br')

        def step(s, ins, check):
            cls, wc, al, vn, kc, ub, off = s[0], s[1], s[2], s[3], s[5], s[6], s[7]
            fo, slots = s[8], s[9]
            if check and ins.mem: checkmem(s, ins)
            pv = s[11]; ld = loadmask(s, ins) if ins.mem and not ins.mem[4] else 0
            pin = 0
            for r in ins.rd:
                if r < 31: pin |= pv[r]
            if ins.txt.split()[0] in BR and check: brcheck(ins, pin)
            disp = ins.call and ins.own and ins.own[2] == DISPATCH
            if ins.call:
                for r in range(19):          # a frame address in a call-clobbered register is handed to the callee
                    if cls[r] == FRAME: s[10] = NEG if fo[r] is None else fo[r] if s[10] is None else min(s[10], fo[r])
                if s[10] is not None:        # a callee may write at and above every frame address that escaped
                    for k in [k for k in slots if k + 8 > s[10]]: del slots[k]
                for r in list(range(19)) + [30]: pv[r] = UNK; cls[r] = GUEST; wc[r] = False; al[r] = 1; vn[r] = (1 << 40) | (ins.a << 5) | r; kc[r] = None; ub[r] = None; off[r] = None
                if disp: pv[0] = DISP
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
                elif ins.kind == 'addi' and ins.img[0] == R_ADD_LO12 and ins.arg != SP and cls[ins.arg] == IMAGE and off[ins.arg] == ('pg', ins.img[1]): ioff = ('at', ins.img[1][2], ins.img[1][3], ins.img[1][1], ins.img[1][4], ins.img[1][0])
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
                if check and r == 27: tick('x27_writes'); glob('x27 written', ins)
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
                if ins.a in restores and 19 <= r <= 26: wc[r] = True; ub[r] = W32       # rule (c) proved the entry value is back: below 2^32 like at entry
                if check and 19 <= r <= 26 and not w and not wc[r]: glob('X-form write of a guest register not proven below 2^32', ins)
                fo[r] = None
                if cls[r] == FRAME: fo[r] = nfo
            for r, w in ins.wr:                 # provenance of the written registers: a load gives its class, anything else the union of the registers it reads
                if r != SP and r != ins.wbbase: pv[r] = ld if ld else pv[ins.arg] if ins.kind == 'mov' and ins.arg != SP and len(ins.wr) == 1 else UNK | (pin & ILLB)
            if ins.kind == 'tst' and ins.arg[0] != SP: s[4] = (vn[ins.arg[0]], ins.arg[1])
            elif ins.fl: s[4] = None

        def checkmem(s, ins):
            base, idx, disp, wb, store, sz = ins.mem
            c = FRAME if base == SP else s[0][base]
            img = None                       # an IMAGE access: None for a proven read-only load (host); else a guest access, with the alignment proof
            if c == IMAGE:
                if ins.kind == 'image': tick('host_image_got'); return          # the load of a GOT entry: the linker's word, not data
                k, pos = imgro(s, ins)
                if k == 'ro': tick('host_image_table' if idx else 'host_image_ro'); return
                st = s[7][base]
                img = bool(st and st[0] == 'at' and not (idx or disp or wb) and sz > 1 and st[3] % sz == 0 and st[4] % sz == 0)
            if c != GUEST and c != IMAGE:
                tick('host_' + CLS[c])
                if c == CPU and not idx and not (wb or ins.asz is None or s[7][base][0] + disp < 0 or s[7][base][1] + disp + ins.asz > CPUSIZE): tick('cpu_accesses_inside_struct'); return
                if c == CPU and not idx:
                    tick('cpu_outside_struct'); item('order', ins); item('addrwrap', ins)
                    red('order', ins, 'an access through the cpu pointer not proven inside struct cpu'); red('addrwrap', ins, 'an access through the cpu pointer not proven inside struct cpu'); return
                if idx:
                    item('order', ins); item('addrwrap', ins)
                    red('order', ins, 'a %s base with an index register' % CLS[c])
                    red('addrwrap', ins, 'a %s base with an index register' % CLS[c])
                return
            tick('guest_store' if store else 'guest_load')
            if c == IMAGE: tick('guest_image')       # an IMAGE access that is not a proven read-only load is a guest access
            item('order', ins); item('addrwrap', ins)
            if sz: tick('guest_ordered')
            gacc[id(ins)] = not sz
            if idx or disp or wb: red('addrwrap', ins, 'the operand is not the base register alone')
            elif not s[1][base] and c != IMAGE: red('addrwrap', ins, 'the base is not proven a 32-bit value')       # an IMAGE address of an ILP32 object is a 32-bit link-time address (ASM-007)
            if sz > 1:
                item('align', ins)
                if not (not (idx or disp or wb) and (img if c == IMAGE else s[2][base] >= sz)): red('align', ins, 'no alignment test of %d bytes dominates the access' % sz)

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

        copy = lambda s: [list(s[0]), list(s[1]), list(s[2]), list(s[3]), s[4], list(s[5]), list(s[6]), list(s[7]), list(s[8]), dict(s[9]), s[10], list(s[11])]
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
                        tick('guest_plain_load_dmb', len(wait)); wait = []
                    continue
                if not ins.call and id(ins) not in gacc: continue
                for w in wait: red('order', w, 'a plain guest load without dmb ishld before the next guest access or call')
                wait = []
                if gacc.get(id(ins)):
                    if not ins.mem[4]: wait.append(ins)
                    elif sb: tick('guest_plain_store_dmb')
                    else: red('order', ins, 'a plain guest store without dmb ishst after the last guest access or call')
                sb = False
            for w in wait: red('order', w, 'a plain guest load without dmb ishld before the block end')
    res['rows'] = {g: sorted((stem, lab, why) for lab, why in tab[g].values()) for g in GATES}
    return res


def run(rtobj, lifted, asm=()):
    """-> ({gate: [(object stem, 'FUNC+0xOFF', reason)]}, {counter: n}, number of objects). Every row is one universe item, sorted;
    a certified row has reason ''. The verdict is computed from these rows only (verdict)."""
    files = []; asm = frozenset(os.path.abspath(a) for a in asm)
    rtall, rtglob = deffuncs(rtobj)
    if DISPATCH not in rtglob: raise ValueError('%s: no defined global STT_FUNC %s (the dispatcher)' % (rtobj, DISPATCH))
    for a in lifted:
        files += sorted(os.path.join(a, f) for f in os.listdir(a) if f.endswith('.o')) if os.path.isdir(a) else [a]
    files = list(dict.fromkeys(files))
    funcs = set(rtall)
    for f in files: funcs |= deffuncs(f)[0]
    init(cpusize(rtobj), asm, frozenset(funcs))
    rows = {g: [] for g in GATES}; n = {k: 0 for k in ('x27_writes', 'cpu_outside_struct', 'cpu_accesses_inside_struct', 'rule_violations')}     # the global counts of the two CPU-class checks are always reported
    with multiprocessing.Pool(int(os.environ.get('JOBS', '0')) or os.cpu_count(), init, (CPUSIZE, asm, FUNCS)) as pool:
        for r in pool.imap(analyse, files, chunksize=4):
            for g in GATES: rows[g] += r['rows'][g]
            for k, v in r['n'].items(): n[k] = n.get(k, 0) + v
    return {g: sorted(v) for g, v in rows.items()}, n, len(files)


def rows(rtobj, lifted, asm=()):
    """-> {'order': [...], 'addrwrap': [...], 'align': [...]}: for each gate, the sorted (object stem, 'FUNC+0xOFF', reason) of every
    universe item; reason '' is a certified item. FUNC is the containing STT_FUNC symbol, a label of the report and no input of a decision."""
    return run(rtobj, lifted, asm)[0]


def verdict(g, rws, n):
    """the gate's result from its rows: green iff something was decoded and every row is certified"""
    bad = [r for r in rws if r[2]]
    return bool(n.get('instructions')) and not bad, len(rws), len(rws) - len(bad), bad


def main(argv):
    objs, asm, k = [], [], 1
    while k < len(argv):
        if argv[k] == '--asm': asm.append(argv[k + 1]); k += 2
        else: objs.append(argv[k]); k += 1
    rws, n, nfiles = run(argv[0], objs + asm, asm); rc = 0
    for g in GATES:
        green, u, c, bad = verdict(g, rws[g], n)
        counts = ', '.join('%s %d' % kv for kv in sorted(n.items()))
        res = {'format': 'fnvnx-gate/1', 'name': g, 'status': 'green' if green else 'red', 'universe': u, 'certified': c,
               'uncertified': ['%s %s: %s' % r for r in bad[:50]], 'counts': n,
               'detail': '%d objects; struct cpu %d bytes; %d of %d certified; %s' % (nfiles, CPUSIZE, c, u, counts) if nfiles else 'no object given'}
        with open(os.path.join(os.environ['GATE_OUT'], g + '.json'), 'w') as fh: json.dump(res, fh, sort_keys=True)
        print('%s %s %d/%d' % (g, res['status'], c, u), file=sys.stderr)
        rc |= 0 if green else 2
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
