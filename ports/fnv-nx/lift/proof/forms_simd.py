"""SIMD integer, bitwise and move families (MMX and SSE/SSE2), SDM Vol 2 per mnemonic.  xmm / mm operands go through forms.rd / forms.wr; every MMX
instruction gets the one MMX rule (Form.mmx) from forms.build.  Memory operands of 128 bits are two 64-bit accesses; the aligned forms fault with
kind 7 (access violation: Windows reports a #GP for a misaligned 16-byte operand as STATUS_ACCESS_VIOLATION) when the address is not 16-byte aligned."""
from fnvlift.dsl import C, V, ashr, concat, eq, extract, ite, lshr, shl, slt, sext, trunc, ult, zext
from proof.forms import ea, handler, isvec, rd, wr

def lanes(v, e): return [extract(v, (i + 1) * e - 1, i * e) for i in range(v.w // e)]       # low lane first
def join(ls):
    r = ls[-1]
    for x in reversed(ls[:-1]): r = concat(r, x)
    return r
def mapl(e, a, b, f): return join([f(x, y) for x, y in zip(lanes(a, e), lanes(b, e))])
def align(c, op, n=16):
    if op.k == 'm' and op.w == 128: c.F.fault(7, ~eq(ea(op) & C(n - 1, 32), 0))
def src(c, op=None, w=None):
    op = op or c.ops[1]; align(c, op) if c.aligned else None; return rd(op, w)
def two(c, f, aligned=True):           # dst = f(dst, src) over the operand width
    c.aligned = aligned; d = c.ops[0]; wr(c.F, d, f(rd(d), src(c)))

def f_lane(e, g): return lambda a, b: mapl(e, a, b, g)
SZ = {'b': 8, 'w': 16, 'd': 32, 'q': 64}
@handler('pand', 'andps', 'andpd')
def _and(c): two(c, lambda a, b: a & b)
@handler('pandn', 'andnps', 'andnpd')
def _andn(c): two(c, lambda a, b: ~a & b)
@handler('por', 'orps', 'orpd')
def _or(c): two(c, lambda a, b: a | b)
@handler('pxor', 'xorps', 'xorpd')
def _xor(c): two(c, lambda a, b: a ^ b)
def arith(sfx, g):
    for k, e in SZ.items():
        if k == 'q' and sfx not in ('padd', 'psub'): continue
        handler(sfx + k)(lambda c, e=e: two(c, f_lane(e, g)))
arith('padd', lambda x, y: x + y); arith('psub', lambda x, y: x - y)
def smin(e, w=None): return -(1 << (e - 1)) & ((1 << (w or e)) - 1)      # -2^(e-1) as a w-bit pattern
def smax(e): return (1 << (e - 1)) - 1
def satS(e, op):
    def g(x, y):
        w = e + 1; r = op(sext(x, w), sext(y, w)); hi, lo = C(smax(e), w), C(smin(e, w), w)
        return trunc(ite(slt(hi, r), hi, ite(slt(r, lo), lo, r)), e)
    return g
# unsigned saturation: sum above 2^e-1 -> 2^e-1; difference below 0 -> 0 (the borrow bit of the e+1-bit result)
def satU(e, add):
    def g(x, y):
        w = e + 1
        if add:
            r = zext(x, w) + zext(y, w); return trunc(ite(eq(r >> C(e, w), 0), r, C((1 << e) - 1, w)), e)
        return ite(ult(x, y), C(0, e), x - y)
    return g
for k, e in (('b', 8), ('w', 16)):
    handler('padds' + k)(lambda c, e=e: two(c, f_lane(e, satS(e, lambda a, b: a + b))))
    handler('psubs' + k)(lambda c, e=e: two(c, f_lane(e, satS(e, lambda a, b: a - b))))
    handler('paddus' + k)(lambda c, e=e: two(c, f_lane(e, satU(e, True))))
    handler('psubus' + k)(lambda c, e=e: two(c, f_lane(e, satU(e, False))))
for k, e in (('b', 8), ('w', 16), ('d', 32)):
    handler('pcmpeq' + k)(lambda c, e=e: two(c, f_lane(e, lambda x, y: ite(eq(x, y), C(-1 & ((1 << e) - 1), e), C(0, e)))))
    handler('pcmpgt' + k)(lambda c, e=e: two(c, f_lane(e, lambda x, y: ite(slt(y, x), C(-1 & ((1 << e) - 1), e), C(0, e)))))
@handler('pminub')
def _pminub(c): two(c, f_lane(8, lambda x, y: ite(ult(y, x), y, x)))
@handler('pmaxub')
def _pmaxub(c): two(c, f_lane(8, lambda x, y: ite(ult(x, y), y, x)))
@handler('pminsw')
def _pminsw(c): two(c, f_lane(16, lambda x, y: ite(slt(y, x), y, x)))
@handler('pmaxsw')
def _pmaxsw(c): two(c, f_lane(16, lambda x, y: ite(slt(x, y), y, x)))
@handler('pavgb')
def _pavgb(c): two(c, f_lane(8, lambda x, y: trunc((zext(x, 9) + zext(y, 9) + C(1, 9)) >> C(1, 9), 8)))
@handler('pavgw')
def _pavgw(c): two(c, f_lane(16, lambda x, y: trunc((zext(x, 17) + zext(y, 17) + C(1, 17)) >> C(1, 17), 16)))
@handler('pmullw')
def _pmullw(c): two(c, f_lane(16, lambda x, y: x * y))
@handler('pmulhw')
def _pmulhw(c): two(c, f_lane(16, lambda x, y: extract(sext(x, 32) * sext(y, 32), 31, 16)))
@handler('pmulhuw')
def _pmulhuw(c): two(c, f_lane(16, lambda x, y: extract(zext(x, 32) * zext(y, 32), 31, 16)))
@handler('pmuludq')
def _pmuludq(c): two(c, lambda a, b: join([zext(extract(x, 31, 0), 64) * zext(extract(y, 31, 0), 64) for x, y in zip(lanes(a, 64), lanes(b, 64))]))
@handler('pmaddwd')
def _pmaddwd(c):
    def g(a, b):
        p = [sext(x, 32) * sext(y, 32) for x, y in zip(lanes(a, 16), lanes(b, 16))]
        return join([p[i] + p[i + 1] for i in range(0, len(p), 2)])
    two(c, g)
@handler('psadbw')
def _psadbw(c):
    def g(a, b):
        d = [ite(ult(x, y), y - x, x - y) for x, y in zip(lanes(a, 8), lanes(b, 8))]
        out = []
        for h in range(0, len(d), 8):
            s = zext(d[h], 16)
            for x in d[h + 1:h + 8]: s = s + zext(x, 16)
            out.append(zext(s, 64))
        return join(out)
    two(c, g)

def unpack(c, e, hi):
    c.aligned = True; d, s = c.ops; a = rd(d)
    n = d.w // 2                                      # a half of the destination holds the lanes taken: low half (hi=False) or high half
    b = src(c, s, None if (d.w == 128 or hi) else 32)
    if d.w == 64 and not hi: b = zext(b, 64) if b.w == 32 else b
    la, lb = lanes(a, e), lanes(b, e)
    off = (len(la) // 2) if hi else 0
    wr(c.F, d, join([x for i in range(len(la) // 2) for x in (la[off + i], lb[off + i])]))
for k, e in (('bw', 8), ('wd', 16), ('dq', 32), ('qdq', 64)):
    handler('punpckl' + k)(lambda c, e=e: unpack(c, e, False)); handler('punpckh' + k)(lambda c, e=e: unpack(c, e, True))
def pack(e, signed):
    def g(a, b):
        r = []
        for x in lanes(a, e) + lanes(b, e):
            if signed:
                t = e // 2; lo, hi = C(smin(t, e), e), C(smax(t), e)
                r.append(trunc(ite(slt(hi, x), hi, ite(slt(x, lo), lo, x)), t))
            else:
                t = e // 2; hi = C((1 << t) - 1, e)
                r.append(trunc(ite(slt(x, C(0, e)), C(0, e), ite(ult(hi, x), hi, x)), t))
        return join(r)
    return g
handler('packsswb')(lambda c: two(c, pack(16, True))); handler('packssdw')(lambda c: two(c, pack(32, True))); handler('packuswb')(lambda c: two(c, pack(16, False)))
def shift(e, kind):
    def g(c):
        c.aligned = True; d, s = c.ops; a = rd(d)
        if s.k == 'i': n = C(s.val & 0xff, 64)
        else: n = rd(s, 64)
        big = ~ult(n, C(e, 64)); n_ = trunc(n, e)
        ls = lanes(a, e)
        if kind == 'l': f = lambda x: ite(big, C(0, e), shl(x, n_))
        elif kind == 'r': f = lambda x: ite(big, C(0, e), lshr(x, n_))
        else: f = lambda x: ashr(x, ite(big, C(e - 1, e), n_))
        wr(c.F, d, join([f(x) for x in ls]))
    return g
for k, e in (('w', 16), ('d', 32), ('q', 64)):
    handler('psll' + k)(shift(e, 'l')); handler('psrl' + k)(shift(e, 'r'))
for k, e in (('w', 16), ('d', 32)): handler('psra' + k)(shift(e, 'a'))
@handler('pslldq')
def _pslldq(c):
    n = min(c.ops[1].val & 0xff, 16); d = c.ops[0]; a = rd(d); wr(c.F, d, C(0, 128) if n >= 16 else shl(a, C(8 * n, 128)))
@handler('psrldq')
def _psrldq(c):
    n = min(c.ops[1].val & 0xff, 16); d = c.ops[0]; a = rd(d); wr(c.F, d, C(0, 128) if n >= 16 else lshr(a, C(8 * n, 128)))
def pick(v, e, sel): # lane `sel` (a 2-bit constant) of v
    return lanes(v, e)[sel]
@handler('pshufd')
def _pshufd(c):
    c.aligned = True; d, s, i = c.ops; v = src(c, s); im = i.val & 0xff
    wr(c.F, d, join([pick(v, 32, (im >> (2 * k)) & 3) for k in range(4)]))
@handler('pshuflw')
def _pshuflw(c):
    c.aligned = True; d, s, i = c.ops; v = src(c, s); im = i.val & 0xff; L = lanes(v, 16)
    wr(c.F, d, join([L[(im >> (2 * k)) & 3] for k in range(4)] + L[4:]))
@handler('pshufhw')
def _pshufhw(c):
    c.aligned = True; d, s, i = c.ops; v = src(c, s); im = i.val & 0xff; L = lanes(v, 16)
    wr(c.F, d, join(L[:4] + [L[4 + ((im >> (2 * k)) & 3)] for k in range(4)]))
@handler('pshufw')
def _pshufw(c):
    c.aligned = True; d, s, i = c.ops; v = src(c, s); im = i.val & 0xff
    wr(c.F, d, join([pick(v, 16, (im >> (2 * k)) & 3) for k in range(4)]))
@handler('shufps')
def _shufps(c):
    c.aligned = True; d, s, i = c.ops; a = rd(d); b = src(c, s); im = i.val & 0xff
    wr(c.F, d, join([pick(a, 32, im & 3), pick(a, 32, (im >> 2) & 3), pick(b, 32, (im >> 4) & 3), pick(b, 32, (im >> 6) & 3)]))
@handler('shufpd')
def _shufpd(c):
    c.aligned = True; d, s, i = c.ops; a = rd(d); b = src(c, s); im = i.val & 0xff
    wr(c.F, d, join([pick(a, 64, im & 1), pick(b, 64, (im >> 1) & 1)]))
@handler('unpcklps')
def _unpcklps(c): unpack(c, 32, False)
@handler('unpckhps')
def _unpckhps(c): unpack(c, 32, True)
@handler('unpcklpd')
def _unpcklpd(c): unpack(c, 64, False)
@handler('unpckhpd')
def _unpckhpd(c): unpack(c, 64, True)
@handler('pmovmskb')
def _pmovmskb(c):
    d, s = c.ops; v = rd(s); L = lanes(v, 8); r = zext(extract(L[0], 7, 7), 32)
    for i, x in enumerate(L[1:], 1): r = r | shl(zext(extract(x, 7, 7), 32), C(i, 32))
    wr(c.F, d, r)
@handler('movmskps')
def _movmskps(c):
    d, s = c.ops; L = lanes(rd(s), 32); r = zext(extract(L[0], 31, 31), 32)
    for i, x in enumerate(L[1:], 1): r = r | shl(zext(extract(x, 31, 31), 32), C(i, 32))
    wr(c.F, d, r)
@handler('movmskpd')
def _movmskpd(c):
    d, s = c.ops; L = lanes(rd(s), 64); wr(c.F, d, zext(extract(L[0], 63, 63), 32) | shl(zext(extract(L[1], 63, 63), 32), C(1, 32)))
@handler('pextrw')
def _pextrw(c):
    d, s, i = c.ops; n = (i.val & 7) if s.w == 128 else (i.val & 3); wr(c.F, d, zext(lanes(rd(s), 16)[n], 32))
@handler('pinsrw')
def _pinsrw(c):
    d, s, i = c.ops; a = rd(d); n = (i.val & 7) if d.w == 128 else (i.val & 3)
    v = trunc(rd(s, 32), 16) if s.k == 'r' else rd(s, 16)
    L = lanes(a, 16); L[n] = v; wr(c.F, d, join(L))
# ---- moves
@handler('movdqa', 'movaps', 'movapd')
def _movdqa(c):
    c.aligned = True; d, s = c.ops; v = src(c, s)
    if d.k == 'm': align(c, d)
    wr(c.F, d, v)
@handler('movdqu', 'movups', 'movupd')
def _movdqu(c): d, s = c.ops; wr(c.F, d, rd(s))
@handler('movq')
def _movq(c):
    d, s = c.ops
    if isvec(d) and d.name.startswith('xmm'): wr(c.F, d, zext(rd(s, 64), 128))      # movq xmm, xmm/m64 zero-extends
    else: wr(c.F, d, rd(s, 64))
@handler('movd')
def _movd(c):
    d, s = c.ops; v = rd(s, 32)
    if isvec(d): wr(c.F, d, zext(v, d.w))
    else: wr(c.F, d, v)
@handler('movlps', 'movlpd')
def _movlps(c):
    d, s = c.ops
    if d.k == 'm': wr(c.F, d, rd(s, 64))
    else: wr(c.F, d, concat(extract(rd(d), 127, 64), rd(s, 64)))
@handler('movhps', 'movhpd')
def _movhps(c):
    d, s = c.ops
    if d.k == 'm': wr(c.F, d, extract(rd(s), 127, 64))
    else: wr(c.F, d, concat(rd(s, 64), extract(rd(d), 63, 0)))
@handler('movhlps')
def _movhlps(c): d, s = c.ops; wr(c.F, d, concat(extract(rd(d), 127, 64), extract(rd(s), 127, 64)))
@handler('movlhps')
def _movlhps(c): d, s = c.ops; wr(c.F, d, concat(extract(rd(s), 63, 0), extract(rd(d), 63, 0)))
@handler('movss')
def _movss(c):
    d, s = c.ops
    if d.k == 'm': wr(c.F, d, rd(s, 32))
    elif s.k == 'm': wr(c.F, d, zext(rd(s, 32), 128))
    else: wr(c.F, d, concat(extract(rd(d), 127, 32), rd(s, 32)))
def movsd_sse(c):
    d, s = c.ops
    if d.k == 'm': wr(c.F, d, rd(s, 64))
    elif s.k == 'm': wr(c.F, d, zext(rd(s, 64), 128))
    else: wr(c.F, d, concat(extract(rd(d), 127, 64), rd(s, 64)))
