#!/usr/bin/env python3
"""fnv-nx P6n: convert an NXD9 D3D9-level capture (docs/RENDER_NATIVE.md §8.3, render/native/capture/d3d9cap.h;
what S16 writes from the real game) into perf's internal .nxd3d stream (perf/nxd3d.h), so real captures feed the
same consumers as the synthetic mixes:

    python3 -I nxd9_import.py <in.nxd9> <out.nxd3d> [--frames N]

then e.g. `report.py`'s steps by hand: emu_count.py <native_drv.nso> <out.nxd3d> ..., nine_drv <out.nxd3d> 0.
Every PRES ends a frame. Mapping (approximations are marked *):
  TEX (not RT) -> ResTexture; SURF colour -> ResTarget (* always with a depth surface); SRT -> Target;
  CLR -> Target with the clear flags (* the consumers clear only on a target change);
  VSH/PSH -> ResShader with the sampler count (dcl_2d/cube/volume tokens) and the highest constant register + 1
  read from the bytecode (* nine_drv then builds a stand-in shader with those counts, not the real bytecode);
  RS/SS/STEX -> RS/Samp/Tex; SVS/SPS/SDCL -> Shaders; STRM(stream 0)+SIDX -> Geom (one id per (vb, ib) pair);
  VSCF/PSCF -> Consts before the next draw with the vec4 counts set since the previous draw (* the consumers set
  them from register 0); DRWI/DRAW -> Draw (* DRAW is treated as indexed; textures used = bound samplers 0..n-1
  contiguous from 0; layer = ALPHABLENDENABLE). NXD9 has no PASS tags: draws are untagged.
"""
import struct, sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from gen_stream import W  # noqa: E402


def tag(v):
    return struct.pack("<I", v).decode("latin1").rstrip()


def records(d):
    assert d[:8] == b"NXD9\0\0\0\1", "not an NXD9 file"
    o = 8
    while o + 8 <= len(d):
        t, n = struct.unpack_from("<II", d, o)
        yield tag(t), d[o + 8:o + 8 + n]
        o += 8 + n + ((-n) % 4)


def shader_counts(bc):
    """(samplers declared, highest float constant register read + 1) of D3D9 SM2/SM3 bytecode"""
    toks = struct.unpack("<%dI" % (len(bc) // 4), bc[:len(bc) // 4 * 4])
    samplers, maxc, i = 0, -1, 1
    while i < len(toks):
        t = toks[i]
        if t == 0x0000FFFF:
            break
        op = t & 0xFFFF
        if op == 0xFFFE:                    # comment
            i += 1 + ((t >> 16) & 0x7FFF); continue
        n = (t >> 24) & 0xF
        args = toks[i + 1:i + 1 + n]
        if op == 0x1F and len(args) == 2:   # dcl
            rt = ((args[1] >> 28) & 7) | ((args[1] >> 8) & 0x18)
            if rt == 10:
                samplers += 1
        elif op not in (0x51, 0x30, 0x2F):  # def/defi/defb define, do not read
            for a in args[1:]:
                rt = ((a >> 28) & 7) | ((a >> 8) & 0x18)
                if rt == 2:
                    maxc = max(maxc, a & 0x7FF)
        i += 1 + n
    return samplers, maxc + 1


def convert(data, max_frames=None):
    w = W()
    rs, cur = {}, {"vs": 0, "ps": 0, "decl": 0, "vb": 0, "ib": 0, "stride": 0, "rt": 0}
    vbs, ibs, geo, bound = {}, {}, {}, {}
    nvs = nps = 0
    frame, started, draws = 0, False, 0
    body = W()
    for t, p in records(data):
        u = lambda i: struct.unpack_from("<I", p, 4 * i)[0]
        if not started and t not in ("DEV", "TEX", "TEXU", "SURF", "VB", "IB", "VBU", "IBU", "DECL", "VSH", "PSH"):
            body.op(10, frame); started = True
        if t == "TEX":
            if not (u(5) & 1):
                w.op(1, u(0), u(1), u(2), u(3), u(4))
        elif t == "SURF":
            if u(1) == 1:
                w.op(4, u(0), u(3), u(4), u(2), 1)
        elif t == "VB":
            vbs[u(0)] = u(1)
        elif t == "IB":
            ibs[u(0)] = (u(1), u(2))
        elif t in ("VSH", "PSH"):
            n = u(3)
            s, c = shader_counts(p[16:16 + n])
            w.op(2, u(0), 0 if t == "VSH" else 1, max(1, c) if t == "VSH" else (s << 8 | c))
        elif t == "RS":
            rs[u(0)] = u(1); body.op(14, u(0), u(1))
        elif t == "SS":
            body.op(15, u(0), u(1), u(2))
        elif t == "STEX":
            bound[u(0)] = u(1); body.op(16, u(0), u(1))
        elif t in ("SVS", "SPS", "SDCL"):
            cur[{"SVS": "vs", "SPS": "ps", "SDCL": "decl"}[t]] = u(0)
            body.op(17, cur["vs"], cur["ps"], cur["decl"])
        elif t in ("STRM", "SIDX"):
            if t == "STRM" and u(0) == 0:
                cur["vb"], cur["stride"] = u(1), u(3)
            elif t == "SIDX":
                cur["ib"] = u(0)
            k = (cur["vb"], cur["ib"])
            if k not in geo:
                geo[k] = len(geo) + 1
                vsz = vbs.get(cur["vb"], 0); isz, fmt = ibs.get(cur["ib"], (0, 101))
                w.op(3, geo[k], vsz // max(1, cur["stride"]), isz // (4 if fmt == 102 else 2), cur["stride"])
            body.op(18, geo[k])
        elif t == "VSCF":
            nvs += u(1)
        elif t == "PSCF":
            nps += u(1)
        elif t == "SRT" and u(0) == 0:
            cur["rt"] = u(1); body.op(12, u(1), 0)
        elif t == "CLR":
            body.op(12, cur["rt"], u(0))
        elif t in ("DRWI", "DRAW"):
            ntex = 0
            while bound.get(ntex):
                ntex += 1
            body.op(19, min(nvs, 255), min(nps, 255)); nvs = nps = 0
            if t == "DRWI":
                prim, base, mn, nv, start, cnt = struct.unpack_from("<IiIIII", p)
            else:
                prim, start, cnt = struct.unpack_from("<III", p); base, nv = 0, cnt * 3
            body.op(20, prim, base & 0xFFFFFFFF, nv, start, cnt, ntex, 1 if rs.get(27) else 0, draws & 0xFFFF)
            draws += 1
        elif t == "PRES":
            body.op(11); frame += 1; started = False
            if max_frames and frame >= max_frames:
                break
    if started:
        body.op(11)
    body.op(0)
    w.w.extend(body.w)
    return w.bytes(), frame, draws


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        raise SystemExit(__doc__)
    mf = int(a[a.index("--frames") + 1]) if "--frames" in a else None
    out, frames, draws = convert(open(a[0], "rb").read(), mf)
    open(a[1], "wb").write(out)
    print("%s: %d frames, %d draws -> %s" % (a[0], frames, draws, a[1]))


if __name__ == "__main__":
    main()
