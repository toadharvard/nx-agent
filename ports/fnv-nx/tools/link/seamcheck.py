#!/usr/bin/env python3
"""seamcheck.py - static seam checker: call-site seams tested all at once instead of one boot fault at a time.

    python3 seamcheck.py <link root> <FalloutNV.exe> [--vcall] [--layout] [--shard i/n] [--jobs N]
                         [--partial] [--only ADDR[,ADDR..]] -o <out>/seams.tsv

<link root> is a link directory (out/addrmap.tsv, out/refs.tsv, fixed/*.o; guest/*.elf when present). The exe is the
player's own (agent-only, D3). The tool reads both and writes only its own output (`-o`, a link's out/ directory,
never the repository): one TSV, plus `<-o>.stats.txt` with the counts, plus the exe vftable scan cache
(`<-o dir>/exe_vftables.json`, exevft.load).

Checks (each one a function `run_<name>(ctx) -> (rows, stats)`; rows start with the check name so the outputs of
several checks share one file):

* `--vcall` (this module) - the virtual call sites of every function that has both a native body (kinds F / C / X of
  addrmap.tsv, bodies from fixed/*.o and their relocations) and an exe body:
    1. exe: the function is disassembled by recursive descent (capstone x86; jump tables followed) and its calls
       listed in address order as events: `D` direct (target), `V` virtual (`mov r,[obj+voff]; call [r+off]`, also
       `mov r,[r+off]; call r`), `I` other indirect (import slots, function pointers);
    2. native: the AArch64 ILP32 body is swept linearly, `ldr wN,[xM,#voff]; ldr wK,[xN,#off]; blr xK` is a `V`
       event, `bl <sym>` a `D` event (the symbol is mapped to an exe address through the address map);
    3. alignment (conservative, see `align`): direct-call events whose targets both sides know and that occur on both
       sides are anchors; the anchor sequences must be identical on both sides; between consecutive anchors the
       virtual calls of both sides must be equally many and are paired in order, and a pair's vptr offsets must
       agree. A function where this fails is skipped and counted (reason in the stats);
    4. receiver class: `this`-based sites (receiver is the incoming `this` on both sides, the native symbol a member
       function of class C) are checked against the V pair of C (primary vtable); for every other site the receiver's
       class is inferred (`Infer`, `--infer` letters, the note's `by-<sources>`): (a) the vftable a constructor or an
       inlined vptr store gives the object, (b) the values the image stores in the global / field / return value /
       argument / spill slot the receiver comes from, an import's result (a COM object: exe slot order), (c) RTTI
       hierarchies for a static type, (d) native GOT globals and mangled parameter types. Facts of every exe function
       are cached in `<-o dir>/receiver_facts.pickle` (a full run writes it, --only / --shard read it). A site with
       no inferred class is checked against all native/exe vtable pairs (addrmap V rows, the exe vftables of exevft;
       the slot alignment is the one vtmetric.compare_slots picks) that have both slots, and the verdict says how
       many agree. Sites of one function whose targets cross (GCC laid two neighbouring calls out in the other order)
       are re-paired when both then agree (`repaired-crossed`, repair_crossed). SEAMCHECK_DEBUG=1 prints the
       evidence per inferred site;
    5. per candidate pair: exe slot j = off_x86/4, native slot k = off_aarch64/4; the pair agrees when the native
       table's slot k reaches the native counterpart of the function the exe vftable's slot j reaches (or, for
       destructor slots and trap slots with no address, when the pair's own k -> j mapping, the minus-one rule,
       maps k to j); it disagrees otherwise.
  Verdicts: `ok` (all comparable candidates agree), `mismatch` (all comparable candidates disagree), `mixed` (some of
  each: depends on the receiver's class), `unknown` (no pair has both slots, or the vptr offsets differ). A
  mismatch or mixed row also says `delta` = k - j.

* `--layout` - the LAYOUT check (seamlayout.py, W40): native object sizes against the exe's `new` / static / field
  evidence, in the common columns (see run_layout). `--tree` passes a prepared tree for its layout probe.

Rows (tab separated, `#` header): check, function (native symbol), exe function, exe site, native site (offset in
the function, hex), receiver (class, or `any`), exe slot, native slot, exe target, native target, verdict, note.
"""
import argparse
import collections
import multiprocessing
import os
import pickle
import re
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import exevft     # noqa: E402
import vtables    # noqa: E402
import vtmetric   # noqa: E402

try:
    import capstone
    from capstone import x86 as cx86
    from capstone import arm64 as ca64
except ImportError:    # the tests skip when capstone is missing
    capstone = None

NATIVE_KINDS = ('F', 'C', 'X')
HEADER = ('check', 'function', 'exe_function', 'exe_site', 'native_site', 'receiver', 'exe_slot', 'native_slot',
          'exe_target', 'native_target', 'verdict', 'note')
# direct calls the native code makes that have no exe counterpart (GCC's exception cleanup, runtime helpers)
NOISE = re.compile(r'^(_Unwind_|__cxa_|__gxx_|__stack_chk|abort$|__fnvnx_(vtrap|trap|unwind))')
MAX_EXE_FUNC = 0x10000

# ---------------------------------------------------------------------------------------------------- event model
# An event is a tuple:  ('D', target address or None, name, this tag, site)     a direct call (or tail call)
#                       ('V', voff, off, root, expr, site)    a virtual call through [expr+voff] then +off
#                       ('I', None, site)                     any other indirect call
# Values are tracked as tags: ('this',) the incoming this, ('arg', n), ('u', n) an unknown value (a fresh id per
# register definition), ('ld', tag, off) the word at [tag + off]. root: 'this' when the receiver (expr) is the
# incoming this. expr: the receiver's tag, equal tags are the same expression; `this tag`: the tag of the call's
# this register (ecx / x0). site: address (exe) / offset (native).

X86_CANON = {}
for _n, _names in {'eax': ('eax', 'ax', 'al', 'ah'), 'ecx': ('ecx', 'cx', 'cl', 'ch'),
                   'edx': ('edx', 'dx', 'dl', 'dh'), 'ebx': ('ebx', 'bx', 'bl', 'bh'),
                   'esi': ('esi', 'si', 'sil'), 'edi': ('edi', 'di', 'dil'), 'ebp': ('ebp', 'bp'),
                   'esp': ('esp', 'sp')}.items():
    for _x in _names:
        X86_CANON[_x] = _n
X86_CLOBBER = ('eax', 'ecx', 'edx')


def _x86_reg(md, r):
    return X86_CANON.get(md.reg_name(r) or '', md.reg_name(r))


def x86_disasm(read, start, limit_end=None):
    """Recursive-descent disassembly of the exe function at `start` -> [capstone insn] in address order. read(va, n)
    -> bytes or None. Follows jumps and conditional branches inside [start, limit_end) and switch tables
    (`jmp [reg*4+table]`)."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    hi = start + MAX_EXE_FUNC
    if limit_end:
        hi = min(hi, limit_end)
    seen = {}
    work = [start]
    while work:
        a = work.pop()
        while a not in seen and start <= a < hi:
            code = read(a, 16)
            if code is None:
                n = 1
                while n < 16 and read(a, n + 1) is not None:
                    n += 1
                code = read(a, n) if n else None
                if not code:
                    break
            got = next(md.disasm(code, a), None)
            if got is None:
                break
            seen[a] = got
            g = got.id
            nxt = a + got.size
            if g in (cx86.X86_INS_RET, cx86.X86_INS_INT3, cx86.X86_INS_UD2, cx86.X86_INS_RETF, cx86.X86_INS_HLT):
                break
            if g == cx86.X86_INS_JMP:
                op = got.operands[0]
                if op.type == capstone.CS_OP_IMM:
                    work.append(op.imm)
                elif op.type == capstone.CS_OP_MEM and op.mem.index and op.mem.scale == 4 and not op.mem.base:
                    t = op.mem.disp & 0xffffffff
                    for k in range(512):
                        w = read(t + 4 * k, 4)
                        if w is None:
                            break
                        w = struct.unpack('<I', w)[0]
                        if not start <= w < hi:
                            break
                        work.append(w)
                break
            if cx86.X86_GRP_JUMP in got.groups and g != cx86.X86_INS_JMP:
                op = got.operands[0]
                if op.type == capstone.CS_OP_IMM:
                    work.append(op.imm)
            a = nxt
    return [seen[a] for a in sorted(seen)]


DATA_LO, DATA_HI = 0x00fdf000, 0x02100000    # the image's data (exevft.DATA): an immediate there is an address


def _gload(tag, disp):
    """The tag of the word at [tag + disp]: a global's word when tag is a global's address (('ga', A))."""
    if tag and tag[0] == 'ga' and isinstance(tag[1], int):
        return ('ld', ('ga', (tag[1] + disp) & 0xffffffff), 0)
    return ('ld', tag, disp)


def _imm_tag(v):
    v &= 0xffffffff
    return ('ga', v) if DATA_LO <= v < DATA_HI else ('imm', v)


def x86_events(insns, start, end_hint=None, info=None):
    """-> [event] of an exe function's calls, in address order.

    With `info` (a dict), the facts the receiver inference (`Infer`) reads are collected into it as well:
    'thisof' [(tag, callee, site)] the direct calls and the tag of their ecx; 'vstore' [(tag, imm, site)] the
    `mov dword ptr [r], imm` / `mov [r], r2` stores at displacement 0 of an address-like value (a vptr store when the
    value is a vftable start); 'stores' [(key, tag, site)] the dword stores to a global (key ('g', A)) or a field of
    `this` (('f', disp)); 'rets' [tag of eax at each ret]; 'calls' [(callee, [tags pushed since the previous call, last
    pushed first])]; 'slots' {ebp disp: [tags written]} of the spill slots written more than once (a reload of one is
    the tag ('phi', disp)); 'retthis' {('ret', callee, site): ecx tag} for the constructor results."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    tags = {'ecx': ('this',)}
    # an ebp frame only when the function sets one up (`mov ebp, esp`); otherwise ebp is a general register
    frame = any(i.id == cx86.X86_INS_MOV and len(i.operands) == 2 and i.operands[0].type == capstone.CS_OP_REG and
                i.operands[1].type == capstone.CS_OP_REG and _x86_reg(md, i.operands[0].reg) == 'ebp' and
                _x86_reg(md, i.operands[1].reg) == 'esp' for i in insns[:8])
    stack = {4 + 4 * n: ('arg', n) for n in range(1, 9)} if frame else {}  # [ebp + disp]: spilled values
    writes = collections.Counter()   # spill slots written more than once hold a merge of values (a phi)
    if frame:
        for ins in insns:
            for op in ins.operands:
                if op.type == capstone.CS_OP_MEM and op.mem.base and not op.mem.index and \
                        _x86_reg(md, op.mem.base) == 'ebp' and op.access & capstone.CS_AC_WRITE:
                    writes[op.mem.disp] += 1
    multi = {d for d, n in writes.items() if n > 1 and d < 0}
    uid = [0]
    depth = 0          # bytes pushed since the entry while the entry block runs straight (None after it)
    pushed = []
    if info is not None:
        for k in ('thisof', 'vstore', 'stores', 'rets', 'calls'):
            info.setdefault(k, [])
        info.setdefault('retthis', {})
    slots = info.setdefault('slots', {}) if info is not None else {}

    def tg(r):
        if r not in tags:
            uid[0] += 1
            tags[r] = ('u', uid[0])
        return tags[r]

    def mem_tag(m):
        """The tag of the dword at memory operand m (None when untracked)."""
        if m.segment or m.index:
            return None
        if not m.base:
            return _gload(('ga', m.disp & 0xffffffff), 0)
        b = _x86_reg(md, m.base)
        if b == 'ebp' and frame:
            if m.disp in multi:
                return ('phi', m.disp)
            if m.disp not in stack:
                uid[0] += 1
                stack[m.disp] = ('u', uid[0])
            return stack[m.disp]   # a reload of a spilled value
        if b == 'esp':
            if depth is not None and m.disp - depth >= 4 and not (m.disp - depth) % 4:
                return ('arg', (m.disp - depth) // 4)
            return None
        return _gload(tg(b), m.disp)

    def note_store(m, val, site):
        if info is None or m.segment or m.index:
            return
        if not m.base:
            key, btag = ('g', m.disp & 0xffffffff), ('ga', m.disp & 0xffffffff)
        else:
            b = _x86_reg(md, m.base)
            if b in ('esp',) or (b == 'ebp' and frame):
                return
            btag = tg(b)
            key = None
            if btag[0] == 'ga' and isinstance(btag[1], int):
                key = ('g', (btag[1] + m.disp) & 0xffffffff)
            elif btag == ('this',):
                key = ('f', m.disp)
        if key and m.disp >= 0:
            info['stores'].append((key, val, site))
        if (m.disp == 0 or not m.base) and val and val[0] == 'ga':
            info['vstore'].append((btag if m.base else ('ga', m.disp & 0xffffffff), val[1], site))
    out = []
    lo = start
    hi = max([i.address + i.size for i in insns] or [start])
    for ins in insns:
        g = ins.id
        ops = ins.operands
        new = None   # (reg, tag) to set after clearing the written registers
        if g == cx86.X86_INS_RET and info is not None:
            info['rets'].append(tags.get('eax'))
        if g == cx86.X86_INS_CALL or g == cx86.X86_INS_JMP:
            depth = None
            op = ops[0] if ops else None
            if op is None:
                continue
            if g == cx86.X86_INS_JMP and op.type == capstone.CS_OP_IMM and lo <= op.imm < hi:
                continue
            if g == cx86.X86_INS_JMP and op.type == capstone.CS_OP_MEM and op.mem.index:
                continue   # a switch
            ret = None
            if op.type == capstone.CS_OP_IMM:
                out.append(('D', op.imm, None, tags.get('ecx'), ins.address))
                ret = ('ret', op.imm & 0xffffffff, ins.address)
                if info is not None:
                    info['thisof'].append((tags.get('ecx'), op.imm & 0xffffffff, ins.address))
                    info['calls'].append((op.imm & 0xffffffff, pushed[::-1][:8], ins.address))
                    info['retthis'][ret] = tags.get('ecx')
            elif op.type == capstone.CS_OP_REG:
                t = tags.get(_x86_reg(md, op.reg))
                ev = _virt(t, None)
                out.append(ev + (ins.address,) if ev else ('I', None, ins.address))
                if not ev and t and t[0] == 'ld' and t[1][0] == 'ga':
                    ret = ('cr', t, ins.address)    # a call through a global (an import slot, a function pointer)
            elif op.type == capstone.CS_OP_MEM:
                m = op.mem
                if m.index or not m.base:
                    out.append(('I', None, ins.address))
                    if not m.index and not m.base:
                        ret = ('cr', _gload(('ga', m.disp & 0xffffffff), 0), ins.address)
                else:
                    t = tg(_x86_reg(md, m.base))
                    ev = _virt(t, m.disp)
                    out.append(ev + (ins.address,) if ev else ('I', None, ins.address))
            if g == cx86.X86_INS_CALL:
                pushed = []
                for r in X86_CLOBBER:
                    tags.pop(r, None)
                if ret:
                    tags['eax'] = ret
            continue
        if cx86.X86_GRP_JUMP in ins.groups or g in (cx86.X86_INS_RET, cx86.X86_INS_RETF):
            depth = None
        if g == cx86.X86_INS_PUSH and ops:
            s = ops[0]
            if s.type == capstone.CS_OP_REG:
                pushed.append(tg(_x86_reg(md, s.reg)))
            elif s.type == capstone.CS_OP_IMM:
                pushed.append(_imm_tag(s.imm))
            elif s.type == capstone.CS_OP_MEM:
                pushed.append(mem_tag(s.mem))
            else:
                pushed.append(None)
            if depth is not None:
                depth += 4
            continue
        if g == cx86.X86_INS_POP and depth is not None:
            depth -= 4
        if g in (cx86.X86_INS_SUB, cx86.X86_INS_ADD) and len(ops) == 2 and ops[0].type == capstone.CS_OP_REG and \
                _x86_reg(md, ops[0].reg) == 'esp':
            if depth is not None and ops[1].type == capstone.CS_OP_IMM:
                depth += ops[1].imm if g == cx86.X86_INS_SUB else -ops[1].imm
            else:
                depth = None
        # tag propagation: only `mov r, r2`, `mov r, [base+disp]`; every other write clears the register
        if g == cx86.X86_INS_MOV and len(ops) == 2 and ops[0].type == capstone.CS_OP_MEM and ops[0].size == 4 and \
                ops[1].type in (capstone.CS_OP_REG, capstone.CS_OP_IMM):
            m = ops[0].mem
            val = tg(_x86_reg(md, ops[1].reg)) if ops[1].type == capstone.CS_OP_REG else _imm_tag(ops[1].imm)
            note_store(m, val, ins.address)
            if m.base and not m.index and frame and _x86_reg(md, m.base) == 'ebp':
                if ops[1].type == capstone.CS_OP_REG:
                    if m.disp in multi:
                        slots.setdefault(m.disp, []).append(val)
                    else:
                        stack[m.disp] = val   # a spill of a register
                elif m.disp in multi:
                    slots.setdefault(m.disp, []).append(val)
                continue
        elif frame and multi:
            for op in ops:   # any other write to a merged spill slot: its value is unknown
                if op.type == capstone.CS_OP_MEM and op.mem.base and not op.mem.index and op.mem.disp in multi \
                        and _x86_reg(md, op.mem.base) == 'ebp' and op.access & capstone.CS_AC_WRITE:
                    slots.setdefault(op.mem.disp, []).append(None)
        if g == cx86.X86_INS_MOV and len(ops) == 2 and ops[0].type == capstone.CS_OP_REG and ops[0].size == 4:
            d = _x86_reg(md, ops[0].reg)
            s = ops[1]
            if s.type == capstone.CS_OP_REG:
                new = (d, tg(_x86_reg(md, s.reg)))
            elif s.type == capstone.CS_OP_MEM and not s.mem.index and s.mem.segment == 0:
                new = (d, mem_tag(s.mem))
            elif s.type == capstone.CS_OP_IMM:
                t = _imm_tag(s.imm)
                new = (d, t if t[0] == 'ga' else None)
            else:
                new = (d, None)
        elif g == cx86.X86_INS_LEA and len(ops) == 2 and ops[0].type == capstone.CS_OP_REG and \
                not ops[1].mem.base and not ops[1].mem.index and not ops[1].mem.segment:
            t = _imm_tag(ops[1].mem.disp)
            new = (_x86_reg(md, ops[0].reg), t if t[0] == 'ga' else None)
        try:
            _, wr = ins.regs_access()
        except capstone.CsError:
            wr = []
        if g == cx86.X86_INS_POP:   # an epilogue restore (linear order puts it before later blocks)
            wr = []
        for r in wr:
            tags.pop(_x86_reg(md, r), None)
        if new and new[1] is not None:
            tags[new[0]] = new[1]
    if multi:
        # a merged slot whose every write is the incoming this is this (the /Od spill of ecx, rewritten)
        for d in multi:
            ws = slots.get(d, [])
            if ws and all(w == ('this',) for w in ws):
                out = [_subst_phi(e, d) for e in out]
    return out


def _subst_phi(e, d):
    if e[0] == 'V' and e[4] == ('phi', d):
        return e[:3] + ('this', ('this',)) + e[5:]
    if e[0] == 'D' and e[3] == ('phi', d):
        return e[:3] + (('this',),) + e[4:]
    return e


def _virt(tag, disp):
    """A call through register tag (`disp` None: the register itself is the target) -> ('V', voff, off, recv) or None.
    tag chain: ('ld', X, voff) is "loaded from [X + voff]"."""
    if disp is None:     # call r where r = [vptr + off]
        if tag and tag[0] == 'ld' and tag[1] and tag[1][0] == 'ld':
            return ('V', tag[1][2], tag[2], _root(tag[1][1]), tag[1][1])
        return None
    if tag and tag[0] == 'ld':
        return ('V', tag[2], disp, _root(tag[1]), tag[1])
    return None


def _root(t):
    return 'this' if t == ('this',) else None


# ------------------------------------------------------------------------------------------------ AArch64 side

def a64_canon(name):
    if name and name[0] in 'wx' and name[1:].isdigit():
        return 'x' + name[1:]
    if name in ('sp', 'wsp'):
        return 'sp'
    return name


CALLEE_SAVED = {'x%d' % i for i in range(19, 31)}


R_P32_ADR_GOT_PAGE, R_P32_LD32_GOT_LO12, R_P32_ADR_PREL_PG_HI21, R_P32_ADD_ABS_LO12 = 26, 27, 11, 12


def a64_events(code, relocs, resolve, rtypes=None):
    """-> [event] of a native function's calls (code: bytes of the function; relocs: {offset: symbol name} of the
    relocations of its section; resolve(name) -> exe address or None). rtypes ({offset: relocation type}, optional):
    with it, `adrp` + GOT `ldr` (or `adrp` + `add :lo12:`) of a symbol is the address of that global (('ga', exe
    address or symbol)), so a word loaded from it is the global's value and a call through a global function pointer
    is no virtual call."""
    rtypes = rtypes or {}
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    md.detail = True
    tags = {'x0': ('this',)}
    for r in range(1, 8):
        tags['x%d' % r] = ('arg', r)
    uid = [0]

    def tg(r):
        if r not in tags:
            uid[0] += 1
            tags[r] = ('u', uid[0])
        return tags[r]
    out = []
    for ins in md.disasm(code, 0):
        g = ins.id
        ops = ins.operands
        off = ins.address
        if g in (ca64.ARM64_INS_BL, ca64.ARM64_INS_B) and ins.mnemonic in ('bl', 'b'):
            if off in relocs:
                nm = relocs[off]
                out.append(('D', resolve(nm), nm, tags.get('x0'), off))
            elif g == ca64.ARM64_INS_BL:
                out.append(('D', None, None, tags.get('x0'), off))
            if g == ca64.ARM64_INS_BL:
                for r in range(0, 19):
                    tags.pop('x%d' % r, None)
            continue
        if g in (ca64.ARM64_INS_BLR, ca64.ARM64_INS_BR):
            t = tags.get(a64_canon(ins.reg_name(ops[0].reg)))
            ev = _virt(t, None)
            if g == ca64.ARM64_INS_BR and not ev:
                continue   # a jump table
            out.append(ev + (off,) if ev else ('I', None, off))
            if g == ca64.ARM64_INS_BLR:
                for r in range(0, 19):
                    tags.pop('x%d' % r, None)
            continue
        new = None
        rt = rtypes.get(off)
        if rt in (R_P32_ADR_GOT_PAGE, R_P32_ADR_PREL_PG_HI21) and g == ca64.ARM64_INS_ADRP:
            new = (a64_canon(ins.reg_name(ops[0].reg)), ('gp', relocs.get(off)))
        elif rt in (R_P32_LD32_GOT_LO12, R_P32_ADD_ABS_LO12) and len(ops) >= 2 and \
                g in (ca64.ARM64_INS_LDR, ca64.ARM64_INS_ADD):
            nm = relocs.get(off)
            a = resolve(nm) if nm else None
            new = (a64_canon(ins.reg_name(ops[0].reg)), ('ga', a if a is not None else nm))
        elif g == ca64.ARM64_INS_LDR and len(ops) == 2 and ops[1].type == capstone.CS_OP_MEM and \
                ins.reg_name(ops[0].reg).startswith('w') and not ops[1].mem.index:
            b = a64_canon(ins.reg_name(ops[1].mem.base))
            if b != 'sp' and not ins.writeback:
                new = (a64_canon(ins.reg_name(ops[0].reg)), _gload(tg(b), ops[1].mem.disp))
        elif g == ca64.ARM64_INS_MOV and len(ops) == 2 and ops[0].type == capstone.CS_OP_REG and \
                ops[1].type == capstone.CS_OP_REG:
            new = (a64_canon(ins.reg_name(ops[0].reg)), tags.get(a64_canon(ins.reg_name(ops[1].reg))))
        elif g == ca64.ARM64_INS_ORR and len(ops) == 3 and ops[1].type == capstone.CS_OP_REG and \
                ins.reg_name(ops[1].reg) in ('xzr', 'wzr') and ops[2].type == capstone.CS_OP_REG and not ops[2].shift.value:
            new = (a64_canon(ins.reg_name(ops[0].reg)), tags.get(a64_canon(ins.reg_name(ops[2].reg))))
        try:
            _, wr = ins.regs_access()
        except capstone.CsError:
            wr = []
        if g in (ca64.ARM64_INS_LDP, ca64.ARM64_INS_LDR) and ops and ops[-1].type == capstone.CS_OP_MEM and \
                a64_canon(ins.reg_name(ops[-1].mem.base)) == 'sp':
            wr = [r for r in wr if not a64_canon(ins.reg_name(r)) in CALLEE_SAVED]   # restores of the epilogue
        for r in wr:
            tags.pop(a64_canon(ins.reg_name(r)), None)
        if new and new[1] is not None:
            tags[new[0]] = new[1]
    return out


# ------------------------------------------------------------------------------------------------------ aligning

def _runs(vs):
    """Consecutive virtual calls with the same (vptr offset, slot offset) collapse into one run: [(key, [events])]."""
    out = []
    for e in vs:
        if out and out[-1][0] == (e[1], e[2]):
            out[-1][1].append(e)
        else:
            out.append(((e[1], e[2]), [e]))
    return out


def _consistent(pairs):
    """The map exe offset -> native offset the paired keys imply is a function and injective, vptr offsets equal."""
    fwd, back = {}, {}
    for (ex, nx) in pairs:
        if ex[0] != nx[0]:
            return 'vptr-offset %d/%d' % (ex[0], nx[0])
        if fwd.setdefault(ex, nx) != nx or back.setdefault(nx, ex) != ex:
            return 'offset-map'
    return None


def align(exe_ev, nat_ev):
    """Pair the virtual calls of one function. -> (status, [(exe event, native event)], note).
    status: 'aligned', 'no-vcalls' (neither side has one), or 'unaligned' (note: why).

    Rule 1 (runs): the virtual calls of each side in address order, consecutive calls with the same key
    (vptr offset, slot offset) collapsed into runs (GCC duplicates and unrolls what MSVC emits once); both sides must
    have equally many runs, the keys they pair must map exe offset -> native offset as a function and injectively,
    and the vptr offsets must agree; run i pairs with run i (the events of a run pair in order, extras of the longer
    side are dropped). Block order differs between the compilers (GCC sinks and hoists), so direct calls are no
    anchors for this rule: they move relative to the virtual calls in real code.
    Rule 2 (anchors), when the run counts differ: direct calls whose target both sides know and that occur on both
    sides are anchors; their sequences must be identical on both sides and the virtual calls between two
    consecutive anchors must be equally many and pair in order (vptr offsets equal, offset map consistent).
    Direct calls with an unknown target (inlined helpers, imports, runtime) and native noise (unwind, cxa) are
    ignored. A function neither rule aligns is skipped ('unaligned') and counted."""
    ev = list(exe_ev)
    nv = [e for e in nat_ev if not (e[0] == 'D' and e[2] and NOISE.match(e[2]))]
    ve = [e for e in ev if e[0] == 'V']
    vn = [e for e in nv if e[0] == 'V']
    if not ve and not vn:
        return 'no-vcalls', [], ''
    if not ve or not vn:
        return 'unaligned', [], 'vcalls-one-side %d/%d' % (len(ve), len(vn))
    re_, rn = _runs(ve), _runs(vn)
    if len(re_) == len(rn):
        why = _consistent([(a[0], b[0]) for a, b in zip(re_, rn)])
        if why:
            return 'unaligned', [], why
        pairs = []
        for (_, a), (_, b) in zip(re_, rn):
            for i, x in enumerate(a):
                pairs.append((x, b[min(i, len(b) - 1)]))
        return 'aligned', pairs, ''
    ea = {e[1] for e in ev if e[0] == 'D' and e[1] is not None}
    na = {e[1] for e in nv if e[0] == 'D' and e[1] is not None}
    common = ea & na
    eseq = [e[1] for e in ev if e[0] == 'D' and e[1] in common]
    nseq = [e[1] for e in nv if e[0] == 'D' and e[1] in common]
    if not common or eseq != nseq:
        return 'unaligned', [], 'run-count %d/%d' % (len(re_), len(rn))

    def gaps(events):
        out, cur = [], []
        for e in events:
            if e[0] == 'D' and e[1] in common:
                out.append(cur)
                cur = []
            elif e[0] == 'V':
                cur.append(e)
        out.append(cur)
        return out

    pairs = []
    for a, b in zip(gaps(ev), gaps(nv)):
        if len(a) != len(b):
            return 'unaligned', [], 'run-count %d/%d' % (len(re_), len(rn))
        pairs += list(zip(a, b))
    why = _consistent([((a[1], a[2]), (b[1], b[2])) for a, b in pairs])
    if why:
        return 'unaligned', [], why
    return 'aligned', pairs, 'anchors'


# ------------------------------------------------------------------------------------------------------- tables

def best_alignment(names, d, exe_slots, symaddr, is_dtor=None):
    """vtmetric.compare_slots' choice, but returning the winning destructor position c too:
    -> (c, [native address of slot k or None])."""
    addr = [vtmetric.slot_address(n, symaddr) for n in names]
    cands = [d] + [p for p in vtmetric._pair_positions(names, d) if p != d]
    cands += [p for p in vtmetric.renamed_pairs(names, exe_slots, is_dtor) if p not in cands]
    if d is not None:
        cands.append(None)
    best = None
    for c in cands:
        agree, bad = vtmetric._align(addr, c, exe_slots)
        if best is None or agree > best[0]:
            best = (agree, c)
    return best[1], addr


class Pair:
    """One native vtable paired with one exe vftable."""
    __slots__ = ('v', 'cls', 'z', 'primary', 'c', 'addr', 'exe', 'n', 'xcls')

    def __init__(self, v, cls, z, primary, c, addr, exe, xcls=None):
        self.v, self.cls, self.z, self.primary, self.c, self.addr, self.exe = v, cls, z, primary, c, addr, exe
        self.n = len(addr)
        self.xcls = xcls   # the exe RTTI class of the vftable, in the encoding of a class name (`9NiD3DPass`)


def xclass_enc(td):
    """'.?AVNiD3DPass@@' -> '9NiD3DPass' (simple names only: a namespaced or template class has no such encoding)."""
    m = re.match(r'^\.\?A[VU]([A-Za-z_][A-Za-z_0-9]*)@@$', td or '')
    return '%d%s' % (len(m.group(1)), m.group(1)) if m else None


def class_of_ztv(z):
    """'_ZTV19BGSDehydrationStage' -> '19BGSDehydrationStage'; a nested name keeps its `N..E`."""
    return z[4:] if z.startswith('_ZTV') else z


def method_class(sym):
    """The class encoding of a member function symbol (`_ZN13IniCollection12FUN_00c44280EP10IniSetting` ->
    '13IniCollection'; namespaced -> 'N3foo3BarE'), else None."""
    m = re.match(r'_ZN(?:[KVr]*)(.*)', sym)
    if not m:
        return None
    s, comps = m.group(1), []
    while s and s[0].isdigit():
        n = re.match(r'\d+', s).group(0)
        ln = int(n)
        comps.append(s[len(n):len(n) + ln])
        s = s[len(n) + ln:]
        if s[:1] in ('E', ''):
            break
    if len(comps) < 2:
        return None
    cls = comps[:-1]
    return '%d%s' % (len(cls[0]), cls[0]) if len(cls) == 1 else 'N' + ''.join('%d%s' % (len(c), c) for c in cls) + 'E'


def build_pairs(link, exe, cache, log=print):
    out = os.path.join(link, 'out')
    native, vrows = vtmetric.read_addrmap(os.path.join(out, 'addrmap.tsv'))
    renames = vtmetric.read_renames(os.path.join(out, 'refs.tsv'))
    symaddr = vtmetric.read_symaddr(os.path.join(out, 'addrmap.tsv'))
    exev = exevft.load(exe, cache)
    exev = dict(exev)
    exev.update(exevft.plain_vftables(exe, [v for v in vrows if v not in exev], set(exev) | set(vrows)))
    is_dtor = lambda a: native.get(a) == 'G' or exevft.deleting_dtor(exe, a)   # noqa: E731
    pairs, objs = [], {}
    for v, (z, ofile, note, ap) in sorted(vrows.items()):
        e = exev.get(v)
        if e is None:
            continue
        if ofile not in objs:
            try:
                objs[ofile] = vtables.Obj(os.path.join(link, 'fixed', ofile))
            except (OSError, ValueError):
                objs[ofile] = None
        o = objs[ofile]
        if o is None or z not in o.defs:
            continue
        ents = o.ents(z)
        n = vtables.slots_at(ents, ap)
        slot_ents = ents[ap // 4: ap // 4 + n]
        ren = renames.get(ofile, {})
        names = [ren.get(x.sym, x.sym) if x.sym else None for x in slot_ents]
        d = exevft.dtor_pos(names, [x.word for x in slot_ents])
        cur = [x.sym for x in slot_ents]
        c, addr = best_alignment(cur, d, e.slots, symaddr, is_dtor)
        pairs.append(Pair(v, class_of_ztv(z), z, note.startswith('primary'), c, addr, list(e.slots),
                          xclass_enc(e.td) if e.off == 0 else None))
    if len(objs) > 64:
        objs.clear()
    return pairs, native, symaddr, exev


def judge_pair(p, j, k):
    """'ok' / 'bad' / None for one pair and one (exe slot j, native slot k) site; also (native addr, exe addr)."""
    if j >= len(p.exe) or k >= p.n:
        return None, None, None
    na, ea = p.addr[k], p.exe[j]
    if na is not None and na == ea:
        return 'ok', na, ea
    if na is None:
        return ('ok' if exevft.exe_slot_index(k, p.c) == j else 'bad'), na, ea
    if j < 2 and exevft.exe_slot_index(k, p.c) == j:
        return 'ok', na, ea   # destructor slots: D1 / D0 against the deleting destructor
    return 'bad', na, ea


class Judge:
    def __init__(self, pairs, exe_classes=(), is_delete_this=None):
        self.pairs = pairs
        self.exe_classes = exe_classes
        self.is_delete_this = is_delete_this
        self.by_class = collections.defaultdict(list)
        self.by_slot = collections.defaultdict(list)   # exe function address -> pairs whose exe vftable holds it
        self.by_xclass = collections.defaultdict(list)   # exe RTTI class -> pairs (a shell / copy table stands for it)
        for p in pairs:
            self.by_class[p.cls].append(p)
            if p.xcls and p.xcls != p.cls:
                self.by_xclass[p.xcls].append(p)
            for a in set(p.exe):
                self.by_slot[a].append(p)
        self.cache = {}

    def holders(self, fn, classes=()):
        """The pairs that can have a `this`-based function as receiver: the pairs whose exe vftable holds it and the
        primary pairs of `classes` (the function's own class and its this-passing callers')."""
        out = list(self.by_slot.get(fn, []))
        for c in classes:
            out += [p for p in self.by_class.get(c, []) if p.primary]
        seen, res = set(), []
        for p in out:
            if id(p) not in seen:
                seen.add(id(p))
                res.append(p)
        return res

    def site(self, j, k, cls=None, fn=None):
        """-> (verdict, receiver text, native target, exe target, note). cls: the receiver's class encoding when
        known (this-based or inferred), else None: every pair is a candidate and the verdict is `ok` / `mismatch`
        only when all comparable candidates agree / disagree, else `unknown` (ambiguous receiver)."""
        key = (j, k, cls, tuple(id(p) for p in fn) if fn else None)
        if key in self.cache:
            return self.cache[key]
        res = self._site(j, k, cls, fn)
        self.cache[key] = res
        return res

    def _site(self, j, k, cls, fn=None):
        cand = []
        if fn:
            # a `this`-based site: every class that can be the function's receiver (holders, its class, its
            # this-passing callers' classes) is real, so one disagreeing class is a seam
            cand = fn
            cls = None
        elif cls:
            cand = [p for p in self.by_class.get(cls, []) if p.primary] or self.by_class.get(cls, [])
            if not cand:
                # the native table of an exe class can carry another name (a shell `FUN_00e7fea0` for NiD3DPass, a
                # `__fnvnx_vmo_<V>` copy for NiStream): the pair whose exe vftable has the class's RTTI name
                cand = [p for p in self.by_xclass.get(cls, []) if p.primary] or self.by_xclass.get(cls, [])
            if not cand:
                if cls_text(cls) in self.exe_classes:
                    # the class has an exe vftable and no native table: its objects keep the exe's order
                    v = 'ok' if k == j else 'mismatch'
                    return (v, cls_text(cls), None, None, 'class-without-native-table exe-order delta=%d' % (k - j))
                return ('unknown', cls_text(cls), None, None, 'class-not-in-exe')
        recv = cls_text(cls) if cls else 'any'
        member = bool(cand) and bool(fn) and not cls
        if not cand:
            cand = self.pairs
        ok = bad = 0
        okc, badc = collections.Counter(), collections.Counter()
        first_bad = first_ok = None
        dthis = False
        badv = []
        for p in cand:
            r, na, ea = judge_pair(p, j, k)
            if r == 'bad' and j == 1 and k == 1 and p.c == 0 and ea and self.is_delete_this and self.is_delete_this(ea):
                # `delete p` (GCC: the D0 at native slot 1) for the exe's `p->DeleteThis()` whose slot is the common
                # `delete this` body: the same call (a class that overrides DeleteThis has another body there)
                r = 'ok'
            if r == 'ok':
                ok += 1
                okc[p.cls] += 1
                first_ok = first_ok or (na, ea)
            elif r == 'bad':
                bad += 1
                badc[p.cls] += 1
                first_bad = first_bad or (na, ea)
                badv.append(p.v)
                if j == 1 and self.is_delete_this and ea and self.is_delete_this(ea):
                    dthis = True
        if ok + bad == 0:
            return ('unknown', recv, None, None, 'no-pair-has-slot')
        na, ea = (first_bad if bad else first_ok)
        top = ','.join(cls_text(c) for c, _ in (badc if bad else okc).most_common(3))
        note = 'agree=%d disagree=%d delta=%d %s' % (ok, bad, k - j, top)
        if bad:
            note += ' bad-vft=' + ','.join('%08x' % v for v in sorted(badv)[:4])
        if dthis:
            note += ' deletethis'
        if member:
            recv = ','.join(sorted({cls_text(p.cls) for p in cand}))   # every class: triage maps the row to its tables
            return ('mismatch' if bad else 'ok', recv, na, ea, note + ' by-receivers')
        if cls:
            return ('mismatch' if bad else 'ok', recv, na, ea, note)
        if not bad:
            return ('ok', recv, na, ea, note)
        if not ok:
            if abs(k - j) > 2:   # no class explains such a shift: the pairing of the two call sites is suspect
                return ('unknown', recv, na, ea, 'suspect-alignment ' + note)
            return ('mismatch', recv, na, ea, note)
        return ('unknown', recv, na, ea, 'ambiguous-receiver ' + note)


def site_set(j, k, pairs, order, label, is_delete_this=None):
    """A site judged against an inferred receiver: the candidate pairs (judge_pair) and the exe-order classes (an
    import's object, an exe class without a native table: native slot k must be exe slot j). `ok` / `mismatch` only
    when every comparable candidate agrees / disagrees, else `unknown`. -> (verdict, receiver, native target, exe
    target, note)."""
    ok = bad = 0
    okc, badc = collections.Counter(), collections.Counter()
    first_bad = first_ok = None
    badv = []
    for p in pairs:
        r, na, ea = judge_pair(p, j, k)
        if r == 'bad' and j == 1 and k == 1 and p.c == 0 and ea and is_delete_this and is_delete_this(ea):
            r = 'ok'   # `delete p` (the D0 at native slot 1) for the exe's common `delete this` body (Judge._site)
        if r == 'ok':
            ok += 1
            okc[p.cls] += 1
            first_ok = first_ok or (na, ea)
        elif r == 'bad':
            bad += 1
            badc[p.cls] += 1
            first_bad = first_bad or (na, ea)
            badv.append(p.v)
    for c in order:
        if k == j:
            ok += 1
            okc[c] += 1
        else:
            bad += 1
            badc[c] += 1
    names = sorted({cls_text(p.cls) for p in pairs} | set(order))
    recv = ','.join(names) if names else 'any'
    if ok + bad == 0:
        return ('unknown', recv, None, None, 'no-pair-has-slot by-' + label)
    na, ea = (first_bad if bad else first_ok) or (None, None)
    top = ','.join(cls_text(c) for c, _ in (badc if bad else okc).most_common(3))
    note = 'agree=%d disagree=%d delta=%d %s' % (ok, bad, k - j, top)
    if badv:
        note += ' bad-vft=' + ','.join('%08x' % v for v in sorted(badv)[:4])
    note += ' by-' + label
    if order and not pairs:
        note += ' exe-order'
    if not bad:
        return ('ok', recv, na, ea, note)
    if not ok:
        if abs(k - j) > 2:
            return ('unknown', recv, na, ea, 'suspect-alignment ' + note)
        return ('mismatch', recv, na, ea, note)
    return ('unknown', recv, na, ea, 'ambiguous-receiver ' + note)


def cls_text(c):
    return c if c == 'any' or not re.match(r'\d', c) else re.sub(r'^\d+', '', c)


# ------------------------------------------------------------------------------------------------ the VCALL check

class Ctx:
    pass


def load_addrmap_functions(path):
    """[(address, native symbol, link symbol, object file)] of the kinds with a native body, and all addresses."""
    funcs, addrs = [], []
    for line in open(path, encoding='utf-8', errors='surrogateescape'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 5:
            continue
        a = int(f[0], 16)
        addrs.append(a)
        if f[1] in NATIVE_KINDS and f[3] and f[4]:
            funcs.append((a, f[3], f[2], f[4]))
    return funcs, sorted(set(addrs))


def native_function(obj, names, cache, rtypes=None):
    """-> (code bytes, {offset: reloc symbol name}) of the first of `names` defined as a sized function in obj.
    rtypes (a dict, optional) receives {offset: relocation type}."""
    for nm in names:
        i = obj.defs.get(nm)
        if i is None:
            continue
        _, value, size, typ, bind, shndx = obj.syms[i]
        if size == 0 or obj.secs[shndx][1] != 1:
            continue
        sec = obj.secs[shndx]
        code = obj.data[sec[4] + value: sec[4] + value + size]
        rel = obj.rel(shndx)
        rels = {}
        for off, (si, typ_, add) in rel.items():
            if value <= off < value + size:
                s = obj.syms[si]
                rels[off - value] = s[0] if s[3] != 3 else '.section:' + obj.secnames[s[5]]
                if rtypes is not None:
                    rtypes[off - value] = typ_
        return code, rels
    return None


_G = {}


def _init_worker(link, exe, pairs, symaddr, addrs, partial, symof, exe_classes):
    from bodies import Pe
    _G['pe'] = Pe(exe)
    _G['link'] = link
    _G['judge'] = Judge(pairs, exe_classes, lambda a: exevft.delete_this(exe, a))
    _G['symof'] = symof
    _G['symaddr'] = symaddr
    _G['addrs'] = addrs
    _G['partial'] = partial
    _G['objs'] = {}


def _resolve(name):
    if name is None:
        return None
    a = _G['symaddr'].get(name)
    if a is not None:
        return a
    m = vtmetric.ADDR_NAME.match(name) or vtmetric.ADDR_STUB.match(name)
    return int(m.group(1), 16) if m else None


_BUILTIN = set('vwbcahstijlmxynofdegz')


def _source_name(s, i):
    """<source-name> at s[i] -> (text with its length prefix, next index) or (None, i)."""
    m = re.match(r'\d+', s[i:])
    if not m:
        return None, i
    n = int(m.group(0))
    j = i + len(m.group(0))
    return s[i:j + n], j + n


def param_types(sym):
    """The parameter types of an Itanium function symbol: [class encoding (for a pointer or reference to a class,
    cv-qualifiers dropped) or None (anything else)], with `this` not counted; None when the symbol does not parse
    (templates, special names: the parser is small on purpose). `_ZN3Foo3barEP3Bari` -> ['3Bar', None]."""
    if not sym or not sym.startswith('_Z'):
        return None
    s, i, subs = sym, 2, []
    if s[i:i + 1] == 'N':
        i += 1
        while s[i:i + 1] in ('K', 'V', 'r'):
            i += 1
        comps = []
        while i < len(s) and s[i] != 'E':
            if s[i] in 'CD' and s[i + 1:i + 2].isdigit():   # a constructor / destructor name
                i += 2
                comps.append(None)
                continue
            nm, j = _source_name(s, i)
            if nm is None:
                return None
            comps.append(nm)
            i = j
        i += 1
        pre = [c for c in comps[:-1]]
        for n in range(1, len(pre) + 1):
            subs.append(pre[0] if n == 1 else 'N' + ''.join(pre[:n]) + 'E')
    else:
        nm, i = _source_name(s, i)
        if nm is None:
            return None
    out = []
    if s[i:] == 'v':
        return []

    def typ(i):
        """-> (class encoding or None, is pointer-or-ref to class, next index) or None."""
        c = s[i:i + 1]
        if c in ('P', 'R', 'O'):
            r = typ(i + 1)
            if r is None:
                return None
            enc, _, j = r
            subs.append(('ptr', enc))
            return enc, True, j
        if c in ('K', 'V'):
            r = typ(i + 1)
            if r is None:
                return None
            subs.append(('cv', r[0]))
            return r[0], r[1], r[2]
        if c in _BUILTIN:
            return None, False, i + 1
        if c == 'S':
            m = re.match(r'S([0-9A-Z]*)_', s[i:])
            if not m:
                return None
            n = 0 if not m.group(1) else int(m.group(1), 36) + 1
            if n >= len(subs):
                return None
            x = subs[n]
            enc = x if isinstance(x, str) else x[1]
            return (enc if isinstance(x, str) else None), False, i + len(m.group(0))
        if c == 'N':
            j = i + 1
            comps = []
            while j < len(s) and s[j] != 'E':
                nm, k = _source_name(s, j)
                if nm is None:
                    return None
                comps.append(nm)
                j = k
            enc = 'N' + ''.join(comps) + 'E'
            subs.append(enc)
            return enc, False, j + 1
        if c.isdigit():
            nm, j = _source_name(s, i)
            subs.append(nm)
            return nm, False, j
        return None
    while i < len(s):
        r = typ(i)
        if r is None:
            return None
        enc, ptr, i = r
        out.append(enc if ptr else None)
    return out


def is_member(sym):
    """A member function symbol (`_ZN...`: a nested name; constructors and destructors included)."""
    return bool(sym) and sym.startswith('_ZN')


def msvc_td(cls):
    """Itanium class encoding -> the MSVC TypeDescriptor names it can have ('3Foo' -> .?AVFoo@@ / .?AUFoo@@)."""
    if cls.startswith('N') and cls.endswith('E'):
        comps, s = [], cls[1:-1]
        while s:
            nm, j = _source_name(s, 0)
            if nm is None:
                return []
            comps.append(re.sub(r'^\d+', '', nm))
            s = s[j:]
        name = '@'.join(reversed(comps))
    else:
        name = re.sub(r'^\d+', '', cls)
    return ['.?AV%s@@' % name, '.?AU%s@@' % name]


class Infer:
    """Receiver-class inference over the facts every function's x86_events collected (`info`), for the receivers
    that are not `this`. Evidence is a frozenset of atoms: ('dyn', V) the receiver is an object of the exe class whose
    vftable is V; ('st', C) its static type is class C (a C or a class derived from it); ('ext',) an object that came
    from outside the image (an import's result: a COM interface), whose vftable layout is the exe's. None: unknown.
    Every resolution is all-or-nothing: a location (a global, a field, a return value, an argument, a merged spill
    slot) has the union of the classes of every value the image stores there, and is unknown as soon as one value is.

    Sources (`enabled`, letters):
      a  the vptr store: a constructor (an addrmap C row, or a C1/C2 symbol) called with the object as this, or a
         `mov [obj], vftable` before the site (an inlined constructor) -> dyn
      b  globals, fields of this, return values, arguments of members (from the direct callers), merged spill slots,
         import results (ext), the member functions the same location is passed to as this (st)
      c  RTTI: a static type C is judged by the vftables whose hierarchy (the COL's) contains C, not by C's alone
      d  native side: the global a GOT relocation names; the C++ type of an argument from the mangled name"""

    def __init__(self, finfo, symof, kinds, exev, ctor_vft, judge, enabled='abcd', iat=None):
        self.fi, self.symof, self.kinds, self.exev = finfo, symof, kinds, exev
        self.ctor_vft = ctor_vft          # exe address -> the vftable its constructor body stores, or None
        self.judge = judge
        self.on = set(enabled)
        self.iat = iat or (0, 0)
        self.memo = {}
        self.stores = collections.defaultdict(list)    # ('g', A) / ('f', class, disp) -> [(function, tag, site)]
        self.objv = collections.defaultdict(list)      # A -> [(function, tag)] constructor calls / vptr stores on &A
        self.args = collections.defaultdict(list)      # (callee, n) -> [(function, tag)]
        self.callers = collections.Counter()
        self.static = collections.defaultdict(set)     # location key -> member classes it is passed to as this
        self.slot_fns = set(judge.by_slot) if judge else set()
        self.td_pairs = collections.defaultdict(list)  # TypeDescriptor -> pairs whose exe hierarchy holds it
        if judge:
            for p in judge.pairs:
                e = exev.get(p.v)
                for td, m in (e.bases if e else ()):
                    if m == e.off:
                        self.td_pairs[td].append(p)
        self.pair_by_v = {p.v: p for p in judge.pairs} if judge else {}
        for f, inf in finfo.items():
            fc = method_class(symof.get(f, '') or '')
            for key, tag, site in inf.get('stores', ()):
                if key[0] == 'f':
                    if not fc:
                        continue
                    key = ('f', fc, key[1])
                self.stores[key].append((f, tag, site))
            for tag, t, site in inf.get('thisof', ()):
                if tag and tag[0] == 'ga' and isinstance(tag[1], int):
                    self.objv[tag[1]].append((f, tag))
                lk = self.loc_key(f, tag, fc)
                c = method_class(symof.get(t, '') or '')
                if lk and c and not self.is_ctor(t):
                    self.static[lk].add(c)
            for tag, v, site in inf.get('vstore', ()):
                if tag and tag[0] == 'ga' and isinstance(tag[1], int):
                    self.objv[tag[1]].append((f, tag))
            for t, pushed, site in inf.get('calls', ()):
                self.callers[t] += 1
                pt = self.arity(t)
                if pt is None or len(pushed) < pt:
                    continue
                for n in range(pt):
                    self.args[(t, n + 1)].append((f, pushed[n], site))

    def arity(self, t):
        sym = self.symof.get(t)
        if not is_member(sym):
            return None   # stack arguments are counted for member functions (thiscall) only
        pt = param_types(sym)
        return None if pt is None else len(pt)

    def is_ctor(self, t):
        if self.kinds.get(t) == 'C':
            return True
        sym = self.symof.get(t) or ''
        return bool(re.search(r'C[12]E', sym))

    def loc_key(self, f, tag, fc=None):
        if not tag:
            return None
        if tag[0] == 'ld' and tag[1] and tag[1][0] == 'ga' and isinstance(tag[1][1], int) and tag[2] == 0:
            return ('g', tag[1][1])
        if tag[0] == 'ld' and tag[1] == ('this',):
            fc = fc or method_class(self.symof.get(f, '') or '')
            return ('f', fc, tag[2]) if fc else None
        return None

    # ---- resolution
    def resolve(self, f, tag, at=None, depth=0):
        """-> (evidence or None, frozenset of the source labels used)."""
        if tag is None or depth > 12:
            return None, frozenset()
        key = (f, tag, at)
        if key in self.memo:
            r = self.memo[key]
            return r if r is not None else (None, frozenset())
        self.memo[key] = None    # a cycle reads as unknown
        r = self._resolve(f, tag, at, depth)
        self.memo[key] = r
        return r

    def _local_dyn(self, f, tag, at):
        inf = self.fi.get(f, {})
        best = None
        for t, v, site in inf.get('vstore', ()):
            if t == tag and (at is None or site < at) and v in self.exev and self.exev[v].off == 0:
                if best is None or site > best[0]:
                    best = (site, v, 'vptr')
        for t, callee, site in inf.get('thisof', ()):
            if t == tag and (at is None or site <= at) and self.is_ctor(callee):
                v = self.ctor_vft(callee)
                if v is not None and (best is None or site > best[0]):
                    best = (site, v, 'ctor')
        return best

    def _resolve(self, f, tag, at, depth):
        k = tag[0]
        if 'a' in self.on:
            b = self._local_dyn(f, tag, at)
            if b:
                return frozenset([('dyn', b[1])]), frozenset([b[2]])
        if 'b' not in self.on:
            return self._static(f, tag)
        if k == 'ret':
            t = tag[1]
            if self.is_ctor(t):
                return self.resolve(f, self.fi.get(f, {}).get('retthis', {}).get(tag), tag[2], depth + 1)
            return self._all(('r', t), [(t, x, None) for x in self.fi.get(t, {}).get('rets', ())] if t in self.fi
                             else [], 'ret', depth, f, tag)
        if k == 'phi':
            vals = self.fi.get(f, {}).get('slots', {}).get(tag[1], [])
            return self._all(None, [(f, x, None) for x in vals], 'phi', depth, f, tag)
        if k == 'cr':
            x = tag[1]
            a = x[1][1] if x and x[0] == 'ld' and x[1][0] == 'ga' else None
            if isinstance(a, int) and (self.iat[0] <= a < self.iat[1] or self._ext_fnptr(a, depth)):
                return frozenset([('ext',)]), frozenset(['ext'])
            return None, frozenset()
        if k == 'ga' and isinstance(tag[1], int):
            if 'a' in self.on:
                for g, t in self.objv.get(tag[1], ()):
                    b = self._local_dyn(g, t, None)
                    if b:
                        return frozenset([('dyn', b[1])]), frozenset([b[2], 'gobj'])
            return None, frozenset()
        if k == 'arg':
            if f in self.slot_fns or not is_member(self.symof.get(f)) or not self.callers.get(f):
                return self._static(f, tag)
            return self._all(('a', f, tag[1]), self.args.get((f, tag[1]), []), 'arg', depth, f, tag)
        lk = self.loc_key(f, tag)
        if lk:
            return self._all(lk, self.stores.get(lk, []), 'global' if lk[0] == 'g' else 'field', depth, f, tag)
        return self._static(f, tag)

    def _all(self, key, vals, label, depth, f, tag):
        """The union of the evidence of every value; unknown when one value is (or there is none)."""
        ev, src = set(), {label}
        ok = bool(vals)
        for g, t, site in vals:
            if t and t[0] == 'imm' and t[1] == 0:
                continue   # a null store
            e, s = self.resolve(g, t, site, depth + 1)
            if e is None or any(a[0] == 'st' for a in e):
                ok = False
                break
            ev |= e
            src |= s
        if ok and ev:
            return frozenset(ev), frozenset(src)
        return self._static(f, tag, key)

    def _static(self, f, tag, key=None):
        """The member functions the receiver (or its location, across all functions) is passed to as this."""
        if 'b' not in self.on:
            return None, frozenset()
        got = set()
        for t, callee, site in self.fi.get(f, {}).get('thisof', ()):
            if t == tag and not self.is_ctor(callee):
                c = method_class(self.symof.get(callee, '') or '')
                if c:
                    got.add(c)
        lk = key or self.loc_key(f, tag)
        if lk and lk in self.static:
            got |= self.static[lk]
        if len(got) == 1:
            return frozenset([('st', got.pop())]), frozenset(['member'])
        return None, frozenset()

    def _ext_fnptr(self, a, depth):
        """Every value stored in the global at a is an import's result (GetProcAddress) or an import slot's word."""
        vals = self.stores.get(('g', a), [])
        if not vals:
            return False
        for g, t, site in vals:
            if not t:
                return False
            if t == ('imm', 0):
                continue
            if t[0] == 'cr' and t[1] and t[1][0] == 'ld' and t[1][1][0] == 'ga' and \
                    isinstance(t[1][1][1], int) and self.iat[0] <= t[1][1][1] < self.iat[1]:
                continue
            if t[0] == 'ld' and t[1][0] == 'ga' and isinstance(t[1][1], int) and self.iat[0] <= t[1][1] < self.iat[1]:
                continue
            return False
        return True

    def native(self, nsym, tag, nmem=None):
        """Native-side evidence (source d): a global the GOT names (its exe address) or a parameter's C++ type."""
        if 'd' not in self.on or tag is None:
            return None, frozenset()
        if tag[0] == 'ld' and tag[1] and tag[1][0] == 'ga' and isinstance(tag[1][1], int) and tag[2] == 0:
            e, s = self._all(('g', tag[1][1]), self.stores.get(('g', tag[1][1]), []), 'global', 0, None, tag) \
                if 'b' in self.on else (None, frozenset())
            return e, (s | {'got'}) if e else s
        if tag[0] == 'ga' and isinstance(tag[1], int) and 'a' in self.on:
            for g, t in self.objv.get(tag[1], ()):
                b = self._local_dyn(g, t, None)
                if b:
                    return frozenset([('dyn', b[1])]), frozenset([b[2], 'gobj', 'got'])
        member = method_class(nsym) is not None
        n = None
        if tag[0] == 'arg':
            n = tag[1] - 1 if member else tag[1]
        elif tag == ('this',) and not member:
            n = 0
        if n is not None:
            pt = param_types(nsym)
            if pt and n < len(pt) and pt[n]:
                return frozenset([('st', pt[n])]), frozenset(['argtype'])
        if nmem:   # the one class whose member the native function calls with the receiver as this
            return frozenset([('st', nmem)]), frozenset(['nmember'])
        return None, frozenset()

    # ---- candidates
    def candidates(self, ev):
        """-> (pairs, exe-order classes) to judge a site by, or None. exe-order: classes whose objects keep the exe's
        slot order (an import's object; an exe vftable with no native table)."""
        dyn = [a[1] for a in ev if a[0] == 'dyn']
        exto = ['COM' for a in ev if a[0] == 'ext']
        st = [a[1] for a in ev if a[0] == 'st']
        if dyn or exto:
            pairs, order = [], list(exto)
            for v in dyn:
                p = self.pair_by_v.get(v)
                if p:
                    pairs.append(p)
                else:
                    order.append(re.sub(r'^\.\?A[VU]|@@$', '', self.exev[v].td) if v in self.exev else '%08x' % v)
            return pairs, order
        if len(st) == 1:
            c = st[0]
            if 'c' in self.on:
                tds = set(msvc_td(c))
                for p in self.judge.by_class.get(c, []) + self.judge.by_xclass.get(c, []):
                    e = self.exev.get(p.v)
                    if e and p.primary and e.off == 0:
                        tds.add(e.td)
                cand = {id(p): p for td in tds for p in self.td_pairs.get(td, [])}
                if cand:
                    return list(cand.values()), []
                return None
            cand = [p for p in self.judge.by_class.get(c, []) if p.primary] or self.judge.by_class.get(c, []) or \
                [p for p in self.judge.by_xclass.get(c, []) if p.primary] or self.judge.by_xclass.get(c, [])
            return (cand, []) if cand else None
        return None


def check_function(task):
    """-> (status, sites, edges, info). sites: one tuple per matched virtual call (judged in the parent, which knows
    the this-passing call graph and every function's facts); edges: the exe addresses this function calls directly
    with its own `this`; info: the facts of x86_events for the receiver inference (Infer). A task with no native
    symbol (an exe-only function) gives its facts only."""
    import bisect
    addr, nsym, lsym, ofile = task
    addrs = _G['addrs']
    i = bisect.bisect_right(addrs, addr)
    end = addrs[i] if i < len(addrs) else None
    if nsym is None:
        insns = x86_disasm(_G['pe'].read, addr, end)
        info = {}
        if insns:
            x86_events(insns, addr, info=info)
        return 'exe-only', [], [], _slim(info)
    objs = _G['objs']
    if ofile not in objs:
        if len(objs) > 8:
            objs.clear()
        try:
            objs[ofile] = vtables.Obj(os.path.join(_G['link'], 'fixed', ofile))
        except (OSError, ValueError):
            objs[ofile] = None
    o = objs[ofile]
    if o is None:
        return 'no-object', [], [], None
    rtypes = {}
    nf = native_function(o, [nsym, lsym], None, rtypes)
    if nf is None:
        return 'no-native-body', [], [], None
    code, rels = nf
    nev = a64_events(code, rels, _resolve, rtypes)
    edges = [e[1] for e in nev if e[0] == 'D' and e[1] is not None and e[3] == ('this',)]
    insns = x86_disasm(_G['pe'].read, addr, end)
    if not insns:
        return 'no-exe-body', [], edges, None
    info = {}
    eev = x86_events(insns, addr, info=info)
    info = _slim(info)
    status, pairs, note = align(eev, nev)
    if status == 'no-vcalls':
        return 'no-vcalls', [], edges, info
    if status != 'aligned' and not (status == 'unaligned' and _G['partial'] and pairs):
        return 'unaligned:' + note.split(' ')[0], [], edges, info
    sites = []
    # a refcount release `if (!InterlockedDecrement(&o->rc)) o->DeleteThis()`: the slot-1 call right after an import call
    release = {e[-1] for n_, e in enumerate(eev) if n_ and e[0] == 'V' and e[2] == 4 and eev[n_ - 1][0] == 'I'
               and 0 < e[-1] - eev[n_ - 1][-1] <= 0x20}
    for ee, ne in pairs:
        this_based = ee[3] == 'this' and ne[3] == 'this' and ee[1] == 0 and ne[1] == 0
        etag = ntag = nmem = None
        if not this_based and ee[1] == 0 and ne[1] == 0:
            etag, ntag = ee[4], ne[4]
            got = {method_class(e[2]) for e in nev if e[0] == 'D' and e[3] == ntag and e[2] and
                   method_class(e[2]) and not re.search(r'C[12]E', e[2])}
            nmem = got.pop() if len(got) == 1 else None
        sites.append((nsym, addr, ee[-1], ne[-1], ee[2] // 4, ne[2] // 4, this_based, etag, ntag, nmem,
                      ee[-1] in release))
    return 'aligned', sites, edges, info


def _slim(info):
    """Drops the facts the inference never reads (untracked values) to keep the parent's copy small."""
    if not info:
        return None
    out = {}
    out['thisof'] = [x for x in info.get('thisof', ()) if x[0]]
    out['vstore'] = [x for x in info.get('vstore', ()) if x[0]]
    out['stores'] = info.get('stores', [])
    out['rets'] = info.get('rets', [])
    out['calls'] = [x for x in info.get('calls', ()) if x[1]]
    out['slots'] = info.get('slots', {})
    out['retthis'] = {k: v for k, v in info.get('retthis', {}).items() if v}
    return {k: v for k, v in out.items() if v}


def receivers(fn, rev, symof, judge, depth=3, cap=600):
    """Candidate pairs for a `this`-based function: the pairs holding it in an exe slot, its class's pairs, and the
    same for the functions that call it passing their own this (up to `depth` levels)."""
    seen, frontier, classes, cand = {fn}, [fn], set(), []
    for _ in range(depth):
        nxt = []
        for f in frontier:
            c = method_class(symof.get(f, ''))
            if c:
                classes.add(c)
            cand += judge.holders(f)
            for x in rev.get(f, ()):
                if x not in seen:
                    seen.add(x)
                    nxt.append(x)
        frontier = nxt
        if not frontier or len(cand) > cap:
            break
    return judge.holders(fn, classes) + [p for p in cand]


def iat_range(exe):
    """[lo, hi) of the image's import address table (data directory 12), as virtual addresses; (0, 0) if none."""
    d = open(exe, 'rb').read(0x1000)
    pe, = struct.unpack_from('<I', d, 0x3c)
    base, = struct.unpack_from('<I', d, pe + 24 + 28)
    rva, size = struct.unpack_from('<II', d, pe + 24 + 96 + 8 * 12)
    return (base + rva, base + rva + size) if rva else (0, 0)


def load_kinds(path):
    """{address: kind} of addrmap.tsv, and the exe-only functions (kinds E / G: no native body of their own) as
    facts-only tasks."""
    kinds, extra = {}, []
    for line in open(path, encoding='utf-8', errors='surrogateescape'):
        if line.startswith('#'):
            continue
        f = line.split('\t', 2)
        if len(f) < 2:
            continue
        a = int(f[0], 16)
        kinds[a] = f[1]
        if f[1] in ('E', 'G'):
            extra.append((a, None, None, None))
    return kinds, extra


FACTS_FORMAT = 'fnvnx-seamfacts/1'


def _facts_key(link, exe):
    st = [os.stat(p) for p in (os.path.join(link, 'out', 'addrmap.tsv'), exe, os.path.abspath(__file__))]
    return (FACTS_FORMAT,) + tuple((x.st_size, int(x.st_mtime)) for x in st)


def run_vcall(ctx):
    t0 = time.time()
    link, exe = ctx.link, ctx.exe
    pairs, native, symaddr, exev = build_pairs(link, exe, ctx.cache)
    ctx.log('vcall: %d native/exe vtable pairs (%.0fs)' % (len(pairs), time.time() - t0))
    funcs, addrs = load_addrmap_functions(os.path.join(link, 'out', 'addrmap.tsv'))
    kinds, exe_only = load_kinds(os.path.join(link, 'out', 'addrmap.tsv'))
    funcs_all = list(funcs)
    partial_run = bool(ctx.only or ctx.shard)
    if ctx.only:
        funcs = [f for f in funcs if f[0] in ctx.only]
    if ctx.shard:
        i, n = ctx.shard
        funcs = [f for f in funcs if (f[0] // 16) % n == i]
    funcs.sort(key=lambda f: (f[3], f[0]))   # group by object: each worker loads a file once
    stats = collections.Counter()
    stats['functions-with-both-bodies'] = len(funcs)
    symof = {f[0]: f[1] for f in funcs_all}
    exe_classes = {re.sub(r'^\.\?A[VU]|@@$', '', v.td) for v in exev.values() if v.td and v.off == 0}
    # the receiver facts of every exe function: computed on a full run, cached next to -o for --only / --shard runs
    fpath = os.path.join(os.path.dirname(ctx.cache), 'receiver_facts.pickle')
    fkey = _facts_key(link, exe)
    cached = None
    if partial_run and ctx.infer:
        try:
            with open(fpath, 'rb') as fh:
                k, cached = pickle.load(fh)
            if k != fkey:
                cached = None
        except (OSError, EOFError, pickle.PickleError, ValueError):
            cached = None
        if cached is None:
            ctx.log('vcall: no receiver facts cache (%s): facts of the selected functions only' % fpath)
    tasks = list(funcs) + ([] if partial_run else exe_only)
    initargs = (link, exe, pairs, symaddr, addrs, ctx.partial, symof, exe_classes)
    chunk = 16
    if ctx.jobs > 1:
        with multiprocessing.Pool(ctx.jobs, _init_worker, initargs) as pool:
            it = pool.imap(check_function, tasks, chunksize=chunk)
            res = list(_progress(it, len(tasks), ctx.log))
    else:
        _init_worker(*initargs)
        res = [check_function(f) for f in tasks]
    rev = collections.defaultdict(set)    # callee -> functions that call it with their own this
    sites = []
    finfo = dict(cached or {})
    for f, (status, ss, edges, info) in zip(tasks, res):
        if info:
            finfo[f[0]] = info
        if status == 'exe-only':
            stats['facts-exe-only-functions'] += 1
            continue
        stats['fn-' + status] += 1
        sites += ss
        for t in edges:
            if t != f[0]:
                rev[t].add(f[0])
    if not partial_run and ctx.infer:
        try:
            with open(fpath, 'wb') as fh:
                pickle.dump((fkey, finfo), fh, protocol=4)
        except OSError:
            pass
    judge = Judge(pairs, exe_classes, lambda a: exevft.delete_this(exe, a))
    starts = set(exev)
    cvc = {}

    def ctor_vft(a):
        if a not in cvc:
            cvc[a] = exevft.ctor_vftable(exe, a, starts)
        return cvc[a]
    infer = Infer(finfo, symof, kinds, exev, ctor_vft, judge, ctx.infer, iat_range(exe))
    ctx.log('vcall: receiver facts of %d functions (%.0fs)' % (len(finfo), time.time() - t0))
    rows = []
    rcache = {}
    def judge_one(site):
        nsym, addr, esite, nsite, j, k, this_based, etag, ntag, nmem, rel = site
        src = ()
        if this_based:
            if addr not in rcache:
                rcache[addr] = receivers(addr, rev, symof, judge)
            cand = rcache[addr] or None
            cls = method_class(nsym)
            v, recv, nt, et, why = judge.site(j, k, cls, cand)
            if cls:
                why += ' by-this'
        else:
            v, recv, nt, et, why, src = judge_inferred(infer, judge, nsym, addr, esite, j, k, etag, ntag, nmem)
            if rel and j == 1 and k == 1 and not src:
                # a release's DeleteThis with no inferred receiver: native `delete p` is the D0 when the receiver's
                # pair is at 0 / 1, but not for a family whose pair is later (TESForm: native 1 is a placeholder),
                # so without the receiver this stays unknown, not ok (honest verdicts)
                return ('unknown', 'any', None, None, 'release-delete-this'), ()
        return (v, recv, nt, et, why), src

    judged = [judge_one(x) for x in sites]
    stats['crossed-pairs-repaired'] += repair_crossed(sites, judged, judge_one)
    for (nsym, addr, esite, nsite, j, k, this_based, etag, ntag, nmem, rel), ((v, recv, nt, et, why), src) in \
            zip(sites, judged):
        for x in src:
            stats['infer-src-%s-%s' % (x, v)] += 1
        rows.append(('vcall', nsym, '%08x' % addr, '%08x' % esite, '%x' % nsite, recv, str(j), str(k),
                     '%08x' % et if et else '', '%08x' % nt if nt else '', v, why))
    verdicts = collections.Counter()
    by_class = collections.Counter()
    for r in rows:
        verdicts[r[10]] += 1
        if r[10] in ('mismatch', 'mixed'):
            by_class[(r[10], r[5])] += 1
    stats['functions-checked'] = stats['fn-aligned']
    stats['sites-matched'] = len(rows)
    for k, v in verdicts.items():
        stats['sites-' + k] = v
    stats['time-s'] = int(time.time() - t0)
    ctx.extra = [('top %s %s' % kv[0], kv[1]) for kv in by_class.most_common(40)]
    return rows, stats


def repair_crossed(sites, judged, judge_one):
    """Crossed pairs: two sites of one function whose call sites the native code lays out in the other order
    (GCC reorders neighbouring calls, switch arms, loop bodies), so pairing by address order within a segment
    crossed them: A's exe target is B's native target and back. They are re-paired (exe A with native B and back)
    when both re-judged sites then agree; sites / judged ([((verdict, receiver, native target, exe target, note),
    sources)]) are updated in place. judge_one(site) -> a judged entry. -> the number of pairs re-paired."""
    n = 0
    by_fn = collections.defaultdict(list)
    for i, x in enumerate(sites):
        by_fn[x[1]].append(i)
    for idx in by_fn.values():
        if len(idx) < 2:
            continue
        used = set()
        for a in idx:
            if a in used:
                continue
            ra = judged[a][0]
            if ra[0] == 'ok' or not ra[2] or not ra[3] or ra[2] == ra[3]:
                continue
            for b in idx:
                if b == a or b in used:
                    continue
                rb = judged[b][0]
                if rb[0] == 'ok' or ra[3] != rb[2] or rb[3] != ra[2]:
                    continue
                sa, sb = sites[a], sites[b]
                na = sa[:3] + (sb[3], sa[4], sb[5]) + sa[6:7] + (sa[7], sb[8], sb[9], sa[10])
                nb = sb[:3] + (sa[3], sb[4], sa[5]) + sb[6:7] + (sb[7], sa[8], sa[9], sb[10])
                if sa[6] != sb[6]:
                    break   # one this-based, one not: no re-pairing
                ja, jb = judge_one(na), judge_one(nb)
                if ja[0][0] == 'ok' and jb[0][0] == 'ok':
                    sites[a], sites[b] = na, nb
                    judged[a] = ((ja[0][:4] + (ja[0][4] + ' repaired-crossed',)), ja[1])
                    judged[b] = ((jb[0][:4] + (jb[0][4] + ' repaired-crossed',)), jb[1])
                    used |= {a, b}
                    n += 1
                break
    return n


def _strong(e):
    return bool(e) and any(a[0] in ('dyn', 'ext') for a in e)


def judge_inferred(infer, judge, nsym, addr, esite, j, k, etag, ntag, nmem):
    """A site whose receiver is not `this`: the exe side's evidence (Infer.resolve), else the native side's
    (Infer.native); exact evidence (dyn / ext) from both sides must agree. -> (verdict, receiver, native target, exe
    target, note, source labels)."""
    if etag is None and ntag is None:
        v, recv, nt, et, why = judge.site(j, k, None, None)
        return v, recv, nt, et, why, ()
    ee, es = infer.resolve(addr, etag, esite)
    ne, ns = infer.native(nsym, ntag, nmem)
    if os.environ.get('SEAMCHECK_DEBUG'):
        print('site %08x %x: exe %s -> %s %s | native %s -> %s %s' % (addr, esite, etag, ee, sorted(es), ntag, ne,
                                                                     sorted(ns)), file=sys.stderr)
    if _strong(ee) and _strong(ne) and ee != ne:
        v, recv, nt, et, why = judge.site(j, k, None, None)
        return 'unknown', recv, nt, et, 'receiver-conflict ' + why, ()
    if _strong(ee):
        ev, src = ee, es
    elif _strong(ne):
        ev, src = ne, ns
    elif ee:
        ev, src = ee, es
    else:
        ev, src = ne, ns
    if not ev:
        v, recv, nt, et, why = judge.site(j, k, None, None)
        return v, recv, nt, et, why, ()
    label = '+'.join(sorted(src))
    cands = infer.candidates(ev)
    if cands is None:
        st = [a[1] for a in ev if a[0] == 'st']
        if len(st) == 1:   # a static type with no RTTI hierarchy: its own table (or the exe order), as before
            v, recv, nt, et, why = judge.site(j, k, st[0], None)
            return v, recv, nt, et, why + ' by-' + label, src
        v, recv, nt, et, why = judge.site(j, k, None, None)
        return v, recv, nt, et, why, ()
    if 'c' in infer.on and any(a[0] == 'st' for a in ev):
        src = frozenset(src) | {'rtti'}
        label = '+'.join(sorted(src))
    v, recv, nt, et, why = site_set(j, k, cands[0], cands[1], label, judge.is_delete_this)
    return v, recv, nt, et, why, src


def _progress(it, n, log):
    for i, x in enumerate(it):
        if i and i % 5000 == 0:
            log('vcall: %d/%d' % (i, n))
        yield x


def run_layout(ctx):
    """LAYOUT (W40, seamlayout.py): native object sizes against the exe's evidence, one row per (class, evidence) in
    the common columns: function = class, exe_function = evidence kind (new / static / maxoff), exe_site = an example,
    exe_slot / native_slot = exe / native size, exe_target = site count, native_target = where the native size came
    from, note = boot flag and seamlayout's note."""
    try:
        import seamlayout
    except ImportError:
        ctx.log('layout: tools/link/seamlayout.py not present; skipped')
        return [], collections.Counter({'layout-module-missing': 1})
    got = seamlayout.check_layout(ctx.link, ctx.exe, tree=getattr(ctx, 'tree', None), jobs=ctx.jobs,
                                  topdown=getattr(ctx, 'boot', None),
                                  shard=ctx.shard, progress=ctx.log)
    col = {c: i for i, c in enumerate(seamlayout.COLUMNS)}
    rows, stats = [], collections.Counter()
    for r in got:
        g = lambda c: r[col[c]] if c in col and col[c] < len(r) else ''
        rows.append(('layout', g('class'), g('evidence'), g('example'), '', g('class'), g('exe_size'),
                     g('native_size'), g('sites'), g('native_src'), g('verdict'),
                     ('boot=%s %s' % (g('boot'), g('note'))).strip()))
        stats['layout-%s-%s' % (g('evidence'), g('verdict'))] += 1
        if g('boot') == '1' and g('verdict') == 'mismatch':
            stats['layout-boot-mismatch'] += 1
    return rows, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('link')
    ap.add_argument('exe')
    ap.add_argument('--vcall', action='store_true')
    ap.add_argument('--layout', action='store_true')
    ap.add_argument('--shard', help='i/n: only functions whose address / 16 mod n is i')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    ap.add_argument('--partial', action='store_true', help='also use the aligned part of a partly aligned function')
    ap.add_argument('--only', help='comma separated hex function addresses')
    ap.add_argument('--infer', default='abcd', help='receiver inference sources (letters of Infer; empty: none)')
    ap.add_argument('--tree', help='prepared source tree for the LAYOUT probe (seamlayout.py)')
    ap.add_argument('--boot', help="boot-path functions for LAYOUT's boot column: R2's topdown.tsv, or a plain address "
                                   "list (S18's trace-coc called set, one `<addr> [name]` per line)")
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args(argv)
    if not (a.vcall or a.layout):
        a.vcall = True
    ctx = Ctx()
    ctx.link, ctx.exe, ctx.jobs, ctx.partial, ctx.tree, ctx.boot = a.link, a.exe, a.jobs, a.partial, a.tree, a.boot
    ctx.shard = tuple(int(x) for x in a.shard.split('/')) if a.shard else None
    ctx.only = {int(x, 16) for x in a.only.split(',')} if a.only else None
    ctx.infer = a.infer
    ctx.cache = os.path.join(os.path.dirname(os.path.abspath(a.out)), 'exe_vftables.json')
    ctx.log = lambda s: print(s, file=sys.stderr, flush=True)
    ctx.extra = []
    rows, stats = [], collections.Counter()
    for flag, fn in ((a.vcall, run_vcall), (a.layout, run_layout)):
        if flag:
            r, s = fn(ctx)
            rows += r
            stats.update(s)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, 'w') as f:
        f.write('# fnvnx-seams 1\n# ' + '\t'.join(HEADER) + '\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    with open(a.out + '.stats.txt', 'w') as f:
        for k in sorted(stats):
            f.write('%s\t%s\n' % (k, stats[k]))
        for k, v in ctx.extra:
            f.write('%s\t%s\n' % (k, v))
    for k in sorted(stats):
        print('%s\t%s' % (k, stats[k]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
