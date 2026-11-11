#!/usr/bin/env python3
"""Gate `forms` (fnvnx-gate/1): an x86 instruction form without a certificate is a build error (rule R2).

    python3 -I formgate.py --shapes DIR --certs FILE [--pins FILE]        env: FNVNX_FORMGATE_SHAPES, FNVNX_FORMGATE_CERTS, GATE_OUT
    formgate.check(shapes_dir, certs_file, pins=PINS) -> (universe, certified, uncertified_shape_keys_sorted)     (build-time use)

Universe: the distinct ENCODING SHAPES (proof/shape.py) of every module listed in DIR/KEYS.tsv; DIR/<module>/shapes.tsv are the
outputs of proof/census.py.  A shape is certified iff certs.tsv has a row with the same shape key, the dsl_sha that the CURRENT code
gives (Form.sha() of forms.build on the representative encoding rep_bytes(shape) at address shape.ADDR; none when forms.build has no
builder for it (forms.build raises KeyError / AssertionError) or the representative does not decode back to the shape, and a shape with dsl_sha none is uncertified), the pinned
model_sha and result == 'proved'.  Exit 0 iff green.

Private inputs are pinned by sha256 in formgate_pins.json (next to this file): the gate REFUSES (exit 2, no result file) when DIR/KEYS.tsv
or certs.tsv or any module's shapes.tsv named in KEYS.tsv differs from its pin.  KEYS.tsv lists module, image sha256, ranges sha256,
tool sha256, shapes.tsv sha256 (tab separated); it is itself pinned, and it pins every shapes.tsv.

dsl_sha convention: a shape's symbolic fields (immediate / displacement / relative target / far pointer / moffs) are zero in the
representative, so the Form is built with all those fields = 0 at address 0.  The hash binds the certificate to the builder code
(forms*.py, dsl.py); that the Form is correct for every field value is what the certificate itself (the proof) asserts.

The gate imports no SMT solver: the DSL imports z3 only inside to_smt / post.  No clock, no host state, sorted output."""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import forms, shape  # noqa: E402

PINS = os.path.join(HERE, 'formgate_pins.json')
FORMAT = 'fnvnx-gate/1'
MAX_IDS = 50
REFERENCES = ('sail', 'fpsmt', 'k', 'sdm', 'table')
FAMILIES = ('int', 'x87', 'simd', 'sys')


class PinError(Exception):
    """a private input does not have the sha256 its pin names"""


def sha_file(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def rows(path):
    with open(path) as fh:
        for ln in fh:
            ln = ln.rstrip('\n')
            if ln and ln[0] != '#':
                yield ln.split('\t')


def load_pins(path):
    with open(path) as fh:
        return json.load(fh)


def verify_pins(shapes_dir, certs_file, pins=PINS):
    """-> the model_sha256 the certificates must carry; raises PinError on any mismatch."""
    p = load_pins(pins)
    keys = os.path.join(shapes_dir, 'KEYS.tsv')
    for what, path, want in (('shapes KEYS.tsv', keys, p['shapes']['sha256']), ('certs.tsv', certs_file, p['certs']['sha256'])):
        got = sha_file(path)
        if got != want:
            raise PinError('%s %s: sha256 %s != pinned %s' % (what, path, got, want))
    for r in rows(keys):
        if len(r) != 5:
            raise PinError('KEYS.tsv: bad row %r' % r)
        path = os.path.join(shapes_dir, r[0], 'shapes.tsv')
        got = sha_file(path)
        if got != r[4]:
            raise PinError('%s: sha256 %s != KEYS.tsv %s' % (path, got, r[4]))
    return p['model_sha256']


def dsl_sha(key, md):
    """Form.sha() of the current builder on the shape's representative; None = no builder (or the representative is not the shape)."""
    r = shape.rep_bytes(key)
    got = list(md.disasm(r, shape.ADDR))
    if len(got) != 1 or got[0].size != len(r) or shape.shape_key(got[0]) != key:
        return None
    try:
        return forms.build(got[0], shape.ADDR).sha()
    except (KeyError, AssertionError):     # forms.build: KeyError = mnemonic outside the covered set; AssertionError = the DSL width check rejects the
        return None                        # operand form the builder produces (e.g. a 16-bit pop).  No Form exists, so the shape stays uncertified (the safe side)


def load_shapes(shapes_dir):
    """({shape: family}, {module: [shape keys]}) over the modules of KEYS.tsv"""
    fam, mods = {}, {}
    for r in rows(os.path.join(shapes_dir, 'KEYS.tsv')):
        ks = []
        for s in rows(os.path.join(shapes_dir, r[0], 'shapes.tsv')):
            ks.append(s[0])
            fam[s[0]] = s[1]
        mods[r[0]] = ks
    return fam, mods


def load_certs(certs_file, model_sha):
    """{shape: set of dsl_sha} of the rows that are proofs against the pinned model"""
    ok = {}
    for r in rows(certs_file):
        if len(r) != 6:
            raise ValueError('certs.tsv: bad row %r' % r)
        key, _, dsl, model, ref, res = r
        if res == 'proved' and model == model_sha and ref in REFERENCES:
            ok.setdefault(key, set()).add(dsl)
    return ok


def analyse(shapes_dir, certs_file, pins=PINS):
    model_sha = verify_pins(shapes_dir, certs_file, pins)
    fam, mods = load_shapes(shapes_dir)
    certs = load_certs(certs_file, model_sha)
    md = shape.decoder()
    cert, nobuilder = {}, 0
    for k in sorted(fam):
        d = dsl_sha(k, md)
        nobuilder += d is None
        cert[k] = d is not None and d in certs.get(k, ())
    return fam, mods, cert, nobuilder


def check(shapes_dir, certs_file, pins=PINS):
    fam, mods, cert, nobuilder = analyse(shapes_dir, certs_file, pins)
    unc = [k for k in sorted(cert) if not cert[k]]
    return len(cert), len(cert) - len(unc), unc


def detail_of(fam, mods, cert, nobuilder):
    byfam = []
    for f in FAMILIES:
        ks = [k for k in fam if fam[k] == f]
        byfam.append('%s %d/%d' % (f, sum(cert[k] for k in ks), len(ks)))
    bymod = ['%s %d/%d' % (m, sum(cert[k] for k in sorted(set(mods[m]))), len(set(mods[m]))) for m in sorted(mods)]
    return 'certified/universe per family: %s; per module: %s; shapes without a DSL builder: %d' % (', '.join(byfam), ', '.join(bymod), nobuilder)


def write_result(out_dir, universe, certified, uncertified, detail):
    """$GATE_OUT/forms.json, the shape of contract/gatefile.py"""
    r = dict(format=FORMAT, name='forms', status='green' if certified == universe else 'red', universe=universe, certified=certified,
             uncertified=sorted(uncertified)[:MAX_IDS], detail=detail)
    path = os.path.join(out_dir, 'forms.json')
    with open(path, 'w', newline='\n') as fh:
        json.dump(r, fh, sort_keys=True, separators=(',', ':'))
        fh.write('\n')
    return path


def main(argv):
    ap = argparse.ArgumentParser(prog='formgate')
    ap.add_argument('--shapes', default=os.environ.get('FNVNX_FORMGATE_SHAPES'))
    ap.add_argument('--certs', default=os.environ.get('FNVNX_FORMGATE_CERTS'))
    ap.add_argument('--pins', default=PINS)
    a = ap.parse_args(argv)
    if not a.shapes or not a.certs:
        sys.stderr.write('formgate: --shapes DIR and --certs FILE (or FNVNX_FORMGATE_SHAPES / FNVNX_FORMGATE_CERTS) are required\n')
        return 2
    try:
        fam, mods, cert, nobuilder = analyse(a.shapes, a.certs, a.pins)
    except PinError as e:
        sys.stderr.write('formgate: REFUSED: %s\n' % e)
        return 2
    unc = [k for k in sorted(cert) if not cert[k]]
    universe, certified = len(cert), len(cert) - len(unc)
    detail = detail_of(fam, mods, cert, nobuilder)
    out = os.environ.get('GATE_OUT')
    if out:
        write_result(out, universe, certified, unc, detail)
    sys.stdout.write('forms: %s %d of %d shapes certified; %s\n' % ('green' if not unc else 'red', certified, universe, detail))
    return 0 if not unc else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
