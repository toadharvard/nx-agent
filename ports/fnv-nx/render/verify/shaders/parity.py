#!/usr/bin/env python3
# V6n shader parity harness (S6b's tools: SM1-3 assembler, sdp2dksh, MojoShader glsldk GLSL).
#
#   python3 -I render/verify/shaders/parity.py --selftest            synthetic corpus tests/v6n/shaders/corpus.v6ns
#   python3 -I render/verify/shaders/parity.py PATH/shaderpackage*.sdp  the player's packages (not in the repo)
#   options: --tools DIR (s6b_asm, sdp2dksh; default $FNVNX_SHADERS_BUILD or render/shaders/build/host)
#            --bin DIR (v6n_nine, v6n_gl), --work DIR, --max N (pixel shaders per package), --seeds K,
#            --target gl|s6ngl (V6n's GL executor, or S6n's GL backend: S6b's GLSL through nxgl_shim_glsl)
#
# Per pixel shader: sdp2dksh translates it (front end + GLSL); the shader then draws the same scenes on the oracle
# (Nine executes the D3D9 bytecode on softpipe) and on V6n's GL target (executes S6b's GLSL under the layout
# contract render/shaders/include/fnvnx_dk_layout.h, with S6n's translator for every state). Scenes: a textured
# RHW quad with random c0..c7, diffuse/specular gradients, and (option fog/persp) table fog on a perspective quad.
# Inputs the harness does not drive (t1..t7, v1 beyond specular, VS-specific semantics) are reported per shader.
# Vertex shaders are translated and validated (DKSH header) only. Output: one line per shader + summary;
# decomp/game-derived results stay in --work (default build/v6n/parity), never in git.
import os, sys, subprocess, random, json, struct, shutil, re
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import cmp as C
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))

def corpus(path):
    out, cur = [], None
    for l in open(path):
        if l.startswith('=== '):
            p = l[4:].split(); cur = {'name': p[0], 'opts': p[1:], 'src': []}; out.append(cur)
        elif cur is not None and not l.startswith('//'):
            cur['src'].append(l)
    return out

def scene_text(name, bin_, glsl, seed, opts):
    r = random.Random(seed)
    L = ['size 16 16', 'rt A8R8G8B8', 'ds D24S8', 'clear 7 0xff203040 1.0 0', 'rs CULLMODE CULL_NONE',
         'tex 0 A8R8G8B8 8 8 1 hash %d' % (seed * 7 + 1), 'samp 0 MINFILTER TEXF_POINT', 'samp 0 MAGFILTER TEXF_POINT',
         'ps %s %s' % (bin_, glsl)]
    for c in range(8):
        L.append('psc %d %s' % (c, ' '.join('%.4f' % r.uniform(-1, 1.5) for _ in range(4))))
    persp = 'persp' in opts or 'fog' in opts
    if 'fog' in opts:
        L += ['rs FOGENABLE 1', 'rs FOGTABLEMODE FOG_LINEAR', 'rs FOGSTART f:0.2', 'rs FOGEND f:0.9', 'rs FOGCOLOR 0xff00ff00']
    w = [1, 1, 1, 1] if not persp else [0.5, 2.0, 1.0, 0.25]
    z = [0.5] * 4 if not persp else [0.2, 0.4, 0.6, 0.8]
    P = [(0, 0, 0.0117, 0.0117), (16, 0, 1.0117, 0.0117), (0, 16, 0.0117, 1.0117), (16, 16, 1.0117, 1.0117)]
    cols = ['0xff%06x' % r.getrandbits(24) for _ in range(4)]
    L.append('mode rhw')
    for i, (x, y, u, v) in enumerate(P):
        L.append('v %g %g %g %g %s 0x%08x %g %g' % (x, y, z[i], w[i], cols[i], r.getrandbits(32), u, v))
    L.append('draw TRIANGLESTRIP 2')
    return '\n'.join(L) + '\n'

def cube_samplers(glsl_text):
    """sampler stages the translation declares as samplerCube (layout(binding = N) uniform samplerCube ...)"""
    return {int(m) for m in re.findall(r'layout\(binding = (\d+)\) uniform samplerCube', glsl_text)}

def drive_scene(bin_, glsl, seed, vs, model):
    """packages: a VS (tests/v6n/shaders/drv2|drv3.vsa) drives COLOR0/1 and TEXCOORD0..7 (TEXCOORD0 = uv, 1..7 random
    affine in uv), 8 point-sampled textures (2D, or an 8x8 cube where the translation declares a samplerCube),
    random c0..c63"""
    r = random.Random(seed)
    cubes = cube_samplers(open(glsl).read())
    L = ['size 16 16', 'rt A8R8G8B8', 'ds D24S8', 'clear 7 0xff203040 1.0 0', 'rs CULLMODE CULL_NONE']
    for s_ in range(8):
        if s_ in cubes: L.append('texcube %d A8R8G8B8 8 hash %d' % (s_, seed * 7 + 1 + s_ * 101))
        else: L.append('tex %d A8R8G8B8 8 8 1 hash %d' % (s_, seed * 7 + 1 + s_ * 101))
        L += ['samp %d MINFILTER TEXF_POINT' % s_, 'samp %d MAGFILTER TEXF_POINT' % s_, 'samp %d MIPFILTER TEXF_NONE' % s_]
    vb, vg = vs['vs_3_0' if model.startswith('ps_3') else 'vs_2_0']
    L += ['vs %s %s' % (vb, vg), 'ps %s %s' % (bin_, glsl)]
    for c in range(1, 16):
        L.append('vsc %d %s' % (c, ' '.join('%.4f' % r.uniform(-1.5, 1.5) for _ in range(4))))
    for c in range(64):
        L.append('psc %d %s' % (c, ' '.join('%.4f' % r.uniform(-1, 1.5) for _ in range(4))))
    L.append('mode xyz')
    for (x, y, u, v) in [(-1, 1, 0.0117, 0.0117), (1, 1, 1.0117, 0.0117), (-1, -1, 0.0117, 1.0117), (1, -1, 1.0117, 1.0117)]:
        L.append('v %g %g 0.5 1 0xff%06x 0x%08x %g %g' % (x, y, r.getrandbits(24), r.getrandbits(32), u, v))
    L.append('draw TRIANGLESTRIP 2')
    return '\n'.join(L) + '\n'

def driver_vs(asm, sdp2, work):
    out = {}
    for n, model in (('drv2', 'vs_2_0'), ('drv3', 'vs_3_0')):
        b = os.path.join(work, n + '.bin'); sdp = os.path.join(work, n + '.sdp'); od = os.path.join(work, n + '_t')
        subprocess.run([asm, os.path.join(ROOT, 'tests/v6n/shaders', n + '.vsa'), b], check=True)
        subprocess.run([sdp2, '--pack', sdp, n + '.vso=' + b], check=True, capture_output=True)
        shutil.rmtree(od, ignore_errors=True); os.makedirs(od)
        subprocess.run([sdp2, '-o', od, '--glsl', '--no-dksh', sdp], check=True, capture_output=True)
        out[model] = (b, [os.path.join(od, f) for f in os.listdir(od) if f.endswith('.glsl')][0])
    return out

def main():
    a = sys.argv[1:]
    tools = os.environ.get('FNVNX_SHADERS_BUILD', os.path.join(ROOT, 'render/shaders/build/host'))
    bindir = os.path.join(ROOT, 'build/v6n/bin'); work = os.path.join(ROOT, 'build/v6n/parity')
    selftest = False; pk = []; maxn = 100000; seeds = 2; target = 'gl'
    i = 0
    while i < len(a):
        if a[i] == '--selftest': selftest = True; i += 1
        elif a[i] == '--tools': tools = a[i + 1]; i += 2
        elif a[i] == '--bin': bindir = a[i + 1]; i += 2
        elif a[i] == '--work': work = a[i + 1]; i += 2
        elif a[i] == '--max': maxn = int(a[i + 1]); i += 2
        elif a[i] == '--seeds': seeds = int(a[i + 1]); i += 2
        elif a[i] == '--target': target = a[i + 1]; i += 2   # gl (V6n's executor) or s6ngl (S6n's backend + shim)
        else: pk.append(os.path.abspath(a[i])); i += 1
    asm, sdp2 = os.path.join(tools, 's6b_asm'), os.path.join(tools, 'sdp2dksh')
    for t in (sdp2, asm):
        if not os.path.exists(t):
            sys.exit('missing %s: run render/shaders/build.sh --tests (FNVNX_SHADERS_BUILD=...)' % t)
    os.makedirs(work, exist_ok=True)
    opts = {}
    if selftest:
        entries = []
        for s in corpus(os.path.join(ROOT, 'tests/v6n/shaders/corpus.v6ns')):
            src = os.path.join(work, s['name'] + '.asm'); open(src, 'w').write(''.join(s['src']))
            b = os.path.join(work, s['name'] + '.bin')
            r = subprocess.run([asm, src, b], capture_output=True, text=True)
            if r.returncode:
                print('%-20s ASSEMBLY FAILED %s' % (s['name'], r.stderr.strip()[:100])); continue
            entries.append('%s.pso=%s' % (s['name'], b)); opts[s['name'] + '.pso'] = s['opts']
        sdp = os.path.join(work, 'shaderpackage900.sdp')
        subprocess.run([sdp2, '--pack', sdp] + entries, check=True, capture_output=True)
        pk = [sdp]
    if not pk:
        sys.exit(__doc__ if __doc__ else 'give .sdp paths or --selftest')
    tr = os.path.join(work, 'translated'); os.makedirs(tr, exist_ok=True)
    dvs = driver_vs(asm, sdp2, work) if not selftest else None
    r = subprocess.run([sdp2, '-o', tr, '--glsl'] + pk, capture_output=True, text=True)
    print(r.stdout.strip().split('\n')[-1] if r.stdout.strip() else r.stderr.strip()[:200])
    rows = [l.rstrip('\n').split('\t') for l in open(os.path.join(tr, 'index.tsv'))][1:]
    stats = {'ps': 0, 'vs': 0, 'translate_fail': 0, 'match': 0, 'mismatch': 0, 'error': 0, 'undriven': 0}
    scenes = []; meta = {}
    for h, pkg, name, model, status, *rest in rows:
        is_ps = model.startswith('ps')
        stats['ps' if is_ps else 'vs'] += 1
        if status != 'ok':
            stats['translate_fail'] += 1
            print('%-24s %-8s TRANSLATE-FAIL %s' % (name, model, rest[-1][:90] if rest else '')); continue
        if not is_ps or stats['ps'] > maxn:
            continue
        j = json.load(open(os.path.join(tr, h + '.json')))
        if selftest:
            undriven = [x['name'] for x in j.get('inputs', []) if not ((x['usage'] == 'TEXCOORD' and x['index'] == 0) or
                        (x['usage'] == 'COLOR' and x['index'] < 2) or x['usage'] in ('POSITION', 'FOG') or x['name'] == 'vPos')]
        else:   # the driver VS feeds COLOR0/1 and TEXCOORD0..7; 2D textures only
            undriven = [x['name'] for x in j.get('inputs', []) if not ((x['usage'] == 'TEXCOORD' and x['index'] < 8) or
                        (x['usage'] == 'COLOR' and x['index'] < 2) or x['usage'] in ('POSITION', 'FOG') or x['name'] in ('vPos', 'vFace'))]
            g = open(os.path.join(tr, h + '.glsl')).read()
            undriven += ['sampler3D' for _ in range(g.count('uniform sampler3D'))][:1]   # cube samplers are driven
        stats['undriven'] += bool(undriven)
        for sd in range(seeds):
            sc = os.path.join(work, '%s_%s_%d.scene' % (os.path.basename(pkg)[:-4], name.replace('.', '_'), sd))
            binp = os.path.join(work, h + '.bin')
            if not os.path.exists(binp):   # extract the bytecode from the package
                data = open(next(p for p in pk if os.path.basename(p) == os.path.basename(pkg)), 'rb').read()
                off = 12
                while off < len(data):
                    nm = data[off:off + 256].split(b'\0')[0].decode('latin1'); sz = struct.unpack_from('<I', data, off + 256)[0]
                    if nm == name: open(binp, 'wb').write(data[off + 260:off + 260 + sz]); break
                    off += 260 + sz
            open(sc, 'w').write(scene_text(name, binp, os.path.join(tr, h + '.glsl'), sd + 1, opts.get(name, [])) if selftest
                                else drive_scene(binp, os.path.join(tr, h + '.glsl'), sd + 1, dvs, model))
            scenes.append(sc); meta[sc] = (name, model, undriven)
    if scenes:
        nd, gd = os.path.join(work, 'nine'), os.path.join(work, target)
        for d in (nd, gd): os.makedirs(d, exist_ok=True)
        subprocess.run([os.path.join(bindir, 'v6n_nine'), '-q', '-o', nd] + scenes, capture_output=True)
        subprocess.run([os.path.join(bindir, 'v6n_' + target), '-q', '-o', gd] + scenes, capture_output=True)
        per = {}
        for sc in scenes:
            b = os.path.basename(sc)[:-6] + '.v6nd'
            name, model, und = meta[sc]
            if not os.path.exists(os.path.join(nd, b)) or not os.path.exists(os.path.join(gd, b)):
                v, m = 'ERROR', ['oracle' if not os.path.exists(os.path.join(nd, b)) else 'target' + ' failed']
            else:
                ok, m, _ = C.compare(C.load(os.path.join(nd, b)), C.load(os.path.join(gd, b)))
                v = 'MATCH' if ok else 'MISMATCH'
            pv = per.get(name)
            if pv is None or (pv[0] == 'MATCH' and v != 'MATCH'):
                per[name] = (v, m, model, und)
        for name, (v, m, model, und) in per.items():
            stats[{'MATCH': 'match', 'MISMATCH': 'mismatch', 'ERROR': 'error'}[v]] += 1
            print('%-24s %-8s %-8s%s %s' % (name, model, v, ' undriven=' + ','.join(und) if und else '', (m[0] if m else '')[:110]))
    print('parity: %(ps)d PS, %(vs)d VS; translate failures %(translate_fail)d; PS pixel parity: %(match)d match, '
          '%(mismatch)d mismatch, %(error)d error; PS with undriven inputs %(undriven)d' % stats)
    return 0 if not stats['mismatch'] and not stats['error'] else 1

if __name__ == '__main__':
    sys.exit(main())
