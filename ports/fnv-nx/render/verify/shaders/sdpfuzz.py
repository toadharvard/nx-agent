#!/usr/bin/env python3
# V6n: whole-package shader parity by fuzzing. Every program in the given .sdp packages is translated with S6b's
# sdp2dksh (--glsl) and executed twice per seed: the original D3D9 bytecode on the oracle (Gallium Nine on softpipe,
# v6n_nine) and S6b's GLSL on a GL target (v6n_gl = V6n's executor of the layout contract, or v6n_s6ngl = S6n's
# backend through its shim; vertex programs need v6n_gl). Inputs are random per seed:
#   pixel programs : a driver VS (tests/v6n/shaders/drv2|drv3.vsa) feeds COLOR0/1 (random vertex colours) and
#                    TEXCOORD0..7 (uv and random affine maps of it); 8 random textures (point sampled; 2D, or 8x8 cube
#                    where the translation declares samplerCube); random
#                    c0..c63; 16x16 A8R8G8B8 target.
#   vertex programs: random FLOAT4 vertex elements for every declared input (vdecl / vg, D3DDECLUSAGE + index; the
#                    position transform's CTAB matrix (name containing "Proj") near identity so the output covers the
#                    target, every other float constant random); a generated PS writes a random linear combination of
#                    every COLOR / TEXCOORD output to an A32B32G32R32F target; D24S8 depth compares oPos.z.
# Identical bytecode (same hash, in one or several packages) is run once and its result shared.
# Output (machine readable): <work>/results.json (schema "v6n-sdpfuzz/1", see RENDER_VERIFY.md §3.7); game-derived,
# so it stays in build/ or the scratch directory. Aggregate counts only go to the docs.
#
#   python3 -I render/verify/shaders/sdpfuzz.py PKG.sdp... [--target gl|s6ngl] [--seeds K] [--work DIR] [--jobs 2 (oracle + target processes)]
#           [--translated DIR]  translations shared by several runs (made once with --no-dksh, then reused)
#           [--shard K/N]   only the unique bytecode with int(hash) % N == K (one work dir per shard: D17 ten-minute chunks)
#   python3 -I render/verify/shaders/sdpfuzz.py --merge OUT.json WORK1 WORK2 ...   join the shards' results.json
import os, sys, subprocess, random, json, struct, hashlib, time, collections, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import numpy as np
import cmp as C
import parity as P
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
USAGE = {'POSITION': 0, 'BLENDWEIGHT': 1, 'BLENDINDICES': 2, 'NORMAL': 3, 'PSIZE': 4, 'TEXCOORD': 5, 'TANGENT': 6,
         'BINORMAL': 7, 'TESSFACTOR': 8, 'POSITIONT': 9, 'COLOR': 10, 'FOG': 11, 'DEPTH': 12, 'SAMPLE': 13}
ATTR_OK = {'POSITION', 'BLENDWEIGHT', 'BLENDINDICES', 'NORMAL', 'COLOR', 'TEXCOORD', 'TANGENT', 'BINORMAL'}

def extract(pkg_path):
    """name -> bytecode of one .sdp (u32 version?, u32 count, u32 ?, then 256-byte name, u32 size, blob)"""
    d = open(pkg_path, 'rb').read(); out = {}; off = 12
    while off + 260 <= len(d):
        nm = d[off:off + 256].split(b'\0')[0].decode('latin1'); sz = struct.unpack_from('<I', d, off + 256)[0]
        out[nm] = d[off + 260:off + 260 + sz]; off += 260 + sz
    return out

import re
def written(glsl, name):
    """components of output vs_<name> the translated VS writes ('xyzw' when written whole)"""
    m = set()
    for w in re.findall(r'\bvs_%s(\.[xyzw]+)?\s*=[^=]' % re.escape(name), glsl):
        m.update(w[1:] if w else 'xyzw')
    return ''.join(c for c in 'xyzw' if c in m)

def combo_ps(outputs, vs_model, asm, sdp2, work, cache, glsl=''):
    """PS = c0 + sum_k out_k * c(k+1): every COLOR/TEXCOORD output of the VS reaches the colour; only the components
    the VS writes are read (D3D9 leaves the others undefined): swizzle replicates the last written one"""
    sem = []
    for o in outputs:
        if o['usage'] not in ('COLOR', 'TEXCOORD'): continue
        m = written(glsl, o['name'])
        if m: sem.append((o['usage'], o['index'], (m + m[-1] * 4)[:4]))
    key = (vs_model.startswith('vs_3'), tuple(sem))
    if key in cache: return cache[key]
    L = []
    if key[0]:
        L.append('ps_3_0')
        for k, (u, i, m) in enumerate(sem): L.append('dcl_%s%d v%d' % (u.lower(), i, k))
        reg = ['v%d.%s' % (k, sem[k][2]) for k in range(len(sem))]
    else:
        L.append('ps_2_0')
        reg = []
        for (u, i, m) in sem:
            r = ('t%d' if u == 'TEXCOORD' else 'v%d') % i; L.append('dcl %s' % r); reg.append(r + '.' + m)
    L.append('mov r0, c0')
    for k, r in enumerate(reg):
        if not key[0] and r.startswith('v'):   # D3D9 saturates vs < 3.0 colour outputs; S6b does not (finding P2):
            L.append('mov_sat r1, %s' % r); r = 'r1'   # saturated here so the other differences stay visible
        L.append('mad r0, %s, c%d, r0' % (r, k + 1))
    L.append('mov oC0, r0')
    n = 'combo%d' % len(cache)
    src = os.path.join(work, n + '.psa'); open(src, 'w').write('\n'.join(L) + '\n')
    b = os.path.join(work, n + '.bin'); subprocess.run([asm, src, b], check=True, capture_output=True)
    sdp = os.path.join(work, n + '.sdp'); subprocess.run([sdp2, '--pack', sdp, n + '.pso=' + b], check=True, capture_output=True)
    od = os.path.join(work, n + '_t'); shutil.rmtree(od, ignore_errors=True); os.makedirs(od)
    subprocess.run([sdp2, '-o', od, '--glsl', '--no-dksh', sdp], check=True, capture_output=True)
    g = [os.path.join(od, f) for f in os.listdir(od) if f.endswith('.glsl')][0]
    cache[key] = (b, g, len(reg))
    return cache[key]

NUM_HELPERS = """
float v6n_log2(float x) { return x == 0.0 ? -1.7014117e38 : log2(abs(x)); }
vec2 v6n_log2(vec2 x) { return vec2(v6n_log2(x.x), v6n_log2(x.y)); }
vec3 v6n_log2(vec3 x) { return vec3(v6n_log2(x.x), v6n_log2(x.y), v6n_log2(x.z)); }
vec4 v6n_log2(vec4 x) { return vec4(v6n_log2(x.x), v6n_log2(x.y), v6n_log2(x.z), v6n_log2(x.w)); }
vec3 v6n_nrm(vec3 v) { float d = dot(v, v); return d == 0.0 ? vec3(0.0) : v * inversesqrt(d); }
vec4 v6n_nrm(vec4 v) { float d = dot(v, v); return d == 0.0 ? vec4(0.0) : v * inversesqrt(d); }
"""
def d3d9_numerics(glsl, model=''):
    """V6n-side variant of a translated shader with Nine's D3D9 numerics: log = log2(|x|) and log(0) = -2^127, nrm(0) = 0,
    rcp(0) / rsq(0) = 2^127 (S6b: log2(x), NaN, 1e38), and for vs < 3.0 oD0/oD1 saturated per vertex. Used only to
    attribute differences (findings P2-P4); the verdict is the unmodified translation's."""
    g = glsl.replace('const float FLT_MAX = 1e38;', 'const float FLT_MAX = 1.7014117e38;')
    g = re.sub(r'\blog2\(', 'v6n_log2(', g); g = re.sub(r'\bnormalize\(', 'v6n_nrm(', g)
    if model.startswith('vs_1') or model.startswith('vs_2'):   # D3D9 saturates oD0/oD1 per vertex (finding P2)
        cl = ''.join('\tvs_%s = clamp(vs_%s, 0.0, 1.0);\n' % (n, n) for n in ('oD0', 'oD1') if '#define vs_%s ' % n in g)
        k = g.find('\tgl_Position.xy += vs_drv_posfix')
        if cl and k > 0: g = g[:k] + cl + g[k:]
    j = g.find('void main()')
    return g[:j] + NUM_HELPERS + g[j:] if j > 0 else g

def vs_scene(j, vbin, vglsl, combo, seed):
    r = random.Random(seed)
    ins = [x for x in j.get('inputs', []) if x['usage'] in ATTR_OK]
    proj = set()
    for c in j.get('ctab', []):
        if c.get('set') == 'float' and 'proj' in c['name'].lower() and c.get('rows', 4) >= 4:
            proj.update(range(c['reg'], c['reg'] + 4)); break
    L = ['size 16 16', 'rt A32B32G32R32F', 'ds D24S8', 'clear 7 0xff000000 1.0 0', 'rs CULLMODE CULL_NONE',
         'rs ZENABLE 1', 'rs ZFUNC CMP_ALWAYS', 'vs %s %s' % (vbin, vglsl), 'ps %s %s' % (combo[0], combo[1])]
    base = min(proj) if proj else None
    for c in range(256):
        if base is not None and c in proj:
            row = [1.0 if k == c - base else 0.0 for k in range(4)]
            if c - base == 2: row = [0.0, 0.0, 0.5, 0.25]          # z in (0,1) for z in [-0.5, 1]
            v = [x + r.uniform(-0.05, 0.05) for x in row]
        else:
            v = [r.uniform(-1, 1) for _ in range(4)]
        L.append('vsc %d %s' % (c, ' '.join('%.5f' % x for x in v)))
    for c in range(combo[2] + 1):
        L.append('psc %d %s' % (c, ' '.join('%.5f' % r.uniform(-1, 1) for _ in range(4))))
    if not ins: return None
    L.append('vdecl ' + ' '.join('%d:%d' % (USAGE[x['usage']], x['index']) for x in ins))
    grid = []
    for gy in range(3):
        for gx in range(3):
            # inside the viewport: softpipe's clipper loses pixel centres on the clip edge when w varies (O9)
            grid.append((-0.8 + 0.8 * gx + r.uniform(-0.1, 0.1), -0.8 + 0.8 * gy + r.uniform(-0.1, 0.1)))
    tris = []
    for gy in range(2):
        for gx in range(2):
            a, b_, c_, d = gy * 3 + gx, gy * 3 + gx + 1, gy * 3 + gx + 3, gy * 3 + gx + 4
            tris += [a, b_, c_, b_, d, c_]
    verts = []
    for gi in range(9):
        vals = []
        for x in ins:
            if x['usage'] == 'POSITION': vals += [grid[gi][0], grid[gi][1], r.uniform(0.0, 0.9), 1.0]
            elif x['usage'] == 'COLOR': vals += [r.uniform(0, 1) for _ in range(4)]
            elif x['usage'] == 'BLENDINDICES': vals += [float(r.randrange(0, 4)) for _ in range(4)]
            elif x['usage'] == 'BLENDWEIGHT': w = [r.uniform(0, 1) for _ in range(4)]; s = sum(w); vals += [k / s for k in w]
            else: vals += [r.uniform(-1, 1) for _ in range(4)]
        verts.append(vals)
    for t in tris: L.append('vg ' + ' '.join('%.5f' % v for v in verts[t]))
    L.append('drawg TRIANGLELIST %d' % (len(tris) // 3))
    return '\n'.join(L) + '\n'

COV_TIE = 2   # differing pixels tolerated per vertex-program seed (centres on an edge of VS-computed triangles: coverage,
              # or the neighbouring triangle's depth / attributes at a shared edge)
SENS = 0.1    # a pixel may differ by SENS x the oracle's own change under the 1e-5 input perturbation (float32 rounding
              # is ~1e-7 relative, so a correct translation stays well inside; an ill-conditioned input is not a defect)
def maxerr(a, b, q=None):
    """-> (ok, colour max |d| (8-bit steps or float units), depth max LSB, px over tolerance, first finding).
    Colour tolerance as cmp.py (2/255, or 1e-3 relative on float targets), depth 8 LSB (D24), each widened per pixel by
    SENS x |oracle - oracle on perturbed inputs| when q is given. Pixels covered in only one image: float (vertex
    program) scenes compare where both are covered and allow COV_TIE coverage differences, excluding pixels whose
    coverage already flips under the perturbation."""
    ch = min(a['ch'], 4)
    ca, cb = a['c'][..., :ch].astype(np.float64), b['c'][..., :ch].astype(np.float64)
    nan2 = np.isnan(ca) & np.isnan(cb); one = np.isnan(ca) ^ np.isnan(cb)
    d = np.where(nan2, 0.0, np.abs(ca - cb)); d = np.where(one, np.inf, d)
    tol = C.FLOAT_REL * np.maximum(1.0, np.abs(np.nan_to_num(ca))) if a['isf'] else np.full(ca.shape, C.COLOR_TOL_8)
    da, db = a['d'].astype(np.int64), b['d'].astype(np.int64)
    dd = np.abs(da - db); dtol = np.full(dd.shape, float(C.DEPTH_TOL[24]))
    unstable_cov = np.zeros(dd.shape, bool)
    if q is not None:
        cq = q['c'][..., :ch].astype(np.float64)
        sq = np.abs(np.nan_to_num(ca, posinf=3e38, neginf=-3e38) - np.nan_to_num(cq, posinf=3e38, neginf=-3e38))
        sq = np.where(np.isnan(ca) ^ np.isnan(cq), np.inf, sq)
        tol = tol + SENS * sq
        dtol = dtol + SENS * np.abs(da - q['d'].astype(np.int64))
        if a['isf']: unstable_cov = (da != 16777215) != (q['d'] != 16777215)
    if a['isf']:
        cova, covb = da != 16777215, db != 16777215
        # O9: D3D9 pixel centres of row 0 / column 0 lie on the x = -w / y = w clip planes; softpipe's clipper drops them
        # for clipped triangles when w varies (llvmpipe's guard band keeps them, as D3D9 hardware does): not compared
        edge = np.zeros(dd.shape, bool); edge[0, :] = True; edge[:, 0] = True
        cova &= ~edge; covb &= ~edge
        both = cova & covb; covdiff = int(((cova != covb) & ~unstable_cov).sum())
    else:
        both = np.ones(dd.shape, bool); covdiff = 0
    badc = (d > tol).any(axis=-1) & both
    badd = (dd > dtol) & both
    ce = float(np.max(np.where(both[..., None], d, 0.0))); de = int(np.max(np.where(both, dd, 0)))
    nbad = int((badc | badd).sum()) + covdiff
    if a['isf'] and nbad <= COV_TIE: nbad = 0   # a pixel on a shared edge drawn by the neighbouring triangle
    msg = ''
    if covdiff > COV_TIE: msg = 'coverage: %d px differ' % covdiff
    elif badc.any():
        y, x = np.argwhere(badc)[0]; msg = 'colour: %d px (max |d| %.4g), first (%d,%d) ref %s test %s' % (badc.sum(), ce, x, y, ca[y, x].tolist(), cb[y, x].tolist())
    elif badd.any(): msg = 'depth: %d px (max %d LSB)' % (badd.sum(), de)
    return nbad == 0, ce, de, nbad, msg

def perturb(text, r, eps=1e-5):
    """the same scene with every constant and vertex value scaled by 1 +- eps (stability probe of the oracle)"""
    out = []
    for l in text.split('\n'):
        p = l.split()
        if p and p[0] in ('vsc', 'psc'):
            l = ' '.join(p[:2] + ['%.9g' % (float(x) * (1 + eps * r.choice((-1, 1)))) for x in p[2:]])
        elif p and p[0] == 'vg':
            l = ' '.join(p[:1] + ['%.9g' % (float(x) * (1 + eps * r.choice((-1, 1)))) for x in p[1:]])
        elif p and p[0] == 'v' and len(p) == 9:
            l = ' '.join(p[:7] + ['%.9g' % (float(x) * (1 + eps * r.choice((-1, 1)))) for x in p[7:]])
        out.append(l)
    return '\n'.join(out)

def run_batches(exe, outdir, scenes, jobs):
    os.makedirs(outdir, exist_ok=True)
    chunk = 400; procs = []
    for i in range(0, len(scenes), chunk):
        procs.append(subprocess.Popen([exe, '-q', '-o', outdir] + scenes[i:i + chunk], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        while len([p for p in procs if p.poll() is None]) >= jobs: time.sleep(0.2)
    for p in procs: p.wait()

def summarize(programs):
    """-> totals, per package counts, unique-bytecode counts (the first program of each hash speaks for it)"""
    tot = collections.Counter(); per = collections.defaultdict(collections.Counter); uniq = collections.Counter(); seen = set()
    for e in programs:
        k = e['status']; tot[k] += 1; tot['programs'] += 1; tot[e['stage']] += 1; tot['%s_%s' % (e['stage'], k)] += 1
        per[e['package']][k] += 1; per[e['package']]['programs'] += 1
        per[e['package']]['parsed'] += e['parsed']; per[e['package']]['translated'] += e['translated']
        if k == 'fail':   # attribution of a failure (see RENDER_VERIFY.md §3.7)
            tot['fail_' + e['cause']] += 1; per[e['package']]['fail_' + e['cause']] += 1
        if e['translated'] and e['hash'] not in seen:
            seen.add(e['hash']); uniq[k] += 1
            if k == 'fail': uniq['fail_' + e['cause']] += 1
    return tot, per, uniq

def merge(out, works):
    rs = [json.load(open(os.path.join(w, 'results.json'))) for w in works]
    progs = []
    for i, e in enumerate(rs[0]['programs']):
        cands = [r['programs'][i] for r in rs if r['programs'][i]['status'] != 'other_shard']
        assert all(c['hash'] == e['hash'] for c in cands), 'shards of different package sets'
        progs.append(cands[0] if cands else e)
    tot, per, uniq = summarize(progs)
    m = dict(rs[0]); m['shards'] = [r.get('shard') for r in rs]; m.pop('shard', None)
    m['packages'] = [{**{k: v for k, v in p.items() if k in ('file', 'sha256')}, **per[p['file']]} for p in rs[0]['packages']]
    m['totals'] = dict(tot); m['unique_bytecode'] = dict(uniq); m['programs'] = progs
    json.dump(m, open(out, 'w'), indent=1)
    print('merged %d shards: %s' % (len(rs), out)); print('totals: %s' % json.dumps(dict(tot), sort_keys=True))
    print('unique bytecode: %s' % json.dumps(dict(uniq), sort_keys=True))
    return 0

def main():
    a = sys.argv[1:]
    if a and a[0] == '--merge': return merge(a[1], a[2:])
    tools = os.environ.get('FNVNX_SHADERS_BUILD', os.path.join(ROOT, 'render/shaders/build/host'))
    bindir = os.path.join(ROOT, 'build/v6n/bin'); work = os.path.join(ROOT, 'build/v6n/sdpfuzz')
    target = 'gl'; seeds = 4; jobs = 2; pk = []; i = 0; compare_only = False; shard = None; translated = None
    while i < len(a):
        if a[i] == '--target': target = a[i + 1]; i += 2
        elif a[i] == '--seeds': seeds = int(a[i + 1]); i += 2
        elif a[i] == '--work': work = a[i + 1]; i += 2
        elif a[i] == '--jobs': jobs = int(a[i + 1]); i += 2
        elif a[i] == '--tools': tools = a[i + 1]; i += 2
        elif a[i] == '--bin': bindir = os.path.abspath(a[i + 1]); i += 2
        elif a[i] == '--compare-only': compare_only = True; i += 1   # reuse the images of a previous run
        elif a[i] == '--shard': shard = tuple(int(x) for x in a[i + 1].split('/')); i += 2
        elif a[i] == '--translated': translated = os.path.abspath(a[i + 1]); i += 2
        else: pk.append(os.path.abspath(a[i])); i += 1
    if not pk: sys.exit(__doc__)
    asm, sdp2 = os.path.join(tools, 's6b_asm'), os.path.join(tools, 'sdp2dksh')
    work = os.path.abspath(work); os.makedirs(work, exist_ok=True)
    if translated:   # shared by shards: translated once (GLSL only; DKSH compilation is S6b's census), then reused
        tr = translated; os.makedirs(tr, exist_ok=True); tl = os.path.join(tr, 'translator.txt')
        if not os.path.exists(os.path.join(tr, 'index.tsv')):
            r = subprocess.run([sdp2, '-o', tr, '--glsl', '--no-dksh'] + pk, capture_output=True, text=True)
            open(tl, 'w').write(r.stdout.strip().split('\n')[-1] if r.stdout.strip() else r.stderr.strip()[:200])
        tline = open(tl).read().strip() if os.path.exists(tl) else ''
    else:
        tr = os.path.join(work, 'translated'); os.makedirs(tr, exist_ok=True)
        r = subprocess.run([sdp2, '-o', tr, '--glsl'] + pk, capture_output=True, text=True)
        tline = r.stdout.strip().split('\n')[-1] if r.stdout.strip() else r.stderr.strip()[:200]
    rows = [l.rstrip('\n').split('\t') for l in open(os.path.join(tr, 'index.tsv'))][1:]
    blobs = {p: extract(p) for p in pk}
    dvs = P.driver_vs(asm, sdp2, work)
    sw = os.path.join(work, 'scenes'); os.makedirs(sw, exist_ok=True)
    programs = []; byhash = {}; scenes = []; cache = {}; numscenes = []; probescenes = []; undriven_of = {}
    for h, pkg, name, model, status, *rest in rows:
        stage = 'pixel' if model.startswith('ps') else 'vertex'
        bc = blobs.get(pkg, {}).get(name)
        e = {'package': os.path.basename(pkg), 'name': name, 'model': model, 'stage': stage, 'hash': h,
             'bytes': len(bc) if bc else 0, 'parsed': bc is not None, 'translated': status == 'ok',
             'status': None, 'undriven': [], 'seeds': 0, 'max_err_color': None, 'max_err_depth_lsb': None,
             'bad_px': None, 'detail': ''}
        programs.append(e)
        if status != 'ok':
            e['status'] = 'translate_fail'; e['detail'] = (rest[-1] if rest else '')[:200]; continue
        if shard and int(h, 16) % shard[1] != shard[0]:
            e['status'] = 'other_shard'; continue
        if h in byhash: continue
        byhash[h] = []
        binp = os.path.join(work, h + '.bin')
        if not os.path.exists(binp): open(binp, 'wb').write(bc)
        j = json.load(open(os.path.join(tr, h + '.json'))); g = os.path.join(tr, h + '.glsl')
        und = []
        if stage == 'pixel':
            und = [x['name'] for x in j.get('inputs', []) if not ((x['usage'] == 'TEXCOORD' and x['index'] < 8) or
                   (x['usage'] == 'COLOR' and x['index'] < 2) or x['usage'] in ('POSITION', 'FOG') or x['name'] in ('vPos', 'vFace'))]
            gs = open(g).read()
            if 'uniform sampler3D' in gs: und.append('sampler3D')
            for sd in range(seeds):
                sc = os.path.join(sw, '%s_p%d.scene' % (h, sd))
                open(sc, 'w').write(P.drive_scene(binp, g, sd + 1 + 1000 * len(byhash), dvs, model)); byhash[h].append(sc)
        else:
            und = ['%s%d' % (x['usage'], x['index']) for x in j.get('inputs', []) if x['usage'] not in ATTR_OK]
            if target != 'gl':
                byhash[h] = None; continue
            combo = combo_ps(j.get('outputs', []), model, asm, sdp2, work, cache, open(g).read())
            for sd in range(seeds):
                t = vs_scene(j, binp, g, combo, sd + 1 + 1000 * len(byhash))
                if t is None: byhash[h] = None; break
                sc = os.path.join(sw, '%s_v%d.scene' % (h, sd)); open(sc, 'w').write(t); byhash[h].append(sc)
        e['undriven'] = und; undriven_of[h] = und
        if byhash[h]:
            gn = os.path.join(work, h + '.num.glsl'); open(gn, 'w').write(d3d9_numerics(open(g).read(), model))
            for sc in list(byhash[h]):
                nsc = sc[:-6] + 'n.scene'
                open(nsc, 'w').write(open(sc).read().replace(' %s\n' % g, ' %s\n' % gn))
                numscenes.append(nsc)
                psc_ = sc[:-6] + 'q.scene'
                open(psc_, 'w').write(perturb(open(sc).read(), random.Random(os.path.basename(sc))))   # by name: the same in every work dir
                probescenes.append(psc_)
        scenes += byhash[h] or []
    print('%s; %d programs, %d unique translated, %d scenes' % (tline, len(programs), len(byhash), len(scenes)), flush=True)
    nd, gd = os.path.join(work, 'out_nine'), os.path.join(work, 'out_' + target)
    t0 = time.time()
    import threading   # oracle and target side by side: --jobs processes in all (default 2)
    th = [threading.Thread(target=run_batches, args=(os.path.join(bindir, x), d, sl, max(1, jobs // 2)))
          for x, d, sl in (('v6n_nine', nd, scenes + probescenes), ('v6n_' + target, gd, scenes + numscenes))]
    if not compare_only:
        for x in th: x.start()
        for x in th: x.join()
    print('ran in %.0f s' % (time.time() - t0), flush=True)
    res = {}
    for h, scs in byhash.items():
        if scs is None: res[h] = {'status': 'not_run', 'detail': 'no drivable input' if target == 'gl' else 'vertex programs need --target gl'}; continue
        ce = 0.0; de = 0; bp = 0; det = ''; seeds_res = []; onan = False
        err = None
        for sc in scs:
            base = os.path.basename(sc)[:-6]
            fa, fb = os.path.join(nd, base + '.v6nd'), os.path.join(gd, base + '.v6nd')
            fq, fn = os.path.join(nd, base + 'q.v6nd'), os.path.join(gd, base + 'n.v6nd')
            if not os.path.exists(fa): err = ('oracle_error', 'oracle could not run the bytecode'); break
            if not os.path.exists(fb): err = ('target_error', 'target could not run the GLSL'); break
            A = C.load(fa); Q = C.load(fq) if os.path.exists(fq) else None
            ok, c_, d_, n_, m_ = maxerr(A, C.load(fb), Q)
            num_ok = os.path.exists(fn) and maxerr(A, C.load(fn), Q)[0]
            seeds_res.append((ok, num_ok))
            if not ok and np.isnan(A['c']).any(): onan = True   # the oracle itself produced NaN on this input
            ce = max(ce, c_); de = max(de, d_); bp = max(bp, n_)
            if not ok and not det: det = m_
        if err:
            res[h] = {'status': err[0], 'detail': err[1]}; continue
        st = 'pass' if all(ok for ok, _ in seeds_res) else 'fail'
        nst = 'pass' if all(nok for _, nok in seeds_res) else 'fail'
        res[h] = {'status': st, 'max_err_color': ce, 'max_err_depth_lsb': de, 'bad_px': bp, 'seeds': len(scs), 'detail': det[:200],
                  'status_d3d9_numerics': nst, 'oracle_nan': onan, 'undriven': undriven_of.get(h, [])}
        if st == 'fail':
            res[h]['cause'] = ('d3d9_numerics' if nst == 'pass' else 'undriven_input' if res[h]['undriven'] else
                               'oracle_nan' if onan else 'open')
    for e in programs:
        if e['status'] is None: e.update(res[e['hash']])
    tot, per, uniq = summarize(programs)
    out = {'schema': 'v6n-sdpfuzz/1', 'target': 'v6n_' + target, 'oracle': 'v6n_nine (Gallium Nine, softpipe + V6n O3/O5 fixes)',
           'seeds': seeds, 'thresholds': {'color_8bit': C.COLOR_TOL_8, 'float_rel': C.FLOAT_REL, 'depth_lsb': C.DEPTH_TOL},
           'translator': tline, 'shard': '%d/%d' % shard if shard else None,
           'packages': [{'file': os.path.basename(p), 'sha256': hashlib.sha256(open(p, 'rb').read()).hexdigest(), **per[os.path.basename(p)]} for p in pk],
           'totals': dict(tot), 'unique_bytecode': dict(uniq), 'programs': programs}
    jp = os.path.join(work, 'results.json'); json.dump(out, open(jp, 'w'), indent=1)
    print('results: %s' % jp)
    print('totals: %s' % json.dumps(dict(tot), sort_keys=True))
    print('unique bytecode: %s' % json.dumps(dict(uniq), sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
