#!/usr/bin/env python3
"""Tests for fpbound (V-FP rule). Plain script: one PASS/FAIL line per case, exit 0 on pass."""
import io
import math
import os
import struct
import sys
import tokenize
from fractions import Fraction as Fr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import fpbound as fb  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

FAILS = testcase.FAILED
class Rng:
    """Fixed LCG (no random module)."""
    def __init__(self, seed):
        self.s = seed

    def n(self, k):
        self.s = (self.s * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return (self.s >> 33) % k


def round_to(w, p, emin):
    """Round-to-nearest-even of a Fraction to p significant bits, exponent floor emin (subnormals)."""
    if w == 0:
        return Fr(0)
    a = abs(w)
    k = a.numerator.bit_length() - a.denominator.bit_length()
    while Fr(2) ** k > a:
        k -= 1
    while Fr(2) ** (k + 1) <= a:
        k += 1
    q = Fr(2) ** (max(k, emin) - p + 1)
    m = math.floor(a / q)
    rem = a / q - m
    if rem > Fr(1, 2) or (rem == Fr(1, 2) and m % 2 == 1):
        m += 1
    return m * q if w > 0 else -(m * q)


def f32val(b):
    return Fr(struct.unpack("<f", struct.pack("<I", b))[0])


# ---------------------------------------------------------------- exact intervals
S = 128


def isqrt_iv(a):
    lo = Fr(math.isqrt(math.floor(a * 4 ** S)), 2 ** S)
    return lo, (lo if lo * lo == a else lo + Fr(1, 2 ** S))


def iv_op(op, a, b):
    (l1, h1), (l2, h2) = a, (b or (0, 0))
    if op == "add":
        return l1 + l2, h1 + h2
    if op == "sub":
        return l1 - h2, h1 - l2
    if op == "mul":
        c = [l1 * l2, l1 * h2, h1 * l2, h1 * h2]
        return min(c), max(c)
    if op == "div":
        c = [l1 / l2, l1 / h2, h1 / l2, h1 / h2]
        return min(c), max(c)
    if op == "sqrt":
        return isqrt_iv(l1)[0], isqrt_iv(h1)[1]
    if op == "neg":
        return -h1, -l1
    if op == "abs":
        return (l1, h1) if l1 >= 0 else ((-h1, -l1) if h1 <= 0 else (Fr(0), max(-l1, h1)))
    if op == "cube":
        return l1 ** 3, h1 ** 3
    return l1, h1  # st32, st64: identity on the reals


OPS = ["add", "sub", "mul", "div", "sqrt", "neg", "abs", "st32", "st64", "cube"]


class Chain:
    pass


def gen_chain(rng, lo_exp, span, nmax):
    ch = Chain()
    ch.bits = []
    for _ in range(3):
        if rng.n(25) == 0:
            ch.bits.append(0)
        else:
            ch.bits.append((rng.n(2) << 31) | ((lo_exp + rng.n(span)) << 23) | rng.n(1 << 23))
    ch.iv = [(f32val(b), f32val(b)) for b in ch.bits]
    ch.ops = []
    for _ in range(1 + rng.n(nmax)):
        for _try in range(30):
            op = OPS[rng.n(len(OPS))]
            i, j = rng.n(len(ch.iv)), rng.n(len(ch.iv))
            if op == "div" and ch.iv[j][0] <= 0 <= ch.iv[j][1]:
                continue
            if op == "sqrt" and ch.iv[i][0] <= 0:
                continue
            break
        else:
            op, i, j = "add", 0, 1
        ch.ops.append((op, i, j, rng.n(2001) - 1000))
        ch.iv.append(iv_op(op, ch.iv[i], ch.iv[j]))
    return ch


def run(ch, f, bits=None):
    """Run the chain with module ops at format f. Returns dict(vs, ok, viol, sim, ratio)."""
    vs = [fb.V.from_f32bits(b) for b in (bits or ch.bits)]
    res = dict(vs=vs, ok=True, viol=0, sim=0, ratio=None, sub=0)
    for k, (op, i, j, dn) in enumerate(ch.ops):
        x, y = vs[i], vs[j]
        w = None
        if op in ("add", "sub", "mul", "div"):
            r = getattr(fb, op)(x, y, f)
            if r.kind == "f":
                w = {"add": lambda: x.val + y.val, "sub": lambda: x.val - y.val,
                     "mul": lambda: x.val * y.val, "div": lambda: x.val / y.val}[op]()
        elif op == "sqrt":
            r = fb.sqrt(x, f)
        elif op == "neg":
            r = fb.neg(x)
        elif op == "abs":
            r = fb.abs_(x)
        elif op in ("st32", "st64"):
            r = fb.store(x, fb.F32 if op == "st32" else fb.F64)
        else:
            d = Fr(dn, 2 ** (f.p + 4))
            z = round_to(x.val ** 3 * (1 + d), f.p, f.emin)
            r = fb.transc(x, z, Fr(1, 2 ** (f.p - 7)), 3 * (abs(x.val) + x.err) ** 2, f)
        if w is not None and round_to(w, f.p, f.emin) != r.val:
            res["sim"] += 1
        vs.append(r)
        if r.kind == "u":
            res["ok"] = False
            break
        lo, hi = ch.iv[len(vs) - 1]
        if not (r.val - r.err <= lo and hi <= r.val + r.err):
            res["viol"] += 1
        if 0 < abs(r.val) < Fr(2) ** f.emin:
            res["sub"] += 1
        if k == len(ch.ops) - 1:
            act = abs(r.val - (lo + hi) / 2)
            if act > 0:
                res["ratio"] = float(r.err / act)
    return res


def median(a):
    a = sorted(a)
    return a[len(a) // 2] if a else float("nan")


def u(f):
    return Fr(1, 2 ** f.p)


# ---------------------------------------------------------------- (1) per-operation hand vectors
def t_hand():
    F = fb.F64
    v = fb.V.fin
    # add: 1 + 2^-24 in binary32 is a tie, rounds to 1; r = u*1
    r = fb.add(v(Fr(1)), v(Fr(1, 2 ** 24)), fb.F32)
    case("hand add f32 tie", r.val == 1 and r.err == Fr(1, 2 ** 24))
    r = fb.add(v(Fr(1), Fr(1, 8)), v(Fr(2), Fr(1, 8)), F)
    case("hand add exact", r.val == 3 and r.err == Fr(1, 4))
    r = fb.sub(v(Fr(5), Fr(1, 8)), v(Fr(2), Fr(1, 4)), F)
    case("hand sub exact", r.val == 3 and r.err == Fr(3, 8))
    x = Fr(1) + Fr(1, 2 ** 23)
    r = fb.mul(v(x), v(x), fb.F32)
    z = round_to(x * x, 24, -126)
    case("hand mul f32 rounding", r.val == z and r.err == z * Fr(1, 2 ** 24) and abs(z - x * x) <= r.err)
    r = fb.mul(v(Fr(3), Fr(1, 100)), v(Fr(5), Fr(2, 100)), F)
    case("hand mul errs", r.val == 15 and r.err == Fr(1102, 10000))
    r = fb.div(v(Fr(6), Fr(1, 10)), v(Fr(3), Fr(2, 10)), F)
    case("hand div errs", r.val == 2 and r.err == Fr(5, 28))
    r = fb.sqrt(v(Fr(4), Fr(1)), F)
    case("hand sqrt errs", r.val == 2 and r.err * r.err * 12 >= 1 and r.err < Fr(2887, 10000))
    r = fb.sqrt(v(Fr(2)), fb.F32)
    case("hand sqrt(2) f32", r.val == round_to(Fr(math.isqrt(2 * 4 ** 80), 2 ** 80), 24, -126) and r.err > 0)
    r = fb.neg(v(Fr(3), Fr(1, 8)))
    q = fb.abs_(r)
    case("hand neg/abs", r.val == -3 and r.err == Fr(1, 8) and q.val == 3 and q.err == Fr(1, 8))
    r = fb.store(v(Fr(1) + Fr(1, 2 ** 30), Fr(1, 16)), fb.F32)
    case("hand store narrower", r.val == 1 and r.err == Fr(1, 16) + Fr(1, 2 ** 24))
    r = fb.scale2(v(Fr(3), Fr(1, 8)), -2, F)
    case("hand scale2", r.val == Fr(3, 4) and r.err == Fr(1, 32))
    r = fb.transc(v(Fr(2), Fr(1, 8)), Fr(8), Fr(1, 2 ** 10), Fr(12), F)
    case("hand transc", r.val == 8 and r.err >= Fr(12, 8) + Fr(8, 2 ** 10))
    x = fb.V.fin(Fr(1, 3))
    case("hand constructors",
         fb.V.from_f32bits(0x3F800000).val == 1 and fb.V.from_f64bits(0x3FF0000000000000).val == 1
         and fb.V.from_x87(0x3FFF, 1 << 63).val == 1 and fb.V.from_x87(0xBFFF, 1 << 63).val == -1
         and fb.V.from_x87(0, 1).val == Fr(1, 2 ** 16445) and fb.V.from_x87(0x7FFF, 1 << 63).kind == "+"
         and fb.V.from_x87(0xFFFF, 1 << 63).kind == "-" and fb.V.from_x87(0x7FFF, 3 << 62).kind == "n"
         and fb.V.from_f32bits(0x7F800000).kind == "+" and fb.V.from_f64bits(0x7FF8000000000001).kind == "n"
         and x.err == 0)


# ---------------------------------------------------------------- (1,2,7) random chains
def t_random():
    rng = Rng(12345)
    chains = [gen_chain(rng, 124, 7, 12) for _ in range(2000)]
    precs = [fb.F32, fb.F64, fb.X87_64]
    stats = {}
    for f in precs:
        viol = sim = unst = 0
        ratios = []
        for ch in chains:
            r = run(ch, f)
            viol += r["viol"]
            sim += r["sim"]
            unst += (not r["ok"])
            if r["ratio"]:
                ratios.append(r["ratio"])
        stats[f.p] = ratios
        case("soundness p=%d (2000 chains, %d unstable)", viol == 0,
             "violations=%d tightness_median=%.3g" % (viol, median(ratios)), key=(f.p, unst))
        case("module rounding == test round_to p=%d", sim == 0, "mismatches=%d" % sim, key=f.p)
    # (2) acceptance: f32 chain vs x87-64 chain never rejects
    cnt = {"accept": 0, "reject": 0, "unstable": 0}
    for ch in chains:
        a, b = run(ch, fb.F32), run(ch, fb.X87_64)
        if not (a["ok"] and b["ok"]):
            cnt["unstable"] += 1
        else:
            cnt[fb.verdict(a["vs"][-1], b["vs"][-1])] += 1
    case("accept rule f32 vs x87-64 never rejects", cnt["reject"] == 0 and cnt["accept"] > 1000, str(cnt))
    # perturbed by 2 ulps of binary32 at input 0
    rej = acc = 0
    for ch in chains:
        pb = [ch.bits[0] + 2] + ch.bits[1:]
        a, b = run(ch, fb.F32, pb), run(ch, fb.X87_64)
        if a["ok"] and b["ok"]:
            if fb.verdict(a["vs"][-1], b["vs"][-1]) == "reject":
                rej += 1
            else:
                acc += 1
    pct = 100.0 * rej / max(1, rej + acc)
    case("sensitivity: 2-ulp input perturbation rejected", rej > 0, "rejected=%.1f%% of %d stable" % (pct, rej + acc))
    # (7) subnormals in binary32
    sub = viol = unst = 0
    rng = Rng(777)
    for _ in range(1500):
        ch = gen_chain(rng, 55, 20, 8)
        r = run(ch, fb.F32)
        sub += r["sub"]
        viol += r["viol"]
        unst += (not r["ok"])
    case("subnormal results sound (f32)", viol == 0 and sub > 100, "subnormal_results=%d violations=%d" % (sub, viol))


# ---------------------------------------------------------------- (3) single op f32 vs f32
def t_single():
    rng = Rng(99)
    bad = nrej = 0
    for _ in range(2000):
        a = (rng.n(2) << 31) | ((120 + rng.n(14)) << 23) | rng.n(1 << 23)
        b = (rng.n(2) << 31) | ((120 + rng.n(14)) << 23) | rng.n(1 << 23)
        x, y = fb.V.from_f32bits(a), fb.V.from_f32bits(b)
        fa, fbv = struct.unpack("<f", struct.pack("<I", a))[0], struct.unpack("<f", struct.pack("<I", b))[0]
        for op, py in (("add", fa + fbv), ("sub", fa - fbv), ("mul", fa * fbv), ("div", fa / fbv)):
            ours = getattr(fb, op)(x, y, fb.F32)
            ref = struct.unpack("<f", struct.pack("<f", py))[0]
            if ours.val != Fr(ref):
                bad += 1
            if fb.verdict(ours, fb.V.from_float(ref)) != "accept":
                nrej += 1
    case("single op f32==f32 bit-equal, accepted", bad == 0 and nrej == 0, "bad=%d notaccepted=%d" % (bad, nrej))


# ---------------------------------------------------------------- (4) stability truth tables
def t_stable():
    sc, st, sr = fb.stable_compare, fb.stable_to_int, fb.stable_round
    h, q = Fr(1, 2), Fr(1, 4)
    tab = [
        (sc(1, 0, 2, 0, "lt"), True), (sc(2, 0, 2, 0, "lt"), True), (sc(2, Fr(1, 10), 2, 0, "lt"), False),
        (sc(1, h, 2, q, "lt"), True), (sc(1, h, 2, h, "lt"), False),
        (sc(2, 0, 2, 0, "le"), True), (sc(1, h, 2, h, "le"), True), (sc(3, h, 2, h, "le"), False),
        (sc(3, q, 2, h, "le"), True),
        (sc(2, 0, 2, 0, "eq"), True), (sc(2, 0, 2, Fr(1, 10), "eq"), False), (sc(2, 0, 3, 0, "eq"), True),
        (sc(2, Fr(1, 10), 2, Fr(1, 10), "eq"), False), (sc(1, Fr(1, 10), 3, Fr(1, 10), "eq"), True),
        (sc(1, 0, 2, 0, "un"), True), (sc(1, None, 2, 0, "lt"), False), (sc(float("nan"), 0, 2, 0, "un"), True),
        (st(Fr(5, 2), 0, "trunc"), True), (st(3, 0, "trunc"), True), (st(3, Fr(1, 10), "trunc"), False),
        (st(Fr(5, 2), q, "trunc"), True), (st(Fr(5, 2), 0, "nearest"), True),
        (st(Fr(5, 2), Fr(1, 10), "nearest"), False), (st(Fr(1, 4), Fr(1, 10), "nearest"), True),
        (st(2, 0, "floor"), True), (st(2, Fr(1, 10), "floor"), False), (st(2, Fr(1, 10), "ceil"), False),
        (st(Fr(3, 2), q, "floor"), True), (st(Fr(3, 2), q, "ceil"), True),
        (st(2 ** 31 - 1, 0, "trunc"), True), (st(2 ** 31, 0, "trunc"), False),
        (st(Fr(2 ** 31 - 1) + q, Fr(1, 8), "trunc"), True), (st(Fr(2 ** 31) - h, q, "nearest"), False),
        (st(-2 ** 31, 0, "trunc"), True), (st(-2 ** 31 - 1, 0, "trunc"), False),
        (st(2 ** 31, 0, "trunc", 64), True), (st(2 ** 63, 0, "trunc", 64), False),
        (st(float("nan"), 0, "trunc"), False), (st(1, None, "trunc"), False),
        (sr(Fr(1) + Fr(1, 2 ** 24), 0, fb.F32), True), (sr(Fr(1) + Fr(1, 2 ** 24), Fr(1, 2 ** 40), fb.F32), False),
        (sr(Fr(1) + Fr(1, 2 ** 25), Fr(1, 2 ** 26), fb.F32), True), (sr(1, Fr(1, 2 ** 30), fb.F32), True),
        (sr(Fr(2 ** 128 - 2 ** 104), 0, fb.F32), True), (sr(Fr(2 ** 128 - 2 ** 104), Fr(2 ** 103), fb.F32), False),
        (sr(Fr(2 ** 128 - 2 ** 104), Fr(2 ** 102), fb.F32), True), (sr(1, None, fb.F32), False),
    ]
    bad = [i for i, (a, b) in enumerate(tab) if a is not b]
    case("stable_compare/to_int/round truth tables (%d rows)", not bad, "bad_rows=%s" % bad, key=len(tab))


# ---------------------------------------------------------------- (5) unbounded, (6) special values
def t_special():
    v, F = fb.V.fin, fb.F64
    case("div by interval containing 0 -> unbounded",
         fb.div(v(Fr(1)), v(Fr(1, 10), Fr(1, 10)), F).kind == "u" and fb.div(v(Fr(1)), v(Fr(1), Fr(2)), F).kind == "u"
         and fb.div(v(Fr(1)), v(Fr(1), Fr(1)), F).kind == "u" and fb.div(v(Fr(1)), v(Fr(0)), F).kind == "u"
         and fb.div(v(Fr(1)), v(Fr(1), Fr(1, 2)), F).kind == "f")
    case("unbounded -> unstable verdict",
         fb.verdict(fb.div(v(Fr(1)), v(Fr(0)), F), v(Fr(1))) == "unstable"
         and fb.verdict(v(Fr(1)), fb.div(v(Fr(1)), v(Fr(0)), F)) == "unstable")
    case("sqrt interval reaching below 0 -> unbounded",
         fb.sqrt(v(Fr(1), Fr(2)), F).kind == "u" and fb.sqrt(v(Fr(1), Fr(1)), F).kind == "u"
         and fb.sqrt(v(Fr(0), Fr(1, 10)), F).kind == "u" and fb.sqrt(v(Fr(-5), Fr(1)), F).kind == "n"
         and fb.sqrt(v(Fr(0)), F).val == 0 and fb.sqrt(v(Fr(0)), F).err == 0 and fb.sqrt(v(Fr(9), Fr(1)), F).kind == "f")
    nan1, nan2 = fb.V.from_f32bits(0x7FC00001), fb.V.from_f32bits(0xFFC00000)
    pinf, ninf = fb.V.from_f32bits(0x7F800000), fb.V.from_f32bits(0xFF800000)
    one = v(Fr(1))
    ver = fb.verdict
    case("NaN rules", ver(nan1, nan2) == "accept" and ver(nan1, one) == "reject" and ver(one, nan1) == "reject"
         and ver(nan1, fb.mul(v(Fr(2 ** 100)), v(Fr(2 ** 100)), fb.F32)) == "unstable")
    case("inf rules", ver(pinf, pinf) == "accept" and ver(pinf, ninf) == "reject" and ver(pinf, one) == "reject"
         and ver(one, ninf) == "reject" and ver(pinf, nan1) == "reject")
    case("inf arithmetic",
         fb.add(pinf, ninf, F).kind == "n" and fb.add(pinf, one, F).kind == "+" and fb.mul(v(Fr(0)), pinf, F).kind == "n"
         and fb.mul(v(Fr(-2)), pinf, F).kind == "-" and fb.div(one, pinf, F).val == 0 and fb.div(pinf, pinf, F).kind == "n"
         and fb.sqrt(ninf, F).kind == "n" and fb.mul(v(Fr(0), Fr(1)), pinf, F).kind == "u" and fb.add(nan1, one, F).kind == "n")
    case("overflow interval -> unstable",
         fb.mul(v(Fr(2 ** 100)), v(Fr(2 ** 100)), fb.F32).kind == "u"
         and fb.add(v(Fr(2 ** 128 - 2 ** 104)), v(Fr(2 ** 103)), fb.F32).kind == "u"
         and fb.add(v(Fr(2 ** 128 - 2 ** 105)), v(Fr(2 ** 104)), fb.F32).kind == "f")
    case("case_verdict combines stability",
         fb.case_verdict(["accept", "accept"], [True, True]) == "accept"
         and fb.case_verdict(["accept", "reject"], [True, True]) == "reject"
         and fb.case_verdict(["reject"], [False]) == "unstable"
         and fb.case_verdict(["accept", "unstable"], [True, True]) == "unstable")


# ---------------------------------------------------------------- (8) source hygiene
def t_source():
    path = os.path.join(os.path.dirname(HERE), "fpbound.py")
    src = open(path).read()
    floats = []
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.NUMBER:
            s = t.string.lower()
            if not s.startswith("0x") and ("." in s or "e" in s or "j" in s):
                floats.append(s)
    case("no float literals in module", not floats, "found=%s" % floats[:5])
    mods = set()
    for line in src.splitlines():
        w = line.split()
        if len(w) > 1 and w[0] in ("import", "from"):
            mods.add(w[1])
    case("stdlib-only imports", mods <= {"fractions", "math", "struct", "collections"}, str(sorted(mods)))
    case("module <= 300 lines", src.count("\n") <= 300, "lines=%d" % src.count("\n"))


def main():
    for t in (t_hand, t_random, t_single, t_stable, t_special, t_source):
        try:
            t()
        except Exception as e:  # report as a failure, keep going
            case('exception', False, "exception %r" % (e,), key=t.__name__)
    print("FAILED: %s" % FAILS if FAILS else "ALL PASS")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
