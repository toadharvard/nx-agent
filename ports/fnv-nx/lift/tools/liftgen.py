#!/usr/bin/env python3
"""The generator of the lifted pack: the lifter's output for the whole code universe as C++ SOURCE. No compiler runs here: the objects are built
only by verify/recipe.py from the product root lift/pack (build.json), whose `generated` stage copies the units out of the published pack tree.
    liftgen.py --shard K/N --out DIR [--jobs J]   lift the entries whose index (address order) mod N == K -> texts_K.txt, missing_K.tsv, forms_K.tsv, encs_K.tsv, shapes_K.tsv
    liftgen.py --finish --out DIR [--also MODDIR=UNIVERSE.tsv ...] --bridge bridge.tsv [--need iat.tsv ...]
        merge the shards of every image of the process -> DIR/pack/: u/000.cpp .. u/fff.cpp, disp.S, imp.cpp, inc/fnvl_prelude.h, inc/imp.h,
        entries.tsv, forms.tsv, encs.tsv, shapes.tsv, imp_entries.tsv, imp_blocked.tsv, gate_liftgen.json
    liftgen.py --recipe REF COMMIT TREE_SHA256    write lift/pack/build.json for the published pack tree (fnv-nx-build branch, commit, cas.py ingest hash)
    liftgen.py --module NAME ...                  (shard mode) a module of modules.tsv, lifted at its assigned base with its universe
RULE units: entry A lives in unit u/<(A >> 4) & 0xfff as 3 hex digits>.cpp, for every image. The 4096 unit names are fixed, so the recipe's
output list never changes, and a changed function text changes exactly one unit (the build's stage keys are content keys: every other
unit is a cache hit). A unit is `#include "fnvl_prelude.h"` + its entries in address order; an empty bucket is the include alone.
RULE completeness: --finish fails unless every universe entry of every image has a text (an entry the lifter refuses is a missing row).
Symbols the pack references in the runtime (bind-l3 import rows, the two EH handler entries) are not checked here: a symbol the runtime does
not define is an undefined symbol of the link, which the linkstrict gate refuses.
Deterministic: sorted iteration, no clocks or paths in the outputs. Inputs FNVNX_EXE, FNVNX_UNIVERSE, FNVNX_PROOF; outputs only under --out
(exe-derived: they go to the private fnv-nx-build repository, never into this one)."""
import argparse, collections, glob, json, multiprocessing as mp, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(HERE)
for p in ('tools', ''):
    sys.path.insert(0, os.path.join(LIFT, p))
BATCH = 60
ENCS = 4            # encodings listed per form (encs.tsv): the distinct byte strings at the lowest addresses; the universe of test/instest.py
NBUCKET = 4096
# THE flags of lifted C++ (one definition: the pack recipe below and test/instest.py use it). No flag that turns an error into a warning or silences one.
PACK_CXX_FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fms-extensions', '-fshort-wchar', '-frounding-math', '-fsigned-char', '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks', '-fcheck-new', '-fno-sized-deallocation', '-fno-lifetime-dse', '-ffp-contract=off', '-fPIC', '-march=armv8-a+crc+crypto', '-mtune=cortex-a57', '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics', '-U_FORTIFY_SOURCE', '-ffreestanding', '-fno-exceptions', '-fno-asynchronous-unwind-tables', '-fno-math-errno', '-fno-jump-tables', '-fno-ipa-ra', '-ffixed-x19', '-ffixed-x20', '-ffixed-x21', '-ffixed-x22', '-ffixed-x23', '-ffixed-x24', '-ffixed-x25', '-ffixed-x26', '-ffixed-x27', '-Werror=attributes']
PACK_C_FLAGS = ['-mabi=ilp32', '-Werror=attributes']
ROOT = 'ports/fnv-nx/lift/pack'
EH_KEYS = ((0x000fffd0, 'fnvl_msvcrt_eh_nested_catch'), (0x000fffe0, 'fnvl_msvcrt_eh_nested_collide'))    # rt/msvcrt.h FNVL_EH_KEY_*: the two handlers the msvcrt EH runtime registers on fs:[0]
G = {}


def bucket(a): return (a >> 4) & (NBUCKET - 1)


def unit_names(): return ['u/%03x.cpp' % i for i in range(NBUCKET)] + ['disp.S', 'imp.cpp']


def lift_one(a):
    """-> (text, (form counter, form rows, shape counter)) or (None, reason)"""
    import fnvlift as L
    from proof import shape
    import formcert
    G['cap'].clear()
    try:
        t, m = L.lift_entry(G['img'], a)
    except L.Unsupported as e:
        return None, str(e)
    t = re.sub(r'extern "C" void (L_%08x\(void\)\n\{)' % a, r'extern "C" __attribute__((section(".text.fnvl"))) void \1', t, count=1)
    assert '.text.fnvl' in t
    ins = G['cap'][-1][0]
    return t, (formcert.census(ins), [(formcert.form_key(i), bytes(i.bytes), x) for x, i in ins.items()],
               collections.Counter((shape.shape_key(i), shape.family(i)) for i in ins.values()))


def enc_add(encs, rows):
    """encs: form -> {encoding bytes: lowest address}, cut to the ENCS distinct encodings of the lowest addresses. The cut commutes with the
    union: the ENCS lowest of a union are among the ENCS lowest of each part."""
    for f, r, x in rows:
        e = encs.setdefault(f, {}); e[r] = min(x, e.get(r, x))
    for f, e in encs.items():
        if len(e) > ENCS: encs[f] = dict(sorted(e.items(), key=lambda t: (t[1], t[0]))[:ENCS])
    return encs


def work(addrs):
    ent = G['ent']; texts, forms, missing, encs, shapes = [], collections.Counter(), [], {}, collections.Counter()
    for a in addrs:
        t, x = lift_one(a)
        if t is None: missing.append((a, ent[a][0], x))
        else: texts.append((a, t)); forms.update(x[0]); enc_add(encs, x[1]); shapes.update(x[2])
    return texts, dict(forms), missing, [(f, r, x) for f, e in encs.items() for r, x in e.items()], dict(shapes)


def write_texts(path, texts):
    with open(path, 'wb') as f:
        for a, t in sorted(texts):
            b = t.encode()
            if not b.endswith(b'\n'): b += b'\n'
            f.write(b'//@ %08x %d\n' % (a, len(b))); f.write(b)


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


def init(exe, ent, emit):
    """the state of a lifting process (the parent and every pool worker), built from the arguments: the result does not depend on the start method"""
    import fnvlift as L, fnvlift.core as core
    core.set_emit(emit)
    G.update(ent=ent, img=L.Image(exe), cap=[])
    if 'orig_explore' not in G:         # once per process: the capture wrapper around core.explore
        G['orig_explore'] = orig = core.explore
        def cap(*x, **k): r = orig(*x, **k); G['cap'].append(r); return r
        core.explore = cap


def shard(a):
    import fnvlift as L, fnvlift.core as core, formcert
    K, N = map(int, a.shard.split('/')); out = a.out
    ent = L.universe.load()[0]; mine = sorted(ent)[K::N]
    init(a.exe, ent, a.emit)
    with mp.Pool(a.jobs, initializer=init, initargs=(a.exe, ent, a.emit)) as p: got = list(p.imap(work, [mine[j:j + BATCH] for j in range(0, len(mine), BATCH)], 1))
    texts = [t for g in got for t in g[0]]; miss = sorted(m for g in got for m in g[2])
    forms = collections.Counter()
    for g in got: forms.update(g[1])
    encs = {}
    for g in got: enc_add(encs, g[3])
    shapes = collections.Counter()
    for g in got: shapes.update(g[4])
    certs = formcert.Certs()
    write_texts('%s/texts_%d.txt' % (out, K), texts)
    w = lambda n, rows: open('%s/%s_%d.tsv' % (out, n, K), 'w').write(''.join('\t'.join(map(str, x)) + '\n' for x in rows))
    w('missing', [('%08x' % ad, k, why) for ad, k, why in miss])
    w('forms', [(f, n, certs.status(f)) for f, n in sorted(forms.items())])
    w('encs', [(f, r.hex(), '%08x' % x) for f, e in sorted(encs.items()) for r, x in sorted(e.items(), key=lambda t: (t[1], t[0]))])
    w('shapes', [(k, fam, n) for (k, fam), n in sorted(shapes.items(), key=lambda t: (t[0][0].encode(), t[0][1]))])    # shape_key, family, instruction count (L3b formgate)
    print('shard %d/%d: entries %d lifted %d missing %d; text bytes %d; forms %d' % (K, N, len(mine), len(texts), len(miss), sum(len(t) for _, t in texts), len(forms)))


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


def finish(a):
    import dispgen, fnvlift as L, impgen
    out = a.out; pk = out + '/pack'
    for d in (pk + '/u', pk + '/inc'): os.makedirs(d, exist_ok=True)
    images = [(out, L.universe.load()[0])] + [(s.split('=', 1)[0], L.universe.load(s.split('=', 1)[1])[0]) for s in a.also]
    texts, forms, encs, shapes, shape_fam = {}, collections.defaultdict(lambda: [0, 'none']), {}, collections.Counter(), {}
    for d, ent in images:
        t = image(d, ent)
        if set(t) & set(texts): sys.exit('finish: %s shares entry addresses with another image' % d)
        texts.update(t)
        for f in sorted(glob.glob(d + '/forms_*.tsv')):
            for form, n, s in tsv(f): forms[form][0] += int(n); forms[form][1] = s
            e = f.replace('/forms_', '/encs_')
            if not os.path.isfile(e): sys.exit('finish: %s: no encodings list beside %s' % (d, os.path.basename(f)))
            enc_add(encs, [(form, bytes.fromhex(r), int(x, 16)) for form, r, x in tsv(e)])
        for f in sorted(glob.glob(d + '/texts_*.txt')):      # completeness: every shard that has texts has its shapes
            sf = f.replace('/texts_', '/shapes_').replace('.txt', '.tsv')
            if not os.path.isfile(sf): sys.exit('finish: %s: no shapes list beside %s' % (d, os.path.basename(f)))
            for k, fam, n in tsv(sf):
                if shape_fam.setdefault(k, fam) != fam: sys.exit('finish: shape %s has two families (%s, %s)' % (k, shape_fam[k], fam))
                shapes[k] += int(n)
    if set(encs) != set(forms): sys.exit('finish: the encodings lists and the form lists name different forms')
    units = collections.defaultdict(list)
    for x in sorted(texts): units[bucket(x)].append(texts[x])
    for i in range(NBUCKET):
        with open('%s/u/%03x.cpp' % (pk, i), 'wb') as f: f.write(b'#include "fnvl_prelude.h"\n' + b''.join(units[i]))
    open(pk + '/inc/fnvl_prelude.h', 'w', newline='').write('#ifndef FNVL_PRELUDE_H\n#define FNVL_PRELUDE_H\n' + L.prelude(os.path.join(LIFT, 'rt')) + '\n#endif\n')
    open(pk + '/inc/imp.h', 'w', newline='').write(L.inline(os.path.join(LIFT, 'rt'), 'imp.h'))
    syms = {x: 'L_%08x' % x for x in texts}; code = set(syms)
    imp, blocked, hit, absent = impgen.generate(a.bridge, pk, a.need, a.l3, None)
    if hit or absent: sys.exit('finish: an import row a loaded image needs has no entry (imp_blocked.tsv)')
    t = open(pk + '/imp.cpp').read()
    assert t.startswith('#include "fnvl.h"\n#include "imp.h"\n')
    open(pk + '/imp.cpp', 'w').write('#include "fnvl_prelude.h"\n' + t.split('\n', 1)[1])
    assert not set(k for k, *_ in imp) & code
    syms.update({k: s for k, s, *_ in imp})
    for k, s in EH_KEYS:
        assert k not in syms
        syms[k] = s
    w = lambda n, rows: open('%s/%s.tsv' % (pk, n), 'w').write(''.join('\t'.join(map(str, x)) + '\n' for x in rows))
    rows = [('%08x' % x, s, 'code' if x in code else 'import' if s.startswith('I_') or x >= 0x00100000 else 'runtime') for x, s in sorted(syms.items())]
    w('entries', rows)
    open(pk + '/entries.tsv', 'a').write(open(pk + '/imp_entries.tsv').readline())      # `# table_id <sha256 of bridge.tsv>` (last line)
    asm, h = dispgen.table(rows)      # the one dispatcher generator (tools/dispgen.py): disp.S is a pure function of the entry rows
    open(pk + '/disp.S', 'w').write(asm)
    w('shapes', [(k, shape_fam[k], n) for k, n in sorted(shapes.items(), key=lambda t: t[0].encode())])      # the universe of formgate: exactly what was lifted
    w('forms', [(f, v[0], v[1]) for f, v in sorted(forms.items())])
    w('encs', [(f, r.hex(), '%08x' % x) for f, e in sorted(encs.items()) for r, x in sorted(e.items(), key=lambda t: (t[1], t[0]))])
    nc = sum(1 for v in forms.values() if v[1] == 'sail')
    res = dict(schema='fnvnx-gate/1', name='l3.liftgen', status='green', images=len(images), universe=[len(e) for _, e in images], entries=len(texts), imports=len(imp), units=NBUCKET + 2,
               text_bytes=sum(len(t) for t in texts.values()), forms=len(forms), forms_certified=nc, build='measurement: forms without a certificate %d of %d' % (len(forms) - nc, len(forms)))
    json.dump(res, open(pk + '/gate_liftgen.json', 'w'), sort_keys=True)
    print('finish: images %d, entries %d, imports %d, units %d, text bytes %d, largest unit %d bytes; forms %d (certified %d); dispatcher %d slots, %d hash attempts' % (
        len(images), len(texts), len(imp), NBUCKET + 2, res['text_bytes'], max(sum(map(len, u)) for u in units.values()), len(forms), nc, len(h['slots']), h['attempts']))


def recipe(ref, commit, tree):
    """lift/pack/build.json: the flags are PACK_CXX_FLAGS (one source; test/instest.py imports them), the units are the fixed names."""
    gc = json.load(open(os.path.join(LIFT, '..', 'verify', 'guest_compiler.json')))   # the one source of the guest compiler (msvcmodel)
    rec = {'format': 'fnvnx-recipe/1', 'compiler': {'external': gc['external']['name'], 'cc': gc['cc'], 'cxx': gc['cxx']},
           'flags': {'c': list(PACK_C_FLAGS), 'cxx': list(PACK_CXX_FLAGS), 'asm': ['-mabi=ilp32', '-fPIC']},
           'include': ['/ext/liftpack/inc'], 'units': 'all',
           'generated': [{'tool': 'ports/fnv-nx/lift/tools/packcopy.py', 'args': ['/ext/liftpack', '/out'], 'inputs': [], 'outputs': unit_names()}],
           'link': None,
           'external': [{'name': 'liftpack', 'repo': 'fnv-nx-build', 'ref': ref, 'commit': commit, 'tree_sha256': tree, 'at': '/ext/liftpack'}, gc['external']]}
    os.makedirs(os.path.join(LIFT, 'pack'), exist_ok=True)
    with open(os.path.join(LIFT, 'pack', 'build.json'), 'w') as f:
        json.dump(rec, f, indent=1); f.write('\n')


def module_env(name):
    import pemod
    path, row = pemod.locate(name); u = '%s/%s/universe.tsv' % (os.environ.get('FNVNX_FACTS_OUT', '/home/user/l3facts2'), os.path.splitext(row.name)[0])
    if not os.path.exists(u): sys.exit('--module %s: no universe %s (tools/universe.py --module)' % (name, u))
    os.environ.update(FNVNX_EXE=path, FNVNX_BASE='%08x' % row.base, FNVNX_UNIVERSE=u)
    return path


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--shard'); A('--finish', action='store_true'); A('--recipe', nargs=3, metavar=('REF', 'COMMIT', 'TREE_SHA256')); A('--out', default='/home/user/l3work/liftgen'); A('--jobs', type=int, default=3)
    A('--exe', default=os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe')); A('--module'); A('--emit', default='', choices=['', 'dsl'], help='dsl: integer instructions from the proven DSL forms (fnvlift/f_dsl.py) before the hand handlers')
    A('--bridge', default=os.environ.get('FNVNX_BRIDGE')); A('--need', action='append', default=[]); A('--l3', default=None); A('--also', action='append', default=[], metavar='DIR=UNIVERSE.tsv')
    a = ap.parse_args()
    if a.recipe: return recipe(*a.recipe)
    os.makedirs(a.out, exist_ok=True)
    if a.module: a.exe = module_env(a.module)
    if a.finish:
        if not a.bridge: ap.error('--finish needs --bridge')
        finish(a)
    elif a.shard: shard(a)
    else: ap.error('--shard K/N, --finish or --recipe')


if __name__ == '__main__': main()
