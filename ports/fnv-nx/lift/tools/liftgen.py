#!/usr/bin/env python3
"""The generator of the lifted pack: the lifter's output for the whole code universe as C++ SOURCE. No compiler runs here: the objects are built
only by verify/recipe.py from the product root lift/pack (build.json), whose `generated` stage copies the units out of the published pack tree.
    liftgen.py --shard K/N --out DIR [--jobs J]   lift the entries whose index (address order) mod N == K -> texts_K.txt, missing_K.tsv, forms_K.tsv, encs_K.tsv, shapes_K.tsv
    liftgen.py --finish --out DIR --exe EXE --modules MODULES_TSV --image DIR=UNIVERSE.tsv ...   (one --image per image: the exe and every row of the module table)
        merge the shards of every image of the process -> DIR/pack/: u/000.src .. u/fff.src (the source of the unit u/NNN.cpp: a tool-class root holds no C++ unit, lift/pack's packcopy stage names it), entries.tsv (lifted and runtime rows),
        forms.tsv, encs.tsv, shapes.tsv, gate_liftgen.json. Nothing derived from the loader's bridge table is written (imp.cpp, disp.cc, the import rows and
        the table_id, inc/fnvl_prelude.h and inc/imp.h are the generated stages of lift/pack: the runtime's headers are never in the pack).
    liftgen.py --module NAME ...                  (shard mode) a module of modules.tsv, lifted at its assigned base with its universe
RULE units: entry A lives in unit u/<(A >> 4) & 0xfff as 3 hex digits>.cpp, for every image. The 4096 unit names are fixed, so the recipe's
output list never changes, and a changed function text changes exactly one unit (the build's stage keys are content keys: every other
unit is a cache hit). A unit is `#include "fnvl_prelude.h"` + its entries in address order; an empty bucket is the include alone.
RULE completeness: --finish fails unless every universe entry of every image has a text (an entry the lifter refuses is a missing row).
Symbols the pack references in the runtime (bind-l3 import rows, the two EH handler entries) are not checked here: a symbol the runtime does
not define is an undefined symbol of the link, which the linkstrict gate refuses.
Deterministic: sorted iteration, no clocks or paths in the outputs. Inputs: explicit arguments only (--exe, --universe, --certs, ...); outputs only under --out
(exe-derived: they go to the private fnv-nx-build repository, never into this one)."""
import argparse, collections, glob, hashlib, json, multiprocessing as mp, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = sys.argv[sys.argv.index('--lift') + 1] if '--lift' in sys.argv else None      # the lift/ directory (fnvlift/, rt/, proof/): a declared input of the stage, always an argument (no default from this file's place)
for p in (HERE, LIFT):
    if p: sys.path.insert(0, p)
BATCH = 60
ENCS = 4            # encodings listed per form (encs.tsv): the distinct byte strings at the lowest addresses; the universe of the retired one-instruction harness
NBUCKET = 4096
ROOT = 'ports/fnv-nx/lift/pack'
G = {}
import lifted      # the one definition of the lifted set (the lift step and the census read it from here)


def bucket(a): return (a >> 4) & (NBUCKET - 1)


def unit_names(): return ['u/%03x.cpp' % i for i in range(NBUCKET)]
# The pack's external holds the bodies and what is derived from them alone. Everything derived from the loader's bridge table is a generated stage of lift/pack (recipe():
# impgen.py -> imp.cpp + the import rows, packentries.py -> entries.tsv with the table_id trailer, dispgen.py -> disp.cc): a change of the table rebuilds those stages, never the external.


def lift_one(a):
    """-> (text, (form counter, form rows, shape counter)) or (None, reason)"""
    from proof import shape
    import formcert
    if G.get('prov'):      # --prov: the text is the tag-OFF text; the tag-ON lift of the same entry must give the same bytes (checked here for every entry of the universe)
        from fnvlift import prov
        prov.ON = False
        t, ins = lifted.lift_with_instructions(a)
        if t is None: return None, ins
        prov.take(); prov.ON = True
        try: t_on, _ = lifted.lift_with_instructions(a)
        finally: prov.ON = False
        if t_on != t: raise SystemExit('liftgen --prov: the text of entry %08x differs with the tag on' % a)
    else:
        t, ins = lifted.lift_with_instructions(a)
        if t is None: return None, ins
    t0 = t
    t = re.sub(r'extern "C" void (L_%08x\(void\)\n\{)' % a, r'extern "C" __attribute__((section(".text.fnvl"))) void \1', t, count=1)
    assert '.text.fnvl' in t
    pv = prov_record(ins, t0, t) if G.get('prov') else None
    return t, (formcert.census(ins), [(formcert.form_key(i), bytes(i.bytes), x) for x, i in ins.items()],
               collections.Counter((shape.shape_key(i), shape.family(i)) for i in ins.values()), pv)


def prov_record(ins, t0, t):
    """the provenance record of the function just lifted (fnvlift/prov.py), with the section attribute liftgen adds and the bytes per form key / shape family"""
    from fnvlift import prov
    from proof import shape
    import formcert
    r = prov.take()
    if r is None or r['text'] != len(t0): raise SystemExit('liftgen --prov: no provenance record for the text just lifted')
    r['cats']['liftgen | section attribute (.text.fnvl)'] = [len(t) - len(t0), 0]
    assert sum(v[0] for v in r['cats'].values()) == len(t)
    forms, fams = collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])
    for va, nb in r['insb'].items():
        i = ins[va]; f = forms[formcert.form_key(i)]; f[0] += nb; f[1] += 1
        g = fams[shape.family(i)]; g[0] += nb; g[1] += 1
    return dict(cats=r['cats'], forms=dict(forms), fams=dict(fams), n=r['n'], text=len(t))


def enc_add(encs, rows):
    """encs: form -> {encoding bytes: lowest address}, cut to the ENCS distinct encodings of the lowest addresses. The cut commutes with the
    union: the ENCS lowest of a union are among the ENCS lowest of each part."""
    for f, r, x in rows:
        e = encs.setdefault(f, {}); e[r] = min(x, e.get(r, x))
    for f, e in encs.items():
        if len(e) > ENCS: encs[f] = dict(sorted(e.items(), key=lambda t: (t[1], t[0]))[:ENCS])
    return encs


def work(addrs):
    ent = G['ent']; texts, forms, missing, encs, shapes = [], collections.Counter(), [], {}, collections.Counter(); pvs = []
    for a in addrs:
        t, x = lift_one(a)
        if t is None: missing.append((a, ent[a][0], x))
        else: texts.append((a, t)); forms.update(x[0]); enc_add(encs, x[1]); shapes.update(x[2]); pvs.append(x[3])
    return texts, dict(forms), missing, [(f, r, x) for f, e in encs.items() for r, x in e.items()], dict(shapes), pvs


def text_records(texts):
    """the bytes of texts_K.txt, record by record (the one definition: write_texts writes them, texts_digest hashes them)"""
    for a, t in sorted(texts):
        b = t.encode()
        if not b.endswith(b'\n'): b += b'\n'
        yield b'//@ %08x %d\n' % (a, len(b)); yield b


def write_texts(path, texts):
    with open(path, 'wb') as f:
        for r in text_records(texts): f.write(r)


def texts_digest(texts):
    h = hashlib.sha256()
    for r in text_records(texts): h.update(r)
    return h.hexdigest()


def read_texts(path):
    out = {}
    with open(path, 'rb') as f:
        while True:
            h = f.readline()
            if not h: return out
            m = re.fullmatch(rb'//@ ([0-9a-f]{8}) (\d+)\n', h)
            if not m: sys.exit('%s: bad record header %r' % (path, h[:40]))
            a = int(m[1], 16)
            if a in out: sys.exit('%s: entry %08x twice' % (path, a))
            out[a] = f.read(int(m[2]))


def init(exe, base, ent, emit, jtab, pv=False):
    """the state of a lifting process (the parent and every pool worker), built from the arguments: the result does not depend on the start method"""
    import fnvlift as L
    G.update(ent=ent, prov=pv)
    lifted.begin(L.Image(exe, base), emit, jtab)


def shard(a):
    import fnvlift as L, fnvlift.core as core, formcert
    K, N = map(int, a.shard.split('/')); out = a.out
    ent = L.universe.load()[0]; mine = [x for x in sorted(ent) if bucket(x) % N == K]      # shard K of N: the entries whose unit bucket is K mod N (the rule of the pack's units)
    init(a.exe, a.base, ent, a.emit, a.jtab, a.prov)
    with mp.Pool(a.jobs, initializer=init, initargs=(a.exe, a.base, ent, a.emit, a.jtab, a.prov)) as p: got = list(p.imap(work, [mine[j:j + BATCH] for j in range(0, len(mine), BATCH)], 1))
    texts = [t for g in got for t in g[0]]; miss = sorted(m for g in got for m in g[2])
    forms = collections.Counter()
    for g in got: forms.update(g[1])
    encs = {}
    for g in got: enc_add(encs, g[3])
    shapes = collections.Counter()
    for g in got: shapes.update(g[4])
    if a.prov:      # the measurement: the side table of the emitted bytes and the digest of the texts (= sha256 of texts_K.txt of the same shard without --prov); no other output
        write_prov('%s/prov_%d.json' % (out, K), [p for g in got for p in g[5]], dict(shard=a.shard, entries=len(mine), lifted=len(texts), missing=len(miss), texts_sha256=texts_digest(texts)))
        print('shard %d/%d (prov): entries %d lifted %d missing %d; text bytes %d' % (K, N, len(mine), len(texts), len(miss), sum(len(t) for _, t in texts)))
        return
    certs = formcert.Certs(a.certs)
    json.dump(inputs(a), open('%s/inputs_%d.json' % (out, K), 'w'), sort_keys=True)      # what this shard generated from (finish checks that every shard of an image agrees)
    write_texts('%s/texts_%d.txt' % (out, K), texts)
    w = lambda n, rows: open('%s/%s_%d.tsv' % (out, n, K), 'w').write(''.join('\t'.join(map(str, x)) + '\n' for x in rows))
    w('missing', [('%08x' % ad, k, why) for ad, k, why in miss])
    w('forms', [(f, n, certs.status(f)) for f, n in sorted(forms.items())])
    w('encs', [(f, r.hex(), '%08x' % x) for f, e in sorted(encs.items()) for r, x in sorted(e.items(), key=lambda t: (t[1], t[0]))])
    w('shapes', [(k, fam, n) for (k, fam), n in sorted(shapes.items(), key=lambda t: (t[0][0].encode(), t[0][1]))])    # shape_key, family, instruction count (L3b formgate)
    print('shard %d/%d: entries %d lifted %d missing %d; text bytes %d; forms %d' % (K, N, len(mine), len(texts), len(miss), sum(len(t) for _, t in texts), len(forms)))


def write_prov(path, pvs, extra):
    """--prov: the provenance side table of this shard (sums over its functions; fnvlift/prov.py categories, bytes per form key and per shape family). The texts are unchanged."""
    cats, forms, fams = collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])
    n = text = 0
    for p in pvs:
        for dst, src in ((cats, p['cats']), (forms, p['forms']), (fams, p['fams'])):
            for k, v in src.items(): dst[k][0] += v[0]; dst[k][1] += v[1]
        n += p['n']; text += p['text']
    if sum(v[0] for v in cats.values()) != text: sys.exit('liftgen --prov: the categories do not add up to the text bytes')
    json.dump(dict(format='fnvnx-lift-prov/1', functions=len(pvs), insns=n, text_bytes=text, cats=cats, forms=forms, fams=fams, **extra), open(path, 'w'), sort_keys=True)


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest() if path else None


def lifter_tree():
    """sha256 over (path, content) of every file the generated text is a function of: the lifter package, the runtime headers it inlines and the generator tools"""
    if LIFT is None: raise SystemExit('liftgen: --lift DIR (the declared lifter tree) is required')
    fs = [(os.path.relpath(f, LIFT), f) for f in glob.glob(LIFT + '/fnvlift/*.py') + glob.glob(LIFT + '/fnvlift/*/*.py') + glob.glob(LIFT + '/rt/*.h') + [LIFT + '/proof/__init__.py', LIFT + '/proof/shape.py']]
    fs += [('tools/%s.py' % n, os.path.join(HERE, n + '.py')) for n in ('liftgen', 'impgen', 'dispgen', 'formcert')]      # names, not paths: the same under a stage (/tool) and in a checkout
    h = hashlib.sha256()
    for k, f in sorted(fs): h.update(k.encode() + b'\0' + hashlib.sha256(open(f, 'rb').read()).digest())
    return h.hexdigest()


def inputs(a):
    """the generation inputs of a shard: the exe, the universe, the jump-table facts, the emit mode and the lifter; a change of any of them changes the pack manifest and so the external's tree"""
    return dict(exe_sha256=sha(a.exe), universe_sha256=sha(a.universe), jtab_sha256=sha(a.jtab), emit=a.emit, lifter_tree_sha256=lifter_tree(),
                base=('%08x' % a.base if a.base is not None else None))      # base: the loader's assigned base of a module (modules.tsv row); None for the exe


def image_inputs(d):
    """the one inputs record of the shards of image directory d (every shard must agree)"""
    rs = [json.load(open(f)) for f in sorted(glob.glob(d + '/inputs_*.json'))]
    if not rs or any(r != rs[0] for r in rs): sys.exit('finish: %s: shards missing their inputs record or generated from different inputs' % d)
    return rs[0]


def tsv(f): return [l.rstrip('\n').split('\t') for l in open(f)]


def image(d, ent):
    """The texts of one image from its shard directory; fails unless they are exactly the universe."""
    texts, miss = {}, []
    for f in sorted(glob.glob(d + '/texts_*.txt')):
        t = read_texts(f)
        if set(t) & set(texts): sys.exit('finish: %s: an entry is in two shards' % d)
        texts.update(t)
    for f in sorted(glob.glob(d + '/missing_*.tsv')): miss += tsv(f)
    if set(texts) != set(ent):
        sys.exit('finish: %s: %d universe entries, %d texts, %d missing rows: the pack is not complete' % (d, len(ent), len(texts), len(miss)))
    return texts


def module_table(path):
    """-> (sha256 of the loader's modules.tsv, {lower-case file name: row}) : the ONE source of the module set and of every base (contract/modules.py output; tools/pemod.modules)"""
    import pemod
    return sha(path), pemod.modules(path)


def check_images(images, table, exe_sha):
    """RULE one module table: the image set of the pack is the exe plus EVERY row of the loader's table, nothing else; each image was lifted from the file the row pins (sha256) at the row's
    assigned base, and every entry of its universe lies inside the row's address range [base, base + size_of_image). The pack takes no module set and no base of its own."""
    by_sha = {r.sha256: r for r in table.values()}; seen, bad = {}, []
    for d, ent in images:
        rec = image_inputs(d); r = by_sha.get(rec['exe_sha256'])
        if rec['exe_sha256'] == exe_sha:
            if rec.get('base') is not None: bad.append('%s: the exe image carries a base %s' % (d, rec['base']))
            continue
        if r is None: bad.append('%s: its file (sha256 %s) is not a row of the module table' % (d, rec['exe_sha256'])); continue
        if r.name in seen: bad.append('%s: the image of %s is given twice (%s)' % (d, r.name, seen[r.name])); continue
        seen[r.name] = d
        if rec.get('base') is None or int(rec['base'], 16) != r.base: bad.append('%s: %s lifted at base %s, the table assigns %08x' % (d, r.name, rec.get('base'), r.base))
        out = sorted(x for x in ent if not r.base <= x < r.base + r.sizeimg)
        if out: bad.append('%s: %d universe entries outside [%08x, %08x) of %s (first %08x)' % (d, len(out), r.base, r.base + r.sizeimg, r.name, out[0]))
    bad += ['%s: a row of the module table with no image in the pack' % n for n in sorted(set(r.name for r in table.values()) - set(seen))]
    if bad: sys.exit('finish: the images do not agree with the module table:\n  ' + '\n  '.join(bad))


def finish(a):
    import fnvlift as L
    out = a.out; pk = out + '/pack'
    os.makedirs(pk + '/u', exist_ok=True)
    images = [(d, L.universe.load(u)[0]) for d, u in (x.split('=', 1) for x in a.image)]      # every image: its shard directory and its universe (the exe's included)
    table_sha, table = module_table(a.modules)
    check_images(images, table, sha(a.exe))
    texts, forms, encs, shapes, shape_fam = {}, collections.defaultdict(lambda: [0, 'none']), {}, collections.Counter(), {}
    for d, ent in images:
        t = image(d, ent)
        if set(t) & set(texts): sys.exit('finish: %s shares entry addresses with another image' % d)
        texts.update(t)
        for f in sorted(glob.glob(d + '/forms_*.tsv')):
            for form, n, s in tsv(f): forms[form][0] += int(n); forms[form][1] = s
            e = os.path.join(os.path.dirname(f), 'encs_' + os.path.basename(f)[len('forms_'):])
            if not os.path.isfile(e): sys.exit('finish: %s: no encodings list beside %s' % (d, os.path.basename(f)))
            enc_add(encs, [(form, bytes.fromhex(r), int(x, 16)) for form, r, x in tsv(e)])
        for f in sorted(glob.glob(d + '/texts_*.txt')):      # completeness: every shard that has texts has its shapes
            sf = os.path.join(os.path.dirname(f), 'shapes_' + os.path.basename(f)[len('texts_'):-len('.txt')] + '.tsv')
            if not os.path.isfile(sf): sys.exit('finish: %s: no shapes list beside %s' % (d, os.path.basename(f)))
            for k, fam, n in tsv(sf):
                if shape_fam.setdefault(k, fam) != fam: sys.exit('finish: shape %s has two families (%s, %s)' % (k, shape_fam[k], fam))
                shapes[k] += int(n)
    if set(encs) != set(forms): sys.exit('finish: the encodings lists and the form lists name different forms')
    units = collections.defaultdict(list)
    for x in sorted(texts): units[bucket(x)].append(texts[x])
    for i in range(NBUCKET):
        with open('%s/u/%03x.src' % (pk, i), 'wb') as f: f.write(b'#include "fnvl_prelude.h"\n' + b''.join(units[i]))
    syms = {x: 'L_%08x' % x for x in texts}
    kind = {x: 'code' for x in syms}                      # the kind of an entry is the producer set its key came from: the keys of texts (the import rows, the runtime entries of the FNVNX_EXPORT records included, are the pack's impgen stage)
    w = lambda n, rows: open('%s/%s.tsv' % (pk, n), 'w').write(''.join('\t'.join(map(str, x)) + '\n' for x in rows))
    w('entries', [('%08x' % x, sy, kind[x]) for x, sy in sorted(syms.items())])      # lifted and runtime rows
    open(pk + '/entries.tsv', 'a').write('# module_table sha256 %s\n' % table_sha)      # the loader's modules.tsv this pack was lifted against (the stage packentries.py checks it)
    w('shapes', [(k, shape_fam[k], n) for k, n in sorted(shapes.items(), key=lambda t: t[0].encode())])      # the universe of formgate: exactly what was lifted
    w('forms', [(f, v[0], v[1]) for f, v in sorted(forms.items())])
    w('encs', [(f, r.hex(), '%08x' % x) for f, e in sorted(encs.items()) for r, x in sorted(e.items(), key=lambda t: (t[1], t[0]))])
    nc = sum(1 for v in forms.values() if v[1] == 'sail')
    res = dict(schema='fnvnx-gate/1', name='l3.liftgen', status='green', images=len(images), universe=[len(e) for _, e in images], entries=len(texts), units=NBUCKET,
               text_bytes=sum(len(t) for t in texts.values()), forms=len(forms), forms_certified=nc, build='measurement: forms without a certificate %d of %d' % (len(forms) - nc, len(forms)))
    json.dump(res, open(pk + '/gate_liftgen.json', 'w'), sort_keys=True)
    json.dump(dict(format='fnvnx-liftpack-manifest/1', images=[image_inputs(d) for d, _ in images]), open(pk + '/manifest.json', 'w'), sort_keys=True, indent=1)      # generation inputs: part of the external's tree
    print('finish: images %d, entries %d, units %d, text bytes %d, largest unit %d bytes; forms %d (certified %d)' % (
        len(images), len(texts), NBUCKET, res['text_bytes'], max(sum(map(len, u)) for u in units.values()), len(forms), nc))


def module_inputs(a):
    """--module NAME: the module's file, its assigned load base (modules.tsv row) and its universe (tools/universe.py --module output below --facts-out), all from declared inputs"""
    import pemod
    path, row = pemod.locate(a.module, a.modules, a.moddirs.split(':'))
    u = '%s/%s/universe.tsv' % (a.facts_out, os.path.splitext(row.name)[0])
    if not os.path.exists(u): sys.exit('--module %s: no universe %s (tools/universe.py --module)' % (a.module, u))
    j = '%s/%s/ehtables.json' % (a.facts_out, os.path.splitext(row.name)[0])
    if not os.path.exists(j): sys.exit('--module %s: no jump-table facts %s (tools/ehtables.py)' % (a.module, j))
    a.exe, a.base, a.universe, a.jtab = path, row.base, u, j


def main():
    ap = argparse.ArgumentParser(description='Every input is an explicit argument: no environment variable, no default path.'); A = ap.add_argument
    A('--shard'); A('--finish', action='store_true'); A('--out'); A('--jobs', type=int, default=3)
    A('--exe', help='the exe (or, with --module, ignored: the module file is located in --moddirs)'); A('--universe', help='universe.tsv of the image (with --module: derived below --facts-out)')
    A('--module'); A('--modules', help='modules.tsv (the loader module table)'); A('--moddirs', help='DIR[:DIR...] that hold the module files'); A('--facts-out', help='where tools/universe.py --module wrote the module facts')
    A('--prov', action='store_true', help='shard mode: write ONLY prov_K.json, the provenance side table of the emitted bytes (fnvlift/prov.py) and the digest of the texts; every entry is lifted with the tag off and on and the two texts must be equal')
    A('--emit', default='dsl', choices=['dsl'], help='dsl: integer instructions from the proven DSL forms (fnvlift/f_dsl.py) before the hand handlers')
    A('--jtab', default=None, help='ehtables.json of the module (tools/ehtables.py facts, `jtab_tables`)'); A('--l3', default=None)
    A('--lift', help='the lift/ directory (fnvlift/, rt/) when it is not the parent of this tool (a recipe stage mounts it as a declared input)')
    A('--image', action='append', default=[], metavar='DIR=UNIVERSE.tsv', help='finish: one image (exe and every module row): its shard directory and its universe')
    A('--fpstate', help='directory of the fp-state facts (invariants.tsv)')
    A('--certs', help='the directory of the form certificates (proof/prove.py output; shard mode)'); A('--fpdepth', help='the x87 stack-depth facts (depth.tsv)'); A('--sdmrows', help='the SDM opcode rows (sdmfeat.py dumprows JSON)'); A('--cpumodel', help='the declared CPU data (verify/cpuid_model.json)'); A('--rcptab', help='rt/rcptab.c (the certified table source)'); A('--rcpcert', help='rt/rcptab.cert.json'); A('--udsail', help='the Sail #UD verdicts (proof/udsail.py output): read only when a refused sequence or a form with no SDM row is met'); A('--segwrites', help='the per-image write sites of the data segment registers (lift/facts SEGWRITES)'); A('--x86sys', help='the declared x86 system record (platform X86SYS output)')
    a = ap.parse_args()
    need = ['out'] + (['exe', 'modules', 'image'] if a.finish else ['fpstate', 'fpdepth', 'x86sys', 'sdmrows', 'cpumodel', 'rcptab', 'rcpcert', 'segwrites', 'certs'] + (['modules', 'moddirs', 'facts_out', 'module'] if a.module else ['exe', 'universe']))      # --finish merges texts and reads no lifter input but the universe files
    miss = ['--' + k.replace('_', '-') for k in need if getattr(a, k) is None]
    if miss: ap.error('missing %s' % ' '.join(miss))
    a.base = None
    if a.module: module_inputs(a)
    import fnvlift
    if not a.finish: fnvlift.inputs.configure(universe=a.universe, fpstate=a.fpstate, fpdepth=a.fpdepth, x86sys=a.x86sys, sdmrows=a.sdmrows, cpumodel=a.cpumodel, rcptab=a.rcptab, rcpcert=a.rcpcert, segwrites=a.segwrites, **({'udsail': a.udsail} if a.udsail else {}), image=os.path.splitext(a.module)[0].lower() if a.module else 'exe')      # finish merges texts: it reads no lifter input
    os.makedirs(a.out, exist_ok=True)
    if a.finish: finish(a)
    elif a.shard: shard(a)
    else: ap.error('--shard K/N or --finish')


if __name__ == '__main__': main()
