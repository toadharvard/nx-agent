"""gen.py: case generation + encoding for test_pvw.py (grammar read by t_run.cpp). All numbers in the file are hex; floats are their binary32 bit patterns."""
import random, struct, math, numpy as np
from oracle import f32, bits, flt, model
import oracle
RSN = dict(SPECULARENABLE=29, FOGSTART=36, FOGEND=37, FOGDENSITY=38, RANGEFOGENABLE=48, CLIPPING=136, LIGHTING=137, AMBIENT=139, FOGVERTEXMODE=140, COLORVERTEX=141, LOCALVIEWER=142, NORMALIZENORMALS=143,
           DIFFUSEMATERIALSOURCE=145, SPECULARMATERIALSOURCE=146, AMBIENTMATERIALSOURCE=147, EMISSIVEMATERIALSOURCE=148)
def rs_default():   # the device defaults (dev/state_model.py / MS tables)
    return dict(SPECULARENABLE=0, FOGSTART=bits(0.0), FOGEND=bits(1.0), FOGDENSITY=bits(1.0), RANGEFOGENABLE=0, CLIPPING=1, LIGHTING=1, AMBIENT=0, FOGVERTEXMODE=0, COLORVERTEX=1, LOCALVIEWER=1, NORMALIZENORMALS=0,
                DIFFUSEMATERIALSOURCE=1, SPECULARMATERIALSOURCE=2, AMBIENTMATERIALSOURCE=0, EMISSIVEMATERIALSOURCE=0)
def ident(): return [[f32(1.0 if i == j else 0.0) for j in range(4)] for i in range(4)]
def fm(rows): return [[f32(x) for x in r] for r in rows]
def rotz(a): c, s = math.cos(a), math.sin(a); return fm([[c, s, 0, 0], [-s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
def roty(a): c, s = math.cos(a), math.sin(a); return fm([[c, 0, -s, 0], [0, 1, 0, 0], [s, 0, c, 0], [0, 0, 0, 1]])
def trans(x, y, z): m = ident(); m[3][0], m[3][1], m[3][2] = f32(x), f32(y), f32(z); return m
def scale(x, y, z): m = ident(); m[0][0], m[1][1], m[2][2] = f32(x), f32(y), f32(z); return m
def persp(fov, asp, zn, zf):   # D3DXMatrixPerspectiveFovLH
    ys = 1.0 / math.tan(fov / 2); xs = ys / asp; return fm([[xs, 0, 0, 0], [0, ys, 0, 0], [0, 0, zf / (zf - zn), 1], [0, 0, -zn * zf / (zf - zn), 0]])
def rnd(r, s=1.0): return fm([[r.uniform(-s, s) for _ in range(4)] for _ in range(4)])
def mat_choices(r):
    return [ident(), trans(1.5, -2, 0.25), rotz(0.7), roty(-1.1), scale(2, 0.5, 3), persp(1.0, 4 / 3, 1.0, 100.0), persp(0.8, 1.0, 0.1, 10.0), rnd(r, 1.5), mm_(rotz(0.3), trans(0.5, 0.5, 2))]
def mm_(a, b): return oracle.mm(a, b)
VPS = [(0, 0, 640, 480, f32(0.0), f32(1.0)), (10, 20, 300, 200, f32(0.25), f32(0.75)), (5, 7, 1, 1, f32(0.0), f32(1.0)), (100, 50, 1000, 3, f32(0.5), f32(0.5)), (0, 0, 800, 600, f32(-1.0), f32(2.0)), (3, 4, 17, 31, f32(0.1), f32(0.1) + f32(0.0004))]

# ---- source layouts. A layout: decl elements, streams {idx: dict(stride, offset, cfvf, data builder)}; vertices are random bytes shaped by element types
def fvf_layout(fvf):
    """(decl, stride): wine d3d9 vdecl_convert_fvf order position, normal, psize, diffuse, specular, texcoords (only the groups used by the sweep)"""
    el = []; off = 0
    def add(ty, us, ui, n): nonlocal off; el.append((0, off, ty, us, ui)); off += n
    pm = fvf & 0x400e
    if pm == 2: add(2, 0, 0, 12)
    elif pm == 4: add(3, 9, 0, 16)
    if fvf & 0x10: add(2, 3, 0, 12)
    if fvf & 0x20: add(0, 4, 0, 4)
    if fvf & 0x40: add(4, 10, 0, 4)
    if fvf & 0x80: add(4, 10, 1, 4)
    for i in range((fvf & 0xf00) >> 8):
        code = (fvf >> (16 + 2 * i)) & 3; add([1, 2, 3, 0][code], 5, i, [8, 12, 16, 4][code])
    return el, off
def tex_fvf(sizes):   # sizes 1-4 -> FVF bits
    v = len(sizes) << 8
    for i, s in enumerate(sizes): v |= {2: 0, 3: 1, 4: 2, 1: 3}[s] << (16 + 2 * i)
    return v
def rand_vertex(r, el, stride, kinds=None):
    b = bytearray(stride)
    for (st, off, ty, us, ui) in el:
        n = [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8][ty]
        if ty <= 3: b[off:off + n] = struct.pack("<%df" % (ty + 1), *[f32(r.uniform(-2, 2)) for _ in range(ty + 1)])
        else: b[off:off + n] = bytes(r.getrandbits(8) for _ in range(n))
    return b
def make_streams(r, el, strides, offsets, nvert, cfvf=0):
    streams = {}
    for st in sorted({e[0] for e in el}):
        stride = strides.get(st, max(e[1] + [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8][e[2]] for e in el if e[0] == st)); off = offsets.get(st, 0)
        els = [e for e in el if e[0] == st]; data = bytearray(off)
        for i in range(nvert): data += rand_vertex(r, els, stride)
        data += bytes(4); streams[st] = dict(stride=stride, offset=off, data=bytes(data), cfvf=cfvf if st == 0 else 0)
    return streams
def rand_light(r, ty):
    c = lambda: [f32(r.uniform(0, 1)) for _ in range(4)]
    v = lambda: [f32(r.uniform(-1, 1)) for _ in range(3)]
    th = r.uniform(0.2, 1.0); return dict(type=ty, enabled=1, dif=c(), spc=c(), amb=[f32(r.uniform(0, 0.4)) for _ in range(4)], pos=[f32(r.uniform(-3, 3)) for _ in range(3)], dir=v(), range=f32(r.choice([2.0, 5.0, 50.0, 1e30])),
                                          falloff=f32(r.choice([0.5, 1.0, 2.0])), att=[f32(r.choice([0.0, 0.5, 1.0])), f32(r.choice([0.0, 0.1, 0.3])), f32(r.choice([0.0, 0.05]))], theta=f32(th), phi=f32(th + r.uniform(0.1, 0.8)))
def base_case(r, **kw):
    c = dict(ex=0, pool=2, rs=rs_default(), view=ident(), proj=ident(), world=ident(), vp=VPS[0], material=dict(dif=[f32(1)] * 4, amb=[f32(1)] * 4, spc=[f32(0)] * 4, emi=[f32(0)] * 4, power=f32(0)), lights={},
             decl=[], usefvf=None, streams={}, vs=0, dstfvf=0, dstbytes=b"", start=0, dstidx=0, count=1, declarg=0, dstnull=0, flags=0, alias=0)
    c.update(kw); return c
def rand_material(r):
    col = lambda: [f32(r.uniform(0, 1)) for _ in range(4)]
    return dict(dif=col(), amb=col(), spc=col(), emi=[f32(r.uniform(0, 0.3)) for _ in range(4)], power=f32(r.choice([0.0, 1.0, 4.0, 16.0, 50.5])))
def dst_len(fvf, idx, count, extra=16):
    return oracle.fvf_size(fvf)[0] * (idx + count) + extra
def finalize(c, r, fill=0xAB, dlen=None):
    """set the destination buffer bytes and run the model"""
    if "dlen" not in c or c.get("dlen") is None: c["dlen"] = dst_len(c["dstfvf"], c["dstidx"], c["count"], 16) if dlen is None else dlen
    c["dstbytes"] = bytes([fill]) * c["dlen"]
    return c
def run_model(c):
    hr, fx, out = model(c); ext = oracle.EXT if hr == 0 else None
    c["hr"] = hr; c["fix"] = sorted(fx) + [15] * (oracle.NSKIP[0] - 1 if 15 in fx else 0)   # id 15: one FIXME per enabled light of an unhandled type
    c["exp"] = out if hr == 0 else c["dstbytes"]; c["ext"] = ext; return c
def h(b): return b.hex() if b else "-"
def encode(c):
    o = ["K %x %x %x %x %s" % (c["ex"], c["pool"], c["hr"], len(c["fix"]), " ".join("%x" % x for x in c["fix"]))]
    o.append("RS %x " % len(c["rs"]) + " ".join("%x %x" % (RSN[k], v) for k, v in c["rs"].items()))
    o.append("XF " + " ".join("%x" % bits(x) for m in (c["view"], c["proj"], c["world"]) for row in m for x in row))
    v = c["vp"]; o.append("VP %x %x %x %x %x %x" % (v[0], v[1], v[2], v[3], bits(v[4]), bits(v[5])))
    m = c["material"]; o.append("MT " + " ".join("%x" % bits(x) for x in m["dif"] + m["amb"] + m["spc"] + m["emi"] + [m["power"]]))
    ls = c["lights"]; s = "LT %x" % len(ls)
    for idx, L in c["lights"].items():
        vals = L["dif"] + L["spc"] + L["amb"] + L["pos"] + L["dir"] + [L["range"], L["falloff"]] + L["att"] + [L["theta"], L["phi"]]
        s += " %x %x %x " % (idx, L["type"], L["enabled"]) + " ".join("%x" % bits(x) for x in vals)
    o.append(s)
    if c["usefvf"] is not None: o.append("DC ffffffff %x" % c["usefvf"])
    else: o.append("DC %x " % len(c["decl"]) + " ".join("%x %x %x %x %x" % e for e in c["decl"]))
    st = c["streams"]; o.append("ST %x " % len(st) + " ".join("%x %x %x %x %x %x %s" % (i, d["stride"], d["offset"], len(d["data"]), d.get("cfvf", 0), d.get("usage", 0), h(d["data"])) for i, d in st.items()))
    o.append("VS %x" % c["vs"]); o.append("DS %x %x %s" % (c["dstfvf"], 0 if c["alias"] else c["dlen"], h(c["dstbytes"])))
    o.append("CL %x %x %x %x %x %x" % (c["start"], c["dstidx"], c["count"], c["declarg"], c["dstnull"], c["flags"]))
    o.append("EX " + h(c["exp"])); e = c["ext"]
    if e is None: o.append("DR -")
    elif c["pool"] == 1: o.append("DR %x %x" % e)
    else: o.append("DR 0 %x" % len(c["exp"]))
    return " ".join(o) + "\n"
