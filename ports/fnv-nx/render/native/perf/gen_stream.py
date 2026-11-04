#!/usr/bin/env python3
"""fnv-nx P6n: synthetic D3D9-level draw streams (.nxd3d, format: perf/nxd3d.h) with the draw mix of
docs/RENDER_NATIVE.md §3.7. Every number used is in FAMILIES below and is copied into report.json.

    python3 -I gen_stream.py <out dir>      writes fam_<family>.nxd3d (one family, its 'hi' draw count),
                                            mix_lo.nxd3d, mix_hi.nxd3d, wo_<family>.nxd3d (mix_hi without it),
                                            empty.nxd3d and streams.json (the spec)

Each file has FRAMES frames; the consumers measure the last one (earlier frames warm the state caches, the
sampler cache and Nine's shader variants). Deterministic (fixed seed).
"""
import json, os, random, struct, sys

FRAMES = 3
# Synthetic engine pass ids: 900 + family index (outside the engine's 0..758 numbering, so a tool can never take
# them for real ones). marker_family: the NxFamilyId (nx/families.h) the PASS marker carries.
# draws: (lo, hi) per frame, RENDER_NATIVE.md §3.7 (lit: 600-1000 geometries x 1.2-1.6 passes).
# tex: texture stages per draw; tex_p: probability that a draw is textured at all.
# rs: D3DRS sets the engine issues per draw (NiDX9RenderState calls, most redundant).
# p_mat: probability that a draw changes its render-state set (material: blend/alpha test/z/cull/bias).
# p_tex: probability that a draw changes its textures; p_sh: shader pair change; p_geo: geometry change.
# vs/ps: per-draw constants in bytes (SetVertex/PixelShaderConstantF; native: one UBO block of vs + ps bytes).
# shaders: distinct shader pairs in the family; mats: distinct render-state sets.
# Order = frame order (shadow maps, reflection, main scene, alpha, image space + UI).
# targets: render targets the family's draws go to, switched every `per_target` draws (0 = one target).
FAMILIES = [
    dict(name="shadow", fam=4, draws=(100, 300), tex=1, tex_p=0.2, rs=6, p_mat=0.05, p_tex=0.20, p_sh=0.10, p_geo=0.95,
         vs=64, ps=0, shaders=2, mats=2, targets=["shadow0", "shadow1", "shadow2"], per_target=-1, layer=0),
    dict(name="water_refl", fam=3, draws=(100, 300), tex=2, tex_p=1.0, rs=12, p_mat=0.15, p_tex=0.80, p_sh=0.20,
         p_geo=0.95, vs=128, ps=32, shaders=10, mats=4, targets=["refl"], per_target=0, layer=0),
    dict(name="lit", fam=3, draws=(720, 1600), tex=3, tex_p=1.0, rs=14, p_mat=0.20, p_tex=0.85, p_sh=0.25, p_geo=0.95,
         vs=192, ps=64, shaders=24, mats=6, targets=["main"], per_target=0, layer=0),
    dict(name="land", fam=5, draws=(100, 150), tex=6, tex_p=1.0, rs=10, p_mat=0.05, p_tex=1.0, p_sh=0.30, p_geo=1.0,
         vs=96, ps=32, shaders=6, mats=2, targets=["main"], per_target=0, layer=0),
    dict(name="lod", fam=3, draws=(100, 200), tex=2, tex_p=1.0, rs=8, p_mat=0.05, p_tex=0.50, p_sh=0.10, p_geo=1.0,
         vs=80, ps=16, shaders=3, mats=2, targets=["main"], per_target=0, layer=0),
    dict(name="trees", fam=3, draws=(150, 450), tex=2, tex_p=1.0, rs=10, p_mat=0.33, p_tex=0.40, p_sh=0.33, p_geo=0.7,
         vs=112, ps=16, shaders=3, mats=3, targets=["main"], per_target=0, layer=0),
    dict(name="grass", fam=2, draws=(100, 300), tex=1, tex_p=1.0, rs=8, p_mat=0.02, p_tex=0.10, p_sh=0.05, p_geo=0.5,
         vs=48, ps=16, shaders=2, mats=1, targets=["main"], per_target=0, layer=0),
    dict(name="actors", fam=3, draws=(50, 200), tex=3, tex_p=1.0, rs=14, p_mat=0.20, p_tex=0.70, p_sh=0.30, p_geo=1.0,
         vs=192, ps=64, shaders=6, mats=3, targets=["main"], per_target=0, layer=0),
    dict(name="particles", fam=2, draws=(50, 150), tex=1, tex_p=1.0, rs=12, p_mat=0.50, p_tex=0.40, p_sh=0.30,
         p_geo=1.0, vs=64, ps=16, shaders=4, mats=4, targets=["main"], per_target=0, layer=1),
    dict(name="imagespace_ui", fam=7, draws=(20, 60), tex=2, tex_p=1.0, rs=10, p_mat=0.60, p_tex=1.0, p_sh=0.70,
         p_geo=0.3, vs=16, ps=64, shaders=12, mats=5, targets=["is0", "is1", "is2", "is3", "main"], per_target=6, layer=1),
]
# Targets: (width, height, D3DFMT colour, has depth). main = the 1280x720 back buffer (target 0).
TARGETS = {"main": (1280, 720, 22, 1), "shadow0": (1024, 1024, 21, 1), "shadow1": (1024, 1024, 21, 1),
           "shadow2": (1024, 1024, 21, 1), "refl": (640, 360, 21, 1), "is0": (640, 360, 21, 0),
           "is1": (320, 180, 21, 0), "is2": (160, 90, 21, 0), "is3": (1280, 720, 21, 0)}
# D3DRS states of the material sets: ZENABLE, ZWRITEENABLE, ALPHATESTENABLE, SRCBLEND, DESTBLEND, CULLMODE, ZFUNC,
# ALPHAREF, ALPHAFUNC, ALPHABLENDENABLE, FOGENABLE, STENCILENABLE, COLORWRITEENABLE, BLENDOP, DEPTHBIAS,
# SLOPESCALEDEPTHBIAS, FOGCOLOR. The first `rs` of them are set per draw (in this order).
RS_ORDER = [7, 14, 15, 19, 20, 22, 23, 24, 25, 27, 28, 52, 168, 171, 195, 175, 34]
F1 = 0x3F800000
MATERIALS = [   # value per RS_ORDER entry
    [1, 1, 0, 2, 1, 3, 4, 0, 8, 0, 1, 0, 15, 1, 0, 0, 0xFF808080],            # opaque
    [1, 1, 1, 2, 1, 3, 4, 128, 7, 0, 1, 0, 15, 1, 0, 0, 0xFF808080],          # alpha tested
    [1, 0, 0, 5, 6, 3, 4, 0, 8, 1, 1, 0, 15, 1, 0, 0, 0xFF808080],            # alpha blended
    [1, 0, 0, 2, 2, 1, 4, 0, 8, 1, 0, 0, 7, 1, 0, 0, 0xFF808080],             # additive, no cull
    [1, 0, 0, 5, 6, 3, 4, 0, 8, 1, 1, 0, 15, 1, 0xB8000000 & 0, 0, 0xFF808080],  # decal (bias via SLOPESCALE)
    [0, 0, 0, 2, 1, 1, 8, 0, 8, 0, 0, 0, 15, 1, 0, 0, 0xFF000000],            # screen space
]
for i, m in enumerate(MATERIALS):           # decal: slope-scaled bias 1.0, depth bias as float bits
    if i == 4:
        m[14] = 0xB4000000; m[15] = F1
SAMP = [(1, 1), (2, 1), (5, 2), (6, 2), (7, 2)]   # ADDRESSU/V WRAP, MAG/MIN/MIP LINEAR
D3DFMT_DXT1, D3DFMT_DXT5 = 0x31545844, 0x35545844


class W:
    def __init__(self):
        self.w = []

    def op(self, op, *a):
        self.w.append(op | len(a) << 16)
        self.w.extend(int(x) & 0xFFFFFFFF for x in a)

    def bytes(self):
        return b"NXD3D\0\0\1" + struct.pack("<%dI" % len(self.w), *self.w)


def gen(families, level, frames=FRAMES, seed=1):
    """level: 0 = lo draw counts, 1 = hi. Returns (bytes, stats)."""
    rnd = random.Random(seed)
    w = W()
    tid = {}                                  # target name -> id (main = the back buffer, 0)
    for name, (tw, th, fmt, dep) in TARGETS.items():
        tid[name] = 0 if name == "main" else len([k for k in tid if k != "main"]) + 1
        if tid[name]:
            w.op(4, tid[name], tw, th, fmt, dep)
    # resources: per family its textures, shaders and geometry (ids unique across the stream)
    tex_id, sh_id, geo_id = [1], [1], [1]
    res = {}
    for f in families:
        ntex = max(4, f["tex"] * 8)
        texs = list(range(tex_id[0], tex_id[0] + ntex)); tex_id[0] += ntex
        for t in texs:
            w.op(1, t, D3DFMT_DXT5 if t % 3 == 0 else D3DFMT_DXT1, 64, 64, 7)
        pairs = []
        for s in range(f["shaders"]):
            vs, ps = sh_id[0], sh_id[0] + 1; sh_id[0] += 2
            w.op(2, vs, 0, max(4, f["vs"] // 16) + (s % 3))
            w.op(2, ps, 1, (f["tex"] << 8) | (max(1, f["ps"] // 16) + (s % 3)))
            pairs.append((vs, ps))
        if f["tex_p"] < 1.0:                   # untextured variants (shadow: only alpha-tested casters sample)
            extra = []
            for (vs, ps) in pairs:
                ps0 = sh_id[0]; sh_id[0] += 1
                w.op(2, ps0, 1, (0 << 8) | max(1, f["ps"] // 16))
                extra.append((vs, ps0))
            pairs = pairs + extra
        ngeo = 64
        geos = list(range(geo_id[0], geo_id[0] + ngeo)); geo_id[0] += ngeo
        for g in geos:
            w.op(3, g, 64, 96, 20)
        res[f["name"]] = (texs, pairs, geos)
    stats = {}
    for fr in range(frames):
        w.op(10, fr)
        rnd.seed(seed * 1000 + fr % 2)          # the measured frame repeats a warm-up frame's draws exactly
        for fi, f in enumerate(families):
            # per family seed: a family draws the same in every stream it is in (leave-one-out streams)
            rnd.seed(seed * 1000 + fr % 2 + 17 * (FAMILIES.index(f) if f in FAMILIES else 0))
            fi = FAMILIES.index(f) if f in FAMILIES else 0
            texs, pairs, geos = res[f["name"]]
            n = f["draws"][level]
            npairs = f["shaders"]
            mat = fi % len(MATERIALS) if f["name"] != "imagespace_ui" else 5
            cur_tex = [texs[i % len(texs)] for i in range(f["tex"])]
            cur_pair = pairs[0]; cur_geo = geos[0]
            targets = f["targets"]
            per = f["per_target"] if f["per_target"] > 0 else (n + len(targets) - 1) // len(targets) if f["per_target"] < 0 else n
            st = stats.setdefault(f["name"], dict(draws=0, rs_calls=0, tex_changes=0, shader_changes=0, mat_changes=0,
                                                  target_changes=0, const_bytes=0))
            for d in range(n):
                if d % per == 0:
                    t = targets[(d // per) % len(targets)]
                    w.op(12, tid[t], 7 if TARGETS[t][3] else 1)
                    w.op(13, 900 + FAMILIES.index(f), f["fam"])
                    st["target_changes"] += 1
                if rnd.random() < f["p_mat"]:
                    mat = rnd.randrange(len(MATERIALS)) if f["name"] != "imagespace_ui" else rnd.choice([3, 5, 2])
                    st["mat_changes"] += 1
                m = MATERIALS[mat]
                for k in range(f["rs"]):
                    w.op(14, RS_ORDER[k], m[k])
                st["rs_calls"] += f["rs"]
                textured = rnd.random() < f["tex_p"]
                ntex = f["tex"] if textured else 0
                if rnd.random() < f["p_sh"]:
                    cur_pair = pairs[rnd.randrange(npairs)]
                    st["shader_changes"] += 1
                pair = cur_pair if textured else pairs[npairs + pairs.index(cur_pair)]   # its untextured variant
                w.op(17, pair[0], pair[1], 1)
                if ntex and rnd.random() < f["p_tex"]:
                    cur_tex = [texs[rnd.randrange(len(texs))] for _ in range(f["tex"])]
                    st["tex_changes"] += 1
                for s in range(ntex):
                    w.op(16, s, cur_tex[s])
                    for (ty, v) in SAMP:       # the engine re-sets the sampler states of each stage it binds
                        w.op(15, s, ty, v)
                if rnd.random() < f["p_geo"]:
                    cur_geo = geos[rnd.randrange(len(geos))]
                w.op(18, cur_geo)
                w.op(19, f["vs"] // 16, f["ps"] // 16)
                st["const_bytes"] += f["vs"] + f["ps"]
                w.op(20, 4, 0, 64, 0, 1, ntex, f["layer"], rnd.randrange(65536))
                st["draws"] += 1
        w.op(11)
    w.op(0)
    for s in stats.values():
        for k in list(s):
            s[k] //= frames
    return w.bytes(), stats


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    spec = {"frames": FRAMES, "families": FAMILIES, "targets": TARGETS, "rs_order": RS_ORDER,
            "materials": MATERIALS, "sampler_states_per_stage": SAMP, "streams": {}}
    jobs = [("fam_" + f["name"], [f], 1) for f in FAMILIES] + [("mix_lo", FAMILIES, 0), ("mix_hi", FAMILIES, 1)]
    # leave-one-out: mix_hi without one family; mix_hi - wo_<f> = the family's marginal cost inside the full frame
    jobs += [("wo_" + f["name"], [g for g in FAMILIES if g is not f], 1) for f in FAMILIES]
    for name, fams, level in jobs:
        data, stats = gen(fams, level)
        open(os.path.join(out, name + ".nxd3d"), "wb").write(data)
        spec["streams"][name] = {"bytes": len(data), "per_frame": stats}
    data, _ = gen([dict(FAMILIES[0], draws=(0, 0))], 1)
    open(os.path.join(out, "empty.nxd3d"), "wb").write(data)
    spec["streams"]["empty"] = {"bytes": len(data), "per_frame": {}}
    json.dump(spec, open(os.path.join(out, "streams.json"), "w"), indent=1)
    print("streams: %s" % ", ".join(sorted(spec["streams"])))


if __name__ == "__main__":
    main()
