#!/usr/bin/env python3
"""publish_refs.py - reference images of a perm.py set, split by family group, for the render streams that have no
WineD3D (the D21 coverage gate per family until real captures cover them).

    publish_refs.py --sdp <pkg.sdp> --manifest <perm dir>/manifest.json --ref <reference run> --out <dir>
                    [--salts <salts.json>] [--group lit,land,veg,other] [--programs <list>] [--permreport <json>]

Writes <out>/<package stem>-<group>/:
  <hash>.png      the reference frame of that program (one per unique program; PNG re-saved losslessly)
  index.tsv       entry name, hash, stage, model, perm capture, frame, salt, class (permreport, when given)
  salts.json      the seeds used (perm.py --salts), so the captures can be rebuilt bit for bit
  SHA256SUMS      every file above
  README.md       provenance, how to rebuild the captures and compare
Groups by the entry's family (its name without digits and extension), so every entry of the package lands in
exactly one group:
  lit    SLS*, SM* (SM3, SM3LL), SKIN*, HAIR*
  land   TILE*, SKY*, WATER*, DISTLOD*, ISFFT*, ISWATER*
  veg    GRASS*, STB*, STLEAF*, STFROND*, DISTTREE*, PAR*, PARTICLE*, GDECAL*, PRECIP*
  other  everything else (image space, NOLIGHT, BOLT, BEAM, debug, ...)
--programs restricts to the names or hashes in a file (a requested list). Program names and images are the game's:
the output goes to the private repository only.
"""
import argparse, hashlib, json, os, re, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'verify', 'shaders'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'verify'))
import nxd9io

GROUPS = [('lit', ('SLS', 'SM', 'SKIN', 'HAIR')),
          ('land', ('TILE', 'SKY', 'WATER', 'DISTLOD', 'ISFFT', 'ISWATER')),
          ('veg', ('GRASS', 'STB', 'STLEAF', 'STFROND', 'DISTTREE', 'PAR', 'GDECAL', 'PRECIP'))]


def group_of(name):
    fam = re.sub(r'\d+\w*$', '', name.rsplit('.', 1)[0])
    for g, prefixes in GROUPS:
        if any(fam.startswith(p) for p in prefixes):
            return g
    return 'other'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sdp', required=True); ap.add_argument('--manifest', required=True)
    ap.add_argument('--ref', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--salts'); ap.add_argument('--group', default='lit,land,veg,other')
    ap.add_argument('--programs'); ap.add_argument('--permreport')
    a = ap.parse_args()
    from sdpfuzz import extract
    from PIL import Image
    names = {}
    for n, bc in extract(a.sdp).items():
        names.setdefault('%016x' % nxd9io.fnv1a64(bc), []).append(n)
    man = json.load(open(a.manifest))
    where = {e['hash']: (c, e) for c, lst in man['captures'].items() for e in lst}
    cls = {}
    if a.permreport:
        cls = {r['hash']: r['class'] for r in json.load(open(a.permreport))['rows']}
    want = None
    if a.programs:
        want = {l.strip() for l in open(a.programs) if l.strip() and not l.startswith('#')}
    stem = os.path.splitext(os.path.basename(a.sdp))[0].replace('shaderpackage', 'perm')
    groups = a.group.split(',')
    summary = {}
    for g in groups:
        d = os.path.join(a.out, f'{stem}-{g}')
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        rows, files, missing = [], [], []
        for h, ns in sorted(names.items()):
            for n in sorted(ns):
                if group_of(n) != g or (want is not None and n not in want and h not in want):
                    continue
                if h not in where:
                    missing.append((n, h, 'not in the perm set'))
                    continue
                cap, e = where[h]
                src = os.path.join(a.ref, cap, f"f{e['frame']}.png")
                if not os.path.isfile(src):
                    missing.append((n, h, 'no reference frame'))
                    continue
                if not os.path.isfile(os.path.join(d, h + '.png')):
                    Image.open(src).save(os.path.join(d, h + '.png'), optimize=True)
                    files.append(h + '.png')
                rows.append((n, h, e['stage'], e['model'], cap, e['frame'], str(e.get('salt', 0)), cls.get(h, '')))
        with open(os.path.join(d, 'index.tsv'), 'w') as f:
            f.write('name\thash\tstage\tmodel\tcapture\tframe\tsalt\tclass\n')
            for r in rows:
                f.write('\t'.join(r) + '\n')
            for n, h, why in missing:
                f.write(f'{n}\t{h}\t\t\t\t\t\tmissing: {why}\n')
        if a.salts:
            shutil.copy(a.salts, os.path.join(d, 'salts.json'))
            files.append('salts.json')
        files.append('index.tsv')
        uniq = len({r[1] for r in rows})
        open(os.path.join(d, 'README.md'), 'w').write(f"""# S16 references: {stem}-{g} (private, game-derived)

{len(rows)} entries, {uniq} unique programs, of {os.path.basename(a.sdp)}, group "{g}" (tools/compare/publish_refs.py).
Missing: {len(missing)} (listed in index.tsv with the reason).

Each <hash>.png is the reference frame of perm.py's capture for that program: one draw after a clear, 96x96,
rendered by nxd9play under Wine (WineD3D on llvmpipe), noise floor 0. Rebuild the captures bit for bit with
claude/fnv-s16's tools/compare/perm.py:

    perm.py build --sdp {os.path.basename(a.sdp)} --asm s6b_asm --sdp2dksh sdp2dksh --out caps --salts salts.json --only <hash,...>

then `nx_render d3d9 caps/perm_000.nxd9 -o out` and compare out/f<frame>.png with <hash>.png (index.tsv gives the
frame of each hash in the full set; with --only the frames follow the package's name order). The program's verdict in
S16's own run is the class column: pass, over (a difference with a well-conditioned reference), ill (the reference
moves under a 1e-5 input perturbation) or vacuous (nothing drawn: the reference is the clear colour).
Threshold (S16 decision): PSNR >= 40 dB, SSIM >= 0.98, FLIP <= 0.05, <= 0.5 % of pixels over 8/255.
""")
        files.append('README.md')
        with open(os.path.join(d, 'SHA256SUMS'), 'w') as f:
            for fn in sorted(files):
                f.write(hashlib.sha256(open(os.path.join(d, fn), 'rb').read()).hexdigest() + '  ' + fn + '\n')
        summary[g] = {'entries': len(rows), 'unique': uniq, 'missing': len(missing)}
    print('publish_refs:', json.dumps(summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())
