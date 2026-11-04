#!/usr/bin/env python3
# V6n: writes the hand-designed scenes (synthetic, committed as text):
#   tests/v6n/traps/<mutant>.scene   one scene per GL-target trap mutant: the unmutated target must MATCH Nine,
#                                    the mutant (V6N_MUTANT=<name>) must MISMATCH
#   tests/v6n/scenes/fmt_<FMT>.scene texture-format conformance (every format of S6n's table that both sides load)
#   python3 -I render/verify/gen_scenes.py [tests/v6n]
import os, sys

PRE = """size 16 16
rt {rt}
ds {ds}
clear 7 {clear} {z} {s}
rs CULLMODE CULL_NONE
tss 0 COLOROP TOP_SELECTARG1
tss 0 COLORARG1 TA_DIFFUSE
tss 0 ALPHAOP TOP_SELECTARG1
tss 0 ALPHAARG1 TA_DIFFUSE
"""
def pre(rt='A8R8G8B8', ds='D24S8', clear='0xff000000', z='1.0', s='0'):
    return PRE.format(rt=rt, ds=ds, clear=clear, z=z, s=s)
def quad(x0=0, y0=0, x1=16, y1=16, z=0.5, c='0xffffffff', spec='0', u0=0, v0=0, u1=1, v1=1, rhw=1):
    """two RHW triangles, both clockwise on screen"""
    v = lambda x, y, u, vv: 'v %g %g %g %g %s %s %g %g' % (x, y, z, rhw, c, spec, u, vv)
    return '\n'.join(['mode rhw', v(x0, y0, u0, v0), v(x1, y0, u1, v0), v(x0, y1, u0, v1),
                      v(x1, y0, u1, v0), v(x1, y1, u1, v1), v(x0, y1, u0, v1), 'draw TRIANGLELIST 2']) + '\n'
def xyzquad(z=0.5, c='0xffffffff', spec='0', z2=None):
    z2 = z if z2 is None else z2
    v = lambda x, y, zz, u, vv: 'v %g %g %g 1 %s %s %g %g' % (x, y, zz, c, spec, u, vv)
    return '\n'.join(['mode xyz', v(-1, 1, z, 0, 0), v(1, 1, z, 1, 0), v(-1, -1, z2, 0, 1),
                      v(1, 1, z, 1, 0), v(1, -1, z2, 1, 1), v(-1, -1, z2, 0, 1), 'draw TRIANGLELIST 2']) + '\n'
TEXSEL = """tss 0 COLOROP TOP_SELECTARG1
tss 0 COLORARG1 TA_TEXTURE
tss 0 ALPHAOP TOP_SELECTARG1
tss 0 ALPHAARG1 TA_TEXTURE
"""

TRAPS = {
 'cull_winding': pre() + 'rs CULLMODE CULL_CCW\nmode rhw\n'
    'v 1 1 0.5 1 0xffff0000 0 0 0\nv 7 1 0.5 1 0xffff0000 0 0 0\nv 1 7 0.5 1 0xffff0000 0 0 0\n'
    'v 9 9 0.5 1 0xff00ff00 0 0 0\nv 9 15 0.5 1 0xff00ff00 0 0 0\nv 15 9 0.5 1 0xff00ff00 0 0 0\ndraw TRIANGLELIST 2\n',
 'zwrite_ignored': pre() + 'rs ZENABLE 1\nrs ZWRITEENABLE 0\n' + quad(z=0.5),
 'zfunc_equal_lequal': pre(z='0.5') + 'rs ZENABLE 1\nrs ZFUNC CMP_EQUAL\n' + quad(z=0.25, c='0xff00ff00'),
 'bothsrcalpha': pre(clear='0xffff0000') + 'rs ALPHABLENDENABLE 1\nrs SRCBLEND BLEND_BOTHSRCALPHA\nrs DESTBLEND BLEND_ONE\n' + quad(c='0x800000ff'),
 'separate_alpha': pre(clear='0x40ff0000') + 'rs ALPHABLENDENABLE 1\nrs SRCBLEND BLEND_ONE\nrs DESTBLEND BLEND_ZERO\n'
    'rs SEPARATEALPHABLENDENABLE 1\nrs SRCBLENDALPHA BLEND_ZERO\nrs DESTBLENDALPHA BLEND_ONE\n' + quad(c='0xc000ff00'),
 'no_half_z': pre() + 'rs ZENABLE 1\n' + xyzquad(z=-0.5, c='0xffff0000', z2=0.5),
 'mipnone_lod': pre() + 'tex 0 A8R8G8B8 8 8 4 hash 11\nsamp 0 MIPFILTER TEXF_NONE\nsamp 0 MINFILTER TEXF_POINT\nsamp 0 MAGFILTER TEXF_POINT\n'
    + TEXSEL + quad(0, 0, 2, 2),
 'mirroronce': pre() + 'tex 0 A8R8G8B8 4 4 1 hash 3\nsamp 0 ADDRESSU TADDRESS_MIRRORONCE\nsamp 0 ADDRESSV TADDRESS_MIRRORONCE\n' + TEXSEL
    + quad(u0=-3, v0=-3, u1=3, v1=3),
 'border_argb': pre() + 'tex 0 A8R8G8B8 4 4 1 hash 5\nsamp 0 ADDRESSU TADDRESS_BORDER\nsamp 0 ADDRESSV TADDRESS_BORDER\nsamp 0 BORDERCOLOR 0x80ff4020\n'
    + TEXSEL + quad(u0=-1, v0=-1, u1=2, v1=2),
 'blendfactor_argb': pre() + 'rs ALPHABLENDENABLE 1\nrs SRCBLEND BLEND_BLENDFACTOR\nrs DESTBLEND BLEND_ZERO\nrs BLENDFACTOR 0x40ff8010\n' + quad(),
 'srgb_write_blend': pre(clear='0xff404040') + 'rs SRGBWRITEENABLE 1\nrs ALPHABLENDENABLE 1\nrs SRCBLEND BLEND_ONE\nrs DESTBLEND BLEND_ONE\n' + quad(c='0xff404040'),
 'fog_exp_exp2': pre() + 'rs FOGENABLE 1\nrs FOGTABLEMODE FOG_EXP\nrs FOGDENSITY f:1.0\nrs FOGCOLOR 0xff00ff00\n' + xyzquad(z=0.5, c='0xffff0000'),
 'fog_linear_scale': pre() + 'rs FOGENABLE 1\nrs FOGTABLEMODE FOG_LINEAR\nrs FOGSTART f:0.6\nrs FOGEND f:0.8\nrs FOGCOLOR 0xff00ff00\n' + xyzquad(z=0.2, c='0xff800000'),
 'alpharef_256': pre() + 'rs ALPHATESTENABLE 1\nrs ALPHAFUNC CMP_GREATER\nrs ALPHAREF 255\n' + quad(c='0xffffffff'),
 'ati2_swizzle': pre() + 'tex 0 ATI2 8 8 1 hash 7\nsamp 0 MINFILTER TEXF_POINT\nsamp 0 MAGFILTER TEXF_POINT\n' + TEXSEL + quad(),
 'a4r4g4b4_order': pre() + 'tex 0 A4R4G4B4 8 8 1 hash 8\n' + TEXSEL + quad(),
 'l8_swizzle': pre() + 'tex 0 L8 8 8 1 hash 9\n' + TEXSEL + quad(),
 'a8_swizzle': pre() + 'tex 0 A8 8 8 1 hash 10\n' + TEXSEL + quad(),
 'a8l8_swizzle': pre() + 'tex 0 A8L8 8 8 1 hash 12\n' + TEXSEL + quad(),
 'provoking_last': pre() + 'rs SHADEMODE SHADE_FLAT\nmode rhw\nv 1 1 0.5 1 0xffff0000 0 0 0\nv 15 1 0.5 1 0xff00ff00 0 0 0\nv 1 15 0.5 1 0xff0000ff 0 0 0\ndraw TRIANGLELIST 1\n',
 'twosided_front': pre(s='0x10') + 'rs STENCILENABLE 1\nrs TWOSIDEDSTENCILMODE 1\nrs STENCILPASS STENCILOP_REPLACE\nrs STENCILREF 0x5a\n'
    'rs CCW_STENCILPASS STENCILOP_INCR\n' + quad(),
 'colormask_order': pre() + 'rs COLORWRITEENABLE 0x1\n' + quad(),
 'no_half_pixel': pre() + 'mode rhw\nv 2 2 0.5 1 0xffffffff 0 0 0\nv 5 2 0.5 1 0xffffffff 0 0 0\nv 2 5 0.5 1 0xffffffff 0 0 0\ndraw TRIANGLELIST 1\n',
 'clear_ignores_scissor': pre() + 'rs SCISSORTESTENABLE 1\nscissor 4 4 9 11\nclear 1 0xffff0000 1.0 0\n',
 'clear_honors_mask': pre() + 'rs COLORWRITEENABLE 0x1\nclear 1 0xffffffff 1.0 0\n',
 'x8_dst_alpha': None,      # oracle deviation O1 (softpipe reads the stored X byte as DESTALPHA): analytic check
 '_x8_dst_alpha': pre(rt='X8R8G8B8', clear='0x00ffffff') + 'rs ALPHABLENDENABLE 1\nrs SRCBLEND BLEND_DESTALPHA\nrs DESTBLEND BLEND_ZERO\n' + quad(c='0xff808080'),
 'stencil_ref_unmasked': pre(s='0xff') + 'rs STENCILENABLE 1\nrs STENCILFUNC CMP_EQUAL\nrs STENCILREF 0x0f\nrs STENCILMASK 0x0f\n' + quad(),
 'no_texture_black': pre() + 'tss 0 COLOROP TOP_MODULATE\ntss 0 COLORARG1 TA_TEXTURE\ntss 0 COLORARG2 TA_DIFFUSE\n' + quad(),
 'rhw_viewport_z': pre() + 'rs ZENABLE 1\nvp 0 0 16 16 0.5 1.0\n' + quad(z=0.25),
 'fog_vertex_specular': pre() + 'rs FOGENABLE 1\nrs FOGCOLOR 0xff00ff00\n' + quad(c='0xffff0000', spec='0x00000000'),
 'specular_alpha': pre() + 'rs SPECULARENABLE 1\n' + quad(c='0x40800000', spec='0x80004000'),
 'upper_left_origin': pre() + 'mode rhw\nv 2 2 0.25 1 0xffff0000 0 0 0\nv 14 2 0.5 1 0xff00ff00 0 0 0\nv 2 14 0.75 1 0x800000ff 0 0 0\ndraw TRIANGLELIST 1\n',
 'd24_bias_scale': None,   # deviation D1: checked analytically (tests/v6n/run.sh claim C40), not against Nine
}

FORMATS = ['A8R8G8B8', 'X8R8G8B8', 'R5G6B5', 'X1R5G5B5', 'A1R5G5B5', 'A4R4G4B4', 'X4R4G4B4', 'A8', 'L8', 'A8L8',
           'A2B10G10R10', 'A2R10G10B10', 'A8B8G8R8', 'X8B8G8R8', 'G16R16', 'A16B16G16R16', 'L16', 'V8U8',
           'R16F', 'G16R16F', 'A16B16G16R16F', 'R32F', 'G32R32F', 'A32B32G32R32F',
           'DXT1', 'DXT2', 'DXT3', 'DXT4', 'DXT5', 'ATI1', 'ATI2']

# V8U8 seed 23: seed 22 has a -128 texel next to the border, which softpipe decodes as -128/127 (oracle quirk O2)
BORDER_DEFECT = set()   # was A8 L8 L16 A8L8 A2R10G10B10 ATI1 until S6n fixed defect border_swizzle (17077d6)
FLOATRT = {'V8U8', 'R16F', 'G16R16F', 'A16B16G16R16F', 'R32F', 'G32R32F', 'A32B32G32R32F'}

def main():
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', '..', 'tests', 'v6n')
    td, sd = os.path.join(base, 'traps'), os.path.join(base, 'scenes')
    os.makedirs(td, exist_ok=True); os.makedirs(sd, exist_ok=True)
    n = 0
    for name, body in TRAPS.items():
        if body is None or name.startswith('_'):
            continue
        open(os.path.join(td, name + '.scene'), 'w').write('# trap for V6N_MUTANT=%s (gen_scenes.py)\n' % name + body); n += 1
    for f in FORMATS:
        # unorm-sourced values go to an 8-bit target (2/255 threshold: decoders/filters of softpipe and llvmpipe
        # round 5/6-bit, DXT and bilinear results differently by 1/255); float and signed formats to a float target
        rt = 'A32B32G32R32F' if f in FLOATRT else 'A8R8G8B8'
        body = pre(rt=rt, ds='NONE') + 'tex 0 %s 8 8 1 hash 21\nsamp 0 MINFILTER TEXF_POINT\nsamp 0 MAGFILTER TEXF_POINT\n' % f + TEXSEL + quad()
        open(os.path.join(sd, 'fmt_%s.scene' % f), 'w').write('# texture format %s sampled with POINT into a float target (gen_scenes.py)\n' % f + body)
        body = pre(rt=rt, ds='NONE') + 'tex 0 %s 8 8 1 hash %d\nsamp 0 ADDRESSU TADDRESS_BORDER\nsamp 0 ADDRESSV TADDRESS_BORDER\nsamp 0 BORDERCOLOR 0x80ff4020\n' % (f, 23 if f == 'V8U8' else 22) + TEXSEL + quad(u0=-1, v0=-1, u1=2, v1=2)
        if f in BORDER_DEFECT:
            continue   # tests/v6n/defects/border_swizzle.scene
        open(os.path.join(sd, 'fmtborder_%s.scene' % f), 'w').write('# texture format %s: border colour + bilinear (gen_scenes.py)\n' % f + body)
    print('wrote %d trap scenes, %d format scenes' % (n, 2 * len(FORMATS)))

if __name__ == '__main__':
    main()
