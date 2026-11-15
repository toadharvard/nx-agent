#!/usr/bin/env python3
"""proof/simdspec.py against its references: the SDM cells (stated values of the pages, quoted per test), the SDM 1:9.6.2 MMX rule through x87spec, and the transcription of rt/rcptab.c
against the C function itself.  No emulator (Unicorn / QEMU) and no host CPU: those are not references of a certificate (measure_simd_host.py is the CPUREF measurement).  The ACL2
citations are checked by acl2_cite_check.py DIR, the SDM quotes by sdm_cite_check.py.     python3 -I tests/test_simdspec.py"""
import os, random, sys, unittest
import z3
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import simdspec as S
K = S.K


def enc_of(pfx, op, mod3, mem_ok):
    """(bytes, wide-pfx) for the register form and the memory form of a row: reg field 2, r/m 5 (register) or [ebx] (memory)"""
    p = bytes.fromhex(pfx) if pfx else b''
    return {'r': p + bytes([0x0F, op, 0xC0 | (2 << 3) | 5]), 'm': p + bytes([0x0F, op, (2 << 3) | 3])}


def val(e):
    r = z3.simplify(e); assert z3.is_bv_value(r) or z3.is_true(r) or z3.is_false(r), r
    return r.as_long() if z3.is_bv_value(r) else bool(z3.is_true(r))


def _state(**kw):
    d = {'xmm%d' % i: K(0, 128) for i in range(8)}; d.update({'fr%d' % i: K(0, 80) for i in range(8)}); d['ftop'] = K(0, 3); d['ftag'] = K(0, 16); d['mxcsr'] = K(0x1F80, 32)
    d.update(kw); return d


class Rows(unittest.TestCase):
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



    def test_sdm_cells(self):
        """values the SDM pages state (each with its quote in simdsdm_cites.txt / the SDM text); the MXCSR_MASK is the SDM 1:11.6.6 default 0xFFBF of a processor without DAZ"""
        S.F._TEST_MASK[:] = [0xFFBF]
        self.addCleanup(lambda: S.F._TEST_MASK.clear())
        # SDM 2A:CVTTSS2SI / 1:4.8.4.1: a NaN, an infinity or a value beyond int32 gives the integer indefinite 0x80000000 and IE (invalid operation)
        for bits in (0x7FC00000, 0x7F800000, 0xFF800000, 0x4F000000, 0xCF800000):
            r = S.run(bytes([0xF3, 0x0F, 0x2C, 0xC0 | (2 << 3) | 5]), _state(xmm5=K(bits, 128)), None, None, {})
            self.assertEqual((val(r.gpr[1]), val(r.d['mxcsr']) & 0x3F), (0x80000000, 1), hex(bits))
        # CVTTSS2SI truncates: -1.5 -> -1 with PE (precision)
        r = S.run(bytes([0xF3, 0x0F, 0x2C, 0xC0 | (2 << 3) | 5]), _state(xmm5=K(0xBFC00000, 128)), None, None, {})
        self.assertEqual((val(r.gpr[1]), val(r.d['mxcsr']) & 0x3F), (0xFFFFFFFF, 0x20))
        # SDM 2A:COMISS Table "Comparison Results": unordered -> ZF, PF, CF = 1,1,1 (IE); greater -> 0,0,0; less -> CF; equal -> ZF
        for a, b, want in ((0x7FC00000, 0x3F800000, (1, 1, 1)), (0x40000000, 0x3F800000, (0, 0, 0)), (0x3F800000, 0x40000000, (0, 0, 1)), (0x3F800000, 0x3F800000, (1, 0, 0))):
            r = S.run(bytes([0x0F, 0x2F, 0xC0 | (2 << 3) | 5]), _state(xmm2=K(a, 128), xmm5=K(b, 128)), None, None, {})
            self.assertEqual(tuple(val(r.flags[f]) for f in ('zf', 'pf', 'cf')), want, (hex(a), hex(b)))
        # SDM 2A:ADDSS / 1:4.8.3.5: Inf + (-Inf) is the QNaN floating-point indefinite 0xFFC00000 with IE
        r = S.run(bytes([0xF3, 0x0F, 0x58, 0xC0 | (2 << 3) | 5]), _state(xmm2=K(0x7F800000, 128), xmm5=K(0xFF800000, 128)), None, None, {})
        self.assertEqual((val(r.d['xmm2']), val(r.d['mxcsr']) & 0x3F), (0xFFC00000, 1))
        # SDM 2A:LDMXCSR: a value with a bit outside MXCSR_MASK is #GP(0)
        if True:
            for v, bad in ((0x1F80, False), (0x1FC0, True), (0x1F80 | 1 << 17, True)):
                r = S.run(bytes([0x0F, 0xAE, (2 << 3) | 3]), _state(), K(v, 32), K(0x10000, 32), {})
                self.assertEqual(val(r.fault), bad, hex(v))


class RcpRows(unittest.TestCase):
    def test_rcp_rsqrt_structure_against_the_c_function(self):
        """ssefp.rcp32 / rsqrt32 (table read from rt/rcptab.c, structure transcribed) equal fnvl_rcp32 / fnvl_rsqrt32 compiled from the same file"""
        import ctypes, subprocess, tempfile
        d = tempfile.mkdtemp(); so = os.path.join(d, 'rcp.so')
        rt = os.path.join(os.path.dirname(HERE), '..', 'rt')
        subprocess.check_call(['gcc', '-O1', '-shared', '-fPIC', '-I', rt, '-o', so, os.path.join(rt, 'rcptab.c')])
        lib = ctypes.CDLL(so); lib.fnvl_rcp32.restype = lib.fnvl_rsqrt32.restype = ctypes.c_uint32; lib.fnvl_rcp32.argtypes = lib.fnvl_rsqrt32.argtypes = [ctypes.c_uint32]
        rng = random.Random(5); xs = [rng.getrandbits(32) for _ in range(300)] + [0, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7F800001, 1, 0x007FFFFF, 0x7E800000, 0x7F000000, 0x3F800000]
        X = z3.BitVec('x', 32)
        for x in xs:
            for sp, cf in ((S.F.rcp32, lib.fnvl_rcp32), (S.F.rsqrt32, lib.fnvl_rsqrt32)):
                self.assertEqual(val(z3.simplify(z3.substitute(sp(X), (X, K(x, 32))))), cf(x), hex(x))


if __name__ == '__main__': unittest.main()
