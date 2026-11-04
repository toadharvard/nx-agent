#!/usr/bin/env python3
"""perm.py - S16-compare's shader-permutation captures: one NXD9 frame per unique program of a shader package, so the
reference (nxd9play under Wine) and the candidate (nx_render d3d9) compare every permutation through the whole
pipeline (vertex declaration, constants, samplers, render states, the translated GLSL nx_render embeds).

    perm.py build --sdp <shaderpackageNNN.sdp> --asm <s6b_asm> --sdp2dksh <sdp2dksh> --out <dir>
                  [--per-capture 64] [--size 96] [--only <hash,...>] [--programs <list.txt>]

<dir>/perm_<k>.nxd9 hold up to --per-capture frames, one program each. <dir>/manifest.json (fnvnx-perm/1) maps
every frame to its program (name, hash, stage, model) and lists the programs that were not captured and why.
--perturb EPS writes the same captures with every float input (constants, vertex floats) scaled by 1 + EPS * u: the
reference rendered from them against the unperturbed reference is the conditioning floor (S16 decision: EPS 1e-5,
as V6n's SENS test). A frame whose reference moves under it is ill-conditioned; its difference is not a defect.
--programs restricts the set to the hashes or names in a file (one per line): the coverage-guided subset, e.g. the
permutations a real frame used (coverage.py).

The captures and the manifest carry the game's shader bytecode and names: they are derived from game files and stay
in the private repository (tools/compare/README.md, "Captures"). This generator holds no game content.

Driving (V6n's choices, render/verify/shaders/sdpfuzz.py, so a difference here can be read against its results):
* pixel program: tests/v6n/shaders/drv2.vsa (vs < 3) or drv3.vsa (ps_3_0) feeds COLOR0/1 and TEXCOORD0..7 over a
  full-target quad; every sampler gets a smooth 64x64 texture with mips (2D) or a cube with six distinct faces,
  linear filtering, wrap; every float constant uniform in [0, 1].
* vertex program: a vertex declaration from its inputs with the engine's element types (POSITION/NORMAL/TANGENT/
  BINORMAL/TEXCOORD FLOAT4, COLOR and BLENDINDICES D3DCOLOR, BLENDWEIGHT FLOAT4) on a 3x3 vertex grid; every
  CTAB matrix identity (z scaled by 0.5), every CTAB bone palette identity rows with a small translation, every other register c0..c255 uniform in [-1, 1]. Relative addressing stays
  inside the arrays it reads (bone indices inside the CTAB palette, texcoords in [0, 1) when the VS uses a0): D3D9
  leaves reads outside [0, 255] undefined, so differences there are no evidence. A generated PS sums every
  COLOR/TEXCOORD output the VS writes (sdpfuzz.combo_ps).
Seeded by the program hash (plus its salt from --salts): the same package and salts give the same captures.
"""
import argparse, json, os, random, shutil, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'render', 'verify', 'shaders'))
sys.path.insert(0, os.path.join(ROOT, 'render', 'verify'))
import nxd9io
from synth import RS, SS, A8R8G8B8, DXT1, fbits, mips, checker, gradient, dxt1

# parameter sets (--variant): one is applied to every program of a build; the sweep runs several per program
VARIANTS = {
    'base':  {},
    'zero':  {'const': 'zero'},                     # every non-matrix float constant 0
    'neg':   {'const': 'neg'},                      # constants in [-1, 0]
    'large': {'const': 'large'},                    # constants in [-1000, 1000]
    'dxt':   {'tex': 'dxt1'},                       # the 2D textures as DXT1 (with mips)
    'atest': {'rs': {'ALPHATESTENABLE': 1, 'ALPHAREF': 128, 'ALPHAFUNC': 5}},    # alpha test GREATER 128
    'fog':   {'rs': {'FOGENABLE': 1, 'FOGTABLEMODE': 3, 'FOGCOLOR': 0xff8040c0}, 'fog': True},   # linear table fog
}
VARIANT = {'v': VARIANTS['base']}
SHARD = {'k': 1, 'n': 1}   # --shard k/N: the programs whose hash % N == k - 1


def cval(r, lo, hi):
    """one constant component under the variant's range (VS matrices are set apart and stay identity)"""
    m = VARIANT['v'].get('const')
    if m == 'zero': return 0.0
    if m == 'neg': return r.uniform(-1, 0)
    if m == 'large': return r.uniform(-1000, 1000)
    return r.uniform(lo, hi)

FORMAT = 'fnvnx-perm/1'
USAGE = {'POSITION': 0, 'BLENDWEIGHT': 1, 'BLENDINDICES': 2, 'NORMAL': 3, 'PSIZE': 4, 'TEXCOORD': 5, 'TANGENT': 6,
         'BINORMAL': 7, 'COLOR': 10}
FLOAT4, D3DCOLOR, UBYTE4, FLOAT2 = 3, 4, 5, 1
TYPE_OF = {'COLOR': D3DCOLOR, 'BLENDINDICES': D3DCOLOR}   # Gamebryo's skin VS: index = D3DCOLOR * 765.01 (= 3 * 255)
PERTURB = {'eps': 0.0, 'rng': None}


def pf(*v):
    """the float inputs of a draw, each scaled by (1 + eps * u), u uniform in [-1, 1] (--perturb; else unchanged)"""
    if not PERTURB['eps']:
        return list(v)
    r = PERTURB['rng']
    return [x * (1.0 + PERTURB['eps'] * r.uniform(-1, 1)) for x in v]


def sh_rec(sid, bc, glsl):
    h = nxd9io.fnv1a64(bc)
    g = glsl.encode()
    return struct.pack('<4I', sid, h & 0xFFFFFFFF, h >> 32, len(bc)) + bc + b'\0' * ((-len(bc)) % 4) + \
        struct.pack('<I', len(g)) + g


def tex_levels(px, w, h, levels):
    return [bytes(l) for l in mips(px, w, h, levels)]


def make_textures(size=64):
    """two smooth 2D textures (BGRA texel bytes, mip chains) and one cube (six faces of one level chain)"""
    a = checker(size, size, (0xd0, 0x40, 0x20, 0xff), (0x30, 0x90, 0xe0, 0x80), 8)   # (r, g, b, a)
    b = gradient(size, size)
    faces = [checker(16, 16, (40 * k, 255 - 40 * k, 128, 255), (255 - 30 * k, 60, 30 * k, 160), 4) for k in range(6)]
    return a, b, faces


class Capture:
    def __init__(self, path, size, tex):
        self.w = nxd9io.Writer(path)
        self.size = size
        self.n = {'VSH ': 0, 'PSH ': 0, 'DECL': 0, 'VB  ': 0, 'IB  ': 0}
        self.w.u32('DEV ', size, size, A8R8G8B8, 0)
        a, b, faces = tex
        sz = 64
        lv = 7
        for tid, px in ((1, a), (2, b)):
            if VARIANT['v'].get('tex') == 'dxt1':
                ls = [dxt1(m, max(1, sz >> i), max(1, sz >> i)) for i, m in enumerate(tex_levels(px, sz, sz, lv))]
                self.w.rec('TEX ', struct.pack('<6I', tid, DXT1, sz, sz, lv, 0) + b''.join(struct.pack('<I', len(l)) + l for l in ls))
            else:
                self.w.rec('TEX ', struct.pack('<6I', tid, A8R8G8B8, sz, sz, lv, 0) +
                           b''.join(struct.pack('<I', len(l)) + l for l in tex_levels(px, sz, sz, lv)))
        cube = b''.join(bytes(f) for f in faces)
        self.w.rec('TEX ', struct.pack('<6I', 3, A8R8G8B8, 16, 16, 1, 2) + struct.pack('<I', len(cube)) + cube)
        self.w.u32('RS  ', RS['CULLMODE'], 1)
        for k, v in VARIANT['v'].get('rs', {}).items():
            self.w.u32('RS  ', RS[k], v)
        if VARIANT['v'].get('fog'):
            self.w.u32('RS  ', RS['FOGSTART'], fbits(0.2)); self.w.u32('RS  ', RS['FOGEND'], fbits(0.8))
        self.w.u32('RS  ', 7, 0)     # ZENABLE false: no depth buffer
        self.w.u32('RS  ', RS['ZWRITEENABLE'], 0)
        for s in range(16):
            for k, v in (('MAGFILTER', 2), ('MINFILTER', 2), ('MIPFILTER', 2), ('ADDRESSU', 1), ('ADDRESSV', 1)):
                self.w.u32('SS  ', s, SS[k], v)

    def new(self, kind):
        self.n[kind] += 1
        return self.n[kind]

    def shader(self, kind, bc, glsl):
        sid = self.new(kind)
        self.w.rec(kind, sh_rec(sid, bc, glsl))
        return sid

    def decl(self, elems):
        did = self.new('DECL')
        self.w.rec('DECL', struct.pack('<2I', did, len(elems)) + b''.join(struct.pack('<HHBBBB', *e) for e in elems))
        return did

    def buffers(self, vdata, stride, idx):
        vb, ib = self.new('VB  '), self.new('IB  ')
        self.w.u32('VB  ', vb, len(vdata))
        self.w.rec('VBU ', struct.pack('<3I', vb, 0, len(vdata)) + vdata)
        data = struct.pack('<%dH' % len(idx), *idx)
        self.w.u32('IB  ', ib, len(data), 101)
        self.w.rec('IBU ', struct.pack('<3I', ib, 0, len(data)) + data)
        self.w.u32('STRM', 0, vb, 0, stride)
        self.w.u32('SIDX', ib)

    def consts(self, tag, regs):
        for r, v in sorted(regs.items()):
            self.w.rec(tag, struct.pack('<2I', r, 1) + struct.pack('<4f', *pf(*v)))

    def frame(self, vs, ps, decl, nverts, ntris, vsc, psc, samplers):
        self.w.rec('CLR ', struct.pack('<2IfII', 1, 0xff404040, 1.0, 0, 0))
        self.w.u32('SVS ', vs)
        self.w.u32('SPS ', ps)
        self.w.u32('SDCL', decl)
        self.consts('VSCF', vsc)
        self.consts('PSCF', psc)
        for s in range(16):
            self.w.u32('STEX', s, samplers.get(s, 0))
        self.w.rec('DRWI', struct.pack('<IiIIII', 4, 0, 0, nverts, 0, ntris))
        self.w.rec('PRES')

    def close(self):
        self.w.close()


def translate_blob(sdp2, bc, work, name):
    """GLSL of one of our own driver programs (packed and translated exactly like the game's)"""
    b = os.path.join(work, name + '.bin')
    open(b, 'wb').write(bc)
    od = os.path.join(work, name + '_t')
    os.makedirs(od, exist_ok=True)
    subprocess.run([sdp2, '-o', od, '--glsl', '--no-dksh', '--blob', b], check=True, capture_output=True)
    return open(os.path.join(od, '%016x.glsl' % nxd9io.fnv1a64(bc))).read()


def assemble(asm, src_path, work, name):
    b = os.path.join(work, name + '.bin')
    r = subprocess.run([asm, src_path, b], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'perm: s6b_asm {name}: {r.stderr.strip()}')
    return open(b, 'rb').read()


def ps_job(j, r):
    """full-target quad for the driver VS: POSITION FLOAT4, COLOR0, COLOR1 (D3DCOLOR), TEXCOORD0 FLOAT2"""
    elems = [(0, 0, FLOAT4, 0, USAGE['POSITION'], 0), (0, 16, D3DCOLOR, 0, USAGE['COLOR'], 0),
             (0, 20, D3DCOLOR, 0, USAGE['COLOR'], 1), (0, 24, FLOAT2, 0, USAGE['TEXCOORD'], 0)]
    verts = b''
    for (x, y, u, v) in ((-1, 1, 0, 0), (1, 1, 1, 0), (1, -1, 1, 1), (-1, -1, 0, 1)):
        c0 = 0xff000000 | r.randrange(1 << 24)
        c1 = 0xff000000 | r.randrange(1 << 24)
        p = pf(x, y, 0.5, 1.0, u * 2.0, v * 2.0)
        verts += struct.pack('<4f2I2f', *p[:4], c0, c1, *p[4:])
    vsc = {k: [r.uniform(-1, 1) for _ in range(4)] for k in range(1, 16)}
    vsc[1] = [r.uniform(0.5, 2), r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(0.5, 2)]
    psc = {c['reg']: [cval(r, 0, 1) for _ in range(4)] for c in j.get('constants', []) if c['set'] == 'float'}
    samplers = {s['reg']: (3 if s['type'] == 'cube' else 1 + (s['reg'] & 1)) for s in j.get('samplers', [])}
    return elems, 32, verts, [0, 1, 2, 0, 2, 3], vsc, psc, samplers


def vs_job(j, r, glsl=''):
    ins = [x for x in j.get('inputs', []) if x['usage'] in USAGE and x['usage'] != 'PSIZE']
    # relative addressing must stay inside the constant arrays it indexes (D3D9 leaves reads outside [0, 255]
    # undefined, so a difference there says nothing): bone indices inside the CTAB palette, other float inputs that
    # can feed a0 (texcoords) in [0, 1)
    rel = 'vs_a0' in glsl
    bones = max([c['count'] // 3 for c in j.get('ctab', []) if c['name'].lower().startswith('bone')] or [4])
    if not any(x['usage'] == 'POSITION' for x in ins):
        return None
    elems, off = [], 0
    for x in ins:
        t = TYPE_OF.get(x['usage'], FLOAT4)
        elems.append((0, off, t, 0, USAGE[x['usage']], x['index']))
        off += 16 if t == FLOAT4 else 4
    grid = [(-0.8 + 0.8 * gx + r.uniform(-0.1, 0.1), -0.8 + 0.8 * gy + r.uniform(-0.1, 0.1))
            for gy in range(3) for gx in range(3)]
    verts = b''
    for gi in range(9):
        for x in ins:
            u = x['usage']
            if u == 'POSITION': verts += struct.pack('<4f', *pf(grid[gi][0], grid[gi][1], r.uniform(0.2, 0.8), 1.0))
            elif u == 'COLOR': verts += struct.pack('<I', r.randrange(1 << 32))
            elif u == 'BLENDINDICES': verts += bytes(r.randrange(0, bones) for _ in range(4))   # B, G, R, A
            elif u == 'BLENDWEIGHT':
                w = [r.uniform(0, 1) for _ in range(4)]; s = sum(w); verts += struct.pack('<4f', *pf(*[k / s for k in w]))
            elif u in ('NORMAL', 'TANGENT', 'BINORMAL'):
                v = [r.uniform(-1, 1) for _ in range(3)]; n = max(1e-3, sum(k * k for k in v) ** 0.5)
                verts += struct.pack('<4f', *pf(*[k / n for k in v], 0.0))
            else: verts += struct.pack('<4f', *pf(*[r.uniform(0, 0.999) if rel else r.uniform(-1, 1) for _ in range(4)]))
    idx = []
    for gy in range(2):
        for gx in range(2):
            a, b, c, d = gy * 3 + gx, gy * 3 + gx + 1, gy * 3 + gx + 3, gy * 3 + gx + 4
            idx += [a, b, c, b, d, c]
    vsc = {k: [cval(r, -1, 1) for _ in range(4)] for k in range(256)}   # every register: relative reads included
    for c in j.get('ctab', []):   # matrices: identity rows (bones, ModelViewProj, ...), z scaled into (0, 1)
        if c.get('set') == 'float' and c.get('class', '').startswith('matrix'):
            rows = max(1, c.get('rows', 4))
            for e in range(c.get('count', rows)):
                k = e % rows
                if k < 4:
                    vsc[c['reg'] + e] = [0.5 if (k == 2 and i == 2) else (1.0 if i == k else 0.0) for i in range(4)]
        elif c.get('set') == 'float' and c['name'].lower().startswith('bone') and c.get('count', 0) % 3 == 0:
            # Gamebryo's bone palette: 3 rows (3x4, translation in .w) per bone, as vectors in the CTAB: identity
            # rotation, a small translation, so skinned vertices stay on the target
            for e in range(c['count']):
                k = e % 3
                vsc[c['reg'] + e] = [1.0 if i == k else 0.0 for i in range(3)] + [r.uniform(-0.1, 0.1)]
    return elems, off, verts, idx, vsc


def build(sdp, asm, sdp2, out, per=64, size=96, only=None, perturb=0.0, salts=None, variant='base'):
    VARIANT['v'] = VARIANTS[variant]
    PERTURB['eps'], PERTURB['rng'] = perturb, random.Random(0x5160)
    os.makedirs(out, exist_ok=True)
    tex = make_textures()
    sdps = sdp if isinstance(sdp, list) else [sdp]
    man = {'format': FORMAT, 'package': ','.join(os.path.basename(x) for x in sdps), 'size': size, 'perturb': perturb, 'variant': variant,
           'captures': {}, 'skipped': []}
    from sdpfuzz import extract, combo_ps
    progs = {}
    for x in sdps:   # several packages: every program, named <package stem>:<name> past the first package (the same
        stem = os.path.splitext(os.path.basename(x))[0]   # name differs in bytecode between packages); deduplicated by hash below
        for n, bc in extract(x).items():
            progs[n if x == sdps[0] else f'{stem}:{n}'] = bc
    with tempfile.TemporaryDirectory() as work:
        tr = os.path.join(work, 'tr')
        os.makedirs(tr)
        r = subprocess.run([sdp2, '-o', tr, '--glsl', '--no-dksh'] + sdps, capture_output=True, text=True)
        drv = {}
        for m in ('drv2', 'drv3'):
            bc = assemble(asm, os.path.join(ROOT, 'tests', 'v6n', 'shaders', m + '.vsa'), work, m)
            drv[m] = (bc, translate_blob(sdp2, bc, work, m))
        seen, jobs = set(), []
        for name in sorted(progs):
            bc = progs[name]
            h = '%016x' % nxd9io.fnv1a64(bc)
            if only and h not in only and name not in only: continue
            if SHARD['n'] > 1 and int(h, 16) % SHARD['n'] != SHARD['k'] - 1: continue
            if h in seen: continue
            seen.add(h)
            jp, gp = os.path.join(tr, h + '.json'), os.path.join(tr, h + '.glsl')
            if not (os.path.isfile(jp) and os.path.isfile(gp)):
                man['skipped'].append({'name': name, 'hash': h, 'why': 'not translated'}); continue
            jobs.append((name, h, bc, json.load(open(jp)), open(gp).read()))
        cache, cap, k, nf = {}, None, 0, 0
        for name, h, bc, j, glsl in jobs:
            rnd = random.Random(int(h, 16) + (salts or {}).get(h, 0) * 0x9E3779B97F4A7C15)
            if j['stage'] == 'pixel':
                vbc, vgl = drv['drv3' if j['model'].startswith('ps_3') else 'drv2']
                elems, stride, verts, idx, vsc, psc, samp = ps_job(j, rnd)
                pair = ('VSH ', vbc, vgl), ('PSH ', bc, glsl)
                nv, nt = 4, 2
            else:
                job = vs_job(j, rnd, glsl)
                if job is None:
                    man['skipped'].append({'name': name, 'hash': h, 'why': 'no POSITION input'}); continue
                elems, stride, verts, idx, vsc = job
                cb, cg, nreg = combo_ps(j.get('outputs', []), j['model'], asm, sdp2, work, cache, glsl)
                psc = {c: [rnd.uniform(-1, 1) for _ in range(4)] for c in range(nreg + 1)}
                pair = ('VSH ', bc, glsl), ('PSH ', open(cb, 'rb').read(), open(cg).read())
                samp, nv, nt = {}, 9, 8
            if cap is None or nf == per:
                if cap: cap.close()
                cname = f'perm_{k:03d}'
                k += 1
                cap, nf = Capture(os.path.join(out, cname + '.nxd9'), size, tex), 0
                man['captures'][cname] = []
            vs = cap.shader(*pair[0])
            ps = cap.shader(*pair[1])
            d = cap.decl(elems)
            cap.buffers(verts, stride, idx)
            cap.frame(vs, ps, d, nv, nt, vsc, psc, samp)
            man['captures'][cname].append({'frame': '%05d' % nf, 'name': name, 'hash': h, 'salt': (salts or {}).get(h, 0), 'stage': j['stage'],
                                           'model': j['model'], 'samplers': [s['type'] for s in j.get('samplers', [])]})
            nf += 1
        if cap: cap.close()
    man['programs'] = sum(len(v) for v in man['captures'].values())
    json.dump(man, open(os.path.join(out, 'manifest.json'), 'w'), indent=1)
    return man


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build'])
    ap.add_argument('--sdp', required=True, action='append', help='a shader package (repeat for several)')
    ap.add_argument('--asm', required=True)
    ap.add_argument('--shard', help='k/N: only the programs whose hash %% N == k - 1')
    ap.add_argument('--sdp2dksh', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--per-capture', type=int, default=64); ap.add_argument('--size', type=int, default=96)
    ap.add_argument('--only'); ap.add_argument('--programs')
    ap.add_argument('--variant', default='base', choices=sorted(VARIANTS), help='parameter set (VARIANTS)')
    ap.add_argument('--salts', help='JSON {hash: salt}: another seed for those programs (permcover.py)')
    ap.add_argument('--perturb', type=float, default=0.0, help='scale every float input by 1 + eps * u (conditioning run)')
    a = ap.parse_args()
    only = set(a.only.split(',')) if a.only else None
    if a.programs:
        only = (only or set()) | {l.strip() for l in open(a.programs) if l.strip() and not l.startswith('#')}
    if a.shard:
        SHARD['k'], SHARD['n'] = map(int, a.shard.split('/'))
    m = build(a.sdp, a.asm, a.sdp2dksh, a.out, a.per_capture, a.size, only, a.perturb,
              json.load(open(a.salts)) if a.salts else None, a.variant)
    print(f"perm: {m['programs']} programs in {len(m['captures'])} captures, {len(m['skipped'])} skipped")
    return 0


if __name__ == '__main__':
    sys.exit(main())
