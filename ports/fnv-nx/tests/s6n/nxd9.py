#!/usr/bin/env python3
"""Writer for NXD9, the D3D9-level capture nx_render executes (render/native/capture/d3d9cap.h,
docs/RENDER_NATIVE.md §8.3). A reference for S16-compare's converter, and the generator of the synthetic test
captures in test_nx_render.py.

    w = Nxd9Writer(path); w.device(64, 48, 21, 75); ...; w.present(); w.close()
"""
import struct


def fnv1a64(data):
    h = 0xcbf29ce484222325
    for b in data:
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def tag(s):
    return struct.unpack("<I", s.ljust(4).encode())[0]


class Nxd9Writer:
    def __init__(self, path):
        self.f = open(path, "wb")
        self.f.write(b"NXD9\0\0\0\1")

    def rec(self, t, payload):
        self.f.write(struct.pack("<II", tag(t), len(payload)))
        self.f.write(payload)
        self.f.write(b"\0" * ((-len(payload)) % 4))

    def u32(self, t, *v):
        self.rec(t, struct.pack("<%dI" % len(v), *v))

    def device(self, w, h, back_fmt=21, depth_fmt=75):
        self.u32("DEV", w, h, back_fmt, depth_fmt)

    def texture(self, tid, fmt, w, h, levels, flags=0):
        """levels: list of bytes objects (D3D9 level layout, tightly packed), or [] for a render target"""
        p = struct.pack("<6I", tid, fmt, w, h, max(1, len(levels)) if levels else 1, flags)
        for l in levels:
            p += struct.pack("<I", len(l)) + l + b"\0" * ((-len(l)) % 4)
        self.rec("TEX", p)

    def surface(self, sid, kind, fmt, w, h, tex=0):
        self.u32("SURF", sid, kind, fmt, w, h, tex)

    def vb(self, bid, data):
        self.u32("VB", bid, len(data))
        self.rec("VBU", struct.pack("<3I", bid, 0, len(data)) + data)

    def ib(self, bid, data, fmt=101):
        self.u32("IB", bid, len(data), fmt)
        self.rec("IBU", struct.pack("<3I", bid, 0, len(data)) + data)

    def decl(self, did, elems):
        """elems: (stream, offset, type, method, usage, usage_index)"""
        self.rec("DECL", struct.pack("<2I", did, len(elems)) + b"".join(struct.pack("<HHBBBB", *e) for e in elems))

    def shader(self, kind, sid, bytecode, glsl):
        h = fnv1a64(bytecode)
        g = glsl.encode()
        p = struct.pack("<4I", sid, h & 0xFFFFFFFF, h >> 32, len(bytecode)) + bytecode + b"\0" * ((-len(bytecode)) % 4)
        p += struct.pack("<I", len(g)) + g
        self.rec("VSH" if kind == "vs" else "PSH", p)

    def rs(self, state, value):
        self.u32("RS", state, value)

    def ss(self, sampler, typ, value):
        self.u32("SS", sampler, typ, value)

    def const_f(self, kind, start, vec4s):
        self.rec("VSCF" if kind == "vs" else "PSCF",
                 struct.pack("<2I", start, len(vec4s)) + b"".join(struct.pack("<4f", *v) for v in vec4s))

    def clear(self, flags, color, z=1.0, stencil=0, rects=()):
        self.rec("CLR", struct.pack("<2IfII", flags, color, z, stencil, len(rects)) +
                 b"".join(struct.pack("<4i", *r) for r in rects))

    def viewport(self, x, y, w, h, mn=0.0, mx=1.0):
        self.rec("VIEW", struct.pack("<4I2f", x, y, w, h, mn, mx))

    def draw(self, prim, start, count):
        self.u32("DRAW", prim, start, count)

    def draw_indexed(self, prim, base, min_index, num_vertices, start_index, count):
        self.rec("DRWI", struct.pack("<IiIIII", prim, base, min_index, num_vertices, start_index, count))

    def present(self):
        self.rec("PRES", b"")

    def close(self):
        self.f.close()
