#!/usr/bin/env python3
"""fnv-nx P6n: GPU cost estimate from S6n's shader package (render/native/shaders/build.py output).

    python3 -I gpu_cost.py <shaders dir (.glsl + .dksh)> <uam> <out.json> [--real <sdp2dksh -o dir of shaderpackage019>]

Per DKSH: registers (DkshProgramHeader.num_gprs), Maxwell instructions (code words that are not scheduling words,
not uam's NOP / "BRA $" padding), and from uam's TGSI (-t): static instruction count, texture instructions, and a
dynamic estimate (the light loop of lit*.frag run LIGHTS times; every other TGSI loop is a compiled switch and runs
once; both arms of every IF are counted, so branchy epilogues are upper bounds). maxwell_dyn = maxwell_static x
tgsi_dyn / tgsi_static.

Scene model (MODEL below, every number is copied into the JSON): per family, covered pixels = target pixels x
coverage x overdraw; fragment time = pixels x max(ALU: maxwell_dyn / 256 lanes, TEX: tex x TEX_COST / 16 TMUs,
ROP: 1 / 16, depth-only 1 / 32, 64-bit colour 1 / 8) / clock, ALU and TEX divided by an issue efficiency (EFF: 1.0 and
0.6 give the lo/hi estimate); ALU slots count fp16x2 instructions at half cost (FP16_RATE); DRAM bytes per pixel
from the family's render-target format (TARGET_FORMATS, colour x2 when blending), depth traffic x Z_COMPRESS and
TEX_BYTES per texture fetch; presets (PRESETS) give target size, shadow map and reflection size per config; vertex time = draws x vertices x vs maxwell_dyn / 256 / clock; frame
time = max(sum over families, DRAM traffic / (25.6 GB/s x efficiency)). No caches, no early-z rejection benefit
beyond the overdraw factor, no cross-family overlap, no CPU/GPU sync stalls.
"""
import json, os, re, struct, subprocess, sys

LIGHTS = 3                  # active lights per lit draw (sun + 2 point lights; the shader caps at 8)
TEX_COST = 1.5              # TMU cycles per texture instruction (bilinear 1, trilinear 2: mixed)
EFF = (1.0, 0.6)            # SM issue efficiency (ALU and TMU): 1.0 = peak (lo estimate), 0.6 = latency/occupancy bound (hi)
GPU = {"name": "Tegra X1 GM20B", "sm": 2, "lanes": 256, "tmu": 16, "rop": 16, "dram_gbs": 25.6, "dram_eff": 0.6,
       "clocks_mhz": {"docked": 768.0, "handheld": 384.0, "handheld_boost": 460.8}}
# Presets (RENDER_NATIVE.md §9.3), dynamic resolution OFF: the gate configs are docked_720p and handheld_720p.
# shadow: maps x size; refl: water reflection size. The *_dynres configs are informational (scene scaled).
PRESETS = {
    "docked_720p":    dict(clock="docked", w=1280, h=720, shadow=(3, 1024), refl=(512, 512), gate=True),
    "handheld_720p":  dict(clock="handheld", w=1280, h=720, shadow=(3, 512), refl=(256, 256), gate=True),
    "docked_1080p":   dict(clock="docked", w=1920, h=1080, shadow=(3, 1024), refl=(512, 512), gate=False),
    "handheld_540p_dynres": dict(clock="handheld", w=960, h=540, shadow=(3, 512), refl=(256, 256), gate=False),
}
# Render-target formats per target kind and their bytes per pixel. FNV's HDR path renders the scene into an fp16
# target (A16B16G16R16F) and runs the image-space chain on fp16 targets; S6n changes these here (or in
# render/native/perf/gpu_targets.json, which overrides this table) when the renderer's targets change.
TARGET_FORMATS = {"main": "RGBA16F", "refl": "RGBA16F", "imagespace": "RGBA16F", "shadow": "D24", "depth": "D24S8"}
FORMAT_BYTES = {"RGBA8": 4, "RGB10A2": 4, "R11G11B10F": 4, "RGBA16F": 8, "RG16F": 4, "R16F": 2, "D24": 4, "D24S8": 4, "D32F": 4}
Z_COMPRESS = 0.5            # depth traffic after Maxwell's Z compression
TEX_BYTES = 0.5             # DRAM bytes per texture fetch after the texture cache (BCn)
FP16_RATE = 2.0             # Tegra X1: HFMA2/HADD2/HMUL2 issue two fp16 lanes per fp32 lane
# family -> shaders, target kind, coverage, overdraw, vertices/draw; blend: colour read-modify-write;
# depth: 0 none, 1 test only, 2 test + write.
MODEL = {
    "shadow":       dict(vs="depth.vert", ps="depth.frag", target="shadow", coverage=0.6, overdraw=1.5, verts=600, depth=2, depth_only=True),
    "water_refl":   dict(vs="lit.vert", ps="lit.frag", target="refl", coverage=0.8, overdraw=1.5, verts=400, depth=2),
    "lit":          dict(vs="lit.vert", ps="lit_spec.frag", target="main", coverage=0.55, overdraw=1.8, verts=600, depth=2),
    "land":         dict(vs="land.vert", ps="land.frag", target="main", coverage=0.45, overdraw=1.2, verts=1089, depth=2),
    "lod":          dict(vs="lit.vert", ps="lit.frag", target="main", coverage=0.15, overdraw=1.2, verts=400, depth=2),
    "trees":        dict(vs="lit.vert", ps="lit.frag", target="main", coverage=0.15, overdraw=2.5, verts=300, depth=2),
    "grass":        dict(vs="nolight_tex.vert", ps="nolight_tex.frag", target="main", coverage=0.20, overdraw=3.0, verts=2000, depth=2),
    "actors":       dict(vs="lit_vc.vert", ps="lit_spec.frag", target="main", coverage=0.05, overdraw=1.3, verts=1500, depth=2),
    "sky":          dict(vs="sky.vert", ps="sky_clouds.frag", target="main", coverage=0.35, overdraw=1.0, verts=500, depth=1, draws=(4, 6)),
    "particles":    dict(vs="nolight_texvc.vert", ps="nolight_tex.frag", target="main", coverage=0.10, overdraw=4.0, verts=200, depth=1, blend=True),
    "imagespace_ui": dict(vs="quad.vert", ps="tex.frag", target="imagespace", coverage=3.5, overdraw=1.0, verts=4, depth=0),
}
# Real-like shader costs: the game's own programs (shaderpackage019.sdp, the SM3 package) translated by S6b's
# sdp2dksh (MojoShader glsldk -> uam), averaged per shader class over the programs that translate. Used as the
# D3D9-translation reference scene ("real019_"); family -> (vertex class, pixel class). Classes are name prefixes.
REAL_MAP = {
    "shadow": ("SLS vso", "SLS pso"), "water_refl": ("SLS vso", "SLS pso"), "lit": ("SLS vso", "SLS pso"),
    "land": ("SLS vso", "SLS pso"), "lod": ("DISTLOD vso", "SM pso"), "trees": ("STB vso", "STB pso"),
    "grass": ("GRASS vso", "STLEAF pso"), "actors": ("SKIN vso", "SKIN pso"), "sky": ("SKY vso", "SKY pso"),
    "particles": ("PAR vso", "PARTICLE pso"), "imagespace_ui": ("ISBLUR vso", "IS pso"),
}


def real_table(sdir):
    """class -> averaged stats from sdp2dksh's index.tsv + reflection JSON (only programs that translated)"""
    import collections
    acc = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for line in open(os.path.join(sdir, "index.tsv")):
        f = line.rstrip("\n").split("\t")
        if f[0] == "hash" or len(f) < 9 or f[4] != "ok":
            continue
        name, ext = f[2].rsplit(".", 1)
        base = re.sub(r"[0-9]+[A-Za-z]*$", "", name)
        tex = 0
        j = os.path.join(sdir, f[0] + ".json")
        if os.path.exists(j):
            tex = len(json.load(open(j)).get("samplers", []))
        keys = [base + " " + ext]
        if ext == "pso" and name.startswith("IS"):
            keys.append("IS pso")
        for k in keys:
            a = acc[k]; a[0] += 1; a[1] += int(f[7]); a[2] += int(f[8]); a[3] += tex
    tab = {}
    for k, (n, ins, gprs, tex) in acc.items():
        d = ins / n
        tab["real:" + k] = {"programs": n, "maxwell_static": round(d, 1), "maxwell_dyn": round(d, 1), "alu_slots": round(d, 1),
                            "fp16_dyn": 0, "gprs": round(gprs / n, 1), "tex": round(tex / n, 2)}
    return tab


NOP, BRA0, BRA1 = 0x50b0000000070f00, 0xe2400fffff07000f, 0xe2400fffff87000f
# Maxwell fp16x2 ALU opcodes, register forms (top 13 bits): HFMA2 0x5d00, HMUL2 0x5d08, HADD2 0x5d10 (envytools
# gm107 encodings; the cbuf/immediate forms are not decoded, so the fp16 count is a lower bound)
FP16_OPS = {0x5d00 >> 3, 0x5d08 >> 3, 0x5d10 >> 3}
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
    real = [w for i, w in enumerate(words) if i % 4 and w not in (NOP, BRA0, BRA1)]
    insns = len(real)
    fp16 = sum(1 for w in real if (w >> 51) in FP16_OPS)
    return {"gprs": gprs, "code_bytes": codesz, "maxwell_static": insns, "fp16_static": fp16, "scratch_per_warp": scratch,
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
        k = s["tgsi_dyn"] / max(1, s["tgsi_static"])
        s["maxwell_dyn"] = round(s["maxwell_static"] * k, 1)
        s["fp16_dyn"] = round(s["fp16_static"] * k, 1)
        # fp32-equivalent issue slots: fp16x2 instructions at FP16_RATE
        s["alu_slots"] = round(s["maxwell_dyn"] - s["fp16_dyn"] + s["fp16_dyn"] / FP16_RATE, 1)
        tab[name] = s
    return tab


def target_px(m, p):
    kind = m["target"]
    if kind == "shadow":
        n, sz = p["shadow"]; return n * sz * sz
    if kind == "refl":
        return p["refl"][0] * p["refl"][1]
    return p["w"] * p["h"]


def bytes_per_px(m, fmts):
    """DRAM bytes per covered pixel: colour write (x2 with blending), depth read (+ write), texture misses"""
    if m.get("depth_only"):
        colour = 0.0
    else:
        colour = FORMAT_BYTES[fmts[m["target"]]] * (2 if m.get("blend") else 1)
    dfmt = fmts["shadow"] if m["target"] == "shadow" else fmts["depth"]
    depth = FORMAT_BYTES[dfmt] * m.get("depth", 0) * Z_COMPRESS
    return colour + depth


def scene(tab, mix_draws, name, eff=1.0, fmts=None, shader_map=None):
    p = PRESETS[name]
    fmts = fmts or TARGET_FORMATS
    hz = GPU["clocks_mhz"][p["clock"]] * 1e6
    fams, total_s, dram = {}, 0.0, 0.0
    for fam, m in MODEL.items():
        draws = mix_draws.get(fam, m.get("draws", (0, 0))[1])
        if shader_map:
            vs, ps = tab["real:" + shader_map[fam][0]], tab["real:" + shader_map[fam][1]]
        else:
            vs, ps = tab[m["vs"]], tab[m["ps"]]
        px = target_px(m, p) * m["coverage"] * m["overdraw"]
        alu = ps["alu_slots"] / GPU["lanes"] / eff
        texc = ps["tex"] * TEX_COST / GPU["tmu"] / eff
        rop = 1.0 / (2 * GPU["rop"]) if m.get("depth_only") else 1.0 / GPU["rop"]
        if not m.get("depth_only") and FORMAT_BYTES[fmts[m["target"]]] >= 8:
            rop *= 2            # 64-bit colour targets: half ROP rate
        frag_cyc = px * max(alu, texc, rop)
        vtx_cyc = draws * m["verts"] * vs["alu_slots"] / GPU["lanes"] / eff
        t = (frag_cyc + vtx_cyc) / hz
        b = px * (bytes_per_px(m, fmts) + ps["tex"] * TEX_BYTES)
        fams[fam] = {"draws": draws, "pixels": round(px), "bound": ["alu", "tex", "rop"][[alu, texc, rop].index(max(alu, texc, rop))],
                     "frag_ms": round(frag_cyc / hz * 1e3, 3), "vertex_ms": round(vtx_cyc / hz * 1e3, 3), "ms": round(t * 1e3, 3),
                     "dram_mb": round(b / 1e6, 2), "vs": vs.get("name", m["vs"]) if not shader_map else shader_map[fam][0],
                     "ps": m["ps"] if not shader_map else shader_map[fam][1], "target_format": None if m.get("depth_only") else fmts[m["target"]]}
        total_s += t; dram += b
    bw_s = dram / (GPU["dram_gbs"] * 1e9 * GPU["dram_eff"])
    frame = max(total_s, bw_s)
    return {"config": name, "width": p["w"], "height": p["h"], "gpu_mhz": GPU["clocks_mhz"][p["clock"]], "efficiency": eff,
            "gate": p["gate"], "dynres": "off" if p["gate"] else "on (informational)", "families": fams,
            "compute_ms": round(total_s * 1e3, 2), "dram_ms": round(bw_s * 1e3, 2), "frame_ms": round(frame * 1e3, 2),
            "bound": "compute" if total_s >= bw_s else "dram"}


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    sdir, uam, out = sys.argv[1:4]
    real = sys.argv[sys.argv.index("--real") + 1] if "--real" in sys.argv else None
    tab = shader_table(sdir, uam, os.path.join(os.path.dirname(os.path.abspath(out)), "tgsi"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gen_stream
    fmts = dict(TARGET_FORMATS)
    over = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_targets.json")
    if os.path.exists(over):
        fmts.update(json.load(open(over)).get("formats", {}))
    scenes = {}
    for lvl, key in ((0, "lo"), (1, "hi")):
        draws = {f["name"]: f["draws"][lvl] for f in gen_stream.FAMILIES}
        scenes["mix_" + key] = [scene(tab, draws, c, e, fmts) for c in PRESETS for e in EFF]
    real_tab = {}
    if real and os.path.exists(os.path.join(real, "index.tsv")):
        real_tab = real_table(real)
        tab.update(real_tab)
        draws = {f["name"]: f["draws"][1] for f in gen_stream.FAMILIES}
        scenes["real019_mix_hi"] = [scene(tab, draws, c, e, fmts, REAL_MAP) for c in PRESETS for e in EFF]
    res = {"method": __doc__.strip().split("\n\n", 2)[2], "assumptions": {"lights": LIGHTS, "tex_cost": TEX_COST, "efficiency": EFF, "gpu": GPU,
           "presets": PRESETS, "target_formats": fmts, "format_bytes": FORMAT_BYTES, "z_compress": Z_COMPRESS,
           "tex_bytes": TEX_BYTES, "fp16_rate": FP16_RATE, "families": MODEL,
           "real_map": REAL_MAP if real_tab else None}, "shaders": tab, "scenes": scenes}
    json.dump(res, open(out, "w"), indent=1)
    for k, v in scenes.items():
        print(k, ", ".join("%s@%.1f %.1f ms (%s)" % (s["config"], s["efficiency"], s["frame_ms"], s["bound"]) for s in v))


if __name__ == "__main__":
    main()
