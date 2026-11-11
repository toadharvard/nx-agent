"""oracle.py: the independent python model of tests/l6/draw. Every rule below is restated from its SOURCE (named at the function), not read from render/d3d9/dev/draw_*.cpp.
 a  Wine 9.0 dlls/d3d9/device.c d3d9_device_Draw* + the non-todo rows of dlls/d3d9/tests/device.c test_draw_primitive (Windows)   b device.c vertex_count_from_primitive_count, SetStreamSourceFreq (frequency = divider & 0x7fffff)
 c fnvnx_dk_layout.h "Location rule" + wined3d_context_gl_load_numbered_arrays + nxgpu4.h NxVtxLayout4   d fnvnx_dk_layout.h / shaders README (driver blocks, constants)   e fnvnx_dk_layout.h "Varying completion" + shcache.h key rule"""
import os, random, struct, sys
sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "render", "total", "census"))
import sm_hand  # noqa: E402  (read-only import)
OPS = {"MOV": 1, "ADD": 2, "MUL": 5, "MAD": 4, "DCL": 31, "DEF": 81, "TEXLD": 66, "TEXCRD": 64, "DP4": 9}   # D3DSIO opcode numbers (d3d9types.h)
OK, INVALID = 0, 0x8876086C
def hx(text): b = sm_hand.assemble(text, OPS); return " ".join("%x" % w for w in struct.unpack("<%dI" % (len(b) // 4), b))
def f32(x): return struct.unpack("<f", struct.pack("<f", x))[0]
def fbits(x): return struct.unpack("<I", struct.pack("<f", x))[0]
def u32(x): return x & 0xFFFFFFFF
# ---------------------------------------------------------------------------------------------------------------- a
def vcount(prim, n):   # device.c vertex_count_from_primitive_count (UINT arithmetic)
    return u32({1: n, 2: n * 2, 3: n + 1, 4: n * 3, 5: n + 2, 6: n + 2}[prim])
def oracle_a(M, prim, count, stride, decl, vb, ib, data, idata):
    up = M >= 2; idx = M in (1, 3); pvb, pst, pib = vb, (16 if vb else 0), ib; valid = 1 <= prim <= 6
    if up:
        if stride == 0: return INVALID, 0, pvb, pst, pib                      # DrawPrimitiveUP: stride 0 = INVALIDCALL, before the count test
        if count == 0: return OK, 0, pvb, pst, pib                            # primitive_count 0 = D3D_OK
        if not decl: return INVALID, 0, 0, pst, (0 if idx else pib)          # Windows rows: vb NULL (stride kept), ib NULL after a failed UP draw
        if not data or (idx and not idata): return INVALID, 0, pvb, pst, pib  # D5 (documented decision)
        return OK, int(valid), 0, 0, (0 if idx else pib)                      # a completed UP draw: stream 0 = (NULL, 0, 0), UP-indexed: ib NULL
    if not decl: return INVALID, 0, pvb, pst, pib                             # "Called without a valid vertex declaration set"
    if idx and not ib: return OK, 0, pvb, pst, pib                            # Windows row: NULL index buffer = SUCCEEDED
    if not valid or count == 0: return OK, 0, pvb, pst, pib
    return OK, int(vb), pvb, pst, pib                                         # NULL stream source = SUCCEEDED, no vertex data
def write_a(d):
    out = []
    for M in range(4):
        for prim in range(8):
            for count in (0, 1, 5):
                for stride in ((0, 16) if M >= 2 else (16,)):
                    for decl in (0, 1):
                        for vb in (0, 1):
                            for ib in (0, 1):
                                for data in ((0, 1) if M >= 2 else (1,)):
                                    for idata in ((0, 1) if M == 3 else (1,)):
                                        for ifmt in ((101, 102, 0, 77) if M == 3 else (101,)):
                                            e = oracle_a(M, prim, count, stride, decl, vb, ib, data, idata)
                                            out.append("%d %d %d %d %d %d %d %d %d %d  %x %d %d %d %d %d" % ((M, prim, count, stride, decl, vb, ib, data, idata, ifmt) + (e[0], e[1], e[2], e[3], e[4], 3 if (M == 3 and ifmt == 101) else 0)))
    open(os.path.join(d, "a.txt"), "w").write("\n".join(out) + "\n")
# ---------------------------------------------------------------------------------------------------------------- b
def write_b(d):
    r = random.Random(11); out = []
    for i in range(1500):
        indexed = r.random() < 0.6; prim = r.randint(1, 6); count = r.choice([1, 2, 3, 7, 100, 3000, 0x55555556, 0x7FFFFFFF, 0xFFFFFFFF, r.randint(1, 4000)])
        first = r.randint(0, 5000); base = r.choice([0, 0, 1, -1, 100, -2000, 0x7FFFFFF0, -0x80000000]); inst = r.choice([1, 1, 2, 4, 9, 0x7FFFFF]); flags = r.choice([0, 1 << 30, 1 << 30])
        if not indexed: inst_exp = 1
        else: inst_exp = inst
        freq = flags | inst if (flags or inst > 1) else 1
        out.append("%d %d %u %u %d %u  %u %u" % (int(indexed), prim, count, first, base, freq, vcount(prim, count), inst_exp))
    open(os.path.join(d, "b.txt"), "w").write("\n".join(out) + "\n")
# ---------------------------------------------------------------------------------------------------------------- c
def attrib_canon(u, i):   # fnvnx_dk_layout.h fnvnx_dk_attrib_location (the canonical table)
    if u == 0: return 0 if i == 0 else -1
    if u == 1: return 1 if i == 0 else -1
    if u == 2: return 2 if i == 0 else -1
    if u == 3: return 3 if i == 0 else -1
    if u == 10: return 4 + i if i < 2 else -1
    if u == 5: return 6 + i if i < 8 else -1
    if u == 6: return 14 if i == 0 else -1
    if u == 7: return 15 if i == 0 else -1
    return -1
def varying_canon(u, i):
    if u == 10: return i if i < 2 else -1
    if u == 5: return 2 + i if i < 16 else -1
    if u == 11: return 18 if i == 0 else -1
    if u == 3: return 19 + i if i < 2 else -1
    if u == 6: return 21 if i == 0 else -1
    if u == 7: return 22 if i == 0 else -1
    if u == 1: return 23 if i == 0 else -1
    if u == 2: return 24 if i == 0 else -1
    if u == 0: return 24 + i if 1 <= i < 4 else -1
    if u == 12: return 28 if i == 0 else -1
    if u == 4: return 28 + i if 1 <= i < 3 else -1
    return -1
def assign(canon, slots, pairs):   # the rule: canonical pairs keep their slot, the others take the free slots in ascending (usage, index) order
    loc = {p: canon(*p) for p in pairs}; taken = {v for v in loc.values() if v >= 0}; free = [s for s in range(slots) if s not in taken]
    for p in sorted(p for p in pairs if loc[p] < 0): loc[p] = free.pop(0)
    return loc
SM1 = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (10, 0), (10, 1)] + [(5, k) for k in range(8)] + [(0, 1)]   # shader model 1 register -> usage (mojoshader.c shader_model_1_input_usage)
UN = ["position", "blendweight", "blendindices", "normal", "psize", "texcoord", "tangent", "binormal", "tessfactor", "positiont", "color", "fog", "depth", "sample"]
DT = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 16]   # D3DDECLTYPEs the env profile reports (caps DeclTypes = 0x30f)
def write_c(d):
    r = random.Random(5); out = []; pool = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (5, 1), (5, 7), (5, 9), (5, 12), (6, 0), (7, 0), (10, 0), (10, 1), (10, 2), (0, 1), (3, 1), (11, 0), (12, 0), (9, 0), (8, 0)]
    for n in range(400):
        sm1 = r.random() < 0.2
        if sm1: regs = sorted(r.sample(range(16), r.randint(1, 6))); pairs = [SM1[k] for k in regs]; src = "vs_1_1\n" + "".join("mov oD0, v%d\n" % k for k in regs) + "mov oPos, v%d\n" % regs[0]
        else:
            pairs = r.sample(pool, r.randint(1, 9)); regs = list(range(len(pairs)))
            src = "vs_3_0\n" + "".join("dcl_%s%s v%d\n" % (UN[u], i or "", k) for k, (u, i) in enumerate(pairs)) + "dcl_position o0\n" + "".join("mov o0, v%d\n" % k for k in regs)
        loc = assign(attrib_canon, 16, pairs)
        nst = r.randint(1, 4); elems = []
        for (u, i) in pairs:
            if r.random() < 0.8: elems.append((r.randrange(nst), r.randrange(0, 60, 4), r.choice(DT), u, i))
        for _ in range(r.randint(0, 3)): elems.append((r.randrange(nst), r.randrange(0, 60, 4), r.choice(DT), r.choice(pool)[0], r.randint(0, 3)))   # extra elements: no input reads them (or a duplicate pair: the last wins)
        freq = {0: 1}
        for s in range(1, nst):
            m = r.random(); freq[s] = 1 if m < 0.5 else (1 << 30 | r.randint(1, 5)) if m < 0.75 else (2 << 30 | r.randint(1, 5))
        bound = [1 if r.random() < 0.9 else 0 for _ in range(nst)]
        attrs = []; used = 0; draw = 1; unb = []
        for k, p in enumerate(pairs):
            es = [e for e in elems if (e[3], e[4]) == p]
            if not es: unb.append(p); continue
            e = es[-1]                                                         # the last element of the pair wins
            if not bound[e[0]]: draw = 0
            attrs.append((e[1], e[0], e[2], loc[p])); used |= 1 << e[0]
        zero = -1
        if unb and draw:
            zero = min(s for s in range(16) if not used >> s & 1)
            for p in unb: attrs.append((0, zero, 3, loc[p]))
        imask = 0; div = {}
        for s in range(16):
            if used >> s & 1 and s < nst and freq.get(s, 1) >> 30 == 2: imask |= 1 << s; div[s] = freq[s] & 0x7FFFFF
        out += ["case", "vs " + hx(src)] + ["el %d %d %d %d %d" % e for e in elems] + ["freq %d %u" % (s, freq[s]) for s in range(nst)] + ["bound " + " ".join(str(b) for b in bound), "draw %d" % draw]
        if draw: out += ["imask %d" % imask] + ["div %d %d" % (s, v) for s, v in sorted(div.items())] + ["attr %d %d %d %d" % a for a in attrs] + ["zero %d" % zero]
        out.append("end")
    open(os.path.join(d, "c.txt"), "w").write("\n".join(out) + "\n")
# ---------------------------------------------------------------------------------------------------------------- d
def col(c): return [f32(((c >> s) & 255) / 255.0) for s in (16, 8, 0, 24)]   # D3DCOLOR ARGB -> r, g, b, a
def pk(*v): return b"".join(struct.pack("<f" if isinstance(x, float) else "<I", x) for x in v)
def rnd_f(r): return f32(r.choice([0.0, 1.0, -1.0, 0.5, 255.0, 1e-3, r.uniform(-100, 100), r.uniform(-1, 1)]))
def fog_mode(en, tab, psver):   # d3d9_state.h "Fog:" + shaders README: 0 off / 1 vertex / 2 exp / 3 exp2 / 4 linear
    if en == 0 or psver >= 0x300: return 0
    return {0: 1, 1: 2, 2: 3, 3: 4}.get(tab, 0)
def vs_block(psver): return pk(0.0, 0.0, 1.0, 0.0, 0.0 if psver == 0x300 else 1.0, 0.0, 0.0, 0.0) + bytes(32)
def ps_block(rs, tss, psver, stype):
    fe = rs.get(28, 0); tab = rs.get(35, 0); at = rs.get(15, 0) != 0; af = rs.get(25, 8); ar = rs.get(24, 0)
    func = af if 1 <= af <= 8 else 8
    b = pk(*col(rs.get(34, 0))) + pk(float(fog_mode(fe, tab, psver)), struct.unpack("<f", struct.pack("<I", rs.get(36, 0)))[0], struct.unpack("<f", struct.pack("<I", rs.get(37, 0x3F800000)))[0], struct.unpack("<f", struct.pack("<I", rs.get(38, 0x3F800000)))[0])
    b += pk(func if at else 0, 0, 0, 0) + pk(f32(f32(float(ar)) / 255.0), 0.0, 0.0, 0.0)
    for n in range(8): b += pk(*[tss.get((n, t), 0.0) for t in (7, 8, 9, 10)]) + pk(tss.get((n, 22), 0.0), tss.get((n, 23), 0.0), 0.0, 0.0)
    return b + b"".join(struct.pack("<I", w) for w in stype) + bytes(352 - len(b) - 32)
RS_FOGENABLE, RS_FOGCOLOR, RS_FOGTABLE, RS_FOGSTART, RS_FOGEND, RS_FOGDENS, RS_ATEST, RS_AFUNC, RS_AREF = 28, 34, 35, 36, 37, 38, 15, 25, 24
def write_d(d):
    r = random.Random(23); out = []
    VS2 = hx("vs_2_0\ndcl_position v0\nmov oPos, v0\n"); PS = {"2_0": hx("ps_2_0\ndcl v0\nmov oC0, v0\n"), "3_0": hx("ps_3_0\ndcl_color0 v0\nmov oC0, v0\n"), "1_4": hx("ps_1_4\ntexld r0, t0\n")}
    for n in range(300):
        pk_ = r.choice(["ffp", "2_0", "3_0", "1_4"]); vk = r.choice(["ffp", "2_0"]); psver = {"ffp": 0, "2_0": 0x200, "3_0": 0x300, "1_4": 0x104}[pk_]
        rs = {}; tss = {}
        for k, mk in ((RS_FOGENABLE, lambda: r.choice([0, 1, 5])), (RS_FOGCOLOR, lambda: r.getrandbits(32)), (RS_FOGTABLE, lambda: r.choice([0, 1, 2, 3, 3, 4, 77])), (RS_ATEST, lambda: r.choice([0, 1, 9])),
                      (RS_AFUNC, lambda: r.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 200])), (RS_AREF, lambda: r.choice([0, 1, 128, 255, r.getrandbits(32)]))):
            if r.random() < 0.8: rs[k] = mk()
        for k in (RS_FOGSTART, RS_FOGEND, RS_FOGDENS):
            if r.random() < 0.8: rs[k] = fbits(rnd_f(r))
        for s in range(8):
            for t in (7, 8, 9, 10, 22, 23):
                if r.random() < 0.3: tss[(s, t)] = rnd_f(r)
        tex = [r.choice([0, 1, 2, 3]) for _ in range(4)]                       # per stage: none / 2D / cube / volume
        stype = [({0: 0, 1: 0, 2: 1, 3: 2}[tex[s]] if s < 4 else 0) if pk_ in ("ffp", "1_4") else 0 for s in range(8)]
        vsf = {}; psf = {}; vsi = {}; psi = {}; vsb = {}; psb = {}
        for tab, hi, nn in ((vsf, 256, 4), (psf, 224, 4)):
            for _ in range(r.randint(0, 4)):
                s = r.randrange(hi); c = r.randint(1, min(5, hi - s))
                for k in range(c): tab[s + k] = [rnd_f(r) for _ in range(4)]
        for tab in (vsi, psi):
            for _ in range(r.randint(0, 3)): tab[r.randrange(16)] = [r.randint(-5000, 5000) for _ in range(4)]
        for tab in (vsb, psb):
            for _ in range(r.randint(0, 3)): tab[r.randrange(16)] = r.choice([0, 1, 2, 0x80000000])
        out += ["case", "vs " + ("ffp" if vk == "ffp" else VS2), "ps " + ("ffp" if pk_ == "ffp" else PS[pk_])] + ["rs %d %u" % kv for kv in rs.items()] + ["tss %d %d %u" % (s, t, fbits(v)) for (s, t), v in tss.items()]
        out += ["tex %d %d" % (s, tex[s]) for s in range(4)]
        for nm, tab in (("vsf", vsf), ("psf", psf)):
            for i, v in sorted(tab.items()): out.append("%s %d %s" % (nm, i, " ".join("%u" % fbits(x) for x in v)))
        for nm, tab in (("vsi", vsi), ("psi", psi)):
            for i, v in sorted(tab.items()): out.append("%s %d %s" % (nm, i, " ".join("%d" % x for x in v)))
        for nm, tab in (("vsb", vsb), ("psb", psb)):
            for i, v in sorted(tab.items()): out.append("%s %d %u" % (nm, i, v))
        ffv = bytes(r.getrandbits(8) for _ in range(64)); ffp = bytes(r.getrandbits(8) for _ in range(32))
        out += ["ffv " + ffv.hex(), "ffp " + ffp.hex()]
        def blk(tab, n_, wide): b = b"".join(pk(*[fbits(x) for x in tab.get(i, [0.0] * 4)]) if False else struct.pack("<4I", *[fbits(x) for x in tab.get(i, [0.0] * 4)]) for i in range(n_)); return b + bytes(wide - len(b))
        def ib(ti, tb): return b"".join(struct.pack("<4i", *ti.get(i, [0] * 4)) for i in range(16)) + b"".join(struct.pack("<4I", int(tb.get(i, 0) != 0), 0, 0, 0) for i in range(16))
        out += ["ubo 0 0 " + blk(vsf, 256, 4096).hex(), "ubo 0 1 " + ib(vsi, vsb).hex(), "ubo 0 2 " + vs_block(psver).hex(), "ubo 1 0 " + blk(psf, 224, 4096).hex(), "ubo 1 1 " + ib(psi, psb).hex(),
                "ubo 1 2 " + ps_block(rs, tss, psver, stype).hex()]
        if vk == "ffp": out.append("ubo 0 4 " + ffv.hex())
        if pk_ == "ffp": out.append("ubo 1 3 " + ffp.hex())
        out.append("end")
    open(os.path.join(d, "d.txt"), "w").write("\n".join(out) + "\n")
# ---------------------------------------------------------------------------------------------------------------- e
def needs_complete(w, rd): return any(varying_canon(*p) >= 0 and p not in w for p in rd)
def link(vs_out, ps_in):   # shcache.h shcache_link_set: (VS outputs without POSITION0 / PSIZE0) U PS inputs; empty when all canonical, else ascending
    s = sorted({p for p in vs_out if p not in ((0, 0), (4, 0))} | set(ps_in))
    return [] if all(varying_canon(*p) >= 0 for p in s) else s
def write_e(d):
    r = random.Random(31); out = []
    vout_pool = [(10, 0), (10, 1), (5, 0), (5, 1), (5, 3), (5, 9), (11, 0), (4, 0), (3, 2), (0, 1), (6, 1), (8, 0), (9, 0), (13, 0), (2, 0), (4, 1)]
    for n in range(300):
        vo = r.sample(vout_pool, r.randint(0, 6)); vo = [(0, 0)] + [p for p in vo if p != (0, 0)]
        src = "vs_3_0\ndcl_position v0\n" + "".join("dcl_%s%s o%d\n" % (UN[u], i or "", k) for k, (u, i) in enumerate(vo)) + "".join("mov o%d, v0\n" % k for k in range(len(vo)))
        if r.random() < 0.3:   # vs_1_1: the outputs are the registers written (oPos, oD#, oT#, oFog, oPts)
            vo = [(0, 0)] + r.sample([(10, 0), (10, 1), (5, 0), (5, 3), (5, 7), (11, 0), (4, 0)], r.randint(0, 5)); reg = {(0, 0): "oPos", (4, 0): "oPts", (11, 0): "oFog"}
            src = "vs_1_1\nmov oPos, v0\n" + "".join("mov %s, v0\n" % (reg.get(p) or ("oD%d" % p[1] if p[0] == 10 else "oT%d" % p[1])) for p in vo[1:])
        kind = r.choice(["3_0", "2_0", "1_4", "ffp"])
        if kind == "3_0":
            pi = r.sample(vout_pool, r.randint(0, 5)); pi = [p for p in pi if p[0] not in (0,)]; ps = "ps_3_0\n" + "".join("dcl_%s%s v%d\n" % (UN[u], i or "", k) for k, (u, i) in enumerate(pi)) + ("mov oC0, v0\n" if pi else "mov oC0, c0\n"); psv = 3
        elif kind == "2_0":
            pi = [(5, k) for k in sorted(r.sample(range(8), r.randint(0, 3)))] + [(10, k) for k in sorted(r.sample(range(2), r.randint(0, 2)))]
            ps = "ps_2_0\n" + "".join("dcl t%d\n" % i if u == 5 else "dcl v%d\n" % i for (u, i) in pi) + ("mov oC0, %s\n" % ("t%d" % pi[0][1] if pi[0][0] == 5 else "v%d" % pi[0][1]) if pi else "mov oC0, c0\n"); psv = 2
        elif kind == "1_4":
            pi = [(5, k) for k in sorted(r.sample(range(6), r.randint(1, 3)))] + [(10, k) for k in sorted(r.sample(range(2), r.randint(0, 2)))]
            ps = "ps_1_4\n" + "".join("texld r%d, t%d\n" % (k, i) if u == 5 else "mov r%d, v%d\n" % (k, i) for k, (u, i) in enumerate(pi)); psv = 1
        else: pi = [(10, 0), (5, 0), (11, 0)]; ps = None; psv = 0
        w = {p for p in vo if p not in ((0, 0), (4, 0))} | {(11, 0)}
        rd = set(pi) | ({(11, 0)} if psv < 3 else set())
        if kind == "ffp": rd = set(pi)
        var = int(needs_complete(w, rd)); L = link(set(vo), set(pi))
        out += ["case", "vs " + hx(src), "ps " + (hx(ps) if ps else "ffp"), "expect %d %s" % (var, " ".join("%d" % (u * 16 + i) for u, i in L)), "end"]
    open(os.path.join(d, "e.txt"), "w").write("\n".join(out) + "\n")
def write_all(d):
    for f in (write_a, write_b, write_c, write_d, write_e): f(d)
