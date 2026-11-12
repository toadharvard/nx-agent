#!/usr/bin/env python3
"""Exception bench (R4): SEH frames on fs:[0], hardware exceptions, RtlUnwind, RaiseException, on BOTH sides with the same seeded state.
    ehtest.py [--census]
x86 side: Unicorn runs the code; there is no OS, so the harness (Python) is the OS: on a divide error / int3 / ud2 / call of a service address it runs
the SAME algorithm as rt/seh.cc (record and context on the guest stack, fs:[0] walk, handlers run through Unicorn, unwind, ContinueExecution).
Lifted side: the lifted code + rt/seh.cc under Unicorn AArch64. Compared: call trace (entry address, esp: handlers, funclets, continuations
included), registers, every written byte (records and contexts too), the root's return address, the unhandled-exception report.
COVERAGE (honest): hand-assembled functions (synthetic image at 0x401000) with the shapes of the exe's frames; NOT yet real exe functions: those need
the IAT (RtlUnwind, RaiseException) patched to the service entries and a host-fault -> guest eip map for access violations.
--census lifts every eh-unwind/eh-catch/eh-cont/eh-throw/seh-filter/seh-handler entry of the universe and counts lifted / failed."""
import argparse, collections, os, struct, subprocess, sys
from keystone import Ks, KS_ARCH_X86, KS_MODE_32
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.environ.get('FNVL_LIFTER', os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, LIFT)
import chain, chrun, nltest, fnvlift as L
from unicorn import *
from unicorn.x86_const import *
T = chrun.T; M = 0xffffffff
FSB, RET_SENT, SVC_UNW, SVC_RAISE = 0x7FFDF000, 0xDEADBEE0, 0x00500010, 0x00500020
BASE = nltest.BASE; SENT = chrun.SENT
FR = lambda h: 'push %d\npush dword ptr fs:[0]\nmov dword ptr fs:[0], esp\n' % h          # SEH frame: push handler; push fs:[0]; fs:[0] = esp
UNFR = 'pop dword ptr fs:[0]\nadd esp, 4\n'
NAMES = ('mA', 'fA', 'h0', 'hA', 'mB', 'fB', 'hB', 'f3', 'h2', 'fU', 'fD', 'fR')
A = {n: BASE + 0x100 * i for i, n in enumerate(NAMES)}


def handler_unwind(c1):    # the shape of __except_handler3: unwinding -> ContinueSearch; else RtlUnwind(frame, c1, rec, 0); esp = frame; jmp c1
    return ('mov eax,[esp+4]\ntest byte ptr [eax+4],2\njz go\nmov eax,1\nret\ngo: push 0\npush eax\npush %d\npush dword ptr [esp+0x14]\ncall %d\n'
            'mov eax,[esp+8]\nmov esp,eax\nmov edx,%d\njmp edx\n' % (c1, SVC_UNW, c1))


def build():
    """-> (code, {name: (addr, size, start, owns)}); the c1 continuations are inner entries of mA / mB"""
    ks = Ks(KS_ARCH_X86, KS_MODE_32); code = bytearray(b'\xcc' * (0x100 * len(NAMES) + 0x100)); ents = {}
    def put(name, text, at=None, size_of=None):
        at = A[name] if at is None else at
        b = bytes(ks.asm(text, at)[0]); code[at - BASE:at - BASE + len(b)] = b; return at, len(b)
    for m, f, h in (('mA', 'fA', 'hA'), ('mB', 'fB', 'hB')):
        c1 = A[m] + 0x40; done = c1 + 5
        a, n = put(m, FR(A[h]) + 'call %d\njmp %d\n' % (A[f], done))
        put(m, 'mov eax, 0x77\n' + UNFR + 'ret\n', c1); ents[m] = (A[m], 0x80, A[m], [c1]); ents[m + '.c1'] = (A[m], 0x80, c1, [c1])
        put(h, handler_unwind(c1)); ents[h] = (A[h], 0x80, A[h], [])
    put('fA', FR(A['h0']) + 'mov eax,5\nxor edx,edx\nxor ecx,ecx\ndiv ecx\nadd esp,8\nret\n')
    put('h0', 'mov eax,1\nret\n')
    put('fB', FR(A['h0']) + 'push 0\npush 0\npush 1\npush 0xE06D7363\ncall %d\nadd esp,8\nret\n' % SVC_RAISE)
    put('f3', FR(A['h2']) + 'mov ebx,0x1234\nint3\nmov eax,0x55\n' + UNFR + 'ret\n')       # #BP, handler says ContinueExecution
    put('h2', 'xor eax,eax\nret\n')
    put('fU', 'xor edx,edx\nxor ecx,ecx\ndiv ecx\nret\n')                                      # no frame: unhandled
    put('fD', FR(A['h0']) + 'mov eax,1\nud2\nret\n')                                           # frame that searches on, then #UD: unhandled
    put('fR', FR(A['h2']) + 'push 0\npush 0\npush 0\npush 0x1000\ncall %d\nmov eax,0x66\n' % SVC_RAISE + UNFR + 'ret\n')    # RaiseException, handler continues
    for n in NAMES:
        if n not in ents: ents[n] = (A[n], 0x80, A[n], [])
    return bytes(code), ents


class Done(Exception):
    pass


class EH(chrun.X86):
    """x86 oracle with the OS in Python: mirrors rt/seh.cc statement by statement"""
    def r32(s, a): s.ensure(a, 4, False); return struct.unpack('<I', bytes(s.uc.mem_read(a, 4)))[0]
    def w32(s, a, v): s.ensure(a, 4, False); s.uc.mem_write(a & M, struct.pack('<I', v & M)); s.writes.add((a & M, 4))
    def zero(s, a, n): [s.w32(a + i, 0) for i in range(0, n, 4)]
    def R(s): return [s.uc.reg_read(k) for k in T.X86R]
    def setR(s, v): [s.uc.reg_write(k, x & M) for k, x in zip(T.X86R, v)]
    def reg(s, i): return s.uc.reg_read(T.X86R[i])
    def setreg(s, i, v): s.uc.reg_write(T.X86R[i], v & M)
    def reg9(s): return s.uc.reg_read(UC_X86_REG_EIP)

    def hook(s, uc, a, size, ud):
        if a in s.stops: s.stopped = a; uc.emu_stop(); return
        s.n += 1; s.cur = a
        if a in s.ents: s.trace.append((a, uc.reg_read(UC_X86_REG_ESP)))
        if s.n > s.budget: raise Done('budget')

    def run_eh(s, root, ents, st, budget):
        uc = Uc(UC_ARCH_X86, UC_MODE_32); s.init_world(uc, True); s.reset(st['wk']); s.ents, s.trace, s.n, s.budget, s.intno = ents, [], 0, budget, None
        s.stops = {SENT, RET_SENT, SVC_UNW, SVC_RAISE}; s.status = 'ok'
        uc.mem_map(T.HX, 0x1000)
        d = lambda base, lim, acc, fl: struct.pack('<HHBBBB', lim & 0xffff, base & 0xffff, base >> 16 & 0xff, acc, lim >> 16 | fl << 4, base >> 24)
        uc.mem_write(T.HX + 0x800, b'\0' * 8 + d(FSB, 0xfffff, 0x92, 0xc) + d(0, 0xfffff, 0x92, 0xc)); uc.reg_write(UC_X86_REG_GDTR, (0, T.HX + 0x800, 24, 0))
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, ac, a, n, v, ud: s.writes.add((a & M, n))); uc.hook_add(UC_HOOK_CODE, s.hook)
        uc.hook_add(UC_HOOK_INTR, lambda uc, no, ud: (setattr(s, 'intno', no), uc.emu_stop()))
        sp = st['regs'][4]; s.ensure(sp, 4, False); uc.mem_write(sp, struct.pack('<I', SENT)); s.w32(FSB, 0xffffffff)
        for sg, sel in ((UC_X86_REG_SS, 16), (UC_X86_REG_DS, 16), (UC_X86_REG_ES, 16), (UC_X86_REG_FS, 8)): uc.reg_write(sg, sel)       # flat segments, fs base FSB (as test/instest.py)
        s.setR(st['regs']); uc.reg_write(UC_X86_REG_EFLAGS, 0x202); uc.reg_write(UC_X86_REG_FPCW, 0x27f); s.writes.clear()
        try: s.drive(root)
        except Done as e: s.status = e.args[0]
        except UcError as e: s.status = 'fault'
        if s.collide: s.status = 'collide'
        top = (uc.reg_read(UC_X86_REG_FPSW) >> 11) & 7
        return dict(status=s.status, regs=s.R(), n=s.n, trace=s.trace, ftop=top, st0=0.0, mem=s.written() if s.status == 'ok' or s.status.startswith('unhandled') else {})

    def drive(s, pc):
        """run from pc until the sentinel: 'ret' (RET_SENT: a handler returned) or 'root' (SENT); services and hardware faults are handled here"""
        while True:
            s.stopped = None; s.intno = None
            ud = False
            try: s.uc.emu_start(pc, 0xfffffff0, timeout=8000000)
            except UcError as e:
                if e.errno != UC_ERR_INSN_INVALID: raise Done('fault')
                ud = True
            at = s.reg9()
            if s.collide: raise Done('collide')
            a = s.stopped
            if s.intno is not None or ud:
                code = s.intno
                if code == 3: at -= 1                       # the trap reports the address after the int3
                ins = next(T.G['L'].core.md.disasm(bytes(s.uc.mem_read(at, 16)), at))
                if code == 0: r = s.raise_rec(0xC0000094, 0, at, [], at, s.reg(4))
                elif ins.mnemonic == 'int3': r = s.raise_rec(0x80000003, 0, at, [0], at + 1, s.reg(4))
                elif ins.mnemonic == 'ud2': r = s.raise_rec(0xC000001D, 0, at, [], at, s.reg(4))
                else: raise Done('fault intno %d' % code)
            elif a == SENT: return 'root'
            elif a == RET_SENT: return 'ret'
            elif a == SVC_UNW: r = s.rtl_unwind()
            elif a == SVC_RAISE: r = s.raise_exc()
            else: raise Done('budget')
            if r == 'root': return 'root'
            pc = r                                     # ('resume' at the address)

    # ---- the OS, mirroring rt/seh.cc ----
    def make_context(s, top, sp, eip):
        x = top - 716; s.zero(x, 716); w = lambda o, v: s.w32(x + o, v); R = s.R()
        w(0, 0x1000F); w(28, 0x27f); w(32, 0); w(36, 0xffff)
        for o, v in ((144, 0x3B), (148, 0x23), (152, 0x23), (188, 0x1B), (200, 0x23), (156, R[7]), (160, R[6]), (164, R[3]), (168, R[2]), (172, R[1]), (176, R[0]), (180, R[5]), (184, eip), (192, 0x202), (196, sp)): w(o, v)
        return x

    def call_handler(s, h, rec, frame, ctx, disp):
        sv = s.R(); sp = sv[4] - 16
        for i, v in enumerate((rec, frame, ctx, disp)): s.w32(sp + 4 * i, v)
        sp -= 4; s.w32(sp, RET_SENT); s.setreg(4, sp); s.uc.reg_write(UC_X86_REG_EIP, h)
        if s.drive(h) == 'root': return None
        r = s.reg(0); s.setR(sv); return r

    def dispatch(s, rec, ctx):
        frame, prev = s.r32(FSB), 0
        while frame != M:
            if frame & 3 or frame <= prev: return 2
            disp = s.reg(4) - 16; s.zero(disp, 16); s.w32(disp, frame); s.setreg(4, disp)
            r = s.call_handler(s.r32(frame + 4), rec, frame, ctx, disp)
            if r is None: return 0
            s.setreg(4, disp + 16)
            if r == 0:
                s.setR([s.r32(ctx + 176), s.r32(ctx + 172), s.r32(ctx + 168), s.r32(ctx + 164), s.r32(ctx + 196), s.r32(ctx + 180), s.r32(ctx + 160), s.r32(ctx + 156)])
                return 1
            if r != 1: s.w32(rec, 0xC0000026); return 2
            prev, frame = frame, s.r32(frame)
        return 2

    def raise_rec(s, code, flags, addr, info, eip, sp):
        rec = sp - 80; s.zero(rec, 80)
        for o, v in ((0, code), (4, flags), (12, addr), (16, len(info))): s.w32(rec + o, v)
        for i, v in enumerate(info): s.w32(rec + 20 + 4 * i, v)
        ctx = s.make_context(rec, sp, eip); s.setreg(4, ctx)
        d = s.dispatch(rec, ctx)
        if d == 2: raise Done('unhandled(%x@%x)' % (s.r32(rec), s.r32(rec + 12)))
        return 'root' if d == 0 else s.r32(ctx + 184)

    def raise_exc(s):
        sp0 = s.reg(4); ra, code, fl, n, args = [s.r32(sp0 + 4 * i) for i in range(5)]; n = min(n, 15)
        info = [s.r32(args + 4 * i) for i in range(n)]; sp = sp0 + 20; s.setreg(4, sp)
        return s.raise_rec(code, fl, ra, info, ra, sp)

    def rtl_unwind(s):
        sp0 = s.reg(4); ra, target, tip, rec, rv = [s.r32(sp0 + 4 * i) for i in range(5)]; sp = sp0 + 20
        if not rec: rec = sp0 - 80; s.zero(rec, 80); s.w32(rec, 0xC0000027); s.w32(rec + 12, ra); sp0 = rec
        s.w32(rec + 4, s.r32(rec + 4) | 2 | (0 if target else 4))
        ea = s.reg(0); s.setreg(0, rv); ctx = s.make_context(sp0, sp, ra); s.setreg(0, ea); s.setreg(4, ctx)
        frame = s.r32(FSB)
        while frame != M and frame != target:
            disp = s.reg(4) - 16; s.zero(disp, 16); s.w32(disp, frame); s.setreg(4, disp)
            if s.call_handler(s.r32(frame + 4), rec, frame, ctx, disp) is None: return 'root'
            s.setreg(4, disp + 16); s.w32(FSB, s.r32(frame)); frame = s.r32(frame)
        s.setreg(4, sp); return ra


def compare(x, y):
    if x['status'].startswith('unhandled'):
        if y['status'] != x['status']: return 'diff', 'x86 %s lifted %s' % (x['status'], y['status'])
        y = dict(y, status='ok', ra=chrun.SENT)
    return chrun.compare(x, y)


def census(a):
    img = L.Image(os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')); ent = L.universe.load()[0]; c = collections.defaultdict(collections.Counter)
    for e, (kind, ext, par) in sorted(ent.items()):
        if kind.startswith(('eh-', 'seh-')):
            try: L.lift_entry(img, e); c[kind]['lifted'] += 1
            except L.Unsupported as ex: c[kind]['failed: ' + str(ex).split(' at ')[0][:40]] += 1
            except Exception as ex: c[kind]['error ' + type(ex).__name__] += 1
    for k, v in sorted(c.items()): print('%-11s total %4d lifted %4d  %s' % (k, sum(v.values()), v['lifted'], dict((a, b) for a, b in v.items() if a != 'lifted')))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--census', action='store_true'); ap.add_argument('--seeds', type=int, default=3)
    ap.add_argument('--work', default='/home/user/l3work/chain/ehtest'); a = ap.parse_args(); a.lifter = LIFT
    if a.census: return census(a)
    os.makedirs(a.work, exist_ok=True)
    code, ents = build(); nltest.BASE = BASE; img = nltest.Fake(code); img.entries = set(v[2] for v in ents.values()) | {SVC_UNW, SVC_RAISE}; T.G['img'] = img; T.G['L'] = L
    texts = {}
    for n, (ad, sz, start, owns) in ents.items(): texts[start] = L.lift(img, ad, sz, start=start, owns=owns)[0]
    flags = [f for f in T.FLAGS if f != '-DFNVL_FLAGS_IO'] + ['-I', LIFT + '/rt']
    o, err = chain.comp_chunk((L.prelude(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rt')) + '\n'.join(texts.values()), flags + ['-DFNVL_TRACE'], a.work))
    if not o: sys.exit('compile: ' + err)
    so = a.work + '/seh.o'; r = subprocess.run(['aarch64-linux-gnu-g++'] + flags + ['-x', 'c++', '-I', LIFT + '/rt', LIFT + '/rt/seh.cc', '-o', so], capture_output=True, text=True)
    if r.returncode: sys.exit('seh.cc: ' + r.stderr[:600])
    syms = {ad: 'L_%08x' % ad for ad in texts}; syms[SVC_UNW] = 'fnvl_os_RtlUnwind'; syms[SVC_RAISE] = 'fnvl_os_RaiseException'
    im = chrun.Image(chain.link(a, [o, so], syms, flags, ['--defsym', 'L_%08x=fnvl_os_RtlUnwind' % SVC_UNW, '--defsym', 'L_%08x=fnvl_os_RaiseException' % SVC_RAISE])); evs = set(st_ for ad, sz, st_, ow in ents.values()); bad = 0    # a direct call of a service address is a call of L_<addr>
    CASES = (('mA', 'unwind through 2 frames after #DE'), ('f3', '#BP, ContinueExecution'), ('mB', 'RaiseException, unwind'), ('fU', '#DE no frame: unhandled'),
             ('fD', '#UD, search, unhandled'), ('fR', 'RaiseException, ContinueExecution'))
    for root, what in CASES:
        res = []
        for i in range(a.seeds):
            st = chrun.gen_state(A[root], i); st['fs'] = FSB; st['init'] = [(FSB, M)]
            x = EH().run_eh(A[root], evs, st, 60000)
            if x['status'] != 'ok' and not x['status'].startswith('unhandled'): res.append(('skip', 'x86 ' + x['status'])); continue
            y = chrun.Arm().run(im, A[root], st, 2000000); res.append(compare(x, y))
        ok = all(v == 'equal' for v, _ in res); bad += not ok
        print('%-3s %-40s %s %s' % (root, what, 'equal' if ok else 'DIFF', ' | '.join('%s %s' % r for r in res if r[0] != 'equal')[:200]))
    sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
