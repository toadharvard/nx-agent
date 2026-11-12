#!/usr/bin/env python3
"""d9rec_stats.py STREAM.d9s [--md OUT.md] [--json OUT.json] [--tsv-dir DIR] [--window N] [--expect-mode WxH@R] [--spec SPEC] [--title T]
Statistics (counts only) of one D9S1 call stream (render/d3d9/gen/STREAM.md): recorded from the original game, or written by a guest.
Prints a markdown report (stdout or --md); --json the same numbers; --tsv-dir writes perframe.tsv, methods.tsv, modes.tsv.
Frame index f = number of Present / PresentEx / swap-chain Present records (depth 0) that precede the record; the Present ends frame f.
Calls after the last Present are frame `frames` (trailing, counted separately).  Shader hash = FNV-1a 64 of the bytecode (as tools/compare).
FFP rule: a draw's vertex (pixel) stage is "none" when the last SetVertexShader (SetPixelShader) of that device passed id 0, "unknown" after
a state-block Apply (until the next Set), a Reset sets both to none, and the initial state is none.
--expect-mode: exit 3 unless every adapter mode returned for X8R8G8B8 is exactly that one mode.
--require-world: exit 4 unless the stream reached a world cell (WORLD-REACHED rule below; thresholds --world-min-draws/-frames/-textures).
WORLD-REACHED rule (a measured property of the original game's streams, not a guess from names): the stream has a run of >= 10 consecutive complete
frames that each have >= 100 draw calls and, over the run, bind >= 50 distinct textures (SetTexture with a non-null texture id).  Measured basis
(farm run of 783ec078, 4 recordings, 26,000 frames of boot, loading screens and the main menu): draws per frame never exceed 28 (median 4-19);
a world cell has hundreds of draws per frame.  Per-frame draw percentiles are printed whatever the verdict.
Exit 0 ok, 2 unreadable stream, 4 world not reached (with --require-world)."""
import argparse, collections, hashlib, json, os, re, struct, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "cmp"))
import d9s1  # noqa: E402

DEF_SPEC = os.path.join(HERE, "..", "..", "spec", "d3d9_spec.json")
DEVS = ("IDirect3DDevice9", "IDirect3DDevice9Ex")
FFP = ("SetFVF", "SetTransform", "MultiplyTransform", "SetLight", "LightEnable", "SetMaterial", "SetTextureStageState", "SetClipPlane",
       "SetNPatchMode", "ProcessVertices")
FMT = {22: "X8R8G8B8", 21: "A8R8G8B8", 23: "R5G6B5", 24: "X1R5G5B5"}


def key_of(b):
    return hashlib.sha256(bytes(b)).hexdigest()


def load(data, spec):
    """(header, records, error) - a failing record ends the decode there: the records before it are returned with the error text and offset"""
    try: h, r = d9s1.read_stream(data, spec); return h, r, None
    except d9s1.ReadError as ex:
        m = re.search(r"record at offset (\d+)", str(ex)); off = int(m.group(1)) if m else 44
        h, r = d9s1.read_stream(data[:off], spec); return h, r, (str(ex), off, len(data) - off)


def analyse(data, spec):
    hdr, recs, err = load(data, spec)
    names = {(m["iface"], m["name"]) for m in spec["methods"]} | {(None, e["name"]) for e in spec["exports"]}
    called = collections.Counter(); hr_fail = collections.Counter(); env_rec = collections.Counter(); env_items = collections.Counter()
    threads = collections.OrderedDict(); depth_hist = collections.Counter(); fr = []     # per-frame dicts
    new_fr = lambda: collections.Counter()
    fr.append(new_fr()); f = 0; texs = [set()]
    vs, ps, hashes = {}, {}, {"VS": collections.Counter(), "PS": collections.Counter()}
    creates = collections.Counter(); other_create_by_method = collections.Counter(); first_draw = None
    modes, mcount = [], []; first_hash_frame = {}
    for r in recs:
        key = (r.iface, r.name); called[key] += 1; c = fr[f]; c["calls"] += 1
        threads[r.thread] = threads.get(r.thread, 0) + 1; depth_hist[r.depth] += 1
        n, m = r.name, r.m; dev = r.iface in DEVS; byl = {i.label: i for i in r.items}
        ret = byl.get("ret")
        if ret is not None and m["ret_kind"] == "hresult" and ret.data is not None:
            v = struct.unpack("<I", ret.data)[0]
            if v & 0x80000000: hr_fail[(r.iface, n, "0x%08x" % v)] += 1
        ne = sum(1 for i in r.items if i.cls == 1 and i.rec)
        if ne: env_rec[key] += 1; env_items[key] += ne
        if n.startswith("Create") and dev:
            if n in ("CreateVertexShader", "CreatePixelShader"):
                k = "VS" if n == "CreateVertexShader" else "PS"; bc = byl["in:byte_code"].data; c["create_" + k] += 1; creates[k] += 1
                h = key_of(bc); hashes[k][h] += 1; first_hash_frame.setdefault((k, h), f)
            else: c["create_other"] += 1; other_create_by_method[n] += 1
        if any(p["kind"] == "locked" for p in m["params"]):
            c["locks"] += 1; c["lock_bytes_in"] += sum(len(i.data) for i in r.items if i.label.startswith("lock:") and i.label.endswith(":data") and i.data)
        if "closes" in m:
            c["unlocks"] += 1; i = byl.get("close:data"); c["unlock_bytes"] += len(i.data) if i is not None and i.data else 0
        if dev and n.startswith("Draw"):
            c["draws"] += 1; first_draw = f if first_draw is None else first_draw
            a, b = vs.get(r.this, 0), ps.get(r.this, 0)
            if a is None or b is None: c["draws_unknown_stage"] += 1
            else:
                if a == 0: c["draws_no_vs"] += 1
                if b == 0: c["draws_no_ps"] += 1
                if a == 0 and b == 0: c["draws_no_vs_no_ps"] += 1
        elif dev and n == "SetTexture":
            t = byl.get("in:pTexture")
            if t is not None and t.data and len(t.data) >= 4 and struct.unpack("<I", t.data[:4])[0]: texs[f].add(struct.unpack("<I", t.data[:4])[0])
        elif dev and n in ("SetVertexShader", "SetPixelShader"):
            (vs if n == "SetVertexShader" else ps)[r.this] = struct.unpack("<I", byl["in:pShader"].data)[0]
        elif dev and n in ("Reset", "ResetEx"): vs[r.this] = ps[r.this] = 0
        elif r.iface == "IDirect3DStateBlock9" and n == "Apply":
            for d in set(list(vs) + list(ps)): vs[d] = ps[d] = None
            c["stateblock_apply"] += 1
        if n in ("GetAdapterModeCount",) and ret is not None and ret.data:
            mcount.append((struct.unpack("<I", byl["in:Format"].data)[0], struct.unpack("<I", ret.data)[0]))
        if n == "EnumAdapterModes" and "out:pMode" in byl and byl["out:pMode"].data:
            w, h, rr, fm = struct.unpack("<4I", byl["out:pMode"].data[:16]); modes.append((struct.unpack("<I", byl["in:Format"].data)[0], struct.unpack("<I", byl["in:Mode"].data)[0], w, h, rr, fm))
        if n in ("Present", "PresentEx") and r.depth == 0 and (dev or (r.iface or "").startswith("IDirect3DSwapChain9")):
            f += 1; fr.append(new_fr()); texs.append(set())
    return dict(texs=texs, tail=[(r.thread, (r.iface or "").replace("IDirect3D", ""), r.name) for r in recs[-8:]], hdr=hdr, recs=recs, err=err, called=called, hr_fail=hr_fail, env_rec=env_rec, env_items=env_items, threads=threads, depth=depth_hist,
                fr=fr, frames=f, creates=creates, other_create=other_create_by_method, hashes=hashes, first_hash_frame=first_hash_frame, first_draw=first_draw,
                modes=modes, mcount=mcount, names=names, nbytes=len(data))


WORLD_DEFAULT = (100, 10, 50)   # min draws per frame, min consecutive frames, min distinct textures over the run


def pct(v, q):
    v = sorted(v); return v[min(len(v) - 1, int(q * len(v)))] if v else 0


def world_verdict(draws, texs, min_draws=WORLD_DEFAULT[0], min_frames=WORLD_DEFAULT[1], min_tex=WORLD_DEFAULT[2], window=100):
    """draws: draws per complete frame; texs: set of bound texture ids per complete frame.  Rule: see the module doc.  Pure function of the two lists."""
    runs, i, n = [], 0, len(draws)
    while i < n:
        if draws[i] >= min_draws:
            j = i
            while j < n and draws[j] >= min_draws: j += 1
            runs.append((j - i, i, len(set().union(*texs[i:j])))); i = j
        else: i += 1
    ok = [r for r in runs if r[0] >= min_frames and r[2] >= min_tex]
    best = max(runs, key=lambda r: (r[0], r[2]), default=(0, None, 0))
    last = draws[-window:]
    return {"reached": bool(ok), "rule": f">= {min_frames} consecutive frames with >= {min_draws} draws, >= {min_tex} distinct textures bound over the run",
            "thresholds": [min_draws, min_frames, min_tex], "frames": n, "frames_over_threshold": sum(1 for d in draws if d >= min_draws),
            "best_run_frames": best[0], "best_run_start": best[1], "best_run_textures": best[2], "first_world_frame": ok[0][1] if ok else None,
            "draws_pct": {"all": {q: pct(draws, q / 100) for q in (50, 90, 99, 100)}, "last": {q: pct(last, q / 100) for q in (50, 90, 99, 100)}},
            "last_window": len(last), "distinct_textures_all": len(set().union(*texs)) if texs else 0}


def build(a, spec, window, world=WORLD_DEFAULT):
    fr, f = a["fr"], a["frames"]; recs = a["recs"]
    tot = collections.Counter()
    for c in fr: tot.update(c)
    cf = [i for i, c in enumerate(fr) if c["create_VS"] or c["create_PS"]]
    win = list(range(max(0, f - window), f)); hist = collections.Counter(fr[i]["create_VS"] + fr[i]["create_PS"] for i in win)
    fd = a["first_draw"]; after = sum(fr[i]["create_VS"] + fr[i]["create_PS"] for i in range(fd, len(fr))) if fd is not None else 0
    R = {"records": len(recs), "bytes": a["nbytes"], "frames": f, "trailing_calls": fr[f]["calls"] if f < len(fr) else 0,
         "decode_error": a["err"], "threads": len(a["threads"]), "calls_per_thread": list(a["threads"].values()), "max_depth": max(a["depth"]) if a["depth"] else 0,
         "nested_records": sum(v for k, v in a["depth"].items() if k > 0),
         "creates": {"VS": a["creates"]["VS"], "PS": a["creates"]["PS"], "distinct_VS": len(a["hashes"]["VS"]), "distinct_PS": len(a["hashes"]["PS"]),
                     "distinct_either": len(set(a["hashes"]["VS"]) | set(a["hashes"]["PS"])), "hash": "sha256", "last_frame_with_create": cf[-1] if cf else None,
                     "first_frame_with_create": cf[0] if cf else None, "frames_with_create": len(cf), "first_draw_frame": fd, "creates_from_first_draw_frame_on": after,
                     "window": [win[0], win[-1]] if win else None, "window_hist": dict(sorted(hist.items()))},
         "other_create_by_method": dict(a["other_create"]), "totals": dict(tot), "called": {f"{i or '(export)'}::{n}": v for (i, n), v in sorted(a["called"].items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))},
         "env_by_method": {f"{i or '(export)'}::{n}": [a["env_rec"][(i, n)], a["env_items"][(i, n)]] for (i, n) in sorted(a["env_rec"], key=lambda k: (str(k[0]), k[1]))},
         "failed_hresult": {f"{i or '(export)'}::{n} {h}": v for (i, n, h), v in sorted(a["hr_fail"].items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2]))},
         "modes": a["modes"], "mode_counts": a["mcount"]}
    nc = f if f < len(fr) else len(fr)
    R["world"] = world_verdict([fr[i]["draws"] for i in range(nc)], a["texs"][:nc], *world, window=window)
    R["tail"] = {"calls_after_last_present": R["trailing_calls"], "last_calls": [f"t{t} {i}::{n}" for t, i, n in a["tail"]]}
    R["never_called"] = sorted(f"{i}::{n}" for (i, n) in a["names"] - set(a["called"]) if i)
    R["never_called_exports"] = sorted(n for (i, n) in a["names"] - set(a["called"]) if not i)
    call = lambda *ns: {n: sum(v for (i, k), v in a["called"].items() if k == n and i in DEVS) for n in ns}
    R["ffp_calls"] = call(*FFP); R["ffp_calls"]["SetFVF_etc_total"] = sum(R["ffp_calls"].values())
    R["stateblock_methods"] = {f"{i}::{n}": v for (i, n), v in sorted(a["called"].items(), key=lambda kv: (str(kv[0][0]), kv[0][1])) if i == "IDirect3DStateBlock9" or "StateBlock" in n}
    R["query_methods"] = {f"{i}::{n}": v for (i, n), v in sorted(a["called"].items(), key=lambda kv: (str(kv[0][0]), kv[0][1])) if (i or "").endswith("Query9") or "Query" in n}
    R["frame_rows"] = [[i, c["calls"], c["create_VS"], c["create_PS"], c["create_other"], c["locks"], c["unlocks"], c["lock_bytes_in"], c["unlock_bytes"], c["draws"],
                        c["draws_no_vs"], c["draws_no_ps"], c["draws_unknown_stage"]] for i, c in enumerate(fr)]
    return R


COLS = ["frame", "calls", "create_VS", "create_PS", "create_other", "locks", "unlocks", "lock_bytes_in", "unlock_bytes", "draws", "draws_no_vs", "draws_no_ps", "draws_unknown_stage"]


def world_md(R):
    w, p, t = R["world"], R["world"]["draws_pct"], R["tail"]
    pr = lambda d: ", ".join(f"p{q} {v}" for q, v in d.items())
    return ["## 11. World reached (does the stream show a world cell, not the menu / loading screens?)",
            f"- verdict: {'WORLD-REACHED' if w['reached'] else 'NOT WORLD-REACHED (menu / loading only)'}; rule: {w['rule']}",
            f"- draws per frame, all {w['frames']} complete frames: {pr(p['all'])}; last {w['last_window']} frames: {pr(p['last'])}; frames with >= {w['thresholds'][0]} draws: {w['frames_over_threshold']}",
            f"- longest run of frames over the draw threshold: {w['best_run_frames']} (from frame {w['best_run_start']}), distinct textures bound in it: {w['best_run_textures']}; distinct textures bound in the stream: {w['distinct_textures_all']}",
            f"- stream end: {t['calls_after_last_present']} calls after the last Present; last calls: {'; '.join(t['last_calls'][-4:])}"]


def md(R, title):
    o = [f"# D9S1 stream statistics{': ' + title if title else ''}", ""]
    c = R["creates"]; t = R["totals"]
    o += ["## 1. Size", f"- records {R['records']}, frames (Present/PresentEx/swap-chain Present at depth 0) {R['frames']}, bytes {R['bytes']}, calls after the last Present {R['trailing_calls']}",
          f"- decode error: {('record at offset %d: %s; %d bytes not decoded' % (R['decode_error'][1], R['decode_error'][0], R['decode_error'][2])) if R['decode_error'] else 'none'}",
          f"- threads {R['threads']}, calls per thread {R['calls_per_thread']}, max nesting depth {R['max_depth']}, records at depth > 0: {R['nested_records']}", "",
          "## 2. Shader creation (are shaders created inside steady-state frames?)",
          f"- CreateVertexShader {c['VS']}, CreatePixelShader {c['PS']}; distinct bytecodes ({c['hash']}): VS {c['distinct_VS']}, PS {c['distinct_PS']}, either {c['distinct_either']}",
          f"- first frame with a create {c['first_frame_with_create']}, last frame with a create {c['last_frame_with_create']}, frames with a create {c['frames_with_create']} of {R['frames']}",
          f"- first frame with a draw {c['first_draw_frame']}; creates in frames from the first draw frame on: {c['creates_from_first_draw_frame_on']}",
          f"- creates per frame over the final window (frames {c['window']}): histogram creates:frames " + (", ".join(f"{k}:{v}" for k, v in c["window_hist"].items()) or "(no frames)"), "",
          "## 3. Other Create* (device), calls by method", "- " + (", ".join(f"{k} {v}" for k, v in sorted(R["other_create_by_method"].items())) or "none"), "",
          "## 4. Locks, draws, fixed function",
          f"- Lock-type calls {t.get('locks', 0)} (recorded lock payload {t.get('lock_bytes_in', 0)} B), unlock-type calls {t.get('unlocks', 0)} (recorded unlock payload {t.get('unlock_bytes', 0)} B)",
          f"- draws (Draw*, Draw*UP, patches) {t.get('draws', 0)}; with no vertex shader {t.get('draws_no_vs', 0)}, with no pixel shader {t.get('draws_no_ps', 0)}, with neither {t.get('draws_no_vs_no_ps', 0)}, stage unknown (after a state-block Apply) {t.get('draws_unknown_stage', 0)}",
          "- fixed-function calls: " + ", ".join(f"{k} {v}" for k, v in R["ffp_calls"].items()),
          "- state-block methods: " + (", ".join(f"{k} {v}" for k, v in R["stateblock_methods"].items()) or "none"),
          "- query methods: " + (", ".join(f"{k} {v}" for k, v in R["query_methods"].items()) or "none"), "",
          "## 5. Per frame (full table: perframe.tsv; columns " + " ".join(COLS) + ")"]
    rows = R["frame_rows"]; pick = rows if len(rows) <= 14 else rows[:7] + [None] + rows[-7:]
    for r in pick: o.append("- ..." if r is None else "- " + " ".join(f"{k}={v}" for k, v in zip(COLS, r)))
    for j, nm in ((1, "calls"), (9, "draws"), (7, "lock_bytes_in")):
        v = sorted(r[j] for r in rows[:-1]) or [0]; o.append(f"- {nm} per complete frame: min {v[0]}, median {v[len(v) // 2]}, max {v[-1]}")
    o += ["", "## 6. Failed HRESULTs by method", *(f"- {k}: {v}" for k, v in R["failed_hresult"].items()), *([] if R["failed_hresult"] else ["- none"]), "",
          "## 7. env-class answers by method (records with a recorded env answer, env items)", *(f"- {k}: {v[0]} records, {v[1]} items" for k, v in R["env_by_method"].items()),
          *([] if R["env_by_method"] else ["- none"]), "",
          "## 8. Adapter modes", "- mode counts (format id:count): " + (", ".join(f"{FMT.get(k, k)}:{v}" for k, v in R["mode_counts"]) or "none"),
          "- enumerated (format, index, WxH@Hz, mode format): " + ("; ".join(f"{FMT.get(m[0], m[0])} #{m[1]} {m[2]}x{m[3]}@{m[4]} {FMT.get(m[5], m[5])}" for m in R["modes"]) or "none"), "",
          f"## 9. Methods called ({len(R['called'])} distinct)", "| method | calls |", "|---|---|", *(f"| {k} | {v} |" for k, v in R["called"].items()), "",
          f"## 10. Spec methods never called ({len(R['never_called'])} of the spec's interface methods; exports never called: {', '.join(R['never_called_exports']) or 'none'})"]
    by = collections.defaultdict(list)
    for k in R["never_called"]: i, n = k.split("::"); by[i].append(n)
    o += [f"- {i}: {', '.join(v)}" for i, v in sorted(by.items())]
    o += ["", *world_md(R)]
    return "\n".join(o) + "\n"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("stream"); ap.add_argument("--md"); ap.add_argument("--json"); ap.add_argument("--tsv-dir")
    ap.add_argument("--window", type=int, default=100); ap.add_argument("--expect-mode"); ap.add_argument("--spec", default=DEF_SPEC); ap.add_argument("--title", default="")
    ap.add_argument("--require-world", action="store_true"); ap.add_argument("--world-min-draws", type=int, default=WORLD_DEFAULT[0])
    ap.add_argument("--world-min-frames", type=int, default=WORLD_DEFAULT[1]); ap.add_argument("--world-min-textures", type=int, default=WORLD_DEFAULT[2])
    o = ap.parse_args(); raw = open(o.spec, "rb").read(); spec = json.loads(raw)
    try: a = analyse(open(o.stream, "rb").read(), spec)
    except (d9s1.ReadError, OSError, KeyError, IndexError) as ex: print(f"d9rec_stats: unreadable stream: {ex!r}", file=sys.stderr); return 2
    R = build(a, spec, o.window, (o.world_min_draws, o.world_min_frames, o.world_min_textures)); txt = md(R, o.title or os.path.basename(o.stream))
    if o.md: open(o.md, "w").write(txt)
    else: sys.stdout.write(txt)
    if o.json: json.dump({k: v for k, v in R.items() if k != "frame_rows"}, open(o.json, "w"), indent=1)
    if o.tsv_dir:
        os.makedirs(o.tsv_dir, exist_ok=True)
        open(o.tsv_dir + "/perframe.tsv", "w").write("\t".join(COLS) + "\n" + "".join("\t".join(map(str, r)) + "\n" for r in R["frame_rows"]))
        open(o.tsv_dir + "/methods.tsv", "w").write("method\tcalls\n" + "".join(f"{k}\t{v}\n" for k, v in R["called"].items()))
        open(o.tsv_dir + "/modes.tsv", "w").write("format\tindex\twidth\theight\trefresh\tmode_format\n" + "".join("\t".join(map(str, m)) + "\n" for m in R["modes"]))
    print(f"d9rec_stats: {'WORLD-REACHED' if R['world']['reached'] else 'NOT WORLD-REACHED'} ({R['world']['rule']}; best run {R['world']['best_run_frames']} frames, max draws {R['world']['draws_pct']['all'][100]})", file=sys.stderr)
    if o.require_world and not R["world"]["reached"]: return 4
    if o.expect_mode:
        w, h, r = map(int, re.fullmatch(r"(\d+)x(\d+)@(\d+)", o.expect_mode).groups())
        ms = [m for m in R["modes"] if m[0] == 22]; cnt = [v for k, v in R["mode_counts"] if k == 22]
        ok = bool(ms) and all(m[2:5] == (w, h, r) for m in ms) and len({m[1] for m in ms}) == 1 and all(v == 1 for v in cnt) and bool(cnt)
        print(f"d9rec_stats: single-mode check {o.expect_mode}: {'PASS' if ok else 'FAIL'} (X8R8G8B8 counts {cnt}, modes {[m[2:5] for m in ms]})", file=sys.stderr)
        if not ok: return 3
    return 0


if __name__ == "__main__": sys.exit(main())
