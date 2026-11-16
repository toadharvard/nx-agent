"""flagvocab: the vocabulary is generated from the emitter; --check accepts a status-first row and refuses a row with no status, two statuses, an unknown attr or a bad count attr.
Usage: test_flagvocab.py [universe.tsv ...]   (the given real universe files must pass too)"""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import flagvocab
v = flagvocab.vocab()
assert v['status'] == ['proven', 'candidate'] and 'ptr' in v['attr'] and 'first-ud' in v['attr'] and v['count_attr'] == ['reloc-misfit', 'ud', 'xf'], v
def rows(*flags):
    p = tempfile.mktemp(); open(p, 'w').write('# h\n' + ''.join('00401000\tcall\t10\t%s\n' % f for f in flags)); return p
assert flagvocab.check(rows('proven', 'candidate,ptr,seh-handler', 'proven,first-ud,ud=3,xf=12')) == []
for bad in ('ptr', 'proven,candidate', 'proven,nope', 'proven,ud=x', 'proven,ud', 'candidate,proven'):
    assert flagvocab.check(rows(bad)), bad
for f in sys.argv[1:]:
    assert flagvocab.check(f) == [], f
print('ok')
