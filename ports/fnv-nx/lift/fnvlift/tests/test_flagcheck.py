"""The flaglive check (tools/flagcheck.py, proof/FLAGLIVE.md rule 2) on lifted buffers.
  accepts: the text, the flag-drop rows and the record of a correct lift;
  rejects: a list with one bit flipped / a row removed / a row added; a unit with one dropped flag line restored; a unit with one kept flag line removed; a unit with a Form block removed;
  the merge stage rejects a pack list that is not the union of the shard lists.
    test_flagcheck.py"""
import os, sys, time, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _inputs; _inputs.configure()
from fnvlift import flaglive as fl
from fnvlift.image import Image
import fnvlift as L
sys.path.insert(0, os.path.join(_inputs.LIFT, 'tools')); import flagcheck as fc
FAILS = []
B = 0x401000


def check(n, ok):
    print('%-90s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


def lift(h):
    code = bytes.fromhex(h)
    t, meta = L.lift(Image.from_bytes(code, B), B, len(code))[:2]
    return t, meta


def run(t, m, rows=None):
    rows = dict(m['flagdrop']) if rows is None else rows
    return fc.check_function(B, t, m['fl'], rows, fl.mask, fl.dropped)


BUFS = {
    'add;add;ret': '01d801d1c3',
    'add;cmp;je;add;ret': '01d839d8740201d1c3',
    'dec;jne top;ret': '4975fdc3',
    'add;adc;ret': '01d811d8c3',
    'cmp;sete;xor;ret': '39d80f94c031c0c3',
    'add;div;ret': '01d8f7f1c3',
    'add;call;ret': '01d8e800000000c3',
    'sub;sub;cmp;ja;ret': '29d829c839d8770190c3',
    'cld;add;std;sub;ret': 'fc01d8fd29d8c3',
}
t0 = time.process_time()
lifted = {k: lift(v) for k, v in BUFS.items()}
t_lift = time.process_time() - t0
t0 = time.process_time()
for k, (t, m) in lifted.items():
    d = run(t, m)
    check('accepts a correct lift: %s' % k, d == [])
t_chk = time.process_time() - t0
print('lift of %d buffers: %.3f CPU-s; flagcheck of them (lookups only): %.3f CPU-s' % (len(BUFS), t_lift, t_chk))
check('the buffers hold dropped flag writes and Form blocks (the check is not vacuous)', sum(len(m['flagdrop']) for _, m in lifted.values()) >= 4 and sum(1 for _, m in lifted.values() for x in m['fl']['fsw'] if x[2]) >= 20)

t, m = lifted['add;add;ret']
rows = dict(m['flagdrop'])
a0 = min(rows)
bad = dict(rows); bad[a0] ^= 1
d = run(t, m, bad)
check('rejects a list with one bit flipped (cf kept where it is dead)', len(d) == 1 and 'flagdrop %08x' % a0 in d[0])
bad = dict(rows); bad[a0] ^= 1 << 5
check('rejects a list with a different bit flipped (of)', len(run(t, m, bad)) == 1)
bad = dict(rows); del bad[a0]
check('rejects a list with the row removed', len(run(t, m, bad)) == 1)
bad = dict(rows); bad[B + 2] = 1
check('rejects a list with a row added for an instruction that drops nothing', len(run(t, m, bad)) == 1)

lines = t.split('\n')
# a dropped flag line restored: the first add (dropped all six) gets back `cf = 0;` inside its block
i = lines.index('{')
rest = lines[:i + 1] + ['cf = 0;'] + lines[i + 1:]
d = run('\n'.join(rest), m)
check('rejects a unit with one dropped flag line restored', len(d) == 1 and 'instruction %08x' % a0 in d[0])
# a kept flag line removed: the second add keeps all six; remove its zf line
idx = [j for j, l in enumerate(lines) if l.startswith('zf = ')]
check('the second add stores zf', len(idx) == 1)
rest = lines[:idx[0]] + lines[idx[0] + 1:]
d = run('\n'.join(rest), m)
check('rejects a unit with one kept flag line removed', len(d) == 1 and 'instruction %08x' % (B + 2) in d[0])
# the same flag restored in the wrong instruction is still one difference per block (the blocks are aligned to instructions)
sw = lines[:idx[0]] + lines[idx[0] + 1:]
j = sw.index('{') + 1
sw = sw[:j] + ['zf = 0;'] + sw[j:]
d = run('\n'.join(sw), m)
check('rejects a flag line moved from the instruction that keeps it to one that dropped it (two differences)', len(d) == 2)
# a block removed
b0 = lines.index('{'); b1 = lines.index('}', b0)
d = run('\n'.join(lines[:b0] + lines[b1 + 1:]), m)
check('rejects a unit with a Form block removed', len(d) == 1 and 'Form blocks' in d[0])
# a flag line that is not in a Form block is not a Form flag store: a handler line is ignored, as in the lifter
check('a flag store outside any Form block does not count', run('\n'.join(lines + ['zf = 1;']), m) == [])

# the cases with escapes keep every flag; the check sees them the same way
t, m = lifted['add;div;ret']
check('add;div;ret: the add keeps all six stores', run(t, m) == [] and not m['flagdrop'])
lines = t.split('\n'); idx = [j for j, l in enumerate(lines) if l.startswith('cf = ')]
d = run('\n'.join(lines[:idx[0]] + lines[idx[0] + 1:]), m)
check('add;div;ret: removing the add\'s cf store is rejected', len(d) == 1)

# merge stage: the pack list is the sorted union of the shard lists
d = tempfile.mkdtemp(prefix='flagcheck-')
os.makedirs(d + '/exe')
rowsl = sorted('%08x\t%08x\t%02x' % (B, a, mk) for a, mk in lifted['add;add;ret'][1]['flagdrop'].items())
open(d + '/exe/flagdrop_0.tsv', 'w').write(''.join(l + '\n' for l in rowsl))
json.dump(dict(shard='0/1', entries=1, blocks=5, rows=1, differences=0, first=[]), open(d + '/exe/flagcheck_0.json', 'w'))
open(d + '/pack.tsv', 'w').write(''.join(l + '\n' for l in rowsl))
check('merge accepts the pack list that is the union of the shard lists', fc.merge(d, d + '/pack.tsv', d + '/o') == 0 and json.load(open(d + '/o/flaglive.json'))['differences'] == 0)
open(d + '/pack.tsv', 'w').write(''.join(l + '\n' for l in [rowsl[0].replace('\t3f', '\t3e')]))
check('merge rejects a pack list with one bit flipped', fc.merge(d, d + '/pack.tsv', d + '/o2') == 1)

# the shard path of tools/liftgen.py (--flagcheck): check_work over a universe entry, with the shard's own texts and list
from fnvlift import inputs
import liftgen as LG, lifted
code = bytes.fromhex(BUFS['add;add;ret'])
u = d + '/universe.tsv'; open(u, 'w').write('%08x\tcode\t%d\t\n' % (B, len(code)))
inputs.reset(); _inputs.configure(); inputs.reset()
inputs.configure(universe=u, fpstate=d, fpdepth=d + '/depth.tsv', x86sys=_inputs.X86SYS, cpumodel=_inputs.CPUMODEL, rcptab=_inputs.RCPTAB, rcpcert=_inputs.RCPCERT, udsail=d + '/udsail.tsv', segwrites=d + '/segwrites.json', image='buf')
lifted.begin(Image.from_bytes(code, B), 'dsl', None)
t1, m1 = lift(BUFS['add;add;ret'])
LG.G['chk'] = ({B: t1.encode()}, {B: dict(m1['flagdrop'])})
blocks, diffs = LG.check_work([B])
check('liftgen --flagcheck path: an entry of the shard with the pack\'s own text and list is clean', diffs == [] and blocks == 3)
LG.G['chk'] = ({B: t1.encode()}, {B: {B: m1['flagdrop'][B] ^ 1}})
check('liftgen --flagcheck path: a flipped bit of the list is reported', len(LG.check_work([B])[1]) == 1)
LG.G['chk'] = ({}, {})
check('liftgen --flagcheck path: an entry the pack has no text for is reported', len(LG.check_work([B])[1]) == 1)
print('FAIL: %s' % FAILS if FAILS else 'all ok')
sys.exit(1 if FAILS else 0)
