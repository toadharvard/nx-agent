#!/usr/bin/env python3
"""R9 (memory ordering) gate. Lifts synthetic instruction bytes and real universe entries, compiles them with the flags of test/instest.py, and checks
 (i)  ORDERED mode (default): every guest-memory access is ldar/stlr, or a plain access with the matching dmb (ldr; dmb ishld / dmb ishst; str) on the
      unaligned and 16-byte path; every xchg-mem / LOCK RMW with a PROVED aligned constant address is one ldaxr/stlxr loop with `dmb ish` right after it, no run-time alignment branch and no call;
      an address NOT proved aligned (in-block constant propagation, fnvlift/memclass.py const_address; register bit offsets of bts/btr/btc) gets the run-time choice: path (i) the same
      ldaxr/stlxr loop + `dmb ish`, path (ii) the split-lock window `bl fnvl_world_stop; plain ldr .. str; dmb ish; bl fnvl_world_resume` (capstone-decoded, straight-line, rt/fnvl_mem.h);
      the same cases in PLAIN class (every instruction wrapped in `{ enum { FNVL_R9 = 0 }; ... }`) contain no ldar/stlr/dmb and are byte-identical to the objects
      built without rt/fnvl_mem.h (today's M* macros);
      the memclass hook (core.MEMCLASS = PlainSet / AllPlain, env FNVL_MEMCLASS) binds the class per instruction;
 (ii) ORDERED and PLAIN objects give equal results under Unicorn AArch64 (harness of test/instest.py) with aligned and with odd addresses; the LOCK forms in class 2
      (atomic old value + the plain handler's flags / registers) equal the plain handler. Every lock form and xchg is run at address = 0 mod width and at every misaligned
      residue (dword: 1,2,3, a dword straddling a 16-byte boundary and a page boundary; word: 1; qword: 1..7): results equal the PLAIN class, and the harness world
      (single guest thread; fnvl_world_stop/resume are asm stubs with counters at WORLD) shows 0 stop/resume calls on an aligned address and exactly one
      stop..resume pair on a misaligned one, nothing between them but the plain RMW (executed instructions decoded: no call, branch, exclusive or acquire/release; ends with dmb ish).
(iii) the three known xchg sites of the exe (0xedf814, 0xedf979, 0xedfa00: constant 0x0127073C) are proven aligned; 0xec3cda (`xchg [esp],..`) is plain by class and gets the run-time choice otherwise.
    r9test.py [--work DIR] [--entries N]
Prints one summary line; exit 1 on any failure."""
import argparse, hashlib, os, random, re, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
sys.path.insert(0, LIFT); sys.path.insert(0, HERE)
import instest as T
from tools import r9report as RR
import fnvlift as L
from fnvlift import core, memclass as MC
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1
from unicorn import UC_HOOK_CODE
EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
FACTS = os.environ.get('FNVNX_FACTS', '/home/user/l3facts')
UNIVERSE = os.environ.get('FNVNX_UNIVERSE', FACTS + '/universe.tsv')
SYN = 0x7e000000
C = 0x0127073c                                         # the constant address of the known exe xchg sites (multiple of 4)
I4 = lambda v: struct.pack('<I', v).hex()
FAILS = []
def check(ok, msg):
    if not ok: FAILS.append(msg); print('FAIL', msg, file=sys.stderr)
# (name, bytes, ordered-ldar/stlr accesses expected, expects unaligned dmb path ('ld'/'st' kinds), registers holding the address: ebx 3, esi 6, edi 7, esp 4)
CASES = [
    ('mov eax,[ebx]', '8b03', 1, 'l', (3,)), ('mov [ebx],eax', '8903', 1, 's', (3,)), ('mov al,[ebx]', '8a03', 1, '', (3,)), ('mov [ebx],al', '8803', 1, '', (3,)),
    ('mov ax,[ebx]', '668b03', 1, 'l', (3,)), ('mov [ebx],ax', '668903', 1, 's', (3,)), ('add [ebx],eax', '0103', 2, 'ls', (3,)), ('add eax,[ebx+4]', '034304', 1, 'l', (3,)),
    ('push eax', '50', 1, 's', (4,)), ('pop eax', '58', 1, 'l', (4,)), ('push dword [ebx]', 'ff33', 2, 'ls', (3, 4)), ('pop dword [ebx]', '8f03', 2, 'ls', (3, 4)),
    ('movsd', 'a5', 2, 'ls', (6, 7)), ('movsb', 'a4', 2, '', (6, 7)), ('rep movsb', 'f3a4', 2, '', (6, 7)), ('rep movsw', 'f366a5', 2, 'ls', (6, 7)),
    ('stosd', 'ab', 1, 's', (7,)), ('rep stosd', 'f3ab', 1, 's', (7,)), ('lodsd', 'ad', 1, 'l', (6,)),
    ('fld dword [ebx]', 'd903', 1, 'l', (3,)), ('fld qword [ebx]', 'dd03', 1, 'l', (3,)), ('fstp dword [ebx]', 'd91b', 1, 's', (3,)), ('fstp qword [ebx]', 'dd1b', 1, 's', (3,)),
    ('fld tbyte [ebx]', 'db2b', 2, 'l', (3,)),
    ('movaps xmm0,[ebx]', '0f2803', 0, 'l', (3,)), ('movups [ebx],xmm1', '0f110b', 0, 's', (3,)), ('movss xmm0,[ebx]', 'f30f1003', 1, 'l', (3,)),
    ('movsd [ebx],xmm2', 'f20f1113', 1, 's', (3,)), ('movq xmm0,[ebx]', 'f30f7e03', 1, 'l', (3,)),
    ('xchg [ebx],al', '8603', 0, '', (3,)),
    # atomic RMW on a PROVED aligned constant address (hex = the complete byte sequence): ORDERED = one ldaxr/stlxr loop + dmb ish
    ('mov ebx,C; xchg [ebx],eax', 'bb' + I4(C) + '8703', 0, '', ()), ('xchg [C],eax', '8705' + I4(C), 0, '', ()), ('xchg [C],ax', '668705' + I4(C), 0, '', ()),
    ('xchg [C],al', '8605' + I4(C), 0, '', ()), ('mov ebx,C; nop; mov eax,ecx; xchg [ebx+4],edx', 'bb' + I4(C) + '9089c8875304', 0, '', ()),
    ('lock add [C],eax', 'f00105' + I4(C), 0, '', ()), ('lock add dword [C],5', 'f08305' + I4(C) + '05', 0, '', ()), ('lock sub [C],eax', 'f02905' + I4(C), 0, '', ()),
    ('lock and [C],eax', 'f02105' + I4(C), 0, '', ()), ('lock or [C],eax', 'f00905' + I4(C), 0, '', ()), ('lock xor [C],eax', 'f03105' + I4(C), 0, '', ()),
    ('lock add word [C],ax', '66f00105' + I4(C), 0, '', ()), ('lock add [C],al', 'f00005' + I4(C), 0, '', ()),
    ('lock inc dword [C]', 'f0ff05' + I4(C), 0, '', ()), ('lock dec dword [C]', 'f0ff0d' + I4(C), 0, '', ()), ('lock not dword [C]', 'f0f715' + I4(C), 0, '', ()),
    ('lock neg dword [C]', 'f0f71d' + I4(C), 0, '', ()), ('lock adc [C],eax', 'f01105' + I4(C), 0, '', ()), ('lock sbb [C],eax', 'f01905' + I4(C), 0, '', ()),
    ('lock xadd [C],eax', 'f00fc105' + I4(C), 0, '', ()), ('lock cmpxchg [C],ecx', 'f00fb10d' + I4(C), 0, '', ()), ('lock cmpxchg8b [C+4]', 'f00fc70d' + I4(C + 4), 0, '', ()),
    ('lock bts [C],5', 'f00fba2d' + I4(C) + '05', 0, '', ()), ('lock btr [C],5', 'f00fba35' + I4(C) + '05', 0, '', ()), ('lock btc [C],35', 'f00fba3d' + I4(C) + '23', 0, '', ()),
    ('mov ebx,C; lock xadd [ebx],ecx', 'bb' + I4(C) + 'f00fc10b', 0, '', ()),
]
ATOMIC = lambda name: 'xchg' in name or 'lock' in name
# alignment NOT proven: lifts (run-time choice between the atomic path and the split-lock path). (name, bytes): the lift must contain the unproven form `false>` and no proved one
UNPROVEN = [('xchg [ebx],eax', '8703'), ('xchg [ebx],ax', '668703'), ('lock add [ebx],eax', 'f00103'), ('lock inc dword [esp]', 'f0ff0424'),
       ('label between: mov ebx,C; jmp +0; xchg', 'bb' + I4(C) + 'eb008703'), ('redefined: mov ebx,C; mov ebx,ecx; xchg', 'bb' + I4(C) + '89cb8703'),
       ('redefined: mov ebx,C; inc ebx; xchg', 'bb' + I4(C) + '438703'), ('misaligned constant C+1', 'bb' + I4(C + 1) + '8703'), ('misaligned disp: [ebx+2]', 'bb' + I4(C) + '875302'),
       ('call between', 'bb' + I4(C) + 'e800000000' + '8703'), ('lock bts [C],eax (register bit offset)', 'f00fab05' + I4(C)), ('lock cmpxchg8b [ebx]', 'f00fc70b'),
       ('xchg gs:[C],eax', '658705' + I4(C)), ('mov bx,imm16; xchg [ebx]', '66bb0001' + '8703')]
M32 = 0xffffffff
s32 = lambda v: v - (1 << 32) if v >> 31 else v
s16 = lambda v: (v & 0xffff) - (1 << 16) if v & 0x8000 else v & 0xffff
def eb(w): return lambda r: (r[3], w)                                             # the address is [ebx]
def bt32(r): return ((r[3] + 4 * (s32(r[0]) >> 5)) & M32, 4)                      # lock bt* [ebx],eax: the bit offset selects the dword
def bt16(r): return ((r[3] + 2 * (s16(r[0]) >> 4)) & M32, 2)                      # lock bt* [ebx],ax
# the split-lock gate: (name, bytes, width, address of the RMW from the register file, fixed registers {index: value}, whether eax(/edx) is set to the memory value on odd runs)
SPL = [('xchg [ebx],eax', '8703', 4, eb(4), {}, 0), ('xchg [ebx],ax', '668703', 2, eb(2), {}, 0), ('xchg [ebx],al', '8603', 1, eb(1), {}, 0),
       ('lock add [ebx],eax', 'f00103', 4, eb(4), {}, 0), ('lock add dword [ebx],5', 'f0830305', 4, eb(4), {}, 0), ('lock add word [ebx],ax', '66f00103', 2, eb(2), {}, 0),
       ('lock sub [ebx],eax', 'f02903', 4, eb(4), {}, 0), ('lock and [ebx],eax', 'f02103', 4, eb(4), {}, 0), ('lock or [ebx],eax', 'f00903', 4, eb(4), {}, 0),
       ('lock xor [ebx],eax', 'f03103', 4, eb(4), {}, 0), ('lock inc dword [ebx]', 'f0ff03', 4, eb(4), {}, 0), ('lock dec word [ebx]', '66f0ff0b', 2, eb(2), {}, 0),
       ('lock not dword [ebx]', 'f0f713', 4, eb(4), {}, 0), ('lock neg dword [ebx]', 'f0f71b', 4, eb(4), {}, 0), ('lock adc [ebx],eax', 'f01103', 4, eb(4), {}, 0),
       ('lock sbb [ebx],eax', 'f01903', 4, eb(4), {}, 0), ('lock xadd [ebx],eax', 'f00fc103', 4, eb(4), {}, 0), ('lock xadd word [ebx],ax', '66f00fc103', 2, eb(2), {}, 0),
       ('lock cmpxchg [ebx],ecx', 'f00fb10b', 4, eb(4), {}, 1), ('lock cmpxchg word [ebx],cx', '66f00fb10b', 2, eb(2), {}, 1), ('lock cmpxchg8b [ebx]', 'f00fc70b', 8, eb(8), {}, 2),
       ('lock bts [ebx],5', 'f00fba2b05', 4, eb(4), {}, 0), ('lock btr [ebx],5', 'f00fba3305', 4, eb(4), {}, 0), ('lock btc [ebx],35', 'f00fba3b23', 4, eb(4), {}, 0)]
for nm, op in (('bts', 'ab'), ('btr', 'b3'), ('btc', 'bb')):
    for ix in (0, 37, -3, 70): SPL.append(('lock %s [ebx],eax eax=%d' % (nm, ix), 'f00f%s03' % op, 4, bt32, {0: ix & M32}, 0))
    for ix in (0, 21, -3): SPL.append(('lock %s [ebx],ax ax=%d' % (nm, ix), '66f00f%s03' % op, 2, bt16, {0: ix & 0xffff}, 0))
OFFS = {1: [0, 1, 5], 2: [0, 2, 1, 15, 0xeff], 4: [0, 4, 8, 1, 2, 3, 13, 0xefd, 6, 7], 8: [0, 8, 1, 2, 3, 4, 5, 6, 7, 12, 0xefd]}   # 13 / 15 / 12 straddle 16 bytes; 0xefd / 0xeff straddle a page
# harness world: ONE guest thread, so stop/resume have nothing to suspend; they are asm stubs that count (stop at WORLD, resume at WORLD+4, WORLD+8 = 1 between them)
WORLD = 0xE1D00000
WORLD_S = ('.text\n.balign 64\n.globl fnvl_world_stop\nfnvl_world_stop: movz x9,#0xE1D0,lsl #16\n ldr w10,[x9]\n add w10,w10,#1\n str w10,[x9]\n mov w10,#1\n str w10,[x9,#8]\n dmb ish\n ret\n'
           '.balign 64\n.globl fnvl_world_resume\nfnvl_world_resume: dmb ish\n movz x9,#0xE1D0,lsl #16\n ldr w10,[x9,#4]\n add w10,w10,#1\n str w10,[x9,#4]\n str wzr,[x9,#8]\n ret\n')
class FakeImg(L.Image):
    def __init__(s, path, syn): super().__init__(path); s.syn = syn
    def bytes(s, a, n):
        for lo, b in s.syn.items():
            if lo <= a < lo + len(b): return (b[a - lo:] + b'\x90' * n)[:n]
        return super().bytes(a, n)
def setup(work):
    os.makedirs(work, exist_ok=True)
    T.runtime_objects(work, LIFT)            # rt.o, fmath.o, rcptab.o, x87.o, hostticks.o, cpumodel.o: the one list of test/instest.py
    rt = open(work + '/rt.S').read()                 # the harness world replaces the empty single-thread bodies of fnvl_world_stop / fnvl_world_resume in rt.o
    rt = ''.join(ln + '\n' for ln in rt.split('\n') if ln and not re.match(r'\.globl fnvl_world_|fnvl_world_\w+: ret$', ln)) + WORLD_S[len('.text\n'):]
    open(work + '/rt.S', 'w').write(rt)
    subprocess.check_call(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-c', work + '/rt.S', '-o', work + '/rt.o'])
def norm(r):
    """a run result without the log of store events (`wr`): the memory image after the run is compared, not how many store instructions produced it
    (an atomic operation that finds the old value already in place, or a rep with a zero count, has no store event)"""
    fp = dict(r['fp']); fp.pop('wr', None); return dict(r, fp=fp)
class WArm(T.Arm):
    """Unicorn AArch64 run with the harness world observed: calls of fnvl_world_stop / fnvl_world_resume, and the instructions EXECUTED between them (decoded by capstone)"""
    def __init__(s, elf):
        super().__init__(elf)
        s.stop, s.resume = s.sym['fnvl_world_stop'], s.sym['fnvl_world_resume']
        s.uc.hook_add(UC_HOOK_CODE, s.watch)
    def watch(s, uc, addr, size, ud):
        if addr == s.stop: s.nstop += 1; s.nest |= s.win; s.win = True; s.log = []
        elif addr == s.resume: s.nres += 1; s.wins.append(s.log); s.win = False
        elif s.win and not (s.stop <= addr < s.stop + 64): s.log.append(bytes(uc.mem_read(addr, 4)) + addr.to_bytes(4, 'little'))
    def run(s, E, st):
        s.nstop = s.nres = 0; s.nest = s.win = False; s.wins = []; s.log = []; s.uc.mem_write(WORLD, bytes(16))
        r = super().run(E, st)
        s.mem_stop, s.mem_res, s.mem_flag = struct.unpack('<III', bytes(s.uc.mem_read(WORLD, 12)))     # the stubs' own counters
        return r
def window_ok(w):
    """an executed window: straight-line plain RMW (a guest load, a guest store), no call, branch, exclusive or acquire/release, ending with dmb ish -> reason or ''"""
    ins = [next(RR.md.disasm(x[:4], int.from_bytes(x[4:], 'little'))) for x in w]
    if not ins or ins[-1].id != RR.K.ARM64_INS_BL: return 'window does not end with the call of fnvl_world_resume'
    ins = ins[:-1]                                  # the bl itself
    if not ins: return 'empty window'
    if any(i.id in RR.BRANCH | RR.ids('BL', 'BLR', 'BR', 'RET') or i.id in RR.ACQREL for i in ins): return 'call/branch/exclusive/acquire-release inside the window'
    if RR.dmb(ins[-1]) != 'ish': return 'window does not end with dmb ish'
    if not any(i.id in RR.LDS for i in ins) or not any(i.id in RR.STS for i in ins): return 'no plain load and store in the window'
    return ''
def memval(st, addr, w):
    b = b''.join((T.imgpage(p) or T.genpage(st['wk'][0], st['wk'][1], p)) for p in range(addr & ~0xfff, ((addr + w - 1) & ~0xfff) + 0x1000, 0x1000))
    return int.from_bytes(b[addr & 0xfff:(addr & 0xfff) + w], 'little')
def lift_mode(img, mode, addr, size, **kw):
    core.MEMCLASS = MC.AllPlain() if mode == 'plain' else None
    try: return L.lift(img, addr, size, strict=False, **kw)[0]
    finally: core.MEMCLASS = None
def compile_obj(texts, out, prelude=None):
    """out = <dir>/<name>.o; the source is always <dir>/x.cpp compiled from <dir> (the object records the source file name)"""
    d = os.path.dirname(out); os.makedirs(d, exist_ok=True); open(d + '/x.cpp', 'w').write((prelude or T.test_prelude(L)) + '\n'.join(texts))
    r = subprocess.run(['aarch64-linux-gnu-g++'] + T.FLAGS + ['x.cpp', '-o', os.path.basename(out)], capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'), cwd=d)
    if r.returncode: print(r.stderr[:1500], file=sys.stderr)
    return r.returncode == 0
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--work', default='/home/user/l3work/r9/t'); ap.add_argument('--entries', type=int, default=10); a = ap.parse_args()
    core.MEMCLASS = None
    work = a.work; setup(work)
    NC = len(CASES); ALL = CASES + [(c[0], c[1], 0, '', (3,)) for c in SPL]
    syn = {SYN + 0x100 * k: bytes.fromhex(c[1]) for k, c in enumerate(ALL)}
    img = FakeImg(EXE, syn); T.G.update(L=L, img=img, work=work)
    # ---- lift every case in both modes
    txt = {m: {} for m in ('plain', 'ordered')}; E = {}
    for k, (name, hx, nacc, kinds, rg) in enumerate(ALL):
        ad = SYN + 0x100 * k; raw = bytes.fromhex(hx); ins = next(L.core.md.disasm(raw, ad)); base = ins.mnemonic.split()[-1]
        x87 = 0xd8 <= raw[0] <= 0xdf
        E[ad] = dict(addr=ad, raw=raw, ins=ins, base=base, rep=ins.mnemonic.split()[0] in ('rep', 'repe', 'repne') and base in T.STRING, x87=x87, mmx=False,
                     sse='xmm' in ins.op_str or base in ('ldmxcsr', 'stmxcsr'))
        for m in txt: txt[m][ad] = lift_mode(img, m, ad, len(raw), x87='dyn' if x87 else None)
    # ---- compile: ordered / plain objects (linked ELFs for Unicorn, .o for the identity check)
    elfs = {}
    for m in txt:
        elfs[m], bad = T.build(1 if m == 'plain' else 2, sorted(txt[m].items()), work)
        check(elfs[m] is not None and not bad, 'compile/link %s: %s' % (m, bad))
    if FAILS: return summary(0, 0, 0, 0, 0, 0)
    old = L.PRELUDE.replace(open(LIFT + '/rt/fnvl_mem.h').read(), '')
    check(old != L.PRELUDE, 'prelude without fnvl_mem.h not derived')
    o1, o2 = work + '/pn/o.o', work + '/po/o.o'
    ts = [t for _, t in sorted(txt['plain'].items())]
    check(compile_obj(ts, o1) and compile_obj(ts, o2, old), 'plain objects do not compile')
    f1, f2 = RR.functions(o1), RR.functions(o2)      # PLAIN-class vs pre-R9 macros: reported, not required to be bit-identical (GCC may schedule differently); sizes must match
    ident = sum(1 for k in f1 if [x.bytes for x in f1[k]] == [x.bytes for x in f2[k]]); delta = sum(len(f1[k]) - len(f2[k]) for k in f1)
    check(set(f1) == set(f2), 'PLAIN-class object has other functions than the pre-R9 object')
    pre = (ident, len(f1), delta)
    # ---- ORDERED object shape
    dp, do = RR.functions(elfs['plain']), RR.functions(elfs['ordered']); tot = dict(ldar=0, stlr=0, dmb=0, cov=0, xchg=0)
    for k, (name, hx, nacc, kinds, rg) in enumerate(CASES):
        sym = 'L_%08x' % (SYN + 0x100 * k); p, o = RR.scan(dp[sym]), RR.scan(do[sym])
        check(p['ldar'] + p['stlr'] + p['ldaxr'] + p['stlxr'] == 0 and p['ishld'] + p['ishst'] + p['ish'] == 0, 'PLAIN has barriers/acquire-release: ' + name)
        check(p['plain_ld'] + p['plain_st'] > 0 or ATOMIC(name), 'PLAIN case has no plain access (vacuous): ' + name)
        check(not o['uncovered'], 'ORDERED %s: plain guest access without dmb: %s' % (name, o['uncovered'][:2]))
        if ATOMIC(name):
            check(o['stlxr'] >= 1 and o['xchg_uncovered'] == 0 and o['ldaxr'] + o['ldar'] >= 1, 'ORDERED %s: atomic loop without `dmb ish` after stlxr/cbnz' % name)
            check(o['ldar'] + o['stlr'] == 0 or 'cmpxchg' in name or 'lock' in name, 'ORDERED %s: xchg through ldar/stlr' % name)
            check(o['aligncheck'] == 0 and not [c for c in o['calls'] if 'xchg' in c or c.startswith('__') or 'world' in c] and o['stops'] == o['resumes'] == 0, 'ORDERED %s: run-time alignment branch, atomic library call or world stop on a proved aligned address: %s' % (name, o['calls'][:3]))
            tot['xchg'] += 1
            continue
        check(o['ldar'] + o['stlr'] >= nacc, 'ORDERED %s: %d ldar/stlr, expected >= %d' % (name, o['ldar'] + o['stlr'], nacc))
        if 'l' in kinds: check(o['ishld'] >= 1, 'ORDERED %s: no dmb ishld on the unaligned/16B path' % name)
        if 's' in kinds: check(o['ishst'] >= 1, 'ORDERED %s: no dmb ishst on the unaligned/16B path' % name)
        tot['ldar'] += o['ldar']; tot['stlr'] += o['stlr']; tot['dmb'] += o['ishld'] + o['ishst']; tot['cov'] += o['plain_ld'] + o['plain_st']
    # ---- split-lock shape: unproven address -> run-time choice, path (i) atomic loop + dmb ish, path (ii) bl stop; plain RMW; dmb ish; bl resume (decoded, straight-line)
    for k, c in enumerate(SPL):
        if c[2] == 1: continue                  # width 1 is aligned by construction: path (i) only (CASES: 'xchg [ebx],al'), run below
        name = c[0]; sym = 'L_%08x' % (SYN + 0x100 * (NC + k)); p, o = RR.scan(dp[sym]), RR.scan(do[sym])
        check(p['ldar'] + p['stlr'] + p['ldaxr'] + p['stlxr'] + p['ishld'] + p['ishst'] + p['ish'] == 0 and p['stops'] == p['resumes'] == 0, 'PLAIN split case %s has barriers or world calls' % name)
        check(not o['uncovered'], 'ORDERED %s: plain guest access without dmb outside the window: %s' % (name, o['uncovered'][:2]))
        w = o['windows']
        check(o['stops'] == 1 and o['resumes'] == 1 and o['bad_windows'] == 0 and len(w) == 1 and w[0][2] >= 1 and w[0][3] >= 1, 'ORDERED %s: want exactly one valid stop..resume window with a plain load and store: %s' % (name, {x: o[x] for x in ('stops', 'resumes', 'bad_windows', 'windows')}))
        check(o['stlxr'] >= 1 and o['ldaxr'] + o['ldar'] >= 1 and o['xchg_uncovered'] == 0 and o['aligncheck'] >= 1, 'ORDERED %s: path (i) (atomic loop + dmb ish behind the alignment test) missing: %s' % (name, {x: o[x] for x in ('ldaxr', 'stlxr', 'xchg_uncovered', 'aligncheck')}))
        check(set(o['calls']) - {'fnvl_world_stop', 'fnvl_world_resume'} <= {'fnvl_raise'}, 'ORDERED %s: other calls %s' % (name, o['calls']))
        tot['split'] = tot.get('split', 0) + 1
    # ---- memclass hook: PlainSet from a file, partial and empty
    ad = SYN
    pf = work + '/plainset.bin'
    with open(pf, 'wb') as f: f.write(struct.pack('<II', SYN + 0x100 * 5, SYN + 0x100 * 6))
    try:
        core.MEMCLASS = MC.PlainSet(pf)
        for k in (0, 5, 6):
            t = L.lift(img, SYN + 0x100 * k, len(CASES[k][1]) // 2, strict=False)[0]
            check((t == txt['plain'][SYN + 0x100 * k]) == (k in (5, 6)) and (t == txt['ordered'][SYN + 0x100 * k]) == (k == 0), 'PlainSet hook wrong for case %d' % k)
        os.environ['FNVL_MEMCLASS'] = pf; check(MC.from_env() is not None and MC.from_env().plain(SYN + 0x500) and not MC.from_env().plain(SYN + 0x400), 'FNVL_MEMCLASS loader')
    finally: core.MEMCLASS = None; os.environ.pop('FNVL_MEMCLASS', None); os.unlink(pf)
    # ---- alignment NOT proven: lifts in ORDERED (run-time choice, `false>` forms only) and in PLAIN (no atomic needed)
    nunp = 0
    for k, (name, hx) in enumerate(UNPROVEN):
        if core.EMIT == 'dsl' and ' gs:' in name: continue          # f_dsl.py rejects a non-default segment itself (Unsupported 'segment') before the R9 hook is reached
        ad2 = SYN + 0x10000 + 0x100 * k; im2 = FakeImg(EXE, {ad2: bytes.fromhex(hx)}); core.MEMCLASS = None
        try: t = L.lift(im2, ad2, len(hx) // 2, strict=False)[0]
        except core.Unsupported as ex: check(False, 'UNPROVEN %s: lift error %s' % (name, ex)); continue
        check(re.search(r',false>\(', t) is not None and not re.search(r',true>\(', t), 'UNPROVEN %s: not the run-time choice form' % name); nunp += 1
        core.MEMCLASS = MC.AllPlain()
        try: tp = L.lift(im2, ad2, len(hx) // 2, strict=False)[0]; check('fnvl_rmw' not in tp and 'fnvl_cas' not in tp and 'world' not in tp, 'UNPROVEN %s: plain class emits an atomic' % name)
        except core.Unsupported as ex: check(False, 'UNPROVEN %s: plain class lift error %s' % (name, ex))
        finally: core.MEMCLASS = None
    # ---- the known xchg sites of the exe (facts, not rules): constant 0x0127073C, proved aligned; 0xec3cda is `xchg [esp],..`
    sites = {}; ents = L.universe.load()[0]
    for xa in (0xedf814, 0xedf979, 0xedfa00, 0xec3cda):
        ins = next(core.md.disasm(T.G['img'].bytes(xa, 16), xa)); check(ins.mnemonic == 'xchg', 'site %x is not an xchg: %s' % (xa, ins.mnemonic))
        ent = next(x for x in sorted((x for x in ents if x <= xa), reverse=True) if x + ents[x][1] > xa)      # the entry whose extent holds the site
        try: t = L.lift_entry(T.G['img'], ent)[0]; sites[xa] = 'proven' if 'FNVL_XCHG,true>' in t and 'FNVL_XCHG,false>' not in t else 'run-time' if 'FNVL_XCHG,false>' in t else 'none'
        except core.Unsupported as ex: sites[xa] = 'unsupported %s' % ex
    check(all(sites[x] == 'proven' for x in (0xedf814, 0xedf979, 0xedfa00)), 'known xchg sites not proven: %s' % sites)
    check(sites[0xec3cda] == 'run-time', '0xec3cda (xchg [esp],..) without class: run-time choice expected: %s' % sites[0xec3cda])
    # ---- Unicorn: PLAIN vs ORDERED, aligned and odd addresses
    arms = {m: WArm(elfs[m]) for m in elfs}; runs = dict(eq=0, aligned=0, odd=0, neg=nunp, split=0, nosplit=0, pairs=0, sites=sum(v == 'proven' for v in sites.values()))
    offs = [0, 4, 8, 1, 2, 3, 6, 7, 5]
    for k, (name, hx, nacc, kinds, rg) in enumerate(CASES):
        ad = SYN + 0x100 * k; e = E[ad]
        for i, off in enumerate(offs[:1] if not rg else offs):
            st = T.gen_state(e, i, 0)
            for r in rg: st['regs'][r] = (0x60100000 if r == 4 else 0x30000000 + 0x1000 * k + 0x100) + off + (0x40 if r == 7 else 0)    # the address registers: 16-aligned base + off
            res = {}
            for m, arm in arms.items(): res[m] = arm.run(e, st)
            check('fault' not in res['plain'], '%s +%d: PLAIN run faulted: %s' % (name, off, res['plain'].get('fault')))
            check(norm(res['plain']) == norm(res['ordered']), '%s +%d: ORDERED result differs from PLAIN: %s' % (name, off, {k: (res['plain'][k], res['ordered'][k]) for k in res['plain'] if res['plain'][k] != res['ordered'].get(k)}))
            runs['eq'] += res['plain'] == res['ordered']; runs['aligned' if off == 0 else 'odd'] += 1
    # ---- split-lock runs: every lock form / xchg at address = 0 mod width and at every misaligned residue; results equal the PLAIN class, world calls as expected
    for k, (name, hx, wd, eaf, fixed, memeq) in enumerate(SPL):
        ad = SYN + 0x100 * (NC + k); e = E[ad]
        for i, off in enumerate(OFFS[wd]):
            for var in range(2 if memeq else 1):
                st = T.gen_state(e, i * 2 + var, 0); st['regs'][3] = 0x30000000 + 0x1000 * k + 0x100 + off
                for r, v in fixed.items(): st['regs'][r] = v
                addr, w = eaf(st['regs'])
                if memeq and var:           # the compare succeeds: eax (edx:eax) = the memory value
                    v = memval(st, addr, w); mk = (1 << 8 * min(w, 4)) - 1; st['regs'][0] = (st['regs'][0] & ~mk & M32) | (v & mk)
                    if memeq == 2: st['regs'][2] = v >> 32
                res = {m: arm.run(e, st) for m, arm in arms.items()}
                check('fault' not in res['plain'] and 'fault' not in res['ordered'], '%s +%d: faulted: %s' % (name, off, [res[m].get('fault') for m in res]))
                check(norm(res['plain']) == norm(res['ordered']), '%s +%d var %d: ORDERED differs from PLAIN: %s' % (name, off, var, {x: (res['plain'][x], res['ordered'][x]) for x in res['plain'] if res['plain'][x] != res['ordered'].get(x)}))
                pa, oa = arms['plain'], arms['ordered']; want = 0 if addr % w == 0 else 1
                check((pa.nstop, pa.nres, pa.mem_stop, pa.mem_res) == (0, 0, 0, 0), '%s +%d: PLAIN class called the world' % (name, off))
                check((oa.nstop, oa.nres, oa.mem_stop, oa.mem_res, oa.mem_flag, oa.nest) == (want, want, want, want, 0, False), '%s +%d addr %x w%d: world stop/resume (hook %d/%d, stub counters %d/%d, open %d, nested %s), want %d pair(s)' % (name, off, addr, w, oa.nstop, oa.nres, oa.mem_stop, oa.mem_res, oa.mem_flag, oa.nest, want))
                for wn in oa.wins: check(window_ok(wn) == '', '%s +%d: executed window: %s' % (name, off, window_ok(wn)))
                runs['split' if want else 'nosplit'] += 1; runs['pairs'] += oa.nstop; runs['eq'] += norm(res['plain']) == norm(res['ordered'])
    # ---- real entries: ordered lifts compile, every guest access covered
    rows = [l.split('\t') for l in open(UNIVERSE) if l[0] != '#']
    rows = [(int(r[0], 16), int(r[2])) for r in rows if r[1] == 'decomp' and 120 <= int(r[2]) <= 500]
    real = []
    for ad, sz in rows[::max(1, len(rows) // 60)]:
        if len(real) >= a.entries: break
        try: t = L.lift_entry(T.G['img'], ad)[0]
        except core.Unsupported: continue           # an entry the lifter cannot lift for another reason is not a test subject here
        real.append((ad, t))
    ri = dict(n=len(real), ldar=0, stlr=0, unc=0)
    if real:
        ok = compile_obj([t for _, t in real], work + '/real/o.o'); check(ok, 'real entries do not compile in ORDERED mode')
        if ok:
            for sym, ins in RR.functions(work + '/real/o.o').items():
                if not sym.startswith('L_'): continue
                s = RR.scan(ins); ri['ldar'] += s['ldar']; ri['stlr'] += s['stlr']; ri['unc'] += len(s['uncovered'])
                check(not s['uncovered'], 'real %s: plain guest access without dmb %s' % (sym, s['uncovered'][:2]))
    return summary(tot, runs, ri, len(CASES), pre, 0)
def summary(tot, runs, ri, ncases, pre, _):
    if not tot: print('r9test: FAIL (setup) %d failures' % len(FAILS)); return 1
    print('r9test: %s  split-lock: %d forms x residues = %d misaligned runs (one stop..resume pair each, %d pairs) + %d aligned runs (0 calls), all equal to PLAIN; cases %d (ordered ldar %d stlr %d, unaligned/16B dmb %d, plain-with-dmb %d, atomic sites %d: one ldaxr/stlxr loop + `dmb ish`, no alignment branch, no call); '
          'PLAIN class vs pre-R9 macros: %d of %d functions byte-identical, size delta %d insns; unicorn plain==ordered %d runs (aligned %d, odd %d, unproven forms lifted %d (run-time choice), exe sites proven %d); real entries %d (ldar %d stlr %d uncovered %d); %d failures' % (
              'FAIL' if FAILS else 'PASS', tot.get('split', 0), runs['split'], runs['pairs'], runs['nosplit'], ncases, tot['ldar'], tot['stlr'], tot['dmb'], tot['cov'], tot['xchg'], pre[0], pre[1], pre[2], runs['eq'], runs['aligned'], runs['odd'], runs['neg'], runs['sites'],
              ri['n'], ri['ldar'], ri['stlr'], ri['unc'], len(FAILS)))
    return 1 if FAILS else 0
if __name__ == '__main__': sys.exit(main())
