#!/usr/bin/env python3
"""engine_fold.py - fold engine_path.py's count of the engine's own D3D9 path into report.json (stream P6n).

    python3 -I render/native/perf/engine_fold.py <report.json> <engine base.json> [--lo <json>] [--hi <json>]
        [--meta key=value ...]

report.py calls fold() itself when <work>/out/engine_path.json exists (and engine_path_lo/_hi.json for the range).
Only aggregates go into report.json: per-draw / per-frame instruction counts, per-function totals under Gamebryo
names, D3D calls per draw, coverage. The frame budget is the owner's target: 60 fps = 16.7 ms, 720p handheld.

Split of the counted path (RENDER_PERF.md §13):
* d3d9_path - what the native renderer replaces (override rows: Do_RenderShape/Tristrips, NiDX9RenderState): the
  generic Gamebryo draw (render-mesh pass loop, shader pipeline, render-state / texture-stage / shader-program /
  constant-map application, the filter in front of every device call);
* kept - engine code that stays on the native path until the families replace it: BSBatchRenderer's per-geometry
  pass and the BSShader class's Prepare/Update/Finish (labels ending in "(kept engine code)").
"""
import json, sys

FRAME_BUDGET_MS = 16.7          # owner: 60 fps, whole frame, 720p handheld
FRAME_TARGET = "60"
KEPT = "(kept engine code)"


def _parts(e):
    fn = e["per_draw"]["self_exp_by_function"]
    kept = sum(v for k, v in fn.items() if k.endswith(KEPT))
    return {"d3d9_path": round(e["per_draw"]["exp"] - kept, 1), "kept": round(kept, 1)}


def fold(rep, base, lo=None, hi=None, meta=None):
    a = rep["assumptions"]; ipc, clocks = a["ipc"], a["clock_ghz"]
    gate = lambda instr: instr / (ipc[0] * clocks[0] * 1e9) * 1e3        # ms at IPC 0.6, 1.02 GHz
    best = lambda instr: instr / (ipc[1] * clocks[1] * 1e9) * 1e3        # ms at IPC 1.0, 1.785 GHz
    lo = lo or base; hi = hi or base
    pb, pl, ph = _parts(base), _parts(lo), _parts(hi)
    pd = {"lo": round(lo["per_draw"]["exp"]), "exp": round(base["per_draw"]["exp"]), "hi": round(hi["per_draw"]["exp"]),
          "path_min": base["per_draw"]["min"], "path_max": base["per_draw"]["max"]}
    frames = {}
    for mix in ("mix_lo", "mix_hi"):
        n = rep["frame"][mix]["draws"]
        frames[mix] = {"draws": n,
                       "instr": {"d3d9_path": round(pb["d3d9_path"] * n), "kept": round(pb["kept"] * n),
                                 "total": round(base["per_draw"]["exp"] * n),
                                 "total_lo": round(lo["per_draw"]["exp"] * n), "total_hi": round(hi["per_draw"]["exp"] * n)},
                       "ms_gate": {"d3d9_path": round(gate(pb["d3d9_path"] * n), 3), "kept": round(gate(pb["kept"] * n), 3),
                                   "total": round(gate(base["per_draw"]["exp"] * n), 3),
                                   "total_hi": round(gate(hi["per_draw"]["exp"] * n), 3)},
                       "ms_best": {"total": round(best(base["per_draw"]["exp"] * n), 3)}}
    us_draw = lambda ins, i, c: ins / (ipc[i] * clocks[c] * 1e3)
    eng = {
        "source": "MEASURED (static path count) on the AArch64 ILP32 prepared objects, render/native/perf/engine_path.py; "
                  "replaces the assumption of PERF.md §5 / RENDER_PERF.md §3.7",
        "replaced_assumption": {"per_draw_instr": [1000, 3000], "per_draw_us": [10, 20],
                                "text": "engine's own per-draw work before the device: 1-3 k instructions [E] (PERF.md §5); "
                                        "10-20 us/draw (RENDER_PERF.md §3.7, mostly this side)"},
        "inputs": meta or {},
        "per_draw_instr": pd,
        "per_draw_instr_rule": "exp = static expectation over the hot path (branch rules and loop trips of the spec: mix_hi "
                               "draw-weighted RS / stage / constant counts, 81 % of render states redundant); lo / hi = "
                               "the same with the spec's low / high sensitivity (redundancy 90 % / 60 %, 1 / 4 state-group "
                               "entries per pass); path_min / path_max = shortest / longest path, bounds only",
        "per_draw_us": {"gate": round(us_draw(base["per_draw"]["exp"], 0, 0), 3),
                        "hi": round(us_draw(hi["per_draw"]["exp"], 0, 0), 3),
                        "best": round(us_draw(lo["per_draw"]["exp"], 1, 1), 3)},
        "parts_per_draw_exp": {"d3d9_path (replaced by the native renderer)": pb["d3d9_path"],
                               "kept (BSBatchRenderer + BSShader Prepare/Update/Finish, stays until the families replace it)": pb["kept"],
                               "range_lo": pl, "range_hi": ph},
        "by_function_per_draw_exp": base["per_draw"]["self_exp_by_function"],
        "d3d_calls_per_draw_exp": base["per_draw"]["d3d_calls_exp"],
        "roles_per_invocation": {r: {k: v[k] for k in ("min", "exp", "max")} for r, v in base["roles"].items() if v.get("found")},
        "frame": frames,
        "coverage": base["coverage"],
        "limits": "static count (no execution): branch probabilities are rules, not measurements; 69 indirect calls stay "
                  "unresolved (mostly geometry/skin getters and the strip path) and count their call only; the shader "
                  "class is the lit one (shadow-light) for the BSShader virtuals; device calls end at our D3D9 front "
                  "(counted in §1); per-frame fixed engine work (BeginFrame/EndFrame, target groups) is not in it",
    }
    rep["engine_d3d9_path"] = eng
    a["not_counted"] = ("engine update, cull, accumulation; skinning palettes; dynamic vertex data. The engine's own D3D9 "
                        "draw path is now measured: engine_d3d9_path (it was assumed 1-3 k instructions per draw)")
    a["engine_d3d9_path"] = eng["source"]
    a["frame_budget_ms"] = FRAME_BUDGET_MS
    # the D3D9 (Nine) side: st/nine (measured, scaled to A64) + the engine path in front of it (measured)
    nine = rep["nine"]
    nine["with_engine_a64_instr"] = {m: round(nine["nine_a64_estimate_instr"][m] + frames[m]["instr"]["total"])
                                     for m in frames}
    nat = {m: rep["frame"][m]["instr"]["main"] + rep["frame"][m]["instr"]["render"] for m in frames}
    nine["with_engine_over_native_with_kept"] = {
        m: round(nine["with_engine_a64_instr"][m] / max(1, nat[m] + frames[m]["instr"]["kept"]), 2) for m in frames}
    nine["with_engine_rule"] = ("D3D9 path per frame = engine_d3d9_path total (d3d9 path + kept) + st/nine A64 estimate; "
                                "native = front + deko3d recording + the kept engine part; nouveau still excluded")
    s = rep["summary"]
    s["frame_budget"] = {"target_fps": 60, "ms": FRAME_BUDGET_MS, "config": "handheld_720p",
                         "rule": "owner: 60 fps for the whole frame at 720p handheld; the gate reads "
                                 "targets_budget_fraction['60']"}
    d_hi, d_lo = rep["frame"]["mix_hi"]["draws"], rep["frame"]["mix_lo"]["draws"]
    pdu = s["per_draw_us"]
    nine_hi = nine["with_engine_a64_instr"]["mix_hi"] / d_hi; nine_lo = nine["with_engine_a64_instr"]["mix_lo"] / d_lo
    pdu["nine_with_engine"] = {"lo": round(min(us_draw(nine_hi, 1, 1), us_draw(nine_lo, 1, 1)), 3),
                               "hi": round(max(us_draw(nine_hi, 0, 0), us_draw(nine_lo, 0, 0)), 3)}
    pdu["nine_with_engine"]["gate"] = pdu["nine_with_engine"]["hi"]
    pdu["engine_d3d9_path"] = {"lo": eng["per_draw_us"]["best"], "hi": eng["per_draw_us"]["gate"],
                               "gate": eng["per_draw_us"]["gate"]}
    kept_ms = frames["mix_hi"]["ms_gate"]["kept"]
    fm = s["frame_ms"]
    fm["main_with_engine_kept"] = {"gate": round(fm["main"]["gate"] + kept_ms, 3), "kept_ms": kept_ms,
                                   "rule": "main gate + the kept engine part (BSBatchRenderer + BSShader prepare/update "
                                           "per draw, mix_hi, IPC 0.6 at 1.02 GHz)"}
    worst = max(fm["main_with_engine_kept"]["gate"], fm["render"]["gate"])
    for t, b in a["targets_ms"].items():
        e = s["targets_budget_fraction"][t]
        e["1.02_with_engine_kept"] = round(worst / b, 3)
        e["d3d9_path_main_1.02"] = round((frames["mix_hi"]["ms_gate"]["total"]
                                          + gate(nine["nine_a64_estimate_instr"]["mix_hi"])) / b, 3)
    s["targets_budget_fraction_rule"] += ("; '1.02_with_engine_kept' = the worst CPU thread with the kept engine part "
                                          "on main; 'd3d9_path_main_1.02' = the D3D9 fallback's main thread (engine path "
                                          "+ st/nine, no driver) / budget, for comparison")
    s["engine_d3d9_path_source"] = eng["source"]
    rehotspot(rep)
    return rep


def rehotspot(rep):
    """Shares of the 60 fps frame (16.7 ms); kept engine rows and the D3D9-path breakdown added; top10 re-ranked."""
    h = rep["hotspots"]
    for k, rows in list(h.items()):
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict) and "ms" in r:
                    r.pop("share_of_22_2ms", None)
                    r["share_of_16_7ms"] = round(r["ms"] / FRAME_BUDGET_MS, 4)
    eng = rep.get("engine_d3d9_path")
    if eng:
        a = rep["assumptions"]; ipc, clocks = a["ipc"], a["clock_ghz"]
        n = rep["frame"]["mix_hi"]["draws"]
        ms = lambda per_draw: per_draw * n / (ipc[0] * clocks[0] * 1e9) * 1e3
        rows = []
        for name, v in eng["by_function_per_draw_exp"].items():
            kept = name.endswith(KEPT)
            rows.append({"kind": "cpu_engine_function", "name": name, "ms": round(ms(v), 3),
                         "share_of_16_7ms": round(ms(v) / FRAME_BUDGET_MS, 4), "instr_per_draw": v,
                         "thread": "main (engine)", "path": "kept on the native path" if kept else
                         "D3D9 path (replaced by the native renderer)", "source": "measured (engine_path.py, static)"})
        rows.sort(key=lambda r: -r["ms"])
        h["engine_d3d9_path_by_function"] = rows
        kept_rows = [r for r in rows if r["path"].startswith("kept")]
        h["cpu_by_function"] = sorted([r for r in h["cpu_by_function"] if r.get("kind") != "cpu_engine_function"]
                                      + kept_rows, key=lambda r: -r["ms"])[:30]
    cpu = [r for r in h["cpu_by_function"]][:20]
    gpu = [dict(r, config="handheld_720p") for r in h.get("gpu_by_pass/mix_hi/handheld_720p", [])]
    top = sorted(cpu + gpu, key=lambda r: -r["ms"])
    h["top10"] = top[:10]
    h["frame_budget_ms"] = FRAME_BUDGET_MS


def main():
    args = sys.argv[1:]
    rep_p, base_p = args[0], args[1]
    lo = hi = None; meta = {}
    i = 2
    while i < len(args):
        if args[i] == "--lo":
            lo = json.load(open(args[i + 1])); i += 2
        elif args[i] == "--hi":
            hi = json.load(open(args[i + 1])); i += 2
        elif args[i] == "--meta":
            k, v = args[i + 1].split("=", 1); meta[k] = v; i += 2
        else:
            raise SystemExit("unknown argument " + args[i])
    rep = json.load(open(rep_p))
    fold(rep, json.load(open(base_p)), lo, hi, meta)
    json.dump(rep, open(rep_p, "w"), indent=1)
    e = rep["engine_d3d9_path"]
    print(json.dumps({"per_draw_instr": e["per_draw_instr"], "per_draw_us": e["per_draw_us"],
                      "parts": e["parts_per_draw_exp"], "frame_mix_hi": e["frame"]["mix_hi"],
                      "frame_ms": rep["summary"]["frame_ms"], "budget": rep["summary"]["targets_budget_fraction"]["60"]},
                     indent=1))


if __name__ == "__main__":
    main()
