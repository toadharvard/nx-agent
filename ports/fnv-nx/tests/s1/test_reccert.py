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

    # stamp: the unit's compile and the placed functions' match decide
    objs = os.path.join(root, 'objects.tsv'); mdir = os.path.join(root, 'match'); os.makedirs(mdir)
    def report(mismatches): json.dump({'units': [{'source': 'src/h.cpp', 'functions': 2, 'matched_functions': 2 - len(mismatches),
                                                  'mismatches': mismatches}]}, open(os.path.join(mdir, 'src_h.cpp.json'), 'w'))
    def cert(): return json.load(open(os.path.join(rdir, 'recon.json'))).get('cert')
    write(recon)
    open(objs, 'w').write('src/h.cpp\t/x/h.o\tfail\n'); report([])
    rc.stamp(os.path.join(root, 'recon'), objs, mdir)
    check('stamp: the unit does not compile -> no certificate', cert() is None, cert())
    open(objs, 'w').write('src/h.cpp\t/x/h.o\tok\n'); report([{'address': '0x00401010', 'why': 'bytes'}])
    rc.stamp(os.path.join(root, 'recon'), objs, mdir)
    check('stamp: a placed function mismatches -> no certificate', cert() is None, cert())
    report([{'address': '0x00409999'}])
    cur, n, rows = rc.stamp(os.path.join(root, 'recon'), objs, mdir, now='2026-11-10T00:00:00Z')
    check('stamp: compiles and its placed functions match -> certified for this rule set',
          cert() and cert()['rules'] == rc.rules_hash() and cert()['compiles'] is True and n['certified'] == 1, cert())
    out, st = apply(root)
    check('the stamped recon is applied', 'H::H()' in out and st['REC1'] == 1, dict(st))
    os.remove(os.path.join(mdir, 'src_h.cpp.json'))
    rc.stamp(os.path.join(root, 'recon'), objs, mdir)
    check('stamp: no match report -> the certificate is withdrawn', cert() is None, cert())
    write(dict(recon, functions=[]))
    rc.stamp(os.path.join(root, 'recon'), objs, mdir)
    check('stamp: a recon that places no function is certified by the compile', cert() and cert()['match'] == 'no placed function', cert())
    print('test_reccert: %s' % ('FAILED %d' % len(FAILS) if FAILS else 'ok'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
