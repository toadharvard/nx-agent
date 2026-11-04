#!/usr/bin/env python3
"""fnv-nx G1: per-draw GPU inputs of a real-game NXD9 capture (render/native/capture/d3d9cap.h), for gpu_cost.py.

    python3 -I nxd9_gpu.py <in.nxd9> <out.json> [--frame N] [--index <sdp2dksh -o dir>/index.tsv]

For every draw of frame N (default: the middle one): render target (surface id, format, size), depth surface,
viewport, vertex / pixel shader (fnvnx_shader_hash; with --index also the package program name), primitive type and
count, vertices, and the D3D9 state the GPU cost depends on (z enable / write / func, alpha blend, alpha test,
colour write mask, stencil). Clears are listed in order with the target they hit. Consecutive draws on the same
(colour, depth) target pair form a "pass" (a render-target switch starts a new one).
The output describes the owner's game frame; it is derived data and stays in the agent's work directory.
"""
import json, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nxd9_import import records  # noqa: E402

D3DFMT = {21: "A8R8G8B8", 22: "X8R8G8B8", 23: "R5G6B5", 31: "A2B10G10R10", 32: "A8B8G8R8", 35: "A2R10G10B10",
          34: "G16R16", 36: "A16B16G16R16", 111: "R16F", 112: "G16R16F", 113: "A16B16G16R16F", 114: "R32F",
          115: "G32R32F", 116: "A32B32G32R32F", 75: "D24S8", 77: "D24X8", 80: "D16", 71: "D16_LOCKABLE",
          0x34324E49: "INTZ", 0x5A574152: "RAWZ", 0x5A465544: "DF24", 0x36314644: "DF16", 50: "L8", 28: "A8",
          827611204: "DXT1", 861165636: "DXT3", 894720068: "DXT5"}
RS = {7: "zenable", 14: "zwrite", 23: "zfunc", 15: "alphatest", 27: "blend", 168: "cwrite", 52: "stencil",
      22: "cull", 24: "alpharef", 25: "alphafunc", 19: "srcblend", 20: "destblend", 206: "sepalpha"}
PRIM = {1: "points", 2: "lines", 3: "linestrip", 4: "tris", 5: "tristrip", 6: "trifan"}


def analyse(path, frame=None, index=None):
    d = open(path, "rb").read()
    names = {}
    if index:
        for line in open(index):
            f = line.rstrip("\n").split("\t")
            if f[0] != "hash" and len(f) > 2:
                names[f[0].lower().lstrip("0x")] = f[2]
    nframes = sum(1 for t, _ in records(d) if t == "PRES")
    frame = nframes // 2 if frame is None else frame
    surf, tex, shaders = {}, {}, {"VSH": {}, "PSH": {}}
    st = {"rt": 0, "ds": 0xFFFFFFFF, "vs": 0, "ps": 0, "view": None, "rs": {7: 1, 14: 1, 23: 4, 15: 0, 27: 0, 168: 15, 52: 0, 22: 3}}
    dev = None
    fr, draws, clears = 0, [], []
    for t, b in records(d):
        if t == "DEV":
            dev = struct.unpack_from("<4I", b)
        elif t == "SURF":
            i, kind, fmt, w, h, tid = struct.unpack_from("<6I", b)
            surf[i] = {"kind": kind, "format": D3DFMT.get(fmt, fmt), "w": w, "h": h, "tex": tid}
        elif t == "TEX":
            i, fmt, w, h, lv, fl = struct.unpack_from("<6I", b)
            tex[i] = {"format": D3DFMT.get(fmt, fmt), "w": w, "h": h, "rt": fl & 1}
        elif t in ("VSH", "PSH"):
            i, lo, hi = struct.unpack_from("<3I", b)
            h = "%016x" % (hi << 32 | lo)
            shaders[t][i] = names.get(h.lstrip("0"), h)
        elif t == "SRT":
            idx, sid = struct.unpack_from("<2I", b)
            if idx == 0:
                st["rt"] = sid
        elif t == "SDS":
            st["ds"] = struct.unpack_from("<I", b)[0]
        elif t == "SVS":
            st["vs"] = struct.unpack_from("<I", b)[0]
        elif t == "SPS":
            st["ps"] = struct.unpack_from("<I", b)[0]
        elif t == "VIEW":
            st["view"] = struct.unpack_from("<4I", b)
        elif t == "RS":
            k, v = struct.unpack_from("<2I", b)
            st["rs"][k] = v
        elif t == "PRES":
            fr += 1
        elif fr == frame and t == "CLR":
            fl, col, z, sten, n = struct.unpack_from("<IIfII", b)
            clears.append({"after_draw": len(draws), "flags": fl, "rt": target(st, surf, dev), "rects": n})
        elif fr == frame and t in ("DRWI", "DRAW"):
            if t == "DRWI":
                pt, bv, mi, nv, si, pc = struct.unpack_from("<IiIIII", b)
            else:
                pt, sv, pc = struct.unpack_from("<3I", b); nv = None
            draws.append({"rt": target(st, surf, dev), "ds": depth(st, surf, dev), "view": st["view"],
                          "vs": shaders["VSH"].get(st["vs"], st["vs"]), "ps": shaders["PSH"].get(st["ps"], st["ps"]),
                          "prim": PRIM.get(pt, pt), "prims": pc, "verts": nv,
                          "state": {RS[k]: v for k, v in st["rs"].items() if k in RS}})
    passes = []
    for i, dr in enumerate(draws):
        key = (dr["rt"]["id"], dr["ds"]["id"] if dr["ds"] else None)
        if not passes or passes[-1]["key"] != key:
            passes.append({"key": key, "rt": dr["rt"], "ds": dr["ds"], "first": i, "draws": 0, "prims": 0})
        passes[-1]["draws"] += 1
        passes[-1]["prims"] += dr["prims"]
    for p in passes:
        p["key"] = list(p["key"])
    return {"file": os.path.basename(path), "frames": nframes, "frame": frame, "device": dev, "draws": draws,
            "clears": clears, "passes": passes}


def target(st, surf, dev):
    s = st["rt"]
    if s == 0:
        return {"id": 0, "format": D3DFMT.get(dev[2], dev[2]) if dev else None, "w": dev[0] if dev else None,
                "h": dev[1] if dev else None, "backbuffer": True}
    x = dict(surf.get(s, {})); x["id"] = s
    return x


def depth(st, surf, dev):
    s = st["ds"]
    if s == 0:
        return None
    if s == 0xFFFFFFFF:
        return {"id": -1, "format": D3DFMT.get(dev[3], dev[3]) if dev else None, "auto": True}
    x = dict(surf.get(s, {})); x["id"] = s
    return x


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    a = sys.argv
    frame = int(a[a.index("--frame") + 1]) if "--frame" in a else None
    index = a[a.index("--index") + 1] if "--index" in a else None
    r = analyse(a[1], frame, index)
    json.dump(r, open(a[2], "w"), indent=1)
    for p in r["passes"]:
        rt = p["rt"]
        print("pass@%d draws %d prims %d rt %s %sx%s ds %s" % (p["first"], p["draws"], p["prims"], rt.get("format"),
              rt.get("w"), rt.get("h"), (p["ds"] or {}).get("format")))
        for dr in r["draws"][p["first"]:p["first"] + p["draws"]]:
            print("   %-22s %-22s %s %6d view %s %s" % (dr["vs"], dr["ps"], dr["prim"], dr["prims"], dr["view"], dr["state"]))


if __name__ == "__main__":
    main()
