"""oracle.py: the independent model of IDirect3DDevice9::ProcessVertices as wine-9.0 does it, written from the Wine source's arithmetic (dlls/wined3d/device.c process_vertices_strided and its helpers, utils.c multiply_matrix /
invert_matrix, context.c wined3d_stream_info_from_declaration) with numpy float32 scalars: every operation is one IEEE binary32 operation in the order Wine's C evaluates it. powf / expf / cosf are the host libm's (the test binary uses
the same glibc routines; checked bit-equal over 200000 random powf inputs between the 32 bit and the 64 bit libm). Out-of-buffer accesses (rules M1 / M2 of pvw_core.cpp) give INVALIDCALL."""
import ctypes, struct, numpy as np
np.seterr(all="ignore")
f32 = np.float32
libm = ctypes.CDLL("libm.so.6")
for n, k in (("powf", 2), ("expf", 1), ("cosf", 1)): getattr(libm, n).restype = ctypes.c_float; getattr(libm, n).argtypes = [ctypes.c_float] * k
INV = 0x8876086C
def F(x): return f32(x)
def bits(x): return struct.unpack("<I", struct.pack("<f", float(x)))[0]
def flt(u): return f32(struct.unpack("<f", struct.pack("<I", u & 0xffffffff))[0])
def powf(a, b): return f32(libm.powf(float(a), float(b)))
def expf(a): return f32(libm.expf(float(a)))
def cosf(a): return f32(libm.cosf(float(a)))
def sqrtf(a): return np.sqrt(f32(a))

def mm(s1, s2):   # multiply_matrix: t[r][c] = ((s1[0][c]*s2[r][0] + s1[1][c]*s2[r][1]) + s1[2][c]*s2[r][2]) + s1[3][c]*s2[r][3]
    t = [[None] * 4 for _ in range(4)]
    for r in range(4):
        for c in range(4): t[r][c] = (s1[0][c] * s2[r][0] + s1[1][c] * s2[r][1]) + s1[2][c] * s2[r][2] + s1[3][c] * s2[r][3]
    return t

def inv(m):
    w = [[f32(m[i][j]) for j in range(4)] + [f32(1.0 if i == j else 0.0) for j in range(4)] for i in range(4)]
    z = f32(0.0); rows = [0, 1, 2, 3]
    R = lambda i: w[rows[i]]
    def sw(a, b): rows[a], rows[b] = rows[b], rows[a]
    if abs(R(3)[0]) > abs(R(2)[0]): sw(3, 2)
    if abs(R(2)[0]) > abs(R(1)[0]): sw(2, 1)
    if abs(R(1)[0]) > abs(R(0)[0]): sw(1, 0)
    if R(0)[0] == 0: return None
    m1 = R(1)[0] / R(0)[0]; m2 = R(2)[0] / R(0)[0]; m3 = R(3)[0] / R(0)[0]
    for c in range(1, 8):
        s = R(0)[c]
        if c >= 4 and s == 0: continue
        R(1)[c] -= m1 * s; R(2)[c] -= m2 * s; R(3)[c] -= m3 * s
    if abs(R(3)[1]) > abs(R(2)[1]): sw(3, 2)
    if abs(R(2)[1]) > abs(R(1)[1]): sw(2, 1)
    if R(1)[1] == 0: return None
    m2 = R(2)[1] / R(1)[1]; m3 = R(3)[1] / R(1)[1]
    for c in range(2, 8):
        s = R(1)[c]
        if c >= 4 and s == 0: continue
        R(2)[c] -= m2 * s; R(3)[c] -= m3 * s
    if abs(R(3)[2]) > abs(R(2)[2]): sw(3, 2)
    if R(2)[2] == 0: return None
    m3 = R(3)[2] / R(2)[2]
    for c in range(3, 8): R(3)[c] -= m3 * R(2)[c]
    if R(3)[3] == 0: return None
    s = f32(1.0) / R(3)[3]
    for c in range(4, 8): R(3)[c] *= s
    m2 = R(2)[3]; s = f32(1.0) / R(2)[2]
    for c in range(4, 8): R(2)[c] = s * (R(2)[c] - R(3)[c] * m2)
    m1 = R(1)[3]
    for c in range(4, 8): R(1)[c] -= R(3)[c] * m1
    m0 = R(0)[3]
    for c in range(4, 8): R(0)[c] -= R(3)[c] * m0
    m1 = R(1)[2]; s = f32(1.0) / R(1)[1]
    for c in range(4, 8): R(1)[c] = s * (R(1)[c] - R(2)[c] * m1)
    m0 = R(0)[2]
    for c in range(4, 8): R(0)[c] -= R(2)[c] * m0
    m0 = R(0)[1]; s = f32(1.0) / R(0)[0]
    for c in range(4, 8): R(0)[c] = s * (R(0)[c] - R(1)[c] * m0)
    return [[R(i)[4 + j] for j in range(4)] for i in range(4)]

def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def normalise(v):
    rn = f32(1.0) / sqrtf(dot(v, v))
    return [x * rn for x in v] if np.isfinite(rn) else list(v)
def xf4(v, m): return [v[0] * m[0][c] + v[1] * m[1][c] + v[2] * m[2][c] + v[3] * m[3][c] for c in range(4)]
def clamp(v, lo, hi): return lo if v < lo else hi if v > hi else v
def cfrom(c): return [f32(((c >> 16) & 255) / 255.0), f32(((c >> 8) & 255) / 255.0), f32((c & 255) / 255.0), f32(((c >> 24) & 255) / 255.0)]
def ctob(c):   # clamp + B8G8R8A8: (uint32)(v*255 + 0.5f)
    q = []
    for v in c:
        v = clamp(f32(v), f32(0), f32(1)); t = v * f32(255.0) + f32(0.5); q.append(int(t) if t >= 0 and t < 4294967296.0 else 0)
    return (q[0] << 16) | (q[1] << 8) | q[2] | (q[3] << 24)

# declaration type -> (kind, components, bytes) as wined3d_format_get_colour / the d3d9 type table
TYPE_BYTES = [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8]
def get_colour(t, data, fix):
    if t == 4: return cfrom(struct.unpack("<I", data[:4])[0])
    if t == 8:
        u = struct.unpack("<I", data[:4])[0]; return [f32((u & 255) / 255.0), f32(((u >> 8) & 255) / 255.0), f32(((u >> 16) & 255) / 255.0), f32(((u >> 24) & 255) / 255.0)]
    if t in (11, 12):
        n = 2 if t == 11 else 4; o = [f32(0), f32(0), f32(0), f32(1)]
        for i in range(n): o[i] = f32(struct.unpack("<H", data[2 * i:2 * i + 2])[0] / 65535.0)
        return o
    if t <= 3:
        o = [f32(0), f32(0), f32(0), f32(1)]
        for i in range(t + 1): o[i] = f32(struct.unpack("<f", data[4 * i:4 * i + 4])[0])
        return o
    fix.add(12); return [f32(0), f32(0), f32(0), f32(1)]
def colsize(t): return 4 if t in (4, 8, 11) else 8 if t == 12 else 4 * (t + 1) if t <= 3 else 0

def fixed_slot(u, i):
    if u in (0, 9) and i == 0: return 0
    if u == 1 and i == 0: return 1
    if u == 2 and i == 0: return 2
    if u == 3 and i == 0: return 3
    if u == 4 and i == 0: return 4
    if u == 10 and i < 2: return 5 + i
    if u == 5 and i < 8: return 7 + i
    return None

def fvf_size(fvf):
    s = 0; fx = set()
    if fvf & 0x10: s += 12
    if fvf & 0x40: s += 4
    if fvf & 0x80: s += 4
    if fvf & 0x20: s += 4
    pm = fvf & 0x400e
    tbl = {2: 12, 4: 16, 6: 16, 8: 20, 10: 24, 12: 28, 14: 32, 0x4002: 16}
    if pm in tbl: s += tbl[pm]
    else: fx.add(13)
    for i in range(((fvf & 0xf00) >> 8)): s += (((fvf >> (16 + 2 * i)) + 1) & 3) * 4 + 4
    return s, fx
def tex_n(fvf, i): return ((((fvf >> (16 + 2 * i)) + 1) & 3) + 1) * 4

EXT = None   # (left, end) of the destination write range of the last successful model() call (None: nothing written)
def model(c):
    """c: dict (see test_pvw.py). -> (hr, set of fixme ids, dst bytes or None)"""
    fx = set()
    if c["declarg"]: fx.add(1)
    if c["dstnull"]: return INV, set(), None
    # stream info
    si = {}   # slot -> (stream, stride, base, type)
    for (st, off, ty, us, ui) in c["decl"]:
        s = c["streams"].get(st)
        if s is None: continue
        sl = fixed_slot(us, ui)
        if sl is None: continue
        si[sl] = (st, s["stride"], ((c["start"] * s["stride"]) & 0xffffffff) + s["offset"] + off, ty)
    if 0 not in si: return INV, fx, None
    rs = c["rs"]
    if rs["CLIPPING"]: fx.add(10)
    dfvf = c["dstfvf"]; vs, f2 = fvf_size(dfvf); fx |= f2
    left = (c["dstidx"] * vs) & 0xffffffff; alias = c.get("alias"); dst = bytearray(c["streams"][0]["data"] if alias else c["dstbytes"]); L = len(dst)
    def rd(slot, i, n):
        st, stride, base, ty = si[slot]; buf = dst if (alias and st == 0) else c["streams"][st]["data"]; off = base + ((i * stride) & 0xffffffff)
        if off + n > len(buf): raise OOB
        return buf[off:off + n]
    class OOB(Exception): pass
    writes = []
    def wr(off, data, spill=False):
        if off + len(data) > L:
            if spill: return
            raise OOB
        writes.append((off, bytes(data)))
        if alias: dst[off:off + len(data)] = data
    view, proj, world = c["view"], c["proj"], c["world"]
    mat = mm(view, world); mat = mm(proj, mat)
    vp = c["vp"]; vx, vy, vw, vh = F(vp[0]), F(vp[1]), F(vp[2]), F(vp[3]); minz = vp[4]; maxz = vp[5] if vp[5] > minz + f32(0.001) else minz + f32(0.001)
    ntex = (dfvf & 0xf00) >> 8; pos = dfvf & 0x400e
    lighting = bool(rs["LIGHTING"]) and bool(dfvf & 0xc0)
    # lights
    fogmode = rs["FOGVERTEXMODE"] & 3; fogcoord = 3 if rs["RANGEFOGENABLE"] else 2
    fstart, fend, fdens = flt(rs["FOGSTART"]), flt(rs["FOGEND"]), flt(rs["FOGDENSITY"])
    mv = None; nm = None; amb_l = None; nd = npnt = nsp = 0; lt = []
    if not (fogmode == 0 and not lighting):
        mv = mm(view, world)
        if lighting:
            im = inv(mv); im = im if im is not None else mv
            nm = [[im[j][i] for j in range(3)] for i in range(3)]
            amb_l = cfrom(rs["AMBIENT"]); act = []
            for idx in sorted(c["lights"]):
                L_ = c["lights"][idx]
                if not L_["enabled"]: continue
                if len(act) == 32: break
                act.append(L_)
            for ty, name in ((3, "d"), (1, "p"), (2, "s")):
                for L_ in act:
                    if L_["type"] != ty: continue
                    e = dict(t=ty, dif=L_["dif"], spc=L_["spc"], amb=L_["amb"])
                    if ty == 3: e["dir"] = normalise(xf4([-L_["dir"][0], -L_["dir"][1], -L_["dir"][2], f32(0)], view)[:3])
                    else:
                        e["pos"] = xf4([L_["pos"][0], L_["pos"][1], L_["pos"][2], f32(1)], view); e["range"] = L_["range"]; e["c"], e["l"], e["q"] = L_["att"]
                        if ty == 2:
                            e["dir"] = normalise(xf4([L_["dir"][0], L_["dir"][1], L_["dir"][2], f32(0)], view)[:3]); e["fall"] = L_["falloff"]
                            e["ch"] = cosf(L_["theta"] / f32(2.0)); e["cp"] = cosf(L_["phi"] / f32(2.0))
                    lt.append(e)
    # material sources
    use = set(si)
    def valid(s): return s if (s == 1 and 5 in use) or (s == 2 and 6 in use) else 0
    if not rs["LIGHTING"]: dm, sm, em, am = 1, 2, 0, 0
    elif not rs["COLORVERTEX"]: dm = sm = em = am = 0
    else: dm, em, am, sm = valid(rs["DIFFUSEMATERIALSOURCE"]), valid(rs["EMISSIVEMATERIALSOURCE"]), valid(rs["AMBIENTMATERIALSOURCE"]), valid(rs["SPECULARMATERIALSOURCE"])
    M = c["material"]; mspec = M["spc"] if rs["SPECULARENABLE"] else [f32(0)] * 4
    def mcs(src, matc, i):
        if src == 0: return list(matc)
        sl = 5 if src == 1 else 6
        if sl not in si: return [f32(1)] * 4 if src == 1 else [f32(0)] * 4
        return get_colour(si[sl][3], rd(sl, i, max(colsize(si[sl][3]), 1)), fx) if colsize(si[sl][3]) else (fx.add(12) or [f32(0), f32(0), f32(0), f32(1)])
    dp = left
    try:
        for i in range(c["count"]):
            p = struct.unpack("<3f", rd(0, i, 12)); p = [f32(x) for x in p]
            posT = None
            if mv is not None:
                posT = xf4([p[0], p[1], p[2], f32(1)], mv); s = f32(1.0) / posT[3]; posT[0] *= s; posT[1] *= s; posT[2] *= s
            if pos in (2, 4):
                x = (p[0] * mat[0][0]) + (p[1] * mat[1][0]) + (p[2] * mat[2][0]) + mat[3][0]
                y = (p[0] * mat[0][1]) + (p[1] * mat[1][1]) + (p[2] * mat[2][1]) + mat[3][1]
                z = (p[0] * mat[0][2]) + (p[1] * mat[1][2]) + (p[2] * mat[2][2]) + mat[3][2]
                rhw = (p[0] * mat[0][3]) + (p[1] * mat[1][3]) + (p[2] * mat[2][3]) + mat[3][3]
                x = x / rhw; y = y / rhw; z = z / rhw; y = y * f32(-1); x = x * (vw / f32(2)); y = y * (vh / f32(2)); z = z * (maxz - minz)
                x = x + (vw / f32(2) + vx); y = y + (vh / f32(2) + vy); z = z + minz; rhw = f32(1) / rhw
                wr(dp, struct.pack("<3f", x, y, z)); wr(dp + 12, struct.pack("<f", rhw), spill=(pos == 2)); dp += 16 if pos == 4 else 12
            if dfvf & 0x20: dp += 4
            if dfvf & 0x10:
                if 3 not in si: return INV, fx - {11, 12}, None
                fx.add(11); wr(dp, rd(3, i, 12)); dp += 12
            if lighting:
                n = None
                if 3 in si: n = [f32(x) for x in struct.unpack("<3f", rd(3, i, 12))]
                shin = M["power"] if rs["SPECULARENABLE"] else f32(0)
                amb, dif, spc = compute(n, posT, nm, amb_l, lt, rs, shin)
            if dfvf & 0x40:
                md = mcs(dm, M["dif"], i)
                if lighting:
                    ma = mcs(am, M["amb"], i); me = mcs(em, M["emi"], i)
                    dc = [amb[k] * ma[k] + dif[k] * md[k] + me[k] for k in range(3)] + [md[3]]
                else: dc = md
                wr(dp, struct.pack("<I", ctob(dc))); dp += 4
            if dfvf & 0x80:
                ms = mcs(sm, mspec, i)
                sc = [spc[k] * ms[k] for k in range(3)] + [ms[3]] if lighting else list(ms)
                if fogmode != 0:
                    pt = [posT[0], posT[1], posT[2]]; fc = sqrtf(dot(pt, pt)) if fogcoord == 3 else abs(posT[2])
                    if fogmode == 3: sc[3] = (fend - fc) / (fend - fstart)
                    elif fogmode == 1: sc[3] = expf(-fc * fdens)
                    else: sc[3] = expf(-fc * fc * fdens * fdens)
                wr(dp, struct.pack("<I", ctob(sc))); dp += 4
            for t in range(ntex):
                n = tex_n(dfvf, t)
                if (7 + t) in si: wr(dp, rd(7 + t, i, n))
                dp += n
    except OOB:
        return INV, fx - {11, 12}, None   # the real pass (fixmes of the loop) never runs after a failed validation
    for off, d in writes: dst[off:off + len(d)] = d   # (alias: already applied one by one, so later source reads saw them)
    global EXT; EXT = (left, max(o + len(d) for o, d in writes)) if writes else None
    return 0, fx, bytes(dst)

def compute(normal, posT, nm, amb_l, lt, rs, shin):
    pn = normalise(list(posT[:3])); nt = [f32(0)] * 3
    if normal is not None:
        v = normal; nt = [v[0] * nm[0][k] + v[1] * nm[1][k] + v[2] * nm[2][k] for k in range(3)]
        if rs["NORMALIZENORMALS"]: nt = normalise(nt)
    dif = [f32(0)] * 4; spc = [f32(0)] * 4; amb = list(amb_l)
    def muladd(d, s, cc):
        for k in range(3): d[k] = d[k] + s[k] * cc
    def ds(dirv, att, L):
        c_ = clamp(dot(dirv, nt), f32(0), f32(1)); muladd(dif, L["dif"], c_ * att)
        v = list(dirv)
        if rs["LOCALVIEWER"]: v = [v[k] - pn[k] for k in range(3)]
        else: v[2] = v[2] - f32(1)
        v = normalise(v); t = dot(nt, v)
        if t > 0 and dot(dirv, nt) > 0: muladd(spc, L["spc"], att * powf(t, shin))
    for L in lt:
        if L["t"] == 3:
            muladd(amb, L["amb"], f32(1))
            if normal is not None: ds(L["dir"], f32(1), L)
    for L in lt:
        if L["t"] != 1: continue
        d = [L["pos"][k] - posT[k] for k in range(3)]; z = dot(d, d); y = sqrtf(z); x = f32(1)
        if not (y <= L["range"]): continue
        att = f32(1) / (x * L["c"] + y * L["l"] + z * L["q"]); muladd(amb, L["amb"], att)
        if normal is not None: ds(normalise(d), att, L)
    for L in lt:
        if L["t"] != 2: continue
        d = [L["pos"][k] - posT[k] for k in range(3)]; z = dot(d, d); y = sqrtf(z); x = f32(1)
        if not (y <= L["range"]): continue
        d = normalise(d); t = -dot(d, L["dir"])
        if t > L["ch"]: att = f32(1)
        elif t <= L["cp"]: att = f32(0)
        else: att = powf((t - L["cp"]) / (L["ch"] - L["cp"]), L["fall"])
        t = x * L["c"] + y * L["l"] + z * L["q"]; att = att / t; muladd(amb, L["amb"], att)
        if normal is not None: ds(d, att, L)
    return amb, dif, spc
