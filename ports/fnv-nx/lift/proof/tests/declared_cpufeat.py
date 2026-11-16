"""The CPUID feature-bit table of lift/proof tests: the same function of the same pins as the recipe stage lift/cpufeat (RECIPE: pdftotext of the pinned SDM and AMD APM volumes, then `sdmfeat.py features`), run here over the
declared inputs of the test: FNVNX_SDM_TXT (pdftotext -layout of the pinned SDM, its PDF beside it as sdm.pdf) and FNVNX_BUILD (a clone of fnv-nx-build holding the pins of proof/pins.json `external` amd-apm, amd-apm-vol5).  There is no
committed copy of the table and no file to point at.  path() -> (the table's path, None); (None, reason) when an input is not there (the caller skips with the reason)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import hashlib, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROOF = os.path.normpath(os.path.join(HERE, '..'))
_CACHE = []


def path():
    if _CACHE: return _CACHE[0]
    sdm, b = os.environ.get('FNVNX_SDM_TXT'), os.environ.get('FNVNX_BUILD')
    if not sdm or not os.path.isfile(sdm) or not os.path.isfile(sdm[:-4] + '.pdf'): r = (None, 'declared input not available: FNVNX_SDM_TXT (pdftotext -layout of the pinned SDM, sdm.pdf beside it)')
    elif not b or not os.path.isdir(b): r = (None, 'declared input not available: FNVNX_BUILD (a clone of fnv-nx-build holding the pinned AMD APM volumes of proof/pins.json)')
    else: r = (_build(sdm, b), None)
    _CACHE.append(r); return r


def _build(sdm, b):
    pins = [e for e in json.load(open(os.path.join(PROOF, 'pins.json')))['external'] if e['name'].startswith('amd-apm')]
    d = tempfile.mkdtemp(prefix='cpufeat-'); apm = []
    for i, pin in enumerate(pins):
        subprocess.run(['git', '-C', b, 'fetch', '-q', 'origin', pin['ref']], check=True)
        pdf = os.path.join(d, 'apm%d.pdf' % i)
        open(pdf, 'wb').write(subprocess.run(['git', '-C', b, 'show', '%s:%s' % (pin['commit'], pin['file'])], check=True, capture_output=True).stdout)
        assert hashlib.sha256(open(pdf, 'rb').read()).hexdigest() == pin['sha256'], pin['name']
        subprocess.run(['pdftotext', '-layout', pdf, pdf[:-4] + '.txt'], check=True); apm += ['--apm', pdf]
    out = os.path.join(d, 'cpuid_feature_bits.tsv')
    sys.path.insert(0, PROOF)
    import fnvnx.lift.proof.sdmfeat as sdmfeat
    with open(os.devnull, 'w') as nul:
        so, sys.stdout = sys.stdout, nul
        try: assert sdmfeat.main(['features', sdm, out] + apm) == 0
        finally: sys.stdout = so
    return out
