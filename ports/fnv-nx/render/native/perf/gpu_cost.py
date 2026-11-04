#!/usr/bin/env python3
"""fnv-nx P6n: GPU cost estimate from S6n's shader package (render/native/shaders/build.py output).

    python3 -I gpu_cost.py <shaders dir (.glsl + .dksh)> <uam> <out.json>

Per DKSH: registers (DkshProgramHeader.num_gprs), Maxwell instructions (code words that are not scheduling words,
not uam's NOP / "BRA $" padding), and from uam's TGSI (-t): static instruction count, texture instructions, and a
dynamic estimate (the light loop of lit*.frag run LIGHTS times; every other TGSI loop is a compiled switch and runs
once; both arms of every IF are counted, so branchy epilogues are upper bounds). maxwell_dyn = maxwell_static x
tgsi_dyn / tgsi_static.

Scene model (MODEL below, every number is copied into the JSON): per family, covered pixels = target pixels x
coverage x overdraw; fragment time = pixels x max(ALU: maxwell_dyn / 256 lanes, TEX: tex x TEX_COST / 16 TMUs,
ROP: 1 / 16 (depth-only 1 / 32)) / clock, ALU and TEX divided by an issue efficiency (EFF: 1.0 and 0.6 give the
lo/hi estimate); vertex time = draws x vertices x vs maxwell_dyn / 256 / clock; frame
time = max(sum over families, DRAM traffic / (25.6 GB/s x efficiency)). No caches, no early-z rejection benefit
beyond the overdraw factor, no cross-family overlap, no CPU/GPU sync stalls.
"""
import json, os, re, struct, subprocess, sys

LIGHTS = 3                  # active lights per lit draw (sun + 2 point lights; the shader caps at 8)
TEX_COST = 1.5              # TMU cycles per texture instruction (bilinear 1, trilinear 2: mixed)
EFF = (1.0, 0.6)            # SM issue efficiency (ALU and TMU): 1.0 = peak (lo estimate), 0.6 = latency/occupancy bound (hi)
GPU = {"name": "Tegra X1 GM20B", "sm": 2, "lanes": 256, "tmu": 16, "rop": 16, "dram_gbs": 25.6, "dram_eff": 0.6,
       "clocks_mhz": {"docked": 768.0, "handheld": 384.0, "handheld_boost": 460.8}}
CONFIGS = [   # (name, clock key, width, height)
    ("docked_720p", "docked", 1280, 720), ("docked_1080p", "docked", 1920, 1080),
    ("handheld_720p", "handheld", 1280, 720), ("handheld_540p", "handheld", 960, 540),
]
# family -> shaders, coverage of its target, overdraw, target scale (relative to the main target), vertices/draw,
# DRAM bytes per covered pixel (colour + depth traffic after compression, texture misses).
MODEL = {
    "shadow":       dict(vs="depth.vert", ps="depth.frag", target=(1024, 1024, 3), coverage=0.6, overdraw=1.5, verts=600, bpp=6, depth_only=True),
    "water_refl":   dict(vs="lit.vert", ps="lit.frag", scale=0.25, coverage=0.8, overdraw=1.5, verts=400, bpp=12),
    "lit":          dict(vs="lit.vert", ps="lit_spec.frag", coverage=0.55, overdraw=1.8, verts=600, bpp=12),
    "land":         dict(vs="land.vert", ps="land.frag", coverage=0.45, overdraw=1.2, verts=1089, bpp=14),
    "lod":          dict(vs="lit.vert", ps="lit.frag", coverage=0.15, overdraw=1.2, verts=400, bpp=12),
    "trees":        dict(vs="lit.vert", ps="lit.frag", coverage=0.15, overdraw=2.5, verts=300, bpp=12),
    "grass":        dict(vs="nolight_tex.vert", ps="nolight_tex.frag", coverage=0.20, overdraw=3.0, verts=2000, bpp=10),
    "actors":       dict(vs="lit_vc.vert", ps="lit_spec.frag", coverage=0.05, overdraw=1.3, verts=1500, bpp=12),
    "sky":          dict(vs="sky.vert", ps="sky_clouds.frag", coverage=0.35, overdraw=1.0, verts=500, bpp=8, draws=(4, 6)),
    "particles":    dict(vs="nolight_texvc.vert", ps="nolight_tex.frag", coverage=0.10, overdraw=4.0, verts=200, bpp=12),
    "imagespace_ui": dict(vs="quad.vert", ps="tex.frag", coverage=3.5, overdraw=1.0, verts=4, bpp=12),
}
NOP, BRA0, BRA1 = 0x50b0000000070f00, 0xe2400fffff07000f, 0xe2400fffff87000f
TEXOPS = {"TEX", "TXB", "TXL", "TXD", "TXP", "TXF", "TG4", "TEX_LZ", "TXF_LZ", "TEX2", "TXB2", "TXL2", "LODQ"}


def dksh_stats(path):
    d = open(path, "rb").read()
    magic, hsz, csz, codesz, poff, nprog = struct.unpack_from("<6I", d, 0)
    assert magic == 0x48534B44, path
    t, ep, gprs, cb1o, cb1s, scratch = struct.unpack_from("<6I", d, poff)
    code = d[csz:csz + codesz]
    start = 0x80 if t != 5 else 0
    end = cb1o if cb1s else len(code)
    words = [struct.unpack_from("<Q", code, o)[0] for o in range(start, end - 7, 8)]
    insns = sum(1 for i, w in enumerate(words) if i % 4 and w not in (NOP, BRA0, BRA1))
    return {"gprs": gprs, "code_bytes": codesz, "maxwell_static": insns, "scratch_per_warp": scratch,
            "occupancy_warps_per_sm": min(64, 65536 // (32 * ((gprs + 7) & ~7))) if gprs else 64}


def tgsi_stats(uam, glsl, stage, tmp, loop_iters):
    out = os.path.join(tmp, os.path.basename(glsl) + ".tgsi")
    subprocess.run([uam, "-s", stage, "-t", out, "-o", out + ".dksh", glsl], check=True, capture_output=True)
    ins = []
    for line in open(out):
        m = re.match(r"\s*\d+:\s*(\S+)", line)
        if m:
            ins.append(m.group(1))
    static = sum(1 for o in ins if o not in ("END",))
    tex = sum(1 for o in ins if o in TEXOPS)
    # dynamic: the first `loop_iters` top-level loops (GLSL for-loops, in order) run LIGHTS times
    dyn, depth, loop_no, mult = 0.0, 0, 0, [1.0]
    for o in ins:
        if o == "BGNLOOP":
            m = LIGHTS if (depth == 0 and loop_no < loop_iters) else 1
            if depth == 0:
                loop_no += 1
            depth += 1; mult.append(mult[-1] * m)
            continue
        if o == "ENDLOOP":
            depth -= 1; mult.pop()
            continue
        if o != "END":
            dyn += mult[-1]
    return {"tgsi_static": static, "tgsi_dyn": dyn, "tex": tex}


def shader_table(sdir, uam, tmp):
    os.makedirs(tmp, exist_ok=True)
    tab = {}
    for f in sorted(os.listdir(sdir)):
        if not f.endswith(".dksh"):
            continue
        name = f[:-5]
        stage = name.rsplit(".", 1)[1]
        glsl = os.path.join(sdir, name + ".glsl")
        loops = len(re.findall(r"\bfor\s*\(", open(glsl).read()))
        s = dksh_stats(os.path.join(sdir, f))
        s.update(tgsi_stats(uam, glsl, stage, tmp, loops))
        s["light_loops"] = loops
        s["maxwell_dyn"] = round(s["maxwell_static"] * s["tgsi_dyn"] / max(1, s["tgsi_static"]), 1)
        tab[name] = s
    return tab


def scene(tab, mix_draws, cfg, eff=1.0):
    name, clk, w, h = cfg
    hz = GPU["clocks_mhz"][clk] * 1e6
    fams, total_s, dram = {}, 0.0, 0.0
    for fam, m in MODEL.items():
        draws = mix_draws.get(fam, m.get("draws", (0, 0))[1])
        vs, ps = tab[m["vs"]], tab[m["ps"]]
        if "target" in m:
            tw, th, n = m["target"]; tpx = tw * th * n
        else:
            tpx = w * h * m.get("scale", 1.0)
        px = tpx * m["coverage"] * m["overdraw"]
        alu = ps["maxwell_dyn"] / GPU["lanes"] / eff
        texc = ps["tex"] * TEX_COST / GPU["tmu"] / eff
        rop = 1.0 / (2 * GPU["rop"]) if m.get("depth_only") else 1.0 / GPU["rop"]
        frag_cyc = px * max(alu, texc, rop)
        vtx_cyc = draws * m["verts"] * vs["maxwell_dyn"] / GPU["lanes"] / eff
        t = (frag_cyc + vtx_cyc) / hz
        b = px * m["bpp"]
        fams[fam] = {"draws": draws, "pixels": round(px), "bound": ["alu", "tex", "rop"][[alu, texc, rop].index(max(alu, texc, rop))],
                     "frag_ms": round(frag_cyc / hz * 1e3, 3), "vertex_ms": round(vtx_cyc / hz * 1e3, 3), "ms": round(t * 1e3, 3),
                     "dram_mb": round(b / 1e6, 2)}
        total_s += t; dram += b
    bw_s = dram / (GPU["dram_gbs"] * 1e9 * GPU["dram_eff"])
    frame = max(total_s, bw_s)
    return {"config": name, "width": w, "height": h, "gpu_mhz": GPU["clocks_mhz"][clk], "efficiency": eff, "families": fams,
            "compute_ms": round(total_s * 1e3, 2), "dram_ms": round(bw_s * 1e3, 2), "frame_ms": round(frame * 1e3, 2),
            "bound": "compute" if total_s >= bw_s else "dram"}


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    sdir, uam, out = sys.argv[1:]
    tab = shader_table(sdir, uam, os.path.join(os.path.dirname(os.path.abspath(out)), "tgsi"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gen_stream
    scenes = {}
    for lvl, key in ((0, "lo"), (1, "hi")):
        draws = {f["name"]: f["draws"][lvl] for f in gen_stream.FAMILIES}
        scenes["mix_" + key] = [scene(tab, draws, c, e) for c in CONFIGS for e in EFF]
    res = {"method": __doc__.strip().split("\n\n", 2)[2], "assumptions": {"lights": LIGHTS, "tex_cost": TEX_COST, "efficiency": EFF, "gpu": GPU,
           "configs": CONFIGS, "families": MODEL}, "shaders": tab, "scenes": scenes}
    json.dump(res, open(out, "w"), indent=1)
    for k, v in scenes.items():
        print(k, ", ".join("%s@%.1f %.1f ms (%s)" % (s["config"], s["efficiency"], s["frame_ms"], s["bound"]) for s in v))


if __name__ == "__main__":
    main()
