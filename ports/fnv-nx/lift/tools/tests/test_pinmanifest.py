"""pinmanifest: the manifest of a toy assembled facts directory (exe + one module): counts, the table hash, the vocabulary; a row outside the vocabulary and a missing image are errors."""
import os, sys, json, tempfile, subprocess, hashlib
T = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pinmanifest.py')
d = tempfile.mkdtemp(); os.makedirs(d + '/f/exe'); os.makedirs(d + '/f/foo')
open(d + '/f/exe/universe.tsv', 'w').write('# h\n00401000\tcall\t5\tproven,ptr\n00401010\ttail\t5\tcandidate,ud=3\n')
open(d + '/f/foo/universe.tsv', 'w').write('# h\n10001000\tcall\t5\tproven\n')
tab = d + '/t.tsv'; open(tab, 'w').write('# name\tsha256\tsize\tpreferred_base\tassigned_base\nfoo.dll\tab\t1\t10000000\t01410000\n')
def run(): return subprocess.run([sys.executable, T, '--table', tab, '--facts', d + '/f', '--lifter', 'c0ffee', '--gamedata', 'exe=aa:bb', '--out', d + '/m.json'], capture_output=True, text=True)
r = run(); assert r.returncode == 0, r.stderr
m = json.load(open(d + '/m.json'))
assert m['modules_tsv_sha256'] == hashlib.sha256(open(tab, 'rb').read()).hexdigest() and m['totals'] == {'images': 2, 'universe_rows': 3}, m
assert m['images'][0]['status'] == {'candidate': 1, 'proven': 1} and m['images'][0]['attr'] == {'ptr': 1, 'ud=': 1} and m['universe_flags']['status'] == ['proven', 'candidate']
open(d + '/f/foo/universe.tsv', 'a').write('10001010\tcall\t5\tproven,bogus\n'); assert run().returncode != 0
os.remove(d + '/f/foo/universe.tsv'); assert run().returncode != 0
print('ok')
