#!/usr/bin/env python3
"""test_s6n_cube_nxd9.py - V1 (D14) against S6n ac928fc: "nx_render samples cube textures, TEXC face updates"
(capture/d3d9_exec.cpp: NXD9 level-major faces reordered to the backend's face-major layout, NXFMT_CONVERT
formats converted with 6 faces, the cube flag in the sampler translation, TEXC = LockRect on one face of one
level). The commit's own check uses one level of 1x1 faces, where level-major and face-major are the same bytes.
Here a synthetic NXD9 (tests/s6n/nxd9.py; S6b-style GLSL written here, no game content) samples with textureLod:
  C1  2 levels (2x2, 1x1) A8R8G8B8: each face at level 0 and level 1 (the reorder of a multi-level cube)
  C2  texel orientation inside a face (2x2 faces): the D3D9 face/sc/tc table for directions into each quadrant
  C3  TEXC on level 1 face 3 (-Y) and on level 0 face 4 (+Z): that face/level changes, the neighbours do not
  C4  L8 cube (NXFMT_CONVERT: expanded with 6 faces), 2 levels: grey per face and level
  C5  TEXC with a byte count that is not the level's face size: exit 1 (malformed input), no crash
Expected colours come from the D3D9 cube addressing (major axis, sc/tc, point sampling). One line per case.
  python3 -I tests/v1/test_s6n_cube_nxd9.py --bin <nx_render>     (make -C tests/s6n OUT=<dir> nx_render)
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 's6n'))
import nxd9  # noqa: E402

VS = """#version 450 core
layout(std140, binding = 2) uniform vs_driver { vec4 vs_drv_posfix; vec4 vs_drv_reserved[3]; };
layout(std140, binding = 0) uniform vs_cbuf_f { vec4 vs_uniforms_vec4[256]; };
layout(location = 0) in vec4 vs_v0;
layout(location = 4) in vec4 vs_v1;
layout(location = 0) out highp vec4 io_10_0;
void main()
{
	gl_Position = vec4(dot(vs_uniforms_vec4[0], vs_v0), dot(vs_uniforms_vec4[1], vs_v0), dot(vs_uniforms_vec4[2], vs_v0), dot(vs_uniforms_vec4[3], vs_v0));
	io_10_0 = vs_v1;
	gl_Position.xy += vs_drv_posfix.xy * gl_Position.w;
	gl_Position.y *= vs_drv_posfix.z;
}
"""
PS = """#version 450 core
layout(std140, binding = 0) uniform ps_cbuf_f { vec4 ps_uniforms_vec4[256]; };
layout(binding = 0) uniform samplerCube ps_s0;
layout(location = 0) out highp vec4 _gl_FragData_0;
void main()
{
	_gl_FragData_0 = textureLod(ps_s0, ps_uniforms_vec4[0].xyz, ps_uniforms_vec4[0].w);
}
"""


def face_uv(d):
    """D3D9 cube addressing: (face, u, v) for direction d."""
    x, y, z = d
    ax, ay, az = abs(x), abs(y), abs(z)
    if ax >= ay and ax >= az:
        f, ma, sc, tc = (0, ax, -z, -y) if x > 0 else (1, ax, z, -y)
    elif ay >= az:
        f, ma, sc, tc = (2, ay, x, z) if y > 0 else (3, ay, x, -z)
    else:
        f, ma, sc, tc = (4, az, x, -y) if z > 0 else (5, az, -x, -y)
    return f, (sc / ma + 1) / 2, (tc / ma + 1) / 2


def png(path):
    from PIL import Image
    return Image.open(path).convert('RGBA')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', required=True)
    o = ap.parse_args()
    work = tempfile.mkdtemp(prefix='v1-cube-')   # raises when it cannot
    try:
        return run(o.bin, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run(binary, work):
    # A8R8G8B8 (BGRA bytes): level 0 texel (col, row) of face f = (30 + 40 f, 60 + 120 col, 60 + 120 row);
    # level 1 of face f = (30 + 40 f, 10, 250)
    c0 = lambda f, col, row: (30 + 40 * f, 60 + 120 * col, 60 + 120 * row)
    c1 = lambda f: (30 + 40 * f, 10, 250)
    bgra = lambda c: struct.pack('4B', c[2], c[1], c[0], 255)
    lvl0 = b''.join(bgra(c0(f, col, row)) for f in range(6) for row in range(2) for col in range(2))
    lvl1 = b''.join(bgra(c1(f)) for f in range(6))
    # L8: level 0 face f = 20 + 30 f, level 1 = 205 + 8 f
    l8_0 = b''.join(bytes([20 + 30 * f]) * 4 for f in range(6))
    l8_1 = b''.join(bytes([205 + 8 * f]) for f in range(6))
    path = os.path.join(work, 'cube2.nxd9')
    w = nxd9.Nxd9Writer(path)
    w.device(16, 16, 21, 0)
    w.shader('vs', 1, struct.pack('<2I', 0xFFFE0300, 0x0000FFFF), VS)
    w.shader('ps', 3, struct.pack('<2I', 0xFFFF0300, 0x0000FFFF), PS)
    w.decl(1, [(0, 0, 2, 0, 0, 0), (0, 12, 4, 0, 10, 0)])
    quad = [struct.pack('<3fI', x, y, 0.5, 0xFFFFFFFF) for x, y in ((-1, -1), (1, -1), (-1, 1), (1, -1), (1, 1), (-1, 1))]
    w.vb(1, b''.join(quad))
    w.texture(5, 21, 2, 2, [lvl0, lvl1], flags=2)
    w.texture(6, 50, 2, 2, [l8_0, l8_1], flags=2)
    w.u32('SDCL', 1); w.u32('STRM', 0, 1, 0, 16); w.u32('SVS', 1); w.u32('SPS', 3)
    w.const_f('vs', 0, [(1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)])
    w.rs(7, 0); w.rs(22, 1)
    for t, v in ((5, 1), (6, 1), (7, 1)):   # MAG, MIN, MIP point
        w.ss(0, t, v)
    frames = []   # (case, what, expected rgb)

    def frame(case, what, d, lod, want, tex=5):
        w.u32('STEX', 0, tex)
        w.clear(1, 0xFF000000)
        w.const_f('ps', 0, [(d[0], d[1], d[2], float(lod))])
        w.draw(4, 0, 2)
        w.present()
        frames.append((case, what, want))
    centre = [(1, 0.3, 0.2), (-1, 0.3, 0.2), (0.3, 1, 0.2), (0.3, -1, 0.2), (0.3, 0.2, 1), (0.3, 0.2, -1)]
    for f, d in enumerate(centre):
        assert face_uv(d)[0] == f
        frame('C1', 'face %d level 1' % f, d, 1, c1(f))
    quads = []
    for f in range(6):
        for s1 in (-0.5, 0.5):
            for s2 in (-0.5, 0.5):
                d = [0.0, 0.0, 0.0]
                ax = f // 2
                d[ax] = 1.0 if f % 2 == 0 else -1.0
                o1, o2 = [k for k in range(3) if k != ax]
                d[o1], d[o2] = s1, s2
                ff, u, v = face_uv(d)
                assert ff == f
                quads.append((f, tuple(d), int(u * 2), int(v * 2)))
    for f, d, col, row in quads:
        frame('C1' if (col, row) == (0, 0) else 'C2', 'face %d level 0 texel (%d,%d) dir %s' % (f, col, row, d), d, 0, c0(f, col, row))
    # C3: TEXC level 1 face 3 -> (1, 2, 3); level 0 face 4 -> four new texels
    w.rec('TEXC', struct.pack('<4I', 5, 3, 1, 4) + bgra((1, 2, 3)))
    newf4 = [(200, 201, 202), (203, 204, 205), (206, 207, 208), (209, 210, 211)]
    w.rec('TEXC', struct.pack('<4I', 5, 4, 0, 16) + b''.join(bgra(c) for c in newf4))
    frame('C3', 'after TEXC: -Y level 1 updated', centre[3], 1, (1, 2, 3))
    frame('C3', 'after TEXC: +Y level 1 unchanged', centre[2], 1, c1(2))
    frame('C3', 'after TEXC: +Z level 1 unchanged', centre[4], 1, c1(4))
    for f, d, col, row in quads:
        if f in (3, 4, 5):
            want = newf4[row * 2 + col] if f == 4 else c0(f, col, row)
            frame('C3', 'after TEXC: face %d level 0 texel (%d,%d)' % (f, col, row), d, 0, want)
    for f, d in enumerate(centre):
        g0, g1 = 20 + 30 * f, 205 + 8 * f
        frame('C4', 'L8 face %d level 1' % f, d, 1, (g1, g1, g1), tex=6)
        frame('C4', 'L8 face %d level 0' % f, d, 0, (g0, g0, g0), tex=6)
    w.close()
    out = os.path.join(work, 'out')
    os.makedirs(out)
    r = subprocess.run([binary, 'd3d9', path, '-o', out], capture_output=True, text=True, timeout=120)
    fails = 0
    res = {}
    if r.returncode != 0:
        print('C0: DEFECT - nx_render exit %d: %s' % (r.returncode, (r.stdout + r.stderr)[-400:]))
        fails += 1
    for i, (case, what, want) in enumerate(frames):
        p = os.path.join(out, 'f%05d.png' % i)
        got = png(p).getpixel((8, 8))[:3] if os.path.exists(p) else None
        ok = got == tuple(want)
        res.setdefault(case, []).append((ok, what, got, want))
    for case in ('C1', 'C2', 'C3', 'C4'):
        rows = res.get(case, [])
        bad = [x for x in rows if not x[0]]
        fails += bool(bad) or not rows
        print('%s: %s - %d frames, %d differ from D3D9 cube addressing' % (case, 'DEFECT' if bad or not rows else 'ok', len(rows), len(bad)))
        for _, what, got, want in bad[:4]:
            print('    %s: got %s, want %s' % (what, got, want))
    # C5: TEXC with the wrong byte count
    bad = os.path.join(work, 'bad.nxd9')
    w = nxd9.Nxd9Writer(bad)
    w.device(16, 16, 21, 0)
    w.texture(5, 21, 2, 2, [lvl0, lvl1], flags=2)
    w.rec('TEXC', struct.pack('<4I', 5, 0, 0, 8) + b'\0' * 8)
    w.present()
    w.close()
    out5 = os.path.join(work, 'out5')
    os.makedirs(out5)
    r = subprocess.run([binary, 'd3d9', bad, '-o', out5], capture_output=True, text=True, timeout=60)
    ok = r.returncode == 1
    fails += not ok
    print('C5: %s - TEXC of 8 bytes on a 16-byte face: exit %d %s' % ('ok' if ok else 'DEFECT', r.returncode, (r.stdout + r.stderr).strip()[-120:]))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
