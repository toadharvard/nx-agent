"""The CPUID feature-bit table of lift/proof tests, from its ONE pin: the external `cpufeat` of lift/liftsrc/build.json (commit + tree_sha256).  path() -> the file cpuid_feature_bits.tsv of that commit, checked out
of the fnv-nx-build clone $FNVNX_BUILD and verified against tree_sha256 with verify/cas.ingest (the loader's own tree hash); (None, reason) when the clone, the commit or the hash is not there (the caller skips with the reason).
FNVNX_CPUFEAT (a file) takes precedence, as in test_simdcheck."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FNVNX = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
FILE = 'cpuid_feature_bits.tsv'


def path():
    p = os.environ.get('FNVNX_CPUFEAT')
    if p: return p, None
    b = os.environ.get('FNVNX_BUILD')
    if not b or not os.path.isdir(b): return None, 'declared input not available: FNVNX_CPUFEAT (the table) or FNVNX_BUILD (a clone of fnv-nx-build holding the pin of lift/liftsrc/build.json external cpufeat)'
    e = [x for x in json.load(open(os.path.join(FNVNX, 'lift', 'liftsrc', 'build.json')))['external'] if x['name'] == 'cpufeat'][0]
    d = os.path.join(tempfile.mkdtemp(prefix='cpufeat-'), 'tree')
    os.makedirs(d)
    try:
        tar = subprocess.run(['git', '-C', b, 'archive', e['commit']], check=True, capture_output=True).stdout
        subprocess.run(['tar', '-x', '-C', d], input=tar, check=True)
    except subprocess.CalledProcessError: return None, 'the pinned commit %s of the external cpufeat is not in %s' % (e['commit'], b)
    sys.path.insert(0, os.path.join(FNVNX, 'verify'))
    import cas
    if cas.ingest(d, tempfile.mkdtemp(prefix='cpufeat-store-')) != e['tree_sha256']: return None, 'tree_sha256 of the checkout differs from the pin of the external cpufeat'
    return os.path.join(d, FILE), None
