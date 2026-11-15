"""SIMD integer rows whose source is the Intel SDM text (OWNER 10), registered into simdspec.ROWS by `register(S)` (called at the bottom of simdspec.py with its own module object, so
there is one ROWS table).  Where the pinned ACL2 x86isa model has the instruction the row also carries its ACL2 cite (acl2_cites.tsv); the rest is the SDM page of the
instruction.  Each SDM key is `SDM 2B:<NAME>` (sdm_pages.tsv: the PDF page of the instruction); the quoted fragments of the pages are in simdsdm_cites.txt and `sdm_cite_check.py SDM.txt simdsdm_cites.txt` checks every
fragment against the page text.  Families: shifts (register-count and imm8 groups, psrldq/pslldq), pmullw / pmulhw / pmulhuw / pmuludq / pmaddwd, pack*, pavg, pmin / pmax, psadbw,
pmovmskb, pextrw / pinsrw (the imm8 is the whole byte, masked as the SDM says), psubq mm, emms, movntps, prefetch."""
import z3
from z3 import And, Concat, Extract, If, ZeroExt, SignExt, LShR, ULT, UGT, ULE, UGE


def register(S):
    K, row, cite, Res, lanes, join, bmap, xmm, mm, put_xmm, put_mm, _w, _src, ONES = (S.K, S.row, S.cite, S.Res, S.lanes, S.join, S.bmap, S.xmm, S.mm, S.put_xmm, S.put_mm, S._w, S._src, S.ONES)

    def both(name, fn, op, quote_key, wide_only=False, mmx_op=None, acl=None, mmx_ok=True):
        """MMX (no prefix) and SSE2 (66) forms of a lane function `fn(dst, src) -> value` over the operand width"""
        cs = [cite(*acl)] if acl else []
        if mmx_ok and not wide_only:
            row('', op if mmx_op is None else mmx_op, name, cs, mw=64)(lambda e, d, m, a, g, fn=fn: _w(e, a, m, d, False, fn))
        row('66', op, name, cs, mw=128)(lambda e, d, m, a, g, fn=fn: _w(e, a, m, d, True, fn))

    # ---- shifts: SDM 2B:PSLLW "Shift words in xmm1 left by xmm2/m128 while" ----------------------------------------------------------------------------------------
    # The count is the low 64 bits of the source operand (unsigned); a count above the element size - 1 gives 0 (psll, psrl) or the sign (psra).
    def sh(kind, eb):
        def f(x, c):
            n = Extract(eb - 1, 0, c) if eb <= 64 else c
            big = UGT(c, K(eb - 1, 64))
            if kind == 'sll': return If(big, K(0, eb), x << n)
            if kind == 'srl': return If(big, K(0, eb), LShR(x, n))
            return If(big, x >> K(eb - 1, eb), x >> n)         # sra: z3 >> is arithmetic; a count above the size gives the sign bits
        return f

    def shift_reg(kind, eb, wide):
        def fn(e, d, m, a, g):
            src = _src(e, d, m, 128 if wide else 64); c = Extract(63, 0, src); dst = xmm(d, e.reg) if wide else mm(d, e.reg)
            f = sh(kind, eb); out = join([f(x, c) for x in lanes(dst, eb)])
            r = Res(put_xmm(d, e.reg, out) if wide else put_mm(d, e.reg, out))
            if wide and e.mem: r.fault = S.misaligned(a)
            return r
        return fn

    # SDM 2B:PSLLW (quote: simdsdm_cites.txt)
    # SDM 2B:PSRLW (quote: simdsdm_cites.txt)
    # SDM 2B:PSRAW (quote: simdsdm_cites.txt)
    for kind, base in (('srl', 0xD0), ('sra', 0xE0), ('sll', 0xF0)):
        for eb, k in ((16, 1), (32, 2), (64, 3)):
            if kind == 'sra' and eb == 64: continue
            nm = {'srl': 'psrl', 'sra': 'psra', 'sll': 'psll'}[kind] + {16: 'w', 32: 'd', 64: 'q'}[eb]
            row('', base + k, nm, [], mw=64)(shift_reg(kind, eb, False))
            row('66', base + k, nm, [], mw=128)(shift_reg(kind, eb, True))

    def shift_imm(wide):
        """0F 71 / 72 / 73 groups: ModRM.reg selects the operation, ModRM.rm is the register, imm8 the count (mod = 3 only)"""
        grp = {0x71: {2: ('srl', 16), 4: ('sra', 16), 6: ('sll', 16)}, 0x72: {2: ('srl', 32), 4: ('sra', 32), 6: ('sll', 32)}, 0x73: {2: ('srl', 64), 6: ('sll', 64)}}

        def fn(e, d, m, a, g):
            k = grp[e.op].get(e.reg)
            if k is None and wide and e.op == 0x73 and e.reg in (3, 7): k = ('bsrl' if e.reg == 3 else 'bsll', 128)
            if k is None: raise KeyError('no row for group %02x /%d' % (e.op, e.reg))
            kind, eb = k; w = 128 if wide else 64; dst = xmm(d, e.rm) if wide else mm(d, e.rm); c = ZeroExt(64 - 8, e.imm)
            if kind in ('bsrl', 'bsll'):                                   # whole-register byte shifts; count > 15 clears (SDM 2B:PSLLDQ "If the value specified by the count operand is greater than 15")
                n = ZeroExt(120, e.imm) * K(8, 128)
                big = UGT(e.imm, K(15, 8))
                out = If(big, K(0, 128), (dst << n) if kind == 'bsll' else LShR(dst, n))
            else:
                f = sh(kind, eb); out = join([f(x, c) for x in lanes(dst, eb)])
            return Res(put_xmm(d, e.rm, out) if wide else put_mm(d, e.rm, out))
        return fn
    for op in (0x71, 0x72, 0x73):
        row('', op, 'psh-imm', [], form='r', imm=True)(shift_imm(False))
        row('66', op, 'psh-imm', [], form='r', imm=True)(shift_imm(True))

    # ---- multiplies ----------------------------------------------------------------------------------------------------------------------------------------------
    # SDM 2B:PMULLW (quote: simdsdm_cites.txt); ACL2 pmul.lisp x86-pmullw-mmx
    mulw = lambda hi, sg: (lambda x, y: Extract(31 if hi else 15, 16 if hi else 0, (SignExt(16, x) * SignExt(16, y)) if sg else (ZeroExt(16, x) * ZeroExt(16, y))))
    both('pmullw', lambda x, y: bmap(16, x, y, mulw(False, True)), 0xD5, None, acl=('pmul.lisp', 'x86-pmullw-mmx', '(result (simd-mul-low-spec (* 8 operand-size) 16 src1 src2))'))
    # SDM 2B:PMULHW (quote: simdsdm_cites.txt); ACL2 x86-pmulhw-mmx
    both('pmulhw', lambda x, y: bmap(16, x, y, mulw(True, True)), 0xE5, None, acl=('pmul.lisp', 'x86-pmulhw-mmx', '(result (simd-mul-signed-high-spec (* 8 operand-size) 16 src1 src2))'))
    # SDM 2B:PMULHUW (quote: simdsdm_cites.txt)
    both('pmulhuw', lambda x, y: bmap(16, x, y, mulw(True, False)), 0xE4, None)
    # SDM 2B:PMULUDQ (quote: simdsdm_cites.txt)

    def muludq(x, y):
        return join([ZeroExt(32, a) * ZeroExt(32, b) for a, b in zip(lanes(x, 32)[0::2], lanes(y, 32)[0::2])])
    both('pmuludq', muludq, 0xF4, None)

    def maddwd(x, y):
        a, b = lanes(x, 16), lanes(y, 16)
        return join([SignExt(16, a[2 * i]) * SignExt(16, b[2 * i]) + SignExt(16, a[2 * i + 1]) * SignExt(16, b[2 * i + 1]) for i in range(len(a) // 2)])
    # SDM 2B:PMADDWD (quote: simdsdm_cites.txt); ACL2 pmadd.lisp x86-pmaddwd-mmx
    both('pmaddwd', maddwd, 0xF5, None, acl=('pmadd.lisp', 'x86-pmaddwd-mmx', '(result (simd-muladd-spec (* 8 operand-size) 16 src1 src2))'))

    # ---- pack: the destination lanes fill the low half, the source lanes the high half --------------------------------------------------------------------------
    def sat_s(eb):                                    # signed eb -> eb/2, signed saturation
        h = eb // 2; hi, lo = K((1 << (h - 1)) - 1, eb), K(-(1 << (h - 1)) & ((1 << eb) - 1), eb)
        return lambda v: Extract(h - 1, 0, If(v > hi, hi, If(v < lo, lo, v)))

    def sat_u(eb):                                    # signed eb -> unsigned eb/2
        h = eb // 2; hi = K((1 << h) - 1, eb)
        return lambda v: Extract(h - 1, 0, If(v > hi, hi, If(v < K(0, eb), K(0, eb), v)))

    def pack(eb, sat):
        return lambda x, y: join([sat(v) for v in lanes(x, eb) + lanes(y, eb)])
    # SDM 2B:PACKSSWB (quote: simdsdm_cites.txt); ACL2 pack.lisp
    both('packsswb', pack(16, sat_s(16)), 0x63, None, acl=('pack.lisp', 'x86-packsswb-mmx', '(result (packss-spec 64 16 src1 src2))'))
    both('packssdw', pack(32, sat_s(32)), 0x6B, None, acl=('pack.lisp', 'x86-packssdw-mmx', '(result (packss-spec 64 32 src1 src2))'))
    both('packuswb', pack(16, sat_u(16)), 0x67, None, acl=('pack.lisp', 'x86-packuswb-mmx', '(result (packus-spec 64 16 src1 src2))'))

    # ---- averages, min / max, sad ---------------------------------------------------------------------------------------------------------------------------------
    # SDM 2B:PAVGB (quote: simdsdm_cites.txt)
    avg = lambda eb: (lambda x, y: Extract(eb, 1, ZeroExt(1, x) + ZeroExt(1, y) + K(1, eb + 1)))
    both('pavgb', lambda x, y: bmap(8, x, y, avg(8)), 0xE0, None)
    both('pavgw', lambda x, y: bmap(16, x, y, avg(16)), 0xE3, None)
    # SDM 2B:PMAXSW (quote: simdsdm_cites.txt)
    both('pmaxsw', lambda x, y: bmap(16, x, y, lambda a, b: If(a > b, a, b)), 0xEE, None)
    both('pminsw', lambda x, y: bmap(16, x, y, lambda a, b: If(a < b, a, b)), 0xEA, None)
    # SDM 2B:PMAXUB (quote: simdsdm_cites.txt)
    both('pmaxub', lambda x, y: bmap(8, x, y, lambda a, b: If(UGT(a, b), a, b)), 0xDE, None)
    both('pminub', lambda x, y: bmap(8, x, y, lambda a, b: If(ULT(a, b), a, b)), 0xDA, None)

    def sad(x, y):
        out = []
        for qx, qy in zip(lanes(x, 64), lanes(y, 64)):
            s = K(0, 16)
            for a, b in zip(lanes(qx, 8), lanes(qy, 8)):
                s = s + If(UGE(a, b), ZeroExt(8, a - b), ZeroExt(8, b - a))
            out.append(ZeroExt(48, s))
        return join(out)
    # SDM 2B:PSADBW (quote: simdsdm_cites.txt)
    both('psadbw', sad, 0xF6, None)
    # SDM 2B:PSUBQ (quote: simdsdm_cites.txt)
    row('', 0xFB, 'psubq', [], mw=64)(lambda e, d, m, a, g: _w(e, a, m, d, False, lambda x, y: x - y))

    # ---- moves with the general registers ---------------------------------------------------------------------------------------------------------------------------
    # SDM 2B:PMOVMSKB (quote: simdsdm_cites.txt)
    row('', 0xD7, 'pmovmskb', [], form='r')(S._movmsk(8, False, False))
    row('66', 0xD7, 'pmovmskb', [], form='r')(S._movmsk(8, True, True))

    def pextrw(wide):
        def f(e, d, m, a, g):
            v = xmm(d, e.rm) if wide else mm(d, e.rm); n = 8 if wide else 4
            sel = ZeroExt(32 - 8, e.imm) & K(n - 1, 32)                     # SDM 2B:PEXTRW: the word selected by the low bits of imm8 (3 for mm, 7 for xmm)
            w = Extract(15, 0, LShR(v, ZeroExt(v.size() - 32, sel) * K(16, v.size())))
            return Res(d if wide else S._mmx_state(d), gpr=(e.reg, ZeroExt(16, w)))
        return f
    # SDM 2B:PEXTRW (quote: simdsdm_cites.txt)
    row('', 0xC5, 'pextrw', [], form='r', imm=True)(pextrw(False))
    row('66', 0xC5, 'pextrw', [], form='r', imm=True)(pextrw(True))

    def pinsrw(wide):
        def f(e, d, m, a, g):
            w16 = m if e.mem else Extract(15, 0, g[e.rm]); n = 8 if wide else 4
            dst = xmm(d, e.reg) if wide else mm(d, e.reg); size = 128 if wide else 64
            sel = ZeroExt(32 - 8, e.imm) & K(n - 1, 32)
            sh_ = ZeroExt(size - 32, sel) * K(16, size)
            mask = K(0xFFFF, size) << sh_
            out = (dst & ~mask) | (ZeroExt(size - 16, w16) << sh_)
            return Res(put_xmm(d, e.reg, out) if wide else put_mm(d, e.reg, out))
        return f
    # SDM 2B:PINSRW (quote: simdsdm_cites.txt)
    row('', 0xC4, 'pinsrw', [], mw=16, al=False, imm=True)(pinsrw(False))
    row('66', 0xC4, 'pinsrw', [], mw=16, al=False, imm=True)(pinsrw(True))

    # ---- state / hints ---------------------------------------------------------------------------------------------------------------------------------------------
    # SDM 2A:EMMS (quote: simdsdm_cites.txt); ACL2 emms.lisp x86-emms
    row('', 0x77, 'emms', [])(lambda e, d, m, a, g: Res(S.X.emms_effect(d)))
    # SDM 2B:MOVNTPS (quote: simdsdm_cites.txt) (the value is stored; the hint has no architectural effect; Exceptions Type 1: aligned)

    def movntps(e, d, m, a, g):
        r = Res(d, store=(a, xmm(d, e.reg), 128)); r.fault = S.misaligned(a); return r
    row('', 0x2B, 'movntps', [], form='m', mw=128)(movntps)
    # SDM 2B:PREFETCHh (quote: simdsdm_cites.txt): a hint with no architectural effect and no fault (0F 18 /0../3)

    def prefetch(e, d, m, a, g):
        if e.reg >= 4: raise KeyError('0F 18 /%d is not a prefetch row' % e.reg)
        return Res(d)
    row('', 0x18, 'prefetch', [], form='m', mw=8, al=False)(prefetch)
