#!/usr/bin/env python3
"""tests/s1/test_reccert.py - REC-CERT: REC1 applies a recon only with a certificate for the current rule set.

    python3 tests/s1/test_reccert.py        (our own snippets, no decomp)
"""
import importlib.util
import json
import os
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, TOOLS)
import prepare  # noqa: E402

sys.modules['prepare'] = prepare


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


s1 = load('s1_compile', os.path.join(TOOLS, 'rules', 's1_compile.py'))
rc = load('reccert', os.path.join(TOOLS, 'rules', 'reccert.py'))
FAILS = []


def check(name, cond, detail=''):
    if not cond:
        FAILS.append(name); print('FAIL', name, detail)


def fresh():
    for k in ('_fnvnx_recon', '_fnvnx_rules_hash', '_fnvnx_recon_rejected'): prepare.__dict__[k] = None
    prepare.__dict__['_fnvnx_recon_rejected'] = []


def apply(root, text='// @fnv 0x00401000\nvoid f() {}\n'):
    fresh()
    st = Counter()
    out = s1.r_recon_bodies(os.path.join(root, 'dec', 'src', 'h.cpp'), text, st)
    return out, st


def patch_cert(root):
    """PATCH-CERT: a site patch applies as a whole, and only with a certificate for this rule set"""
    sys.path.insert(0, os.path.join(TOOLS, 'rules'))
    pm = load('s1b_patches_t', os.path.join(TOOLS, 'rules', 's1b_patches.py'))
    pdir = os.path.join(root, 'patches'); os.makedirs(pdir)
    open(os.path.join(pdir, 't.s1b'), 'w').write(
        '@@ XINC1 src/h.cpp file\n# evidence\n<<<\nint a;\n===\nint a1;\n>>>\n<<<\nint b;\n===\nint b1;\n>>>\n'
        '@@ XINC1 src/h.cpp file\n# evidence\n<<<\nint c;\n===\nint c1;\n>>>\n')
    pm.SITES = pm.load_sites(pdir); pm.BY_FILE = {}
    for s in pm.SITES: pm.BY_FILE.setdefault(s.path, []).append(s)
    certs = os.path.join(pdir, 'certs.json')
    def run(text):
        pm._CERT.pop(certs, None)
        st = Counter(); out = pm.apply_sites('/x/src/h.cpp', text, st, 'XINC1', [], certs)
        return out, st
    os.environ.pop('FNVNX_PATCH_CERTIFY', None)
    out, st = run('int a;\nint b;\nint c;\n')
    check('no certificate: no site applied', out == 'int a;\nint b;\nint c;\n' and st['XINC1-NOCERT'] == 2, (out, dict(st)))
    os.environ['FNVNX_PATCH_CERTIFY'] = '1'
    out, st = run('int a;\nint b;\nint c;\n')
    check('the certifying run applies every site and counts each outcome',
          out == 'int a1;\nint b1;\nint c1;\n' and st['S1B-SITE/t.s1b:1/applied'] == 1 and st['S1B-SITE/t.s1b:13/applied'] == 1, (out, dict(st)))
    out, st = run('int a;\nint c;\n')
    check('a site applies as a whole or not at all (its second hunk has no match: the first is not applied either)',
          out == 'int a;\nint c1;\n' and st['XINC1-MISS'] == 1 and st['S1B-SITE/t.s1b:1/miss'] == 1 and st['XINC1'] == 1, (out, dict(st)))
    del os.environ['FNVNX_PATCH_CERTIFY']
    # stamp: applied + GCC unit ok + MSVC identical
    stats = os.path.join(root, 'ps.tsv'); objs = os.path.join(root, 'po.tsv'); rep = os.path.join(root, 'pm.jsonl')
    open(stats, 'w').write('S1B-SITE/t.s1b:1/applied\t1 files\t1 edits\nS1B-SITE/t.s1b:13/miss\t1 files\t1 edits\n')
    open(objs, 'w').write('src/h.cpp\t/x/h.o\tok\n')
    open(rep, 'w').write(json.dumps({'unit': 0, 'source': 'src/h.cpp', 'addr': '0x00401000', 'status': 'identical'}) + '\n')
    real = rc.TOOLS
    import shutil
    fake = os.path.join(root, 'tools'); shutil.copytree(real, fake, ignore=shutil.ignore_patterns('__pycache__', 'equiv', 'link', 'farm'))
    rcf = load('reccert_f', os.path.join(fake, 'rules', 'reccert.py'))
    # the stamping module reads the patches next to its own tools/: give it this test's
    os.makedirs(os.path.join(root, 'patches'), exist_ok=True)
    cur, n, rows = rcf.stamp_patches(stats, objs, rep, out=certs)
    c = json.load(open(certs))
    check('stamp-patches writes the rule set and one entry per certified site',
          c['format'] == 'fnvnx-patchcert/1' and c['rules'] == rcf.rules_hash() and set(c['sites']) == {'t.s1b:1'}, c)
    out, st = run('int a;\nint b;\nint c;\n')
    check('the certified site applies, the other does not', out == 'int a1;\nint b1;\nint c;\n' and st['XINC1-NOCERT'] == 1, (out, dict(st)))
    open(os.path.join(pdir, 't.s1b'), 'a').write('')
    c['sites']['t.s1b:1']['site'] = 'other text'; json.dump(c, open(certs, 'w'))
    out, st = run('int a;\nint b;\nint c;\n')
    check('a certificate of other site text does not apply', out == 'int a;\nint b;\nint c;\n', out)
    open(rep, 'w').write(json.dumps({'unit': 0, 'source': 'src/h.cpp', 'addr': '0x00401000', 'status': 'differs'}) + '\n')
    cur, n, rows = rcf.stamp_patches(stats, objs, rep, out=certs)
    check('stamp-patches: MSVC differs -> not certified', n == 0 and any('MSVC: differs' in r[4] for r in rows), rows)


def main():
    for k in ('FNVNX_RECON_CERTIFY', 'FNVNX_PREPARE_SKIP'): os.environ.pop(k, None)
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, 'dec', 'build')); os.makedirs(os.path.join(root, 'dec', 'src'))
    json.dump({'units': [{'source': 'src/h.cpp', 'functions': [{'address': '0x00401000', 'symbol': '?f@@YAXXZ', 'size': 8}],
                          'symbols': {}}]}, open(os.path.join(root, 'dec', 'build', 'units.json'), 'w'))
    rdir = os.path.join(root, 'recon', 'r1'); os.makedirs(rdir)
    recon = {'format': 'fnvnx-recon/1', 'unit': 'src/h.cpp', 'append': 'body.cpp',
             'functions': [{'address': '0x00401010', 'symbol': '??0H@@QAE@XZ', 'size': 16}]}
    def write(r): json.dump(r, open(os.path.join(rdir, 'recon.json'), 'w'))
    write(recon)
    open(os.path.join(rdir, 'body.cpp'), 'w').write('struct H { H(); };\nH::H() {}\n')
    os.environ['FNVNX_RECON'] = os.path.join(root, 'recon')

    out, st = apply(root)
    check('no certificate: not applied, counted', 'H::H()' not in out and st['REC-NOCERT'] == 1 and not st['REC1'], dict(st))
    os.environ['FNVNX_RECON_CERTIFY'] = '1'
    out, st = apply(root)
    check('the certifying run applies it', 'H::H()' in out and st['REC1'] == 1, dict(st))
    del os.environ['FNVNX_RECON_CERTIFY']

    write(dict(recon, cert={'rules': 'another rule set', 'compiles': True}))
    out, st = apply(root)
    check('a certificate of another rule set: not applied', 'H::H()' not in out and st['REC-NOCERT'] == 1, dict(st))
    write(dict(recon, cert={'rules': rc.rules_hash(), 'compiles': True}))
    out, st = apply(root)
    check('a certificate of this rule set: applied', 'H::H()' in out and st['REC1'] == 1, dict(st))
    os.environ['FNVNX_PREPARE_SKIP'] = 'r_some_rule'
    out, st = apply(root)
    check('leaving a rule out is another rule set', 'H::H()' not in out and st['REC-NOCERT'] == 1, dict(st))
    check('the hash names the rules left out, in any order',
          rc.rules_hash('r_b,r_a') == rc.rules_hash('r_a,r_b') and rc.rules_hash('r_a') != rc.rules_hash(''))
    del os.environ['FNVNX_PREPARE_SKIP']

    # the certifying run names what it applied
    write(recon)
    os.environ['FNVNX_RECON_CERTIFY'] = '1'
    out, st = apply(root)
    check('the certifying run counts the recon it applied by name', st['REC-SITE/r1/applied'] == 1, dict(st))
    del os.environ['FNVNX_RECON_CERTIFY']
    # stamp: applied + the unit's compile + a report that compared the unit + the placed functions not mismatched
    objs = os.path.join(root, 'objects.tsv'); mdir = os.path.join(root, 'match'); os.makedirs(mdir)
    stats = os.path.join(root, 'stats.tsv')
    def report(mismatches, status=None):
        json.dump({'units': [{'source': 'src/h.cpp', 'functions': 2, 'matched_functions': 2 - len(mismatches),
                              'status': status or ('mismatch' if mismatches else 'ok'), 'mismatches': mismatches}]},
                  open(os.path.join(mdir, 'm.json'), 'w'))
    def cert(): return json.load(open(os.path.join(rdir, 'recon.json'))).get('cert')
    ov = os.path.join(root, 'ov')
    os.makedirs(os.path.join(ov, 'build'))
    json.dump({'units': [{'source': 'src/h.cpp'}]}, open(os.path.join(ov, 'build', 'units.json'), 'w'))
    def stamp(): return rc.stamp(os.path.join(root, 'recon'), stats, objs, mdir, ov)
    open(stats, 'w').write('REC-SITE/r1/applied\t1 files\t1 edits\n')
    open(objs, 'w').write('src/h.cpp\t/x/h.o\tfail\n'); report([])
    stamp()
    check('stamp: the unit does not compile -> no certificate', cert() is None, cert())
    open(objs, 'w').write('src/h.cpp\t/x/h.o\tok\n'); report([{'address': '0x00401010', 'why': 'bytes'}])
    stamp()
    check('stamp: a placed function mismatches -> no certificate', cert() is None, cert())
    report([{'address': '0x00409999'}])
    cur, n, rows = stamp()
    check('stamp: applied, compiles, compared, its placed functions equal -> certified for this rule set',
          cert() and cert()['rules'] == rc.rules_hash() and cert()['compiles'] is True and n['certified'] == 1, cert())
    before = open(os.path.join(rdir, 'recon.json')).read(); stamp()
    check('stamping twice changes nothing', open(os.path.join(rdir, 'recon.json')).read() == before)
    out, st = apply(root)
    check('the stamped recon is applied', 'H::H()' in out and st['REC1'] == 1, dict(st))
    json.dump({'units': [{'source': 'src/h.cpp', 'status': 'compile-failed'}]}, open(os.path.join(mdir, 'm.json'), 'w'))
    stamp()
    check('stamp: a report that did not compare the unit (compile-failed, no mismatches key) certifies nothing', cert() is None, cert())
    report([]); open(stats, 'w').write('')
    stamp()
    check('stamp: prepare did not apply the recon -> no certificate', cert() is None, cert())
    open(stats, 'w').write('REC-SITE/r1/applied\t1 files\t1 edits\n')
    os.remove(os.path.join(mdir, 'm.json'))
    try: stamp(); err = None
    except rc.OverlayMismatch as e: err = str(e)
    check('stamp: no match report -> the overlay set differs, fails and certifies nothing', err and cert() is None, (err, cert()))
    write(dict(recon, functions=[])); report([])
    stamp()
    check('stamp: a recon that places no function is certified when its unit was compared',
          cert() and cert()['match'].startswith('no placed function'), cert())
    # stamp: the reports must be of the overlay's units (same set of sources), else nothing is certified
    write(dict(recon, cert={'rules': 'old', 'compiles': True})); report([])
    json.dump({'units': [{'source': 'src/h.cpp'}, {'source': 'src/pristine_only.cpp'}]},
              open(os.path.join(ov, 'build', 'units.json'), 'w'))
    before = open(os.path.join(rdir, 'recon.json')).read()
    try: stamp(); err = None
    except rc.OverlayMismatch as e: err = str(e)
    check('stamp: reports of another unit set than the overlay: refused with counts, recon.json untouched',
          err and 'hold 1 units' in err and 'holds 2' in err and open(os.path.join(rdir, 'recon.json')).read() == before, err)
    cli = rc.main(['stamp', os.path.join(root, 'recon'), '--stats', stats, '--objects', objs, '--match', mdir, '--overlay', ov])
    check('stamp CLI: the mismatch is a non-zero exit', cli != 0, cli)
    write(recon)
    # pristine-overlay: the recon on the pristine unit, units.json cut to the recon's unit with its recipe entry
    write(recon)
    open(os.path.join(root, 'dec', 'src', 'h.cpp'), 'w').write('// @fnv 0x00401000\nvoid f() {}\n')
    open(os.path.join(root, 'dec', 'src', 'o.cpp'), 'w').write('int o;\n')
    ov = os.path.join(root, 'ov')
    n_units, rej = rc.pristine_overlay(os.path.join(root, 'dec'), os.path.join(root, 'recon'), ov)
    uj = json.load(open(os.path.join(ov, 'build', 'units.json')))['units']
    check('pristine-overlay: the unit with the body, the recipe entry added, other files linked',
          n_units == 1 and not rej and 'H::H() {}' in open(os.path.join(ov, 'src', 'h.cpp')).read()
          and any(f['address'] == '0x00401010' for f in uj[0]['functions']) and os.path.islink(os.path.join(ov, 'src', 'o.cpp')),
          (n_units, rej, uj))
    # no entry between <out> and build_and_match.py is a link out of the overlay (it resolves its root through them)
    os.makedirs(os.path.join(root, 'dec', 'tools'))
    open(os.path.join(root, 'dec', 'tools', 'build_and_match.py'), 'w').write('# x\n')
    ov2 = os.path.join(root, 'ov2')
    os.makedirs(ov2); os.symlink(os.path.join(root, 'dec', 'tools'), os.path.join(ov2, 'tools'))   # a stale link from an old run
    rc.pristine_overlay(os.path.join(root, 'dec'), os.path.join(root, 'recon'), ov2)
    bm = os.path.join(ov2, 'tools', 'build_and_match.py')
    check('pristine-overlay: tools is a real copy; build_and_match.py resolves inside the overlay',
          not os.path.islink(os.path.join(ov2, 'tools')) and os.path.realpath(bm) == os.path.join(os.path.realpath(ov2), 'tools', 'build_and_match.py'),
          os.path.realpath(bm))
    patch_cert(root)
    print('test_reccert: %s' % ('FAILED %d' % len(FAILS) if FAILS else 'ok'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
