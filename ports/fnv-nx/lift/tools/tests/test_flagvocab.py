"""flagvocab: the vocabulary is generated from the emitter; --check accepts a status-first row and refuses a row with no status, two statuses, an unknown attr or a bad count attr.
Usage: test_flagvocab.py [universe.tsv ...]   (the given real universe files must pass too)"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import sys, os, tempfile
import fnvnx.lift.tools.flagvocab as flagvocab
v = flagvocab.vocab()
assert v['status'] == ['proven', 'candidate'] and 'ptr' in v['attr'] and 'first-ud' in v['attr'] and [a for a in v['attr'] if a.endswith('=')] == ['reloc-misfit=', 'ud=', 'xf='] and set(v) == {'status', 'attr'}, v
def rows(*flags):
    p = tempfile.mktemp(); open(p, 'w').write('# h\n' + ''.join('00401000\tcall\t10\t%s\n' % f for f in flags)); return p
assert flagvocab.check(rows('proven', 'candidate,ptr,seh-handler', 'proven,first-ud,ud=3,xf=12')) == []
for bad in ('ptr', 'proven,candidate', 'proven,nope', 'proven,ud=x', 'proven,ud', 'candidate,proven', 'proven,ud='):
    assert flagvocab.check(rows(bad)), bad
for f in sys.argv[1:]:
    assert flagvocab.check(f) == [], f
print('ok')
