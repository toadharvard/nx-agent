#!/usr/bin/env python3
# V6n shader parity harness (S6b's tools: SM1-3 assembler, sdp2dksh, MojoShader glsldk GLSL).
#
#   python3 -I render/verify/shaders/parity.py --selftest            synthetic corpus tests/v6n/shaders/corpus.v6ns
#   python3 -I render/verify/shaders/parity.py PATH/shaderpackage*.sdp  the player's packages (not in the repo)
#   options: --tools DIR (s6b_asm, sdp2dksh; default $FNVNX_SHADERS_BUILD or render/shaders/build/host)
#            --bin DIR (v6n_nine, v6n_gl), --work DIR, --max N (pixel shaders per package), --seeds K
#
# Per pixel shader: sdp2dksh translates it (front end + GLSL); the shader then draws the same scenes on the oracle
# (Nine executes the D3D9 bytecode on softpipe) and on V6n's GL target (executes S6b's GLSL under the layout
# contract render/shaders/include/fnvnx_dk_layout.h, with S6n's translator for every state). Scenes: a textured
# RHW quad with random c0..c7, diffuse/specular gradients, and (option fog/persp) table fog on a perspective quad.
# Inputs the harness does not drive (t1..t7, v1 beyond specular, VS-specific semantics) are reported per shader.
# Vertex shaders are translated and validated (DKSH header) only. Output: one line per shader + summary;
# decomp/game-derived results stay in --work (default build/v6n/parity), never in git.
import os, sys, subprocess, random, json, struct
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

def main():
    a = sys.argv[1:]
    tools = os.environ.get('FNVNX_SHADERS_BUILD', os.path.join(ROOT, 'render/shaders/build/host'))
    bindir = os.path.join(ROOT, 'build/v6n/bin'); work = os.path.join(ROOT, 'build/v6n/parity')
    selftest = False; pk = []; maxn = 100000; seeds = 2
    i = 0
    while i < len(a):
        if a[i] == '--selftest': selftest = True; i += 1
        elif a[i] == '--tools': tools = a[i + 1]; i += 2
        elif a[i] == '--bin': bindir = a[i + 1]; i += 2
        elif a[i] == '--work': work = a[i + 1]; i += 2
        elif a[i] == '--max': maxn = int(a[i + 1]); i += 2
        elif a[i] == '--seeds': seeds = int(a[i + 1]); i += 2
        else: pk.append(os.path.abspath(a[i])); i += 1
    asm, sdp2 = os.path.join(tools, 's6b_asm'), os.path.join(tools, 'sdp2dksh')
    for t in (sdp2,) + ((asm,) if selftest else ()):
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
        undriven = [x['name'] for x in j.get('inputs', []) if not ((x['usage'] == 'TEXCOORD' and x['index'] == 0) or
                    (x['usage'] == 'COLOR' and x['index'] < 2) or x['usage'] in ('POSITION', 'FOG') or x['name'] == 'vPos')]
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
            open(sc, 'w').write(scene_text(name, binp, os.path.join(tr, h + '.glsl'), sd + 1, opts.get(name, [])))
            scenes.append(sc); meta[sc] = (name, model, undriven)
    if scenes:
        nd, gd = os.path.join(work, 'nine'), os.path.join(work, 'gl')
        for d in (nd, gd): os.makedirs(d, exist_ok=True)
        subprocess.run([os.path.join(bindir, 'v6n_nine'), '-q', '-o', nd] + scenes, capture_output=True)
        subprocess.run([os.path.join(bindir, 'v6n_gl'), '-q', '-o', gd] + scenes, capture_output=True)
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
