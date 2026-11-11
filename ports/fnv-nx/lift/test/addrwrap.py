#!/usr/bin/env python3
"""Gate: no memory access of lifted code folds a constant into a 64-bit addressing mode (x86 addresses wrap at 2^32).

RULE, stated on decoded AArch64 operands (capstone, detail on; pyelftools for sections, symbols and relocations; no text of a disassembly is read):
  for every function of the probe object and every memory operand of every instruction in it whose base is not the function's own frame (sp, or x29 while every
  write of x29 on the way is `mov/add/sub x29, <frame register> [, #imm]`):
    (a) displacement 0, (b) no index register, (c) no pre/post-index writeback, (d) no relocation on the instruction (a symbol offset is a folded constant),
    (e) the base register is W32-CLEAN on every path from the function entry to the instruction: its last write on that path was a write of its W view (which
        zero-extends to 64 bits, i.e. the 32-bit operation is the wrap point), with no later X-view write.
  Dataflow (forward, over the basic blocks of the function): state = per register one of CLEAN / FRAME / DIRTY; entry: sp FRAME, every other register DIRTY (the
  arguments of an ILP32 call carry undefined upper halves); transfer by the registers each instruction writes (capstone regs_access, W view -> CLEAN, X view -> DIRTY;
  the compare/test ids write only the flags); a call (group CALL) makes x0..x18 and x30 DIRTY; join = meet (equal -> that value, else DIRTY).
  Every load/store instruction (by the A64 top-level encoding, bits 27 and 25 = 1, 0) must carry a capstone memory operand, a branch that is not a call, a return or a
  relative branch to a block of the function must end in a relocation (tail call) -- anything the analysis cannot classify is BAD, never passed.
PROBES: every accessor path of rt/fnvl_mem.h in both classes (FNVL_R9 = 0 plain scope, 1 ordered scope), compiled with the flags of test/instest.py through the
  lifter's own prelude: M8/M16/M32/M64/MF32/MF64/MX loads and stores (ordered: ldar/stlr when aligned, ldr;dmb ishld / dmb ishst;str when not, 16 byte: dmb form),
  fnvl_rmw (add sub and or xor xchg), fnvl_cas, fnvl_rmwf at widths 8..64, PROVEN and not (ldaxr/stlxr loop; not proven also the split-lock path with the world stop
  / resume calls), constant offsets 0x40 0xfffffffc 0x1183000 0, with and without an index term. Each probe must also show the instructions of its path (coverage).
NEGATIVE CONTROLS: hand-written functions that DO fold a constant, add an index, write back, or use a 64-bit value as the address; all must be BAD.
    addrwrap.py [WORKDIR]  (WORKDIR: declared input, default /home/user/l3work/addrwrap)
    prints `addrwrap: probes N bad B (negative controls K of K detected)`; exit 0 iff B == 0 and every control is detected."""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
sys.path.insert(0, LIFT); sys.path.insert(0, HERE)
import instest as T
import fnvlift as L
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_GRP_CALL, CS_GRP_JUMP, CS_GRP_RET, CS_GRP_BRANCH_RELATIVE
from capstone import arm64 as A
from elftools.elf.elffile import ELFFile

# ---- register model: x0..x30 = 0..30, sp = 31 ----
DIRTY, CLEAN, FRAME = 0, 1, 2
SPI = 31
X = {r: n for n, r in enumerate((A.ARM64_REG_X0, A.ARM64_REG_X1, A.ARM64_REG_X2, A.ARM64_REG_X3, A.ARM64_REG_X4, A.ARM64_REG_X5, A.ARM64_REG_X6, A.ARM64_REG_X7, A.ARM64_REG_X8, A.ARM64_REG_X9, A.ARM64_REG_X10, A.ARM64_REG_X11, A.ARM64_REG_X12, A.ARM64_REG_X13, A.ARM64_REG_X14, A.ARM64_REG_X15, A.ARM64_REG_X16, A.ARM64_REG_X17, A.ARM64_REG_X18, A.ARM64_REG_X19, A.ARM64_REG_X20, A.ARM64_REG_X21, A.ARM64_REG_X22, A.ARM64_REG_X23, A.ARM64_REG_X24, A.ARM64_REG_X25, A.ARM64_REG_X26, A.ARM64_REG_X27, A.ARM64_REG_X28))}
W = {r: n for n, r in enumerate((A.ARM64_REG_W0, A.ARM64_REG_W1, A.ARM64_REG_W2, A.ARM64_REG_W3, A.ARM64_REG_W4, A.ARM64_REG_W5, A.ARM64_REG_W6, A.ARM64_REG_W7, A.ARM64_REG_W8, A.ARM64_REG_W9, A.ARM64_REG_W10, A.ARM64_REG_W11, A.ARM64_REG_W12, A.ARM64_REG_W13, A.ARM64_REG_W14, A.ARM64_REG_W15, A.ARM64_REG_W16, A.ARM64_REG_W17, A.ARM64_REG_W18, A.ARM64_REG_W19, A.ARM64_REG_W20, A.ARM64_REG_W21, A.ARM64_REG_W22, A.ARM64_REG_W23, A.ARM64_REG_W24, A.ARM64_REG_W25, A.ARM64_REG_W26, A.ARM64_REG_W27, A.ARM64_REG_W28))}
X[A.ARM64_REG_FP] = 29; X[A.ARM64_REG_LR] = 30; X[A.ARM64_REG_SP] = SPI
W[A.ARM64_REG_W29] = 29; W[A.ARM64_REG_W30] = 30; W[A.ARM64_REG_WSP] = SPI
FLAGS_ONLY = frozenset((A.ARM64_INS_TST, A.ARM64_INS_CMP, A.ARM64_INS_CMN, A.ARM64_INS_CCMP, A.ARM64_INS_CCMN))   # write NZCV only (capstone lists the first source as written)
FRAME_OPS = frozenset((A.ARM64_INS_ADD, A.ARM64_INS_SUB, A.ARM64_INS_MOV))
UNCOND = frozenset((A.ARM64_CC_INVALID, A.ARM64_CC_AL))
ACQREL = frozenset((A.ARM64_INS_LDAR, A.ARM64_INS_LDARB, A.ARM64_INS_LDARH, A.ARM64_INS_STLR, A.ARM64_INS_STLRB, A.ARM64_INS_STLRH))
EXCL = frozenset((A.ARM64_INS_LDAXR, A.ARM64_INS_LDAXRB, A.ARM64_INS_LDAXRH, A.ARM64_INS_STLXR, A.ARM64_INS_STLXRB, A.ARM64_INS_STLXRH, A.ARM64_INS_LDAXP, A.ARM64_INS_STLXP))
DMB = A.ARM64_INS_DMB


def meet(a, b):
    return tuple(x if x == y else DIRTY for x, y in zip(a, b))


def is_loadstore(raw):
    """A64 top-level encoding: bits 28..25 = x1x0 is the loads-and-stores group (all of them: literal, exclusive, atomic, SIMD, prefetch)"""
    w = int.from_bytes(raw[:4], 'little')
    return (w >> 27) & 1 == 1 and (w >> 25) & 1 == 0


class Func:
    def __init__(s, name, base, insns, relocs):
        s.name, s.base, s.insns, s.relocs = name, base, insns, relocs          # insns: list of capstone instructions in address order
        s.end = insns[-1].address + insns[-1].size if insns else base


def transfer(ins, st):
    """the state after instruction ins (list of 32 -> new list)"""
    st = list(st)
    r, w = ins.regs_access()
    wr = [] if ins.id in FLAGS_ONLY else list(w)
    mem = [o for o in ins.operands if o.type == A.ARM64_OP_MEM]
    for rid in wr:
        if rid in W:
            st[W[rid]] = CLEAN
        elif rid in X:
            n = X[rid]
            o = ins.operands
            if mem and ins.writeback and X.get(mem[0].mem.base) == n and st[n] == FRAME:
                continue                                                       # frame pointer bumped by an immediate
            fr = (ins.id in FRAME_OPS and len(o) in (2, 3) and o[0].type == A.ARM64_OP_REG and X.get(o[0].reg) == n and o[1].type == A.ARM64_OP_REG
                  and X.get(o[1].reg) is not None and st[X[o[1].reg]] == FRAME and (len(o) == 2 or o[2].type == A.ARM64_OP_IMM))
            st[n] = FRAME if fr else DIRTY
    if CS_GRP_CALL in ins.groups:
        for n in list(range(19)) + [30]:
            st[n] = DIRTY
    return st


def analyse(f):
    """-> (list of problems, set of instruction ids seen, number of guest (non-frame) memory operands)"""
    bad = []
    byaddr = {i.address: i for i in f.insns}
    leaders = {f.base}
    succ_of = {}
    for i in f.insns:
        nxt = i.address + i.size
        j = CS_GRP_JUMP in i.groups
        if CS_GRP_RET in i.groups:
            succ_of[i.address] = []
        elif CS_GRP_CALL in i.groups:
            succ_of[i.address] = [nxt]                                          # a call returns; its effect is in transfer()
        elif j:
            if CS_GRP_BRANCH_RELATIVE not in i.groups:
                bad.append('%x: indirect jump' % i.address); succ_of[i.address] = []
                leaders.add(nxt)
                continue
            tgt = i.operands[-1].imm if i.operands and i.operands[-1].type == A.ARM64_OP_IMM else None
            if i.address in f.relocs:
                s = []                                                         # relocated: transfer to another symbol (tail call), not to a block here
            elif tgt is not None and tgt in byaddr:
                s = [tgt]; leaders.add(tgt)
            else:
                bad.append('%x: branch leaves the function without a relocation' % i.address); s = []
            if not (i.id == A.ARM64_INS_B and i.cc in UNCOND):
                s = s + [nxt]
            succ_of[i.address] = s
            leaders.add(nxt)
        else:
            succ_of[i.address] = [nxt]
    leaders = sorted(a for a in leaders if a in byaddr)
    blocks = {}
    for k, a in enumerate(leaders):
        e = leaders[k + 1] if k + 1 < len(leaders) else f.end
        blocks[a] = [i for i in f.insns if a <= i.address < e]
    entry = tuple([DIRTY] * SPI + [FRAME])
    IN = {}
    def run(seeds):
        work = sorted(seeds)
        for a in seeds:
            IN[a] = entry if a not in IN else IN[a]
        while work:
            a = work.pop(0)
            st = IN[a]
            for i in blocks[a]:
                st = transfer(i, st)
            last = blocks[a][-1]
            for t in succ_of[last.address]:
                if t not in blocks:
                    bad.append('%x: control reaches %x, not a block of the function' % (last.address, t)); continue
                nv = st if t not in IN else meet(IN[t], tuple(st))
                if t not in IN or nv != IN[t]:
                    IN[t] = tuple(nv); work.append(t); work.sort()
    run([f.base])
    while True:
        un = [a for a in leaders if a not in IN]
        if not un:
            break
        run([un[0]])                                                            # unreachable block: analysed from the entry state, never assumed safe
    seen, guest = set(), 0
    for a in leaders:
        st = IN[a]
        for i in blocks[a]:
            seen.add(i.id)
            mem = [o for o in i.operands if o.type == A.ARM64_OP_MEM]
            if is_loadstore(i.bytes) and not mem:
                bad.append('%x: load/store instruction without a memory operand (unclassified)' % i.address)
            for o in mem:
                b = X.get(o.mem.base)
                m = o.mem
                if b is None:
                    bad.append('%x: base register not a general register' % i.address); continue
                if st[b] == FRAME:
                    continue
                guest += 1
                if m.disp != 0: bad.append('%x: displacement %#x folded into the address' % (i.address, m.disp))
                if m.index != 0: bad.append('%x: index register' % i.address)
                if i.writeback: bad.append('%x: base writeback' % i.address)
                if i.address in f.relocs: bad.append('%x: relocated displacement' % i.address)
                if st[b] != CLEAN: bad.append('%x: base register x%d is not the result of a 32-bit operation on every path' % (i.address, b))
            st = transfer(i, st)
    return bad, seen, guest


def load(path):
    """-> list of Func of the executable sections of the object, in (section, address) order"""
    with open(path, 'rb') as fh:
        elf = ELFFile(fh)
        md = Cs(CS_ARCH_ARM64, CS_MODE_ARM); md.detail = True
        syms = elf.get_section_by_name('.symtab')
        out = []
        for si, sec in enumerate(elf.iter_sections()):
            if not sec['sh_flags'] & 4 or sec['sh_type'] != 'SHT_PROGBITS':
                continue
            rel = elf.get_section_by_name('.rela' + sec.name)
            roffs = frozenset(r['r_offset'] for r in rel.iter_relocations()) if rel else frozenset()
            code = sec.data()
            fs = sorted((s['st_value'], s['st_size'], s.name) for s in syms.iter_symbols() if s['st_shndx'] == si and s['st_info']['type'] == 'STT_FUNC')
            for v, n, name in fs:
                ins = list(md.disasm(code[v:v + n], v))
                out.append(Func(name, v, ins, roffs))
                out[-1].short = sum(i.size for i in ins) != n
        return out


# ---- probes ----
OFFS = ('0x40u', '0xfffffffcu', '0x1183000u', '0u')
IDX = ('', '+ecx*4u', '+ecx')
ACC = (('M8', 'u8', 'u32'), ('M16', 'u16', 'u32'), ('M32', 'u32', 'u32'), ('M64', 'u64', 'u64'), ('MF32', 'float', 'float'), ('MF64', 'double', 'double'))
WIDTH = {'M8': 8, 'M16': 16, 'M32': 32, 'M64': 64, 'MF32': 32, 'MF64': 64, 'MX': 128}
RMW_OPS = ('FNVL_ADD', 'FNVL_SUB', 'FNVL_AND', 'FNVL_OR', 'FNVL_XOR', 'FNVL_XCHG')


def probes():
    """-> list of (group, C++ definition text with the name @); group = (kind, class, width, proven)"""
    P = []
    for cls in (0, 1):
        sc = '{ enum { FNVL_R9 = %d }; ' % cls if cls == 0 else '{ '
        for m, rt, vt in ACC + (('MX', 'u32', 'u32'),):
            for off in OFFS:
                for ix in IDX:
                    a = '(u32)(esi%s+%s)' % (ix, off)
                    if m == 'MX':
                        P.append((('load', cls, 128, None), 'u32 @(u32 esi,u32 ecx){ %s xmm_t t = MX(%s); u32x4 v; __builtin_memcpy(&v, &t, 16); __asm__ volatile("" :: "w"(v)); return 0; } }' % (sc, a)))
                        P.append((('store', cls, 128, None), 'void @(u32 esi,u32 ecx,u32 v){ %s xmm_t x; x.q[0]=v; x.q[1]=~(u64)v; MX(%s)=x; } }' % (sc, a)))
                    else:
                        P.append((('load', cls, WIDTH[m], None), '%s @(u32 esi,u32 ecx){ %s return (%s)%s(%s); } }' % (vt, sc, vt, m, a)))
                        P.append((('store', cls, WIDTH[m], None), 'void @(u32 esi,u32 ecx,%s v){ %s %s(%s)=(%s)v; } }' % (vt, sc, m, a, rt)))
    for pv in ('false', 'true'):
        for n in (8, 16, 32, 64):
            u = 'u%d' % n
            for off in OFFS:
                for ix in IDX[:2]:
                    a = '(u32)(esi%s+%s)' % (ix, off)
                    for op in RMW_OPS:
                        P.append((('rmw', 1, n, pv), '%s @(u32 esi,u32 ecx,%s v){ return fnvl_rmw<%s,%s,%s>(%s,v); }' % (u, u, u, op, pv, a)))
                    P.append((('cas', 1, n, pv), '%s @(u32 esi,u32 ecx,%s e,%s d){ return fnvl_cas<%s,%s>(%s,e,d); }' % (u, u, u, u, pv, a)))
                    P.append((('rmwf', 1, n, pv), '%s @(u32 esi,u32 ecx,%s v){ return fnvl_rmwf<%s,%s>(%s,[=](%s o){ return (%s)(0-o+v); }); }' % (u, u, u, pv, a, u, u)))
    return P


# negative controls: each folds a constant, adds an index / writeback, or addresses with a 64-bit value; every one must be BAD
CONTROLS = (
    ('const folded, load', 'u32 @(u32 a){ return *(u32*)((unsigned long)a + 64); }'),
    ('const folded, store', 'void @(u32 a,u32 v){ *(u32*)((unsigned long)a + 0x1183000ul) = v; }'),
    ('index register', 'u32 @(u32 a,u32 i){ u32 r; u64 x=a, y=i; __asm__("ldr %w0, [%1, %2, lsl #2]" : "=r"(r) : "r"(x), "r"(y) : "memory"); return r; }'),
    ('64-bit argument used as is', 'u32 @(u64 a){ u32 r; __asm__("ldr %w0, [%1]" : "=r"(r) : "r"(a) : "memory"); return r; }'),
    ('64-bit add after a 32-bit write, disp 0', 'u32 @(u32 a,u32 b){ u64 t=a; u32 r; __asm__("add %0, %0, %1\\n\\tldr %w2, [%0]" : "+r"(t), "=&r"(r) : "r"((u64)b) : "memory"); return r; }'),
    ('writeback', 'u32 @(u32 a,u32 n){ u32 s=0; for (u32* p=(u32*)(unsigned long)a; n--; ) s += *p++; return s; }'),
    ('constant after a 32-bit sum', 'u32 @(u32 a,u32 b){ return *(u32*)((unsigned long)(a+b) + 4); }'),
)


def build(d, name, defs):
    src = T.test_prelude(L) + 'typedef u32 u32x4 __attribute__((vector_size(16)));\n' + '\n'.join(t.replace('@', 'f%d' % k) for k, t in enumerate(defs)) + '\n'
    with open(d + '/' + name + '.cpp', 'w') as f:
        f.write(src)
    r = subprocess.run(['aarch64-linux-gnu-g++'] + T.FLAGS + [name + '.cpp', '-o', name + '.o'], capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'), cwd=d)
    if r.returncode:
        print(r.stderr[:2000], file=sys.stderr); sys.exit(2)
    return load(d + '/' + name + '.o')


def expect(group, seen, calls):
    """the instructions of the path the probe stands for are really in its code: -> problem text or None"""
    kind, cls, w, pv = group
    acq = bool(seen & ACQREL); ex = bool(seen & EXCL); dmb = DMB in seen
    if kind in ('load', 'store'):
        if cls == 0:
            return None if not (acq or ex or dmb) else 'plain probe contains ordering instructions'
        if w == 128:
            return None if dmb and not acq else 'ordered 128-bit probe lacks the dmb form'
        if w == 8:
            return None if acq and not dmb else 'ordered byte probe is not a single ldarb/stlrb'        # a byte access is aligned by construction: no second path
        return None if acq and dmb else 'ordered probe lacks the aligned (ldar/stlr) or the unaligned (dmb) path'
    if pv == 'true' or w == 8:
        return None if ex and not calls else 'RMW probe on the aligned-only path lacks the exclusive loop or has a call'
    return None if ex and calls and dmb else 'unproven RMW probe lacks the loop, the split-lock call or the dmb'


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else '/home/user/l3work/addrwrap'
    os.makedirs(d, exist_ok=True)
    PR = probes()
    fs = build(d, 'probes', [t for g, t in PR])
    bad, tally = 0, {}
    if len(fs) != len(PR):
        bad += 1; print('BAD probe object has %d functions, expected %d' % (len(fs), len(PR)))
    for (g, t), f in zip(PR, fs):
        pb, seen, guest = analyse(f)
        calls = any(CS_GRP_CALL in i.groups for i in f.insns)
        if guest == 0: pb.append('no guest memory access found in the probe')
        if f.short: pb.append('function bytes not fully decoded')
        e = expect(g, seen, calls)
        if e: pb.append(e)
        tally[g] = tally.get(g, 0) + 1
        if pb:
            bad += 1; print('BAD', g, t.replace('@', f.name), pb[:3])
    cf = build(d, 'controls', [t for n, t in CONTROLS])
    det = 0
    if len(cf) != len(CONTROLS):
        print('BAD control object has %d functions, expected %d' % (len(cf), len(CONTROLS)))
    for (n, t), f in zip(CONTROLS, cf):
        pb, seen, guest = analyse(f)
        if guest and pb: det += 1
        else: print('UNDETECTED control:', n)
    for g in sorted(tally, key=str):
        print('  probes %-6s class %d width %3d proven %-5s : %d' % (g[0], g[1], g[2], g[3], tally[g]))
    print('addrwrap: probes %d bad %d (negative controls %d of %d detected)' % (len(PR), bad, det, len(CONTROLS)))
    sys.exit(0 if bad == 0 and det == len(CONTROLS) and len(cf) == len(CONTROLS) else 1)


if __name__ == '__main__':
    main()
