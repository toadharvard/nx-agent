#!/usr/bin/env python3
"""nx_render (render/native/capture/nx_render.cpp): the S16-compare entry point. Run by `make -C tests/s6n nx_render`.

    python3 -I tests/s6n/test_nx_render.py --bin <nx_render> --run-gl <run_gl> --out <dir>

1. scene mode: every N0/N2 scene PNG equals tests/s6n/golden/<scene>.png exactly; --per-draw / --until-draw names.
2. nxcap mode: run_gl's capture of all scenes replays to rb<i>.png equal to the goldens.
3. d3d9 mode: a synthetic NXD9 capture (tests/s6n/nxd9.py; S6b-style GLSL written here, no game content):
   D3DCOLOR vertex colours, indexed and non-indexed draws, alpha test through the driver UBO, a varying the VS does
   not write, a rect clear, a skipped fixed-function draw (exit code 3), two presents, per-draw images.
"""
import argparse, os, shutil, subprocess, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
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
layout(std140, binding = 2) uniform ps_driver { vec4 ps_drv_fogcolor; vec4 ps_drv_fog; ivec4 ps_drv_alpha; vec4 ps_drv_alpharef; vec4 ps_drv_texbem[16]; };
layout(std140, binding = 0) uniform ps_cbuf_f { vec4 ps_uniforms_vec4[256]; };
layout(location = 0) in highp vec4 io_10_0;
layout(location = 2) in highp vec4 io_5_0;
layout(location = 0) out highp vec4 _gl_FragData_0;
void main()
{
	_gl_FragData_0 = io_10_0 * ps_uniforms_vec4[0] + io_5_0;
	if (ps_drv_alpha.x == 5 && !(_gl_FragData_0.a > ps_drv_alpharef.x)) discard;
}
"""


def png(path):
    from PIL import Image
    return Image.open(path).convert("RGBA")


def run(cmd, expect):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != expect:
        print("FAIL rc %d (expected %d): %s\n%s%s" % (r.returncode, expect, " ".join(cmd), r.stdout, r.stderr))
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--run-gl", required=True)
    ap.add_argument("--out", required=True)
    o = ap.parse_args()
    shutil.rmtree(o.out, ignore_errors=True)
    os.makedirs(o.out)
    checks = fails = 0

    def check(cond, what):
        nonlocal checks, fails
        checks += 1
        if not cond:
            fails += 1
            print("FAIL", what)

    golden = os.path.join(HERE, "golden")
    names = sorted(f[:-4] for f in os.listdir(golden) if f.endswith(".png"))
    # 1. scenes
    d = os.path.join(o.out, "scenes")
    os.makedirs(d)
    check(run([o.bin, "scene", "all", "-o", d], 0), "scene all")
    for n in names:
        p = os.path.join(d, n + ".png")
        check(os.path.exists(p) and png(p).tobytes() == png(os.path.join(golden, n + ".png")).tobytes(), "scene %s == golden" % n)
    d2 = os.path.join(o.out, "perdraw")
    os.makedirs(d2)
    check(run([o.bin, "scene", "blend", "-o", d2, "--per-draw"], 0), "scene blend --per-draw")
    draws = sorted(f for f in os.listdir(d2) if f.startswith("blend_d"))
    check(len(draws) >= 2 and draws[0] == "blend_d00000.png", "per-draw images blend_d00000.png.. (%d)" % len(draws))
    check(png(os.path.join(d2, draws[-1])).tobytes() == png(os.path.join(golden, "blend.png")).tobytes(),
          "last per-draw image == final image")
    d3 = os.path.join(o.out, "until")
    os.makedirs(d3)
    check(run([o.bin, "scene", "blend", "-o", d3, "--until-draw", "0"], 0), "--until-draw 0")
    check(png(os.path.join(d3, "blend.png")).tobytes() != png(os.path.join(golden, "blend.png")).tobytes(),
          "--until-draw 0 stops the scene after its first draw")
    # 2. nxcap
    cap = os.path.join(o.out, "n0.nxcap")
    os.makedirs(os.path.join(o.out, "rg"))
    check(run([o.run_gl, os.path.join(o.out, "rg"), "--capture", cap], 0), "run_gl --capture")
    d4 = os.path.join(o.out, "nxcap")
    os.makedirs(d4)
    check(run([o.bin, "nxcap", cap, "-o", d4], 0), "nxcap")
    rbs = sorted(f for f in os.listdir(d4) if f.startswith("rb"))
    check(len(rbs) == len(names), "nxcap: one rb<i>.png per readback (%d)" % len(rbs))
    goldens = {png(os.path.join(golden, n + ".png")).tobytes() for n in names}
    check(all(png(os.path.join(d4, f)).tobytes() in goldens for f in rbs), "nxcap readbacks equal the goldens")
    # 3. d3d9
    nx = os.path.join(o.out, "t.nxd9")
    w = nxd9.Nxd9Writer(nx)
    w.device(64, 48, 21, 75)
    w.shader("vs", 1, struct.pack("<2I", 0xFFFE0300, 0x0000FFFF), VS)
    w.shader("ps", 2, struct.pack("<2I", 0xFFFF0200, 0x0000FFFF), PS)
    w.decl(1, [(0, 0, 2, 0, 0, 0), (0, 12, 4, 0, 10, 0)])          # FLOAT3 POSITION0, D3DCOLOR COLOR0
    def quad(x0, y0, x1, y1, argb):
        return [struct.pack("<3fI", x, y, 0.5, argb) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y0), (x1, y1), (x0, y1))]
    verts = quad(-1, -1, 0, 1, 0xFFFF0000) + quad(0, 0, 1, 1, 0x8000FF00) + quad(0, -1, 1, 0, 0x2000FF00)
    w.vb(1, b"".join(verts))
    w.ib(1, struct.pack("<6H", 0, 1, 2, 3, 4, 5))
    w.u32("SDCL", 1); w.u32("STRM", 0, 1, 0, 16); w.u32("SIDX", 1)
    w.u32("SVS", 1); w.u32("SPS", 2)
    w.const_f("vs", 0, [(1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)])
    w.const_f("ps", 0, [(1, 1, 1, 1)])
    w.rs(7, 0)                                                      # ZENABLE off
    w.rs(22, 1)                                                     # CULLMODE NONE
    w.clear(1, 0xFF202020)
    w.draw(4, 0, 2)                                                 # red left half
    w.rs(15, 1); w.rs(25, 5); w.rs(24, 0x40)                        # ALPHATEST GREATER 0x40
    w.draw_indexed(4, 6, 0, 6, 0, 2)                                # green top right, alpha 0x80: passes
    w.draw_indexed(4, 12, 0, 6, 0, 2)                               # bottom right, alpha 0x20: discarded
    w.u32("SVS", 0)
    w.draw(4, 0, 2)                                                 # fixed function: skipped
    w.clear(1, 0xFFFFFFFF, rects=[(0, 40, 8, 48)])
    w.present()
    w.clear(1, 0xFF0000FF)
    w.present()
    w.close()
    d5 = os.path.join(o.out, "d3d9")
    os.makedirs(d5)
    check(run([o.bin, "d3d9", nx, "-o", d5, "--per-draw"], 3), "d3d9: rendered with a skipped draw (exit 3)")
    s = open(os.path.join(d5, "summary.txt")).read() if os.path.exists(os.path.join(d5, "summary.txt")) else ""
    check("frames 2" in s and "skipped 1" in s and "fixed-function" in s, "d3d9 summary: " + s.strip())
    if os.path.exists(os.path.join(d5, "f00000.png")):
        f0 = png(os.path.join(d5, "f00000.png"))
        check(f0.getpixel((8, 24)) == (255, 0, 0, 255), "D3DCOLOR red quad %s" % (f0.getpixel((8, 24)),))
        check(f0.getpixel((48, 10))[:3] == (0, 255, 0), "alpha test passes alpha 0x80 > 0x40 %s" % (f0.getpixel((48, 10)),))
        check(f0.getpixel((48, 36))[:3] == (0x20, 0x20, 0x20), "alpha test discards alpha 0x20 %s" % (f0.getpixel((48, 36)),))
        check(f0.getpixel((2, 44))[:3] == (255, 255, 255) and f0.getpixel((10, 44))[:3] == (255, 0, 0), "rect clear")
        check(f0.getpixel((31, 24))[0] == 255 and f0.getpixel((32, 24))[0] != 255, "quad edge at x = 32 (D3D9 centres)")
    else:
        check(False, "f00000.png written")
    check(os.path.exists(os.path.join(d5, "f00001.png")) and png(os.path.join(d5, "f00001.png")).getpixel((5, 5))[:3] == (0, 0, 255),
          "second frame f00001.png")
    pd = sorted(f for f in os.listdir(d5) if "_d" in f)
    check(pd == ["f00000_d00000.png", "f00000_d00001.png", "f00000_d00002.png"], "per-draw names %s" % pd)
    if "f00000_d00000.png" in pd:
        check(png(os.path.join(d5, "f00000_d00000.png")).getpixel((48, 10))[:3] == (0x20, 0x20, 0x20), "draw 0 image has no green yet")
    d6 = os.path.join(o.out, "d3d9_until")
    os.makedirs(d6)
    check(run([o.bin, "d3d9", nx, "-o", d6, "--until-draw", "0", "--frame", "0"], 3), "d3d9 --until-draw 0")
    if os.path.exists(os.path.join(d6, "f00000.png")):
        f0 = png(os.path.join(d6, "f00000.png"))
        check(f0.getpixel((8, 24))[:3] == (255, 0, 0) and f0.getpixel((48, 10))[:3] == (0x20, 0x20, 0x20), "--until-draw 0 frame")
    check(run([o.bin, "d3d9", os.path.join(o.out, "missing.nxd9"), "-o", d6], 2), "missing input: exit 2")
    open(os.path.join(o.out, "bad.nxd9"), "wb").write(b"NOPE")
    check(run([o.bin, "d3d9", os.path.join(o.out, "bad.nxd9"), "-o", d6], 1), "malformed input: exit 1")
    print("nx_render: %d checks, %d failed" % (checks, fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
