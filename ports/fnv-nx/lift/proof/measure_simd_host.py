#!/usr/bin/env python3
"""MEASUREMENT, not a test and not part of any certificate (CPUREF, CLAUDE.md "One fixed reference CPU model"): the rows of simdspec.py run against the CPU this script runs on, which is
meaningful only on the pinned reference host.  The very bytes of a row run between ldmxcsr and stmxcsr; value, MXCSR flags and the general-register result are compared with the spec.
No Unicorn / QEMU (a second path, deleted).  The result is a report: FpRows (add sub mul div min max sqrt cmp comis), CvtRows, MxcsrRows.     python3 -I measure_simd_host.py"""
import os, random, sys, unittest
import z3
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tests'))
import simdspec as S
K = S.K
N = int(os.environ.get('SIMD_N', '12'))
FPN = int(os.environ.get('SIMD_FPN', '40'))


from test_simdspec import enc_of, val


def fpval(rng, w):
    """operand lanes that hit every class: normals, denormals, zeros, infinities, SNaN / QNaN, extremes, the rounding boundaries"""
    eb, fb = (8, 23) if w == 32 else (11, 52)
    cls = rng.randrange(9); sgn = rng.getrandbits(1) << (w - 1); fr = rng.getrandbits(fb); ex = rng.randrange(1, (1 << eb) - 1)
    return sgn | {0: ex << fb | fr, 1: fr, 2: 0, 3: ((1 << eb) - 1) << fb, 4: ((1 << eb) - 1) << fb | fr | 1, 5: ((1 << eb) - 1) << fb | 1 << (fb - 1) | fr,
                  6: ((1 << eb) - 2) << fb | fr, 7: 1 << fb | (fr & 3), 8: (rng.randrange(0, 4) + (1 << (eb - 1)) - 2) << fb | fr}[cls]


def emu_fp(code, st, mem, mxcsr, imm=None, gsrc=0):
    """the reference for MXCSR-dependent behaviour: the host CPU itself (x86-64 Intel), running the very bytes of the row between ldmxcsr and stmxcsr (QEMU / unicorn does not
    maintain the SSE exception flags and ignores FTZ: it is not a reference here).  The row's encoding is re-addressed from [ebx] (r/m 3) to [rdi+16] (r/m 7, disp8 0x10);
    code: movups xmm2,[rdi]; movups xmm5,[rdi+16]; stmxcsr [rdi+40]; ldmxcsr [rdi+32]; ROW; stmxcsr [rdi+36]; ldmxcsr [rdi+40]; movups [rdi+48],xmm2; ret"""
    import ctypes, mmap
    row = bytearray(code)
    if len(row) >= 3 and row[-1] == ((2 << 3) | 3): row[-1] = 0x40 | (2 << 3) | 7; row += bytes([0x10])
    if imm is not None: row += bytes([imm])
    body = (bytes([0x55, 0x8B, 0x6F, 0x50, 0x0F, 0x10, 0x17, 0x0F, 0x10, 0x6F, 0x10, 0x0F, 0xAE, 0x5F, 0x28, 0x0F, 0xAE, 0x57, 0x20]) + bytes(row) +
            bytes([0x89, 0x57, 0x4C, 0x9C, 0x58, 0x89, 0x47, 0x44, 0x0F, 0xAE, 0x5F, 0x24, 0x0F, 0xAE, 0x57, 0x28, 0x0F, 0x11, 0x57, 0x30, 0x5D, 0xC3]))      # push rbp; mov ebp,[rdi+0x50] (the register source of cvtsi2s?); ...; mov [rdi+0x4c],edx (the register result of cvts?2si)
    mm = mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
    mm.write(body)
    buf = mmap.mmap(-1, 4096)
    ptr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
    buf[0:16] = st['xmm2'].to_bytes(16, 'little'); buf[16:32] = (st['xmm5'].to_bytes(16, 'little') if len(code) and code[-1] >> 6 == 3 else mem)
    buf[32:36] = mxcsr.to_bytes(4, 'little'); buf[0x50:0x54] = gsrc.to_bytes(4, 'little')
    ctypes.CFUNCTYPE(None, ctypes.c_void_p)(ctypes.addressof(ctypes.c_char.from_buffer(mm)))(ptr)
    out = {'xmm2': int.from_bytes(buf[48:64], 'little'), 'mxcsr': int.from_bytes(buf[36:40], 'little'), 'eflags': int.from_bytes(buf[68:72], "little"), 'edx': int.from_bytes(buf[0x4C:0x50], 'little')}
    for i in (0, 1, 3, 4, 5, 6, 7): out['xmm%d' % i] = st['xmm%d' % i]
    return out


def host_mxcsr_mask():
    import ctypes, mmap
    mm = mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC); mm.write(bytes([0x0F, 0xAE, 0x07, 0xC3]))       # fxsave [rdi]; ret
    buf = mmap.mmap(-1, 4096); ctypes.CFUNCTYPE(None, ctypes.c_void_p)(ctypes.addressof(ctypes.c_char.from_buffer(mm)))(ctypes.addressof(ctypes.c_char.from_buffer(buf)))
    return int.from_bytes(buf[28:32], 'little') or 0xFFBF


class FpRows(unittest.TestCase):
    def test_fp_arith_against_host(self):
        """add sub mul div min max sqrt, ps pd ss sd: value, MXCSR flags, rounding control, DAZ and FTZ, masks set (ASM-204), on operands of every class"""
        S.F._TEST_MASK[:] = [host_mxcsr_mask()]           # the host CPU's own MXCSR_MASK (fxsave): a measurement harness only; certificates read FNVL_CPUREF_MXCSR_MASK
        rng = random.Random(11); fails = []; nc = 0
        for (pfx, op, m3), R in sorted(S.ROWS.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if (op not in S._FPOPS and op not in (0xC2, 0x2E, 0x2F)) or (pfx, op, m3) != (pfx, op, None) or pfx not in S._FPFMT or R.name.startswith(('rcp', 'rsqrt')): continue
            w, scalar = S._FPFMT[pfx]
            for f in ('r', 'm'):
                code = enc_of(pfx, op, None, True)[f]
                for n in range(FPN):
                    st = {'xmm%d' % i: sum(fpval(rng, w) << (w * j) for j in range(128 // w)) for i in range(8)}
                    mem = rng.randbytes(16) if rng.random() < .5 else b''.join(fpval(rng, w).to_bytes(w // 8, 'little') for _ in range(128 // w))
                    mx = (0x1F80 | rng.getrandbits(2) << 13 | (rng.getrandbits(1) << 6) | (rng.getrandbits(1) << 15) | rng.getrandbits(6) * rng.getrandbits(1)) & S.F._TEST_MASK[0]
                    immv = rng.getrandbits(8) if R.imm else None
                    want = emu_fp(code, st, mem, mx, immv)
                    d = {k: K(v, 128) for k, v in st.items()}; d['mxcsr'] = K(mx, 32); d['ftop'] = K(0, 3); d['ftag'] = K(0, 16)
                    for i in range(8): d['fr%d' % i] = K(0, 80)
                    mw = S.mem_width(code) if f == 'm' else 0
                    m = K(int.from_bytes(mem[:mw // 8], 'little'), mw) if f == 'm' else None
                    r = S.run(code + (bytes([immv]) if R.imm else b''), d, m, K(0x10000, 32), {}, K(immv, 8) if R.imm else None); nc += 1
                    if want is None: continue
                    if r.flags:
                        for fn_, bit_ in (('cf', 0), ('pf', 2), ('af', 4), ('zf', 6), ('sf', 7), ('of', 11)):
                            if val(r.flags[fn_]) != (want['eflags'] >> bit_) & 1: fails.append((R.name, f, 'eflag ' + fn_, want['eflags'] >> bit_ & 1, val(r.flags[fn_]), hex(mx), [hex(x) for x in (st['xmm2'], st['xmm5'] if f == 'r' else int.from_bytes(mem, 'little'))]))
                    for k in ['xmm%d' % i for i in range(8)] + ['mxcsr']:
                        got = val(r.d[k])
                        if got != want[k]: fails.append((R.name, f, k, hex(want[k]), hex(got), hex(mx), [hex(x) for x in (st['xmm2'], st['xmm5'] if f == 'r' else int.from_bytes(mem, 'little'))]))
        print('fp cases', nc, 'fails', len(fails))
        for x in fails[:20]: print('FAIL', x)
        self.assertEqual(fails, [])


class CvtRows(unittest.TestCase):
    _SRC = {'cvtdq2ps': 'i', 'cvtps2dq': 32, 'cvttps2dq': 32, 'cvtdq2pd': 'i', 'cvtpd2dq': 64, 'cvttpd2dq': 64, 'cvtps2pd': 32, 'cvtpd2ps': 64, 'cvtss2sd': 32, 'cvtsd2ss': 64,
            'cvtsi2ss': 'i', 'cvtsi2sd': 'i', 'cvtss2si': 32, 'cvttss2si': 32, 'cvtsd2si': 64, 'cvttsd2si': 64}

    def test_cvt_against_host(self):
        """the conversion rows (cvtdq2ps ... cvttsd2si): value, MXCSR flags, rounding control, DAZ / FTZ, masks set (ASM-204), against the host CPU on operands of every class"""
        S.F._TEST_MASK[:] = [host_mxcsr_mask()]
        rng = random.Random(17); fails = []; nc = 0
        def ival(): return rng.choice([0, 1, 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF, 0x1000001, 0xFFFFFF, rng.getrandbits(32), rng.getrandbits(32), rng.getrandbits(24) << 8])
        for (pfx, op, m3), R in sorted(S.ROWS.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if R.name not in self._SRC or m3 is not None: continue
            kind = self._SRC[R.name]; scalar_gpr = R.name.endswith('si') or R.name.startswith('cvtsi2')
            for f in ('r', 'm'):
                code = enc_of(pfx, op, None, True)[f]
                for n in range(FPN):
                    gen = (lambda: ival()) if kind == 'i' else (lambda: fpval(rng, kind))
                    lw = 32 if kind in ('i', 32) else 64
                    st = {'xmm%d' % i: sum(gen() << (lw * j) for j in range(128 // lw)) for i in range(8)}
                    mem = b''.join(gen().to_bytes(lw // 8, 'little') for _ in range(128 // lw))
                    mx = (0x1F80 | rng.getrandbits(2) << 13 | (rng.getrandbits(1) << 6) | (rng.getrandbits(1) << 15) | rng.getrandbits(6) * rng.getrandbits(1)) & S.F._TEST_MASK[0]
                    g5 = ival()
                    want = emu_fp(code, st, mem, mx, None, g5)
                    d = {k: K(v, 128) for k, v in st.items()}; d['mxcsr'] = K(mx, 32); d['ftop'] = K(0, 3); d['ftag'] = K(0, 16)
                    for i in range(8): d['fr%d' % i] = K(0, 80)
                    mw = S.mem_width(code) if f == 'm' else 0
                    m = K(int.from_bytes(mem[:mw // 8], 'little'), mw) if f == 'm' else None
                    r = S.run(code, d, m, K(0x10000, 32), {5: K(g5, 32)}, None); nc += 1
                    tag = (R.name, f, hex(mx), hex(st['xmm2']), hex(st['xmm5'] if f == 'r' else int.from_bytes(mem, 'little')), hex(g5))
                    for k in ['xmm%d' % i for i in range(8)] + ['mxcsr']:
                        if val(r.d[k]) != want[k]: fails.append(tag + (k, hex(want[k]), hex(val(r.d[k]))))
                    if r.gpr and val(r.gpr[1]) != want['edx']: fails.append(tag + ('edx', hex(want['edx']), hex(val(r.gpr[1]))))
        print('cvt cases', nc, 'fails', len(fails))
        for x in fails[:20]: print('FAIL', x)
        self.assertEqual(fails, [])


class MxcsrRows(unittest.TestCase):
    def test_ldmxcsr_fault_and_stmxcsr_against_host(self):
        """LDMXCSR of a value with a bit outside MXCSR_MASK faults (#GP(0), SDM 2A:LDMXCSR), otherwise MXCSR := the value; STMXCSR stores MXCSR (host CPU, one forked child per case)"""
        import ctypes, mmap
        S.F._TEST_MASK[:] = [host_mxcsr_mask()]
        rng = random.Random(3); bad = []
        for n in range(60):
            v = (rng.getrandbits(32) & S.F._TEST_MASK[0]) if n % 2 else rng.getrandbits(32)
            r = S.run(bytes([0x0F, 0xAE, (2 << 3) | 3]), {'mxcsr': K(0, 32)}, K(v, 32), K(0x10000, 32), {})
            fault = val(r.fault)
            pid = os.fork()
            if pid == 0:
                code = bytes([0x0F, 0xAE, 0x5F, 0x08, 0x0F, 0xAE, 0x17, 0x0F, 0xAE, 0x5F, 0x04, 0x0F, 0xAE, 0x57, 0x08, 0xC3])        # stmxcsr [rdi+8]; ldmxcsr [rdi]; stmxcsr [rdi+4]; ldmxcsr [rdi+8]; ret
                mm = mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC); mm.write(code)
                buf = mmap.mmap(-1, 4096); buf[0:4] = v.to_bytes(4, 'little')
                ctypes.CFUNCTYPE(None, ctypes.c_void_p)(ctypes.addressof(ctypes.c_char.from_buffer(mm)))(ctypes.addressof(ctypes.c_char.from_buffer(buf)))
                os._exit(0 if int.from_bytes(buf[4:8], 'little') == v else 2)
            _, st = os.waitpid(pid, 0)
            if (st != 0 and st != 2 << 8) != fault: bad.append((hex(v), fault, st))
            if not fault and st == 0 and val(r.d['mxcsr']) != v: bad.append((hex(v), 'value'))
            if st == 2 << 8: bad.append((hex(v), 'stmxcsr readback'))
        self.assertEqual(bad, [])
        self.assertEqual(val(S.run(bytes([0x0F, 0xAE, (3 << 3) | 3]), {'mxcsr': K(0x1F80, 32)}, None, K(0x10000, 32), {}).store[1]), 0x1F80)


if __name__ == '__main__': unittest.main()
