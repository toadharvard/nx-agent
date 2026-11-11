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


def carrier_d(v, e, k):
    """the double value of an x87 carrier (struct fv, rt/fnvl_x87.h): 1 F64 bits, 2 F32 pattern, 3-5 integer, 6 M80"""
    if k == 1: return struct.unpack('<d', struct.pack('<Q', v))[0]
    if k == 2: return struct.unpack('<f', struct.pack('<I', v))[0]
    if k in (3, 4, 5): return float(v - (1 << 64) if v >> 63 else v)
    return st80((v, e))


STICKY = 0x3f       # FSW IE DE ZE OE UE PE: the sticky exception flags; the Unicorn oracle does not model them (always 0), the lifted side reports the host FPSR (README R8)


def fsw_form(code):
    """'ax' for fnstsw ax, 'm' for fnstsw m16 (the only forms that deliver the FSW to the guest: FSWV in rt/fnvl_x87.h), else None"""
    i = 0
    while i < 4 and code[i] in (0x26, 0x2e, 0x36, 0x3e, 0x64, 0x65, 0x66, 0x67): i += 1
    if code[i] == 0xdf and code[i + 1] == 0xe0: return 'ax'
    if code[i] == 0xdd and code[i + 1] < 0xc0 and (code[i + 1] >> 3) & 7 == 7: return 'm'


_RCP = []


def rcp_host():
    """the reference-CPU functions (rt/rcptab.c, equal to the pinned host on all 2^32 inputs: test/rcptest.py), built for this host. Unicorn computes
    rcp/rsqrt exactly, which is not what the reference CPU returns, so the oracle's results of these instructions are replaced by the reference ones;
    everything downstream is compared bit for bit."""
    if not _RCP:
        import ctypes, subprocess, tempfile
        so = os.path.join(tempfile.gettempdir(), 'fnvl_rcptab_%d.so' % os.getuid())
        subprocess.check_call(['gcc', '-O2', '-shared', '-fPIC', os.path.join(os.path.dirname(HERE), 'rt', 'rcptab.c'), '-o', so + '.%d' % os.getpid()]); os.replace(so + '.%d' % os.getpid(), so)
        lib = ctypes.CDLL(so)
        for f in (lib.fnvl_rcp32, lib.fnvl_rsqrt32): f.argtypes = [ctypes.c_uint32]; f.restype = ctypes.c_uint32
        _RCP.append({0x53: lib.fnvl_rcp32, 0x52: lib.fnvl_rsqrt32})
    return _RCP[0]


def rcp_form(b):
    """-> (opcode 0x52 rsqrt / 0x53 rcp, scalar, dest xmm, source xmm or None for a memory operand) when the bytes are rcpps/rcpss/rsqrtps/rsqrtss"""
    i = 1 if b[0] == 0xf3 else 0
    if b[i] != 0x0f or b[i + 1] not in (0x52, 0x53): return None
    m = b[i + 2]
    return b[i + 1], i == 1, (m >> 3) & 7, (m & 7) if m >> 6 == 3 else None


_SSE = []
SSE_OPS = {(b'', 0x58): ('addps', 0), (b'', 0x5c): ('subps', 1), (b'', 0x59): ('mulps', 2), (b'', 0x5e): ('divps', 3),
           (b'\xf3', 0x58): ('addss', 4), (b'\xf3', 0x5c): ('subss', 5), (b'\xf3', 0x59): ('mulss', 6), (b'\xf3', 0x5e): ('divss', 7),
           (b'\xf2', 0x7c): ('haddps', 8), (b'\xf2', 0x7d): ('hsubps', 9), (b'\xf2', 0xd0): ('addsubps', 10)}


def sse_host():
    """the REAL host CPU executing the single-precision SSE arithmetic instruction itself (xmm0 = first operand, xmm1 = second, result in xmm0).
    The Unicorn oracle (QEMU softfloat) returns, when BOTH operands of a lane are NaN, the NaN picked by significand / sign instead of the first operand
    quieted, which is what x86 SSE does (SDM vol. 1 table 4-7); evidence: a 15 x 15 operand matrix (SNaN / QNaN of both signs and payloads, numbers, inf) run on this host for add/sub/mul/div ss equals the
    first-operand rule in all 900 cases, and Unicorn differs for ps/ss (not pd/sd, min/max, sqrt, cvt) whenever both operands are NaN. So the oracle's result lanes
    that have a NaN operand are replaced by the host's; everything downstream is compared bit for bit."""
    if not _SSE:
        import ctypes, subprocess, tempfile
        names = sorted(SSE_OPS.values(), key=lambda t: t[1])
        src = '#include <stdint.h>\n' + ''.join(
            'static void f%d(const uint32_t*a,const uint32_t*b,uint32_t*r){ __asm__ volatile("movups (%%1),%%%%xmm0\\n movups (%%2),%%%%xmm1\\n %s %%%%xmm1,%%%%xmm0\\n movups %%%%xmm0,(%%0)"::"r"(r),"r"(a),"r"(b):"memory","xmm0","xmm1"); }\n' % (i, n)
            for n, i in names)
        src += 'void fnvl_ssehost(int op,const uint32_t*a,const uint32_t*b,uint32_t*r){ switch(op){' + ''.join('case %d: f%d(a,b,r); break;' % (i, i) for n, i in names) + '} }\n'
        so = os.path.join(tempfile.gettempdir(), 'fnvl_ssehost_%d.so' % os.getuid()); c = so + '.%d.c' % os.getpid()
        open(c, 'w').write(src); subprocess.check_call(['gcc', '-O1', '-shared', '-fPIC', c, '-o', so + '.%d' % os.getpid()]); os.remove(c); os.replace(so + '.%d' % os.getpid(), so)
        lib = ctypes.CDLL(so); _SSE.append(lib.fnvl_ssehost)
    return _SSE[0]


def pfx_split(b):
    """-> (mandatory prefix bytes, rest) of an instruction byte string: segment / address-size prefixes dropped, 66 / f2 / f3 kept (last wins)"""
    i, p = 0, b''
    while b[i] in (0x26, 0x2e, 0x36, 0x3e, 0x64, 0x65, 0x66, 0x67, 0xf2, 0xf3):
        if b[i] in (0x66, 0xf2, 0xf3): p = bytes([b[i]])
        i += 1
    return p, b[i:]


def sse_form(b):
    """-> (op index, dest xmm, source xmm or None for memory) for addps/subps/mulps/divps, the ss forms, haddps/hsubps/addsubps (reg or mem source)"""
    p, r = pfx_split(b)
    if r[0] != 0x0f or (p, r[1]) not in SSE_OPS: return None
    m = r[2]; return SSE_OPS[(p, r[1])][1], (m >> 3) & 7, (m & 7) if m >> 6 == 3 else None


def fld_form(b):
    """-> 4 / 8 for fld m32 (d9 /0) / fld m64 (dd /0) with a memory operand"""
    _, r = pfx_split(b)
    if r[0] in (0xd9, 0xdd) and r[1] < 0xc0 and (r[1] >> 3) & 7 == 0: return 4 if r[0] == 0xd9 else 8


def isnan32(x): return (x & 0x7f800000) == 0x7f800000 and x & 0x7fffff != 0


def sse_lanes(op, a, b):
    """lanes (of 4 or 1) of the result whose operands include a NaN: ops 0-3 / 10 per lane, 4-7 lane 0 only, hadd / hsub lane k from a / b pairs"""
    w = lambda x, i: (x >> (32 * i)) & 0xffffffff
    if op in (4, 5, 6, 7): return [0] if isnan32(w(a, 0)) or isnan32(w(b, 0)) else []
    if op in (8, 9): pr = [(w(a, 0), w(a, 1)), (w(a, 2), w(a, 3)), (w(b, 0), w(b, 1)), (w(b, 2), w(b, 3))]; return [k for k in range(4) if isnan32(pr[k][0]) or isnan32(pr[k][1])]
    return [k for k in range(4) if isnan32(w(a, k)) or isnan32(w(b, k))]


class X86(T.World):
    def run(s, root, ents, st, budget, sticky=None):
        """sticky: the sticky bits the lifted run delivered to its flag-reading instructions, in order; the oracle substitutes them (same ordinal) into the FSW it delivers"""
        s.sticky, s.pend, s.fsw = sticky, None, []
        s.rp = s.fx = None
        uc = Uc(UC_ARCH_X86, UC_MODE_32); s.init_world(uc, True); s.reset(st['wk']); s.ents, s.trace, s.n = ents, [], 0
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, ac, a, n, v, ud: s.writes.add((a & 0xffffffff, n)))
        uc.hook_add(UC_HOOK_CODE, s.code); uc.hook_add(UC_HOOK_MEM_WRITE, s.wr_fsw); uc.hook_add(UC_HOOK_MEM_READ, s.rd_rcp)
        sp = st['regs'][4]; s.ensure(sp, 4, False); uc.mem_write(sp, struct.pack('<I', SENT)); s.writes.clear()
        for a_, v_ in st.get('init', ()): s.ensure(a_, 4, False); uc.mem_write(a_, struct.pack('<I', v_))     # extra initial memory words (same as the lifted side)
        s.writes.clear()
        for k, v in zip(T.X86R, st['regs']): uc.reg_write(k, v)
        uc.reg_write(UC_X86_REG_EFLAGS, 0x202); uc.reg_write(UC_X86_REG_FPCW, 0x27f)
        status = 'ok'
        try: uc.emu_start(root, SENT, timeout=6000000, count=budget)
        except UcError as e: status = 'fault'
        if s.collide: status = 'collide'
        elif status == 'ok' and uc.reg_read(UC_X86_REG_EIP) != SENT: status = 'budget'
        top = (uc.reg_read(UC_X86_REG_FPSW) >> 11) & 7
        return dict(status=status, regs=[uc.reg_read(k) for k in T.X86R], n=s.n, trace=s.trace, ftop=top, fsw=s.fsw, st0=st80(uc.reg_read(UC_X86_REG_FP0 + top)) if status == 'ok' else 0.0,
                    mem=s.written() if status == 'ok' else {})

    def wr_fsw(s, uc, ac, a, n, v, ud):
        if s.pend == 'm' and n == 2: s.pend = ('m', a)

    def rd_rcp(s, uc, ac, a, n, v, ud):
        if s.rp is not None and s.rp[4] is None: s.rp[4] = a          # the memory operand of the rcp/rsqrt instruction being executed
        if s.fx is not None and s.fx[-1] is None: s.fx[-1] = a        # ... of the SSE arithmetic / fld instruction being executed (the first read it makes)

    def code(s, uc, a, size, ud):
        s.n += 1
        if s.rp is not None:        # the previous instruction was rcp/rsqrt: the reference CPU's result replaces the oracle's
            op, scalar, d, src, ma = s.rp; s.rp = None; f = rcp_host()[op]
            x = src if src is not None else int.from_bytes(bytes(uc.mem_read(ma, 4 if scalar else 16)), 'little')
            r = uc.reg_read(UC_X86_REG_XMM0 + d)
            for k in range(1 if scalar else 4): r = (r & ~(0xffffffff << (32 * k))) | (f((x >> (32 * k)) & 0xffffffff) << (32 * k))
            uc.reg_write(UC_X86_REG_XMM0 + d, r)
        if s.fx is not None:        # the previous instruction was an SSE single-precision arithmetic op or fld m32/m64: the real CPU's NaN results replace the oracle's
            fx, s.fx = s.fx, None
            if fx[0] == 'sse':
                _, op, d, xa, xb, ma = fx
                if xb is None: xb = int.from_bytes(bytes(uc.mem_read(ma, 4 if op in (4, 5, 6, 7) else 16)), 'little')
                lanes = sse_lanes(op, xa, xb)
                if lanes:
                    import ctypes
                    A, B, R = (ctypes.c_uint32 * 4)(*[(xa >> (32 * k)) & 0xffffffff for k in range(4)]), (ctypes.c_uint32 * 4)(*[(xb >> (32 * k)) & 0xffffffff for k in range(4)]), (ctypes.c_uint32 * 4)()
                    sse_host()(op, A, B, R); r = uc.reg_read(UC_X86_REG_XMM0 + d)
                    for k in lanes: r = (r & ~(0xffffffff << (32 * k))) | (R[k] << (32 * k))
                    uc.reg_write(UC_X86_REG_XMM0 + d, r)
            else:       # fld m32 / m64 of a signalling NaN: the real CPU's ST0 is the quiet NaN (Unicorn keeps the signalling one)
                _, size, ma = fx; raw = int.from_bytes(bytes(uc.mem_read(ma, size)), 'little')
                snan = (raw & 0x7f800000) == 0x7f800000 and raw & 0x7fffff and not raw & 0x400000 if size == 4 else (raw >> 52 & 0x7ff) == 0x7ff and raw & (1 << 52) - 1 and not raw & 1 << 51
                if snan:
                    reg = UC_X86_REG_FP0 + ((uc.reg_read(UC_X86_REG_FPSW) >> 11) & 7); mant, exp = uc.reg_read(reg); uc.reg_write(reg, (mant | 1 << 62, exp))
        if s.pend is not None:      # the previous instruction was a flag reader: its result is now in EAX / memory
            if s.pend == 'ax' or s.pend[0] == 'm' and s.pend != 'm':
                if s.pend == 'ax': v = uc.reg_read(UC_X86_REG_EAX) & 0xffff
                else: v = struct.unpack('<H', bytes(uc.mem_read(s.pend[1], 2)))[0]
                s.fsw.append(v & STICKY)
                if s.sticky is not None and len(s.fsw) <= len(s.sticky):
                    v = (v & ~STICKY) | s.sticky[len(s.fsw) - 1]
                    if s.pend == 'ax': uc.reg_write(UC_X86_REG_EAX, (uc.reg_read(UC_X86_REG_EAX) & 0xffff0000) | v)
                    else: uc.mem_write(s.pend[1], struct.pack('<H', v))
            s.pend = None
        if a in s.ents: s.trace.append((a, uc.reg_read(UC_X86_REG_ESP)))
        s.pend = fsw_form(bytes(uc.mem_read(a, 8)))
        raw = bytes(uc.mem_read(a, 8)); sf = sse_form(raw); ff = fld_form(raw)
        if sf: s.fx = ['sse', sf[0], sf[1], uc.reg_read(UC_X86_REG_XMM0 + sf[1]), uc.reg_read(UC_X86_REG_XMM0 + sf[2]) if sf[2] is not None else None, None if sf[2] is None else 0]
        elif ff: s.fx = ['fld', ff, None]
        rf = rcp_form(bytes(uc.mem_read(a, 8)))
        if rf: s.rp = [rf[0], rf[1], rf[2], uc.reg_read(UC_X86_REG_XMM0 + rf[3]) if rf[3] is not None else None, None if rf[3] is None else 0]


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
        for a_, v_ in st.get('init', ()): s.ensure(a_, 4, False); uc.mem_write(a_, struct.pack('<I', v_))     # extra initial memory words (fs:[0] chain head)
        s.writes.clear()
        uc.mem_write(s.CPU, struct.pack('<13I', *st['regs'], st.get('fs', 0), 0, 0, 0x27f, 0) + b'\0' * 4 + b'\0' * 64 + b'\0' * 128 + struct.pack('<II', 0, 0x1f80) + b'\0' * 64 + b'\0' * 4
                     + b'\0' * (472 - 324) + struct.pack('<9I', 0x23, 0x1b, 0x23, 0x23, 0x3b, 0, st.get('fs', 0), 0, 0) + b'\0' * 24)      # ... sel[6] (ES CS SS DS FS GS: the process constants), teb, fip, fdp: then msvcrt[6]: struct cpu is 536 bytes
        s.im, s.n, s.nl, s.nd, s.nt, s.nr, s.ntr, s.trace, s.ev = im, 0, 0, 0, 0, 0, 0, [], None
        s.sp = {im.sym[k][0]: k for k in ('fnvl_trace', 'fnvl_raise_ud', 'fnvl_raise', 'fnvl_dispatch', 'fnvl_os_unhandled', 'fnvl_trace_sw') if k in im.sym}; s.fsw = []
        s.rng = sorted(im.rt)
        uc.hook_add(UC_HOOK_CODE, s.code)
        uc.reg_write(UC_ARM64_REG_X0, s.CPU); uc.reg_write(UC_ARM64_REG_X1, im.sym['L_%08x' % root][0]); uc.reg_write(UC_ARM64_REG_SP, s.ASTACK); uc.reg_write(UC_ARM64_REG_X30, s.RETPAD)
        status = 'ok'
        try: uc.emu_start(im.sym['fnvl_enter'][0], s.RETPAD, timeout=20000000, count=budget)
        except UcError as e: status = 'fault %s at %x' % (e, uc.reg_read(UC_ARM64_REG_PC))
        if s.ev: status = s.ev
        elif status == 'ok' and uc.reg_read(UC_ARM64_REG_PC) != s.RETPAD: status = 'budget'
        b = bytes(uc.mem_read(s.CPU, 456)); ftop, ftag = struct.unpack_from('<2I', b, 36); fx = struct.unpack_from('<QII', b, 328 + 16 * (ftop & 7))      # W13: carrier (payload, e, kind) of ST0
        return dict(status=status, regs=list(struct.unpack_from('<8I', b)) if status == 'ok' else [uc.reg_read(k) & 0xffffffff for k in T.GREG], n=s.n, nl=s.nl, nd=s.nd, fsw=s.fsw, ndisp=s.nr, ntr=s.ntr, trace=s.trace, ftop=ftop & 7,
                    st0=carrier_d(*fx) if fx[2] else None, ra=struct.unpack_from('<I', b, 248)[0], mem=s.written() if status == 'ok' or status.startswith('unhandled') else {})

    def code(s, uc, a, size, ud):
        s.n += 1
        if s.im.lo <= a < s.im.hi:
            s.nl += 1
            return
        k = s.sp.get(a)
        if k == 'fnvl_trace': s.trace.append((uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff, uc.reg_read(UC_ARM64_REG_X1) & 0xffffffff)); s.ntr += 1
        elif k == 'fnvl_dispatch': s.nr += 1
        elif k == 'fnvl_trace_sw': s.fsw.append(uc.reg_read(UC_ARM64_REG_X0) & STICKY)
        elif k == 'fnvl_raise_ud': s.ev = 'fnvl_raise_ud(%x)' % (uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff); uc.emu_stop()
        elif k == 'fnvl_os_unhandled':
            r = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff; s.ensure(r, 16, False); s.ev = 'unhandled(%x@%x)' % tuple(struct.unpack('<2I', bytes(uc.mem_read(r, 4)) + bytes(uc.mem_read(r + 12, 4)))); uc.emu_stop()
        elif k == 'fnvl_raise_av': s.ev = 'fnvl_raise_av(%d,%x,%x)' % tuple(uc.reg_read(r_) & 0xffffffff for r_ in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2)); uc.emu_stop()
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
        y = Arm().run(im, root, st, ab)
        y['trace'] = [t for t in y['trace'] if t[0] in ents]     # the lifted side traces every universe entry it passes (also as a local label); the oracle logs the linked entries
        if y['status'] == 'ok' and x['fsw'] != y['fsw']:     # rule: the sticky exception bits are not compared; everything downstream of them is
            x = X86().run(root, ents, st, xb, y['fsw'])
            if x['status'] != 'ok': out.append(('skip', 'x86 ' + x['status'], 0, 0, 0, 0, 0)); continue
            if len(x['fsw']) != len(y['fsw']): out.append(('diff', 'flag readers x86 %d lifted %d' % (len(x['fsw']), len(y['fsw'])), x['n'], y['nl'], y['nd'], y['ndisp'], y['ntr'])); continue
        v, d = compare(x, y)
        out.append((v, d, x['n'], y['nl'], y['nd'], y['ndisp'], y['ntr']))
    return out
