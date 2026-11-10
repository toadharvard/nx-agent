"""Machines of the chain gate: x86 oracle (Unicorn, real image, real callees) and the lifted AArch64 side (Unicorn, ELF linked at 0xE0000000)
on one lazily generated memory world (test/instest.py World). One Uc per run, so no x87 / hook state leaks between runs."""
import bisect, math, os, random, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'test'))
import instest as T
from unicorn import *
from unicorn.x86_const import *
from unicorn.arm64_const import *
from elftools.elf.elffile import ELFFile

SENT, ESP_TOP = 0xDEAD0000, 0x60100000
ULIM = 0xffffffffffffffff


def gen_state(root, i):
    r = random.Random('c%x:%d' % (root, i)); regs = []
    for _ in range(8):
        t = r.random()
        regs.append(0x30000000 + (r.getrandbits(16) << 4) + (0 if r.random() < .75 else r.getrandbits(4)) if t < .5 else
                    r.choice((0, 1, 2, 3, 4, 5, 7, 8, 15, 16, 0xff, 0xffff, 0x7fffffff, 0x80000000, 0xffffffff, r.getrandbits(4))) if t < .7 else r.getrandbits(32))
    regs[4] = ESP_TOP - 4 * r.randrange(16, 512)
    if root == 0xec8660: regs[0] = r.choice((1, 0x10, 0xff, 0xfff, 0x1000, 0x1001, 0x2345, 0x5000, 0x12000, r.randrange(1, 0x8000)))   # __chkstk: eax = bytes to allocate
    return dict(regs=regs, wk=((root * 31 + i + 1000003) & 0x7fffffff, i % 3))


def st80(t):
    mant, exp = t; s = -1.0 if exp & 0x8000 else 1.0; e = exp & 0x7fff
    if e == 0x7fff: return float('nan') if mant << 1 & (2 ** 64 - 1) else s * float('inf')
    if mant == 0: return s * 0.0
    try: return s * math.ldexp(mant, e - 16383 - 63)
    except OverflowError: return s * float('inf')


class X86(T.World):
    def run(s, root, ents, st, budget):
        uc = Uc(UC_ARCH_X86, UC_MODE_32); s.init_world(uc, True); s.reset(st['wk']); s.ents, s.trace, s.n = ents, [], 0
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, ac, a, n, v, ud: s.writes.add((a & 0xffffffff, n)))
        uc.hook_add(UC_HOOK_CODE, s.code)
        sp = st['regs'][4]; s.ensure(sp, 4, False); uc.mem_write(sp, struct.pack('<I', SENT)); s.writes.clear()
        for k, v in zip(T.X86R, st['regs']): uc.reg_write(k, v)
        uc.reg_write(UC_X86_REG_EFLAGS, 0x202); uc.reg_write(UC_X86_REG_FPCW, 0x27f)
        status = 'ok'
        try: uc.emu_start(root, SENT, timeout=6000000, count=budget)
        except UcError as e: status = 'fault'
        if s.collide: status = 'collide'
        elif status == 'ok' and uc.reg_read(UC_X86_REG_EIP) != SENT: status = 'budget'
        top = (uc.reg_read(UC_X86_REG_FPSW) >> 11) & 7
        return dict(status=status, regs=[uc.reg_read(k) for k in T.X86R], n=s.n, trace=s.trace, ftop=top, st0=st80(uc.reg_read(UC_X86_REG_FP0 + top)) if status == 'ok' else 0.0,
                    mem=s.written() if status == 'ok' else {})

    def code(s, uc, a, size, ud):
        s.n += 1
        if a in s.ents: s.trace.append((a, uc.reg_read(UC_X86_REG_ESP)))


class Image:
    """the linked ELF: loadable segments and symbols"""
    def __init__(s, elf):
        with open(elf, 'rb') as fh:
            e = ELFFile(fh); s.segs = [(p['p_vaddr'], p.data()) for p in e.iter_segments() if p['p_type'] == 'PT_LOAD']
            s.sym = {x.name: (x['st_value'], x['st_size']) for x in e.get_section_by_name('.symtab').iter_symbols() if x.name}
        s.lifted = sorted(v[0] for k, v in s.sym.items() if k.startswith('L_'))
        s.lo, s.hi = s.lifted[0], max(v[0] + v[1] for k, v in s.sym.items() if k.startswith('L_'))
        s.rt = {v[0]: (k, v[0] + v[1]) for k, v in s.sym.items() if k.startswith('fnvl_') and v[1]}


class Arm(T.World):
    CPU, ASTACK, RETPAD = T.CPU, T.ASTACK, T.RETPAD

    def run(s, im, root, st, budget):
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM); s.init_world(uc, False); s.reset(st['wk']); uc.mem_map(T.HARN, T.HARN_END - T.HARN)
        for va, d in im.segs: uc.mem_write(va, d)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
        sp = st['regs'][4]; s.ensure(sp, 4, False); uc.mem_write(sp, struct.pack('<I', SENT))
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, ac, a, n, v, ud: None if T.HARN <= a < T.HARN_END else s.writes.add((a & 0xffffffff, n)))
        s.writes.clear()
        uc.mem_write(s.CPU, struct.pack('<13I', *st['regs'], st.get('fs', 0), 0, 0, 0x27f, 0) + b'\0' * 4 + b'\0' * 64 + b'\0' * 128 + struct.pack('<II', 0, 0x1f80) + b'\0' * 64 + b'\0' * 4)
        s.im, s.n, s.nl, s.nd, s.nt, s.nr, s.ntr, s.trace, s.ev = im, 0, 0, 0, 0, 0, 0, [], None
        s.sp = {im.sym[k][0]: k for k in ('fnvl_trace', 'fnvl_raise_ud', 'fnvl_raise', 'fnvl_dispatch', 'fnvl_os_unhandled') if k in im.sym}
        s.rng = sorted(im.rt)
        uc.hook_add(UC_HOOK_CODE, s.code)
        uc.reg_write(UC_ARM64_REG_X0, s.CPU); uc.reg_write(UC_ARM64_REG_X1, im.sym['L_%08x' % root][0]); uc.reg_write(UC_ARM64_REG_SP, s.ASTACK); uc.reg_write(UC_ARM64_REG_X30, s.RETPAD)
        status = 'ok'
        try: uc.emu_start(im.sym['fnvl_enter'][0], s.RETPAD, timeout=20000000, count=budget)
        except UcError as e: status = 'fault %s at %x' % (e, uc.reg_read(UC_ARM64_REG_PC))
        if s.ev: status = s.ev
        elif status == 'ok' and uc.reg_read(UC_ARM64_REG_PC) != s.RETPAD: status = 'budget'
        b = bytes(uc.mem_read(s.CPU, 324)); ftop, ftag = struct.unpack_from('<2I', b, 36); stv = struct.unpack_from('<8d', b, 56)
        return dict(status=status, regs=list(struct.unpack_from('<8I', b)), n=s.n, nl=s.nl, nd=s.nd, ndisp=s.nr, ntr=s.ntr, trace=s.trace, ftop=ftop & 7,
                    st0=stv[ftop & 7] if ftag >> (ftop & 7) & 1 else None, ra=struct.unpack_from('<I', b, 320)[0], mem=s.written() if status == 'ok' or status.startswith('unhandled') else {})

    def code(s, uc, a, size, ud):
        s.n += 1
        if s.im.lo <= a < s.im.hi:
            s.nl += 1
            return
        k = s.sp.get(a)
        if k == 'fnvl_trace': s.trace.append((uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff, uc.reg_read(UC_ARM64_REG_X1) & 0xffffffff)); s.ntr += 1
        elif k == 'fnvl_dispatch': s.nr += 1
        elif k == 'fnvl_raise_ud': s.ev = 'fnvl_raise_ud(%x)' % (uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff); uc.emu_stop()
        elif k == 'fnvl_os_unhandled':
            r = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff; s.ensure(r, 16, False); s.ev = 'unhandled(%x@%x)' % tuple(struct.unpack('<2I', bytes(uc.mem_read(r, 4)) + bytes(uc.mem_read(r + 12, 4)))); uc.emu_stop()
        elif k == 'fnvl_raise': s.ev = 'fnvl_raise(%d,%x)' % (uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff, uc.reg_read(UC_ARM64_REG_X1) & 0xffffffff); uc.emu_stop()
        i = bisect.bisect_right(s.rng, a) - 1
        if i >= 0 and a < s.im.rt[s.rng[i]][1] and s.im.rt[s.rng[i]][0] == 'fnvl_dispatch': s.nd += 1


def compare(x, y):
    """-> (verdict, detail); x the x86 oracle, y the lifted side"""
    if y['status'] != 'ok': return 'diff', 'lifted ' + y['status']
    if x['trace'] != y['trace']:
        i = next((i for i, (p, q) in enumerate(zip(x['trace'], y['trace'])) if p != q), min(len(x['trace']), len(y['trace'])))
        f = lambda t, i: '%x@%x' % t[i] if i < len(t) else 'end'
        return 'diff', 'trace[%d] x86 %s lifted %s (len %d/%d)' % (i, f(x['trace'], i), f(y['trace'], i), len(x['trace']), len(y['trace']))
    if x['regs'] != y['regs']:
        i = next(i for i in range(8) if x['regs'][i] != y['regs'][i]); return 'diff', '%s x86 %x lifted %x' % (T.R32[i], x['regs'][i], y['regs'][i])
    if y['ra'] != SENT: return 'diff', 'final ra %x' % y['ra']
    if x['ftop'] != y['ftop']: return 'diff', 'x87 top %d/%d' % (x['ftop'], y['ftop'])
    if y['st0'] is not None and not ((x['st0'] != x['st0'] and y['st0'] != y['st0']) or struct.pack('<d', x['st0']) == struct.pack('<d', y['st0'])): return 'diff', 'st0 %r/%r' % (x['st0'], y['st0'])
    if x['mem'] != y['mem']:
        a = next(a for a in sorted(set(x['mem']) | set(y['mem'])) if x['mem'].get(a) != y['mem'].get(a)); return 'diff', 'mem[%x] x86 %s lifted %s' % (a, x['mem'].get(a), y['mem'].get(a))
    return 'equal', ''


def run_chain(im, root, ents, seeds, xb=120000, ab=2000000):
    """-> list of (verdict, detail, x86 insns, A64 lifted insns, dispatch insns, dispatch calls, trace events)"""
    out = []
    for i in range(seeds):
        st = gen_state(root, i); x = X86().run(root, ents, st, xb)
        if x['status'] != 'ok': out.append(('skip', 'x86 ' + x['status'], 0, 0, 0, 0, 0)); continue
        y = Arm().run(im, root, st, ab); v, d = compare(x, y)
        out.append((v, d, x['n'], y['nl'], y['nd'], y['ndisp'], y['ntr']))
    return out
