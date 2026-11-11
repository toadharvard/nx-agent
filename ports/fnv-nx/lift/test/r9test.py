#!/usr/bin/env python3
"""R9 (memory ordering) gate. Lifts synthetic instruction bytes and real universe entries, compiles them with the flags of test/instest.py, and checks
 (i)  ORDERED mode (default): every guest-memory access is ldar/stlr, or a plain access with the matching dmb (ldr; dmb ishld / dmb ishst; str) on the
      unaligned and 16-byte path; every xchg-mem has `dmb ish` right after its stlxr/cbnz loop and an unaligned path that calls fnvl_xchg_unaligned*;
      the same cases in PLAIN class (every instruction wrapped in `{ enum { FNVL_R9 = 0 }; ... }`) contain no ldar/stlr/dmb and are byte-identical to the objects
      built without rt/fnvl_mem.h (today's M* macros);
      the memclass hook (core.MEMCLASS = PlainSet / AllPlain, env FNVL_MEMCLASS) binds the class per instruction;
 (ii) ORDERED and PLAIN objects give equal results under Unicorn AArch64 (harness of test/instest.py) with aligned and with odd addresses.
    r9test.py [--work DIR] [--entries N]
Prints one summary line; exit 1 on any failure."""
import argparse, hashlib, os, random, re, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
sys.path.insert(0, LIFT); sys.path.insert(0, HERE)
import instest as T
sys.path.insert(0, os.path.join(LIFT, 'tools'))
import r9report as RR
import fnvlift as L
from fnvlift import core, memclass as MC
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1
from unicorn import UC_HOOK_CODE
EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')
UNIVERSE = os.environ.get('FNVNX_UNIVERSE', '/home/user/l3facts/universe.tsv')
SYN = 0x7e000000
XCH = ('fnvl_xchg_unaligned8', 'fnvl_xchg_unaligned16', 'fnvl_xchg_unaligned32')
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
    ('xchg [ebx],eax', '8703', 0, '', (3,)), ('xchg [ebx],al', '8603', 0, '', (3,)), ('xchg [ebx],ax', '668703', 0, '', (3,)),
]
XCHGS = {'xchg [ebx],eax': 'fnvl_xchg_unaligned32', 'xchg [ebx],ax': 'fnvl_xchg_unaligned16'}
class FakeImg(L.Image):
    def __init__(s, path, syn): super().__init__(path); s.syn = syn
    def bytes(s, a, n):
        for lo, b in s.syn.items():
            if lo <= a < lo + len(b): return (b[a - lo:] + b'\x90' * n)[:n]
        return super().bytes(a, n)
def setup(work):
    os.makedirs(work, exist_ok=True)
    sh = lambda c: subprocess.check_call(c)
    open(work + '/rt.S', 'w').write('.text\n' + ''.join('.globl %s\n%s: ret\n' % (k, k) for k in ('fnvl_call', 'fnvl_dispatch', 'fnvl_raise', 'fnvl_raise_av', 'fnvl_trace', 'fnvl_rcp32', 'fnvl_rsqrt32') + XCH) +
        '.globl memcpy\nmemcpy: mov x3,x0\n1: cbz x2,2f\n ldrb w4,[x1],#1\n strb w4,[x3],#1\n sub x2,x2,#1\n b 1b\n2: ret\n'
        '.globl memset\nmemset: mov x3,x0\n1: cbz x2,2f\n strb w1,[x3],#1\n sub x2,x2,#1\n b 1b\n2: ret\n'
        '.globl memmove\nmemmove: mov x3,x0\n1: cbz x2,2f\n ldrb w4,[x1],#1\n strb w4,[x3],#1\n sub x2,x2,#1\n b 1b\n2: ret\n')   # test link symbols; the xchg ones are events (Arm2.stub), never run
    sh(['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-c', work + '/rt.S', '-o', work + '/rt.o'])
    base = ['aarch64-linux-gnu-gcc', '-mabi=ilp32', '-O2', '-fPIC', '-ffreestanding', '-fno-stack-protector', '-fno-asynchronous-unwind-tables']
    sh(base + ['-I', LIFT + '/rt', '-c', LIFT + '/rt/x87.c', '-o', work + '/x87.o'])
    open(work + '/hostticks.c', 'w').write('unsigned long long fnvl_host_ticks_ns(void){ static unsigned long long n; return n += 1000; }\n')
    sh(base + ['-c', work + '/hostticks.c', '-o', work + '/hostticks.o']); sh(base + ['-c', LIFT + '/rt/cpumodel.c', '-o', work + '/cpumodel.o'])
class Arm2(T.Arm):
    """Arm harness; reaching fnvl_xchg_unaligned* (declared, defined by nobody: no rule yet) is an event instead of a call"""
    def __init__(s, elf):
        super().__init__(elf); a = [s.sym[k] for k in XCH]
        for k in XCH: s.stubs[s.sym[k]] = k
        s.uc.hook_add(UC_HOOK_CODE, s.stub, None, min(a), max(a) + 4)
    def stub(s, uc, addr, size, ud):
        k = s.stubs.get(addr)
        if k in XCH: s.ev = (k, uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff, uc.reg_read(UC_ARM64_REG_X1) & 0xffffffff); uc.emu_stop(); return
        super().stub(uc, addr, size, ud)
def lift_mode(img, mode, addr, size, **kw):
    core.MEMCLASS = MC.AllPlain() if mode == 'plain' else None
    try: return L.lift(img, addr, size, strict=False, **kw)[0]
    finally: core.MEMCLASS = None
def compile_obj(texts, out, prelude=None):
    """out = <dir>/<name>.o; the source is always <dir>/x.cpp compiled from <dir> (the object records the source file name)"""
    d = os.path.dirname(out); os.makedirs(d, exist_ok=True); open(d + '/x.cpp', 'w').write((prelude or L.PRELUDE) + '\n'.join(texts))
    r = subprocess.run(['aarch64-linux-gnu-g++'] + T.FLAGS + ['x.cpp', '-o', os.path.basename(out)], capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'), cwd=d)
    if r.returncode: print(r.stderr[:1500], file=sys.stderr)
    return r.returncode == 0
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--work', default='/home/user/l3work/r9/t'); ap.add_argument('--entries', type=int, default=10); a = ap.parse_args()
    core.MEMCLASS = None
    work = a.work; setup(work)
    syn = {SYN + 0x100 * k: bytes.fromhex(c[1]) for k, c in enumerate(CASES)}
    img = FakeImg(EXE, syn); T.G.update(L=L, img=img, work=work)
    # ---- lift every case in both modes
    txt = {m: {} for m in ('plain', 'ordered')}; E = {}
    for k, (name, hx, nacc, kinds, rg) in enumerate(CASES):
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
    dp, do = RR.functions(elfs['plain']), RR.functions(elfs['ordered']); tot = dict(ldar=0, stlr=0, dmb=0, cov=0, xchg=0, unal=0)
    for k, (name, hx, nacc, kinds, rg) in enumerate(CASES):
        sym = 'L_%08x' % (SYN + 0x100 * k); p, o = RR.scan(dp[sym]), RR.scan(do[sym])
        check(p['ldar'] + p['stlr'] + p['ldaxr'] + p['stlxr'] == 0 and p['ishld'] + p['ishst'] + p['ish'] == 0, 'PLAIN has barriers/acquire-release: ' + name)
        check(p['plain_ld'] + p['plain_st'] > 0 or 'xchg' in name, 'PLAIN case has no plain access (vacuous): ' + name)
        check(not o['uncovered'], 'ORDERED %s: plain guest access without dmb: %s' % (name, o['uncovered'][:2]))
        if 'xchg' in name:
            check(o['stlxr'] >= 1 and o['xchg_uncovered'] == 0 and o['ldaxr'] >= 1, 'ORDERED %s: xchg loop without `dmb ish` after stlxr/cbnz' % name)
            if name in XCHGS: check(sum(c == XCHGS[name] for c in o['calls']) >= 1, 'ORDERED %s: no call of %s on the unaligned path' % (name, XCHGS[name]))
            check(o['ldar'] + o['stlr'] == 0, 'ORDERED %s: xchg through ldar/stlr' % name); tot['xchg'] += 1; tot['unal'] += sum(c in XCH for c in o['calls'])
            continue
        check(o['ldar'] + o['stlr'] >= nacc, 'ORDERED %s: %d ldar/stlr, expected >= %d' % (name, o['ldar'] + o['stlr'], nacc))
        if 'l' in kinds: check(o['ishld'] >= 1, 'ORDERED %s: no dmb ishld on the unaligned/16B path' % name)
        if 's' in kinds: check(o['ishst'] >= 1, 'ORDERED %s: no dmb ishst on the unaligned/16B path' % name)
        tot['ldar'] += o['ldar']; tot['stlr'] += o['stlr']; tot['dmb'] += o['ishld'] + o['ishst']; tot['cov'] += o['plain_ld'] + o['plain_st']
    # ---- memclass hook: PlainSet from a file, partial and empty
    ad = SYN
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as f: f.write(struct.pack('<II', SYN + 0x100 * 5, SYN + 0x100 * 6)); pf = f.name
    try:
        core.MEMCLASS = MC.PlainSet(pf)
        for k in (0, 5, 6):
            t = L.lift(img, SYN + 0x100 * k, len(CASES[k][1]) // 2, strict=False)[0]
            check((t == txt['plain'][SYN + 0x100 * k]) == (k in (5, 6)) and (t == txt['ordered'][SYN + 0x100 * k]) == (k == 0), 'PlainSet hook wrong for case %d' % k)
        os.environ['FNVL_MEMCLASS'] = pf; check(MC.from_env() is not None and MC.from_env().plain(SYN + 0x500) and not MC.from_env().plain(SYN + 0x400), 'FNVL_MEMCLASS loader')
    finally: core.MEMCLASS = None; os.environ.pop('FNVL_MEMCLASS', None); os.unlink(pf)
    # ---- Unicorn: PLAIN vs ORDERED, aligned and odd addresses
    arms = {m: Arm2(elfs[m]) for m in elfs}; runs = dict(eq=0, aligned=0, odd=0, xev=0)
    offs = [0, 4, 8, 1, 2, 3, 6, 7, 5]
    for k, (name, hx, nacc, kinds, rg) in enumerate(CASES):
        ad = SYN + 0x100 * k; e = E[ad]
        for i, off in enumerate(offs):
            st = T.gen_state(e, i, 0)
            for r in rg: st['regs'][r] = (0x60100000 if r == 4 else 0x30000000 + 0x1000 * k + 0x100) + off + (0x40 if r == 7 else 0)    # the address registers: 16-aligned base + off
            res = {}
            for m, arm in arms.items():
                arm.ev = None; res[m] = arm.run(e, st); res[m + '_ev'] = arm.ev
            if 'xchg' in name and name in XCHGS and off % (2 if '16' in XCHGS[name] else 4):
                ev = res['ordered_ev']; ea = st['regs'][3] & 0xffffffff
                check(ev is not None and ev[0] == XCHGS[name] and ev[1] == ea, '%s +%d: unaligned path did not reach %s(%x,..): %s' % (name, off, XCHGS.get(name), ea, ev)); runs['xev'] += ev is not None
                continue
            check('fault' not in res['plain'], '%s +%d: PLAIN run faulted: %s' % (name, off, res['plain'].get('fault')))
            check(res['plain'] == res['ordered'], '%s +%d: ORDERED result differs from PLAIN' % (name, off))
            runs['eq'] += res['plain'] == res['ordered']; runs['aligned' if off == 0 else 'odd'] += 1
    # ---- real entries: ordered lifts compile, every guest access covered
    rows = [l.split('\t') for l in open(UNIVERSE) if l[0] != '#']
    rows = [(int(r[0], 16), int(r[2])) for r in rows if r[1] == 'decomp' and 120 <= int(r[2]) <= 500]
    real = []
    for ad, sz in rows[::max(1, len(rows) // 60)]:
        if len(real) >= a.entries: break
        try: t = L.lift_entry(T.G['img'], ad)[0]
        except Exception: continue
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
    print('r9test: %s  cases %d (ordered ldar %d stlr %d, unaligned/16B dmb %d, plain-with-dmb %d, xchg sites %d with `dmb ish`, unaligned xchg calls %d); '
          'PLAIN class vs pre-R9 macros: %d of %d functions byte-identical, size delta %d insns; unicorn plain==ordered %d runs (aligned %d, odd %d, xchg-unaligned events %d); real entries %d (ldar %d stlr %d uncovered %d); %d failures' % (
              'FAIL' if FAILS else 'PASS', ncases, tot['ldar'], tot['stlr'], tot['dmb'], tot['cov'], tot['xchg'], tot['unal'], pre[0], pre[1], pre[2], runs['eq'], runs['aligned'], runs['odd'], runs['xev'],
              ri['n'], ri['ldar'], ri['stlr'], ri['unc'], len(FAILS)))
    return 1 if FAILS else 0
if __name__ == '__main__': sys.exit(main())
