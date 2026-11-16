#!/usr/bin/env python3
"""ctrans_run: evaluate ctrans terms on concrete inputs and compare them with the compiled C (ctrans_host).  A check of the transcription (a test), not a proof."""
import random
import z3
import ctrans

M64 = (1 << 64) - 1
# boundary words: integers, binary64 / binary32 patterns (NaN payloads, infinities, signed zeros, denormals, rounding ties, 2^63 and 2^64 edges), 80-bit exponents
P64 = [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 23, 52, 16, 32, 64, 0xff, 0xffff, 0x7fffffff, 0x80000000, 0xffffffff, (1 << 63) - 1, 1 << 63, M64, (1 << 63) | 1,
       0x3FF0000000000000, 0xBFF0000000000000, 0x3FF8000000000000, 0x4004000000000000, 0xC004000000000000, 0x400C000000000000, 0x3FE0000000000000, 0xBFE0000000000000,
       0x8000000000000000, 0x0000000000000001, 0x000FFFFFFFFFFFFF, 0x0010000000000000, 0x7FEFFFFFFFFFFFFF, 0x7FF0000000000000, 0xFFF0000000000000,
       0x7FF8000000000000, 0x7FF0000000000001, 0xFFF8000000000001, 0x7FF4000000000000, 0x4330000000000000, 0x4330000000000001, 0x43E0000000000000, 0xC3E0000000000000,
       0x43F0000000000000, 0x43DFFFFFFFFFFFFF, 0x41E0000000000000, 0xC1E0000200000000, 0x40D0000000000000, 0x4010000000000000,
       0x3F800000, 0xBF800000, 0x3FC00000, 0x7FC00000, 0x7FC00001, 0x7F800001, 0x7F800000, 0xFF800000, 0x00000001, 0x007FFFFF, 0x00800000, 0x7F7FFFFF, 0x4F000000, 0xCF000000,
       0x7FFF, 0xFFFF, 0x3FFF, 0xBFFF, 0x3FFE, 0x403E, 0x403F, 0x4040, 0x0001, 0x7FFE, 0xC000000000000000, 0x8000000000000001, 0xFFFFFFFFFFFFF800, 0x7FFFFFFFFFFFFC00,
       0x8000000000000400, 0xA000000000000000, 0x4000, 0x4001, 0x3FFD]
RMODES = (0, 1, 2, 3)


def words_of(mc, args, cells):
    """the z3 input variables of the arguments, as (var, bits) in the flattened word order of ctrans_host"""
    out = []
    for a in args:
        if isinstance(a, ctrans.V): out.append([a.e])
        elif isinstance(a, dict): out.extend([[v.e] for v in a.values()])
        elif isinstance(a, ctrans.Ptr):
            cell = a.cont
            if len(cell) == 16: out.append([cell[i].e for i in range(8)]); out.append([cell[i].e for i in range(8, 16)])
            else: out.append([cell[0].e])
    return out


def pack(parts):
    return z3.Concat(*reversed(parts)) if len(parts) > 1 else parts[0]


def evaluate(expr, sub):
    r = z3.simplify(z3.substitute(expr, *sub))
    return r.as_long() if z3.is_bv_value(r) else None


def trial_words(rng, widths):
    out = []
    for w in widths:
        r = rng.random()
        out.append((rng.choice(P64) if r < .85 else rng.getrandbits(64)) & ((1 << w) - 1))
    return out


def check_function(mc, host, fn, n, seed, tries=60):
    """n trials of fn on boundary inputs, each drawn until every undefined-behaviour obligation of the term holds (rejection sampling; a trial with none in `tries`
    draws is skipped): -> (agree, skipped, disagreements [(words, rmode, cpu, host, term)])"""
    args, cells = mc.inputs(fn)
    mc.G.pop('c', None)
    groups = words_of(mc, args, cells)          # the input variables, before the execution overwrites the cells
    res = mc.apply(fn, args)
    widths = [sum(x.size() for x in g) for g in groups]
    outs = []
    r = res.ret
    if isinstance(r, ctrans.V): outs.append(r.e)
    elif isinstance(r, dict): outs.extend(v.e for v in r.values())
    for a in args:
        if isinstance(a, ctrans.Ptr):
            cell = a.cont
            if len(cell) == 16: outs.append(pack([x.e for x in cell[0:8]])); outs.append(pack([x.e for x in cell[8:16]]))
            else: outs.append(cell[0].e)
    cpu = mc.G.get('c', {})
    cpu_vars = {k: v.e for k, v in dict.items(cpu) if isinstance(v, ctrans.V)} if cpu else {}
    kinds = [v for k, v in mc.enums.items() if k.startswith('FK_')]
    kidx = [i for i, g in enumerate(groups) if any(str(x).endswith('.k') for x in g)]
    rng = random.Random(seed); agree = skipped = 0; bad = []
    for _ in range(n):
        for _try in range(tries):
            ws = trial_words(rng, widths)
            for i in kidx: ws[i] = rng.choice(kinds)
            rm = rng.choice(RMODES)
            cw = {k: rng.choice(P64) & ((1 << v.size()) - 1) for k, v in cpu_vars.items()}
            if 'fcw' in cw and rng.random() < .8: cw['fcw'] = (rng.getrandbits(2) << 10) | (rng.choice((0, 2, 3)) << 8) | 0x3f
            sub = [(z3.BitVec('fpcr_rmode', 2), z3.BitVecVal(rm, 2))]
            for g, w in zip(groups, ws):
                off = 0
                for x in g: sub.append((x, z3.BitVecVal((w >> off) & ((1 << x.size()) - 1), x.size()))); off += x.size()
            for k, e in cpu_vars.items(): sub.append((e, z3.BitVecVal(cw[k], e.size())))
            if all(z3.is_true(z3.simplify(z3.substitute(u, *sub))) for u in res.ub): break
        else:
            skipped += 1; continue
        got = host.run(fn, ws, cw, rm)
        want = [evaluate(o, sub) for o in outs]
        ok = all(wv is not None and (got[i] & ((1 << o.size()) - 1)) == wv for i, (o, wv) in enumerate(zip(outs, want)))
        if ok: agree += 1
        else: bad.append((ws, rm, cw, got[:len(outs)], want))
    return agree, skipped, bad
