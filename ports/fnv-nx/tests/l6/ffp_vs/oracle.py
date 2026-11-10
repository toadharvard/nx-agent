"""Independent oracle of the D3D9 fixed-function VERTEX pipeline, float64, written straight from the documented
equations (DirectX 9 SDK: "Mathematics of Lighting" / "Vertex Fog" / "Point Sprites" / "Texture Coordinate Processing" /
"Vertex Blending") with numpy matrices in D3D's row-vector convention (v' = v M). It shares no code with
ffp_vs_ref.c. Open points follow WineD3D as listed in render/d3d9/ffp/ffp_vs_README.md. run(rec) -> 52 floats:
position4 color0_4 color1_4 tex[8][4] fog psize clip[6]."""
import math
import numpy as np
np.seterr(all="ignore")
from vsrec import fl

RS = dict(FOGENABLE=28, SPECULARENABLE=29, FOGTABLEMODE=35, FOGSTART=36, FOGEND=37, FOGDENSITY=38, RANGEFOGENABLE=48,
          CLIPPING=136, LIGHTING=137, AMBIENT=139, FOGVERTEXMODE=140, COLORVERTEX=141, LOCALVIEWER=142, NORMALIZENORMALS=143,
          DIFFUSE=145, SPECULAR=146, AMBIENTSRC=147, EMISSIVE=148, VERTEXBLEND=151, CLIPPLANEENABLE=152, POINTSIZE=154,
          POINTSIZE_MIN=155, POINTSCALEENABLE=157, A=158, B=159, C=160, POINTSIZE_MAX=166, INDEXED=167, TWEEN=170)
def sat(x): return min(max(x, 0.0), 1.0)
def vec(x): return np.array(x, dtype=np.float64)
def mat(x): return np.array(x, dtype=np.float64).reshape(4, 4)
def nz(v):                       # D3D "normalise", zero vector stays zero
    n = float(np.dot(v, v)); return v if n == 0.0 else v / math.sqrt(n)

def adj(m):                      # classical adjugate (transpose of cofactor matrix), any n x n
    n = m.shape[0]; c = np.zeros_like(m)
    for i in range(n):
        for j in range(n):
            sub = np.delete(np.delete(m, i, 0), j, 1)
            c[i, j] = (-1) ** (i + j) * (np.linalg.det(sub) if n > 1 else 1.0)
    return c.T

def inv_t_or_adj_t(m):           # (m^-1)^T, singular -> adj(m)^T   (domain function of the README)
    d = np.linalg.det(m)
    return (adj(m) / d).T if d != 0.0 and math.isfinite(d) else adj(m).T

def inv_or_adj(m):
    d = np.linalg.det(m)
    return adj(m) / d if d != 0.0 and math.isfinite(d) else adj(m)

def mcs(src, material, vert_color, colorvertex, present):
    if colorvertex and src == 1 and present[0]: return vert_color[0]
    if colorvertex and src == 2 and present[1]: return vert_color[1]
    return material

def run(r):
    d, rs = r["decl"], r["rs"]
    R = lambda k: rs[RS[k]]
    F = lambda k: fl(rs[RS[k]])
    posT, nbw, bidx = d[0] != 0, min(d[1], 4), d[2]
    hasN, hasPs, hasC0, hasC1, hasP1, hasN1 = d[3] != 0, d[4] != 0, d[5] != 0, d[6] != 0, d[7] != 0, d[8] != 0
    ntex = [min(d[10 + i], 4) for i in range(8)]
    V, Pj, W0 = mat(r["view"]), mat(r["proj"]), mat(r["world0"])
    pal = [mat(r["palette"][16 * (i % 16):16 * (i % 16) + 16]) for i in range(256)]
    c0 = vec(r["c0"]) if hasC0 else vec([1, 1, 1, 1])
    c1 = vec(r["c1"]) if hasC1 else vec([0, 0, 0, 0])
    nrm_in = vec(r["n"][:3]) if hasN else vec([0, 0, 0])
    px = vec(r["pos"])
    vb = R("VERTEXBLEND")
    nb = vb if vb in (1, 2, 3) else 0
    tween = vb == 255
    idx_on = R("INDEXED") != 0 and vb in (1, 2, 3, 256)
    K = nb + 1
    if posT:
        X, Y, Z, rhw = px
        vx, vy, vw, vh = r["vp"]
        ndc = [(2 * (X - vx) / vw - 1) if vw != 0 else 0.0, (1 - 2 * (Y - vy) / vh) if vh != 0 else 0.0, Z]
        w = 1.0 / rhw if rhw != 0 else 1.0
        clip = vec([ndc[0] * w, ndc[1] * w, ndc[2] * w, w]); ec = vec([X, Y, Z, 1.0]); normal = vec([0, 0, 0]); clip_ec = ec
    else:
        t = F("TWEEN")
        p = px[:3]; nn = nrm_in
        if tween:
            p1 = vec(r["pos1"][:3]) if hasP1 else vec([0, 0, 0]); n1 = vec(r["n1"][:3]) if hasN1 else vec([0, 0, 0])
            p = (1 - t) * p + t * p1; nn = (1 - t) * nn + t * n1
        bw = [r["bw"][i] if i < nbw else 0.0 for i in range(4)]
        wt = bw[:nb] + [1.0 - sum(bw[:nb])]
        scale = 255.0 if bidx == 2 else 1.0
        bi = [r["bi"][i] if bidx != 0 else 0.0 for i in range(4)]
        mi = [min(max(math.floor(bi[i] * scale + 0.5), 0), 255) if idx_on else i for i in range(K)]
        wv = [pal[m] @ V for m in mi]
        h = np.append(p, 1.0)
        ec = sum(wt[i] * (h @ wv[i]) for i in range(K))
        clip = ec @ Pj; clip_ec = ec
        if ec[3] != 0.0: ec = ec / ec[3]
        if K == 1 and not idx_on: nr = nn @ inv_t_or_adj_t((W0 @ V)[:3, :3])
        else: nr = sum(wt[i] * (nn @ wv[i][:3, :3]) for i in range(K))
        normal = nz(nr) if R("NORMALIZENORMALS") != 0 else nr
    # ---- lighting / colours
    if not posT and R("LIGHTING") != 0:
        m = r["mat"]; cv = R("COLORVERTEX") != 0
        MD, MA, MS, ME = vec(m[0:4]), vec(m[4:8]), vec(m[8:12]), vec(m[12:16])
        pw = m[16]; a = R("AMBIENT")
        acc_a = vec([(a >> 16 & 255) / 255, (a >> 8 & 255) / 255, (a & 255) / 255])
        acc_d, acc_s = vec([0, 0, 0]), vec([0, 0, 0, 0])
        for i in range(min(r["nLights"][0], 8)):
            Lw = r["lights"][i * 26:(i + 1) * 26]
            typ = Lw[0]
            dif, spc, amc = (vec([fl(x) for x in Lw[o:o + 4]]) for o in (1, 5, 9))
            pos, dr = vec([fl(x) for x in Lw[13:16]]), vec([fl(x) for x in Lw[16:19]])
            rng, fo, a0, a1, a2, th, ph = [fl(x) for x in Lw[19:26]]
            if typ not in (1, 2, 3): continue
            if typ == 3:
                Ld = nz((np.append(-dr, 0.0) @ V)[:3]); att = 1.0
                acc_a = acc_a + amc[:3]
            else:
                tov = (np.append(pos, 1.0) @ V)[:3] - ec[:3]; dist = math.sqrt(float(np.dot(tov, tov)))
                if dist > rng: continue
                Ld = nz(tov); spot = 1.0
                if typ == 2:
                    ax = nz((np.append(dr, 0.0) @ V)[:3]); rho = float(np.dot(-Ld, ax))
                    ch, cp = math.cos(th / 2), math.cos(ph / 2)
                    spot = 1.0 if rho > ch else 0.0 if rho <= cp else ((rho - cp) / (ch - cp)) ** fo
                den = a0 + a1 * dist + a2 * dist * dist
                att = 0.0 if den == 0.0 else spot / den
                acc_a = acc_a + amc[:3] * att
            ndl = float(np.dot(Ld, normal))
            acc_d = acc_d + sat(ndl) * dif[:3] * att
            if R("SPECULARENABLE") != 0:
                hv = nz(Ld - nz(ec[:3])) if R("LOCALVIEWER") != 0 else nz(Ld + vec([0, 0, -1]))
                tt = float(np.dot(normal, hv))
                if ndl > 0 and tt > 0: acc_s = acc_s + float(np.power(tt, pw)) * spc * att
        pres, vc = (hasC0, hasC1), (c0, c1)
        A, D, S, E = (mcs(R(k), mm, vc, cv, pres) for k, mm in (("AMBIENTSRC", MA), ("DIFFUSE", MD), ("SPECULAR", MS), ("EMISSIVE", ME)))
        o0 = np.append(A[:3] * acc_a + D[:3] * acc_d + E[:3], D[3])
        o1 = S * acc_s if R("SPECULARENABLE") != 0 else c1
    else:
        o0, o1 = c0, c1
    o0 = np.clip(o0, 0, 1); o1 = np.clip(o1, 0, 1)
    # ---- texture coordinates
    tex = []
    for s in range(8):
        tci, ttff = r["tcIndex"][s], r["ttff"][s]
        gen = tci >> 16 if 1 <= (tci >> 16) <= 4 else 0
        sset = tci & 0xFFFF
        if gen:
            n = 3
            if gen == 1: src = np.append(normal, 1.0)
            elif gen == 2: src = ec.copy()
            else:
                e = nz(ec[:3]); rv = e - 2 * np.dot(normal, e) * normal
                if gen == 3: src = np.append(rv, 1.0)
                else:
                    m2 = 2 * math.sqrt(rv[0] ** 2 + rv[1] ** 2 + (rv[2] - 1) ** 2)
                    src = vec([rv[0] / m2 + 0.5, rv[1] / m2 + 0.5, 0, 1]) if m2 != 0 else vec([0.5, 0.5, 0, 1])
        else:
            n = ntex[sset] if sset < 8 else 0
            raw = r["tex"][4 * sset:4 * sset + 4] if sset < 8 else [0, 0, 0, 1]
            src = vec([raw[i] if i < n else (1.0 if i == 3 else 0.0) for i in range(4)])
        cnt, proj = ttff & 255, (ttff >> 8) & 1
        if not posT and 1 <= cnt <= 4:
            pad = vec([0, 0, 0, 1]) if n == 0 else vec([src[i] if i < n else (1.0 if i == n else 0.0) for i in range(4)])
            o = pad @ mat(r["texm"][16 * s:16 * s + 16])
            o = vec([o[i] if i < cnt else 0.0 for i in range(4)])
            if proj and cnt < 4: o[3] = o[cnt - 1]
        else:
            o = src.copy()
            if not posT and proj and 1 <= n <= 3: o[3] = src[n - 1]
        tex.append(o)
    # ---- fog (factor 1 = unfogged); table fog: eye depth for the pixel epilogue
    tmode = R("FOGTABLEMODE") if R("FOGTABLEMODE") in (1, 2, 3) else 0
    vmode = R("FOGVERTEXMODE") if R("FOGVERTEXMODE") in (1, 2, 3) else 0
    if R("FOGENABLE") == 0: fog = 1.0
    elif posT: fog = Z if tmode else sat(c1[3])
    elif tmode: fog = abs(ec[2])
    elif vmode == 0: fog = sat(c1[3])
    else:
        dd = math.sqrt(float(np.dot(ec[:3], ec[:3]))) if R("RANGEFOGENABLE") != 0 else abs(ec[2])
        st, en, de = F("FOGSTART"), F("FOGEND"), F("FOGDENSITY")
        if vmode == 3: fog = (en - dd) / (en - st) if en != st else (1.0 if dd <= en else 0.0)
        elif vmode == 1: fog = math.exp(-dd * de)
        else: fog = math.exp(-(dd * de) ** 2)
        fog = sat(fog)
    # ---- point size
    si = r["psize"][0] if hasPs else F("POINTSIZE")
    if R("POINTSCALEENABLE") != 0:
        De = math.sqrt(float(np.dot(ec[:3], ec[:3])))
        den = F("A") + F("B") * De + F("C") * De * De
        ps = r["vp"][3] * si / math.sqrt(den) if den > 0 else math.inf
    else: ps = si
    ps = min(max(ps, F("POINTSIZE_MIN")), F("POINTSIZE_MAX"))
    # ---- clip distances (planes arrive in world space; eye-space plane = V^-1 p, adjugate if V is singular)
    cd = []
    for i in range(6):
        if not posT and R("CLIPPING") != 0 and (R("CLIPPLANEENABLE") >> i) & 1:
            pe = inv_or_adj(V) @ vec(r["clip"][4 * i:4 * i + 4])
            cd.append(float(np.dot(clip_ec, pe)))
        else: cd.append(1.0)
    # ---- position convention of the translated shaders: xy += posfix.xy * w ; y *= posfix.z
    pf = r["posfix"]
    pos = [clip[0] + pf[0] * clip[3], (clip[1] + pf[1] * clip[3]) * pf[2], clip[2], clip[3]]
    return pos + list(o0) + list(o1) + [x for t_ in tex for x in t_] + [fog, ps] + cd
