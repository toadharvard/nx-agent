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
       function of class C) are checked against the V pair of C (primary vtable); every other site is checked against
       all native/exe vtable pairs (addrmap V rows, the exe vftables of exevft; the slot alignment is the one
       vtmetric.compare_slots picks) that have both slots, and the verdict says how many agree;
    5. per candidate pair: exe slot j = off_x86/4, native slot k = off_aarch64/4; the pair agrees when the native
       table's slot k reaches the native counterpart of the function the exe vftable's slot j reaches (or, for
       destructor slots and trap slots with no address, when the pair's own k -> j mapping, the minus-one rule,
       maps k to j); it disagrees otherwise.
  Verdicts: `ok` (all comparable candidates agree), `mismatch` (all comparable candidates disagree), `mixed` (some of
  each: depends on the receiver's class), `unknown` (no pair has both slots, or the vptr offsets differ). A
  mismatch or mixed row also says `delta` = k - j.

* `--layout` - the LAYOUT check lives in `seamcheck_layout.py` (another worker); the module exposes
  `run_layout(ctx) -> (rows, stats)` and is imported when the flag is given.

Rows (tab separated, `#` header): check, function (native symbol), exe function, exe site, native site (offset in
the function, hex), receiver (class, or `any`), exe slot, native slot, exe target, native target, verdict, note.
"""
import argparse
import collections
import multiprocessing
import os
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


def x86_events(insns, start, end_hint=None):
    """-> [event] of an exe function's calls, in address order."""
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    tags = {'ecx': ('this',)}
    stack = {8: ('arg', 1), 12: ('arg', 2), 16: ('arg', 3)}   # [ebp + disp] of an ebp frame: spilled values
    uid = [0]

    def tg(r):
        if r not in tags:
            uid[0] += 1
            tags[r] = ('u', uid[0])
        return tags[r]
    out = []
    lo = start
    hi = max([i.address + i.size for i in insns] or [start])
    for ins in insns:
        g = ins.id
        ops = ins.operands
        new = None   # (reg, tag) to set after clearing the written registers
        if g == cx86.X86_INS_CALL or g == cx86.X86_INS_JMP:
            op = ops[0] if ops else None
            if op is None:
                continue
            if g == cx86.X86_INS_JMP and op.type == capstone.CS_OP_IMM and lo <= op.imm < hi:
                continue
            if g == cx86.X86_INS_JMP and op.type == capstone.CS_OP_MEM and op.mem.index:
                continue   # a switch
            if op.type == capstone.CS_OP_IMM:
                out.append(('D', op.imm, None, tags.get('ecx'), ins.address))
            elif op.type == capstone.CS_OP_REG:
                t = tags.get(_x86_reg(md, op.reg))
                ev = _virt(t, None)
                out.append(ev + (ins.address,) if ev else ('I', None, ins.address))
            elif op.type == capstone.CS_OP_MEM:
                m = op.mem
                if m.index or not m.base:
                    out.append(('I', None, ins.address))
                else:
                    t = tg(_x86_reg(md, m.base))
                    ev = _virt(t, m.disp)
                    out.append(ev + (ins.address,) if ev else ('I', None, ins.address))
            if g == cx86.X86_INS_CALL:
                for r in X86_CLOBBER:
                    tags.pop(r, None)
            continue
        # tag propagation: only `mov r, r2`, `mov r, [base+disp]`; every other write clears the register
        if g == cx86.X86_INS_MOV and len(ops) == 2 and ops[0].type == capstone.CS_OP_MEM and \
                ops[1].type == capstone.CS_OP_REG and ops[0].mem.base and not ops[0].mem.index and \
                _x86_reg(md, ops[0].mem.base) == 'ebp' and ops[0].size == 4:
            stack[ops[0].mem.disp] = tg(_x86_reg(md, ops[1].reg))   # a spill of a register
            continue
        if g == cx86.X86_INS_MOV and len(ops) == 2 and ops[0].type == capstone.CS_OP_REG and ops[0].size == 4:
            d = _x86_reg(md, ops[0].reg)
            s = ops[1]
            if s.type == capstone.CS_OP_REG:
                new = (d, tg(_x86_reg(md, s.reg)))
            elif s.type == capstone.CS_OP_MEM and s.mem.base and not s.mem.index and s.mem.segment == 0:
                b = _x86_reg(md, s.mem.base)
                if b == 'ebp':
                    if s.mem.disp not in stack:
                        uid[0] += 1
                        stack[s.mem.disp] = ('u', uid[0])
                    new = (d, stack[s.mem.disp])   # a reload of a spilled value
                elif b != 'esp':
                    new = (d, ('ld', tg(b), s.mem.disp))
                else:
                    new = (d, None)
            else:
                new = (d, None)
        try:
            _, wr = ins.regs_access()
        except capstone.CsError:
            wr = []
        if g == cx86.X86_INS_POP:   # an epilogue restore (linear order puts it before later blocks)
            wr = []
        for r in wr:
            tags.pop(_x86_reg(md, r), None)
        if g in (cx86.X86_INS_PUSH, cx86.X86_INS_POP):
            pass
        if new and new[1] is not None:
            tags[new[0]] = new[1]
    return out


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


def a64_events(code, relocs, resolve):
    """-> [event] of a native function's calls (code: bytes of the function; relocs: {offset: symbol name} of the
    relocations of its section; resolve(name) -> exe address or None)."""
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
        if g == ca64.ARM64_INS_LDR and len(ops) == 2 and ops[1].type == capstone.CS_OP_MEM and \
                ins.reg_name(ops[0].reg).startswith('w') and not ops[1].mem.index:
            b = a64_canon(ins.reg_name(ops[1].mem.base))
            if b != 'sp' and not ins.writeback:
                new = (a64_canon(ins.reg_name(ops[0].reg)), ('ld', tg(b), ops[1].mem.disp))
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
            recv = ','.join(sorted({cls_text(p.cls) for p in cand})[:3]) + ('...' if len({p.cls for p in cand}) > 3 else '')
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


def native_function(obj, names, cache):
    """-> (code bytes, {offset: reloc symbol name}) of the first of `names` defined as a sized function in obj."""
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


def infer_class(expr, events, symof):
    """The class of a receiver expression: the one class whose member function the same function calls directly
    with the same expression as `this` (the address -> native symbol map names the member), else None."""
    if expr is None or expr[0] in ('this',):
        return None
    got = set()
    for e in events:
        if e[0] == 'D' and e[3] == expr and e[1] in symof:
            c = method_class(symof[e[1]])
            if c:
                got.add(c)
    return got.pop() if len(got) == 1 else None


def check_function(task):
    """-> (status, sites, edges). sites: one tuple per matched virtual call (judged in the parent, which knows the
    this-passing call graph); edges: the exe addresses this function calls directly with its own `this`."""
    import bisect
    addr, nsym, lsym, ofile = task
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
        return 'no-object', [], []
    nf = native_function(o, [nsym, lsym], None)
    if nf is None:
        return 'no-native-body', [], []
    code, rels = nf
    nev = a64_events(code, rels, _resolve)
    edges = [e[1] for e in nev if e[0] == 'D' and e[1] is not None and e[3] == ('this',)]
    addrs = _G['addrs']
    i = bisect.bisect_right(addrs, addr)
    end = addrs[i] if i < len(addrs) else None
    insns = x86_disasm(_G['pe'].read, addr, end)
    if not insns:
        return 'no-exe-body', [], edges
    eev = x86_events(insns, addr)
    status, pairs, note = align(eev, nev)
    if status == 'no-vcalls':
        return 'no-vcalls', [], edges
    if status != 'aligned' and not (status == 'unaligned' and _G['partial'] and pairs):
        return 'unaligned:' + note.split(' ')[0], [], edges
    symof = _G['symof']
    sites = []
    # a refcount release `if (!InterlockedDecrement(&o->rc)) o->DeleteThis()`: the slot-1 call right after an import call
    release = {e[-1] for n_, e in enumerate(eev) if n_ and e[0] == 'V' and e[2] == 4 and eev[n_ - 1][0] == 'I'
               and 0 < e[-1] - eev[n_ - 1][-1] <= 0x20}
    for ee, ne in pairs:
        this_based = ee[3] == 'this' and ne[3] == 'this' and ee[1] == 0 and ne[1] == 0
        cls = None
        if not this_based and ee[1] == 0 and ne[1] == 0:
            cn = infer_class(ne[4], nev, symof)
            ce = infer_class(ee[4], eev, symof)
            cls = cn if cn and (ce is None or ce == cn) else ce if ce and cn is None else None
        sites.append((nsym, addr, ee[-1], ne[-1], ee[2] // 4, ne[2] // 4, this_based, cls, ee[-1] in release))
    return 'aligned', sites, edges


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


def run_vcall(ctx):
    t0 = time.time()
    link, exe = ctx.link, ctx.exe
    pairs, native, symaddr, exev = build_pairs(link, exe, ctx.cache)
    ctx.log('vcall: %d native/exe vtable pairs (%.0fs)' % (len(pairs), time.time() - t0))
    funcs, addrs = load_addrmap_functions(os.path.join(link, 'out', 'addrmap.tsv'))
    funcs_all = list(funcs)
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
    initargs = (link, exe, pairs, symaddr, addrs, ctx.partial, symof, exe_classes)
    chunk = 16
    if ctx.jobs > 1:
        with multiprocessing.Pool(ctx.jobs, _init_worker, initargs) as pool:
            it = pool.imap(check_function, funcs, chunksize=chunk)
            res = list(_progress(it, len(funcs), ctx.log))
    else:
        _init_worker(*initargs)
        res = [check_function(f) for f in funcs]
    rev = collections.defaultdict(set)    # callee -> functions that call it with their own this
    sites = []
    fnaddr = {f[1]: f[0] for f in funcs}
    for f, (status, ss, edges) in zip(funcs, res):
        stats['fn-' + status] += 1
        sites += ss
        for t in edges:
            if t != f[0]:
                rev[t].add(f[0])
    judge = Judge(pairs, exe_classes, lambda a: exevft.delete_this(exe, a))
    rows = []
    rcache = {}
    for (nsym, addr, esite, nsite, j, k, this_based, cls, rel) in sites:
        how = 'inferred'
        cand = None
        if this_based:
            how = 'this'
            if addr not in rcache:
                rcache[addr] = receivers(addr, rev, symof, judge)
            cand = rcache[addr] or None
            cls = method_class(nsym)
        if rel and not this_based and j == 1 and k == 1:
            # the inferred receiver is a guess; the exe call is a release's DeleteThis, native `delete p` is the D0
            rows.append(('vcall', nsym, '%08x' % addr, '%08x' % esite, '%x' % nsite, 'any', str(j), str(k), '', '',
                         'ok', 'release-delete-this'))
            continue
        v, recv, nt, et, why = judge.site(j, k, cls, cand)
        if cls:
            why += ' by-' + how
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


def _progress(it, n, log):
    for i, x in enumerate(it):
        if i and i % 5000 == 0:
            log('vcall: %d/%d' % (i, n))
        yield x


def run_layout(ctx):
    try:
        import seamcheck_layout
    except ImportError:
        ctx.log('layout: tools/link/seamcheck_layout.py not present; skipped')
        return [], collections.Counter({'layout-module-missing': 1})
    return seamcheck_layout.run_layout(ctx)


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
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args(argv)
    if not (a.vcall or a.layout):
        a.vcall = True
    ctx = Ctx()
    ctx.link, ctx.exe, ctx.jobs, ctx.partial = a.link, a.exe, a.jobs, a.partial
    ctx.shard = tuple(int(x) for x in a.shard.split('/')) if a.shard else None
    ctx.only = {int(x, 16) for x in a.only.split(',')} if a.only else None
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
