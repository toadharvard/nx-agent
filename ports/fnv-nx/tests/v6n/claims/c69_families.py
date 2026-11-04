#!/usr/bin/env python3
# V6n claim check C63 / C69: S6n's hand-written family shaders (render/native/shaders, expanded by S6n's build.py)
# against the engine's original D3D9 programs for the same pass, on equivalent synthetic inputs (solid textures,
# constant colours, a full-screen quad). The original runs on the oracle (v6n_nine executes the bytecode), the family
# on S6n's GL backend (v6n_family). Both write an A32B32G32R32F image; compared with cmp.py's float threshold.
#
#   FNVNX_SDP=<dir with shaderpackage002.sdp, or the file> python3 -I tests/v6n/claims/c69_families.py [-v] [CASE...]
# Without FNVNX_SDP the check is skipped (exit 0): the originals are the player's data and never in the repo.
# Each case names the pass (true numbering, build/v6n/pass_space.tsv), the original programs and the family variant;
# the inputs are chosen so that the engine's constant mapping is unambiguous (CTAB names, decomp TileShader).
# Exit 1 when a case differs (one line per case), 2 on a setup error.
import os, sys, struct, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'render/verify'))
import cmp as C
BIN = os.path.join(ROOT, 'build/v6n/bin'); FAM = os.path.join(BIN, 's6n_shaders')

def sdp_programs(path):
    d = open(path, 'rb').read(); out = {}; off = 12
    while off + 260 <= len(d):
        nm = d[off:off + 256].split(b'\0')[0].decode('latin1'); sz = struct.unpack_from('<I', d, off + 256)[0]
        out[nm] = d[off + 260:off + 260 + sz]; off += 260 + sz
    return out

def texels(t):   # a texture is one RGBA colour, or a list of four (2x2, row-major)
    return [t] if isinstance(t[0], (int, float)) else list(t)

def d3dcolor_bytes(t):   # A8R8G8B8 texels in memory order B G R A
    out = ''
    for rgba in texels(t):
        r, g, b, a = [int(round(x * 255)) for x in rgba]; out += '%02x%02x%02x%02x' % (b, g, r, a)
    return out

def famhex(t):
    return ' '.join(''.join('%02x' % int(round(x * 255)) for x in rgba) for rgba in texels(t))

# Full-screen quad as two triangles: model position (x, y, z), uv, vertex colour
QUAD = [((-1, 1, 0.5), (0, 0)), ((1, 1, 0.5), (1, 0)), ((-1, -1, 0.5), (0, 1)),
        ((1, 1, 0.5), (1, 0)), ((1, -1, 0.5), (1, 1)), ((-1, -1, 0.5), (0, 1))]
IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

def verts(c):
    """the case's six vertices: pos, uv, col (and nrm); default the full-screen QUAD with the case's vertex colour"""
    if 'verts' in c: return c['verts']
    return [{'pos': p, 'uv': uv, 'col': c['vcol'], 'nrm': (0, 0, 1)} for p, uv in QUAD]

def tile_case(name, n, fam_vs, fam_ps, tint, scroll, tex0, tex1, vcol):
    """TileShader (pass 757 TILE): TILE1<n>.vso / .pso; constants: VS c0-c3 ModelViewProj, VS c4 TexScroll, PS c0
    TintColor; samplers s0 Src0, s1 AlphaMap. Family block NxTileDraw {wvp, texScroll, tint}, texture slot 0."""
    return {'name': name, 'pass': 757, 'vs': 'TILE1%03d.vso' % n, 'ps': 'TILE1%03d.pso' % n,
            'vsc': {0: IDENT[0:4], 1: IDENT[4:8], 2: IDENT[8:12], 3: IDENT[12:16], 4: scroll}, 'psc': {0: tint},
            'tex': [tex0, tex1], 'vcol': vcol,
            'fam': (fam_vs, fam_ps), 'ubo': IDENT + list(scroll) + list(tint), 'famtex': [tex0]}

CHECKER = ((1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1), (1, 1, 1, 1))
CASES = [
    tile_case('tile', 0, 'tile.vert', 'tile.frag', (0.8, 0.6, 0.4, 0.7), (0, 0, 1, 1), (0.5, 0.75, 1.0, 0.6), (1, 1, 1, 1), (1, 1, 1, 1)),
    tile_case('tile_scroll_offset', 1, 'tile_scroll.vert', 'tile.frag', (1, 1, 1, 1), (0.28, 0.53, 1, 1), CHECKER, (1, 1, 1, 1), (1, 1, 1, 1)),
    tile_case('tile_scroll_scale', 1, 'tile_scroll.vert', 'tile.frag', (1, 1, 1, 1), (0.03, 0.03, 2, 0.5), CHECKER, (1, 1, 1, 1), (1, 1, 1, 1)),
    tile_case('tile_alphamap', 1, 'tile_scroll.vert', 'tile_alphamap.frag', (0.8, 0.6, 0.4, 0.7), (0, 0, 1, 1), (0.5, 0.75, 1.0, 0.6), (0.4, 0.2, 0.9, 0.3), (1, 1, 1, 1)),
    tile_case('tile_vc', 2, 'tile_vc.vert', 'tile_vc.frag', (0.8, 0.6, 0.4, 0.7), (0, 0, 1, 1), (0.5, 0.75, 1.0, 0.6), (1, 1, 1, 1), (0.25, 0.5, 0.75, 0.5)),
]

def nolight_case(name, vs, ps, fam_vs, fam_ps, mat, tex0, vcol, falloff=None, half=1.0):
    """BSShaderNoLighting (passes 66.. NOLIGHTING*): NOLIGHT0xx.vso (vertexConfigs: 000 TEX, 001 VC, 002 TEX+VC, 004
    TEX+FALLOFF) with NOLIGHTTEX / NOLIGHTTEXVC / NOLIGHTVCONLY.pso. VS c0-c3 ModelViewProj, c4-c5 TexCoordTranform
    (identity), c13 FogParam (1000, 1, 1: fog factor 0), c36-c38 WorldView (identity), c40 Falloff; PS c0
    MaterialColor, c1 Toggles (0), c2 FogColor. Positions (+-half, +-half, 1) in view space, ModelViewProj -> clip (+-1, +-1, 0.5, 1).
    Family NxNoLightDraw {wvp, eyeModel (0), falloff, color = MaterialColor, uvOffsetScale (0, 0, 1, 1)}."""
    k = 1.0 / half   # positions (+-half, +-half, 1) in view space fill the target
    mvp = [k, 0, 0, 0, 0, k, 0, 0, 0, 0, 0, 0.5, 0, 0, 0, 1]
    fo = falloff or (1, 0, 1, 1)
    vx = [{'pos': (p[0] * half, p[1] * half, 1), 'uv': uv, 'col': vcol, 'nrm': (0, 0, 1)} for p, uv in QUAD]
    wvp_cols = [mvp[0], mvp[4], mvp[8], mvp[12], mvp[1], mvp[5], mvp[9], mvp[13], mvp[2], mvp[6], mvp[10], mvp[14], mvp[3], mvp[7], mvp[11], mvp[15]]
    return {'name': name, 'pass': 66, 'vs': vs, 'ps': ps, 'verts': vx,
            'vsc': {0: mvp[0:4], 1: mvp[4:8], 2: mvp[8:12], 3: mvp[12:16], 4: (1, 0, 0, 0), 5: (0, 1, 0, 0), 13: (1000, 1, 1, 0),
                    36: (1, 0, 0, 0), 37: (0, 1, 0, 0), 38: (0, 0, 1, 0), 40: fo},
            'psc': {0: mat, 1: (0, 0, 0, 0), 2: (0, 1, 0, 1)}, 'tex': [tex0], 'vcol': vcol,
            'd3d_attrs': ['pos', 'uv', 'col', 'nrm'], 'fam_attrs': [('pos', 3), ('uv', 2), ('col', 4), ('nrm', 3)],
            'fam': (fam_vs, fam_ps), 'ubo': wvp_cols + [0, 0, 0, 0] + list(fo) + list(mat) + [0, 0, 1, 1], 'famtex': [tex0]}

def sky_case():
    """SkyShader (pass 569 SKY): SKY.vso + SKY.pso. The original colours a vertex by
    its vertex colour used as weights of three BlendColor constants (c4-c6: c4 * vc.r + c5 * vc.g + c6 * vc.b, alpha =
    vc.a * c4.a) and puts it on the far plane (z = 0.9999 w); the family colours by the direction's elevation. Same
    elevation everywhere (z = 0: the horizon), two vertex-colour weightings: the original varies, the family cannot."""
    horizon, zenith, third = (0.9, 0.5, 0.2, 1.0), (0.1, 0.3, 0.9, 1.0), (0.0, 0.0, 0.0, 1.0)
    vx = []
    for p, uv in QUAD:
        w = (1, 0, 0, 1) if p[0] < 0 else (0, 1, 0, 1)   # left vertices horizon-weighted, right ones zenith-weighted
        vx.append({'pos': (p[0], p[1], 0.0), 'uv': uv, 'col': w, 'nrm': (0, 0, 1)})
    mvp = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0.5, 0, 0, 0, 1]   # clip (x, y, 0.5, 1) for model (x, y, 0)
    cols = [mvp[0], mvp[4], mvp[8], mvp[12], mvp[1], mvp[5], mvp[9], mvp[13], mvp[2], mvp[6], mvp[10], mvp[14], mvp[3], mvp[7], mvp[11], mvp[15]]
    return {'name': 'sky_vertex_weights', 'pass': 569, 'vs': 'SKY.vso', 'ps': 'SKY.pso', 'verts': vx,
            'vsc': {0: mvp[0:4], 1: mvp[4:8], 2: mvp[8:12], 3: mvp[12:16], 4: horizon, 5: zenith, 6: third},
            'psc': {}, 'tex': [], 'vcol': None, 'd3d_attrs': ['pos', 'col'], 'fam_attrs': [('pos', 3), ('uv', 2)],
            'fam': ('sky.vert', 'sky.frag'), 'ubo': cols + list(zenith) + list(horizon) + [1, 0, 0, 0], 'famtex': []}

def land_case():
    """LANDAD (pass 147, build/v6n/pass_space.tsv: SLS1025.vso + SLS1040.pso): base texture x vertex colour x
    clamp(N.L x PSLightColor + AmbientColor), N from the normal map, L = LightData (c25) in the tangent frame. Flat normal
    map (0.5, 0.5, 1), L = (0, 0, 0.5): N.L = 0.5; ambient 0.2, light 0.8 -> x 0.6. Family land: base x vertex colour,
    no light terms (layer weights 0)."""
    base, vcol = (0.8, 0.6, 0.4, 1.0), (0.5, 0.75, 1.0, 1.0)
    vx = [{'pos': (p[0], p[1], 0.5), 'uv': uv, 'col': vcol, 'nrm': (0, 0, 1), 'tan': (1, 0, 0), 'bin': (0, 1, 0), 'w': (0, 0, 0)} for p, uv in QUAD]
    return {'name': 'land_light', 'pass': 147, 'vs': 'SLS1025.vso', 'ps': 'SLS1040.pso', 'verts': vx,
            'vsc': {0: IDENT[0:4], 1: IDENT[4:8], 2: IDENT[8:12], 3: IDENT[12:16], 25: (0, 0, 0.5, 1)},
            'psc': {1: (0.2, 0.2, 0.2, 1), 3: (0.8, 0.8, 0.8, 1)}, 'tex': [base, (0.5, 0.5, 1.0, 1.0)], 'vcol': vcol,
            'd3d_attrs': ['pos', 'tan', 'bin', 'nrm', 'uv', 'col'], 'fam_attrs': [('pos', 3), ('uv', 2), ('col', 4), ('w', 3)],
            'fam': ('land.vert', 'land.frag'), 'ubo': IDENT + [1, 1, 0, 0], 'famtex': [base, base, base, base]}

def depth_case():
    """ZONLY_At (pass 2: SLS1000.vso + SLS1000.pso, ALPHATESTENABLE): the original's alpha is DiffuseMap.a, so the alpha
    test cuts the texture's holes out of the depth pass; the family depth PS has no texture (alpha 0). Checker alpha
    texture, ALPHAFUNC GREATER, ALPHAREF 128; compared: which pixels pass (colour writes on for the comparison)."""
    tex = ((1, 1, 1, 1), (1, 1, 1, 0), (1, 1, 1, 0), (1, 1, 1, 1))
    return {'name': 'depth_alphatest', 'pass': 2, 'vs': 'SLS1000.vso', 'ps': 'SLS1000.pso',
            'vsc': {0: IDENT[0:4], 1: IDENT[4:8], 2: IDENT[8:12], 3: IDENT[12:16]}, 'psc': {1: (0.5, 0.5, 0.5, 1)},
            'tex': [tex], 'vcol': (1, 1, 1, 1), 'clear': (0.25, 0.25, 0.25, 0.25), 'compare': 'coverage',
            'rs': ['rs ALPHATESTENABLE 1', 'rs ALPHAFUNC CMP_GREATER', 'rs ALPHAREF 128'],
            'epi': [0, 0, 0, 0, 0, 0, 0, 128 / 255.0, 4, 0, 1, 0],   # nxModes: GREATER (NxCompare 4), no fog, alpha test on
            'd3d_attrs': ['pos', 'uv'], 'fam_attrs': [('pos', 3)], 'fam': ('depth.vert', 'depth.frag'), 'ubo': IDENT, 'famtex': [tex]}

def run_case(c, progs, work, verbose):
    # original on the oracle
    for k in ('vs', 'ps'):
        open(os.path.join(work, c[k]), 'wb').write(progs[c[k]])
    cl = c.get('clear', (0, 0, 0, 0)); clhex = famhex(cl)
    argb = clhex[6:8] + clhex[0:6]
    L = ['size 16 16', 'rt A32B32G32R32F', 'ds NONE', 'clear 1 0x%s 1.0 0' % argb, 'rs CULLMODE CULL_NONE', 'rs ZENABLE 0',
         'vs %s' % os.path.join(work, c['vs']), 'ps %s' % os.path.join(work, c['ps'])] + c.get('rs', [])
    for s, t in enumerate(c['tex']):
        sz = 2 if len(texels(t)) == 4 else 1
        L += ['tex %d A8R8G8B8 %d %d 1 solid %s' % (s, sz, sz, d3dcolor_bytes(t)), 'samp %d MINFILTER TEXF_POINT' % s, 'samp %d MAGFILTER TEXF_POINT' % s]
    for r, v in c['vsc'].items(): L.append('vsc %d %s' % (r, ' '.join('%g' % x for x in v)))
    for r, v in c['psc'].items(): L.append('psc %d %s' % (r, ' '.join('%g' % x for x in v)))
    D3D = {'pos': (0, 0), 'uv': (5, 0), 'col': (10, 0), 'nrm': (3, 0), 'tan': (6, 0), 'bin': (7, 0)}
    keys = c.get('d3d_attrs', ['pos', 'uv', 'col'])
    L.append('vdecl ' + ' '.join('%d:%d' % D3D[k] for k in keys))
    for vx in verts(c):
        L.append('vg ' + ' '.join(' '.join('%g' % x for x in (list(vx[k]) + [0, 0, 0, 1][len(vx[k]):])) for k in keys))
    L.append('drawg TRIANGLELIST 2')
    sc = os.path.join(work, c['name'] + '.scene'); open(sc, 'w').write('\n'.join(L) + '\n')
    od = os.path.join(work, 'nine'); os.makedirs(od, exist_ok=True)
    r = subprocess.run([os.path.join(BIN, 'v6n_nine'), '-q', '-o', od, sc], capture_output=True, text=True)
    ref = os.path.join(od, c['name'] + '.v6nd')
    if r.returncode or not os.path.exists(ref): return None, 'oracle: ' + (r.stderr.strip()[:200])
    # family on S6n's GL backend
    F = ['size 16 16', 'vs %s' % os.path.join(FAM, c['fam'][0] + '.glsl'), 'ps %s' % os.path.join(FAM, c['fam'][1] + '.glsl'),
         'ubo ' + ' '.join('%g' % x for x in c['ubo']), 'epi ' + ' '.join('%g' % x for x in c.get('epi', [0] * 12)),
         'clear ' + clhex]
    for s, t in enumerate(c['famtex']): F.append('tex %d %s' % (s, famhex(t)))
    fk = c.get('fam_attrs', [('pos', 3), ('uv', 2), ('col', 4)])
    F.append('layout ' + ' '.join(str(n) for _, n in fk))
    for vx in verts(c): F.append('v ' + ' '.join(' '.join('%g' % x for x in list(vx[k])[:n]) for k, n in fk))
    F.append('draw 2')
    fm = os.path.join(work, c['name'] + '.fam'); open(fm, 'w').write('\n'.join(F) + '\n')
    out = os.path.join(work, c['name'] + '.fam.v6nd')
    r = subprocess.run([os.path.join(BIN, 'v6n_family'), fm, out], capture_output=True, text=True)
    if r.returncode: return None, 'family: ' + r.stderr.strip()[:200]
    a, b = C.load(ref), C.load(out)
    if c.get('compare') == 'coverage':   # which pixels were written (alpha test), not their colour
        import numpy as np
        ca = (np.abs(a['c'] - np.array(cl, np.float32)) > 1e-3).any(-1); cb = (np.abs(b['c'] - np.array(cl, np.float32)) > 1e-3).any(-1)
        nd = int((ca != cb).sum())
        ok, msg = nd == 0, ['coverage: %d px differ (original writes %d, family %d)' % (nd, ca.sum(), cb.sum())]
    else:
        ok, msg, _ = C.compare(a, b)
    px = (a['c'][8, 8].tolist(), b['c'][8, 8].tolist())
    return ok, ('' if ok else msg[0][:160]) + ' | centre: original %s family %s' % tuple(
        '(' + ', '.join('%.3f' % x for x in v) + ')' for v in px)

CASES += [
    nolight_case('nolight_tex', 'NOLIGHT000.vso', 'NOLIGHTTEX.pso', 'nolight_tex.vert', 'nolight_tex.frag', (0.8, 0.6, 0.4, 0.7), (0.5, 0.75, 1.0, 0.6), (1, 1, 1, 1)),
    nolight_case('nolight_texvc', 'NOLIGHT002.vso', 'NOLIGHTTEXVC.pso', 'nolight_texvc.vert', 'nolight_tex.frag', (0.8, 0.6, 0.4, 0.7), (0.5, 0.75, 1.0, 0.6), (0.25, 0.5, 0.75, 0.5)),
    nolight_case('nolight_vc', 'NOLIGHT001.vso', 'NOLIGHTVCONLY.pso', 'nolight_vc.vert', 'nolight_col.frag', (0.8, 0.6, 0.4, 0.7), (1, 1, 1, 1), (0.25, 0.5, 0.75, 0.5)),
    nolight_case('nolight_texfalloff', 'NOLIGHT004.vso', 'NOLIGHTTEX.pso', 'nolight_texfall.vert', 'nolight_tex.frag', (0.8, 0.6, 0.4, 0.7), (0.5, 0.75, 1.0, 0.6), (1, 1, 1, 1), (0.95, 0.6, 1.0, 0.2), 0.5),
]

CASES += [sky_case(), land_case(), depth_case()]

def main():
    a = sys.argv[1:]; verbose = '-v' in a; names = [x for x in a if not x.startswith('-')]
    sdp = os.environ.get('FNVNX_SDP')
    if not sdp: print('c69_families: skipped (set FNVNX_SDP to the player\'s shaderpackage002.sdp or its directory)'); return 0
    if os.path.isdir(sdp): sdp = os.path.join(sdp, 'shaderpackage002.sdp')
    if not os.path.exists(sdp): print('c69_families: %s not found' % sdp); return 2
    progs = sdp_programs(sdp)
    work = tempfile.mkdtemp(prefix='v6n_c69_')
    bad = 0; n = 0
    for c in CASES:
        if names and c['name'] not in names: continue
        n += 1
        ok, msg = run_case(c, progs, work, verbose)
        print('%-22s pass %-4d %s+%s vs %s+%s: %s %s' % (c['name'], c['pass'], c['vs'], c['ps'], c['fam'][0], c['fam'][1],
              'ERROR' if ok is None else 'MATCH' if ok else 'DIFFERS', msg))
        bad += ok is not True
    print('c69_families: %d cases, %d differ' % (n, bad))
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
