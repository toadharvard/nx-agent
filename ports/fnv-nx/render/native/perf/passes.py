#!/usr/bin/env python3
"""fnv-nx P6n: per-pass counters of a native capture (milestone N2's counter).

    python3 -I passes.py <capture.nxcap> [--json out.json] [--frame N | --all]

Reads an .nxcap (render/native/capture/capture.h) and counts, per submitted frame and per run of draws with the
same PASS marker (NxOp_Marker NX_MARKER_PASS: the true engine pass id and the family, docs/RENDER_NATIVE.md §6.1)
inside each frame-graph pass (NxOp_BeginPass ... NxOp_EndPass):
  packets   draws + clears          draws      Draw + DrawIndexed      clears     Clear ops
  state_binds  BindState ops        tex_binds  BindTextures ops        tex_handles  handles in them
  ubo_binds / ubo_bytes  BindUbo ops and the bytes they bind (per-draw slices, epilogues, pass constants)
  vtx_binds, idx_binds, dyn  Viewport/Scissor/StencilRef/BlendConst/DepthBias ops; words  command words
Without a marker the engine pass is "unknown" (NX_PASS_UNKNOWN), family 0. Default: the last frame of the file.
Synthetic streams (perf/gen_stream.py) use engine pass ids 900+ (not engine numbering).
"""
import collections, json, struct, sys

OP_DEFSTATE, OP_DEFLAYOUT, OP_BEGIN, OP_END, OP_BARRIER, OP_STATE, OP_UBO, OP_VTX, OP_IDX, OP_TEX, OP_DRAW, OP_DRAWIDX, \
    OP_VP, OP_SC, OP_SREF, OP_BC, OP_DB, OP_CLEAR, OP_MARKER = range(1, 20)
MARKER_PASS, PASS_UNKNOWN = 0x53534150, 0xFFFFFFFF
FAMILIES = {0: "none", 1: "tile", 2: "nolight", 3: "lit", 4: "depth", 5: "land", 6: "sky", 7: "imagespace"}
FIELDS = ("packets", "draws", "clears", "state_binds", "tex_binds", "tex_handles", "ubo_binds", "ubo_bytes",
          "vtx_binds", "idx_binds", "dyn", "words")


def frames(data):
    assert data[:8] == b"NXCAP\0\0\1", "not an nxcap file"
    off = 8
    while off + 8 <= len(data):
        tag, n = struct.unpack_from("<II", data, off)
        if tag == 0x4D425553:   # SUBM
            frame, present, words = struct.unpack_from("<III", data, off + 8)
            yield frame, struct.unpack_from("<%dI" % words, data, off + 20)
        off += 8 + ((n + 3) & ~3)


def count(words):
    rows = collections.OrderedDict()
    gp, tag, i = -1, (PASS_UNKNOWN, 0), 0
    defines = 0

    def row():
        k = (gp, tag[0], tag[1])
        if k not in rows:
            rows[k] = dict.fromkeys(FIELDS, 0)
        return rows[k]
    while i < len(words):
        op, argc = words[i] & 0xFFFF, words[i] >> 16
        a = words[i + 1:i + 1 + argc]
        i += 1 + argc
        if op == 0:
            break
        if op in (OP_DEFSTATE, OP_DEFLAYOUT):
            defines += 1
            continue
        if op == OP_BEGIN:
            gp += 1; tag = (PASS_UNKNOWN, 0)
        elif op == OP_MARKER and argc >= 3 and a[0] == MARKER_PASS:
            tag = (a[1], a[2])
        r = row()
        r["words"] += 1 + argc
        if op in (OP_DRAW, OP_DRAWIDX):
            r["draws"] += 1; r["packets"] += 1
        elif op == OP_CLEAR:
            r["clears"] += 1; r["packets"] += 1
        elif op == OP_STATE:
            r["state_binds"] += 1
        elif op == OP_TEX:
            r["tex_binds"] += 1; r["tex_handles"] += a[2] if argc >= 3 else 0
        elif op == OP_UBO:
            r["ubo_binds"] += 1; r["ubo_bytes"] += a[4] if argc >= 5 else 0
        elif op == OP_VTX:
            r["vtx_binds"] += 1
        elif op == OP_IDX:
            r["idx_binds"] += 1
        elif op in (OP_VP, OP_SC, OP_SREF, OP_BC, OP_DB):
            r["dyn"] += 1
    out = []
    items = list(rows.items())
    for j, ((p, ep, fam), r) in enumerate(items):
        if not r["packets"] and ep == PASS_UNKNOWN and j + 1 < len(items) and items[j + 1][0][0] == p:
            for f in FIELDS:    # pass-start ops (BeginPass, pass constants) before the first marker: into its row
                items[j + 1][1][f] += r[f]
            continue
        out.append(dict(graph_pass=p, engine_pass=None if ep == PASS_UNKNOWN else ep, family=fam,
                        family_name=FAMILIES.get(fam, "perf-%d" % fam), **r))
    return out, defines


def main():
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    data = open(a[0], "rb").read()
    fr = list(frames(data))
    if "--all" in a:
        sel = fr
    elif "--frame" in a:
        n = int(a[a.index("--frame") + 1]); sel = [f for f in fr if f[0] == n]
    else:
        sel = fr[-1:]
    res = []
    for frame, words in sel:
        rows, defines = count(words)
        tot = {f: sum(r[f] for r in rows) for f in FIELDS}
        res.append({"frame": frame, "defines": defines, "passes": rows, "total": tot})
        print("frame %d: %d graph passes, %d rows, %d define ops" % (frame, len({r["graph_pass"] for r in rows}), len(rows), defines))
        print("  gp  engine  family      packets draws clears states texb  texh  ubob  ubo_bytes  vtx  idx  dyn  words")
        for r in rows + [dict(graph_pass="all", engine_pass="", family_name="", **tot)]:
            print("  %-3s %-7s %-10s %7d %5d %6d %6d %5d %5d %5d %10d %4d %4d %4d %6d" % (
                r["graph_pass"], "-" if r["engine_pass"] is None else r["engine_pass"], r["family_name"], r["packets"],
                r["draws"], r["clears"], r["state_binds"], r["tex_binds"], r["tex_handles"], r["ubo_binds"], r["ubo_bytes"],
                r["vtx_binds"], r["idx_binds"], r["dyn"], r["words"]))
    if "--json" in a:
        json.dump({"capture": a[0], "frames": res}, open(a[a.index("--json") + 1], "w"), indent=1)


if __name__ == "__main__":
    main()
