#!/usr/bin/env python3
# V6n: trap mutants for the state-level differential. Each mutant is one textual edit of a COPY of S6n's
# render/native/front/d3d9_state.c (the original is never touched), built into <out>/mut_<name> together with
# statediff; statediff must report a mismatch for every mutant ("killed"). A surviving mutant means the
# differential cannot see that trap.
#   python3 -I render/verify/statediff/mutants.py <bin dir from build.sh> [cases]
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SRC = os.path.join(ROOT, 'render/native/front/d3d9_state.c')
MUT = [  # name, old, new (old must occur in the source)
    ('bothsrcalpha_no_fixup', 'dst = rs[RS_SRCBLEND] == BLEND_BOTHSRCALPHA ? NxBf_InvSrcAlpha : NxBf_SrcAlpha;', ';'),
    ('alpha_table_is_color_table', 'if (b == 16) return NxBf_One;', 'if (b == 16) return NxBf_Src1Color;'),
    ('nonseparate_alpha_ignores_srcblend', 'srca = bf_alpha(rs[RS_SRCBLEND]);', 'srca = NxBf_One;'),
    ('separate_alpha_op_from_color', 'opa = bop(rs[RS_BLENDOPALPHA]);', 'opa = bop(rs[RS_BLENDOP]);'),
    ('zfunc_off_by_one', 'static uint32_t cmp(uint32_t d) { return d >= 1 && d <= 8 ? d - 1 : NxCmp_Never; }',
     'static uint32_t cmp(uint32_t d) { return d >= 1 && d <= 8 ? d % 8 : NxCmp_Never; }'),
    ('zwrite_with_equal', 'int write = rs[RS_ZWRITEENABLE] && f != NxCmp_Equal && f != NxCmp_Never;',
     'int write = rs[RS_ZWRITEENABLE] != 0;'),
    ('stencil_ops_wrap_sat_swapped', 'static uint32_t sop(uint32_t d) { return d >= 1 && d <= 8 ? d - 1 : NxSop_Zero; }',
     'static uint32_t sop(uint32_t d) { return d == 4 ? 6 : d == 7 ? 3 : d >= 1 && d <= 8 ? d - 1 : NxSop_Zero; }'),
    ('twosided_ignores_ccw', 'back = (uint64_t)cmp(rs[RS_CCW_STENCILFUNC])', 'back = (uint64_t)cmp(rs[RS_STENCILFUNC])'),
    ('cull_winding_swapped', 'rs[RS_CULLMODE] == 2 ? NxCull_CW : rs[RS_CULLMODE] == 3 ? NxCull_CCW',
     'rs[RS_CULLMODE] == 2 ? NxCull_CCW : rs[RS_CULLMODE] == 3 ? NxCull_CW'),
    ('flat_from_gouraud', 'if (rs[RS_SHADEMODE] == 1)', 'if (rs[RS_SHADEMODE] == 2)'),
    ('alpharef_not_normalised', 'dyn->alphaRef = (float)rs[RS_ALPHAREF] / 255.0f;', 'dyn->alphaRef = (float)rs[RS_ALPHAREF] / 256.0f;'),
    ('fog_mode_ps3_kept', '(ps_version & 0xFFFF) < 0x0300', '(ps_version & 0xFFFF) < 0x0400'),
    ('fog_table_exp_exp2_swapped', 'case 1: m = NxFog_Exp; break;', 'case 1: m = NxFog_Exp2; break;'),
    ('blendfactor_argb_as_rgba', 'rgba[0] = (float)((c >> 16) & 0xFF) / 0xFF;', 'rgba[0] = (float)((c >> 24) & 0xFF) / 0xFF;'),
    ('border_argb_order', 'o->border = ((c >> 16) & 0xFF) | (c & 0xFF00) | (c & 0xFF) << 16 | (c & 0xFF000000u);',
     'o->border = c;'),
    ('mipfilter_none_keeps_bias', 'if (s[SAMP_MIPFILTER] != TEXF_NONE) {', 'if (1) {'),
    ('mirroronce_as_mirror', 'case 5: return NxWrap_MirrorOnce;', 'case 5: return NxWrap_Mirror;'),
    ('cube_not_clamped', 'if (tex->cube) {', 'if (0) {'),
    ('point_min_is_linear', 'if (!(s[SAMP_MINFILTER] == TEXF_POINT && !tex->shadow)) f |= NXSMP_MIN_LINEAR;', ''),
    ('maxmiplevel_ignored', 'o->minLod = (uint8_t)lod;', 'o->minLod = 0;'),
    ('colorwrite_rt1_from_rt0', '(rs[RS_COLORWRITEENABLE1] & 15)', '(rs[RS_COLORWRITEENABLE] & 15)'),
    ('scissor_flag_ignored', 'if (rs[RS_SCISSORTESTENABLE]) w |= 1ull << NXRS_R_SCISSOR;', ''),
    ('pointsize_no_clamp', 'dyn->pointSize = NX_CLAMP(', 'dyn->pointSize = (0 ? 0 : '),
]
def main():
    bindir = os.path.abspath(sys.argv[1]); cases = sys.argv[2] if len(sys.argv) > 2 else '300000'
    src = open(SRC).read()
    killed = 0
    for name, old, new in MUT:
        if old not in src:
            print('%-36s SKIP (pattern not in the current S6n source)' % name); continue
        d = os.path.join(bindir, 'mut'); os.makedirs(d, exist_ok=True)
        c = os.path.join(d, 'mut_%s.c' % name)
        if name == 'pointsize_no_clamp':
            m = src.replace(old, new).replace('asfloat(rs[RS_POINTSIZE]), asfloat(rs[RS_POINTSIZE_MIN]), asfloat(rs[RS_POINTSIZE_MAX]));',
                                              'asfloat(rs[RS_POINTSIZE]));')
        else:
            m = src.replace(old, new, 1)
        open(c, 'w').write('#line 1 "d3d9_state.c"\n' + m)
        o = os.path.join(d, 'mut_%s.o' % name); exe = os.path.join(d, 'statediff_%s' % name)
        inc = ['-I' + os.path.join(ROOT, 'render/native/include'), '-I' + os.path.join(ROOT, 'render/native/front')]
        subprocess.check_call(['gcc', '-O1', '-std=c99', '-w'] + inc + ['-c', c, '-o', o])
        subprocess.check_call(['gcc', '-o', exe, os.path.join(bindir, 'statediff.o'), os.path.join(bindir, 'nine_pipe.o'),
                               os.path.join(bindir, 'fake_device.o'), o, '-L' + os.environ.get('NINE', '/home/user/work/v6n/nine') + '/out-host/lib',
                               '-lmesa_util', '-lm', '-lpthread'])
        r = subprocess.run([exe, '-n', cases, '-s', '7'], capture_output=True, text=True)
        first = [l.strip() for l in r.stdout.split('\n')[1:] if l.strip() and not l.strip().startswith('(')]
        ok = r.returncode != 0 and first
        killed += bool(ok)
        print('%-36s %s  %s' % (name, 'KILLED' if ok else 'SURVIVED', first[0][:110] if first else ''))
    print('state-level trap mutants: %d/%d killed' % (killed, len(MUT)))
    return 0 if killed == len(MUT) else 1
if __name__ == '__main__':
    sys.exit(main())
