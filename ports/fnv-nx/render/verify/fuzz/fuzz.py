#!/usr/bin/env python3
# V6n render-state differential fuzzer: random scenes biased to the D3D9 -> modern-API traps, run on the oracle
# (v6n_nine) and a target (v6n_gl or v6n_s6ngl), compared with cmp.py's thresholds; every mismatch is minimised
# (delta debugging over scene lines, then value simplification) and written as a text reproducer.
#
#   python3 -I render/verify/fuzz/fuzz.py [-n CASES] [-s SEED] [--target gl|s6ngl] [--mutant NAME]
#                                         [--repro DIR] [--batch N] [--no-min] [--sensitivity]
#   --sensitivity  run every GL-target trap mutant over the same cases and report how many cases kill each one
#                  (the fuzzer's own trap-detection power; no reproducers written)
# Reproducers: <repro>/<target>_<hash>.scene (default tests/v6n/repro), header = seed, case, first finding.
#
# Excluded on purpose (oracle deviations, docs/RENDER_VERIFY.md §5): DEPTHBIAS / SLOPESCALEDEPTHBIAS (D1, checked
# analytically), DESTALPHA on X8R8G8B8 (O1), V8U8 (O2), anisotropic filtering (implementation-defined), points,
# lines and wireframe (D3D9 line rules differ from GL by design and the engine draws no lines in 3D passes),
# trilinear / LOD bias (LOD precision is implementation-defined), DOTPRODUCT3 as ALPHAOP (undefined), stencil on
# formats without stencil (O6), texture-alpha blends without a TA_TEXTURE argument (O7), unbound textures (O4),
# texcoords on exact texel boundaries and ties on clipped edges (implementation-defined).
import os, sys, random, hashlib, subprocess, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import cmp as C
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
BIN = os.path.join(ROOT, 'build/v6n/bin')

CMP = ['CMP_NEVER', 'CMP_LESS', 'CMP_EQUAL', 'CMP_LESSEQUAL', 'CMP_GREATER', 'CMP_NOTEQUAL', 'CMP_GREATEREQUAL', 'CMP_ALWAYS']
BLEND = ['BLEND_ZERO', 'BLEND_ONE', 'BLEND_SRCCOLOR', 'BLEND_INVSRCCOLOR', 'BLEND_SRCALPHA', 'BLEND_INVSRCALPHA',
         'BLEND_DESTALPHA', 'BLEND_INVDESTALPHA', 'BLEND_DESTCOLOR', 'BLEND_INVDESTCOLOR', 'BLEND_SRCALPHASAT',
         'BLEND_BOTHSRCALPHA', 'BLEND_BOTHINVSRCALPHA', 'BLEND_BLENDFACTOR', 'BLEND_INVBLENDFACTOR']
BOP = ['BLENDOP_ADD', 'BLENDOP_SUBTRACT', 'BLENDOP_REVSUBTRACT', 'BLENDOP_MIN', 'BLENDOP_MAX']
SOP = ['STENCILOP_KEEP', 'STENCILOP_ZERO', 'STENCILOP_REPLACE', 'STENCILOP_INCRSAT', 'STENCILOP_DECRSAT',
       'STENCILOP_INVERT', 'STENCILOP_INCR', 'STENCILOP_DECR']
ADDR = ['TADDRESS_WRAP', 'TADDRESS_MIRROR', 'TADDRESS_CLAMP', 'TADDRESS_BORDER', 'TADDRESS_MIRRORONCE']
TOP = ['TOP_SELECTARG1', 'TOP_SELECTARG2', 'TOP_MODULATE', 'TOP_MODULATE2X', 'TOP_MODULATE4X', 'TOP_ADD',
       'TOP_ADDSIGNED', 'TOP_ADDSIGNED2X', 'TOP_SUBTRACT', 'TOP_ADDSMOOTH', 'TOP_BLENDDIFFUSEALPHA',
       'TOP_BLENDTEXTUREALPHA', 'TOP_BLENDFACTORALPHA', 'TOP_BLENDCURRENTALPHA', 'TOP_DOTPRODUCT3', 'TOP_LERP',
       'TOP_MULTIPLYADD']
TA = ['TA_DIFFUSE', 'TA_CURRENT', 'TA_TEXTURE', 'TA_TFACTOR', 'TA_SPECULAR', 'TA_TEXTURE_COMPLEMENT',
      'TA_DIFFUSE_ALPHAREPLICATE', 'TA_TEXTURE_ALPHAREPLICATE']
TEXFMT = ['A8R8G8B8', 'X8R8G8B8', 'A8B8G8R8', 'R5G6B5', 'A1R5G5B5', 'X1R5G5B5', 'A4R4G4B4', 'X4R4G4B4', 'A8', 'L8',
          'A8L8', 'L16', 'A2R10G10B10', 'A2B10G10R10', 'G16R16', 'A16B16G16R16', 'DXT1', 'DXT3', 'DXT5', 'ATI1', 'ATI2']

def col(r):
    return '0x%08x' % r.choice([r.getrandbits(32), 0xffffffff, 0x80808080, 0xff000000, 0x00ffffff, 0x7f7f7f7f,
                                r.getrandbits(8) * 0x01010101, (r.getrandbits(8) << 24) | 0xffffff])

def state_lines(r, rt):
    """one random render/sampler/stage-state delta, biased to the trap list"""
    L = []
    def rs(n, v): L.append('rs %s %s' % (n, v))
    k = r.random()
    if r.random() < 0.6:   # blend
        rs('ALPHABLENDENABLE', r.choice(['TRUE', 'FALSE', '2']))
        rs('SRCBLEND', r.choice(BLEND)); rs('DESTBLEND', r.choice(BLEND))
        if r.random() < 0.4: rs('BLENDOP', r.choice(BOP))
        if r.random() < 0.4:
            rs('SEPARATEALPHABLENDENABLE', 'TRUE'); rs('SRCBLENDALPHA', r.choice(BLEND)); rs('DESTBLENDALPHA', r.choice(BLEND))
            rs('BLENDOPALPHA', r.choice(BOP))
        if r.random() < 0.3: rs('BLENDFACTOR', col(r))
    if r.random() < 0.3: rs('COLORWRITEENABLE', str(r.randrange(16)))
    if r.random() < 0.5:   # depth
        rs('ZENABLE', r.choice(['TRUE', 'FALSE', '2'])); rs('ZFUNC', r.choice(CMP)); rs('ZWRITEENABLE', r.choice(['TRUE', 'FALSE']))
    if r.random() < 0.35:  # stencil
        rs('STENCILENABLE', 'TRUE'); rs('STENCILFUNC', r.choice(CMP))
        for n in ('STENCILFAIL', 'STENCILZFAIL', 'STENCILPASS'): rs(n, r.choice(SOP))
        rs('STENCILREF', '0x%02x' % r.randrange(256)); rs('STENCILMASK', r.choice(['0xff', '0x0f', '0xf0', '0x%02x' % r.randrange(256)]))
        rs('STENCILWRITEMASK', r.choice(['0xff', '0x0f', '0x%02x' % r.randrange(256)]))
        if r.random() < 0.5:
            rs('TWOSIDEDSTENCILMODE', 'TRUE'); rs('CCW_STENCILFUNC', r.choice(CMP))
            for n in ('CCW_STENCILFAIL', 'CCW_STENCILZFAIL', 'CCW_STENCILPASS'): rs(n, r.choice(SOP))
    if r.random() < 0.4: rs('CULLMODE', r.choice(['CULL_NONE', 'CULL_CW', 'CULL_CCW']))
    if r.random() < 0.2: rs('SHADEMODE', r.choice(['SHADE_FLAT', 'SHADE_GOURAUD']))
    if r.random() < 0.3:
        rs('ALPHATESTENABLE', 'TRUE'); rs('ALPHAFUNC', r.choice(CMP)); rs('ALPHAREF', str(r.choice([0, 1, 127, 128, 254, 255, r.randrange(256)])))
    if r.random() < 0.25:
        rs('FOGENABLE', 'TRUE'); rs('FOGCOLOR', col(r)); rs('FOGTABLEMODE', r.choice(['FOG_NONE', 'FOG_EXP', 'FOG_EXP2', 'FOG_LINEAR']))
        rs('FOGVERTEXMODE', r.choice(['FOG_NONE', 'FOG_EXP', 'FOG_EXP2', 'FOG_LINEAR']))
        s, e = sorted([r.choice([0.0, 0.1, 0.25, 0.5, 0.75, 1.0]), r.choice([0.2, 0.5, 0.8, 1.0, 2.0])])
        if e == s: e = s + 0.5
        rs('FOGSTART', 'f:%g' % s); rs('FOGEND', 'f:%g' % e); rs('FOGDENSITY', 'f:%g' % r.choice([0.5, 1.0, 2.0, 3.0]))
    if r.random() < 0.15 and rt in ('A8R8G8B8', 'X8R8G8B8'): rs('SRGBWRITEENABLE', 'TRUE')
    if r.random() < 0.2: rs('SPECULARENABLE', 'TRUE')
    if r.random() < 0.3: rs('TEXTUREFACTOR', col(r))
    if r.random() < 0.15:
        rs('SCISSORTESTENABLE', 'TRUE')
        x0, y0 = r.randrange(0, 12), r.randrange(0, 12); L.append('scissor %d %d %d %d' % (x0, y0, x0 + r.randrange(1, 12), y0 + r.randrange(1, 12)))
    # texture stage 0/1
    if r.random() < 0.7:
        L.append('tss 0 COLOROP %s' % r.choice(TOP)); L.append('tss 0 COLORARG1 %s' % r.choice(TA)); L.append('tss 0 COLORARG2 %s' % r.choice(TA))
        L.append('tss 0 ALPHAOP %s' % r.choice([t for t in TOP if t != 'TOP_DOTPRODUCT3']));   # undefined for alpha L.append('tss 0 ALPHAARG1 %s' % r.choice(TA)); L.append('tss 0 ALPHAARG2 %s' % r.choice(TA))
        if r.random() < 0.2: L.append('tss 0 COLORARG0 %s' % r.choice(TA))
    if r.random() < 0.25:
        # stage 1 reads no texture (one texcoord set in the FVF); Nine's FF still declares the stage's sampler and
        # softpipe crashes on an unbound view (oracle bug O4), so gen_scene binds a stage-1 texture whenever stage 1 runs
        L.append('tss 1 COLOROP %s' % r.choice([t for t in TOP if t not in ('TOP_BLENDTEXTUREALPHA',)])); L.append('tss 1 COLORARG1 TA_CURRENT'); L.append('tss 1 COLORARG2 %s' % r.choice(['TA_TFACTOR', 'TA_DIFFUSE', 'TA_SPECULAR']))
        L.append('tss 1 ALPHAOP %s' % r.choice(['TOP_SELECTARG1', 'TOP_MODULATE', 'TOP_ADD'])); L.append('tss 1 ALPHAARG1 TA_CURRENT'); L.append('tss 1 ALPHAARG2 TA_TFACTOR')
    if r.random() < 0.6:   # sampler 0
        for n in ('ADDRESSU', 'ADDRESSV'):
            if r.random() < 0.6: L.append('samp 0 %s %s' % (n, r.choice(ADDR)))
        if r.random() < 0.4: L.append('samp 0 BORDERCOLOR %s' % col(r))
        f = r.choice(['TEXF_POINT', 'TEXF_LINEAR'])   # MIN = MAG: the min/mag switch at LOD ~ 0 is implementation-defined
        L.append('samp 0 MAGFILTER %s' % f); L.append('samp 0 MINFILTER %s' % f)
        L.append('samp 0 MIPFILTER TEXF_NONE')   # mip selection at LOD boundaries is implementation-defined (traps cover mips)
        if r.random() < 0.3: L.append('samp 0 MAXMIPLEVEL %d' % r.randrange(4))
        if r.random() < 0.15: L.append('samp 0 SRGBTEXTURE TRUE')   # gen_scene drops it for formats without sRGB read (A2)
    return L

def geometry(r, w, h):
    """one draw: axis-aligned quad on integer coordinates, or a random triangle on a 1/16 px grid; RHW or XYZ"""
    L = []
    xyz = r.random() < 0.3
    def c(): return col(r)
    z = lambda: r.choice([0.0, 0.25, 0.5, 0.75, 1.0, r.randrange(1, 16) / 16.0])
    # texcoords off the texel grids (point sampling exactly on a texel boundary is implementation-defined)
    uv = lambda: r.choice([0, 1, -1, 2, 0.5, r.randrange(-32, 48) / 16.0]) + 0.01171875
    if xyz:
        L.append('mode xyz')
        # inside the clip volume: ties on edges the clipper cut are implementation-defined
        pts = [(r.randrange(-16, 17) / 16.0, r.randrange(-16, 17) / 16.0) for _ in range(3)]
        for (x, y) in pts:
            L.append('v %g %g %g 1 %s %s %g %g' % (x, y, z(), c(), c(), uv(), uv()))
        L.append('draw TRIANGLELIST 1')
        return L
    L.append('mode rhw')
    if r.random() < 0.5:
        x0, y0 = r.randrange(-2, w), r.randrange(-2, h); x1, y1 = x0 + r.randrange(1, w), y0 + r.randrange(1, h)
        zz = z(); rhw = r.choice([1, 1, 0.5, 2, 0.25]); cc = [c() for _ in range(4)]; sp = [c() for _ in range(4)]
        u0, v0, u1, v1 = uv(), uv(), uv(), uv()
        P = [(x0, y0, cc[0], sp[0], u0, v0), (x1, y0, cc[1], sp[1], u1, v0), (x0, y1, cc[2], sp[2], u0, v1), (x1, y1, cc[3], sp[3], u1, v1)]
        if r.random() < 0.5: P = [P[0], P[2], P[1], P[3]]   # the other winding
        for (x, y, cl, s, u, v) in P:
            L.append('v %g %g %g %g %s %s %g %g' % (x, y, zz, rhw, cl, s, u, v))
        L.append('draw TRIANGLESTRIP 2')
    else:
        for _ in range(3):
            L.append('v %g %g %g %g %s %s %g %g' % (r.randrange(-16, 16 * w + 16) / 16.0, r.randrange(-16, 16 * h + 16) / 16.0,
                                                   z(), r.choice([1, 0.5, 2]), c(), c(), uv(), uv()))
        L.append('draw TRIANGLELIST 1')
    return L

def gen_scene(r, target):
    rts = ['A8R8G8B8'] * 6 + ['X8R8G8B8'] + ['A16B16G16R16F', 'A32B32G32R32F']   # s6ngl reads floats back since ABI v3
    rt = r.choice(rts); ds = r.choice(['D24S8'] * 5 + ['D24X8', 'D16', 'NONE'])
    w = h = 16
    L = ['size %d %d' % (w, h), 'rt ' + rt, 'ds ' + ds,
         # clear depth off the vertex z grid: EQUAL/NOTEQUAL against a cleared value equal to the fragment z depends on
         # float -> D24 rounding of clear vs fragment, which differs between implementations
         'clear 7 %s %g 0x%02x' % (col(r), r.choice([1.0, 0.0, 0.6, 0.85]), r.randrange(256))]
    if True:   # always bound: Nine's FF + an unbound texture read only by alpha args crashes softpipe (O4)
        # float targets compare at 1e-3: give them 16-bit texels only (8-bit / 5-bit / DXT decode differs by 1/255)
        f = r.choice(TEXFMT if 'F' not in rt else ['A16B16G16R16', 'G16R16', 'L16']); s = r.choice([4, 8, 16]); lv = r.choice([1, 1, 3])
        L.append('tex 0 %s %d %d %d hash %d' % (f, s, s, lv, r.randrange(1000)))
    for d in range(r.choice([1, 1, 2, 3])):
        L += state_lines(r, rt)
        if r.random() < 0.15:
            x, y = r.randrange(0, 8), r.randrange(0, 8)
            L.append('vp %d %d %d %d %g %g' % (x, y, r.randrange(4, 16 - x + 1), r.randrange(4, 16 - y + 1), r.choice([0.0, 0.25]), r.choice([0.75, 1.0])))
        if r.random() < 0.08:   # mid-scene clear (inside the viewport / scissor; s6ngl: NxOp_Clear since ABI v3)
            L.append('clear %d %s %g 0x%02x' % (r.choice([1, 2, 4, 7]), col(r), r.choice([1.0, 0.5]), r.randrange(256)))
        if r.random() < 0.05: L.append('rs DEPTHBIAS f:0')
        L += geometry(r, w, h)
    # O7: Nine samples a stage's texture only when an argument names TA_TEXTURE; BLEND*TEXTUREALPHA needs one
    for st in ('0', '1'):
        for kind, a1 in (('COLOROP', 'COLORARG1'), ('ALPHAOP', 'ALPHAARG1')):
            if any(l == 'tss %s %s TOP_BLENDTEXTUREALPHA' % (st, kind) for l in L):
                L = [l for l in L if not l.startswith('tss %s %s ' % (st, a1))] + ['tss %s %s TA_TEXTURE' % (st, a1)]
    tf = [l.split()[2] for l in L if l.startswith('tex 0 ')]
    if not tf or tf[0] not in ('A8R8G8B8', 'X8R8G8B8', 'A8B8G8R8', 'DXT1', 'DXT3', 'DXT5'):   # A2: sRGB read is caps-dependent
        L = [l for l in L if l != 'samp 0 SRGBTEXTURE TRUE']
    if ds != 'D24S8':   # O6: softpipe runs the stencil test without a stencil buffer
        L = [l for l in L if not l.startswith('rs STENCILENABLE') and not l.startswith('rs TWOSIDEDSTENCILMODE')]
    if any(l.startswith('tss 1 COLOROP') for l in L):
        L.insert(4, 'tex 1 A8R8G8B8 4 4 1 hash %d' % r.randrange(1000))
    if rt == 'X8R8G8B8':   # O1: drop DESTALPHA factors
        L = [l.replace('BLEND_DESTALPHA', 'BLEND_ONE').replace('BLEND_INVDESTALPHA', 'BLEND_ZERO') for l in L]
    return L

# ---- running -------------------------------------------------------------------------------------------------------
def run_batch(work, scenes, target, mutant=None):
    """scenes: list of (name, lines) -> {name: (verdict, msgs)}"""
    sd = os.path.join(work, 'scenes'); nd = os.path.join(work, 'nine'); td = os.path.join(work, 'tgt')
    for d in (sd, nd, td):
        shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    paths = []
    for n, L in scenes:
        p = os.path.join(sd, n + '.scene'); open(p, 'w').write('\n'.join(L) + '\n'); paths.append((n, p))
    env = dict(os.environ)
    if mutant: env['V6N_MUTANT'] = mutant
    subprocess.run([os.path.join(BIN, 'v6n_nine'), '-q', '-o', nd] + [p for _, p in paths], capture_output=True)
    subprocess.run([os.path.join(BIN, 'v6n_' + target), '-q', '-o', td] + [p for _, p in paths], capture_output=True, env=env)
    out = {}
    for n, p in paths:
        a, b = os.path.join(nd, n + '.v6nd'), os.path.join(td, n + '.v6nd')
        if not os.path.exists(a) or not os.path.exists(b):
            out[n] = ('SKIP', ['oracle' if not os.path.exists(a) else 'target' + ' did not run'])
            continue
        ok, msgs, _ = C.compare(C.load(a), C.load(b))
        out[n] = ('MATCH' if ok else 'MISMATCH', msgs)
    return out

def mismatches(work, L, target, mutant):
    return run_batch(work, [('m', L)], target, mutant)['m'][0] == 'MISMATCH'

def minimise(work, L, target, mutant=None):
    """ddmin over removable lines (everything but size/rt/ds and the vertex+draw groups), then over whole draws,
    then value simplification; keeps the scene mismatching"""
    def groups(L):
        G, cur = [], []
        for l in L:
            cur.append(l)
            if l.startswith('draw ') or not (l.startswith('v ') or l.startswith('mode ')):
                G.append(cur); cur = []
        if cur: G.append(cur)
        return G
    fixed = lambda g: g[0].split()[0] in ('size', 'rt', 'ds', 'tex')   # an unbound texture is oracle quirk O4
    G = groups(L)
    n = 2
    while len(G) >= 2:
        chunk = max(1, len(G) // n); changed = False
        for i in range(0, len(G), chunk):
            cand = G[:i] + [g for g in G[i:i + chunk] if fixed(g)] + G[i + chunk:]
            if len(cand) < len(G) and any(g[-1].startswith('draw') for g in cand) and mismatches(work, sum(cand, []), target, mutant):
                G = cand; n = max(n - 1, 2); changed = True; break
        if not changed:
            if chunk == 1: break
            n = min(len(G), n * 2)
    L = sum(G, [])
    # simplify vertex colours / texcoords one at a time
    for i, l in enumerate(list(L)):
        if l.startswith('v '):
            p = l.split()
            # texcoords simplify to an off-grid value: exactly 0 is a texel boundary (implementation-defined sampling)
            for k, simple in ((5, '0xffffffff'), (6, '0x00000000'), (7, '0.01171875'), (8, '0.01171875')):
                if p[k] != simple:
                    q = p[:]; q[k] = simple; cand = L[:i] + [' '.join(q)] + L[i + 1:]
                    if mismatches(work, cand, target, mutant): L = cand; p = q
    return L

def main():
    a = sys.argv[1:]
    n, seed, target, mutant, batch = 200, 1, 'gl', None, 50
    repro = os.path.join(ROOT, 'tests/v6n/repro'); do_min = True; sens = False
    i = 0
    while i < len(a):
        if a[i] == '-n': n = int(a[i + 1]); i += 2
        elif a[i] == '-s': seed = int(a[i + 1]); i += 2
        elif a[i] == '--target': target = a[i + 1]; i += 2
        elif a[i] == '--mutant': mutant = a[i + 1]; i += 2
        elif a[i] == '--repro': repro = a[i + 1]; i += 2
        elif a[i] == '--batch': batch = int(a[i + 1]); i += 2
        elif a[i] == '--no-min': do_min = False; i += 1
        elif a[i] == '--sensitivity': sens = True; i += 1
        else: sys.exit('unknown argument ' + a[i])
    work = os.path.join(ROOT, 'build/v6n/fuzz', target)
    r = random.Random(seed)
    cases = [('c%05d' % k, gen_scene(r, target)) for k in range(n)]
    if sens:
        sys.path.insert(0, os.path.dirname(HERE))
        src = open(os.path.join(os.path.dirname(HERE), 'target/gl_target.c')).read()
        arr = src[src.index('static const char* MUTANTS[] = {'):]
        arr = arr[:arr.index('0};')]
        import re
        muts = re.findall(r'^\s*"(\w+)",', arr, re.M)
        base = {}
        for k in range(0, n, batch): base.update(run_batch(work, cases[k:k + batch], target))
        clean = [c for c in cases if base[c[0]][0] == 'MATCH']
        print('sensitivity over %d cases (%d match unmutated):' % (n, len(clean)))
        for m in muts:
            res = {}
            for k in range(0, len(clean), batch): res.update(run_batch(work, clean[k:k + batch], target, m))
            kills = sum(v[0] == 'MISMATCH' for v in res.values())
            print('  %-24s killed by %4d / %d random cases' % (m, kills, len(clean)))
        return 0
    found = 0; stats = {'MATCH': 0, 'MISMATCH': 0, 'SKIP': 0}
    os.makedirs(repro, exist_ok=True)
    for k in range(0, n, batch):
        res = run_batch(work, cases[k:k + batch], target, mutant)
        for name, (v, msgs) in res.items():
            stats[v] += 1
            if v != 'MISMATCH':
                continue
            L = dict(cases)[name]
            if do_min:
                L = minimise(os.path.join(work, 'min'), L, target, mutant)
                v2, msgs2 = run_batch(os.path.join(work, 'min'), [('m', L)], target, mutant)['m']
                msgs = msgs2 or msgs
            h = hashlib.sha1('\n'.join(L).encode()).hexdigest()[:10]
            p = os.path.join(repro, '%s_%s.scene' % (target if not mutant else target + '-' + mutant, h))
            if not os.path.exists(p):
                open(p, 'w').write('# fuzz.py reproducer: target %s%s, seed %d, case %s\n# %s\n' % (
                    target, ' mutant ' + mutant if mutant else '', seed, name, (msgs[0] if msgs else '')[:200]) + '\n'.join(L) + '\n')
                found += 1
                print('MISMATCH %s -> %s (%d lines)\n  %s' % (name, os.path.relpath(p, ROOT), len(L), (msgs[0] if msgs else '')[:160]))
    print('fuzz: %d cases on %s%s: %d match, %d mismatch, %d skipped; %d new reproducers' % (
        n, target, ' (mutant %s)' % mutant if mutant else '', stats['MATCH'], stats['MISMATCH'], stats['SKIP'], found))
    return 1 if stats['MISMATCH'] else 0

if __name__ == '__main__':
    sys.exit(main())
