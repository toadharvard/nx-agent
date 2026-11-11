"""cases.py: the case lists of test_pvw.py: a (arithmetic sweep), b (return codes and fixme ids), c (state, dirty ranges, pools, in-place)."""
import random, itertools
from gen import *
import gen, oracle
XYZ, RHW, N, PS, D, S = 2, 4, 0x10, 0x20, 0x40, 0x80
SRC_FVFS = [XYZ, XYZ | N, XYZ | D, XYZ | S, XYZ | D | S, XYZ | N | D | S | tex_fvf([2]), RHW | D, XYZ | tex_fvf([2]), XYZ | tex_fvf([3, 1]), XYZ | N | tex_fvf([4, 2]), XYZ | N | D | tex_fvf([2, 3, 4, 1])]
DST_FVFS = [XYZ, RHW, XYZ | D, XYZ | S, XYZ | D | S, RHW | D | S, XYZ | PS | D, XYZ | N, RHW | tex_fvf([2]), RHW | tex_fvf([1, 3]), XYZ | D | tex_fvf([4, 2, 3]), RHW | D | S | tex_fvf([2, 2, 2, 2]), 6 | D, 8 | tex_fvf([2]), 0x4002 | D, 0, XYZ | N | PS | D | S | tex_fvf([3]), RHW | tex_fvf([4, 4, 4, 4, 4, 4, 4, 4])]
def pick_lights(r, nmax=4):
    ls = {}; idxs = r.sample(range(0, 60), r.randint(0, nmax))
    for i in idxs:
        L = rand_light(r, r.choice([1, 2, 3])); L["enabled"] = r.choice([1, 1, 1, 0]); ls[i] = L
    return ls
def rand_rs(r, lit=None):
    rs = rs_default()
    rs["LIGHTING"] = int(r.random() < 0.6) if lit is None else lit; rs["COLORVERTEX"] = r.choice([0, 1, 1]); rs["SPECULARENABLE"] = r.choice([0, 1]); rs["CLIPPING"] = r.choice([0, 1])
    rs["AMBIENT"] = r.getrandbits(32); rs["FOGVERTEXMODE"] = r.choice([0, 0, 1, 2, 3]); rs["RANGEFOGENABLE"] = r.choice([0, 1]); rs["FOGSTART"] = bits(r.choice([0.0, 1.0, 2.0])); rs["FOGEND"] = bits(r.choice([5.0, 9.0, 30.0]))
    rs["FOGDENSITY"] = bits(r.choice([0.05, 0.2, 0.7])); rs["LOCALVIEWER"] = r.choice([0, 1]); rs["NORMALIZENORMALS"] = r.choice([0, 1])
    for k in ("DIFFUSEMATERIALSOURCE", "SPECULARMATERIALSOURCE", "AMBIENTMATERIALSOURCE", "EMISSIVEMATERIALSOURCE"): rs[k] = r.choice([0, 1, 2])
    return rs
def src_case(r, sfvf, count, start, **kw):
    el, stride = fvf_layout(sfvf); pad = r.choice([0, 0, 4, 12]); off = r.choice([0, 0, 8])
    streams = make_streams(r, el, {0: stride + pad}, {0: off}, start + count + 1)
    c = base_case(r, decl=el, streams=streams, usefvf=sfvf if r.random() < 0.5 else None, start=start, count=count, **kw); return c
def gen_a(seed):
    r = random.Random(seed); out = []
    # 1. matrices x viewports, simple layout
    ms = mat_choices(r)
    for i, (w, v, p) in enumerate(itertools.product(ms[:6], ms[:6], ms[:7])):
        if r.random() > 0.25: continue
        c = src_case(r, XYZ | D, 4, 0); c.update(world=w, view=v, proj=p, vp=r.choice(VPS), dstfvf=r.choice([XYZ, RHW, RHW | D | S]), ex=r.choice([0, 1]))
        c["rs"]["LIGHTING"] = 0; out.append(finalize(c, r))
    for vp in VPS:
        c = src_case(r, XYZ, 5, 0); c.update(vp=vp, proj=persp(1.0, 4 / 3, 1.0, 100.0), view=trans(0, 0, 3), dstfvf=RHW); c["rs"]["LIGHTING"] = 0; out.append(finalize(c, r))
    # 2. every source FVF x destination FVF
    for sf, df in itertools.product(SRC_FVFS, DST_FVFS):
        for rep in range(2):
            c = src_case(r, sf, r.randint(1, 5), r.randint(0, 3)); c.update(world=r.choice(ms), view=r.choice(ms[:5]), proj=r.choice(ms[:7]), vp=r.choice(VPS), dstfvf=df, dstidx=r.randint(0, 3), ex=r.choice([0, 1]),
                                                                          pool=r.choice([0, 1, 2]) if True else 2)
            if c["ex"] and c["pool"] == 1: c["pool"] = 2
            c["rs"] = rand_rs(r, lit=rep); c["material"] = rand_material(r); c["lights"] = pick_lights(r)
            if (df & N) and not (sf & N): c["dstfvf"] = df & ~N
            out.append(finalize(c, r))
    # 3. lighting heavy: many lights of all types, material sources, fog
    for k in range(120):
        sf = r.choice([XYZ | N | D | S, XYZ | N | D, XYZ | N | S, XYZ | D | S, XYZ | N]); df = r.choice([XYZ | D | S, XYZ | D, XYZ | S, RHW | D | S])
        c = src_case(r, sf, r.randint(1, 4), 0); c.update(world=r.choice(ms[:5]), view=r.choice(ms[:5] + [trans(0, 0, 3)]), proj=r.choice([ident(), persp(1.0, 1.3, 1.0, 100.0)]), dstfvf=df)
        c["rs"] = rand_rs(r, lit=1); c["material"] = rand_material(r); c["lights"] = pick_lights(r, 6); out.append(finalize(c, r))
    # 4. source element variants
    for k in range(60):
        el = [(0, 0, 2, 0, 0)]; off = 12
        if r.random() < 0.7: el.append((0, off, r.choice([4, 8, 11, 12, 0, 1, 2, 3, 5, 7]), 10, 0)); off += 16
        if r.random() < 0.7: el.append((r.choice([0, 1]), 0 if True else 0, r.choice([4, 8, 12, 3]), 10, 1))
        for e in el: pass
        # normalise offsets: stream 0 elements packed, stream 1 elements at 0
        s0 = [e for e in el if e[0] == 0]; o = 0; fixed = []
        for e in el:
            if e[0] == 0: fixed.append((0, o, e[2], e[3], e[4])); o += [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8][e[2]]
            else: fixed.append(e)
        if r.random() < 0.5: fixed.append((1, 16, 3, 3, 0)) if r.random() < 0.5 else fixed.append((0, o, 2, 3, 0))
        for t in range(r.randint(0, 3)): fixed.append((r.choice([0, 1]), 32 + 16 * t, r.choice([1, 2, 3, 0]), 5, r.choice([t, t, 9])))
        if r.random() < 0.3: fixed.append((0, o, 2, 3, 1))                      # NORMAL index 1: not a fixed-function input
        # recompute strides
        streams = {}
        for st in sorted({e[0] for e in fixed}):
            sz = max(e[1] + [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8][e[2]] for e in fixed if e[0] == st); stride = sz + r.choice([0, 4]); n = 6; data = bytearray(r.choice([0, 4]))
            for i in range(n): data += rand_vertex(r, [e for e in fixed if e[0] == st], stride)
            data += bytes(8); streams[st] = dict(stride=stride, offset=len(data) - n * stride - 8, data=bytes(data), cfvf=0)
        c = base_case(r, decl=fixed, streams=streams, start=r.randint(0, 2), count=r.randint(1, 3), dstidx=r.randint(0, 2), dstfvf=r.choice([XYZ | D | S, RHW | D | tex_fvf([2, 3]), XYZ | N | D | tex_fvf([4, 1])]), vp=r.choice(VPS), proj=persp(1.0, 1.3, 1.0, 50.0), view=trans(0, 0, 2))
        c["rs"] = rand_rs(r); c["material"] = rand_material(r); c["lights"] = pick_lights(r); out.append(finalize(c, r))
    return out
