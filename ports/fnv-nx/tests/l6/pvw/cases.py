"""cases.py: the case lists of test_pvw.py: a (arithmetic sweep), b (return codes and fixme ids), c (state, dirty ranges, pools, in-place)."""
import random, itertools
from gen import *
import gen, oracle
XYZ, RHW, N, PS, D, S = 2, 4, 0x10, 0x20, 0x40, 0x80
SRC_FVFS = [XYZ, XYZ | N, XYZ | D, XYZ | S, XYZ | D | S, XYZ | N | D | S | tex_fvf([2]), RHW | D, XYZ | tex_fvf([2]), XYZ | tex_fvf([3, 1]), XYZ | N | tex_fvf([4, 2]), XYZ | N | D | tex_fvf([2, 3, 4, 1])]
DST_FVFS = [XYZ, RHW, XYZ | D, XYZ | S, XYZ | D | S, RHW | D | S, XYZ | PS | D, XYZ | N, RHW | tex_fvf([2]), RHW | tex_fvf([1, 3]), XYZ | D | tex_fvf([4, 2, 3]), RHW | D | S | tex_fvf([2, 2, 2, 2]), 6 | D, 8 | tex_fvf([2]), 0, XYZ | N | PS | D | S | tex_fvf([3]), RHW | tex_fvf([4, 4, 4, 4, 4, 4, 4, 4])]
def pick_lights(r, nmax=4):
    ls = {}; idxs = r.sample(range(0, 60), r.randint(0, nmax))
    for i in idxs:
        L = rand_light(r, r.choice([1, 2, 3, 4, 5])); L["enabled"] = r.choice([1, 1, 1, 0]); ls[i] = L
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
        if r.random() < 0.7: el.append((0, off, r.choice([4, 8, 9, 15, 0, 1, 2, 3, 5, 7]), 10, 0)); off += 16
        if r.random() < 0.7: el.append((r.choice([0, 1]), 0 if True else 0, r.choice([4, 8, 10, 3]), 10, 1))
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
    # 7. duplicate usages in one declaration (POSITION, NORMAL, COLOR0, TEXCOORD0 twice): the later element of a usage replaces the earlier one
    for rep in range(16):
        el = [(0, 0, 2, 0, 0), (0, 12, 2, 3, 0), (0, 24, 4, 10, 0), (0, 28, 1, 5, 0), (0, 36, 2, 0, 0), (0, 48, 2, 3, 0), (0, 60, 4, 10, 0), (0, 64, 1, 5, 0)]
        streams = make_streams(r, el, {0: 72}, {0: r.choice([0, 8])}, 6)
        c = base_case(r, decl=el, streams=streams, start=r.randint(0, 2), count=r.randint(1, 3), dstidx=r.randint(0, 2), dstfvf=r.choice([XYZ | N | D | tex_fvf([2]), XYZ | D | tex_fvf([2]), RHW | D | tex_fvf([2])]), vp=r.choice(VPS), proj=persp(1.0, 1.3, 1.0, 50.0), view=trans(0, 0, 2), ex=rep & 1)
        c["rs"] = rand_rs(r, lit=rep >> 1 & 1); c["material"] = rand_material(r); c["lights"] = pick_lights(r); out.append(finalize(c, r))
    # 8. dense random world / view / projection: no zero term in the 4-term matrix products, so the summation order is visible in the bits and the normal-matrix inverse pivots
    for rep in range(24):
        c = src_case(r, XYZ | N | D, r.randint(1, 4), r.randint(0, 2)); sc = r.choice([0.5, 1.5, 3.0])
        c.update(world=rnd(r, sc), view=rnd(r, sc), proj=rnd(r, sc), vp=r.choice(VPS), dstfvf=r.choice([XYZ | D, RHW | D, XYZ | N | D]), ex=rep & 1)
        c["rs"] = rand_rs(r, lit=1 if rep % 3 else 0); c["material"] = rand_material(r); c["lights"] = pick_lights(r); out.append(finalize(c, r))
    # 9. modelview whose column 0 is zero in rows 0..2 and non-zero in row 3 (invertible, the 4th column is not affine): the normal-matrix inverse must pivot row 3 to the top
    #    (without the swap the zero test sees a singular matrix and the modelview itself becomes the normal matrix: a visibly different colour)
    for rep in range(24):
        m = [[r.uniform(-2, 2) for _ in range(4)] for _ in range(4)]
        for i in range(3): m[i][0] = 0.0
        m[3][0] = r.choice([-1, 1]) * r.uniform(0.5, 2)
        c = src_case(r, XYZ | N | D, r.randint(1, 4), r.randint(0, 2))
        c.update(world=fm(m), view=ident(), proj=persp(1.0, 1.3, 1.0, 100.0), vp=r.choice(VPS), dstfvf=r.choice([XYZ | D, RHW | D]), ex=rep & 1)
        c["rs"] = rand_rs(r, lit=1); c["material"] = rand_material(r)
        c["lights"] = {i: rand_light(r, r.choice([1, 2, 3])) for i in range(r.randint(1, 3))}; out.append(finalize(c, r))
    return out

def simple(r, sfvf=XYZ | D, dfvf=RHW | D, count=3, **kw):
    c = src_case(r, sfvf, count, kw.pop("start", 0)); c.update(dstfvf=dfvf, vp=VPS[1], proj=persp(1.0, 4 / 3, 1.0, 100.0), view=trans(0, 0, 3), **kw); c["rs"]["LIGHTING"] = kw.get("lit", 0) if False else c["rs"]["LIGHTING"]
    c["rs"]["LIGHTING"] = 0; c["usefvf"] = None; return c
def gen_b(seed):
    r = random.Random(seed); out = []; add = lambda c, **k: out.append(finalize(c, r, **k))
    for ex in (0, 1):
        # return codes
        c = simple(r, ex=ex); c.update(decl=[], streams={}, usefvf=None); add(c)                                         # no declaration bound: INVALIDCALL (no position source)
        c = simple(r, ex=ex); c.update(decl=[], streams={}, usefvf=None, declarg=1); add(c)                              # ... and the declaration argument's FIXME still fires first
        c = simple(r, sfvf=N | XYZ, ex=ex); c["decl"] = [e for e in c["decl"] if e[3] != 0]; c["usefvf"] = None; add(c)   # declaration without position
        c = simple(r, ex=ex); c["streams"] = {}; add(c)                                                                  # declaration but no stream buffer bound: INVALIDCALL
        c = simple(r, ex=ex, dstnull=1); add(c); c = simple(r, ex=ex, dstnull=1, declarg=1); add(c)                      # NULL destination (rule P1): INVALIDCALL, nothing, no FIXME
        c = simple(r, ex=ex, declarg=1); add(c); c = simple(r, ex=ex, declarg=0); add(c)                                 # fixme 1 / none
        for bad in (1, 2):
            for da in (0, 1):
                c = simple(r, ex=ex, declarg=da, bad=bad); add(c)                                                        # wrong-type destination / declaration object: INVALIDCALL, no FIXME, nothing written
        for cnt, st0 in itertools.product((1, 3), (0, 2)):
            for delta in (0, -1):                                                                                       # source stream ends exactly at / one byte before the end of the last element read (XYZ|DIFFUSE: 16 bytes)
                c = simple(r, ex=ex, count=cnt, start=st0); s0 = c["streams"][0]
                s0["data"] = s0["data"][:s0["offset"] + (st0 + cnt - 1) * s0["stride"] + 16 + delta]; add(c)
        for clip in (0, 1):
            c = simple(r, ex=ex); c["rs"]["CLIPPING"] = clip; add(c)                                                     # fixme 10 iff CLIPPING
        c = simple(r, ex=ex, dfvf=0); add(c); c = simple(r, ex=ex, dfvf=0, count=0); add(c)                              # FVF without position: fixme 13, writes nothing, D3D_OK
        c = simple(r, sfvf=XYZ | N | D, dfvf=XYZ | N | D, ex=ex); add(c)                                               # dst NORMAL with source normal: fixme 11
        c = simple(r, sfvf=XYZ | D, dfvf=XYZ | N | D, ex=ex); add(c)                                                    # dst NORMAL without source normal (rule M2): INVALIDCALL
        c = simple(r, sfvf=XYZ | D, dfvf=XYZ | N, count=0, ex=ex); add(c)                                                # ... but count 0 never reaches the loop: D3D_OK
        c = simple(r, ex=ex); c["decl"] = [(0, 0, 2, 0, 0), (0, 12, 5, 10, 0)]; add(c)                                  # UBYTE4 colour: fixme 12 (once per call), colour (0,0,0,1)
        c = simple(r, ex=ex, count=4); c["decl"] = [(0, 0, 2, 0, 0), (0, 12, 5, 10, 0)]; c["rs"]["LIGHTING"] = 1; c["lights"] = pick_lights(r, 3); c["rs"]["COLORVERTEX"] = 1; add(c)
        c = simple(r, ex=ex); c["vs"] = 1; add(c); c = simple(r, ex=ex); c["flags"] = 1; add(c)                           # a bound vertex shader and D3DPV_DONOTCOPYDATA change nothing
        c = simple(r, ex=ex, count=0); add(c)                                                                            # count 0
        c = simple(r, ex=ex, sfvf=XYZ, dfvf=RHW | tex_fvf([2, 4]), count=2); add(c)                                      # destination texcoords without source: bytes skipped (ERR in Wine)
        # out of range (rules M1)
        for k in range(8):
            c = simple(r, ex=ex, count=3); kind = k
            if kind == 0: c["count"] = 100
            elif kind == 1: c["start"] = 50
            elif kind == 2: c["dstidx"] = 100; c["dlen"] = 64
            elif kind == 3: c["streams"][0]["offset"] += 4000
            elif kind == 4: c["count"] = 0xFFFFFFFF; c["dlen"] = 64
            elif kind == 5: c["dlen"] = 40
            elif kind == 6: c["start"] = 0x40000000; c["streams"][0]["stride"] = 16; c["dlen"] = 64
            elif kind == 7: c["dstidx"] = 0x10000000; c["dlen"] = 64
            add(c)
        # exact fit and one byte short
        c = simple(r, ex=ex, dfvf=RHW | D, count=3); add(c, dlen=48 + 0); c = simple(r, ex=ex, dfvf=RHW | D, count=3); add(c, dlen=47)
        c = simple(r, ex=ex, dfvf=XYZ, count=3); add(c, dlen=36); c = simple(r, ex=ex, dfvf=XYZ, count=3); add(c, dlen=35)    # XYZ: the spill float of the last vertex is dropped
        c = simple(r, ex=ex, dfvf=XYZ | PS, count=3); add(c, dlen=48)                                                  # XYZ|PSIZE: rhw lands in the PSIZE slot
        # declaration shapes
        c = simple(r, ex=ex); c["decl"] = [(0, 0, 2, 0, 0), (0, 12, 4, 10, 0), (0, 0, 3, 9, 0)]; c["decl"][2] = (0, 0, 2, 9, 0); add(c)   # POSITION then POSITIONT of the same slot: the last wins
        c = simple(r, ex=ex); c["decl"] = c["decl"] + [(0, 12, 4, 10, 2)]; add(c)                                      # COLOR 2 is no fixed-function input
        c = simple(r, ex=ex, sfvf=XYZ | tex_fvf([2]), dfvf=RHW | tex_fvf([2])); c["decl"] = c["decl"] + [(0, 12, 1, 5, 8)]; add(c)   # TEXCOORD 8 ignored
        c = simple(r, ex=ex, sfvf=XYZ | D | S, dfvf=RHW | D | S); c["rs"]["DIFFUSEMATERIALSOURCE"] = 2; c["rs"]["LIGHTING"] = 1; c["rs"]["COLORVERTEX"] = 1; add(c)
        # more than 32 enabled lights: only the first 32 (ascending index) count
        c = simple(r, sfvf=XYZ | N | D | S, dfvf=XYZ | D | S, ex=ex); c["rs"]["LIGHTING"] = 1
        c["lights"] = {i: rand_light(r, [1, 2, 3][i % 3]) for i in range(40)}; c["lights"] = dict(sorted(c["lights"].items(), reverse=True)); add(c)
        # PARALLELPOINT (4) and GLSPOT (5, "Unhandled light type": id 15 once per enabled light, the light is skipped and not counted towards the 32); 33 usable lights after 3 skipped ones
        c = simple(r, sfvf=XYZ | N | D | S, dfvf=XYZ | D | S, ex=ex); c["rs"]["LIGHTING"] = 1; c["rs"]["LOCALVIEWER"] = ex
        c["lights"] = {0: rand_light(r, 5), 1: rand_light(r, 4), 2: rand_light(r, 5), 3: rand_light(r, 4), 4: rand_light(r, 5)}; add(c)
        c = simple(r, sfvf=XYZ | N | D | S, dfvf=XYZ | D | S, ex=ex); c["rs"]["LIGHTING"] = 1
        c["lights"] = {i: rand_light(r, [5, 4, 3, 1, 2][i % 5]) for i in range(40)}; add(c)
        c = simple(r, sfvf=XYZ | N | D | S, dfvf=XYZ | D | S, ex=ex); c["rs"]["LIGHTING"] = 0; c["lights"] = {0: rand_light(r, 5)}; add(c)           # no lighting: the lights are not walked, no FIXME
        # source buffer without read access (D3DUSAGE_WRITEONLY in D3DPOOL_DEFAULT): the READ map fails, INVALIDCALL, nothing written; SYSTEMMEM gets MAP_R from d3d9 whatever the usage
        c = simple(r, ex=ex, sfvf=XYZ | D, dfvf=RHW | D, count=3); c["streams"][0]["usage"] = 8; c["streams"][0]["cfvf"] = XYZ | D; c["pool"] = 0; add(c)
        c = simple(r, ex=ex, sfvf=XYZ | D, dfvf=RHW | D, count=3); c["streams"][0]["usage"] = 8; c["streams"][0]["cfvf"] = XYZ | D; c["pool"] = 2; add(c)
        c = simple(r, ex=ex, sfvf=XYZ | D, dfvf=RHW | D, count=3); c["streams"][0]["usage"] = 8; c["streams"][0]["cfvf"] = XYZ | D; c["pool"] = 0; c["decl"] = []; add(c)
        # singular modelview (the normal matrix is the modelview itself)
        c = simple(r, sfvf=XYZ | N | D | S, dfvf=XYZ | D | S, ex=ex); c["rs"]["LIGHTING"] = 1; c["world"] = scale(1, 0, 1); c["lights"] = pick_lights(r, 3); add(c)
    return out
def gen_c(seed):
    r = random.Random(seed); out = []
    for pool in (0, 1, 2):
        for ex in (0, 1):
            if ex and pool == 1: continue
            for dfvf, cnt, di in ((RHW | D, 3, 0), (RHW | D, 2, 2), (XYZ, 3, 1), (XYZ | PS, 2, 0), (RHW | tex_fvf([2]), 2, 1)):
                c = simple(r, ex=ex, pool=pool, dfvf=dfvf, count=cnt, dstidx=di, start=1); out.append(finalize(c, r))
    # in place: the destination is stream 0 itself (Wine's loop reads and writes the same memory vertex by vertex)
    for ex in (0, 1):
        for k in range(6):
            fvf = r.choice([RHW | D, XYZ | D, RHW | D | S, XYZ | D | S | tex_fvf([2])]); el, stride = fvf_layout(fvf); nv = 6
            data = bytearray()
            for i in range(nv): data += rand_vertex(r, el, stride)
            data += bytes(16); c = base_case(r, ex=ex, pool=2, decl=el, streams={0: dict(stride=stride, offset=0, data=bytes(data), cfvf=fvf)}, dstfvf=fvf, alias=1, start=r.randint(0, 2), dstidx=r.randint(0, 2), count=r.randint(1, 3), vp=VPS[1])
            c["rs"]["LIGHTING"] = 0; c["dlen"] = len(data); c["dstbytes"] = bytes(data); out.append(c)
    return out
