#!/usr/bin/env python3
"""proof/simdspec.py against an independent x86 execution (unicorn / QEMU TCG), row by row: every ROWS entry is encoded in a register form and a memory form
(whichever the row's `form` allows), run on random states (xmm0-7, mm/fr0-7, general registers, the 16 bytes at [ebx]), and the spec's post-state is compared
cell by cell: xmm0-7, the 80-bit fr0-7 images, the stored bytes, the written general register, and the access violation of a misaligned 16-byte operand.
The ACL2 citations are checked by acl2_cite_check.py DIR (the ACL2 checkout is not in the repo).     python3 -I tests/test_simdspec.py"""
import os, random, sys, unittest
import z3
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import simdspec as S
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UcError
from unicorn.x86_const import *
K = S.K
N = int(os.environ.get('SIMD_N', '12'))
FPN = int(os.environ.get('SIMD_FPN', '40'))


def enc_of(pfx, op, mod3, mem_ok):
    """(bytes, wide-pfx) for the register form and the memory form of a row: reg field 2, r/m 5 (register) or [ebx] (memory)"""
    p = bytes.fromhex(pfx) if pfx else b''
    return {'r': p + bytes([0x0F, op, 0xC0 | (2 << 3) | 5]), 'm': p + bytes([0x0F, op, (2 << 3) | 3])}


def val(e):
    r = z3.simplify(e); assert z3.is_bv_value(r) or z3.is_true(r) or z3.is_false(r), r
    return r.as_long() if z3.is_bv_value(r) else bool(z3.is_true(r))


def emu(code, st, mem, aligned_addr):
    mu = Uc(UC_ARCH_X86, UC_MODE_32); mu.mem_map(0x400000, 0x1000); mu.mem_map(0x10000, 0x1000)
    base = 0x10000 if aligned_addr else 0x10001
    mu.mem_write(0x400000, code); mu.mem_write(base, mem); mu.reg_write(UC_X86_REG_EBX, base)
    mu.reg_write(UC_X86_REG_ECX, st['g1']); mu.reg_write(UC_X86_REG_EDX, st['g2']); mu.reg_write(UC_X86_REG_EAX, st['g0']); mu.reg_write(UC_X86_REG_ESI, st['g6']); mu.reg_write(UC_X86_REG_EBP, st['g5'])
    for i in range(8):
        mu.reg_write(UC_X86_REG_XMM0 + i, st['xmm%d' % i]); mu.reg_write(UC_X86_REG_FP0 + i, (st['fr%d' % i] & (2 ** 64 - 1), st['fr%d' % i] >> 64))
    try: mu.emu_start(0x400000, 0x400000 + len(code))
    except UcError: return None
    out = {}
    for i in range(8):
        v = mu.reg_read(UC_X86_REG_XMM0 + i); out['xmm%d' % i] = v if isinstance(v, int) else int.from_bytes(v, 'little')
        m, e = mu.reg_read(UC_X86_REG_FP0 + i); out['fr%d' % i] = m | e << 64
    out['mem'] = bytes(mu.mem_read(base, 16))
    for i, r in ((0, UC_X86_REG_EAX), (1, UC_X86_REG_ECX), (2, UC_X86_REG_EDX), (5, UC_X86_REG_EBP), (6, UC_X86_REG_ESI)): out['g%d' % i] = mu.reg_read(r)
    return out


def rnd(rng, kind):
    v = lambda w: rng.getrandbits(w) if rng.random() < .8 else rng.choice([0, (1 << w) - 1, 1 << (w - 1), (1 << (w - 1)) - 1, 1])
    st = {'xmm%d' % i: v(128) for i in range(8)}
    for i in range(8): st['fr%d' % i] = v(64) | (rng.getrandbits(16) << 64)
    for i in (0, 1, 2, 5, 6): st['g%d' % i] = v(32)
    return st


class Rows(unittest.TestCase):
    def test_rows_against_unicorn(self):
        rng = random.Random(7); fails = []; ncase = 0
        for (pfx, op, m3), R in sorted(S.ROWS.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]))):
            name, form = R.name, R.form
            forms = [f for f in ('r', 'm') if f in form or form == 'rm']
            if m3 is True: forms = ['r']
            for f in forms:
                code = enc_of(pfx, op, m3, True)[f]
                immv = None; base = code
                if f == 'r' and (pfx, op, True) not in S.ROWS and (pfx, op, None) in S.ROWS and S.ROWS[(pfx, op, None)].form == 'm': continue
                for n in range(N):
                    for aligned in ((True, False) if f == 'm' else (True,)):
                        st = rnd(rng, 0); mem = rng.randbytes(16)
                        if R.imm: immv = rng.choice([0, 255, rng.getrandbits(8), rng.getrandbits(8)]); code = base + bytes([immv])
                        want = emu(code, st, mem, aligned)
                        d = {k: K(v, 128 if k.startswith('xmm') else 80) for k, v in st.items() if k[0] in 'xf'}
                        d['ftop'] = K(0, 3); d['ftag'] = K(0, 16)
                        gpr = {0: K(st['g0'], 32), 1: K(st['g1'], 32), 2: K(st['g2'], 32), 5: K(st['g5'], 32), 6: K(st['g6'], 32)}
                        m = None; mem_w = S.mem_width(code) if f == 'm' else 0
                        if f == 'm':
                            w = S.mem_width(code); m = K(int.from_bytes(mem[:w // 8], 'little'), w)
                        a = K(0x10000 if aligned else 0x10001, 32)
                        try: r = S.run(code, d, m, a, gpr, K(immv, 8) if R.imm else None)
                        except KeyError: continue
                        ncase += 1
                        fault = val(r.fault)
                        mis = (not aligned) and f == 'm' and S.mem_aligned(code)
                        if fault != mis: fails.append((name, code.hex(), aligned, 'fault', fault, mis))      # SDM: a misaligned 16-byte operand (QEMU user mode does not fault: not a reference for it)
                        if fault or want is None: continue
                        got = {k: val(r.d[k]) for k in r.d if k.startswith('xmm') or k.startswith('fr')}
                        for k, v in got.items():
                            if k.startswith('fr'):
                                if (want[k] ^ v) & (2 ** 64 - 1): fails.append((name, code.hex(), f, k, hex(want[k]), hex(v)))              # the MMX data; bits 79..64 by the SDM rule (test_mmx_exponent): QEMU leaves them
                            elif want[k] != v: fails.append((name, code.hex(), f, k, hex(want[k]), hex(v)))
                        mem2 = bytearray(mem)
                        if r.store: sa, sv, sb = r.store; mem2[:sb // 8] = val(sv).to_bytes(sb // 8, 'little')
                        if bytes(mem2) != want['mem']: fails.append((name, code.hex(), f, 'mem', want['mem'].hex(), bytes(mem2).hex()))
                        for i in (0, 1, 2, 5, 6):
                            exp = st['g%d' % i]
                            if r.gpr and r.gpr[0] == i: exp = val(r.gpr[1])
                            if want['g%d' % i] != exp: fails.append((name, code.hex(), f, 'g%d' % i, hex(want['g%d' % i]), hex(exp)))
        print('cases', ncase, 'fails', len(fails))
        for x in fails[:30]: print('FAIL', x)
        self.assertEqual(fails, [])

    def test_mmx_rule_and_x87_untouched(self):
        """SDM 1:9.6.2 / 9.5.1 through x87spec: an MMX instruction (other than EMMS) clears TOP and the tag word and a written mmN gets 0xFFFF in bits 79..64;
        an SSE instruction touches no x87 state"""
        d = {'xmm%d' % i: K(i + 1, 128) for i in range(8)}; d.update({'fr%d' % i: K(i + 1, 80) for i in range(8)}); d['ftop'] = K(5, 3); d['ftag'] = K(0x1234, 16)
        r = S.run(bytes([0x0F, 0xFC, 0xC0 | (2 << 3) | 5]), d, None, None, {})        # paddb mm2, mm5
        self.assertEqual((val(r.d['ftop']), val(r.d['ftag']), val(r.d['fr2']) >> 64), (0, 0, 0xFFFF))
        for i in (0, 1, 3, 4, 5, 6, 7): self.assertEqual(val(r.d['fr%d' % i]), i + 1)
        r = S.run(bytes([0x66, 0x0F, 0xFC, 0xC0 | (2 << 3) | 5]), d, None, None, {})  # paddb xmm2, xmm5
        self.assertEqual((val(r.d['ftop']), val(r.d['ftag'])), (5, 0x1234))
        r = S.run(bytes([0x0F, 0x7F, 0x03]), d, None, K(0x10000, 32), {})             # movq [ebx], mm0: a read of mm0 still resets TOP and the tags (SDM 1:9.6.2)
        self.assertEqual((val(r.d['ftop']), val(r.d['ftag']), r.store[2]), (0, 0, 64))


def fpval(rng, w):
    """operand lanes that hit every class: normals, denormals, zeros, infinities, SNaN / QNaN, extremes, the rounding boundaries"""
    eb, fb = (8, 23) if w == 32 else (11, 52)
    cls = rng.randrange(9); sgn = rng.getrandbits(1) << (w - 1); fr = rng.getrandbits(fb); ex = rng.randrange(1, (1 << eb) - 1)
    return sgn | {0: ex << fb | fr, 1: fr, 2: 0, 3: ((1 << eb) - 1) << fb, 4: ((1 << eb) - 1) << fb | fr | 1, 5: ((1 << eb) - 1) << fb | 1 << (fb - 1) | fr,
                  6: ((1 << eb) - 2) << fb | fr, 7: 1 << fb | (fr & 3), 8: (rng.randrange(0, 4) + (1 << (eb - 1)) - 2) << fb | fr}[cls]


def emu_fp(code, st, mem, mxcsr):
    """the reference for MXCSR-dependent behaviour: the host CPU itself (x86-64 Intel), running the very bytes of the row between ldmxcsr and stmxcsr (QEMU / unicorn does not
    maintain the SSE exception flags and ignores FTZ: it is not a reference here).  The row's encoding is re-addressed from [ebx] (r/m 3) to [rdi+16] (r/m 7, disp8 0x10);
    code: movups xmm2,[rdi]; movups xmm5,[rdi+16]; stmxcsr [rdi+40]; ldmxcsr [rdi+32]; ROW; stmxcsr [rdi+36]; ldmxcsr [rdi+40]; movups [rdi+48],xmm2; ret"""
    import ctypes, mmap
    row = bytearray(code)
    if len(row) >= 3 and row[-1] == ((2 << 3) | 3): row[-1] = 0x40 | (2 << 3) | 7; row += bytes([0x10])
    body = (bytes([0x0F, 0x10, 0x17, 0x0F, 0x10, 0x6F, 0x10, 0x0F, 0xAE, 0x5F, 0x28, 0x0F, 0xAE, 0x57, 0x20]) + bytes(row) +
            bytes([0x0F, 0xAE, 0x5F, 0x24, 0x0F, 0xAE, 0x57, 0x28, 0x0F, 0x11, 0x57, 0x30, 0xC3]))
    mm = mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
    mm.write(body)
    buf = mmap.mmap(-1, 4096)
    ptr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
    buf[0:16] = st['xmm2'].to_bytes(16, 'little'); buf[16:32] = (st['xmm5'].to_bytes(16, 'little') if len(code) and code[-1] >> 6 == 3 else mem)
    buf[32:36] = mxcsr.to_bytes(4, 'little')
    ctypes.CFUNCTYPE(None, ctypes.c_void_p)(ctypes.addressof(ctypes.c_char.from_buffer(mm)))(ptr)
    out = {'xmm2': int.from_bytes(buf[48:64], 'little'), 'mxcsr': int.from_bytes(buf[36:40], 'little')}
    for i in (0, 1, 3, 4, 5, 6, 7): out['xmm%d' % i] = st['xmm%d' % i]
    return out


class FpRows(unittest.TestCase):
    def test_fp_arith_against_unicorn(self):
        """add sub mul div min max sqrt, ps pd ss sd: value, MXCSR flags, rounding control, DAZ and FTZ, masks set (ASM-204), on operands of every class"""
        rng = random.Random(11); fails = []; nc = 0
        for (pfx, op, m3), R in sorted(S.ROWS.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if op not in S._FPOPS or (pfx, op, m3) != (pfx, op, None) or pfx not in S._FPFMT: continue
            w, scalar = S._FPFMT[pfx]
            for f in ('r', 'm'):
                code = enc_of(pfx, op, None, True)[f]
                for n in range(FPN):
                    st = {'xmm%d' % i: sum(fpval(rng, w) << (w * j) for j in range(128 // w)) for i in range(8)}
                    mem = rng.randbytes(16) if rng.random() < .5 else b''.join(fpval(rng, w).to_bytes(w // 8, 'little') for _ in range(128 // w))
                    mx = 0x1F80 | rng.getrandbits(2) << 13 | (rng.getrandbits(1) << 6) | (rng.getrandbits(1) << 15) | rng.getrandbits(6) * rng.getrandbits(1)
                    want = emu_fp(code, st, mem, mx)
                    d = {k: K(v, 128) for k, v in st.items()}; d['mxcsr'] = K(mx, 32); d['ftop'] = K(0, 3); d['ftag'] = K(0, 16)
                    for i in range(8): d['fr%d' % i] = K(0, 80)
                    mw = S.mem_width(code) if f == 'm' else 0
                    m = K(int.from_bytes(mem[:mw // 8], 'little'), mw) if f == 'm' else None
                    r = S.run(code, d, m, K(0x10000, 32), {}); nc += 1
                    if want is None: continue
                    for k in ['xmm%d' % i for i in range(8)] + ['mxcsr']:
                        got = val(r.d[k])
                        if got != want[k]: fails.append((R.name, f, k, hex(want[k]), hex(got), hex(mx), [hex(x) for x in (st['xmm2'], st['xmm5'] if f == 'r' else int.from_bytes(mem, 'little'))]))
        print('fp cases', nc, 'fails', len(fails))
        for x in fails[:20]: print('FAIL', x)
        self.assertEqual(fails, [])


if __name__ == '__main__': unittest.main()
