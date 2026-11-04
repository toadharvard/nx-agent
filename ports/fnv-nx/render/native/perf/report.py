#!/usr/bin/env python3
"""fnv-nx P6n: run every measurement and write render/native/perf/report.json (schema: docs/RENDER_PERF.md §5).

    python3 -I report.py <work dir> <out report.json>
<work dir> holds what run.sh built: b/ (native_drv.nso/.elf, native_drv_host, native_drv_cap, nine_drv,
n1/shaders), streams/ (gen_stream.py output), and gets out/ (raw results), cg/ (callgrind dumps).
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_stream, emu_count, cg_split  # noqa: E402

IPC = (0.6, 1.0)            # A57 instructions per cycle for this code: lo bound (branchy, dependent loads) / hi bound
CLOCKS_GHZ = (1.02, 1.785)  # Switch CPU: 1020 MHz (retail default, docked and handheld), 1785 MHz (boost profile)
FIF = 2                     # frames in flight for the measurement (3 overflows the UBO ring at the high mix, see notes)
STORE_GBS = 4.0             # effective A57 streaming-store bandwidth for the memory term (GB/s), hi estimate only
TARGETS_MS = {"30": 33.3, "45": 22.2, "60": 16.7}
# bytes the front writes per draw that miss the caches: 512 B UBO slice + 256 B epilogue (ring), 64 B packet,
# ~32 B encoded words; per frame the 65536-entry radix count table (256 KiB) per sort digit actually used
BYTES_PER_DRAW = 512 + 256 + 64 + 32


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def last_frame_segments(marks, frames):
    k = frames - 1
    m = {int(a): b for a, b in marks.items()}
    return m[4 * k + 2] - m[4 * k + 1], m[4 * k + 3] - m[4 * k + 2]


def guest(work, name, dry):
    b, s = os.path.join(work, "b"), os.path.join(work, "streams")
    r = emu_count.count(os.path.join(b, "native_drv.nso"), os.path.join(s, name + ".nxd3d"),
                        os.path.join(work, "emu", name + ("_dry" if dry else "")), dry, FIF)
    assert r["rc"] == 0, r["result"]
    return last_frame_segments(r["marks"], gen_stream.FRAMES), r["result"]


def callgrind(work, exe, name, dry, extra=()):
    cg = os.path.join(work, "cg"); os.makedirs(cg, exist_ok=True)
    base = os.path.join(cg, "%s.%s%s" % (os.path.basename(exe), name, ".dry" if dry else ""))
    for f in os.listdir(cg):
        if f.startswith(os.path.basename(base) + "."):
            os.remove(os.path.join(cg, f))
    sh(["valgrind", "--tool=callgrind", "--callgrind-out-file=" + base] + list(extra) +
       [exe, os.path.join(work, "streams", name + ".nxd3d"), "1" if dry else "0"] + (["-", "-", str(FIF)] if "native" in exe else []))
    parts = {}
    for f in os.listdir(cg):
        if not f.startswith(os.path.basename(base) + "."):
            continue
        p = os.path.join(cg, f)
        trig = [l for l in open(p) if l.startswith("desc: Trigger")]
        if trig and "mark" in trig[0]:
            mid = int(trig[0].split("mark")[1])
            parts[mid] = cg_split.split(p, 12)
    k = gen_stream.FRAMES - 1
    return parts[4 * k + 2], parts[4 * k + 3]


def per(x, n):
    return round(x / n, 1) if n else None


def us(instr, ipc, ghz):
    return instr / (ipc * ghz * 1e3)


def main():
    work, out = sys.argv[1], sys.argv[2]
    spec = json.load(open(os.path.join(work, "streams", "streams.json")))
    b = os.path.join(work, "b")
    names = ["fam_" + f["name"] for f in gen_stream.FAMILIES] + ["mix_lo", "mix_hi", "empty"] + \
        ["wo_" + f["name"] for f in gen_stream.FAMILIES]
    raw = {}
    for n in names:
        print("measuring", n, flush=True)
        (gb, gf), res = guest(work, n, False)
        (db, df), _ = guest(work, n, True)
        hb, hf = callgrind(work, os.path.join(b, "native_drv_host"), n, False)
        hdb, hdf = callgrind(work, os.path.join(b, "native_drv_host"), n, True)
        nb, nf = callgrind(work, os.path.join(b, "nine_drv"), n, False)
        ndb, ndf = callgrind(work, os.path.join(b, "nine_drv"), n, True)
        draws = sum(v["draws"] for v in spec["streams"][n]["per_frame"].values())
        raw[n] = {"draws": draws, "result": res,
                  "a64": {"main": gb - db, "render": gf - df, "decoder": db},
                  "x64_native": {"main": hb["measured"] - hdb["measured"], "render": hf["measured"] - hdf["measured"]},
                  "x64_nine": {"main": nb["measured"] - ndb["measured"], "render": nf["measured"] - ndf["measured"],
                               "driver_excluded": nb["driver"] + nf["driver"], "top_self": nb["top_self"],
                               "driver_entries": nb["driver_entries"][:8]}}
    # profile of the high mix (guest, last frame: packet build and Finish)
    k = gen_stream.FRAMES - 1
    pr = emu_count.count(os.path.join(b, "native_drv.nso"), os.path.join(work, "streams", "mix_hi.nxd3d"),
                         os.path.join(work, "emu", "mix_hi_prof"), False, FIF, 4 * k + 1, os.path.join(b, "native_drv.elf"))
    ovf = emu_count.count(os.path.join(b, "native_drv.nso"), os.path.join(work, "streams", "mix_hi.nxd3d"),
                          os.path.join(work, "emu", "mix_hi_fif3"), False, 3)
    # deko3d recording (host side), when perf/dk/ was built
    dk = None
    dkf = os.path.join(work, "out", "dk_record.json")
    if os.path.exists(dkf):
        dk = json.load(open(dkf))
    rep = build_report(spec, raw, pr, ovf, dk, work)
    json.dump(rep, open(out, "w"), indent=1)
    json.dump(raw, open(os.path.join(work, "out", "raw.json"), "w"), indent=1)
    print(json.dumps(rep["summary"], indent=1))


def build_report(spec, raw, prof, ovf, dk, work):
    fams = {}
    for f in gen_stream.FAMILIES:
        r = raw["fam_" + f["name"]]; n = r["draws"]
        a, x, nn = r["a64"], r["x64_native"], r["x64_nine"]
        # marginal cost inside the full high mix: mix_hi - (mix_hi without this family)
        full, wo = raw["mix_hi"], raw["wo_" + f["name"]]
        dn = full["draws"] - wo["draws"]
        dm = full["a64"]["main"] - wo["a64"]["main"]; dr = full["a64"]["render"] - wo["a64"]["render"]
        dxn = sum(full["x64_native"][k] - wo["x64_native"][k] for k in ("main", "render"))
        dxo = sum(full["x64_nine"][k] - wo["x64_nine"][k] for k in ("main", "render"))
        e = {"draws": {"lo": f["draws"][0], "hi": f["draws"][1]},
             "instr_per_draw": {"main": per(dm, dn), "render": per(dr, dn), "total": per(dm + dr, dn)},
             "x64": {"native_per_draw": per(dxn, dn), "nine_per_draw": per(dxo, dn),
                     "nine_over_native": round(dxo / dxn, 2) if dxn > 0 else None},
             "isolated": {"draws": n, "note": "this family alone in a frame: includes the whole per-frame fixed cost "
                                              "(the radix sort's 65536-entry tables) spread over its draws",
                          "instr_per_draw": {"main": per(a["main"], n), "render": per(a["render"], n)},
                          "x64_native_per_draw": per(x["main"] + x["render"], n), "x64_nine_per_draw": per(nn["main"] + nn["render"], n)},
             "params": {k: v for k, v in f.items() if k not in ("name",)}}
        if dk and ("wo_" + f["name"]) in dk.get("streams", {}):
            e["instr_per_draw"]["backend_record"] = per(dk["streams"]["mix_hi"]["record"] - dk["streams"]["wo_" + f["name"]]["record"], dn)
            e["instr_per_draw"]["total_with_backend"] = round(e["instr_per_draw"]["total"] + e["instr_per_draw"]["backend_record"], 1)
        fams[f["name"]] = e
    frames = {}
    for lvl in ("lo", "hi"):
        r = raw["mix_" + lvl]; n = r["draws"]; a = r["a64"]
        rec = dk["streams"]["mix_" + lvl]["record"] if dk and ("mix_" + lvl) in dk.get("streams", {}) else None
        render = a["render"] + (rec or 0)
        mem_ms = n * BYTES_PER_DRAW / (STORE_GBS * 1e9) * 1e3
        fr = {"draws": n, "instr": {"main": a["main"], "render_front": a["render"], "render_backend": rec, "render": render},
              "ms": {}}
        for ghz in CLOCKS_GHZ:
            g = {}
            for part, ins in (("main", a["main"]), ("render", render)):
                lo, hi = us(ins, IPC[1], ghz) / 1e3, us(ins, IPC[0], ghz) / 1e3 + (mem_ms if part == "main" else 0)
                g[part] = {"lo": round(lo, 3), "hi": round(hi, 3)}
            fr["ms"][str(ghz)] = g
        fr["memory_term_ms"] = round(mem_ms, 3)
        x, nn = r["x64_native"], r["x64_nine"]
        fr["x64"] = {"native": x["main"] + x["render"], "nine": nn["main"] + nn["render"],
                     "ratio_nine_over_native": round((nn["main"] + nn["render"]) / max(1, x["main"] + x["render"]), 2),
                     "nine_driver_excluded": nn["driver_excluded"]}
        frames["mix_" + lvl] = fr
    empty = raw["empty"]["a64"]
    hi = frames["mix_hi"]; lo = frames["mix_lo"]
    a64_over_x64 = (raw["mix_hi"]["a64"]["main"] + raw["mix_hi"]["a64"]["render"]) / max(1, hi["x64"]["native"])
    nine_a64_est = {k: v["x64"]["nine"] * a64_over_x64 for k, v in frames.items()}
    per_draw = {}
    for side, ins_hi, ins_lo in (("native", hi["instr"]["main"] + hi["instr"]["render"], lo["instr"]["main"] + lo["instr"]["render"]),
                                 ("nine", nine_a64_est["mix_hi"], nine_a64_est["mix_lo"])):
        d_hi, d_lo = hi["draws"], lo["draws"]
        lo_us = min(us(ins_hi / d_hi, IPC[1], CLOCKS_GHZ[1]), us(ins_lo / d_lo, IPC[1], CLOCKS_GHZ[1]))
        hi_us = max(us(ins_hi / d_hi, IPC[0], CLOCKS_GHZ[0]), us(ins_lo / d_lo, IPC[0], CLOCKS_GHZ[0]))
        per_draw[side] = {"lo": round(lo_us, 3), "hi": round(hi_us, 3), "gate": round(hi_us, 3)}
    g102 = hi["ms"]["1.02"]
    frame_ms = {"main": {"lo": lo["ms"]["1.785"]["main"]["lo"], "hi": g102["main"]["hi"], "gate": g102["main"]["hi"]},
                "render": {"lo": lo["ms"]["1.785"]["render"]["lo"], "hi": g102["render"]["hi"], "gate": g102["render"]["hi"]}}
    targets = {}
    for t, budget in TARGETS_MS.items():
        targets[t] = {}
        for ghz in CLOCKS_GHZ:
            m = hi["ms"][str(ghz)]
            worst = max(m["main"]["hi"], m["render"]["hi"])
            targets[t][str(ghz)] = "pass" if worst <= budget else "fail"
    budget_fraction = {t: {str(g): round(max(hi["ms"][str(g)]["main"]["hi"], hi["ms"][str(g)]["render"]["hi"]) / b, 3)
                           for g in CLOCKS_GHZ} for t, b in TARGETS_MS.items()}
    commit = sh(["git", "-C", HERE, "rev-parse", "HEAD"]).stdout.strip()
    # the commit changes when the branch is rebased; the tree of render/native (+ dirty flag) identifies the code
    tree = sh(["git", "-C", HERE, "rev-parse", "HEAD:ports/fnv-nx/render/native"]).stdout.strip()
    dirty = bool(sh(["git", "-C", HERE, "status", "--porcelain", "--", ".."]).stdout.strip())
    gpu = json.load(open(os.path.join(work, "out", "gpu.json")))
    gpu_sum = {}
    for mix, lst in gpu["scenes"].items():
        for s in lst:
            gpu_sum.setdefault(mix, {}).setdefault(s["config"], {})["eff_%.1f" % s["efficiency"]] = s["frame_ms"]
    gpu_targets = {}
    for t, budget in TARGETS_MS.items():
        gpu_targets[t] = {cfg: ("pass" if v["eff_0.6"] <= budget else "pass_at_peak_only" if v["eff_1.0"] <= budget else "fail")
                          for cfg, v in gpu_sum["mix_hi"].items()}
    passes = {}
    for name in ("mix_lo", "mix_hi"):
        pf = os.path.join(work, "out", name + ".passes.json")
        if os.path.exists(pf):
            passes[name] = json.load(open(pf))["frames"][-1]
    n0 = os.path.join(work, "out", "n0.passes.json")
    if os.path.exists(n0):
        passes["s6n_n0_capture"] = json.load(open(n0))
    rep = {
        "schema": "fnv-nx P6n report v1 (docs/RENDER_PERF.md §5)",
        "summary": {"commit": commit, "render_native_tree": tree, "worktree_dirty": dirty, "per_draw_us": per_draw, "frame_ms": frame_ms,
                    "per_draw_us_rule": "CPU per draw over a whole frame (fixed per-frame costs included), mix_lo and "
                                        "mix_hi: lo = IPC 1.0 at 1.785 GHz, hi = gate = IPC 0.6 at 1.02 GHz. native = "
                                        "front (StateTracker, translator, packet build, sort, encode) + deko3d recording "
                                        "(nxgpu.c record + libdeko3d); nine = st/nine without any pipe driver (nouveau's "
                                        "validation and push-buffer work is NOT in it), x86-64 count scaled to AArch64",
                    "frame_ms_rule": "mix_hi (3710 draws) at 1.02 GHz for hi/gate (IPC 0.6; main adds the store term), "
                                     "mix_lo (1490 draws) at 1.785 GHz IPC 1.0 for lo. main = packet build (engine-side "
                                     "StateTracker + Renderer::Render); render = Frame::Finish + deko3d recording",
                    "assumptions": {"ipc": {"lo": IPC[0], "hi": IPC[1]}, "clock_ghz": list(CLOCKS_GHZ),
                                    "gate_value": "gate = hi = IPC 0.6 at 1.02 GHz, mix_hi (+ memory term on main)"},
                    "targets": targets, "targets_budget_fraction": budget_fraction,
                    "targets_rule": "CPU verdict: max(main hi, render hi) of mix_hi at IPC 0.6 (the low IPC bound decides) <= "
                                    "33.3/22.2/16.7 ms; covers the renderer only (engine update/cull/accumulation not measured); "
                                    "GPU time is a separate estimate (gpu.targets)",
                    "render_backend_counted": dk is not None},
        "families": fams,
        "frame": frames,
        "fixed_per_frame_instr": {"main": empty["main"], "render": empty["render"]},
        "profile_mix_hi_a64": dict(list(prof.get("profile", {}).items())[:30]),
        "ring_overflow_at_3_frames_in_flight": ovf["result"],
        "nine": {"method": "x86-64 callgrind, same .nxd3d stream through both paths: native front (null backend, "
                           "-O2) vs Gallium Nine 20.1 st/nine (+cso, u_upload, util, libc it calls; release -O3) with "
                           "every call into softpipe (state objects, draw module, rasterizer, flush) excluded "
                           "(perf/cg_split.py); CSMT off (softpipe lacks PIPE_CAP_BUFFER_MAP_PERSISTENT_COHERENT); the "
                           "engine's D3D9 redundancy filter (NiDX9RenderState) modelled and counted on the Nine side",
                 "ratio_same_isa": {k: v["x64"]["ratio_nine_over_native"] for k, v in frames.items()},
                 "per_family_ratio": {k: v["x64"]["nine_over_native"] for k, v in fams.items()},
                 "a64_over_x64_native": round(a64_over_x64, 3),
                 "nine_a64_estimate_instr": {k: round(v) for k, v in nine_a64_est.items()},
                 "nine_a64_estimate_rule": "x86-64 Nine count x (native a64 / native x86-64): assumes Nine's code has the "
                                           "same instruction ratio between the ISAs as the front; not measured",
                 "top_self_mix_hi": raw["mix_hi"]["x64_nine"]["top_self"],
                 "driver_entries_mix_hi": raw["mix_hi"]["x64_nine"]["driver_entries"]},
        "gpu": {"model": gpu["method"], "assumptions": gpu["assumptions"], "shaders": gpu["shaders"], "scenes": gpu["scenes"],
                "summary_ms": gpu_sum, "targets": gpu_targets},
        "passes": passes,
        "backend_record": dk,
        "assumptions": {"ipc": IPC, "clock_ghz": CLOCKS_GHZ, "frames_in_flight": FIF, "store_gbs": STORE_GBS,
                        "bytes_per_draw_memory_term": BYTES_PER_DRAW, "targets_ms": TARGETS_MS,
                        "stream_frames": gen_stream.FRAMES, "measured_frame": "last (warm caches, same draws as frame 0)",
                        "mix": spec["families"], "targets": spec["targets"], "materials": spec["materials"],
                        "rs_order": spec["rs_order"], "sampler_states_per_stage": spec["sampler_states_per_stage"],
                        "cache_misses": "not modelled by the counter; the IPC range is meant to cover L1 misses of the "
                                        "front's tables, the hi estimate of the main thread adds a streaming-store term "
                                        "(bytes_per_draw_memory_term / store_gbs)",
                        "a64_counter": "Unicorn UC_HOOK_BLOCK, size/4 per basic block; ILP32 guest -O2 -mtune=cortex-a57; "
                                       "stream decoder subtracted (dry run)",
                        "not_counted": "engine code (update, cull, accumulation, NiDX9RenderState callers, constant-map "
                                       "walk), the family code that would fill per-draw constants (the stream hands "
                                       "ready blocks), skinning palettes, dynamic vertex data" +
                                       ("" if dk else ", the deko3d backend's recording (host/nxgpu.c + libdeko3d)")},
    }
    return rep


if __name__ == "__main__":
    main()
