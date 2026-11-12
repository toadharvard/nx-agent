#!/usr/bin/env python3
"""d9s_shaders.py: distinct shaders and bound (VS, PS) pairs of a D9S1 stream (render/d3d9/gen/STREAM.md), streaming.
  python3 -I d9s_shaders.py [--spec d3d9_spec.json] OUTDIR STREAM.d9s
OUTDIR gets <key>.bin (key = SHA-256 of the bytecode, 64 hex digits), pairs.tsv (vs ps draws first_frame) and stats.json.
Device state tracked per device object: Set{Vertex,Pixel}Shader (recorded instead of applied between Begin/EndStateBlock), CreateStateBlock
(captures the current shader binding of its categories), Capture, Apply, Reset (state back to NULL). A draw with a NULL stage is fixed function
for that stage: counted by kind, never a pair.  Exit codes: 0 ok, 2 malformed stream or a binding of an unknown object, 3 hash collision (same
64-bit key, different SHA-256)."""
import hashlib, json, mmap, os, struct, sys
from collections import Counter

HEADER = 44
D3DSBT_ALL, D3DSBT_PIXELSTATE, D3DSBT_VERTEXSTATE = 1, 2, 3
DRAWS = ("DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP", "DrawRectPatch", "DrawTriPatch")
PRESENTS = ("Present", "PresentEx")


class StreamError(Exception): pass
class CollisionError(Exception): pass


def stage_of(bc):
    """D3D version token: high word 0xFFFE = vertex, 0xFFFF = pixel; None = neither"""
    if len(bc) < 4: return None
    t = struct.unpack_from("<I", bc)[0] >> 16
    return "vs" if t == 0xFFFE else "ps" if t == 0xFFFF else None


def struct_size(spec, name, cache={}):
    key = (id(spec), name)
    if key not in cache:
        n = 0
        for f in spec["structs"][name]["fields"]:
            k = f["kind"]
            n += 4 if k in ("ptr", "ohandle") else f.get("count", 1) * struct_size(spec, f["struct"]) if k == "struct" else f["size"]
        cache[key] = n
    return cache[key]


class Walker:
    """reads the body of one call (INPUTS, RET, OUTS of STREAM.md) for the pointer-free kinds + buffers + structs"""
    def __init__(s, d, p, spec): s.d, s.p, s.spec = d, p, spec

    def w(s):
        if s.p + 4 > len(s.d): raise StreamError("truncated word")
        v, = struct.unpack_from("<I", s.d, s.p); s.p += 4; return v

    def raw(s, n):
        if s.p + n > len(s.d): raise StreamError("truncated payload")
        b = bytes(s.d[s.p:s.p + n]); s.p += (n + 3) & ~3; return b

    def pointee(s, p):
        k = p["kind"]
        if k in ("value", "enum", "flags", "float"): return s.raw(p.get("size", 4))
        if k in ("ohandle", "handle"): return s.raw(4)
        if k == "struct": return s.raw(struct_size(s.spec, p["struct"]))
        if k == "buffer": return s.raw(s.w())
        raise StreamError("param kind " + k)

    def call(s, m):
        ins, outs, ret = {}, {}, None
        for p in m["params"]:
            if not (p["byref"] or p["kind"] in ("buffer", "locked", "struct")): ins[p["name"]] = s.pointee(p); continue
            present = s.w()
            if present and p["dir"] != "out" and p["kind"] != "locked":
                if "in_when" in p and not s.w(): continue
                ins[p["name"]] = s.pointee(p)
        if m["ret_kind"] != "void":
            s.w(); ret = struct.unpack("<i", s.raw(4))[0] if m["ret_kind"] == "hresult" else s.raw(4)
        for p in m["params"]:
            if p["dir"] == "in": continue
            if p["kind"] == "locked": raise StreamError("locked param in a tracked call")
            if s.w() >> 8: outs[p["name"]] = s.pointee(p)
        return ins, ret, outs


def u32(b): return struct.unpack("<I", b)[0]


class Device:
    __slots__ = ("vs", "ps", "rec")
    def __init__(s): s.vs = s.ps = 0; s.rec = None


def roles(spec):
    names, out = sorted(spec["interfaces"]), {}
    for m in spec["methods"]:
        i, n = m["iface"], m["name"]; key = names.index(i) | m["slot"] << 16; r = None
        if i.startswith("IDirect3DDevice9"):
            r = {"CreateVertexShader": "cvs", "CreatePixelShader": "cps", "SetVertexShader": "svs", "SetPixelShader": "sps",
                 "CreateStateBlock": "csb", "BeginStateBlock": "bsb", "EndStateBlock": "esb", "Reset": "reset", "ResetEx": "reset"}.get(n)
            r = r or ("draw" if n in DRAWS else "present" if n in PRESENTS else None)
        elif i.startswith("IDirect3DSwapChain9") and n in PRESENTS: r = "present"
        elif i == "IDirect3DStateBlock9" and n in ("Capture", "Apply"): r = n.lower()
        if r: out[key] = (r, m)
    return out


def scan(data, spec, spec_sha=None):
    """returns (stats, shaders {hash: (stage, bytes, sha256)}, pairs {(vs, ps): [draws, first_frame]}, first_create {hash: frame})"""
    if bytes(data[:4]) != b"D9S1" or len(data) < HEADER or u32(data[4:8]) != 1: raise StreamError("bad magic, version or short header")
    if spec_sha is not None and bytes(data[8:40]).hex() != spec_sha:
        raise StreamError("stream was recorded with another d3d9_spec.json")
    R = roles(spec); devs, sbs, objs = {}, {}, {}        # objs: object id -> (stage, hash)
    shaders, pairs, st = {}, {}, Counter()
    creates, calls, nested = Counter(), Counter(), Counter()
    frame, pos, seq, n = 0, HEADER, 0, len(data)
    first_create = {}
    while pos < n:
        if pos + 24 > n: raise StreamError(f"truncated record at {pos}")
        ln, rseq, thread, w3, this, depth, nnew = struct.unpack_from("<IIIIIII", data, pos)
        if ln % 4 or ln < 28 or pos + ln > n or rseq != seq: raise StreamError(f"bad record at {start} (seq {rseq}, expected {seq})")
        seq += 1; r = R.get(w3); start, pos = pos, pos + ln
        if r is None: continue
        role, m = r; calls[m["name"]] += 1; nested[role] += depth > 0
        q = start + 28 + 8 * nnew
        nret, = struct.unpack_from("<I", data, q); q += 4 + 4 * nret
        try: ins, ret, outs = Walker(data, q, spec).call(m) if role != "present" else ({}, 0, {})
        except (StreamError, KeyError, struct.error) as e: raise StreamError(f"record at {start} (seq {seq - 1}) {m['name']}: {e}")
        ok = ret is None or (isinstance(ret, int) and ret >= 0)
        dv = devs.setdefault(this, Device())
        if role in ("cvs", "cps"):
            bc = ins["byte_code"]; stg = "vs" if role == "cvs" else "ps"
            if not ok: st["create_failed"] += 1; continue
            if stage_of(bc) != stg: raise StreamError(f"seq {seq - 1}: {m['name']} bytecode has stage {stage_of(bc)}")
            h = hashlib.sha256(bc).hexdigest()
            shaders.setdefault(h, (stg, bc, h)); creates[(frame, stg)] += 1; st["creates_" + stg] += 1; first_create.setdefault(h, frame)
            objs[u32(outs["shader"])] = (stg, h)
        elif role in ("svs", "sps"):
            stg = "vs" if role == "svs" else "ps"; oid = u32(ins["pShader"])
            if not ok: st["set_failed"] += 1; continue
            if oid and (oid not in objs or objs[oid][0] != stg): raise StreamError(f"seq {seq - 1}: {m['name']} of unknown object {oid}")
            if dv.rec is not None: dv.rec[stg] = oid
            else: setattr(dv, stg, oid)
        elif role == "csb":
            t = u32(ins["Type"]); sb = {"dev": this}
            if t in (D3DSBT_ALL, D3DSBT_VERTEXSTATE): sb["vs"] = dv.vs
            if t in (D3DSBT_ALL, D3DSBT_PIXELSTATE): sb["ps"] = dv.ps
            if ok: sbs[u32(outs["ppSB"])] = sb; st["stateblock_create_%d" % t] += 1
        elif role == "bsb":
            if ok: dv.rec = {}; st["stateblock_begin"] += 1
        elif role == "esb":
            if ok and dv.rec is not None:
                sbs[u32(outs["ppSB"])] = dict(dv.rec, dev=this); dv.rec = None; st["stateblock_end"] += 1
        elif role == "capture":
            sb = sbs.get(this)
            if sb is not None and ok:
                for k in ("vs", "ps"):
                    if k in sb: sb[k] = getattr(devs[sb["dev"]], k)
                st["stateblock_capture"] += 1
        elif role == "apply":
            sb = sbs.get(this)
            if sb is not None and ok:
                for k in ("vs", "ps"):
                    if k in sb: setattr(devs[sb["dev"]], k, sb[k])
                st["stateblock_apply"] += 1
        elif role == "reset":
            if ok: dv.vs = dv.ps = 0; dv.rec = None
        elif role == "present":
            if depth == 0: frame += 1
        elif role == "draw":
            if not ok: st["draw_failed"] += 1; continue
            if dv.vs and dv.ps:
                k = (objs[dv.vs][1], objs[dv.ps][1]); e = pairs.setdefault(k, [0, frame]); e[0] += 1; st["draws_vs_ps"] += 1
            else: st["draws_ffp_both" if not dv.vs and not dv.ps else "draws_ffp_ps" if dv.vs else "draws_ffp_vs"] += 1
    per_frame = {}
    for (f, stg), c in sorted(creates.items()): per_frame.setdefault(f, {})[stg] = c
    return {"records": seq, "frames": frame, "calls": dict(sorted(calls.items())), "nested_calls_by_role": {k: v for k, v in nested.items() if v},
            "counters": dict(sorted(st.items())), "create_calls_per_frame": per_frame,
            "distinct_vs": sum(1 for v in shaders.values() if v[0] == "vs"), "distinct_ps": sum(1 for v in shaders.values() if v[0] == "ps"),
            "distinct_pairs": len(pairs)}, shaders, pairs, first_create


def write_out(outdir, stats, shaders, pairs, first_create):
    os.makedirs(outdir, exist_ok=True)
    for h, (stg, bc, sha) in shaders.items():
        fn = os.path.join(outdir, f"{h}.bin")
        if os.path.exists(fn) and open(fn, "rb").read() != bc: raise CollisionError(f"{fn} exists with other content")
        open(fn, "wb").write(bc)
    with open(os.path.join(outdir, "pairs.tsv"), "w") as f:
        f.write("vs\tps\tdraws\tfirst_frame\n")
        for (a, b), (c, fr) in sorted(pairs.items()): f.write(f"{a}\t{b}\t{c}\t{fr}\n")
    stats = dict(stats, shaders={h: {"stage": v[0], "size": len(v[1]), "sha256": v[2], "first_frame": first_create[h]} for h, v in sorted(shaders.items())})
    json.dump(stats, open(os.path.join(outdir, "stats.json"), "w"), indent=1, sort_keys=True)


def main(argv):
    spec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "d3d9", "spec", "d3d9_spec.json")
    if argv[:1] == ["--spec"]: spec_path, argv = argv[1], argv[2:]
    if len(argv) != 2: print(__doc__); return 2
    raw = open(spec_path, "rb").read(); spec = json.loads(raw)
    with open(argv[1], "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        try: res = scan(data, spec, hashlib.sha256(raw).hexdigest())
        except StreamError as e: print("stream error:", e, file=sys.stderr); return 2
        except CollisionError as e: print("COLLISION:", e, file=sys.stderr); return 3
    try: write_out(argv[0], *res)
    except CollisionError as e: print("COLLISION:", e, file=sys.stderr); return 3
    s = res[0]; print(json.dumps({k: s[k] for k in ("records", "frames", "distinct_vs", "distinct_ps", "distinct_pairs", "counters")}))
    return 0


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
