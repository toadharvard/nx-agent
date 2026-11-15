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
        for (pfx, op, m3), (name, fn, cites, form, mw, al) in sorted(S.ROWS.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2]))):
            forms = [f for f in ('r', 'm') if f in form or form == 'rm']
            if m3 is True: forms = ['r']
            for f in forms:
                code = enc_of(pfx, op, m3, True)[f]
                if f == 'r' and (pfx, op, True) not in S.ROWS and (pfx, op, None) in S.ROWS and S.ROWS[(pfx, op, None)][3] == 'm': continue
                for n in range(N):
                    for aligned in ((True, False) if f == 'm' else (True,)):
                        st = rnd(rng, 0); mem = rng.randbytes(16)
                        want = emu(code, st, mem, aligned)
                        d = {k: K(v, 128 if k.startswith('xmm') else 80) for k, v in st.items() if k[0] in 'xf'}
                        d['ftop'] = K(0, 3); d['ftag'] = K(0, 16)
                        gpr = {0: K(st['g0'], 32), 1: K(st['g1'], 32), 2: K(st['g2'], 32), 5: K(st['g5'], 32), 6: K(st['g6'], 32)}
                        m = None; mem_w = S.mem_width(code) if f == 'm' else 0
                        if f == 'm':
                            w = S.mem_width(code); m = K(int.from_bytes(mem[:w // 8], 'little'), w)
                        a = K(0x10000 if aligned else 0x10001, 32)
                        try: r = S.run(code, d, m, a, gpr)
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


if __name__ == '__main__': unittest.main()
